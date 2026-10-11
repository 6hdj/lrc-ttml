"""
CAM++ 说话人声纹提取（中文，ModelScope iic/speech_campplus_sv_zh-cn_16k-common）。

为什么要自己接一层：
    本机装不了 funasr（依赖太重、而且 3.14 上部分依赖没有 wheel），
    所以直接从 funasr 的 wheel 里取出 ``CAMPPlus`` 的网络定义（``models/campplus_src/``），
    再自己实现 Kaldi 风格的 80 维 fbank 前端。模型权重与官方完全一致。

对外只暴露一个函数：
    embed(waveform_16k) -> np.ndarray(192,)   # L2 归一化后的声纹向量
两个向量的余弦相似度即"是不是同一个人"；官方推荐判定阈值 0.31。
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Optional

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "models", "campplus_src")
_MODEL = os.path.join(_HERE, "models", "campplus", "campplus_cn_common.bin")

SR = 16000
N_MELS = 80
FRAME_LEN = 400          # 25ms
FRAME_SHIFT = 160        # 10ms
PREEMPH = 0.97
LOW_FREQ = 20.0
HIGH_FREQ = -400.0       # Kaldi 约定：负数表示 nyquist + 该值
DITHER = 0.0
SIM_THRESHOLD = 0.31     # 官方 configuration.json 里的 yesOrno_thr

_model = None
_lock = threading.Lock()


# --------------------------------------------------------------------------
# Kaldi 兼容 fbank（对齐 torchaudio.compliance.kaldi / funasr.utils.fbank）
# --------------------------------------------------------------------------
def _mel_banks(num_bins: int, sr: int, n_fft: int, low_freq: float, high_freq: float):
    def hz2mel(f):
        return 1127.0 * np.log(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (np.exp(m / 1127.0) - 1.0)

    nyquist = 0.5 * sr
    high = nyquist + high_freq if high_freq <= 0 else high_freq
    fft_bins = n_fft // 2
    fftbin_width = sr / n_fft
    mel_low, mel_high = hz2mel(low_freq), hz2mel(high)
    delta = (mel_high - mel_low) / (num_bins + 1)
    left, center, right = [], [], []
    for i in range(num_bins):
        l = mel_low + i * delta
        c = l + delta
        r = c + delta
        left.append(l)
        center.append(c)
        right.append(r)
    bins = np.zeros((num_bins, fft_bins), dtype=np.float32)
    for i in range(num_bins):
        for j in range(fft_bins):
            f = fftbin_width * j
            m = hz2mel(f)
            if left[i] < m < right[i]:
                if m <= center[i]:
                    bins[i, j] = (m - left[i]) / (center[i] - left[i])
                else:
                    bins[i, j] = (right[i] - m) / (right[i] - center[i])
    return bins


_MEL = _mel_banks(N_MELS, SR, 512, LOW_FREQ, HIGH_FREQ)


def fbank(waveform: np.ndarray, sr: int = SR) -> np.ndarray:
    """80 维 log-mel fbank，(T, 80)。与 Kaldi 的 snip_edges=True 行为一致。"""
    x = np.asarray(waveform, dtype=np.float32)
    if sr != SR:
        raise ValueError("CAM++ 只支持 16kHz")
    if DITHER > 0:
        x = x + np.random.uniform(-DITHER, DITHER, size=x.shape).astype(np.float32)
    x = x - x.mean()                                   # remove_dc_offset
    x = np.pad(x, (0, FRAME_LEN), mode="constant")     # 末尾补一帧，防止越界
    n = 1 + (len(x) - FRAME_LEN) // FRAME_SHIFT
    if n <= 0:
        return np.zeros((0, N_MELS), dtype=np.float32)
    idx = np.arange(FRAME_LEN)[None, :] + FRAME_SHIFT * np.arange(n)[:, None]
    frames = x[idx]
    # pre-emphasis（第一帧的第一点用原值）
    frames = np.concatenate([frames[:, :1], frames[:, 1:] - PREEMPH * frames[:, :-1]], axis=1)
    window = np.hamming(FRAME_LEN).astype(np.float32)  # Kaldi 用的是 0.54-0.46cos
    frames = frames * window
    spec = np.abs(np.fft.rfft(frames, n=512, axis=1)) ** 2
    mel = spec[:, :512 // 2] @ _MEL.T
    mel = np.maximum(mel, 1.0e-10)
    return np.log(mel).astype(np.float32)


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------
def _load_model():
    global _model
    with _lock:
        if _model is not None:
            return _model
        import torch

        if _SRC not in sys.path:
            sys.path.insert(0, _SRC)
        # 打补丁：funasr 的注册表 / 3.12+ 已删除的 distutils / autocast 都不需要
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "campplus_net", os.path.join(_SRC, "model.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["campplus_net"] = mod
        spec.loader.exec_module(mod)

        net = mod.CAMPPlus(feat_dim=80, embedding_size=192, growth_rate=32, bn_size=4,
                           init_channels=128, config_str="batchnorm-relu",
                           memory_efficient=True, output_level="segment")
        state = torch.load(_MODEL, map_location="cpu", weights_only=True)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        missing, unexpected = net.load_state_dict(state, strict=False)
        if missing:
            print(f"[CAM++] 警告：{len(missing)} 个参数没加载上，例如 {missing[:3]}")
        net.eval()
        _model = net
        return _model


def _pick_device():
    """有 CUDA 版 torch 就用显卡（CAM++ 很小，GPU 上基本瞬时）"""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.device("cuda")
    except Exception:
        pass
    return None


def embed(waveform: np.ndarray, sr: int = SR) -> Optional[np.ndarray]:
    """一段音频 → 192 维 L2 归一化声纹向量；音频太短返回 None"""
    x = np.asarray(waveform, dtype=np.float32)
    if len(x) < int(0.2 * sr):
        return None
    import torch

    feat = fbank(x, sr)
    if feat.shape[0] < 10:
        return None
    net = _load_model()
    dev = _pick_device()
    try:
        with torch.no_grad():
            t = torch.from_numpy(feat[None, :, :])
            if dev is not None:
                t = t.to(dev)
                net = net.to(dev)
            out = net(t)
        v = out.detach().cpu().numpy().reshape(-1).astype(np.float32)
    except Exception:
        # 显卡出问题就退回 CPU，不能因为加速把功能弄挂
        with torch.no_grad():
            out = net.to("cpu")(torch.from_numpy(feat[None, :, :]))
        v = out.detach().cpu().numpy().reshape(-1).astype(np.float32)
    n = np.linalg.norm(v) + 1e-9
    return v / n


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def available() -> bool:
    return os.path.exists(_MODEL) and os.path.exists(os.path.join(_SRC, "model.py"))
