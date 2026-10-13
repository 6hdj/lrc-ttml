"""GPU / CUDA 探针：**实测能不能用**，而不是猜。

背景（踩过的坑，别再走一遍）：

* ``torch.cuda.is_available()`` 返回 False 有**三种完全不同的原因**，
  给的提示也完全不同：没显卡驱动 / 驱动太旧 / cuBLAS·cuDNN 没加载。
  一律报"GPU 初始化失败"会让用户去重装驱动，而真正的问题是少一个 DLL。
* ``<程序目录>/cuda`` 下我们自带 cuBLAS / cuDNN。**必须在 import torch 之前**
  把目录加进 DLL 搜索路径，否则 torch 一加载就 ``Library cublas64_12.dll
  is not found or cannot be loaded``。
* ``nvcuda.dll`` 是**显卡驱动**提供的（``System32``），**不是** CUDA Toolkit。
  所以"要不要装 CUDA Toolkit"的答案是**不用** —— 这句话必须写进界面，
  否则用户会去下 3GB 的 Toolkit。

本模块**不导入 Qt**，但会 import torch（慢），只应在后台线程里调用。
"""
from __future__ import annotations

import ctypes
import os
import sys
from typing import Any, Dict, List, Optional

try:
    from desktop import paths
except ImportError:                                   # 单文件直接跑
    import paths                                      # type: ignore


def cuda_dirs() -> List[str]:
    """所有可能放着 CUDA DLL 的目录（顺序 = 优先级）。

    ⚠️⚠️ **必须同时覆盖"源码模式"和"打包模式"两种布局**（真踩过）：
    本函数原来只找 `<app>/cuda`、`<app>/.venvpack/.../nvidia/*/bin`、
    `CUDA_PATH`、`Program Files` —— **一个都不覆盖 PyInstaller 的产物**。
    打包版把 CUDA DLL 放在 `<app>/_internal/nvidia/<lib>/bin`，
    于是注册结果是"一个目录都没注册"，
    而 `asr._register_cuda_dlls()` 又靠 `sys.path` 里的 `_internal` 兜住了 ——
    **两个函数口径不一致**，表现为"体检说没问题、一跑就报
    `Library cublas64_12.dll is not found or cannot be loaded`"。

    现在两边都走这里，并且显式包含 `_internal`。
    """
    out: List[str] = []
    app = paths.app_dir()

    def add(p: str) -> None:
        if p and os.path.isdir(p) and p not in out:
            out.append(p)

    def add_nvidia(base: str) -> None:
        """把 `<base>/nvidia/<lib>/bin` 全加进来（PYPI wheel 的布局）。"""
        nv = os.path.join(base, "nvidia")
        if os.path.isdir(nv):
            for sub in sorted(os.listdir(nv)):
                add(os.path.join(nv, sub, "bin"))

    add(os.path.join(app, "cuda"))                     # 手工放置（首选）
    add(os.path.join(app, "cuda", "bin"))
    # ① PyInstaller 打包版：资源在 `_internal`（= sys._MEIPASS）
    add_nvidia(paths.source_dir())
    # ② 源码 / 便携版：venv 里的 nvidia-*-cu12
    for base in (os.path.join(app, ".venvpack", "Lib", "site-packages"),
                 os.path.join(app, ".venv", "Lib", "site-packages")):
        add_nvidia(base)
    # ③ 任何在 sys.path 上的 nvidia 包（冻结时 `_internal` 也在 sys.path 上）
    for base in list(sys.path):
        if base and os.path.isdir(base):
            add_nvidia(base)
    # ④ 系统装的 CUDA Toolkit（可选）
    add(os.environ.get("CUDA_PATH", ""))
    for v in ("v12.8", "v12.6", "v12.4", "v12.1", "v12.0"):
        add(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "NVIDIA GPU Computing Toolkit", "CUDA", v, "bin"))
    return out


def register_dll_dirs() -> List[str]:
    """把 CUDA 目录注册进 DLL 搜索路径。

    ⚠️ **必须在 import torch 之前调用**（torch 一加载就去解析 cublas）。
    返回实际注册进去的目录。
    """
    dirs = cuda_dirs()
    if os.name != "nt":
        return dirs
    done: List[str] = []
    for d in dirs:
        ok = False
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)
                ok = True
        except OSError:
            ok = False
        old = os.environ.get("PATH", "")
        if d not in old.split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + old
            ok = True
        if ok:
            done.append(d)
    return done


def _find_dll(names: List[str]) -> Optional[str]:
    for d in cuda_dirs():
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    for n in names:
        try:
            ctypes.WinDLL(n)                            # type: ignore[attr-defined]
            return n
        except OSError:
            continue
    return None


