"""桌面应用入口：``python -m desktop``。

启动顺序**不能乱**（每一句都在踩过的坑前面）：

1. ``log_service.setup()`` —— 先有日志，后面出什么事都能查。
2. ``log_service.install_excepthook()`` —— 打包后没有控制台，
   不装钩子的话崩溃是**完全静默**的（用户只能说"点了没反应"）。
3. ``paths.apply_env()`` + ``backend_service.prepare_environment()``
   —— **必须在任何 import torch / asr 之前**定好模型缓存与 CUDA DLL 路径。
4. 单实例检查 —— 两个实例同时跑会争同一个 ``outputs/`` 与模型缓存。
5. ``QApplication`` → 主窗口。

``--doctor`` / ``--selftest`` / ``--uicheck`` 走无界面分支，
方便在服务器/CI 上验证（不需要显示设备）。
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

from desktop import __version__


def _early_env() -> None:
    """处理必须在导入重模块之前完成的事情。"""
    # 仓库根目录要在 sys.path 上，才能 import pipeline / asr / web_server
    from desktop import paths
    for p in (paths.app_dir(), paths._source_dir()):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    paths.apply_env()


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except Exception:                                  # noqa: BLE001
        return False


_QT_MISSING_HELP = """\
运行桌面版需要 PySide6（Qt 的 Python 绑定）。

  源码运行：
      pip install -r requirements-desktop.txt

  普通用户：
      用官方安装包（安装包里已经带好了，不需要你自己装）。

如果你只想用原来的浏览器界面，可以运行：
      python web_server.py
"""


def _single_instance(force: bool = False) -> bool:
    """抢一个"只有一个实例"的锁。返回 True = 该继续启动。

    ⚠️ 用**文件锁**而不是 ``QLocalServer``：Qt 版本/平台差异会让后者
    在个别机器上直接崩，而这里的诉求只是"别开两个"。
    锁文件放数据目录，**故意不删**（删了就有竞态：A 刚删、B 刚拿到句柄）。
    """
    if force or os.environ.get("L2T_ALLOW_MULTI"):
        return True
    from desktop import paths
    lock = os.path.join(paths.data_dir(), "app.lock")
    try:
        paths.ensure_dir(os.path.dirname(lock))
        fh = open(lock, "a+", encoding="utf-8")        # noqa: SIM115
        try:
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        # 句柄必须活着才有锁，挂到模块上别让它被回收
        globals()["_LOCK_FH"] = fh
        return True
    except OSError:
        return True                                    # 锁不了就别拦用户


def run_gui(argv: Optional[List[str]] = None) -> int:
    """启动主窗口。返回进程退出码。"""
    _early_env()
    from desktop.services import log_service
    log = log_service.setup()
    log.info("desktop %s 启动（frozen=%s）", __version__,
             getattr(sys, "frozen", False))

    if not _qt_available():
        print(_QT_MISSING_HELP, file=sys.stderr)
        log.error("缺少 PySide6，无法启动桌面窗口")
        return 3

    if not _single_instance():
        # 已经开着一个了：提示一句就退出，不要弹一堆错
        print("Lyric2TTML 已经在运行了（看看任务栏）。")
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.information(None, "Lyric2TTML",
                                    "Lyric2TTML 已经在运行了。\n"
                                    "请在任务栏里找到它的窗口。")
            del app
        except Exception:                              # noqa: BLE001
            pass
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from desktop import APP_NAME, ORG_NAME
    from desktop.main_window import MainWindow
    from desktop.pages.base import QSS

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(list(argv or sys.argv))
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    # ⚠️ **样式表必须在 QApplication 上设**，不能设在窗口上：
    # 设在窗口上时，弹出的顶层控件（菜单、工具提示、对话框）拿不到样式，
    # 会出现"深色界面里弹出一个白框"。
    app.setStyleSheet(QSS)
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    def _on_crash(text: str) -> None:
        """崩溃时给用户一个"知道出事了、日志在哪"的框（而不是静默消失）。"""
        try:
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Lyric2TTML 出错了")
            box.setText("程序遇到了一个没预料到的错误。")
            box.setInformativeText(
                f"日志已经写到：\n{log_service.paths.log_path()}\n\n"
                "把这个文件发给开发者就能定位问题。")
            box.setDetailedText(text[-4000:])
            box.exec()
        except Exception:                              # noqa: BLE001
            pass

    log_service.install_excepthook(_on_crash)

    win = MainWindow()
    win.show()
    log.info("主窗口已显示")
    code = app.exec()
    log.info("退出，code=%s", code)
    return int(code)


def _app_icon():
    """程序图标（没有就用默认的，不报错）。"""
    try:
        from PySide6.QtGui import QIcon
        from desktop import paths
        for cand in ("desktop/resources/app.ico", "app.ico", "icon.ico"):
            p = os.path.join(paths.app_dir(), cand)
            if os.path.isfile(p):
                return QIcon(p)
    except Exception:                                  # noqa: BLE001
        pass
    return None


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])

    # ---- 无界面分支（CI / 远程 shell / 打包版自检都能用）----
    if any(a in ("--doctor", "-doctor") for a in argv):
        _early_env()
        import doctor
        return int(doctor.main())
    if any(a in ("--selftest", "-selftest") for a in argv):
        # ⚠️ 用 import 而不是 runpy.run_path：冻结之后脚本躺在 `_internal` 里，
        # runpy 去找它的 __main__ 会失败（真踩过）
        _early_env()
        from desktop import selftest
        rest = [a for a in argv if a not in ("--selftest", "-selftest")]
        return int(selftest.main(rest))
    if any(a in ("--uicheck", "-uicheck") for a in argv):
        _early_env()
        from desktop import uicheck
        rest = [a for a in argv if a not in ("--uicheck", "-uicheck")]
        return int(uicheck.main(rest))
    if any(a in ("--model-cli",) for a in argv):
        _early_env()
        from desktop.services import model_manager
        rest = [a for a in argv if a != "--model-cli"]
        return int(model_manager._cli(rest))
    # 运行时组件（PyTorch 等）的 CLI：`--runtime-cli --status/--install/--remove`
    # 需求第 10 条要求"在冻结版 EXE 中实际验证……运行时下载"，
    # 这就是那个可自动化的入口（`tools/verify_frozen_runtime.py` 用它）。
    if any(a in ("--runtime-cli",) for a in argv):
        _early_env()
        from desktop.services import runtime_service
        rest = [a for a in argv if a != "--runtime-cli"]
        return int(runtime_service._cli(rest))
    if any(a in ("--version", "-V") for a in argv):
        print(f"Lyric2TTML 桌面版 {__version__}")
        return 0

    return run_gui(argv)


if __name__ == "__main__":
    sys.exit(main())
