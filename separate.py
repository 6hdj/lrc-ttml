"""
人声分离（Demucs htdemucs）。

作用：把伴奏从音频里剥掉，只留人声，然后**用干净的人声轨去做识别与声纹**。

为什么有用
----------
* ASR：整轨混音里鼓/贝斯会干扰 whisper，导致漏句和幻觉（本项目实测过
  "优优独播剧场"这种纯幻觉）；纯人声轨能显著减少这类问题
* 声纹：CAM++ 在混音上提取的声纹会被伴奏染色，同一歌手不同段落的相似度被拉低；
  人声轨上的声纹更干净，分段判断更稳
* 和声/叠唱：分离**不能**把两个同时张嘴的人拆开（那需要专门的歌手分离模型），
  但它能让人声层整体更干净，叠唱段落的声纹也不再被伴奏主导

权重默认下到项目里的 ``models/demucs``（机器上 Demucs 官方权重站可达）。
"""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))


def _cache_dir() -> str:
    """Demucs 权重目录 —— **必须与模型清单的落盘位置一致**。

    ⚠️⚠️ 2026-10-15 用户实测踩到：原来是 `os.path.join(_HERE, "models", "demucs")`，
    而**冻结后 `__file__` 在 `_internal/` 里** ⇒ 跑到
    `<程序目录>/_internal/models/demucs/hub/checkpoints` 找权重；
    可模型清单（`model_manifest.json` 的 demucs 条目）是按
    `verify.target` 落到 **`<程序目录>/models/demucs/hub/checkpoints`** 的。
    两边差一层 `_internal` ⇒ **明明装好了，运行时还去重下 80MB**。

    现在走 `desktop.paths.app_dir()`（冻结 = exe 旁边，源码 = 仓库根），
    与清单、与 CAM++ 那三处资源根保持同一个口径。
    """
    try:
        from desktop import paths as _p          # 不导入 Qt，安全
        return os.path.join(_p.app_dir(), "models", "demucs")
    except Exception:                            # noqa: BLE001
        return os.path.join(_HERE, "models", "demucs")


CACHE_DIR = _cache_dir()

# demucs 用 torch.hub 下权重，把 TORCH_HOME 指到项目里，避免写到用户目录（沙箱会拦）
os.environ.setdefault("TORCH_HOME", CACHE_DIR)

DEFAULT_MODEL = "htdemucs"          # 4 分轨，速度/质量平衡
SOURCES = ("drums", "bass", "other", "vocals")

_model = None
_model_name = None
_lock = threading.Lock()


def available() -> bool:
    """demucs 是否可用（装了包即可；权重首次使用时下载）"""
    return why_unavailable() == ""


def why_unavailable() -> str:
    """demucs 用不了时返回**具体原因**（可用时返回空串）。

    ⚠️ 为什么要分开：`available()` 只给 True/False，上层只能笼统说
    "没内置 demucs" —— 而真实原因可能是**缺 torch**（按需运行时没装）、
    缺 `einops`/`julius`/`tqdm`，或者别的导入错误。用户看到的提示必须
    指对方向，否则又是一次"软件在说谎"（2026-10-15 实测踩过）。
    """
    try:
        import demucs.pretrained  # noqa: F401
        import demucs.apply       # noqa: F401
        return ""
    except Exception as e:                          # noqa: BLE001
        name = type(e).__name__
        if isinstance(e, ModuleNotFoundError):
            return f"缺少 {getattr(e, 'name', '') or '依赖'}（{name}）"
        return f"{name}: {str(e)[:120]}"


def weights_present(model_name: str = DEFAULT_MODEL) -> bool:
    d = os.path.join(CACHE_DIR, "hub", "checkpoints")
    if not os.path.isdir(d):
        return False
    return any(f.endswith(".th") or f.endswith(".safetensors") for f in os.listdir(d))


def _pick_device(device: str = "auto") -> str:
    if device != "auto":
        return device
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    # torch 是 CPU 版时仍然可能只是想跑 CPU
    return "cpu"


class VocalSeparator:
    """把人声从混音里分离出来"""

    def __init__(self, model: str = DEFAULT_MODEL, device: str = "auto",
                 log: Callable[[str], None] = print, shifts: int = 1,
                 overlap: float = 0.25):
        self.model_name = model
        self.device = _pick_device(device)
        self.shifts = shifts
        self.overlap = overlap
        self.log = log
        self._m = None

    # ------------------------------------------------------------------
    def _load(self):
        global _model, _model_name
        with _lock:
            if self._m is not None:
                return self._m
            from demucs.pretrained import get_model
            if _model is not None and _model_name == self.model_name:
                self._m = _model
                return self._m
            self.log(f"[分离] 加载 Demucs {self.model_name}（{self.device}）")
            m = get_model(self.model_name)
            m.to(self.device)
            m.eval()
            _model, _model_name = m, self.model_name
            self._m = m
            return m

    # ------------------------------------------------------------------
    def separate_to_file(self, audio_path: str, out_path: str,
                         stem: str = "vocals") -> Optional[str]:
        """分离并把指定音轨写成 wav（16k 单声道，方便直接喂给 whisper/声纹）"""
        import torch
        from demucs.apply import apply_model
        from demucs.audio import AudioFile

        model = self._load()
        self.log(f"[分离] 读取音频：{os.path.basename(audio_path)}")
        wav = AudioFile(audio_path).read(streams=0, samplerate=model.samplerate,
                                         channels=model.audio_channels)
        ref = wav.mean(0)
        wav_n = (wav - ref.mean()) / (ref.std() + 1e-8)

        with torch.no_grad():
            out = apply_model(model, wav_n[None], device=self.device,
                              shifts=self.shifts, split=True, overlap=self.overlap,
                              progress=False)[0]
        out = out * ref.std() + ref.mean()
        idx = model.sources.index(stem)
        track = out[idx]

        # 转成 16k 单声道
        import julius
        track = julius.resample_frac(track, model.samplerate, 16000)
        mono = track.mean(0).clamp(-1, 1).cpu().numpy().astype(np.float32)

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        _write_wav(out_path, mono, 16000)
        self.log(f"[分离] 已写出人声轨：{os.path.basename(out_path)}"
                 f"（{len(mono) / 16000:.1f}s）")
        return out_path


def _write_wav(path: str, samples: np.ndarray, sr: int):
    import wave
    data = np.clip(samples, -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
