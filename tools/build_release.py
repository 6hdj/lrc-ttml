"""构建 Release 附件（Windows / PyInstaller）。

    python tools/build_release.py --lite        # 轻量版（默认，推荐）
    python tools/build_release.py --full        # 完整版（含 torch，约 5 GB，不建议）
    python tools/build_release.py --portable    # 便携 ZIP（不打包 exe，直接用 Python）

**桌面窗口（Qt）默认不带**，要带就设环境变量：

    set L2T_WITH_QT=1
    python tools/build_release.py --lite        # 带 Qt，约 3 GB

⚠️ 发布给普通用户的安装包**应该带 Qt**（README 承诺"双击桌面图标直接进软件"）；
不带 Qt 的包双击后会退回浏览器界面 —— 那是给"只想用 Web 界面/要省体积"的人的选项。
官方 GUI 安装包（`--installer`）会自动带上 Qt（见 `build_installer`）。

产物落在 ``dist/``：

    dist/Lyric2TTML-<版本>-win64-lite.zip     ← 上传到 GitHub Release
    dist/Lyric2TTML-<版本>-win64-full.zip
    dist/Lyric2TTML-<版本>-portable.zip

``*_WITH_QT=0`` 时上面几个文件名会带 ``-noweb`` 后缀以免混淆。

--------------------------------------------------------------------------
为什么默认是"轻量版"
--------------------------------------------------------------------------
本项目的依赖里有两块**体积极不对称**的东西：

    torch  +  nvidia CUDA 运行时    ≈ 4.9 GB   ← 只给"声纹分声部"和"人声分离"用
    ctranslate2 / onnxruntime / av  ≈ 0.15 GB  ← 语音识别（核心功能）

而 torch 在本项目里**全部是函数内 import**（`speaker_campplus.py` / `separate.py`
/ `asr.py` 的 DLL 注册），所以不打进去也**不会崩**，只是用不了那两个可选功能。

把 4.9 GB 塞进一个"下载即用"的 exe 是划不来的 —— 用户下 5 GB 才能用上
一个 0.2 GB 就能跑的核心功能。所以：

* **lite**（默认）：≈ 250 MB，ASR + 对齐 + 全部 TTML 功能 + 编辑台；
  分声部/人声分离需要额外装 torch（README 里有说明）。
* **full**：≈ 5 GB，功能全，但只在你确实要给别人"开箱即用全功能"时才值得。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)

APP = "Lyric2TTML"
VERSION = "1.1.0"

# 不随包分发的大件（见文件头的说明）
HEAVY = ["torch", "torchvision", "torchaudio", "torchgen", "functorch",
         "nvidia", "demucs", "sympy", "networkx", "mpmath"]
# 明确用不到、打进去只会变大的
UNUSED = ["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6",
          "IPython", "pytest", "notebook", "torch.distributed", "scipy",
          "pandas", "PIL", "setuptools", "pip", "wheel"]

# 桌面版（PySide6）要不要打进包里。默认**不带**（省约 350MB）；
# 官方 GUI 安装包会自动打开它。
WITH_QT = os.environ.get("L2T_WITH_QT") == "1"

# PySide6 里我们**一个都不用**的模块。
# ⚠️ 必须显式排除：`collect_all('PySide6')` 会把 Essentials + Addons 全拖进来
# （QtQuick / QtQml / Qt3D* / QtWebView / QtMultimedia / QtCharts …），
# 实测多约 90MB DLL 和几百个无用文件。桌面窗口是纯 QtWidgets。
QT_UNUSED = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickTest",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebView", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtHelp",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtSensors",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtScxml",
    "PySide6.QtRemoteObjects", "PySide6.QtNetworkAuth", "PySide6.QtHttpServer",
    "PySide6.QtStateMachine", "PySide6.QtLottie", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtVirtualKeyboard",
]

# 挨着 exe 放的"用户能看见/能改"的东西
SIDE_FILES = ["index.html", "README.md", "LICENSE", "CHANGELOG.md", "启动.bat",
              "检查环境.bat", "下载模型.bat"]
SIDE_DIRS = ["examples"]

# 便携版（portable zip）里的一键安装脚本。内容在 build_portable 里落地。
# ⚠️ 三个 .bat 里的中文都用 **GBK(936)** 写：cmd.exe 按本机 ANSI 代码页读批处理，
#    写 UTF-8 会让提示全变乱码。脚本开头仍 `chcp 65001`，那是给 Python 用的。
INSTALL_BAT = """@echo off
chcp 65001 >nul
title Lyric2TTML - 安装 / 补全
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py -3" )
if not defined PY (
  echo [错误] 没找到 Python。
  echo.
  echo   请到 https://www.python.org/downloads/ 下载 Python 3.10 或更高版本，
  echo   安装时**务必勾选** "Add Python to PATH"，然后重新运行本脚本。
  echo.
  pause
  exit /b 1
)

