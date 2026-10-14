"""打包版（PyInstaller 冻结）的统一入口。

双击 exe = 起**桌面窗口**（装不上 Qt 时退回 Web 界面）；带参数 = 走命令行。
两条路都复用原有模块，不复制任何逻辑。

为什么要单独一个入口，而不是直接打包 ``main.py``：
``web_server`` 用 ``HERE = dirname(abspath(__file__))`` 定位 ``index.html``
和 ``uploads/``、``outputs/``。**冻结之后 `__file__` 指向包内部**
（PyInstaller 的 ``_internal``），往那儿写转换结果是错的 ——
临时目录会被清理、Program Files 下还可能是只读的。
所以这里把几个模块级路径**重定向到 exe 所在目录**（那里才是用户的数据目录）。

    Lyric2TTML.exe                    → 桌面窗口（没有 PySide6 就用浏览器界面）
    Lyric2TTML.exe --web --port 9000  → 直接起 Web 界面
    Lyric2TTML.exe --audio a.wav --lyrics b.lrc -o out.ttml   → 命令行转换
    Lyric2TTML.exe --doctor           → 环境体检
    Lyric2TTML.exe --help             → 命令行全部参数
"""
from __future__ import annotations

import os
import sys
import webbrowser


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def _base_dir() -> str:
    """用户数据目录：冻结时 = exe 所在目录；未冻结时 = 本文件所在目录。"""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _rehome() -> str:
    """把"数据/资源目录"从包内部改到 exe 旁边。返回该目录。"""
    base = _base_dir()
    os.chdir(base)

    # ⚠️⚠️ **运行时组件（PyTorch 等）的 sys.path 必须在这里插**，而且
    # **不分冻结 / 源码**：
    # 从 2026-10-13 起 PyTorch 不进主安装包，改成"用户按需下载到数据目录"的
    # 运行时组件。`speaker_campplus` 里是**延迟** `import torch`，所以只要在
    # 任何一次 `import torch` 之前把目录加进 `sys.path` 就可以。
    #
    # 只放在 `desktop/app.py` 里是不够的 —— 那条路只在**开窗口**时走，
    # 而 `launcher.py --audio ... ` 这种**命令行转换**根本不经过它，
    # 结果就是"装了运行时却仍然报缺 torch"（真踩过：必须两条路都覆盖）。
    try:
        sys.path.insert(0, base)
        try:
            from desktop import paths as _paths
            _paths.apply_runtime_path()
        finally:
            if base in sys.path:
                sys.path.remove(base)
    except Exception:                                   # noqa: BLE001
        pass

    if not _frozen():
        return base

    # ⚠️ **必须在 import 其它模块之前**设好模型缓存目录。
    # main.py / web_server.py / asr.py 里都是 `os.environ.setdefault(
    # "HUGGINGFACE_HUB_CACHE", os.path.join(HERE, ".hf_cache"))`，而冻结后
    # HERE 指向 _internal —— 于是 3GB 的模型会被下到**包内部**：
    # 升级/重装就丢，用户也根本找不到。放在 exe 旁边才是对的。
    #
    # ⚠️⚠️ **但这行 `setdefault` 会把"用户指定的缓存"吃掉**（真踩到）：
    # 程序识别两种写法 —— `HUGGINGFACE_HUB_CACHE`（HF 官方）和
    # `L2T_HF_CACHE`（本项目别名，桌面版/`paths.hf_cache_dir()` 认它）。
    # 老代码只 `setdefault` 官方那个，于是**只要用户传的是 `L2T_HF_CACHE`，
    # 这里就抢先把它写死成 `<exe>/.hf_cache`**，后面 `paths.hf_cache_dir()`
    # 再想纠正也晚了（asr 已经读到了错误的目录）——
    # 表现是：**明明本地有 3GB 模型，程序却重新联网下载**。
    # 实测（`HUGGINGFACE_HUB_CACHE` 生效、`L2T_HF_CACHE` 被忽略，
    # 下载一直跑不完）确认就是这个原因。
    #
    # 现在：**三个来源都认**，而且优先级与 `desktop.paths.hf_cache_dir()`
    # 保持一致（显式指定 > 程序目录旁边的旧缓存 > 默认位置）。
    hub = (os.environ.get("HUGGINGFACE_HUB_CACHE")
           or os.environ.get("L2T_HF_CACHE")
           or os.path.join(base, ".hf_cache"))
    os.environ["HUGGINGFACE_HUB_CACHE"] = hub      # 覆盖，不用 setdefault
    # 顺手把别名也设上，让 `paths.hf_cache_dir()` 看到的是同一个答案
    os.environ.setdefault("L2T_HF_CACHE", hub)
    try:
        os.makedirs(hub, exist_ok=True)
    except OSError:
        pass

    # web_server：HERE 决定 index.html 去哪找，UPLOAD_DIR/OUTPUT_DIR 决定写到哪
    import web_server
    web_server.HERE = base
    web_server.UPLOAD_DIR = os.path.join(base, "uploads")
    web_server.OUTPUT_DIR = os.path.join(base, "outputs")
    os.makedirs(web_server.UPLOAD_DIR, exist_ok=True)
    os.makedirs(web_server.OUTPUT_DIR, exist_ok=True)

    # 声纹/分离的权重目录（models/）也放在 exe 旁边，方便用户自己放模型
    for modname in ("speaker_campplus", "separate", "diarize", "asr"):
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        for attr in ("_HERE", "HERE", "BASE_DIR", "_BASE_DIR", "DEFAULT_CACHE_DIR"):
            if hasattr(mod, attr):
                setattr(mod, attr, base if attr != "DEFAULT_CACHE_DIR" else hub)
    return base


