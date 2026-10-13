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

def _default_cache_dir() -> str:
    """模型缓存目录。

    ⚠️ **冻结打包后不能用 `__file__`**：它指向 `_internal/`，于是默认缓存变成
    `<安装目录>\\_internal\\.hf_cache` —— 藏在包内部。后果：
      * 安装程序/doctor/huggingface_hub 都按 `<安装目录>\\.hf_cache` 走，
        这里却是 `_internal\\.hf_cache` → **装的地方和找的地方不是同一个**，
        "模型明明下好了，程序说本地缓存里没有"（真踩过）；
      * `_internal` 在升级/重装时被覆盖，模型跟着丢。
    所以冻结时以 **exe 所在目录**为准，源码运行时才用 `__file__`。
    """
    import sys as _sys
    if getattr(_sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(_sys.executable)),
                            ".hf_cache")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".hf_cache")


DEFAULT_CACHE_DIR = _default_cache_dir()

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
        cache_dir: str = "",
        cpu_threads: Optional[int] = None,
        log: Callable[[str], None] = print,
    ):
        self.model_name = model
        # ⚠️ **一定要认 `HUGGINGFACE_HUB_CACHE`**，不能只用 DEFAULT_CACHE_DIR。
        # 冻结打包后 `__file__` 指向 `_internal/`，于是默认缓存变成
        # `<安装目录>\_internal\.hf_cache` —— 藏在包内部：
        #   * 安装程序/主程序里凡是走环境变量的地方（doctor、download_model、
        #     huggingface_hub 自己）都指向 `<安装目录>\.hf_cache`，
        #     而这里指向 `_internal\.hf_cache` → **两边不是同一个目录**，
        #     于是"装好了模型，程序却说找不到"（真踩过，用户直接炸了）。
        #   * 升级/重装 `_internal` 被覆盖，模型跟着丢。
        self.cache_dir = (cache_dir
                          or os.environ.get("HUGGINGFACE_HUB_CACHE")
                          or DEFAULT_CACHE_DIR)
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

        ⚠️ 另外也扫 **程序目录下的 ``cuda\\``**：打包版**没有**带这些 DLL
        （cublas+cudnn 合计 2 GB，超过 GitHub Release 单文件上限），
        所以给用户留一个"把 DLL 丢进来就能用显卡"的位置，不用去动系统。
        """
        import glob
        import sys as _sys
        added: List[str] = []
        seen = set()
        # 程序目录（冻结时是 exe 所在目录）下的 cuda/ 与 cuda/**/bin
        app_dir = os.path.dirname(os.path.abspath(
            _sys.executable if getattr(_sys, "frozen", False) else __file__))
        candidates = [os.path.join(app_dir, "cuda"),
                      os.path.join(app_dir, "cuda", "bin")]
        # ① **程序目录下的 `cuda\` —— 直接注册这个目录本身。**
        # ⚠️ 不能把它混进下面那个 `nvidia/*/bin` 通配里：安装程序是把 DLL **摊平**
        # 放进 `cuda\` 的，通配 `cuda/nvidia/*/bin` 一个都匹配不到，
        # 结果一个目录都没 add_dll_directory ——
        # **检测说"DLL 都在"（检测有自己的搜索路径），一跑就报
        # `Library cublas64_12.dll is not found or cannot be loaded`**。真踩过。
        for d in candidates:
            if os.path.isdir(d) and d not in seen:
                seen.add(d)
                added.append(d)
                try:
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(d)
                except Exception:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

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

        # ⚠️⚠️ **打包版必须显式加 `_MEIPASS` 这一支**（真踩过，症状极具误导性）：
        #
        # PyInstaller 把 CUDA DLL 放在 `<exe 同级>/_internal/nvidia/<lib>/bin`。
        # 上面那段靠 `sys.path` 能不能命中，取决于打包方式有没有把 `_internal`
        # 放进 `sys.path` —— **一旦没命中，这里就一个目录都没注册**，于是：
        #   `WhisperModel(device="cuda")` 抛
        #   `Library cublas64_12.dll is not found or cannot be loaded`
        # → `ensure_loaded()` 把它当成"显卡不可用"退到 CPU
        # → **用户看到的是"转换慢得离谱"（几分钟 vs 十几秒），而不是报错。**
        #
        # 所以不要赌 `sys.path`：`_MEIPASS`（包内部）和 exe 同级目录都直接扫。
        # 空目录（比如裁剪后没打 cudnn）会被 `os.path.isdir` 过滤掉，
        # 不会注册无效路径。
        _meipass = getattr(_sys, "_MEIPASS", "")
        _extra_bases = [b for b in (_meipass, app_dir,
                                    os.path.join(app_dir, "_internal"))
                        if b and os.path.isdir(b)]
        for base in _extra_bases:
            for pat in (os.path.join(base, "nvidia", "*", "bin"),
                        os.path.join(base, "_internal", "nvidia", "*", "bin"),
                        os.path.join(base, "cuda"),
                        os.path.join(base, "cuda", "bin")):
                for d in glob.glob(pat):
                    if not os.path.isdir(d) or d in seen:
                        continue
                    seen.add(d)
                    try:
                        if hasattr(os, "add_dll_directory"):
                            os.add_dll_directory(d)
                    except Exception:
                        pass
                    os.environ["PATH"] = d + os.pathsep + os.environ.get(
                        "PATH", "")
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

        # ⚠️ **本项目强制 GPU**：CPU 跑一首歌要几分钟，静默降级只会让人
        # "觉得程序很慢"却不知道原因。所以这里直接拒绝，并把怎么修说清楚。
        if self.device != "cuda":
            raise RuntimeError(
                "没有可用的显卡，语音识别无法运行。\n"
                "  本项目**强制使用 GPU**（CPU 处理一首歌要几分钟，因此没有做 CPU 降级）。\n"
                "  请依次确认：\n"
                "    1) 机器上有 NVIDIA 显卡，且驱动正常（命令行跑 nvidia-smi 能出表格）\n"
                "    2) 显卡支持 CUDA 12（GTX 10 系及以后基本都可以）\n"
                "    3) CUDA 运行库已就位：双击「首次安装.bat」或「检查环境.bat」\n"
            )

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
            name = type(e).__name__
            msg = str(e)

            # ⚠️⚠️ **先分辨"模型没下载到"和"显卡/CUDA 用不了"** —— 两件事的处置完全不同。
            # （老代码把它们混成一句"GPU 初始化失败"，害得用户去查显卡驱动。）
            if self._is_download_error(e):
                raise RuntimeError(self._offline_help(path, name, msg)) from e

            # 显卡不可用 → **不再偷偷退回 CPU**（CPU 太慢，用户明确要求强制 GPU），
            # 而是直接报错并说清怎么修。静默降级的代价是"跑得慢还以为正常"。
            raise RuntimeError(self._gpu_help(name, msg)) from e
        return self._model

    def _gpu_help(self, name: str, msg: str) -> str:
        """显卡用不了时的提示。

        本项目**强制用 GPU**（CPU 跑一首歌要几分钟，体验不可接受），
        所以这里不降级，只负责把"怎么修"说清楚。
        """
        tip = (
            "CUDA 运行库缺失（cublas/cudnn）"
            if self._is_cuda_runtime_error(Exception(msg)) else "显卡初始化失败"
        )
        return (
            f"无法在显卡上运行语音识别：{tip}（{name}）。\n"
            f"  原始报错：{msg[:200]}\n"
            "\n"
            "  本项目**强制使用 GPU**（用 CPU 处理一首歌要几分钟，所以没有做 CPU 降级）。\n"
            "  按下面任一条修好即可：\n"
            "\n"
            "  ① 让程序自己补齐（推荐，双击就行）\n"
            "       「首次安装.bat」→ 会自动把 CUDA 运行库装到程序目录\n"
            "     或双击「检查环境.bat」看看具体缺什么\n"
            "\n"
            "  ② 手动放 DLL：把 cublas64_12.dll / cublasLt64_12.dll / cudnn*64_9.dll\n"
            "     放进程序目录下的 cuda\\ 文件夹（程序会自动把它加进搜索路径）\n"
            "\n"
            "  ③ 用 pip 装（源码运行时）\n"
            "       pip install nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
            "\n"
            "  另外确认显卡驱动是新的（nvidia-smi 能正常输出），且显卡支持 CUDA 12。"
        )

    @staticmethod
    def _is_download_error(e: BaseException) -> bool:
        """这个异常是不是"模型拿不到"（而不是"显卡用不了"）。

        按异常类名判断，不 import huggingface_hub（它可能没装）。
        """
        name = type(e).__name__
        if name in ("LocalEntryNotFoundError", "OfflineModeIsEnabled",
                    "RepositoryNotFoundError", "GatedRepoError",
                    "EntryNotFoundError", "RevisionNotFoundError"):
            return True
        # requests / httpx / urllib 的连接类错误
        if any(k in name for k in ("Connection", "Timeout", "HTTPError",
                                   "MaxRetry", "SSLError", "ProxyError",
                                   "NewConnection", "RemoteDisconnected")):
            return True
        text = str(e).lower()
        return ("cannot find the requested files in the local cache" in text
                or "couldn't connect" in text
                or "connection" in text and "cache" in text)

    def _offline_help(self, path: str, name: str, msg: str) -> str:
        """给"模型拿不到"写一条能照着做的提示。

        ⚠️ 一定要给出**可操作的下一步**。只说"下载失败"等于没说 ——
        用户既不知道模型该放哪、也不知道还能换个小的先用。
        """
        repo = _REPO_MAP.get(self.model_name, self.model_name)
        snap = os.path.join(self.cache_dir, "models--" + repo.replace("/", "--"))
        return (
            f"取不到语音识别模型「{self.model_name}」（{name}）。\n"
            f"  本地缓存：{snap}\n"
            f"  远端仓库：{repo}\n"
            f"  原始报错：{msg[:200]}\n"
            "\n"
            "  这不是显卡问题。首次使用需要联网下载一次模型（large-v3 约 3 GB），\n"
            "  下好后就能完全离线使用。三种办法任选其一：\n"
            "\n"
            "  ① 联网下载：确认能访问 huggingface.co。国内网络建议设镜像后重试\n"
            "       set HF_ENDPOINT=https://hf-mirror.com\n"
            "\n"
            f"  ② 复用已有的模型（换过电脑/换过目录时最快）：\n"
            f"       把别处 .hf_cache\\models--{repo.replace('/', '--')} 整个目录\n"
            f"       复制到 {self.cache_dir}\\ 下即可，不用重新下载\n"
            "\n"
            "  ③ 先用小模型跑通（精度略低，但体积小很多、下载快）：\n"
            "       --model small     （约 0.5 GB）\n"
            "       --model medium    （约 1.5 GB）\n"
            "     界面上对应「Whisper 模型」下拉框。\n"
            f"\n"
            f"  也可以用本地模型目录：把模型解压好后传 --model <目录路径>"
        )

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
        return self._generate(model, audio, language, initial_prompt, beam_size,
                              word_timestamps, vad_filter, temperature)

    def _generate(self, model, audio, language, initial_prompt, beam_size,
                  word_timestamps, vad_filter, temperature) -> List[AsrWord]:
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

    @staticmethod
    def _is_cuda_runtime_error(e: BaseException) -> bool:
        """是不是"显卡在，但 CUDA 运行库（cublas/cudnn）用不了"。

        保留这个判断是为了**给出有针对性的报错**（告诉用户去跑
        `首次安装.bat` 补 CUDA 运行库），而不是笼统一句"失败了"。
        """
        text = str(e).lower()
        return any(k in text for k in (
            "cublas", "cudnn", "cufft", "curand", "nvrtc",
            "is not found or cannot be loaded",
            "no kernel image is available"))

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
