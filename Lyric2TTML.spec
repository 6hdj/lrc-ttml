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

datas = [('index.html', '.'), ('.venvpack/Lib/site-packages/nvidia', 'nvidia'), ('models', 'models')]
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
