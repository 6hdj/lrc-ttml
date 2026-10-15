"""桌面应用的数据目录 / 路径解析。

设计目标（写代码前先定死，否则后面每个模块都会各自拼一遍路径）：

* **程序目录 ≠ 数据目录**。安装目录可能在 ``Program Files``（只读），
  所以配置、日志、输出、模型缓存都不能往那里写。
* **模型缓存必须能"就地继承"旧版**。旧版（Web 界面 + 安装器）把
  ``.hf_cache`` 放在**程序目录**下，用户已经下了 3GB 的 ``large-v3``；
  桌面版如果改用别处，用户升级后要重下 3GB。所以这里**优先复用
  程序目录下已有的 .hf_cache**。
* **便携模式**：程序目录里放了 ``portable.txt`` 就全部就地存放
  （U 盘 / 免安装用户需要）。

``desktop.paths`` **不导入 Qt**，可以在无图形环境里直接跑测试。
"""
from __future__ import annotations

import os
import sys
from typing import Optional

PORTABLE_MARKER = "portable.txt"
APP_NAME = "Lyric2TTML"


# --------------------------------------------------------------------- 基础

def frozen() -> bool:
    """是否运行在 PyInstaller 冻结包里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """程序所在目录。

    冻结时 = ``exe`` 所在目录（不是 ``_internal``！PyInstaller 把依赖放进
    ``_internal``，那里是"包内部"，升级/重装会整个替换掉）。
    未冻结时 = 仓库根目录（``desktop/`` 的上一级）。

    可用 ``L2T_APP_DIR`` 覆盖（测试 / 特殊部署用）。
    """
    explicit = os.environ.get("L2T_APP_DIR")
    if explicit:
        return explicit
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_dir() -> str:
    """**只读资源**目录（以前叫 `_source_dir`，现在公开）。

    冻结时 = ``sys._MEIPASS``（= PyInstaller 的 ``_internal``）—— 那里放着
    ``index.html``、``models/``、``desktop/`` 这些**随包分发、不需要写**的东西。
    未冻结时 = 仓库根目录。

    ⚠️ 和 ``app_dir()`` 的区别很重要，别混用：
    * ``app_dir()``    = 用户能看到、能改的目录（exe 旁边）；
      转换产物、用户自己放的模型、``portable.txt`` 都在这里。
    * ``source_dir()`` = 包内部，**只读**，升级会整体替换。
      PyInstaller 把 datas 都放这儿。

    ⚠️ 为什么要公开（真踩过）：它本来是私有的 `_source_dir()`，于是
    `gpu_probe` 需要"包内部"路径时只能自己再推一遍，推出个**不一致的版本**
    —— 打包版因此找不到 `_internal/nvidia/<lib>/bin` 里的 CUDA DLL。
    需要包内部资源的地方**统一调这个函数**，不要再各推一遍。
    """
    if frozen():
        return getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 兼容旧名字（原来叫 `_source_dir`）。新代码请用 `source_dir()`。
_source_dir = source_dir


def _local_appdata() -> str:
    for var in ("LOCALAPPDATA", "APPDATA"):
        v = os.environ.get(var)
        if v and os.path.isdir(v):
            return v
    return os.path.expanduser("~")


def portable() -> bool:
    """程序目录里有没有 ``portable.txt``。"""
    return os.path.isfile(os.path.join(app_dir(), PORTABLE_MARKER))


def data_dir() -> str:
    """可变数据（配置 / 日志 / 输出 / 模型）的根目录。

    * ``L2T_DATA_DIR`` 显式指定 → 用它（测试、免安装部署）
    * 便携模式 → ``<程序目录>``
    * 否则 → ``%LOCALAPPDATA%\\Lyric2TTML``
    """
    explicit = os.environ.get("L2T_DATA_DIR")
    if explicit:
        return explicit
    if portable():
        return app_dir()
    return os.path.join(_local_appdata(), APP_NAME)


def ensure_dir(path: str) -> str:
    """建目录并返回它。已存在就什么都不做。

    ⚠️ 盘符根目录（``E:\\``）上 ``os.makedirs`` 可能抛 ``WinError 5 拒绝访问``
    —— 那个目录本来就已经存在，直接当成功，别让整个流程崩。
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        if not os.path.isdir(path):
            raise
    return path