REM 已经有虚拟环境就用它（说明之前装过）
set "VPY=%~dp0.venv\\Scripts\\python.exe"
if not exist "%VPY%" set "VPY=%PY%"

"%VPY%" install.py --yes
echo.
echo 装完可以双击 启动.bat 使用。
pause
"""
SETUP_BAT = """@echo off
chcp 65001 >nul
title Lyric2TTML - 首次安装
cd /d "%~dp0"

echo ================================================================
echo   Lyric2TTML 首次安装
echo   联网一次装依赖 + 下模型，之后**完全离线**使用
echo ================================================================
echo.

REM ---------- 1. 找 Python ----------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py -3" )
if not defined PY (
  echo [错误] 没找到 Python。
  echo.
  echo   请到 https://www.python.org/downloads/ 下载 Python 3.10 或更高版本，
  echo   安装时**务必勾选** "Add Python to PATH"，然后重新运行本脚本。
  echo.
  pause
  exit /b 1
)
echo [1/5] Python 就绪

REM ---------- 2. 虚拟环境 ----------
if exist ".venv\\Scripts\\python.exe" (
  echo [2/5] 虚拟环境已存在，跳过
) else (
  echo [2/5] 创建虚拟环境 .venv ...
  %PY% -m venv .venv
  if errorlevel 1 ( echo [错误] 创建虚拟环境失败 & pause & exit /b 1 )
)
set "VPY=%~dp0.venv\\Scripts\\python.exe"

REM ---------- 3. 依赖 ----------
echo [3/5] 安装依赖（第一次要几分钟，请耐心等）...
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [错误] 依赖安装失败。请检查网络后重新运行本脚本。
  pause
  exit /b 1
)

REM ---------- 4. 语音识别模型（关键，最常出问题的一步）----------
echo.
echo [4/5] 下载语音识别模型（large-v3 约 3GB，只需一次）
echo       国内直连 HuggingFace 常失败，脚本会自动先试 hf-mirror 镜像。
"%VPY%" download_model.py
if errorlevel 1 (
  echo.
  echo [警告] 模型没下完 —— 不影响安装，但**转换时会失败**。
  echo        可以稍后双击 下载模型.bat 重试（支持断点续传），
  echo        或先用小模型：编辑 下载模型.bat 改成 --model small
  echo.
)

REM ---------- 5. 体检 ----------
echo.
echo [5/5] 环境体检
"%VPY%" doctor.py

