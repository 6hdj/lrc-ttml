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

# ⚠️⚠️ **默认不打 CUDA 运行时**（方案 D，用户 2026-10-14 拍板）。
#
# 背景（`docs/商业化重构审计与体积报告.md` §3.6 + 待决策）：
#   * 含 CUDA 的主程序目录 1078.2 MB → 单文件安装包 **717.7 MB**；
#   * 不含 CUDA 的 342.4 MB → **189.9 MB**；
#   * 但**不含 CUDA 时语音识别根本跑不了**（`asr.py` 明确拒绝 CPU，
#     不是"慢跑"）—— 所以不能删，只能"移出主包 + 按需下载"。
#
# 已端到端验过（`tools/test_scheme_d_e2e.py`，10/10）：
#   不含 CUDA 的包 → 从 GitHub Release 真下 528.8 MB（93 秒，支持 Range）
#   → 校验落盘 → `cuda_dirs()` 找到 → **真转换成功、严格校验 0 错误**。
#   ⇒ 小包功能与完整包**等价**，只是首次要多下一次运行时。
#
# 什么时候需要打"完整包"（自带 CUDA、离线可用）：
#     $env:L2T_FULL_CUDA = "1"
# 这条留给"离线部署/内网"场景 —— 那种环境下用户下不了 529 MB。
_FULL_CUDA = os.environ.get("L2T_FULL_CUDA") == "1"
_CUDA_KEEP = ['cublas'] if _FULL_CUDA else []
if _FULL_CUDA:
    print('[cuda] L2T_FULL_CUDA=1：打入 cuBLAS（完整包，离线可用）')
else:
    print('[cuda] 默认形态：**不含** CUDA 运行时（按需下载，'
          '见「模型」页 →「运行组件」）；要打完整包设 L2T_FULL_CUDA=1')

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

# ⚠️⚠️ **CAM++ 的 ONNX 图要打进包**（28 MB，含结构与权重，自包含）。
#
# 为什么值得（2026-10-13 起）：它让「对唱分声部」**不再依赖 PyTorch** ——
#   * 只依赖 onnxruntime（本来就在包里）；
#   * 用户不必再下 118 MB 的 PyTorch 运行时组件；
#   * **冻结版里一直没解决的 `import torch` 崩溃问题直接绕过去了**
#     （实测：ONNX 与 torch 在真实音频上归一化后 max|Δ|=5.5e-04、
#      cos=0.999995；同一套 `tools/eval_diarization.py` 跑出 **85.0%**，
#      与历史基线逐首一致）。
#
# ⚠️ 但**不能因此就不打 `campplus_src` 与 `.bin`**：前者是 torch 后端的网络定义，
# 后者是权重（torch 后端与 ONNX 导出都从它来，也是"用户确实下过模型"的凭据）。
# 两个后端要能互相回退，见 `speaker_campplus.backend()`。
_ONNX_SRC = os.path.join('models', 'campplus', 'campplus.onnx')
if os.path.isfile(_ONNX_SRC):
    datas += [(_ONNX_SRC, 'models/campplus')]
    print(f'[models] 打入 CAM++ ONNX 图'
          f'（{os.path.getsize(_ONNX_SRC) / 1048576:.1f} MiB，免 torch 后端）')
else:
    print('[models] ⚠️ 没有 models/campplus/campplus.onnx —— '
          '分声部将要求 pyTorch 运行时组件；'
          '可用 `python tools/export_campplus_onnx.py` 生成')
# 桌面版的两份清单都必须随包带上（第一次启动就要按它们检查状态）：
#   * model_manifest.json    —— 模型权重（CAM++/Whisper/Demucs）
#   * runtime_manifest.json  —— 运行时组件（PyTorch，按需下载）
# ⚠️ 两份**不能混**：模型在 `models/`，运行时在 `<数据目录>/runtime/`，
# 状态机独立、提示文案独立（需求第 9 条：不得互相误报）。
datas += [('desktop/resources/model_manifest.json', 'desktop/resources'),
          ('desktop/resources/runtime_manifest.json', 'desktop/resources')]