# ----------------------------------------------------------------- 各子目录

def config_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "config"))


def config_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def logs_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "logs"))


def log_path() -> str:
    return os.path.join(logs_dir(), "desktop.log")


def projects_dir() -> str:
    """用户自己的工程/输出目录（默认输出位置）。"""
    return ensure_dir(os.path.join(data_dir(), "projects"))


def temp_dir() -> str:
    return ensure_dir(os.path.join(data_dir(), "temp"))


def state_dir() -> str:
    """任务 / 模型状态的持久化目录。"""
    return ensure_dir(os.path.join(data_dir(), "state"))


def state_path(name: str) -> str:
    return os.path.join(state_dir(), name)


def models_dir() -> str:
    """模型总目录（清单、缓存都在这里）。"""
    return ensure_dir(os.path.join(data_dir(), "models"))


# ------------------------------------------------------------- 模型缓存位置

def _has_model_content(path: str, min_bytes: int = 200 * 1024 * 1024) -> bool:
    """这个缓存目录里是不是**真有模型**（避免拿一个空壳目录当"已有缓存"）。"""
    if not os.path.isdir(path):
        return False
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            if total >= min_bytes:
                return True
    return False


def legacy_hf_cache() -> str:
    """旧版把 HF 缓存放在程序目录下。"""
    return os.path.join(app_dir(), ".hf_cache")


def hf_cache_dir() -> str:
    """HuggingFace 模型缓存目录。

    优先级：
    1. 环境变量 ``L2T_HF_CACHE``（测试 / 高级用户显式指定）
    2. 程序目录下**已有内容的** ``.hf_cache``（旧版升级上来，3GB 不用重下）
    3. ``<数据目录>/models/hf_cache``
    """
    explicit = os.environ.get("L2T_HF_CACHE")
    if explicit:
        return ensure_dir(explicit)
    legacy = legacy_hf_cache()
    if _has_model_content(legacy):
        return legacy
    return ensure_dir(os.path.join(models_dir(), "hf_cache"))


def hf_cache_roots() -> list:
    """所有**可能**有模型的缓存目录（探测/复用/迁移时按顺序找）。"""
    out = []
    for p in (os.environ.get("L2T_HF_CACHE"), legacy_hf_cache(),
              os.path.join(models_dir(), "hf_cache")):
        if p and p not in out:
            out.append(p)
    return out


def model_dir(name: str) -> str:
    return ensure_dir(os.path.join(models_dir(), name))


def runtime_root() -> str:
    """**按需下载的运行时组件**装在哪儿（PyTorch 这类大依赖）。

    ⚠️ 必须在**用户可写**目录里，而且**不能**放在程序目录：
    * 程序目录可能是 `Program Files`（不可写）；
    * 升级/重装会整个替换程序目录 —— 用户下的 400 MB 就白下了；
    * PyInstaller 的 `_internal` 更不行（那是临时解包区）。
    """
    explicit = os.environ.get("L2T_RUNTIME_DIR")
    if explicit:
        return ensure_dir(explicit)
    return ensure_dir(os.path.join(data_dir(), "runtime"))