def cuda_libs() -> Dict[str, Any]:
    """cuBLAS / cuDNN 能不能**真的加载**（``ctypes.WinDLL``，不是看文件在不在）。"""
    if os.name != "nt":
        return {"ok": True, "detail": "非 Windows，跳过（由 torch 自己处理）"}
    missing = []
    details = []
    for label, names in (("cuBLAS", ["cublas64_12.dll", "cublas64_11.dll"]),
                         ("cuDNN", ["cudnn64_9.dll", "cudnn64_8.dll"])):
        p = _find_dll(names)
        if p:
            details.append(f"{label} ✓")
        else:
            missing.append(label)
            details.append(f"{label} ✗")
    if missing:
        return {"ok": False, "detail": "、".join(details) + "（缺 " + "/".join(missing) + "）",
                "fix": "安装包内已含 CUDA 运行库。若你用的是源码版，请重新跑安装程序补全。"}
    return {"ok": True, "detail": "、".join(details) + f"（{len(cuda_dirs())} 个目录已注册）"}


def probe() -> Dict[str, Any]:
    """完整探测：驱动 → 库 → torch 能不能真看到显卡。**慢（几秒）**。"""
    info: Dict[str, Any] = {"ok": False, "reason": "", "error": "",
                            "gpu_name": "", "driver": "", "cuda_version": ""}

    if os.name == "nt":
        try:
            ctypes.WinDLL("nvcuda.dll")                 # type: ignore[attr-defined]
        except OSError as e:
            info["reason"] = "no_nvidia_dll"
            info["error"] = f"nvcuda.dll 加载失败：{e}"
            return info

    info["libs"] = cuda_libs()
    register_dll_dirs()

    try:
        import torch
    except Exception as e:                              # noqa: BLE001
        info["reason"] = "no_torch"
        info["error"] = f"torch 无法导入：{type(e).__name__}: {e}"
        return info

    info["torch"] = getattr(torch, "__version__", "")
    info["cuda_version"] = getattr(getattr(torch, "version", None), "cuda", "") or ""
    try:
        available = bool(torch.cuda.is_available())
    except Exception as e:                              # noqa: BLE001
        info["reason"] = "cuda_error"
        info["error"] = f"torch.cuda.is_available() 抛异常：{type(e).__name__}: {e}"
        return info

    if not available:
        # 到这里说明驱动在、库也尽量注册了，但 torch 还是看不到卡
        info["reason"] = "cuda_unavailable"
        info["error"] = ("torch 看不到可用的 CUDA 设备"
                         + ("" if info["libs"].get("ok") else "（CUDA 运行库也没加载成功）"))
        return info

    info["ok"] = True
    try:
        info["gpu_name"] = torch.cuda.get_device_name(0)
        info["device_count"] = torch.cuda.device_count()
        cap = torch.cuda.get_device_capability(0)
        info["compute_capability"] = f"{cap[0]}.{cap[1]}"
    except Exception as e:                              # noqa: BLE001
        info["error"] = f"取显卡信息失败：{e}"
    if not info["driver"]:
        info["driver"] = _driver_version()
    return info


def _driver_version() -> str:
    """从注册表读 NVIDIA 驱动版本（读不到就返回空串，不报错）。"""
    if os.name != "nt":
        return ""
    try:
        import winreg                                    # type: ignore
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\NVIDIA Corporation\Global\NVSMI") as k:
            v, _ = winreg.QueryValueEx(k, "DriverVersion")
            return str(v)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=driver_version",
                              "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=8)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0].strip()
    except Exception:                                    # noqa: BLE001
        pass
    return ""


def human_report(info: Dict[str, Any]) -> str:
    """把探测结果翻成一句人话（界面顶部的状态条用）。"""
    if info.get("ok"):
        extra = f"｜CUDA {info['cuda_version']}" if info.get("cuda_version") else ""
        return f"GPU 就绪：{info.get('gpu_name') or 'NVIDIA GPU'}{extra}"
    reason = info.get("reason")
    if reason == "no_nvidia_dll":
        return "没找到 NVIDIA 显卡驱动 —— 本软件必须用 NVIDIA 显卡识别"
    if reason == "cuda_unavailable":
        return "显卡在，但 CUDA 用不了（多半是驱动太旧或运行库没加载）"
    if reason == "no_torch":
        return "运行库缺失，请重装软件"
    return info.get("error") or "GPU 不可用"


if __name__ == "__main__":
    print(human_report(probe()))