# ⚠️⚠️ **AMLL 解析器要随包分发**（2026-10-15 用户实测）：
# 打包版里那行是「（AMLL 解析器检查跳过：ModuleNotFoundError）」——
# `tools/` 不在包里 ⇒ `import verify_amll_parse` 直接失败，于是
# **每次生成都跳过**「播放器里能不能显示」的检查。
# 而 AGENTS.md 铁律 #16 要求每次都必须用**真解析器**验一遍
# （自己写的校验器查不出"整行塌成一个词、播放器一片空白"）。
# 解析器本体只有 376 KB（去掉了 sourcemap / 类型定义），`node` 用户机器上有。
# 两样都打进去：脚本 + `desktop/resources/amll-parser/`。
if os.path.isdir('desktop/resources/amll-parser/node_modules'):
    datas += [('desktop/resources/amll-parser', 'desktop/resources/amll-parser')]
    print('[amll] 随包分发 AMLL 解析器（生成后做"能不能显示"检查）')
else:
    print('[amll] ⚠️ 没有 desktop/resources/amll-parser —— '
          '打包版会跳过 AMLL 解析器检查（先跑 tools/verify_amll_parse.py --setup）')
if os.path.isfile('tools/verify_amll_parse.py'):
    datas += [('tools/verify_amll_parse.py', 'tools')]
# ⚠️ 图标也要**随包分发**（不只是嵌进 exe 的资源段）：
# `desktop/app.py::_app_icon()` 会去 `_internal/desktop/resources/app.ico` 找它，
# 用来设**窗口与任务栏**图标。只嵌 exe 资源段的话，窗口图标仍然是 Qt 默认的。
if os.path.isfile('desktop/resources/app.ico'):
    datas += [('desktop/resources/app.ico', 'desktop/resources')]
    print('[icon] 随包分发 desktop/resources/app.ico（窗口/任务栏图标用）')

# ⚠️⚠️ **构建元信息**（这份产物是从哪个 commit 打的）—— 必须打进包里。
# 由 `tools/make_build_info.py` 在打包**之前**生成。
# 为什么要它（第 26–27 轮的教训）：出现过"交付物比源码旧两小时、
# 少了两个修复，而所有测试都通过"——因为测试跑的就是那个旧产物。
# 产物里带上 commit 之后：① 用户报问题能一眼看到版本来源；
# ② "产物新鲜度"能**比 commit**而不是比时间戳（时间戳会被"碰一下"骗）。
# ⚠️ 没有这个文件时**不能报错**（开发构建可能没跑生成脚本），只提示。
if os.path.isfile('desktop/resources/build_info.json'):
    datas += [('desktop/resources/build_info.json', 'desktop/resources')]
    _bi = {}
    try:
        import json as _json
        with open('desktop/resources/build_info.json', encoding='utf-8') as _fh:
            _bi = _json.load(_fh)
    except Exception:                                   # noqa: BLE001
        pass
    print(f"[build] 打入构建元信息 commit={_bi.get('git_short', '?')} "
          f"dirty={_bi.get('dirty', '?')}")
else:
    print('[build] ⚠️ 没有 desktop/resources/build_info.json —— '
          '--version 里不会有 commit；先跑 tools/make_build_info.py')