def apply_env() -> str:
    """把模型缓存目录写进环境变量，**必须在 import asr / huggingface_hub 之前调用**。

    返回最终使用的缓存目录。

    ⚠️ 这里**顺便**把已装好的运行时组件目录（PyTorch 等）插进 `sys.path`。
    原因是启动顺序：`apply_env()` 在 `desktop/app.py` 里排在所有
    `import torch` 之前（`speaker_campplus` 是延迟 import 的），
    所以只要在这儿插一次，后面 `import torch` 就能命中运行时组件。
    **放到别处都会晚**（torch 一旦被 import 过就再也换不掉了）。
    """
    hub = hf_cache_dir()
    # 用 setdefault 之外的写法：桌面版要能覆盖安装器/launcher 设的旧值
    os.environ["HUGGINGFACE_HUB_CACHE"] = hub
    apply_runtime_path()
    return hub


def apply_vendor_path() -> list:
    """把**随包分发的纯 Python 依赖**目录加到 `sys.path`，返回加进去的目录。

    2026-10-15 加：`demucs`（人声分离）是纯 Python，和 `einops` / `julius`
    一起放在 `<程序目录>/vendor/`（冻结后 = `_internal/vendor/`，靠
    `resource_roots()` 找）。**不能**让 PyInstaller 把它当模块分析 ——
    那样会顺着 `import torch` 把 2.5GB 的 torch 拖进包（这正是当初
    `--exclude-module demucs` 的原因）。

    torch 由"按需运行时"提供（`apply_runtime_path()`），两者都必须在
    **任何 import 之前**接好。
    """
    import sys
    added = []
    for root in resource_roots():
        d = os.path.join(root, "vendor")
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
            added.append(d)
    return added


def apply_runtime_path() -> list:
    """把已就绪的运行时组件目录加到 `sys.path`，返回加进去的目录。

    允许失败：运行时组件没装是**正常状态**（基础功能不需要它），
    绝不能因此让启动失败。

    ⚠️⚠️ **还要把 `<runtime>/<id>/lib` 注册进 DLL 搜索路径**（踩出来的）：
    在**冻结版**里 `import torch` 曾以 `0xC0000005`（访问冲突）**崩掉整个进程**，
    而源码模式用同一份 torch 没事。原因是原生 DLL 的解析顺序 ——
    冻结进程已经把 PyInstaller 自带的一批运行库（`libiomp5md.dll`、
    MSVC 运行库等）放进了 `_internal`，torch 的 `lib/` 下那几个 DLL 又要求
    **自己那一套**，两边混用时崩在原生层。
    在 `import torch` **之前**把 `<runtime>/torch/lib` 插到搜索路径最前面，
    让它优先拿到自己那份。
    """
    import sys
    added = []
    try:
        root = runtime_root()
        if not os.path.isdir(root):
            return added
        for name in sorted(os.listdir(root)):
            if name.startswith("."):
                continue
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            # ⚠️⚠️ **崩过的运行时目录绝不加进 sys.path**。
            # 冻结版里 `import torch` 实测会让进程 `0xC0000005` 崩掉；
            # 一旦这样的目录进了 `sys.path`，**任何**一次 `import torch`
            # （包括 `speaker_campplus.available()` 里那句探测）都会崩，
            # 用户看到的是"一开软件就闪退"。
            # `runtime_service` 会在尝试 import 之前留一个
            # `.l2t-import-crashed` 记号；这里据此跳过它，
            # 让程序**优雅降级**（单声部照常可用）。
            if os.path.isfile(os.path.join(d, ".l2t-import-crashed")):
                try:
                    from desktop.services import runtime_service as _rs
                    if _rs._crashed_before(d):
                        continue
                except Exception:                       # noqa: BLE001
                    continue
            # ⚠️⚠️ **默认就是 site 目录本身**，别再"往里钻一层"
            # （2026-10-15 用户实测的真 bug）：
            #
            #   实际情况：site = `<数据目录>/runtime/torch`
            #             包    = `<site>/torch/__init__.py`   ← 包在 site 下面一层
            #   ⇒ `sys.path` 要加的是 **site**，这样 `import torch` 才命中
            #     `<site>/torch/__init__.py`。
            #
            # 老判据是"`<site>/torch` 里有 `__init__.py` 就加里层" ——
            # 可那**正是包自己的目录**，于是 `sys.path` 被指到 `<site>/torch`，
            # `import torch` 去找 `<site>/torch/torch/__init__.py` ⇒
            # `ModuleNotFoundError`（用户看到的是
            # "人声分离用不了：缺少 torch._C"），
            # 而 `--runtime-cli --status` 的加载自检走的是另一条路，
            # 照样报"已就绪" —— **状态显示 ready、实际用不了**。
            #
            # 只有**双重嵌套**（`<site>/<name>/<name>/__init__.py`，
            # 即包里又套了一层）才需要加里层。
            import_dir = d
            inner = os.path.join(d, name)
            if (not os.path.isfile(os.path.join(inner, "__init__.py"))
                    and os.path.isfile(os.path.join(inner, name, "__init__.py"))):
                import_dir = inner
            if import_dir not in sys.path:
                sys.path.insert(0, import_dir)
            added.append(import_dir)
            # 原生库目录：`lib`（torch 用这个）/ `Lib` / `bin` / 顶层
            for sub in ("lib", "Lib", "bin", ""):
                libdir = os.path.join(import_dir, sub) if sub else d
                if not os.path.isdir(libdir):
                    continue
                try:
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(libdir)
                except OSError:
                    pass
                old = os.environ.get("PATH", "")
                if libdir not in old.split(os.pathsep):
                    os.environ["PATH"] = libdir + os.pathsep + old
    except Exception:                                   # noqa: BLE001
        pass
    return added


