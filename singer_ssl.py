"""Sony CSL Paris `ssl-singer-identity` 适配器（**实验性，不进生产路径**）。

论文：Singer Identity Representation Learning using Self-Supervised Techniques
      （Bernardo Torres, Stefan Lattner, Gaël Richard）
代码：https://github.com/SonyCSLParis/ssl-singer-identity   （MIT）
权重：https://hf-mirror.com/BernardoTorres/singer-identity  （冻结版本，见 VARIANTS）

## 为什么走 TorchScript

`model.py` 在模块顶层 `import torchaudio.transforms` **和** `nnAudio.features`，
二者本机都没装（也不打算为一个实验装）。仓库同时发布了 `model.ts`，
官方加载路径 `load_model(name, torchscript=True)` 就是 `torch.jit.load` ——
**绕开这两个依赖**。代价：TorchScript 版只接受 **44.1 kHz** 输入
（源码里 `if input_sr != 44100: raise Exception(...)`）。

## 输入契约（从仓库配置核对，不是猜的）

* `sr: 44100`
* `nr_samples: 176000` = **4 秒**（训练用 4 秒片段）→ 见 embed_span 的 mode 说明
* 输出 **1000 维**；官方用法 `embeddings = model(audio_batch)`，shape `(B, 1000)`
* 训练数据是 `tencys_vocals` / `ghero_vocals_*` —— **隔离人声轨**，
  与我们的原始混音条件不同（这一点写进评估协议，不能假装没差别）
* 训练增强含 `pitch_shift 1~1.4` + `formant_shift`，目标是让表征对音高/音区不变
"""
from __future__ import annotations

import os
import threading
from typing import Optional

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_ROOT = os.path.join(HERE, "models", "singer_identity")
SR = 44100
TRAIN_CLIP = 176000          # 4 秒 @ 44.1kHz，训练时的片段长度

# 仓库 README 列出的 5 个预训练模型（顺序即仓库列出顺序，不含性能排序）
VARIANTS = ("byol", "contrastive", "contrastive-vc", "unif-align", "vicreg")
PRIMARY = "byol"             # 预先声明的主候选：README 首个；论文站点本机打不开，
                             # 无法据此排序，所以按列出顺序取第一个，不做事后挑选

_lock = threading.Lock()
_cache: dict = {}


def model_path(variant: str) -> str:
    return os.path.join(MODEL_ROOT, variant, "model.ts")


def available(variant: str = PRIMARY) -> bool:
    return os.path.exists(model_path(variant))


def load(variant: str = PRIMARY):
    """加载 TorchScript 模型（缓存；eval 模式；有 CUDA 就上 GPU）

    ⚠️ 必须用 **BytesIO** 而不是把绝对路径交给 ``torch.jit.load``：
    它底层走 C++ 的 ``fopen``，**路径含中文时找不到文件**
    （实测：路径里有中文目录时抛
    ``RuntimeError: open file failed because of errno 2``，
    换成相对路径或 BytesIO 都正常）。
    """
    with _lock:
        if variant in _cache:
            return _cache[variant]
        if not available(variant):
            raise FileNotFoundError(
                f"缺权重 {model_path(variant)}；从 hf-mirror 的 "
                f"BernardoTorres/singer-identity/{variant}/model.ts 下载")
        import io as _io

        import torch
        with open(model_path(variant), "rb") as f:
            blob = _io.BytesIO(f.read())
        m = torch.jit.load(blob, map_location="cpu")
        m.eval()
        try:
            if torch.cuda.is_available():
                m = m.to("cuda")
        except Exception:
            pass
        _cache[variant] = m
        return m


def _as_tensor(x: np.ndarray):
    import torch
    t = torch.from_numpy(np.asarray(x, dtype=np.float32)).reshape(1, -1)
    m = load(PRIMARY)
    try:
        if next(m.parameters()).is_cuda:
            t = t.to("cuda")
    except Exception:
        try:
            if torch.cuda.is_available():
                t = t.to("cuda")
        except Exception:
            pass
    return m, t


def embed(wave: np.ndarray, variant: str = PRIMARY) -> Optional[np.ndarray]:
    """一段 **44.1kHz** 单声道波形 → L2 归一化的 1000 维嵌入。

    传进来短于 0.2 秒就返回 None（与 CAM++ 适配器保持同样的边界行为）。
    """
    x = np.asarray(wave, dtype=np.float32)
    if len(x) < int(0.2 * SR):
        return None
    import torch
    m = load(variant)
    t = torch.from_numpy(x).reshape(1, -1)
    try:
        if next(m.parameters()).is_cuda:
            t = t.to("cuda")
    except Exception:
        pass
    with torch.no_grad():
        out = m(t)
    v = out.detach().cpu().numpy().reshape(-1).astype(np.float32)
    n = float(np.linalg.norm(v)) + 1e-9
    return v / n


def embed_span(wave: np.ndarray, begin: float, end: float,
               mode: str = "whole", variant: str = PRIMARY) -> Optional[np.ndarray]:
    """取 ``[begin, end)`` 这一段做嵌入。

    ``mode="whole"``（**默认，第一轮主候选**）：整段一次前向。
        与 CAM++ 走完全相同的跨度，保证"只换模型、不换预处理"。

    ``mode="4s"``：按训练条件切成 4 秒片段分别前向再平均。
        这是模型的**原生**输入条件（`nr_samples=176000`）。
        但注意：在 CAM++ 上"窗口平均"实测会毁掉身份信息（90.6%→50.0%），
        所以这里作为**对照**而非默认。
    """
    a, b = int(begin * SR), int(end * SR)
    a = max(0, a)
    b = min(len(wave), b)
    if b - a < int(0.2 * SR):
        return None
    seg = wave[a:b]

    if mode == "whole":
        return embed(seg, variant)

    vecs = []
    step = TRAIN_CLIP
    for s in range(0, len(seg), step):
        piece = seg[s:s + step]
        if len(piece) < int(0.2 * SR):
            continue
        v = embed(piece, variant)
        if v is not None:
            vecs.append(v)
    if not vecs:
        return None
    m = np.mean(vecs, axis=0)
    return m / (np.linalg.norm(m) + 1e-9)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def load_wave_44k(path: str) -> np.ndarray:
    """用项目现成的解码器读成 44.1kHz 单声道（避免自己接 ffmpeg）"""
    from faster_whisper.audio import decode_audio
    return decode_audio(path, sampling_rate=SR)
