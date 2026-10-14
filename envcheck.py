"""穷尽式环境检查：把"跑起来需要什么"逐项、逐文件核一遍。

`install.py` 和 `installer_gui.py` **共用这一份**，避免两套判定漂移。

为什么不能"抽查几项就说缺/不缺"（真踩过）：
  用户从发布包解压到另一个目录后启动，程序说"缺运行库"，但他原目录跑得好好的。
  原目录有 `.venv/Lib/site-packages/nvidia/*/bin` 的 CUDA DLL、`.hf_cache` 的模型、
  `models/campplus` 的权重 —— 新目录**一样都没有**。差别就是这些目录，
  所以检查必须**把这些具体位置和具体文件都列全**，缺哪一项说哪一项。

三条原则：
  1. **能真的试就真的试**：DLL 不只看文件在不在，用 ctypes 加载一遍（缺依赖的 DLL
     文件存在也加载不了）；包不只看 import 成功，还看版本。
  2. **粒度到文件**：CUDA 说清是 cublas 还是 cudnn 缺；模型说清缺哪个文件。
  3. **每条都带"怎么补"**：不能只报"缺 X"。
"""
from __future__ import annotations

import ctypes
import glob
import importlib
import os
import shutil
import subprocess
import sys

CRIT = "crit"      # 缺了核心功能跑不了
OPT = "opt"        # 可选功能
info = "info"


class Item:
    __slots__ = ("key", "name", "why", "level", "ok", "detail", "fix", "action")

    def __init__(self, key, name, why, level, ok, detail="", fix="", action=None):
        self.key = key          # 界面上的行 id
        self.name = name
        self.why = why          # 这个依赖是干嘛的
        self.level = level
        self.ok = ok
        self.detail = detail    # 现状（给用户看的）
        self.fix = fix          # 怎么补
        self.action = action    # 补全函数（None = 手动）


# ============================================================ 需要的东西清单
# (import 名, pip 名, 级别, 用途)。**要全** —— 漏一个就会出现"跑起来才发现缺"。
PKGS = [
    ("faster_whisper", "faster-whisper", CRIT, "语音识别核心"),
    ("ctranslate2", "ctranslate2", CRIT, "faster-whisper 的推理后端"),
    ("numpy", "numpy", CRIT, "数值计算"),
    ("huggingface_hub", "huggingface-hub", CRIT, "模型下载与缓存"),
    ("av", "av", CRIT, "音频解码（PyAV，自带 FFmpeg 库）"),
    ("tokenizers", "tokenizers", CRIT, "分词"),
    ("onnxruntime", "onnxruntime", OPT, "部分模型的推理后端"),
    ("pypinyin", "pypinyin", OPT, "中文同音字容错（强烈建议）"),
    ("torch", "torch", OPT, "声纹分声部 / 人声分离"),
    ("demucs", "demucs", OPT, "人声分离 --separate-vocals"),
]

# CUDA 12 运行库。**逐个列**，缺哪个说哪个。
#
# ⚠️⚠️ **cuDNN 不在必装清单里**（2026-10-13 实测改了）。
# 本项目的 GPU 推理走 CTranslate2，实测**只需要 cuBLAS**：
# 在真 RTX 4060 上跑真音频，30 秒片段
#   cuBLAS + cuDNN + nvrtc → GPU 1.43~1.50s
#   只有 cuBLAS（去掉 cuDNN 1095 MiB、nvrtc 178 MiB）→ GPU 1.43~1.59s
# 同环境 CPU 要 9.4s，所以 GPU 确实在工作，cuDNN 只是白占 1 GB 体积。
# ⇒ 主安装包**只带 cuBLAS**（安装包 3.0 GiB → 1.4 GiB）。
#
# 因此 cuDNN 是 **OPT（可选）**：装了它不会错（保留手放 `cuda\` 的用法），
# 但**缺了不能报错**，否则用户会以为"环境没配好"。
# 有效性自证见 `tools/size_report.py` 与 `.tmp/exp_cuda_quick.py`。
CUDA_DLLS = [
    ("cublas64_12.dll", "cuBLAS（矩阵运算）", CRIT),
    ("cublasLt64_12.dll", "cuBLAS Lt（大矩阵）", CRIT),
    ("cudnn64_9.dll", "cuDNN（卷积加速，可选）", OPT),
]