def resource_roots() -> list:
    """只读资源（``models/`` / ``index.html`` / ``desktop/``）可能在的目录。

    顺序 = 优先级：**程序目录优先**（用户替换/加自己的模型最直观），
    其次是包内部（PyInstaller 的 ``_internal``）。
    ⚠️ 冻结时这两处**不是同一个地方** —— 只找程序目录的话，
    安装包自带的 CAM++ 权重（在 ``_internal/models/``）会"找不到"，
    用户看到的是"声纹模型缺失"（真踩过）。

    ``L2T_APP_DIR`` 显式指定时**只返回它**（测试用，避免翻到真实程序目录）。
    """
    if os.environ.get("L2T_APP_DIR"):
        return [app_dir()]
    out = [app_dir()]
    src = _source_dir()
    if src not in out:
        out.append(src)
    return out


def describe() -> dict:
    """给"设置"页和诊断用的一份路径总览。"""
    return {
        "app_dir": app_dir(),
        "data_dir": data_dir(),
        "source_dir": _source_dir(),
        "portable": portable(),
        "frozen": frozen(),
        "config": config_path(),
        "logs": logs_dir(),
        "projects": projects_dir(),
        "hf_cache": hf_cache_dir(),
        "legacy_hf_cache": legacy_hf_cache(),
    }


def in_repo() -> bool:
    """是不是在源码仓库里跑（决定要不要把输出放回仓库的 outputs/）。"""
    return os.path.isfile(os.path.join(app_dir(), "pipeline.py"))


def default_workspace() -> str:
    """默认的工作目录（放 uploads/ 和 outputs/）。

    * 源码模式 → 仓库根目录（和 CLI / Web 版一致，历史产物能直接看到）
    * 安装版 → ``<数据目录>``（Program Files 不可写，绝不能往那儿写）
    """
    if not frozen() and in_repo():
        return app_dir()
    return data_dir()


def workspace_dir(cfg: Optional[dict] = None) -> str:
    """用户配置过工作目录就用配置的，否则用默认。"""
    raw = (cfg or {}).get("workspace") or ""
    if raw:
        return ensure_dir(raw)
    return ensure_dir(default_workspace())


def uploads_dir(cfg: Optional[dict] = None) -> str:
    return ensure_dir(os.path.join(workspace_dir(cfg), "uploads"))


def outputs_dir(cfg: Optional[dict] = None) -> str:
    return ensure_dir(os.path.join(workspace_dir(cfg), "outputs"))
