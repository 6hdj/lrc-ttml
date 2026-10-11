"""
ASR 模块：基于 faster-whisper 获取音频的**词级/字级时间戳**。

这是整条流水线的"听觉"部分——所有时间信息都来自真实音频，
不再由大模型凭空猜测。

要点
----
* 模型优先使用本地缓存（``.hf_cache``），无网络也能跑。
* 支持整首歌一次转写，也支持**按行切片转写**（配合 LRC 行级时间戳，
  精度显著更高，且不受 VAD 误判影响）。
* 关闭 VAD 默认值：流行歌曲的人声+伴奏混合常常骗过 silero VAD，
  会把大段歌词判成"非语音"而漏掉（实测漏掉 70% 以上）。
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

# 国内镜像（必须在 huggingface 相关导入前设置），仅在没有本地缓存时才需要
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".hf_cache")

# faster-whisper 的 HF 仓库名映射
_REPO_MAP = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
}


@dataclass
class AsrWord:
    """一个识别词/字，带时间戳（秒）"""
    text: str
    start: float
    end: float
    prob: float = 1.0


class WhisperASR:
    """faster-whisper 封装"""

    def __init__(
        self,
        model: str = "large-v3",
        device: str = "auto",
        compute_type: Optional[str] = None,
        cache_dir: str = DEFAULT_CACHE_DIR,
        cpu_threads: Optional[int] = None,
        log: Callable[[str], None] = print,
    ):
        self.model_name = model
        self.cache_dir = cache_dir
        self.cpu_threads = cpu_threads or (os.cpu_count() or 4)
        self.log = log
        self._model = None
        self._audio_cache: dict = {}
        # whisper 实际检测到的语言（`language=None` 自动检测时用）。
        # 生成 TTML 的 `xml:lang` 就靠它 —— 选了"自动检测"还hardcode zh-Hans
        # 会让英文歌的 xml:lang 写错。
        self.last_language = ""

        if device == "auto":
            device = self._detect_device()
        self.device = device
        if compute_type:
            self.compute_type = compute_type
        else:
            self.compute_type = "float16" if device == "cuda" else "int8"

    # ------------------------------------------------------------------ 模型
    @staticmethod
    def _detect_device() -> str:
        """优先看 ctranslate2（faster-whisper 的实际推理后端），再看 torch。

        注意：torch 可能是 CPU-only 版本（``2.x.x+cpu``），此时
        ``torch.cuda.is_available()`` 为 False，但 ctranslate2 仍然能用显卡。
        """
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:
            pass
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"

    @staticmethod
    def _register_cuda_dlls() -> List[str]:
        """把 pip 装的 nvidia-*-cu12 里的 DLL 目录加到搜索路径。

        ctranslate2 的 CUDA 版需要 ``cublas64_12.dll`` / ``cudnn*64_9.dll``，
        这些 DLL 在 ``site-packages/nvidia/<lib>/bin`` 下，必须显式注册。
        """
        import glob
        import sys as _sys
        added: List[str] = []
        seen = set()
        for base in list(_sys.path):
            if not base or not os.path.isdir(base):
                continue
            for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
                if not os.path.isdir(d) or d in seen:
                    continue
                seen.add(d)
                try:
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(d)
                except Exception:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                added.append(d)
        return added

    def resolve_model_path(self) -> str:
        """返回可直接加载的模型路径或名称（优先本地缓存）"""
        if os.path.isdir(self.model_name):
            return self.model_name

        repo = _REPO_MAP.get(self.model_name, self.model_name)
        pattern = os.path.join(self.cache_dir, "models--" + repo.replace("/", "--"), "snapshots", "*")
        for snap in sorted(glob.glob(pattern)):
            if glob.glob(os.path.join(snap, "model.bin")):
                return snap
        # 本地没有 → 交给 faster-whisper 下载（需要网络）
        self.log(f"[ASR] 本地缓存中未找到 {self.model_name}，将尝试联网下载 {repo}")
        return repo

    def ensure_loaded(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "缺少 faster-whisper，请先安装：pip install faster-whisper"
            ) from e

        path = self.resolve_model_path()

        if self.device == "cuda":
            for d in self._register_cuda_dlls():
                self.log(f"[ASR] 注册 CUDA 库目录: {d}")

        self.log(f"[ASR] 加载模型 {os.path.basename(path)} "
                 f"({self.device}, {self.compute_type}, {self.cpu_threads}线程)")
        try:
            self._model = WhisperModel(
                path,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
            )
        except Exception as e:
            if self.device != "cuda":
                raise
            # 显卡不可用（缺 DLL、显存不足…）时自动退回 CPU，别让整个任务失败
            self.log(f"[ASR] ⚠ GPU 初始化失败（{type(e).__name__}: {str(e)[:120]}），回退 CPU")
            self.device = "cpu"
            self.compute_type = "int8"
            self._model = WhisperModel(
                path,
                device="cpu",
                compute_type="int8",
                cpu_threads=self.cpu_threads,
            )
        return self._model

    # ------------------------------------------------------------------ 音频
    def load_audio(self, audio_path: str):
        """解码为 16k 单声道 float32（相同文件只解码一次）"""
        key = os.path.abspath(audio_path)
        if key not in self._audio_cache:
            from faster_whisper.audio import decode_audio
            self._audio_cache[key] = decode_audio(audio_path, sampling_rate=16000)
        return self._audio_cache[key]

    # -------------------------------------------------------------- 转写 API
    def transcribe(
        self,
        audio,
        language: str = "zh",
        initial_prompt: Optional[str] = None,
        beam_size: int = 5,
        word_timestamps: bool = True,
        vad_filter: bool = False,
        temperature: float = 0.0,
    ) -> List[AsrWord]:
        """
        转写整段音频。

        ``audio`` 可以是文件路径，也可以是 16k float32 numpy 数组。
        """
        model = self.ensure_loaded()
        if isinstance(audio, str):
            audio = self.load_audio(audio)

        segments, _info = model.transcribe(
            audio,
            language=language or None,
            beam_size=beam_size,
            word_timestamps=word_timestamps,
            vad_filter=vad_filter,
            initial_prompt=initial_prompt or None,
            condition_on_previous_text=False,
            temperature=temperature,
        )
        # 记下 whisper 判定的语言（自动检测时才有意义）。切片模式下每片都会更新，
        # 取到哪片算哪片 —— 中英混唱本来就可能一片一个语言，主语言按**多数**更合理，
        # 但这里只用于 xml:lang 兜底，不参与对齐，保持简单。
        det = str(getattr(_info, "language", "") or "")
        if det:
            self.last_language = det
        return self._collect(segments)

    def transcribe_slice(
        self,
        audio_array,
        offset: float,
        duration: float,
        language: str = "zh",
        initial_prompt: Optional[str] = None,
        beam_size: int = 5,
    ) -> List[AsrWord]:
        """转写音频的一段（秒），返回的时间戳已加上 ``offset`` 偏移。"""
        sr = 16000
        a = max(0, int(offset * sr))
        b = min(len(audio_array), int((offset + duration) * sr))
        if b - a < int(0.15 * sr):
            return []
        chunk = audio_array[a:b]
        words = self.transcribe(
            chunk,
            language=language,
            initial_prompt=initial_prompt,
            beam_size=beam_size,
            vad_filter=False,
        )
        out = []
        for w in words:
            start = w.start + offset
            end = w.end + offset
            if end > start:
                out.append(AsrWord(w.text, start, end, w.prob))
        return out

    @staticmethod
    def _collect(segments) -> List[AsrWord]:
        words: List[AsrWord] = []
        for seg in segments:
            if not getattr(seg, "words", None):
                continue
            for w in seg.words:
                if w.start is None or w.end is None:
                    continue
                text = (w.word or "").strip()
                if not text:
                    continue
                words.append(AsrWord(text, float(w.start), float(w.end), float(getattr(w, "probability", 1.0) or 0.0)))
        return words


def audio_duration(audio_path: str) -> Optional[float]:
    """读取音频时长（秒），优先 ffprobe，失败时用解码结果"""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", audio_path],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except Exception:
        pass
    try:
        from faster_whisper.audio import decode_audio
        return len(decode_audio(audio_path, sampling_rate=16000)) / 16000.0
    except Exception:
        return None


def is_auto_language(language: str) -> bool:
    """是不是"让 whisper 自己检测"。

    中英混唱的歌**必须**用这个：写死 ``language="zh"`` 会让英文段按中文解码，
    识别质量明显下降。whisper 的 ``language=None`` 就是自动检测。
    """
    return (language or "").strip().lower() in ("", "auto", "detect", "自动", "自动检测")


def normalize_language_code(language: str) -> str:
    """统一语言代码（TTML 用 BCP-47 风格）。

    ⚠️ ``auto`` 不能原样返回 —— 它不是合法的 BCP-47，写进 ``xml:lang`` 会让
    XML 校验挂掉。调用方（``pipeline``）在自动检测时应改用**实际检测到的语言**；
    这里退化成默认的 ``zh-Hans`` 只是最后兜底。
    """
    if is_auto_language(language):
        return "zh-Hans"
    table = {
        "zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-hans": "zh-Hans",
        "zh-tw": "zh-Hant", "zh-hant": "zh-Hant", "chs": "zh-Hans", "cht": "zh-Hant",
        "en": "en", "ja": "ja", "jp": "ja", "ko": "ko", "kr": "ko",
        "fr": "fr", "de": "de", "es": "es", "ru": "ru",
    }
    return table.get((language or "zh").lower(), language or "zh-Hans")


def whisper_language_code(language: str) -> str:
    """TTML 语言代码 → whisper 语言代码。

    ⚠️ 返回**空串**表示"自动检测"（``WhisperASR.transcribe`` 里 ``language or None``）。
    以前这里会把 ``auto`` 原样返回，whisper 收到 ``language="auto"`` 会报错/误判。
    """
    if is_auto_language(language):
        return ""
    if (language or "").lower().startswith("zh"):
        return "zh"
    return (language or "zh").split("-")[0].lower()