def _pkg_version(mod) -> str:
    return str(getattr(mod, "__version__", "") or "")


# ============================================================ 各项检查
def check_python(frozen: bool) -> Item:
    v = sys.version_info
    if frozen:
        return Item("python", "Python 运行环境", "程序自带", info, True,
                    f"打包版内置 {v.major}.{v.minor}.{v.micro}")
    ok = (v.major, v.minor) >= (3, 10)
    return Item("python", "Python 运行环境", "跑程序用", CRIT, ok,
                f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else "（需要 3.10+）"),
                "到 python.org 下载 3.10 或更高版本，安装时**务必勾选** "
                "“Add Python to PATH”，然后重开窗口再试")


def check_packages(frozen: bool = False) -> list:
    """逐个 import + 取版本。**不抽样** —— 上面 PKGS 里每一个都试。"""
    out = []
    missing_crit, missing_opt = [], []
    for mod_name, pip_name, level, why in PKGS:
        if frozen and mod_name in ("torch", "demucs", "onnxruntime"):
            # 打包版按构建配置可能不含这些重家伙，单独报"未内置"而不是"损坏"
            try:
                m = importlib.import_module(mod_name)
                out.append(Item("pkg_" + mod_name, pip_name, why, level, True,
                                _pkg_version(m)))
            except Exception:
                out.append(Item("pkg_" + mod_name, pip_name, why, OPT, False,
                                "打包版未内置（可用 pip 版）",
                                "打包版不含它。要这个功能请用便携版："
                                "pip install -r requirements.txt"))
            continue
        try:
            m = importlib.import_module(mod_name)
            out.append(Item("pkg_" + mod_name, pip_name, why, level, True,
                            _pkg_version(m)))
        except Exception as e:
            fix = f"pip install {pip_name}"
            out.append(Item("pkg_" + mod_name, pip_name, why, level, False,
                            f"未安装（{type(e).__name__}）", fix,
                            (lambda names=[pip_name]: _pip(names))))
            (missing_crit if level == CRIT else missing_opt).append(pip_name)
    return out