def _banner(base: str) -> None:
    print("=" * 62)
    print("  Lyric2TTML — 歌词 + 音频 → Apple Music / AMLL 逐字 TTML")
    print("=" * 62)
    print(f"  工作目录：{base}")
    print("  uploads/ 与 outputs/ 都在这个目录下（转换结果就在 outputs/）")


def _is_elevated() -> bool:
    """当前进程是不是以管理员身份运行"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _open_browser(url: str) -> None:
    """打开浏览器。

    ⚠️⚠️ **不要直接用 `webbrowser.open`**。如果程序是以**管理员身份**运行的，
    它拉起的浏览器也是管理员权限；等用户以后再以普通权限启动时，
    Edge 会弹：

        "Microsoft Edge 未响应，因为现有实例正在以提升的权限运行。
         是否要用普通权限重启现有实例？"

    用户看到的就是"浏览器报错、打不开界面"（真踩过）。
    `explorer.exe <url>` 是**始终以普通用户权限**运行的（Explorer 自己就是），
    所以不管本程序是不是管理员，浏览器都能正常打开。
    """
    import subprocess
    import webbrowser

    if os.name == "nt":
        try:
            # Explorer 转发 URL：非提升、且复用已有浏览器窗口
            subprocess.Popen(["explorer.exe", url], close_fds=True)
            return
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _start_web(base: str, port: int) -> int:
    """起本地 Web 界面（桌面版不可用时的退路，也是 `--web` 的实现）。"""
    import web_server
    for i, a in enumerate(sys.argv[1:]):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    os.environ["L2T_PORT"] = str(port)
    _banner(base)
    url = f"http://127.0.0.1:{port}"
    print(f"  界面地址：{url}")
    print("  关掉这个窗口即可退出服务")
    print("=" * 62)

    # 等服务起来再开浏览器（省得用户看到"无法访问"）
    try:
        import threading
        import time
        import urllib.request

        def _open():
            for _ in range(60):
                try:
                    urllib.request.urlopen(url, timeout=1).read(64)
                    _open_browser(url)
                    return
                except Exception:
                    time.sleep(0.5)
        threading.Thread(target=_open, daemon=True).start()
    except Exception:
        pass

    web_server.main()
    return 0


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except Exception:                                    # noqa: BLE001
        return False


def main() -> int:
    argv = sys.argv[1:]
    # ⚠️⚠️ **先把控制台接上（如果是从命令行启动的）**，再打印任何东西。
    # 打包版是 `console=False`（双击时不出黑窗），于是从 cmd/PowerShell 跑
    # `--doctor` / `--version` 时**默认什么都看不到**；这里附加到父控制台并重绑
    # sys.stdout。双击启动时没有父控制台可附加 —— 正是我们要的（没有黑窗）。
    # ⚠️ 必须在 `_rehome()` 之前：`_rehome()` 里可能已经要打印提示了。
    try:
        import win_console
        win_console.ensure_console_if_cli(argv)
    except Exception:                                   # noqa: BLE001
        pass
    base = _rehome()

    # 以管理员运行会带来一连串麻烦（浏览器权限冲突、文件属主变成管理员导致
    # 以后普通权限改不动）。不需要管理员，所以明确提醒一句。
    if os.name == "nt" and _is_elevated() and "--quiet" not in argv:
        print("[提示] 你正在以**管理员身份**运行。本程序不需要管理员权限，"
              "建议关掉后用普通方式双击 启动.bat。\n"
              "       （管理员运行时，浏览器可能弹"
              "「现有实例正在以提升的权限运行」）")

    # ⚠️ 先进 UTF-8 控制台模式再说话：冻结进程的 stdout 默认走控制台代码页
    # （简体中文 = GBK），而我们输出里有 ⚠️ 这类不在 GBK 里的字符，
    # 不处理就是乱码甚至直接 UnicodeEncodeError。--doctor 这种纯文本输出尤其明显。
    try:
        import main as _m
        _m.ensure_utf8_console()
    except Exception:
        pass

    # 环境体检 / 安装补全 / 下载模型：这些是"安装期"命令，直接转发，不走 argparse
    #
    # ⚠️⚠️ 顺序有讲究：`--runtime-cli` / `--model-cli` 这类**子命令入口**必须先匹配。
    # 它们自己的参数里也可能出现裸词（比如 `--install torch`），
    # 而下面的 `install` 判断会先命中、把整串转给 install.py ——
    # 实测报 `install.py: error: unrecognized arguments: --runtime-cli torch`。
    # 所以下面判断裸词时要把"子命令入口"排除掉。
    _SUBCOMMAND_FLAGS = ("--runtime-cli", "--model-cli")
    if any(a in ("--doctor", "-doctor", "doctor") for a in argv):
        import doctor
        return doctor.main()
    if "--runtime-cli" in argv:
        from desktop.services import runtime_service
        return int(runtime_service._cli(
            [a for a in argv if a != "--runtime-cli"]))
    _bare_install = any(a in ("--install", "-install", "install") for a in argv) \
        and not any(a in _SUBCOMMAND_FLAGS for a in argv)
    if _bare_install:
        import install
        rest = [a for a in argv if a not in ("--install", "-install", "install")]
        sys.argv = ["install.py"] + rest
        return install.main()
    # 卸载：`卸载程序.bat` 会调 `Lyric2TTML.exe --uninstall <目录>`
    # ⚠️ 逻辑在 `uninstall_core`（**不是** installer_gui）：installer_gui 开头
    # `import tkinter`，而主程序的构建特意排除了 tkinter → 会
    # `ModuleNotFoundError: No module named 'tkinter'`（E2E 抓到过）。
    # 另外"搬 3GB 模型"必须由能验证结果的代码来做 —— .bat 里的
    # xcopy / robocopy 在"含中文的路径 + 目标子目录还不存在"时会**报成功却没搬**
    # （详见 uninstall_core.move_tree 的说明）。
    for _uflag in ("--uninstall", "-uninstall"):
        if _uflag in argv:
            import uninstall_core
            return int(uninstall_core.uninstall_cli(argv[argv.index(_uflag) + 1:]))
    if any(a in ("--download-model", "download-model") for a in argv):
        import download_model
        # 复用它的参数（--model small 之类）
        rest = [a for a in argv if a not in ("--download-model", "download-model")]
        sys.argv = ["download_model.py"] + rest
        return download_model.main()
    # 运行时组件（PyTorch 等）：`--runtime-cli --status/--install/--remove`
    # ⚠️ 光把它加进下面的 `_UI_FLAGS` **不够** —— 那只是"别转发给 main.py"，
    # 没有任何地方真正处理它，于是会掉进 `cli_like` 被当成普通参数
    # （实测报 `install.py: error: unrecognized arguments: --runtime-cli`）。
    # 需求第 10 条要求"在冻结版 EXE 中验证运行时下载"，所以要真的路由。
    if "--runtime-cli" in argv:
        from desktop.services import runtime_service
        return int(runtime_service._cli(
            [a for a in argv if a != "--runtime-cli"]))

    # ⚠️⚠️ **`--launch-check` / `--doctor` 也必须真的路由**（2026-10-14 踩到）：
    # 我照着 `--runtime-cli` 的注释把它们加进了 `_UI_FLAGS`，
    # **但漏了"加进白名单 ≠ 有人处理"这后半句** —— 于是
    # `launcher.py --launch-check` 不进 main.py 的 argparse，
    # 而是**直接掉到"默认开桌面窗口"**：安装程序去读 JSON 读不到，
    # 会一直挂到 180 秒超时、把可用环境判成不可用。
    # （实测：跑 `python launcher.py --launch-check` 会一直不退出，
    #  等了 10 分钟还在跑 —— 因为它开了个 GUI 窗口。）
    if any(a in ("--launch-check", "--doctor") for a in argv):
        from desktop import app as _dapp
        return int(_dapp.main(argv))

    # 走命令行：有参数、且不是界面/桌面版专用的那些参数
    #
    # ⚠️ 这个判断很容易漏：`--uicheck` / `--selftest` / `--version` 这些
    # **桌面版自己的开关**如果没列进来，就会被当成"普通参数"转发给
    # `main.py` 的 argparse —— 结果是 `error: unrecognized arguments: --uicheck`
    # 退出码 2（验收脚本就抓到过：打包版跑 `--uicheck` 直接失败，
    # 而它的作用是**验证 Qt 平台插件在不在**，最容易坏的就是它）。
    _UI_FLAGS = ("--web", "--gui", "--server", "--uicheck", "-uicheck",
                 "--selftest", "-selftest", "--model-cli", "--runtime-cli",
                 # ⚠️ `--launch-check`（2026-10-14 加）：安装程序用的
                 # "能不能打开"检查。**必须列进来** —— 否则被转发给
                 # `main.py` 的 argparse，报 `unrecognized arguments`，
                 # 安装程序读不到 JSON 就会把可用环境判成不可用。
                 "--launch-check", "--doctor",
                 "--uninstall", "-uninstall",
                 "--version", "-V")
    cli_like = bool(argv) and not any(a in _UI_FLAGS for a in argv)
    if cli_like:
        import main as cli
        return cli.main(argv)

    # ---- 默认：桌面窗口（装不上 Qt 就退回浏览器界面）----
    # ⚠️ 用环境变量 ``L2T_FORCE_WEB=1`` 可以强制走浏览器界面，
    # 排查"是桌面壳的问题还是后端的问题"时很有用。
    force_web = os.environ.get("L2T_FORCE_WEB") == "1"
    if not force_web and _qt_available():
        try:
            # `--web` 是"我就要浏览器界面"的明确要求，不要再套桌面壳
            if any(a in ("--web", "--gui", "--server") for a in argv):
                raise _UserWantsWeb()
            _banner(base)
            print("  正在打开桌面窗口…")
            print("=" * 62)
            from desktop.app import main as desktop_main
            # ⚠️ 把 argv 传下去：`--uicheck` / `--selftest` / `--doctor` /
            # `--version` 这些桌面版自己的开关要能被它处理
            return int(desktop_main(argv))
        except _UserWantsWeb:
            pass
        except Exception as e:                           # noqa: BLE001
            # ⚠️ 桌面壳起不来**绝不能**变成"双击没反应"：
            # 打一行人能看懂的日志，然后退回浏览器界面，功能照样能用。
            import traceback
            print(f"[警告] 桌面窗口启动失败（{type(e).__name__}: {e}），"
                  f"改用浏览器界面。")
            traceback.print_exc()
            try:
                from desktop.services import log_service
                log_service.setup().error("桌面窗口启动失败：\n%s",
                                          traceback.format_exc())
            except Exception:                            # noqa: BLE001
                pass

    import web_server
    port = int(os.environ.get("L2T_PORT", "8765"))
    return _start_web(base, port)


class _UserWantsWeb(Exception):
    """内部用：用户显式要 `--web`，跳过桌面壳。"""


if __name__ == "__main__":
    sys.exit(main())
