"""环境体检：跑之前先看看缺什么、怎么补。

    python doctor.py
    Lyric2TTML.exe --doctor          （打包版）

设计原则：**每一条不合格都给出"照着做就能好"的下一步**。
只说"缺少 XXX"等于没说 —— 用户既不知道装什么、也不知道装到哪。

退出码：0 = 关键项齐全；1 = 有缺失（缺什么见输出）
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def _base() -> str:
    """数据目录：冻结时 = exe 所在目录（与 launcher 的 _rehome 一致）"""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return HERE


def _resource_roots() -> list:
    """只读资源（``models/``）可能在的目录。

    ⚠️ 冻结时 ``_base()``（exe 旁边）和 ``sys._MEIPASS``（``_internal``）
    **不是同一个地方**：安装包自带的 CAM++ 权重是 PyInstaller 的 data，
    落在 ``_internal/models/`` 里。只查 exe 旁边的话，打包版会报
    "声纹分声部模型：未找到"——**而它明明就在包里**（真踩过）。
    桌面版 ``desktop/paths.resource_roots()`` 用的是同一套顺序，
    两边必须一致，否则"桌面窗口说就绪、doctor 说缺失"。
    """
    out = [_base()]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass and meipass not in out:
        out.append(meipass)
    return out


def _find_resource(*rel: str) -> str:
    """在资源根里找第一个存在的路径；都不存在时返回第一个候选（给报错用）。"""
    first = os.path.join(_base(), *rel)
    for root in _resource_roots():
        p = os.path.join(root, *rel)
        if os.path.exists(p):
            return p
    return first


BASE = _base()

CRIT = "crit"      # 缺了就跑不起来
OPT = "opt"        # 可选功能，缺了只是少个能力
INFO = "info"

ROWS: list = []


def add(level: str, name: str, ok: bool, detail: str = "", fix: str = "") -> None:
    ROWS.append((level, name, ok, detail, fix))


# --------------------------------------------------------------------- 1. Python
def check_python() -> None:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    add(CRIT, "Python ≥ 3.10", ok, f"{v.major}.{v.minor}.{v.micro}",
        "从 python.org 装 3.10+，安装时勾选 “Add Python to PATH”")
    if _frozen():
        # 打包版自带解释器，不依赖系统 Python
        ROWS[-1] = (INFO, "Python（打包版内置）", True, f"{v.major}.{v.minor}.{v.micro}", "")


# ------------------------------------------------------------------ 2. 依赖包
PKGS = [
    ("faster_whisper", "faster-whisper", CRIT, "核心：语音识别"),
    ("ctranslate2", "ctranslate2", CRIT, "faster-whisper 的推理后端"),
    ("numpy", "numpy", CRIT, "数值计算"),
    ("huggingface_hub", "huggingface-hub", CRIT, "模型下载/缓存"),
    ("av", "av", CRIT, "音频解码"),
    ("tokenizers", "tokenizers", CRIT, "分词"),
    ("onnxruntime", "onnxruntime", OPT, "部分模型需要"),
    ("pypinyin", "pypinyin", OPT, "中文同音字容错（建议装）"),
    ("torch", "torch", OPT, "声纹分声部 / 人声分离"),
    ("demucs", "demucs", OPT, "人声分离 --separate-vocals"),
]


def check_packages() -> None:
    missing_crit, missing_opt = [], []
    for mod, pipname, level, why in PKGS:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "")
            add(level, f"{pipname}（{why}）", True, ver)
        except Exception as e:
            add(level, f"{pipname}（{why}）", False, type(e).__name__)
            (missing_crit if level == CRIT else missing_opt).append(pipname)
    globals()["_MISSING"] = (missing_crit, missing_opt)


# ---------------------------------------------------------------- 3. 外部程序
def check_ffmpeg() -> None:
    exe = shutil.which("ffmpeg")
    add(OPT, "ffmpeg（部分格式的解码兜底）", bool(exe),
        exe or "未找到",
        "多数格式靠内置的 PyAV 就能解；遇到解不开的再装：\n"
        "        Windows: winget install Gyan.FFmpeg   （或下载后把 bin 加进 PATH）\n"
        "        macOS:   brew install ffmpeg\n"
        "        Linux:   sudo apt install ffmpeg")


# ---------------------------------------------------------------- 4. Whisper 模型
def check_whisper_models() -> None:
    try:
        from asr import _REPO_MAP, DEFAULT_CACHE_DIR
    except Exception as e:
        add(CRIT, "Whisper 模型", False, f"无法读取 asr 配置：{e}")
        return
    cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or DEFAULT_CACHE_DIR
    names = list(_REPO_MAP.keys())
    found = {}
    for name in names:
        repo = _REPO_MAP[name]
        d = os.path.join(cache, "models--" + repo.replace("/", "--"))
        size = 0
        snaps = os.path.join(d, "snapshots")
        if os.path.isdir(snaps):
            for s in os.listdir(snaps):
                for r, _, fs in os.walk(os.path.join(snaps, s)):
                    for f in fs:
                        try:
                            size += os.path.getsize(os.path.join(r, f))
                        except OSError:
                            pass
        if size > 50 * 1024 * 1024:           # 有像样的权重量才算"有"
            found[name] = size
    globals()["_MODELS"] = found

    detail = ("已缓存：" + "、".join(f"{k}({v / 1024 / 1024:.0f}MB)"
                                    for k, v in found.items())) if found else "一个都没有"
    add(CRIT, "Whisper 语音识别模型", bool(found), detail,
        "首次使用需要联网下载一次（large-v3 约 3 GB）。三选一：\n"
        "        ① 联网下载：程序会自动下；国内建议先设镜像\n"
        "             set HF_ENDPOINT=https://hf-mirror.com\n"
        "        ② 复用已有：把别处 .hf_cache\\models--Systran--faster-whisper-large-v3\n"
        f"           整个目录复制到 {cache}\\\n"
        "        ③ 先用小模型：--model small（约 0.5GB）或 --model medium（约 1.5GB）")


# ------------------------------------------------------- 5. 可选：声纹 / 分离权重
def check_optional_weights() -> None:
    # ⚠️ 用 _find_resource：打包版的 CAM++ 权重在 `_internal/models/` 里，
    # 只查 exe 旁边会误报"未找到"（踩过）
    ck = _find_resource("models", "campplus")
    has = os.path.isdir(ck) and any(
        f.endswith((".onnx", ".pt", ".bin", ".pth"))
        for r, _, fs in os.walk(ck) for f in fs)
    add(OPT, "CAM++ 声纹权重（对唱分声部）", has,
        ck if has else "未找到",
        "不随仓库分发。拿到后放到 models/campplus/ 即可；\n"
        "        没有也不影响其它功能 —— 「演唱者分段」选「关闭」就行")

    demucs = _find_resource("models", "demucs")
    has_d = os.path.isdir(demucs) and any(
        f.endswith((".th", ".pt", ".bin"))
        for r, _, fs in os.walk(demucs) for f in fs)
    add(OPT, "Demucs 权重（--separate-vocals）", has_d,
        demucs if has_d else "首次使用时自动下载（约 80MB）", "")


# ------------------------------------------------------------------- 6. GPU
def check_gpu() -> None:
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        add(INFO, "GPU（CTranslate2 可见的 CUDA 设备）", n > 0,
            f"{n} 个" if n else "0 个 —— 会用 CPU 跑（慢些，但能跑）",
            "想用显卡：装 NVIDIA 驱动 + cublas/cudnn。\n"
            "        pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
    except Exception as e:
        add(INFO, "GPU", False, f"无法探测：{type(e).__name__}", "")

    try:
        import torch
        ok = torch.cuda.is_available()
        add(INFO, "GPU（torch 可见）", ok,
            torch.cuda.get_device_name(0) if ok else "不可用（声纹/分离会用 CPU）", "")
    except Exception:
        add(INFO, "GPU（torch 可见）", False, "torch 未安装（可选）", "")


# ------------------------------------------------------------------- 输出
ICON = {True: "[OK]  ", False: "[--]  "}


def main() -> int:
    # --json：给**安装程序**读的机器可读输出。
    # ⚠️ 为什么必须有这条路：安装程序是独立 exe，它自己的 Python 环境是
    # PyInstaller 打的临时环境 —— 在那里 import numpy/ctranslate2 必然失败，
    # 于是把"没装"报給用户（**假报错**，真踩过）。
    # 正确的做法是让**主程序报自己的环境**，安装程序只负责读结果 + 补全。
    if "--json" in sys.argv:
        import json
        import os as _os

        sys.path.insert(0, HERE)
        _os.environ.setdefault(
            "HUGGINGFACE_HUB_CACHE", _os.path.join(BASE, ".hf_cache"))
        try:
            import envcheck
            items = envcheck.check_all(
                BASE, _os.environ["HUGGINGFACE_HUB_CACHE"], frozen=_frozen())
            out = [{"key": i.key, "name": i.name, "why": i.why, "level": i.level,
                    "ok": i.ok, "detail": i.detail, "fix": i.fix} for i in items]
            print(json.dumps({"base": BASE, "frozen": _frozen(),
                              "items": out}, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False))
        return 0

    os.chdir(BASE)
    print("=" * 74)
    print("  Lyric2TTML 环境体检")
    print("=" * 74)
    print(f"  目录：{BASE}")
    print(f"  模式：{'打包版（自带 Python）' if _frozen() else '源码运行'}")
    print()

    check_python()
    check_packages()
    check_ffmpeg()
    check_whisper_models()
    check_optional_weights()
    check_gpu()

    miss_crit = miss_opt = 0
    for level, name, ok, detail, fix in ROWS:
        tag = {"crit": "必需", "opt": "可选", "info": "信息"}[level]
        print(f"  {ICON[ok]}[{tag}] {name}")
        if detail:
            print(f"          {detail}")
        if not ok and fix:
            print("          → " + fix.replace("\n", "\n          "))
            if level == CRIT:
                miss_crit += 1
            elif level == OPT:
                miss_opt += 1
        elif not ok:
            if level == CRIT:
                miss_crit += 1
    print()
    print("=" * 74)
    if miss_crit:
        print(f"  ✗ 有 {miss_crit} 项**必需**的没配好 —— 按上面的 → 提示处理完再重跑本脚本")
    elif miss_opt:
        print(f"  ✓ 能跑了。另有 {miss_opt} 项可选功能没配（不影响核心转换）")
    else:
        print("  ✓ 全部就绪，可以直接用")
    print("=" * 74)
    return 1 if miss_crit else 0


if __name__ == "__main__":
    sys.exit(main())