binaries = []
hiddenimports = ['doctor', 'envcheck', 'install', 'download_model',
                 # ⚠️ **动态导入的标准库要显式列**（2026-10-15）：
                 # 人声分离（vendored demucs → torch）在**运行时**才 import
                 # `timeit` / `pickletools` 之类，静态分析看不到 ⇒ 不打包 ⇒
                 # 冻结版报 `ModuleNotFoundError: No module named 'timeit'`。
                 # 这些是无害的补充（都在标准库里，几十 KB），留着不影响主路径。
                 'timeit', 'pickletools', 'copyreg', 'ctypes.util',
                 # 卸载逻辑在 uninstall_core（**故意不放在 installer_gui**：
                 # 后者开头 import tkinter，而主程序的构建排除了 tkinter）。
                 # launcher 里是延迟导入，静态分析看不到。
                 'uninstall_core', 'installer_gui',
                 # 正式分发要用：控制台附加（双击无黑窗）+ 外壳集成
                 # （开始菜单/桌面快捷方式、卸载注册表项）。
                 # 都是延迟导入，静态分析看不到。
                 'win_console', 'win_integration',
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

# =====================================================================
#  PyTorch：**不进主安装包**，改成按需下载的运行时组件
# =====================================================================
# 需求原文 §七-B 明确允许："将部分可独立部署的 GPU 运行组件作为独立运行时组件，
# 在安装或首次启动时按需安装"。
#
# 为什么可以拆（这不是"为了体积乱删"，是**依赖边界清楚 + 已实测**）：
#   * torch 只服务 **CAM++ 声纹**（对唱分声部）与 Demucs 人声分离；
#     基础歌词制作、单声部处理、TTML 编辑与导出**完全不需要**它
#     （`grep` 全仓：生产代码 import torch 的只有 `speaker_campplus.py`、
#      `separate.py`、以及 `asr/_detect_device` 与 `doctor/envcheck` 里的探测）。
#   * 权重与网络定义本来就走模型管理器下载，所以"要用再下"这个模式
#     在本项目里**已经跑了很久**，不是新发明。
#
# ⚠️ 生效前提（缺一个就会静默降级，真踩过）：
#   1. `paths.apply_env()` 必须在**任何 import torch 之前**把运行时目录插进
#      `sys.path` —— `desktop/app.py` 的启动顺序已保证；
#   2. `speaker_campplus.available()` 必须把"torch 能不能 import"算进判据，
#      否则会报成"权重缺失"（需求第 9 条明令禁止）；
#   3. 运行时装在**用户可写的数据目录**（`<data>/runtime/torch`），
#      不能进程序目录（升级会替换）也不能进 `_internal`（临时解包区）。
#
# 想打"全功能单包"（含 torch）时：设 `L2T_WITH_TORCH=1`。
WITH_TORCH = os.environ.get("L2T_WITH_TORCH") == "1"
if WITH_TORCH:
    tmp_ret = collect_all('torch')
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    print('[torch] L2T_WITH_TORCH=1 → 打进主包（全功能单包构建）')
else:
    print('[torch] 不进主包 —— 由运行时组件按需下载'
          '（`python -m desktop.services.runtime_service --install torch`）')

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
# ❌❌ **不要**把 `torch` / `torchgen` 加进 `_excludes` —— **两次实测都失败，已定论**。
#
# 问题本身是真的：构建期只要有东西 import 到 torch，PyInstaller 就把它的
# **纯 Python 部分**收进 PYZ；而 **PYZ 的导入器排在 `sys.path` 之前** ⇒
# 运行时组件里那份完整 torch（用户专门下了 118MB）**永远赢不了**：
#
#     ModuleNotFoundError: No module named 'torch._C'
#
# 但把它排除之后**更糟**：`import torch` 改走运行时那份，而它在冻结进程里
# 一加载就自爆：
#
#     AttributeError: partially initialized module 'torch' from
#       <runtime>\torch\torch\__init__.py has no attribute 'autograd'
#       (most likely due to a circular import)
#
# **两次**尝试的记录：
#   ① 第一次：直接排除 → 连普通转换都失败（当时以为是 DLL 目录没注册）
#   ② 第二次：先把 `<site>/torch/lib` 注册好（`paths.apply_runtime_path()` 的
#      包内 lib 扫描）**再**排除 → 普通转换**仍然**失败，同样那句循环导入
# ⇒ 与 DLL 搜索路径无关，是 torch 2.14.1(cp314) 与 PyInstaller 冻结环境的
#   导入顺序冲突。**别再试了**，除非有人真的去啃 torch 的 `__init__` 顺序。
#
# 实测对比（同一音频/歌词/参数）：
#   * 不排除 torch（当前做法）：转换 ✓ 91.0% 匹配率、AMLL 检查 ✓、v1/v2 ✓；
#     人声分离 ✗ 但**提示会说清具体原因**
#   * 排除 torch：**转换直接失败** —— 为了一个可选功能赌上主功能，不值
_excludes += []
# ⚠️⚠️ **demucs 依旧"不分析"，但要作为数据随包分发**（2026-10-15）。
#
# 用户实测：勾了「先做人声分离」，日志却只有一行
# 「[尾音/起音] 跳过：没开 --separate-vocals」—— 他明明勾了。
# 真因：打包版**没有 demucs**（`available()` 恒 False）→ 静默跳过分离 →
# 拖音补全也就没得做，而提示把责任推给了用户。
#
# demucs 是**纯 Python**，实测它只需要 `einops` / `julius`
# （**不需要 torchaudio / soundfile**，`separate.py` 自己写 wav），
# `torch` 由"按需运行时"提供。所以：不进 Analysis（避免顺着 import torch
# 把 2.5GB 拖进来），而是按**数据**放进 `<程序目录>/vendor/`，
# 由 `paths.apply_vendor_path()` 接上 `sys.path`。
# 实测（`.venv` 里真 import 一遍，看新加载的三方模块）：
#   demucs, einops, julius, tqdm, colorama, torch, numpy, yaml, typing_extensions
# 其中 torch 走"按需运行时"，numpy / yaml 已在包里，其余**都得随包带**。
_VENDOR_PKGS = ('demucs', 'einops', 'julius', 'colorama', 'tqdm',
                'typing_extensions')


def _pkg_dir(name: str) -> str:
    """用 find_spec 定位包目录 —— **不会真的 import**（import demucs 会拉 torch）。"""
    try:
        import importlib.util as _iu
        _spec = _iu.find_spec(name)
        for _p in list(getattr(_spec, 'submodule_search_locations', None) or []):
            if os.path.isdir(_p):
                return _p
    except Exception:                                    # noqa: BLE001
        pass
    return ''


for _v in _VENDOR_PKGS:
    _vd = _pkg_dir(_v)
    if _vd:
        datas += [(_vd, f'vendor/{_v}')]
        print(f'[vendor] 随包分发 {_v} ← {_vd}')
    else:
        print(f'[vendor] ⚠️ 打包环境里没有 {_v}（人声分离在打包版里会不可用）')

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
    # ---- torch ----
    # ⚠️⚠️ 只在**不打包 torch** 时才整棵剪掉。
    # 为什么需要这条：`excludes=['torch', ...]` **只阻止 Python 模块被 import**，
    # 而 `collect_all('torch')` 收进来的 **datas/binaries 照样会进包** ——
    # 实测：注释掉 collect_all 之后 `_internal/torch` 还有 352 MiB
    # （torch_cpu.dll 291 MiB 等），包体积一点没降。
    # 这类"改了配置但体积没变"最容易骗人，所以这里用**显式 prune**兜底。
    ("torch/", "PyTorch 运行时（改为按需下载的运行时组件）"),
    ("torchgen", "torch 代码生成器（运行时不需要）"),
    ("torch-", "torch 的 dist-info 元数据"),
    ("functorch", "torch 附属模块"),
    # ---- torch：构建期产物与测试（打包 torch 时仍要剪这些）----
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

# ⚠️ 反向条件：如果要打"全功能单包"（L2T_WITH_TORCH=1），就得把 torch 的裁剪撤掉，
# 否则会打出一个"装着 torch 文件但不完整"的包 —— 那种包最难查。
if WITH_TORCH:
    _PRUNE_RULES = [(r, w) for (r, w) in _PRUNE_RULES
                    if not (r.startswith("torch") or r.startswith("functorch"))]
    print('[torch] 已撤销 torch 相关的 prune 规则（全功能单包构建）')


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

# ⚠️⚠️ **双击不弹黑窗，但命令行/脚本照常可用** —— 用
# `console=True` + `hide_console='hide-early'`（PyInstaller ≥6.0）。
#
# 为什么**不能**用 `console=False`（第一版就是这么写的，被实测否掉了）：
# GUI 子系统进程**不继承父进程的标准句柄**，于是
#   `Lyric2TTML.exe --doctor --json > out.txt`  写出来的文件是 **0 字节**，
#   PowerShell 的 `2>&1 | ...` 也抓不到任何东西。
# 那样会把**脚本、CI、验收工具**（`tools/accept_frozen.py` 就靠捕获输出来判定）
# 全部搞坏 —— 而"命令行可用"是本项目明确要保留的能力。
#
# `hide_console` 是两全的做法：exe 仍是**控制台子系统**（句柄照常继承、
# 重定向照常工作），但**窗口在启动早期就被隐藏**（双击/快捷方式启动时不再有黑窗）。
# 四个可选值里选 `hide-early`：它是"尽早隐藏"，双击场景最干净；
# `minimize-*` 会先闪一下再最小化，仍有观感问题。
#
# ⚠️ 想临时看到控制台（排查启动阶段问题）：
#   设 `L2T_WITH_CONSOLE=1` 重新打包 —— 这时会显示窗口且不隐藏。
_WITH_CONSOLE = os.environ.get("L2T_WITH_CONSOLE") == "1"
print(f"[console] console=True, hide_console="
      f"{'None（调试：显示黑窗）' if _WITH_CONSOLE else 'hide-early（双击无黑窗）'}")

# ==========================================================================
#  应用图标 —— ⚠️ 之前**根本没有**，用的是 PyInstaller 默认图标
# ==========================================================================
# 实测（2026-10-13）：`dist/Lyric2TTML/Lyric2TTML.exe` 的图标是
# **PyInstaller 自带的那个橙色 Python + 窗口**，因为仓库里一个 `.ico` 都没有、
# 这里也没有 `icon=`。后果是桌面快捷方式、开始菜单、任务栏、
# 「设置 → 已安装的应用」里**全是 Python 图标** —— 用户会觉得
# "这是个脚本"而不是一个商业软件。
#
# 图标由 `tools/make_icon.py` 从 `assets/icon.svg` 生成多尺寸 `.ico`
# （16/24/32/48/64/128/256），配色跟随 `desktop/pages/base.py` 的调色板。
_ICON = os.path.join('assets', 'icon.ico')
if not os.path.isfile(_ICON):
    print(f'[icon] ⚠️ 没有 {_ICON} —— 会退回 PyInstaller 默认图标！'
          f'（先跑 `python tools/make_icon.py`）')
else:
    print(f'[icon] 使用 {_ICON}（{os.path.getsize(_ICON)} 字节）')

# 版本资源文件（产品名/版本号/公司名）。缺失不致命，但用户会在
# 「属性 → 详细信息」里看到一片空白。
_VERSION_FILE = os.path.join('build', 'version_info.txt')
if not os.path.isfile(_VERSION_FILE):
    print(f'[version] ⚠️ 没有 {_VERSION_FILE} —— 属性里产品名/版本号会是空的'
          f'（先跑 `python tools/make_version_info.py`）')

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
    hide_console=None if _WITH_CONSOLE else 'hide-early',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON if os.path.isfile(_ICON) else None,
    # ⚠️ 版本资源：没有它，资源管理器「属性 → 详细信息」与任务管理器里
    # **产品名/版本号全是空白**（实测过）。文件由 `tools/make_version_info.py`
    # 生成，版本号取自 `desktop/__init__.py` 的 `__version__`（唯一真源）。
    version=_VERSION_FILE if os.path.isfile(_VERSION_FILE) else None,
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