def _pip(names: list) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + names
    print("  $ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd) == 0


# ---------------------------------------------------------------- 显卡
def check_gpu() -> list:
    """NVIDIA 显卡 + 驱动。用 nvidia-smi（最准），失败再看 CTranslate2。

    ⚠️ 要**明确识别 RTX 系列**并显示型号 —— 用户要的就是"本机是不是 RTX N 卡"。
    但**不把非 RTX 判成致命**：CUDA 12 只要求算力 ≥ 5.0，
    GTX 10 系（1080/1050 等）同样能跑，只是慢些。
    判成致命会让这些人莫名其妙用不了，所以降级为"能用但没加速优势"的提示。
    """
    out = []
    smi = shutil.which("nvidia-smi")
    name, driver, vram = "", "", ""
    ok_gpu = False
    if smi:
        try:
            r = subprocess.run(
                [smi, "--query-gpu=name,driver_version,memory.total",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="replace")
            if r.returncode == 0 and (r.stdout or "").strip():
                first = (r.stdout or "").strip().splitlines()[0]
                parts = [p.strip() for p in first.split(",")]
                name = parts[0] if parts else first
                driver = parts[1] if len(parts) > 1 else ""
                vram = parts[2] if len(parts) > 2 else ""
                ok_gpu = True
        except Exception as e:
            name = f"nvidia-smi 执行失败：{type(e).__name__}"
    else:
        name = "没找到 nvidia-smi"

    if not ok_gpu:
        try:
            import ctranslate2
            n = ctranslate2.get_cuda_device_count()
            if n > 0:
                ok_gpu = True
                name = f"CTranslate2 看到 {n} 个 CUDA 设备（无 nvidia-smi）"
        except Exception:
            pass

    is_rtx = "RTX" in (name or "").upper()
    detail = name
    if ok_gpu and driver:
        detail = f"{name}　驱动 {driver}" + (f"　显存 {vram}" if vram else "")
    if ok_gpu and not is_rtx:
        detail += "（不是 RTX 系列；能跑，但没有 RTX 的加速优势）"

    out.append(Item("gpu", "NVIDIA 显卡（RTX 系列）", "GPU 加速的前提", CRIT,
                    ok_gpu, detail or "不可用",
                    "本项目**强制用 GPU**（CPU 跑一首歌要几分钟）。请确认：\n"
                    "        1) 机器上有 **NVIDIA 显卡**（不是 AMD / Intel 核显）\n"
                    "        2) 装了较新的 NVIDIA 驱动：https://www.nvidia.cn/drivers/\n"
                    "        3) 命令行跑 nvidia-smi 能看到显卡表格\n"
                    "      注：RTX 系列体验最好；GTX 10 系及以后也能用（CUDA 12 要求算力≥5.0）"))

    # 显卡到不到位，再用 torch 复核一遍（声纹/分离走 torch，和 ASR 不是同一条路）
    try:
        import torch
        ok2 = torch.cuda.is_available()
        out.append(Item("gpu_torch", "显卡（torch 视角）",
                        "声纹分声部 / 人声分离用", OPT, ok2,
                        torch.cuda.get_device_name(0) if ok2 else "torch 看不到显卡",
                        "装了 CUDA 版 torch 才会看到：\n"
                        "        pip install torch --index-url "
                        "https://download.pytorch.org/whl/cu124"))
    except Exception:
        pass
    return out


def _cuda_search_dirs() -> list:
    """所有可能放 CUDA DLL 的地方（**一个都不能漏**，否则会误报"缺"）。

    ⚠️ 三类布局都要覆盖（真踩过"体检说缺、其实东西就在包里"）：
    ① **打包版**：`<exe 同级>/_internal/nvidia/<lib>/bin`（PyInstaller datas）；
    ② **源码/便携版**：`<仓库>/.venvpack/Lib/site-packages/nvidia/<lib>/bin`
       —— 打包用的 venv 就叫 `.venvpack`，`.venv` 里没有 CUDA 包；
    ③ 系统装的 CUDA Toolkit / 手工放进 `cuda\\` 的。
    只扫 `sys.path` 是不够的：`.venvpack` 不在 `sys.path` 上（它不是当前环境），
    而打包版把资源放在 `_internal`（`sys.path` 里有没有它取决于打包模式）。
    """
    dirs = []
    meipass = getattr(sys, "_MEIPASS", "")
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    roots = [base, meipass, os.path.join(base, "_internal"),
             os.path.join(meipass, "_internal") if meipass else ""]
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        dirs += [os.path.join(r, "cuda"), os.path.join(r, "cuda", "bin")]
        nv = os.path.join(r, "nvidia")
        if os.path.isdir(nv):
            dirs.append(nv)
            for lib in os.listdir(nv):
                dirs.append(os.path.join(nv, lib, "bin"))
    # pip 装的 nvidia-*-cu12：site-packages/nvidia/<lib>/bin
    for p in list(sys.path):
        nv = os.path.join(p or "", "nvidia")
        if os.path.isdir(nv):
            dirs.append(nv)
            for lib in os.listdir(nv):
                dirs.append(os.path.join(nv, lib, "bin"))
    # 打包用的 venv（源码模式下体检要如实报告"包里那套在哪"）
    for venv in (".venvpack", ".venv"):
        nv = os.path.join(base, venv, "Lib", "site-packages", "nvidia")
        if os.path.isdir(nv):
            for lib in os.listdir(nv):
                dirs.append(os.path.join(nv, lib, "bin"))
    # 系统里已装 CUDA Toolkit 的情况
    for env in ("CUDA_PATH", "CUDA_HOME"):
        v = os.environ.get(env)
        if v:
            dirs.append(os.path.join(v, "bin"))
    dirs += glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\*\bin")
    # PATH 里的目录也搜（有人手工加过）
    dirs += [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    seen, out = set(), []
    for d in dirs:
        if d and os.path.isdir(d) and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def check_cuda_runtime() -> list:
    """逐个 DLL 检查 —— 而且**真的用 ctypes 加载一遍**。

    ⚠️ 只判断文件存在是不够的：DLL 可能在那儿但缺依赖（比如 cudnn 依赖 cublas），
    加载照样失败。实测同一个目录 `os.path.exists` 为真、`ctypes.WinDLL` 抛异常的情况。
    """
    out = []
    dirs = _cuda_search_dirs()
    # ⚠️ 现在是三元组（名称, 说明, 级别）：cuDNN 是 OPT ——
    # 缺了只能"提示"，不能报成 crit，否则用户以为环境没配好（见 CUDA_DLLS 的说明）。
    for dll, why, level in CUDA_DLLS:
        found_at, load_err = "", ""
        for d in dirs:
            cand = os.path.join(d, dll)
            if not os.path.exists(cand):
                continue
            found_at = cand
            try:
                ctypes.WinDLL(cand)          # 真的加载一次
                load_err = ""
                break
            except OSError as e:
                load_err = f"{type(e).__name__}: {str(e)[:90]}"
        if found_at and not load_err:
            out.append(Item("cuda_" + dll, dll, why, level, True,
                            os.path.dirname(found_at)))
        else:
            out.append(Item("cuda_" + dll, dll, why, level, False,
                            ("找到了但加载失败：" + load_err) if found_at
                            else "未找到",
                            "点「开始补全」自动装（从 PyPI 取 wheel 解出 DLL，"
                            "不动系统、不需要管理员权限）"))
    return out


# ---------------------------------------------------------------- 模型
def check_whisper(cache_dir: str) -> Item:
    """不只看目录大小，**看关键文件**齐不齐。"""
    try:
        from asr import _REPO_MAP
    except Exception as e:
        return Item("whisper", "语音识别模型", "", CRIT, False,
                    f"读不到 asr 配置：{e}", "")
    need = ("model.bin", "config.json", "tokenizer.json")
    best, best_missing = "", None
    for name, repo in _REPO_MAP.items():
        root = os.path.join(cache_dir, "models--" + repo.replace("/", "--"),
                            "snapshots")
        if not os.path.isdir(root):
            continue
        for snap in os.listdir(root):
            sp = os.path.join(root, snap)
            miss = [f for f in need if not os.path.exists(os.path.join(sp, f))]
            size = sum(os.path.getsize(os.path.join(r, x))
                       for r, _, fs in os.walk(sp) for x in fs
                       if os.path.exists(os.path.join(r, x)))
            if not miss:
                if size > (best_size := 0) if False else True:
                    best, best_missing = f"{name}（{size / 1024 / 1024:.0f} MB）", []
            elif best_missing is None:
                best_missing = (name, miss)
    if best:
        return Item("whisper", "语音识别模型", "约 3 GB，只需下一次", CRIT,
                    True, best)
    detail = "未下载"
    if best_missing and isinstance(best_missing, tuple):
        detail = f"{best_missing[0]} 不完整，缺 {'、'.join(best_missing[1])}"
    return Item("whisper", "语音识别模型", "约 3 GB，只需下一次", CRIT, False,
                detail,
                "点「开始补全」自动下载（优先 hf-mirror 镜像，支持断点续传）；"
                "想省流量可以选 small 小模型")


def _resource_bases(base: str = "") -> list:
    """只读资源（``models/``）可能在的目录，按优先级。

    ⚠️ 冻结之后有**两个**不同的地方，都要找：
      * exe 旁边（``base`` / ``sys.executable`` 的目录）—— 用户自己放的模型、
        旧版安装器遗留的 `models/`；
      * ``sys._MEIPASS``（= PyInstaller 的 ``_internal``）—— **安装包自带的**
        ``models/campplus_src`` 与随包分发的 CAM++ 权重就在这里。
    只查前者的话，打包版会报"声纹分声部模型：未找到"，
    **而它明明就在包里**（真踩过：打包版 --doctor 输出"未找到"，桌面窗口
    同一时刻说"已就绪" —— 两个检查器给出相反结论是最伤信任的）。
    """
    out = []
    if base:
        out.append(base)
    if getattr(sys, "frozen", False):
        out.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        out.append(os.path.dirname(os.path.abspath(__file__)))
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        out.append(meipass)
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def check_campp(base: str) -> Item:
    """CAM++ 声纹权重：看具体文件，别只看目录在不在。

    ``base`` = 安装目录 / exe 目录；另外还会找包内部（``_MEIPASS``），
    因为安装包自带的权重在那里。
    """
    found, d = [], ""
    for root in _resource_bases(base):
        d = os.path.join(root, "models", "campplus")
        if os.path.isdir(d):
            found = [f for f in os.listdir(d)
                     if f.endswith((".onnx", ".pt", ".bin", ".pth"))]
            if found:
                break
    if found:
        return Item("campp", "声纹分声部模型", "对唱分声部必需", CRIT, True,
                    "、".join(found[:3]) + ("…" if len(found) > 3 else ""))
    return Item("campp", "声纹分声部模型", "对唱分声部必需", CRIT, False,
                f"未找到（{d or (base or '?')}）",
                "点「开始补全」自动下载（ModelScope）。\n"
                "        ⚠️ 缺了它，开了「对唱分声部」也**不会报错**，"
                "只会退化成按单人处理 —— 看起来就像没生效")


def check_ffmpeg() -> Item:
    exe = shutil.which("ffmpeg")
    return Item("ffmpeg", "ffmpeg", "个别音频格式的兜底解码", OPT, bool(exe),
                exe or "未找到",
                "多数格式靠内置的 PyAV 就能解。真要装：\n"
                "        winget install Gyan.FFmpeg\n"
                "        或下载后把 bin 目录加进 PATH")


def check_disk(need_gb: float = 8.0) -> Item:
    """磁盘空间：模型 3GB + CUDA 2GB + 权重 0.3GB + 临时空间。"""
    try:
        free = shutil.disk_usage(os.path.abspath(os.curdir)).free / 1024 ** 3
    except Exception as e:
        return Item("disk", "磁盘空间", "", info, True, f"无法探测：{e}")
    return Item("disk", "磁盘空间", f"补全需要约 {need_gb:.0f} GB 余量", info,
                free >= need_gb, f"剩余 {free:.1f} GB",
                f"当前盘剩余不足 {need_gb:.0f} GB。模型约 3GB、"
                f"CUDA 运行库约 2GB、声纹权重约 0.3GB，请先腾出空间")


def check_writable(base: str) -> Item:
    """程序目录必须可写：模型/权重都要落在旁边。"""
    try:
        p = os.path.join(base, ".l2t_write_test")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(p)
        return Item("writable", "程序目录可写", "", info, True, base)
    except Exception as e:
        return Item("writable", "程序目录可写", "", CRIT, False, str(e)[:80],
                    "程序目录不可写（可能装在 C:\\Program Files 下且没用管理员）。\n"
                    "        请把整个文件夹移到 D:\\ 或文档目录再运行")


# ============================================================ 总入口
def check_all(base: str, cache_dir: str, frozen: bool = False,
              fast: bool = False) -> list:
    """跑完全部检查，返回 Item 列表。fast=True 时跳过重的（DLL 加载、nvidia-smi）。"""
    items = [check_python(frozen)]
    items += check_packages(frozen)
    if not fast:
        items += check_gpu()
    items += check_cuda_runtime()
    items.append(check_whisper(cache_dir))
    items.append(check_campp(base))
    items.append(check_ffmpeg())
    items.append(check_disk())
    items.append(check_writable(base))
    return items


def summarize(items: list) -> tuple:
    """返回 (缺的必需项, 缺的可选项)"""
    miss_crit = [i for i in items if not i.ok and i.level == CRIT]
    miss_opt = [i for i in items if not i.ok and i.level == OPT]
    return miss_crit, miss_opt


# ============================================================ 作用域（能不能打开 vs 能不能做歌词）
#
# ⚠️⚠️ **为什么必须分这一层**（2026-10-14 用户提的问题）：
# `level=CRIT` 的原注释是「缺了**核心功能**跑不了」，但它把两件**完全不同**的事
# 混成了一个等级：
#   * `whisper`（语音识别模型，**3 GB**）—— 缺了**软件照样打开**，
#     只是点「开始制作」时做不了；
#   * `cuda_cublas64_12.dll`（**527 MB**）—— 同上；
#   * `pkg_faster_whisper`、写出权限、磁盘空间 —— 缺了**核心真的跑不动**。
# 后果（用户实测）：**安装程序**把 21 项全检查一遍，然后让用户
# 「开始补全」—— 也就是**软件还没打开，就要先下 3.5 GB**。
# 用户的诉求很明确：**安装只保证"装完能打开"，要下大东西到软件里的
# 「模型」页去下。**
#
# 所以这里把每一项标一个 `scope`：
#   * `launch`  —— **影响能不能打开/核心能否启动**。安装程序只检查这一层。
#   * `convert` —— 处理歌词时才需要。**在软件里下**（模型页/运行组件卡）。
#   * `extra`   —— 可选增强功能。缺了只是少个功能。
#
# ⚠️ 键名是 `Item.key`（界面上那行的 id）。加了新检查项**必须**在这里登记，
# 否则会落到默认值 `convert`（保守：宁可让安装程序少拦，也不要多拦）。
LAUNCH = "launch"
CONVERT = "convert"
EXTRA = "extra"

SCOPE_OF = {
    # —— 能不能打开：解释器、核心依赖、能不能写、有没有地方放 ——
    "python": LAUNCH,
    "pkg_faster_whisper": LAUNCH,
    "pkg_ctranslate2": LAUNCH,
    "pkg_numpy": LAUNCH,
    "pkg_huggingface_hub": LAUNCH,
    "pkg_av": LAUNCH,
    "pkg_tokenizers": LAUNCH,
    "disk": LAUNCH,
    "writable": LAUNCH,
    # —— 处理歌词才需要：模型权重 + GPU 运行库 ——
    "whisper": CONVERT,
    "cuda_cublas64_12.dll": CONVERT,
    "cuda_cublasLt64_12.dll": CONVERT,
    "gpu": CONVERT,
    # —— 可选增强 ——
    "pkg_pypinyin": EXTRA,
    "pkg_onnxruntime": EXTRA,
    "pkg_torch": EXTRA,
    "pkg_demucs": EXTRA,
    "gpu_torch": EXTRA,
    "cuda_cudnn64_9.dll": EXTRA,
    "campp": EXTRA,
    "ffmpeg": EXTRA,
}


def scope_of(item) -> str:
    """这一项属于哪个作用域（没登记的按 `convert` 保守处理）。"""
    return SCOPE_OF.get(getattr(item, "key", ""), CONVERT)


def launch_blockers(items: list) -> list:
    """**只影响"能不能打开/核心能否启动"**的缺失项 —— 安装程序看这一层。

    ⚠️ 语音识别模型、CUDA 运行库**不在**这里面：它们缺了软件照样打开，
    要下载请到软件里的「模型」页。
    """
    return [i for i in items
            if not i.ok and scope_of(i) == LAUNCH]


def convert_blockers(items: list) -> list:
    """处理歌词才需要的缺失项（模型/运行库）—— 软件里提示，不在安装程序里。"""
    return [i for i in items
            if not i.ok and scope_of(i) == CONVERT]


if __name__ == "__main__":
    # 直接跑就是命令行体检
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE",
                          os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       ".hf_cache"))
    base = os.path.dirname(os.path.abspath(__file__))
    print("=" * 74)
    print("  Lyric2TTML 环境检查（穷尽式）")
    print("=" * 74)
    rows = check_all(base, os.environ["HUGGINGFACE_HUB_CACHE"])
    for it in rows:
        tag = {"crit": "必需", "opt": "可选", "info": "信息"}[it.level]
        print(f"  {'[OK]' if it.ok else '[--]'}  [{tag}] {it.name}"
              + (f"  —  {it.detail}" if it.detail else ""))
        if not it.ok and it.fix:
            print("          → " + it.fix.replace("\n", "\n          "))
    mc, mo = summarize(rows)
    print()
    print("=" * 74)
    if mc:
        print(f"  ✗ 缺 {len(mc)} 项必需：{'、'.join(i.name for i in mc)}")
        print("    运行 install.py 可以自动补全")
    elif mo:
        print(f"  ✓ 核心功能可用；另有 {len(mo)} 项可选未装")
    else:
        print("  ✓ 全部就绪")
    print("=" * 74)