echo.
echo ================================================================
echo   安装结束。以后双击「启动.bat」就能用。
echo   有任何问题，双击「检查环境.bat」看缺什么。
echo ================================================================
pause
"""

CHECK_BAT = """@echo off
chcp 65001 >nul
title Lyric2TTML - 检查环境
cd /d "%~dp0"
set "VPY=%~dp0.venv\\Scripts\\python.exe"
if not exist "%VPY%" (
  echo 还没安装。请先双击「首次安装.bat」。
  pause
  exit /b 1
)
"%VPY%" doctor.py
pause
"""

DLMODEL_BAT = """@echo off
chcp 65001 >nul
title Lyric2TTML - 下载模型
cd /d "%~dp0"
set "VPY=%~dp0.venv\\Scripts\\python.exe"
if not exist "%VPY%" (
  echo 还没安装。请先双击「首次安装.bat」。
  pause
  exit /b 1
)
echo 下载 large-v3（约 3GB）。想要小一点的，把 download_model.py 后面
echo 加上 --model small 即可（可编辑本文件）。
echo.
"%VPY%" download_model.py
pause
"""


def run(cmd: list, **kw) -> int:
    print("  $ " + " ".join(cmd))
    return subprocess.call(cmd, **kw)


def folder_size(path: str) -> int:
    total = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total


def clean(keep_dist: bool = True) -> None:
    """清理中间产物。

    ⚠️ **不要整个删 dist/** —— 那样"先打 lite、再打 portable"会把刚做好的
    附件删掉（真踩过：跑完 --portable，94MB 的 lite zip 没了）。
    只清 PyInstaller 的中间目录和 spec；dist 里各变体的子目录由各自函数清。
    """
    for d in ("build", f"{APP}.spec"):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        elif os.path.exists(d):
            os.remove(d)
    if not keep_dist:
        shutil.rmtree("dist", ignore_errors=True)


def build_exe(full: bool) -> str:
    variant = "full" if full else "lite"
    if WITH_QT:
        # 文件名要能区分"带不带桌面窗口"，否则发布时两个包长得一样
        variant += "-desktop"
    out = os.path.join("dist", APP)
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean", "--onedir",
           "--name", APP,
           # index.html 也要进包（冻结后 launcher 会把 HERE 指到 exe 旁边，
           # 那里会再放一份"用户可见"的；包里这份是兜底）
           "--add-data", "index.html;.",
           # 这些包带数据/二进制，PyInstaller 的自动收集不一定全
           "--collect-data", "pypinyin",
           "--collect-data", "faster_whisper",
           "--collect-binaries", "ctranslate2",
           "--collect-binaries", "onnxruntime",
           "--collect-submodules", "huggingface_hub",
           # 运行时元数据（faster_whisper 会 importlib.metadata.version）
           "--copy-metadata", "faster-whisper",
           "--copy-metadata", "ctranslate2",
           "--copy-metadata", "tokenizers",
           "--copy-metadata", "huggingface-hub",
           "--copy-metadata", "numpy",
           "--copy-metadata", "onnxruntime",
           # ⚠️ 这几个是在 launcher.main() 里**条件 import** 的
           # （--doctor / --install / --download-model），PyInstaller 的静态分析
           # 追不到条件分支，必须显式列出；漏了就会出现
           # "Lyric2TTML.exe --doctor 报 ModuleNotFoundError"。
           "--hidden-import", "doctor",
           "--hidden-import", "envcheck",
           "--hidden-import", "install",
           "--hidden-import", "download_model",
           ]
    # ⚠️ 桌面壳（desktop/）是**延迟导入**的（launcher 里 import desktop.app），
    # 而 desktop 内部又有一堆按需 import 的页面/服务模块 —— PyInstaller 的
    # 静态分析追不全，必须显式列。漏了的后果不是崩溃，而是
    # **"双击进不去桌面窗口、悄悄退回浏览器界面"**（有兜底，所以更难发现）。
    for m in (
        "desktop", "desktop.app", "desktop.main_window", "desktop.context",
        "desktop.paths", "desktop.config", "desktop.fileguess",
        "desktop.pages", "desktop.pages.base", "desktop.pages.welcome",
        "desktop.pages.home", "desktop.pages.tasks", "desktop.pages.models",
        "desktop.pages.settings",
        "desktop.services", "desktop.services.task_manager",
        "desktop.services.model_manager", "desktop.services.convert_params",
        "desktop.services.backend_service",
        "desktop.services.environment_service", "desktop.services.gpu_probe",
        "desktop.services.log_service", "desktop.workers",
        # 打包版自检用（--selftest / --uicheck 走 import，不走 runpy）
        "desktop.selftest", "desktop.uicheck",
    ):
        cmd += ["--hidden-import", m]
    # 模型清单必须随包带上（第一次启动就按它检查模型；不带的话
    # 模型页一行都显示不出来）
    cmd += ["--add-data",
            os.path.join("desktop", "resources", "model_manifest.json")
            + os.pathsep + os.path.join("desktop", "resources")]
    if WITH_QT:
        # ⚠️ 只收我们真的用的三个模块。`--collect-all PySide6` 会把
        # Essentials + Addons 全拖进来（实测多约 90MB 无用 DLL）。
        for m in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
                  "shiboken6"):
            cmd += ["--collect-all", m]
        print("  带上 Qt 桌面窗口（PySide6）")
    for m in UNUSED:
        if WITH_QT and m == "PySide6":
            continue                       # 带了 Qt 就不能再排除它
        cmd += ["--exclude-module", m]
    for m in (QT_UNUSED if WITH_QT else []):
        cmd += ["--exclude-module", m]
    # ⚠️ **torch 必须留在包里**（除非显式要求排除）。
    # CAM++ 声纹分声部的网络是用 PyTorch 写的（speaker_campplus.py），
    # 以前 lite 版把 torch 一起排除掉 → 用户开「强制双声部」时
    # **要么静默降级成单人、要么 No module named 'torch'**（两个都真踩过）。
    # 现在用 `.venvpack` 里的 **CPU 版 torch**（约 200MB，不是 CUDA 版那 2.9GB）：
    # 分声部能跑，而 ASR 的 GPU 加速走 CTranslate2，完全不受影响。
    keep_torch = os.environ.get("L2T_WITH_TORCH", "1") == "1"
    skip = [m for m in HEAVY if not (keep_torch and m.startswith("torch"))]
    if not full:
        for m in skip:
            cmd += ["--exclude-module", m]
    # ⚠️ torch 必须用 **--collect-all**，不能只用 --collect-data：
    # torch 的核心是 `torch\_C.pyd` 加 `torch\lib\*.dll`（c10 / c10_cuda / torch_cpu …），
    # 只收"数据文件"会出现"目录里有 torch、import 却失败"的假象（真踩过：
    # `_internal\torch` 有 445MB，`import torch` 照样 ModuleNotFoundError）。
    if keep_torch:
        cmd += ["--collect-all", "torch"]
    # ⚠️⚠️ **CAM++ 的网络定义必须打进包里**（`models/campplus_src/`）。
    # 声纹分声部需要两样东西：
    #   ① 权重 `models/campplus/campplus_cn_common.bin` —— 安装程序从 ModelScope 下；
    #   ② 网络定义 `models/campplus_src/model.py` —— **在仓库里，必须构建时打包**。
    # 少了 ②，`speaker_campplus.available()` 返回 False，`diarize` 就**退回单人**：
    # 用户开着「强制双声部」却一行都不分（真踩过，而且是查了很久才发现的）。
    # ⚠️⚠️ **CUDA 运行库（cuBLAS + cuDNN）必须打进包里**。
    # 事实（上网核对过，多个来源一致）：
    #   * NVIDIA 驱动只提供 **驱动层**（nvcuda.dll），让 GPU 能跑推理 ——
    #     所以**不需要** CUDA Toolkit、不需要 nvcc、不需要单独"装 CUDA"；
    #   * 但 cuBLAS / cuDNN **不在驱动里**，是独立的运行时库，CTranslate2
    #     要用它们（faster-whisper 的经典报错就是
    #     `Library cublas64_12.dll is not found or cannot be loaded`）。
    #   正确做法就是"当 Python 依赖装"（`nvidia-cublas-cu12` / `nvidia-cudnn-cu12`），
    #   构建期直接放进 `_internal/nvidia/`，`asr._register_cuda_dlls()` 会扫到。
    # 之前让安装程序自己 HTTP 下这 2GB，既慢又容易被网络截断（BadZipFile），
    # 纯属自找麻烦。
    nv = os.path.join(".venvpack", "Lib", "site-packages", "nvidia")
    if not os.path.isdir(nv):
        nv = os.path.join(".venv", "Lib", "site-packages", "nvidia")
    if os.path.isdir(nv):
        cmd += ["--add-data", f"{nv}{os.pathsep}nvidia"]
        print(f"  带上 CUDA 运行库：{nv}")
    else:
        print("  ⚠ 没找到 nvidia 包，GPU 推理会失败（先 pip install "
              "nvidia-cublas-cu12 nvidia-cudnn-cu12）")
    if os.path.isdir(os.path.join("models", "campplus_src")):
        cmd += ["--add-data", f"models{os.pathsep}models"]
    cmd.append("launcher.py")

    rc = run(cmd)
    if rc != 0:
        raise SystemExit(f"PyInstaller 失败（退出码 {rc}）")

    # 把"用户可见"的文件放到 exe 旁边（不是 _internal 里面）
    for f in SIDE_FILES:
        if os.path.exists(f):
            shutil.copy2(f, os.path.join(out, f))

    # exe 版的两个助手脚本：直接调 exe 自己（打包版不需要装 Python）
    for fname, args, title in (("检查环境.bat", "--doctor", "检查环境"),
                               ("下载模型.bat", "--download-model", "下载模型")):
        io.open(os.path.join(out, fname), "w", encoding="gbk", newline="\r\n").write(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f"title Lyric2TTML - {title}\r\n"
            "cd /d \"%~dp0\"\r\n"
            f"\"%~dp0{APP}.exe\" {args}\r\n"
            "pause\r\n")
    # 首次安装.bat 对打包版没意义（自带 Python），放一个说明性的转发即可
    io.open(os.path.join(out, "首次安装.bat"), "w", encoding="gbk", newline="\r\n").write(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title Lyric2TTML - 首次安装\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo 打包版自带运行环境，不用装 Python。\r\n"
        "echo 只需要把语音识别模型下载一次（约 3GB，之后完全离线）。\r\n"
        "echo.\r\n"
        f"\"%~dp0{APP}.exe\" --download-model\r\n"
        "echo.\r\n"
        "echo 完成后双击「检查环境.bat」确认，全绿就能用了。\r\n"
        "pause\r\n")
    for d in SIDE_DIRS:
        if os.path.isdir(d):
            dst = os.path.join(out, d)
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(d, dst)
    # 数据目录先建好，用户一眼能看到东西放哪
    os.makedirs(os.path.join(out, "uploads"), exist_ok=True)
    os.makedirs(os.path.join(out, "outputs"), exist_ok=True)
    io.open(os.path.join(out, "outputs", "把转换结果放这里.txt"), "w",
            encoding="utf-8").write("转换生成的 .ttml 都在这个目录里。\n")

    zip_path = os.path.join("dist", f"{APP}-{VERSION}-win64-{variant}.zip")
    zip_dir(out, zip_path)
    size = folder_size(out)
    print(f"\n  目录：{out}  （{size/1024/1024:.0f} MB）")
    print(f"  压缩：{zip_path}  （{os.path.getsize(zip_path)/1024/1024:.0f} MB）")
    return out


def zip_dir(src: str, dst: str) -> None:
    if os.path.exists(dst):
        os.remove(dst)
    base = os.path.dirname(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for r, _, fs in os.walk(src):
            for f in fs:
                p = os.path.join(r, f)
                z.write(p, os.path.relpath(p, base))


def build_portable() -> str:
    """便携 ZIP：源码 + 一键安装脚本（不打包 exe）。

    给"愿意装一次、但要完全离线/可调试"的用户 —— 比 exe 更透明，
    而且**能跑全部功能**（包括需要 torch 的分声部）。
    """
    name = f"{APP}-{VERSION}-portable"
    out = os.path.join("dist", name)
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)

    # 只拷源码（用 git 跟踪的清单，天然排除 outputs/models/uploads/.venv）
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                       encoding="utf-8")
    files = [f.strip() for f in (r.stdout or "").splitlines() if f.strip()]
    for f in files:
        if f.startswith("tests/fixtures/"):
            continue                       # fixture 是给自检的，用户不需要
        dst = os.path.join(out, f)
        os.makedirs(os.path.dirname(dst) or out, exist_ok=True)
        shutil.copy2(f, dst)

    # 四个一键脚本（详见文件上方 SETUP_BAT / INSTALL_BAT / CHECK_BAT / DLMODEL_BAT）
    for fname, content in (("首次安装.bat", SETUP_BAT),
                           ("安装.bat", INSTALL_BAT),
                           ("检查环境.bat", CHECK_BAT),
                           ("下载模型.bat", DLMODEL_BAT)):
        io.open(os.path.join(out, fname), "w", encoding="gbk",
                newline="\r\n").write(content)

    zip_path = os.path.join("dist", f"{name}.zip")
    zip_dir(out, zip_path)
    print(f"\n  目录：{out}  （{folder_size(out)/1024/1024:.0f} MB）")
    print(f"  压缩：{zip_path}  （{os.path.getsize(zip_path)/1024/1024:.0f} MB）")
    return out


def build_installer() -> str:
    """打包**真正的安装程序**：一个 exe 里同时带 GUI 和主程序本体。

    ⚠️ 以前是"安装程序.exe + lite.zip 两个文件，用户得先解压再点安装程序" ——
    用户直接骂了："那不脱裤子放屁吗？" 对，正常的软件安装程序就该**一个文件**：
    双击 → 选目录 → 解压安装 → 检查环境 → 缺什么装什么 → 能用。

    做法：先把主程序（onedir）打成一份 zip 当**载荷**，再用 `--add-data` 塞进安装程序。
    运行时 `installer_gui` 把载荷解到用户选的目录，然后照常检查/补全/启动。

    ⚠️ **安装包默认带 Qt**（`L2T_WITH_QT` 会被强制打开）：桌面快捷方式必须
    双击就出窗口；不带 Qt 的包双击会退回浏览器界面，那就不叫"桌面应用"了。
    想刻意做"不带 Qt 的安装包"，设 `L2T_WITH_QT=0` 明确要求。
    """
    global WITH_QT
    if os.environ.get("L2T_WITH_QT", "1") == "1" and not WITH_QT:
        WITH_QT = True
        os.environ["L2T_WITH_QT"] = "1"
        print("  安装包强制带上 Qt 桌面窗口（L2T_WITH_QT=1）")

    # ① 先把主程序构建出来（安装包里要有东西可装）
    app_dir = os.path.join("dist", APP)
    if not os.path.isdir(app_dir) or not os.path.exists(
            os.path.join(app_dir, f"{APP}.exe")):
        print("  先构建主程序（lite）…")
        run([sys.executable, __file__, "--lite"])

    # ② 打成载荷 zip
    os.makedirs("build", exist_ok=True)
    payload = os.path.join("build", "_app_payload.zip")
    if os.path.exists(payload):
        os.remove(payload)
    print(f"  打包载荷 → {payload}")
    zip_dir(app_dir, payload)
    if not os.path.exists(payload):
        raise SystemExit("载荷打包失败")

    # ③ 安装程序本体（GUI + 载荷）
    out = os.path.join("dist", "安装程序.exe")
    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean", "--onefile",
           "--name", "安装程序",
           # ⚠️ **安装程序必须要求管理员权限**：它要往用户选的任意目录写
           # （包括盘根下的新建目录）、要建桌面快捷方式、要写注册表以外的东西。
           # 不加这个，普通权限会 `WinError 5 拒绝访问`，安装到一半失败。
           # ⚠️ 但**主程序不要**要求管理员 —— 那样浏览器会以管理员身份启动，
           # 之后普通权限打开就报"现有实例正在以提升的权限运行"。
           "--uac-admin",
           "--add-data", f"{payload}{os.pathsep}payload",
           "--hidden-import", "install",
           "--hidden-import", "download_model",
           "--hidden-import", "asr",
           "--hidden-import", "envcheck",
           # 安装程序只要 GUI + 下载能力；重的推理件一律排除（载荷里已经有一份）
           "--exclude-module", "torch",
           "--exclude-module", "demucs",
           "--exclude-module", "numpy",
           "--exclude-module", "ctranslate2",
           "--exclude-module", "onnxruntime",
           "--exclude-module", "faster_whisper",
           "--exclude-module", "matplotlib",
           "installer_gui.py"]
    if run(cmd) != 0:
        raise SystemExit("安装程序打包失败")
    if os.path.exists(out):
        print(f"\n  → {out}  （{os.path.getsize(out)/1024/1024:.1f} MB，"
              f"含主程序本体）")
    else:
        cand = os.path.join("dist", "安装程序")
        if os.path.exists(cand):
            shutil.move(cand, out)
            print(f"\n  → {out}  （{os.path.getsize(out)/1024/1024:.1f} MB）")
    return out


def smoke_test(out: str) -> bool:
    exe = os.path.join(out, f"{APP}.exe")
    if not os.path.exists(exe):
        print(f"  ✗ 找不到 {exe}")
        return False
    print("\n  冒烟测试：--help")
    r = subprocess.run([exe, "--help"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and "usage" in (r.stdout or "").lower()
    print(f"    {'✓' if ok else '✗'} 退出码 {r.returncode}")
    if not ok:
        print((r.stdout or "")[-500:])
        print((r.stderr or "")[-800:])

    # ⚠️ 带 Qt 的包要额外验一次"桌面窗口能不能建出来" —— 这是本次改造的
    # 核心验收点。缺 Qt 平台插件时 exe 起不来，而 `--help` 照样通过，
    # 所以不测这一条的话"打包版没有桌面窗口"会一路溜到用户手里。
    if WITH_QT:
        print("\n  冒烟测试：桌面窗口（Qt 平台插件）")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                   L2T_ALLOW_MULTI="1", L2T_HEADLESS="1",
                   L2T_DATA_DIR=os.path.join(out, ".smoke-data"))
        r = subprocess.run([exe, "--uicheck"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env,
                           cwd=out, timeout=900)
        ui_ok = r.returncode == 0 and "全部通过" in (r.stdout or "")
        print(f"    {'✓' if ui_ok else '✗'} 退出码 {r.returncode}")
        if not ui_ok:
            print((r.stdout or "")[-800:])
            print((r.stderr or "")[-800:])
            return False

    print("\n  冒烟测试：起 Web 服务")
    env = dict(os.environ, L2T_PORT="8877")
    p = subprocess.Popen([exe, "--web"], env=env, cwd=out,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    import time
    import urllib.request
    got = False
    for _ in range(40):
        time.sleep(1)
        try:
            with urllib.request.urlopen("http://127.0.0.1:8877/", timeout=3) as resp:
                body = resp.read()
                got = resp.status == 200 and b"Lyric2TTML" in body
                break
        except Exception:
            if p.poll() is not None:
                break
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
    print(f"    {'✓' if got else '✗'} 页面可访问")
    if not got and p.stdout:
        try:
            print("  服务输出：" + p.stdout.read()[-600:])
        except Exception:
            pass
    return ok and got


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 Lyric2TTML Release 附件")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--lite", action="store_true", help="轻量版 exe（默认）")
    g.add_argument("--full", action="store_true", help="完整版 exe（含 torch）")
    g.add_argument("--portable", action="store_true", help="便携 ZIP（源码 + 安装脚本）")
    g.add_argument("--installer", action="store_true", help="图形化安装程序 exe")
    ap.add_argument("--no-smoke", action="store_true", help="跳过冒烟测试")
    args = ap.parse_args()

    clean()
    if args.portable:
        build_portable()
        return 0
    if args.installer:
        build_installer()
        return 0

    out = build_exe(full=args.full)
    ok = True if args.no_smoke else smoke_test(out)
    print(f"\n{'=' * 60}")
    print("  打包完成" + ("" if ok else "（但**冒烟测试失败**，先别发布）"))
    print(f"{'=' * 60}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
