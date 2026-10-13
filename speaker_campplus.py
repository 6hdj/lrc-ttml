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


def _model_roots() -> list:
    """声纹权重/网络定义可能所在的根目录，**按优先级**排。

    ⚠️ **冻结打包后不能只用 `__file__` 一个位置定死**。打包后 `__file__` 指向
    `_internal/`，而安装程序把权重下到 `<安装目录>/models/campplus` —— 两边对不上，
    `available()` 返回 False，`diarize` 就**静默降级成单人**：用户开了"强制双声部"
    却一行都没分，日志里连个错都没有（真踩过，极难查）。
    所以**exe 所在目录优先**，`_internal` 兜底。
    """
    import sys as _sys
    roots = []
    if getattr(_sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(_sys.executable)))
    roots.append(_HERE)
    out = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


def _find(rel: str) -> str:
    for r in _model_roots():
        p = os.path.join(r, rel)
        if os.path.exists(p):
            return p
    return os.path.join(_HERE, rel)


_SRC = _find(os.path.join("models", "campplus_src"))
_MODEL = _find(os.path.join("models", "campplus", "campplus_cn_common.bin"))
# ONNX 版（**首选后端**）：由 `tools/export_campplus_onnx.py` 从上面同一份权重
# 导出，数值一致性有实测（max|Δ| ≤ 2.3e-05，含生产常用的 801 帧）。
# 它只依赖 **onnxruntime**（已在主安装包里），**不需要 PyTorch** ——
# 于是"对唱分声部"在冻结版里也能用，而且用户不必再下 118 MB 的运行时组件。
_ONNX = _find(os.path.join("models", "campplus", "campplus.onnx"))

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
_onnx_sess = None
_backend_used = ""       # "onnx" / "torch"，只用于诊断上报


def backend() -> str:
    """当前会用哪个后端：`onnx` / `torch` / `""`（都不可用）。

    ⚠️ 顺序是**ONNX 优先**：它不需要 torch（见 `_ONNX` 的说明），
    而 torch 现在是"按需下载的运行时组件"，能不用就不用。
    用环境变量 `L2T_CAMPP_BACKEND=torch|onnx` 可以强制（排查用）。
    """
    force = (os.environ.get("L2T_CAMPP_BACKEND") or "").strip().lower()
    if force in ("onnx", "torch"):
        return force
    if os.path.exists(_ONNX):
        try:
            import onnxruntime                            # noqa: F401
            return "onnx"
        except Exception:                                 # noqa: BLE001
            pass
    try:
        import torch                                      # noqa: F401
        return "torch"
    except Exception:                                     # noqa: BLE001
        return ""


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
    """一段音频 → 192 维 L2 归一化声纹向量；音频太短返回 None。

    ⚠️ **前端（fbank）与后端（ONNX / torch）严格分离**：
    `fbank()` 是纯 numpy 的，两种后端共用同一份特征，
    所以"换后端"不会悄悄改变特征提取（需求明确要求保持这一点）。
    """
    global _backend_used
    x = np.asarray(waveform, dtype=np.float32)
    if len(x) < int(0.2 * sr):
        return None

    feat = fbank(x, sr)
    if feat.shape[0] < 10:
        return None

    be = backend()
    if be == "onnx":
        try:
            v = _embed_onnx(feat)
            _backend_used = "onnx"
        except Exception:                                 # noqa: BLE001
            # ⚠️ ONNX 出问题就退回 torch（如果装了），**不能让功能直接挂**
            v = _embed_torch(feat)
            _backend_used = "torch(onnx 失败回退)"
    elif be == "torch":
        v = _embed_torch(feat)
        _backend_used = "torch"
    else:
        raise RuntimeError(
            "没有可用的 CAM++ 推理后端：既没有 models/campplus/campplus.onnx"
            "（随程序分发），也没有可用的 PyTorch 运行时。")
    n = np.linalg.norm(v) + 1e-9
    return (v / n).astype(np.float32)


def _get_onnx_session():
    """懒加载 ONNX 会话（线程安全）。会话创建约几十毫秒，只做一次。"""
    global _onnx_sess
    with _lock:
        if _onnx_sess is None:
            import onnxruntime as ort
            # ⚠️ 只用 CPU：CAM++ 极小（单行约 10 ms），GPU 版的收益
            # 抵不上"多一个 CUDA 依赖 + 显存占用"的代价；而 ONNX 的 CUDA
            # provider 还需要额外的 CUDA DLL，与"主包只带 cublas"冲突。
            _onnx_sess = ort.InferenceSession(
                _ONNX, providers=["CPUExecutionProvider"])
        return _onnx_sess


