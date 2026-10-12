"""环境体检服务：GPU / CUDA / 依赖 / 磁盘 —— 全部翻译成"人话 + 怎么办"。

为什么单独一层，而不是直接调 ``doctor.py``：
``doctor.py`` 的输出是**给人看的文本**（安装器/命令行用），
桌面窗口需要的是**结构化条目**（图标、颜色、按钮、能不能自动修）。
所以这里复用 ``envcheck`` 的证据，产出统一的结构：

    {"key", "name", "level": "error|warn|ok|info", "ok": bool,
     "detail": "...", "fix": "...", "fix_action": "download_model|install_pkg|none"}

⚠️ **不要在界面启动时就做重活**：``torch`` 的 import 和 CUDA 探测要好几秒，
必须放到后台线程（``workers`` 里跑）。这个模块只提供"怎么查"，不管"什么时候查"。
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Dict, List, Optional

try:
    from desktop import paths
except ImportError:                                   # 单文件直接跑
    import paths                                      # type: ignore

from desktop.services import model_manager


# --------------------------------------------------------------------- 条目

def _item(key: str, name: str, ok: bool, detail: str = "", fix: str = "",
          level: Optional[str] = None, action: str = "none") -> Dict[str, Any]:
    if level is None:
        level = "ok" if ok else "warn"
    return {"key": key, "name": name, "ok": bool(ok), "level": level,
            "detail": detail, "fix": fix, "fix_action": action}


def check_python() -> Dict[str, Any]:
    v = sys.version_info
    ok = v >= (3, 10)
    return _item("python", "Python 运行时",
                 ok, f"{v.major}.{v.minor}.{v.micro}"
                 + ("（随程序分发，无需自己装）" if paths.frozen() else ""),
                 "" if ok else "Python 版本过低，请用发布版安装包",
                 level="ok" if ok else "error")


def check_gpu() -> Dict[str, Any]:
    """NVIDIA 显卡 + 驱动（**不需要 CUDA Toolkit**）。

    判据来自 ``envcheck``：驱动层 ``nvcuda.dll`` 由显卡驱动提供，
    cuBLAS / cuDNN 是 Python 包（随程序分发）。所以这里只查前者。
    """
    try:
        from desktop.services import gpu_probe
    except ImportError:
        return _item("gpu", "NVIDIA 显卡", False, "无法加载 GPU 探测模块",
                     "重装软件", level="error")
    info = gpu_probe.probe()
    if info.get("ok"):
        name = info.get("gpu_name") or "NVIDIA GPU"
        drv = info.get("driver") or "?"
        return _item("gpu", "NVIDIA 显卡", True,
                     f"{name}｜驱动 {drv}｜CUDA {info.get('cuda_version') or '?'}")
    if info.get("reason") == "no_nvidia_dll":
        return _item("gpu", "NVIDIA 显卡", False,
                     "没有找到 NVIDIA 驱动（nvcuda.dll）",
                     "本软件必须用 NVIDIA 显卡做识别。请装好显卡驱动后重试。",
                     level="error")
    return _item("gpu", "NVIDIA 显卡", False,
                 info.get("error") or "无法初始化 CUDA", "看看显卡驱动是否正常",
                 level="error")


def check_cuda_libs() -> Dict[str, Any]:
    """cuBLAS / cuDNN 能不能真加载（用 ``ctypes.WinDLL`` 实测，不猜）。"""
    try:
        from desktop.services import gpu_probe
        r = gpu_probe.cuda_libs()
    except Exception as e:                             # noqa: BLE001
        return _item("cuda", "CUDA 运行库", False, f"{type(e).__name__}: {e}",
                     "重装软件", level="error")
    if r.get("ok"):
        return _item("cuda", "CUDA 运行库", True, r.get("detail") or "cuBLAS / cuDNN 可加载")
    return _item("cuda", "CUDA 运行库", False, r.get("detail") or "加载失败",
                 r.get("fix") or "重装软件（安装包内已含 CUDA 运行库）",
                 level="error")


def check_ffmpeg() -> Dict[str, Any]:
    exe = shutil.which("ffmpeg")
    if exe:
        return _item("ffmpeg", "ffmpeg", True, exe)
    try:
        import av                                       # noqa: F401
        return _item("ffmpeg", "ffmpeg", True,
                     "系统里没有 ffmpeg，但已内置 PyAV 解码（mp3/wav/m4a/flac 都能读）")
    except Exception as e:                              # noqa: BLE001
        return _item("ffmpeg", "ffmpeg", False, f"没有 ffmpeg，PyAV 也不可用：{e}",
                     "装个 ffmpeg 或重装软件", level="error")


def check_disk(need_bytes: int = 4 * 1024 ** 3) -> Dict[str, Any]:
    """磁盘余量：装 + 跑一次转换大概要 4GB（模型 3GB + 中间文件）。"""
    target = paths.data_dir()
    try:
        paths.ensure_dir(target)
        usage = shutil.disk_usage(target)
    except OSError:
        target = os.path.expanduser("~")
        usage = shutil.disk_usage(target)
    free = usage.free
    ok = free >= need_bytes
    return _item("disk", "磁盘空间", ok,
                 f"可用 {model_manager.human(free)}（{target}）",
                 "" if ok else f"建议至少留 {model_manager.human(need_bytes)}，"
                               f"模型和中间文件都要空间",
                 level="ok" if ok else "warn")


def check_writable() -> Dict[str, Any]:
    """数据目录可写吗 —— 不可写会让"装好了但一张图都存不下"。"""
    d = paths.data_dir()
    try:
        paths.ensure_dir(d)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return _item("writable", "数据目录可写", True, d)
    except OSError as e:
        return _item("writable", "数据目录可写", False, f"{d}：{e}",
                     "换个有权限的位置，或用便携模式（程序目录放 portable.txt）",
                     level="error")


def check_models(mm: Optional[model_manager.ModelManager] = None) -> List[Dict[str, Any]]:
    """每个模型一条（走 ModelManager 的同一套判据）。"""
    mm = mm or model_manager.ModelManager()
    out = []
    for s in mm.check_all():
        ok = s.status == model_manager.READY
        level = "ok" if ok else ("warn" if s.policy in ("on_demand", "bundled")
                                 else "error")
        detail = f"{s.detail}"
        if not ok:
            detail += (f"｜需要约 {model_manager.human(s.approx_size)}"
                       if s.approx_size else "")
            if s.note:
                detail += f"｜{s.note}"
        out.append(_item(f"model:{s.id}", s.name, ok, detail,
                         "" if ok else "在「模型」页点下载",
                         level=level,
                         action="none" if ok else "download_model"))
    return out


def check_workspace() -> Dict[str, Any]:
    """工作目录（uploads / outputs 放这里）。"""
    import desktop.config as config
    ws = config.current().workspace()
    try:
        paths.ensure_dir(ws)
        paths.ensure_dir(os.path.join(ws, "outputs"))
        return _item("workspace", "工作目录", True, ws)
    except OSError as e:
        return _item("workspace", "工作目录", False, f"{ws}：{e}",
                     "在「设置」里换一个可写目录", level="error")


# --------------------------------------------------------------- 汇总

def quick_check() -> List[Dict[str, Any]]:
    """**轻量**检查：不 import torch、不碰 CUDA。界面打开时先跑这个。"""
    items = [check_python(), check_ffmpeg(), check_disk(),
             check_writable(), check_workspace(), check_models()]
    flat: List[Dict[str, Any]] = []
    for it in items:
        flat.extend(it) if isinstance(it, list) else flat.append(it)
    return flat


def full_check(mm: Optional[model_manager.ModelManager] = None) -> List[Dict[str, Any]]:
    """**完整**检查：加上 GPU / CUDA 实测。要在后台线程里跑（几秒）。"""
    items = list(quick_check())
    items.insert(1, check_gpu())
    items.insert(2, check_cuda_libs())
    return items


def summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    errors = [i for i in items if i["level"] == "error"]
    warns = [i for i in items if i["level"] == "warn"]
    return {
        "total": len(items),
        "ok": sum(1 for i in items if i["ok"]),
        "errors": len(errors),
        "warnings": len(warns),
        "blocking": [i["name"] for i in errors if i["key"] not in ("gpu", "cuda")],
        "ready": not errors,
        "headline": ("一切就绪" if not errors and not warns else
                     f"{len(errors)} 项要处理、{len(warns)} 项建议看看" if errors else
                     f"{len(warns)} 项建议看看"),
        "items": items,
    }
