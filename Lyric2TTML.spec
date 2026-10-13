# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

    pyinstaller --noconfirm Lyric2TTML.spec

默认构建里**不含 Qt**（PySide6 解包后约 350MB，只在需要桌面窗口时才带）。
要带桌面窗口：

    set L2T_WITH_QT=1
    pyinstaller --noconfirm Lyric2TTML.spec

⚠️ **必须用 onedir（``COLLECT``），不能用 onefile**：实测 255MB 的 onefile
在目标机上会 `[PYI-28444:ERROR] Could not create temporary directory!`
—— onefile 每次启动都要把全部内容解到临时目录，体积一大就失败。
"""
import os

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

WITH_QT = os.environ.get("L2T_WITH_QT") == "1"

datas = [('index.html', '.')]

# =====================================================================
#  CUDA 运行库：**只带 cuBLAS**（实测结论，不是猜的）
# =====================================================================
# 原来是把整个 `nvidia/` 目录打进去 = 2010 MiB（占整个包 64.6%）。
# 实测（`.tmp/exp_cuda_quick.py`，在真 GPU 上跑真音频）：
#
#   cuDNN 1095 MiB + nvrtc 178 MiB + cuBLAS 736 MiB：
#       GPU int8_float16 gen=2.60s   GPU float16 gen=1.50s   引 CPU gen=9.39s
#   不要 cuDNN（省 1095 MiB）：
#       GPU int8_float16 gen=1.65s   GPU float16 gen=1.48s
#   只要 cuBLAS（再省 nvrtc 178 MiB）：
#       GPU int8_float16 gen=1.59s   GPU float16 gen=1.43s
#
# ⇒ **cuDNN 与 nvrtc 对这条链路都不是必需的**，去掉以后还**更快**
#   （CTranslate2 在 Windows 上走 cuBLAS + 自带内核）。GPU 仍在工作，
#   证据是同环境的 CPU 转写要 9.4s、GPU 只要 1.4~1.6s，差 6 倍以上。
#
# ⚠️ 有效性自证（这一步不能省）：把 cublas 整个拿掉时，`device="cuda"`
#    直接失败（CUDA 用不了）—— 说明这个实验真的能测出"坏"。
#    没有这个对照，前面"删了也能跑"的结论一文不值（这是踩过三次的坑：
#    第三版实验里 7 轮全部"成功"，包括故意留空的阴性对照，因为那 7 轮
#    全都静默走了 CPU）。
#
# 运行时仍然按 `nvidia/<lib>/bin` 的布局找 DLL（`asr._register_cuda_dlls`
# 与 `gpu_probe.cuda_dirs` 都认这个布局），所以这里做的是**选择性复制**：
# 只把 cublas 的 bin 放进包里，目录结构保持不变。
_NV = os.path.join('.venvpack', 'Lib', 'site-packages', 'nvidia')
_CUDA_KEEP = ['cublas']          # 只留 cuBLAS；加回 cudnn/nvrtc 前请先实测

if os.path.isdir(_NV):
    for _lib in _CUDA_KEEP:
        _src = os.path.join(_NV, _lib, 'bin')
        if os.path.isdir(_src):
            datas.append((_src, os.path.join('nvidia', _lib, 'bin')))
            _n = len([f for f in os.listdir(_src) if f.lower().endswith('.dll')])
            print(f'[cuda] 打入 {_lib}/bin（{_n} 个 DLL）')

# ⚠️⚠️ **不要**把 `models/` 整个打进去。
#
# 需求原文（体积目标章节）："模型权重应从主安装包中分离。安装包不应包含
# 大型模型权重……模型通过软件内部的模型管理中心下载"。
#
# 而实测 `models/` 有 **307 MB**，其中：
#   * `demucs/hub/checkpoints/*.th`  80MB —— Demucs 权重，模型管理器按需下
#   * `singer_identity/**/model.ts` 5×37MB —— **已冻结、被否决的实验**
#     （`singer_ssl.py`，自检第 24 组禁止生产模块引用它）—— 绝对不该进包
#   * `campplus/campplus_cn_common.bin` 27MB —— CAM++ 权重，模型管理器按需下
#   * `campplus_src/*.py` —— **网络定义**，几十 KB，这个**必须**进包
#     （少了它 `speaker_campplus.available()` 返回 False，
#      开了「强制双声部」会静默退回单人 —— 历史上真踩过）
#
# 所以只打 `campplus_src`（网络定义），权重一律走模型管理器。
# 安全性的证据：`.tmp/verify_campplus_dl.py` 在空目录里真下载过 CAM++
# （28036335 字节、SHA256 一致、check() 判 READY）。
if os.path.isdir(os.path.join('models', 'campplus_src')):
    datas += [('models/campplus_src', 'models/campplus_src')]
    print('[models] 只打网络定义 models/campplus_src（权重由模型管理器下载）')
# 桌面版：模型清单必须随包带上（第一次启动就要按它检查模型）
datas += [('desktop/resources/model_manifest.json', 'desktop/resources')]
binaries = []
hiddenimports = ['doctor', 'envcheck', 'install', 'download_model',
                 # 卸载逻辑在 uninstall_core（**故意不放在 installer_gui**：
                 # 后者开头 import tkinter，而主程序的构建排除了 tkinter）。
                 # launcher 里是延迟导入，静态分析看不到。
                 'uninstall_core', 'installer_gui',
                 'desktop.selftest', 'desktop.uicheck',
                 # 桌面壳（launcher 里是**延迟导入**，PyInstaller 静态分析看不到）
                 'desktop', 'desktop.app', 'desktop.main_window', 'desktop.context',
                 'desktop.paths', 'desktop.config', 'desktop.fileguess',
                 'desktop.pages', 'desktop.pages.base', 'desktop.pages.welcome',
                 'desktop.pages.home', 'desktop.pages.tasks', 'desktop.pages.models',
                 'desktop.pages.settings',
                 'desktop.services', 'desktop.services.task_manager',
                 'desktop.services.model_manager', 'desktop.services.backend_service',
                 'desktop.services.convert_params',
                 'desktop.services.environment_service', 'desktop.services.gpu_probe',
                 'desktop.services.log_service', 'desktop.workers']
datas += collect_data_files('pypinyin')
datas += collect_data_files('faster_whisper')
datas += copy_metadata('faster-whisper')
datas += copy_metadata('ctranslate2')
datas += copy_metadata('tokenizers')
datas += copy_metadata('huggingface-hub')
datas += copy_metadata('numpy')
datas += copy_metadata('onnxruntime')
binaries += collect_dynamic_libs('ctranslate2')
binaries += collect_dynamic_libs('onnxruntime')
hiddenimports += collect_submodules('huggingface_hub')
tmp_ret = collect_all('torch')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# ⚠️ PySide6 不能只写 hiddenimports：Qt 的**插件**（platforms/qwindows.dll、
# styles、imageformats）不会自动带上，缺了会直接报
# "This application failed to start because no Qt platform plugin could be
#  initialized"。
#
# ⚠️⚠️ 但也**绝不能**图省事写 `collect_all('PySide6')`：那会把
# PySide6-Addons 的全部东西都拖进来（实测多出约 200MB）——
# Qt6Quick / Qt6Qml / Qt63D* / Qt6WebView / Qt6Multimedia / Qt6Charts …
# 我们**一个都不用**（界面是纯 QtWidgets，编辑台走系统浏览器）。
# 正确做法是只让 PyInstaller 的标准钩子按**真实 import** 收：
#   hook-PySide6.QtCore / QtGui / QtWidgets  → 收这三个模块的 DLL、
#   platforms/ styles/ imageformats/ 插件与需要的翻译。
# 所以下面只对这三个子模块一一 collect_all；
# **加新界面模块时必须在这里补一行**，否则打包版会 ImportError。
if WITH_QT:
    for _mod in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
        _r = collect_all(_mod)
        datas += _r[0]; binaries += _r[1]; hiddenimports += _r[2]
    _shib = collect_all('shiboken6')
    datas += _shib[0]; binaries += _shib[1]; hiddenimports += _shib[2]

_excludes = ['tkinter', 'matplotlib', 'PyQt5', 'PyQt6', 'PySide2',
             'IPython', 'pytest', 'notebook', 'torch.distributed', 'scipy',
             'pandas', 'PIL', 'setuptools', 'pip', 'wheel', 'functorch',
             'nvidia', 'demucs', 'sympy', 'networkx', 'mpmath']
if not WITH_QT:
    _excludes.append('PySide6')                          # 不带 Qt 的构建要排除掉


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
    optimize=0,
)


# =====================================================================
#  体积裁剪（prune）
# =====================================================================
# 为什么要在 spec 里做这件事，而不是调 collect_all 的参数：
# `collect_all('torch')` / `collect_all('PySide6')` 会把**整个发行包**，
# 包括构建期产物（.lib / .h）、测试套件、示例、命令行小工具一起收进来。
# 这些文件对运行时**一点用都没有**，但白占几百 MB（实测 torch 的
# include 38MB + .lib 42MB、PySide6 的 opengl32sw 20MB …）。
#
# ⚠️ 三条纪律（需求原文要求"不能仅凭文件名判断 DLL 无用"）：
#   1. 只删**能说清为什么不需要**的东西：构建产物、测试、示例、文档、
#      命令行工具、以及**我们明确不用的 Qt 子系统**；
#   2. **绝不删** .dll / .pyd / .so / 数据文件 —— 那些可能是运行时真的会
#      加载的（Qt 插件尤其如此）；
#   3. 每轮裁剪之后都要跑一次**冻结版验收**
#      （`python tools/accept_frozen.py <exe>`），证明功能没坏。
#
# 想关掉裁剪看基线：设 `L2T_NO_PRUNE=1`。
import os as _os

_PRUNE = _os.environ.get("L2T_NO_PRUNE") != "1"

# (匹配前缀, 说明) —— 大小写不敏感，匹配的是归档内的相对路径
_PRUNE_RULES = [
    # ---- torch：构建期产物与测试 ----
    ("torch/include", "C++ 头文件（编译 torch 扩展才需要）"),
    ("torch/bin", "protoc.exe 等命令行工具"),
    ("torch/test", "torch 自带测试"),
    ("torch/testing", "torch.testing 测试工具"),
    # ---- Qt：我们明确不用的子系统 ----
    # 桌面窗口是**纯 QtWidgets**；编辑台走系统浏览器（不引入 WebEngine）。
    # ⚠️ 注意这里**只删 Qt6 的 DLL**，不删 PySide6 的 .pyd：
    # 那些是 Python 绑定，删错会 ImportError。
    ("PySide6/opengl32sw.dll", "Qt 的软件 OpenGL 兜底（20MB，只有无 GPU 驱动才用）"),
    ("PySide6/d3dcompiler_47.dll", "Qt 的 D3D 着色器编译器（ANGLE 用，Qt6 不再需要）"),
    ("PySide6/translations", "Qt 自带界面翻译（我们的界面是中文自绘，不用 Qt 内置文案）"),
    ("PySide6/qml", "QML 运行时资源（我们用 Widgets，不用 QML）"),
    ("PySide6/typesystems", "Qt for Python 的 .typesystem 源文件（生成绑定用）"),
    ("PySide6/include", "Qt for Python 头文件"),
    ("PySide6/scripts", "pyside6-deploy 等命令行工具"),
    ("PySide6/support", "生成 .pyi 的辅助脚本"),
    ("PySide6/doc", "文档"),
    ("shiboken6/include", "shiboken 头文件"),
    ("shiboken6/scripts", "shiboken 命令行工具"),
    # ---- 通用：文档/示例/测试 ----
    ("**/tests/", "第三方包的测试目录"),
    ("**/examples/", "第三方包的示例目录"),
    ("**/__pycache__", "字节码缓存（PyInstaller 自己会编译）"),
]

# Qt6 里我们**不用**的模块（桌面窗口只 import QtCore / QtGui / QtWidgets）。
# ⚠️ 只列**确定不用**的：Qt6Network / Qt6Svg / Qt6PrintSupport 这些
# 会被 QtWidgets 或插件间接用到，**不删**。
_QT_UNUSED_DLLS = [
    "Qt6Quick", "Qt6Qml", "Qt6QmlMeta", "Qt6QmlModels", "Qt6QmlWorkerScript",
    "Qt6QmlCore", "Qt6QmlLocalStorage", "Qt6QmlXmlListModel", "Qt6QmlNetwork",
    "Qt6Designer", "Qt6DesignerComponents", "Qt6UiTools",
    "Qt6WebEngine", "Qt6WebView", "Qt6WebChannel",
    "Qt6Multimedia", "Qt6Charts", "Qt6DataVisualization", "Qt6Graphs",
    "Qt6Sql", "Qt6Test", "Qt6Bluetooth", "Qt6Nfc", "Qt6Positioning",
    "Qt6Location", "Qt6SerialPort", "Qt6SerialBus", "Qt6Sensors",
    "Qt6SpatialAudio", "Qt6TextToSpeech", "Qt6Scxml", "Qt6StateMachine",
    "Qt6RemoteObjects", "Qt6NetworkAuth", "Qt6HttpServer", "Qt6Lottie",
    "Qt6Pdf", "Qt6VirtualKeyboard", "Qt63D", "Qt6ShaderTools", "Qt6Canvas",
]
for _m in _QT_UNUSED_DLLS:
    _PRUNE_RULES.append((f"PySide6/{_m}", f"Qt 未使用模块 {_m}"))


def _norm(toc):
    """把 TOC 统一成 (dest, src, typecode) 三段（PyInstaller 4/5/6 通用）。"""
    out = []
    for e in (toc or []):
        if len(e) >= 3 and isinstance(e[1], str):
            out.append((e[0], e[1], e[2]))
        elif len(e) >= 2:
            out.append((e[0], e[1], "DATA"))
    return out


def _match(dest, rule):
    d = dest.replace("\\", "/").lower()
    r = rule.lower()
    if r.startswith("**/") and r.endswith("/"):
        return f"/{r[3:]}" in d or d.startswith(r[3:])
    return d.startswith(r)


def _prune(toc, label):
    """按规则裁掉 TOC 里的条目，并打印**真的删了多少**。"""
    if not _PRUNE:
        print(f"[prune] {label}: 已禁用（L2T_NO_PRUNE=1），保留全部 {len(toc)} 项")
        return toc
    kept, dropped = [], []
    for item in toc:
        dest = item[0] if isinstance(item[0], str) else str(item[0])
        hit = None
        for rule, why in _PRUNE_RULES:
            if _match(dest, rule):
                hit = why
                break
        if hit:
            dropped.append((dest, hit))
        else:
            kept.append(item)
    print(f"[prune] {label}: 裁掉 {len(dropped)} 项，保留 {len(kept)} 项")
    seen = {}
    for dest, why in dropped:
        key = why.split("（")[0]
        seen[key] = seen.get(key, 0) + 1
    for why, n in sorted(seen.items(), key=lambda x: -x[1])[:12]:
        print(f"        - {why}: {n} 项")
    return kept


a.binaries = _prune(_norm(a.binaries), "binaries")
a.datas = _prune(_norm(a.datas), "datas")
a.zipfiles = _norm(a.zipfiles)


pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Lyric2TTML',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Lyric2TTML',
)