def _embed_onnx(feat: np.ndarray) -> np.ndarray:
    """ONNX 后端：输入 (T, 80)，输出 (192,)。**未归一化**，归一化在 `embed()`。"""
    sess = _get_onnx_session()
    out = sess.run(None, {"feat": feat[None, :, :].astype(np.float32)})[0]
    return np.asarray(out).reshape(-1).astype(np.float32)


def _embed_torch(feat: np.ndarray) -> np.ndarray:
    """torch 后端（原实现，保持不变）。"""
    import torch
    net = _load_model()
    dev = _pick_device()
    try:
        with torch.no_grad():
            t = torch.from_numpy(feat[None, :, :])
            if dev is not None:
                t = t.to(dev)
                net = net.to(dev)
            out = net(t)
        return out.detach().cpu().numpy().reshape(-1).astype(np.float32)
    except Exception:                                     # noqa: BLE001
        # 显卡出问题就退回 CPU，不能因为加速把功能弄挂
        with torch.no_grad():
            out = net.to("cpu")(torch.from_numpy(feat[None, :, :]))
        return out.detach().cpu().numpy().reshape(-1).astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def available() -> bool:
    """**能真的推理**才算可用（权重 + 网络定义 + 至少一个推理后端）。

    ⚠️⚠️ 判据演进（每一步都是踩出来的，别退回去）：

    * 最早：`os.path.exists(_MODEL)` —— **只看权重**。网络定义丢了、
      或后端不可用时照样返回 True，`diarize` 静默降级成单人演唱。
    * 2026-10-13 上：加上"网络定义在不在"。
    * 2026-10-13 下（PyTorch 移出主包后）：再加上"**torch 能不能 import**"。
    * **现在**：判据是"**有没有可用的后端**"，而不是"有没有 torch" ——
      ONNX 后端（`campplus.onnx` + onnxruntime）**不需要 torch**，
      所以"没装 PyTorch"不再是"分声部不可用"。

    两种后端的文件要求不同，不能混着判：
      * **ONNX 后端**：只需要 `campplus.onnx` + onnxruntime。
        ⚠️ **不需要 `.bin`** —— ONNX 图是**自包含**的（结构与权重都在里面，
        由 `tools/export_campplus_onnx.py` 从同一份 `.bin` 导出并做过数值校验）。
        第一版这里写成"还要 `.bin` 存在"，结果打包版（只带 ONNX）里
        `available()` 返回 False、分声部**静默退回按歌词标记** ——
        实测：产物里 v1 32 行 / v2 0 行，等于分声部没生效。真踩过。
      * **torch 后端**：需要 `.bin` + `campplus_src/model.py` + torch。
    """
    be = backend()
    if be == "onnx":
        return os.path.exists(_ONNX)
    if be == "torch":
        return (os.path.exists(_MODEL)
                and os.path.exists(os.path.join(_SRC, "model.py")))
    return False


def missing_reason() -> str:
    """说清**到底缺什么**（需求第 9 条：不得把运行时缺失误报成权重缺失）。

    返回空串 = 什么都不缺。调用方把这段文案直接给用户看。
    """
    has_model = os.path.exists(_MODEL)
    has_src = os.path.exists(os.path.join(_SRC, "model.py"))
    has_onnx = os.path.exists(_ONNX)
    be = backend()

    if be == "onnx":
        # 能走到这里说明 backend() 已经确认 ONNX 文件在 + onnxruntime 可用
        return ""
    if be == "torch":
        if not has_model:
            return ("缺少 CAM++ 声纹权重（约 27 MB）—— "
                    "在「模型管理」里下载「CAM++ 声纹（分声部）」即可。")
        if not has_src:
            return ("缺少 CAM++ 网络定义（models/campplus_src）—— "
                    "这是随程序分发的文件，说明安装包不完整，请重新安装。")
        return ""

    # 两个后端都不可用：把两边的原因都讲清楚，别让用户猜
    onnx_why = ("campplus.onnx 不存在（可用 tools/export_campplus_onnx.py 生成）"
                if not has_onnx else "onnxruntime 不可用")
    return ("缺少可用的 CAM++ 推理后端：ONNX（" + onnx_why + "）与 PyTorch 都不可用。\n"
            "   ① 首选：确认 models/campplus/campplus.onnx 存在，"
            "且 onnxruntime 已安装（主安装包自带）；\n"
            "   ② 或到「模型管理 → 运行时组件」下载 PyTorch 运行时，"
            "并下载 CAM++ 声纹权重。")
