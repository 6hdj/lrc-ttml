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
    from desktop.pages.base import apply_theme, qss

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(list(argv or sys.argv))
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)

    # ---- 主题：**必须在建窗口之前**定下来 ----
    # 配置里的 `theme` 一直存在（dark/light/auto），但**从来没人读过** ——
    # 一个"设了没用"的死字段。现在真正接上。
    _theme_name = "dark"
    # 优先级：环境变量 > 配置文件。环境变量这条路是为了
    # ① 截图/自检能稳定指定主题（否则会被存盘里的旧值盖掉）；
    # ② 技术支持让人临时换个主题排查（不用改配置）。
    _env_theme = (os.environ.get("L2T_THEME") or "").strip()
    if _env_theme:
        _theme_name = _env_theme
    else:
        try:
            # ⚠️ 用 `config.current()`（进程内单例），不要 `Config()` ——
            # 后者会重新读一遍磁盘、和上下文里的那份**不是同一个对象**，
            # 用户在设置页改了主题，这里读到的可能还是旧值。
            from desktop import config as _config_mod
            _theme_name = str(_config_mod.current().get("theme") or "dark")
        except Exception:                               # noqa: BLE001
            pass
    _theme = apply_theme(_theme_name)
    print(f"[theme] {_theme_name} -> {_theme}", flush=True)

    # ⚠️ **样式表必须在 QApplication 上设**，不能设在窗口上：
    # 设在窗口上时，弹出的顶层控件（菜单、工具提示、对话框）拿不到样式，
    # 会出现"深色界面里弹出一个白框"。
    app.setStyleSheet(qss(_theme))
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
    """程序图标（没有就用默认的，不报错）。

    ⚠️⚠️ 冻结版里**必须两个地方都找**：
      * `app_dir()`（exe 旁边）—— 用户自己换了图标可以用这个覆盖；
      * `source_dir()`（`_internal/`）—— 图标是随包分发的资源，
        打包后它在 `_internal/desktop/resources/app.ico`。
    老实现只看 `app_dir()`，而 `app.ico` 从来没被放进过安装目录 ⇒
    `QIcon(None)` ⇒ **窗口/任务栏用的是 Qt 默认图标**（真踩过）。
    `ttml_writer` 的 `index.html`、模型清单都是"两个地方都找"，
    图标漏了同一套规矩。
    """
    try:
        from PySide6.QtGui import QIcon
        from desktop import paths
        roots = []
        for fn in ("app_dir", "source_dir", "data_dir"):
            f = getattr(paths, fn, None)
            if not callable(f):
                continue
            try:
                roots.append(f())
            except Exception:                           # noqa: BLE001
                pass
        rels = ("desktop/resources/app.ico", "app.ico", "icon.ico",
                "resources/app.ico")
        for root in roots:
            for rel in rels:
                p = os.path.join(root, rel.replace("/", os.sep))
                if os.path.isfile(p):
                    ic = QIcon(p)
                    if not ic.isNull():
                        return ic
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
        # ⚠️⚠️ **带上构建来源**（第 27 轮补的缺口）：
        # 原来只打一行 "Lyric2TTML 桌面版 1.1.0" —— 用户报问题时
        # 我们**无法判断他装的是哪一次构建**（本仓真出现过"交付物比源码旧两小时"
        # 而无人察觉）。现在把 `build_info.json`（打包前由
        # `tools/make_build_info.py` 生成）里的 commit / 是否带未提交改动 打出来。
        print(f"Lyric2TTML 桌面版 {__version__}")
        try:
            import json as _json
            from desktop import paths as _paths
            for _root in _paths.resource_roots():
                _p = os.path.join(_root, "desktop", "resources",
                                  "build_info.json")
                if os.path.isfile(_p):
                    with open(_p, encoding="utf-8") as _fh:
                        _bi = _json.load(_fh)
                    print(f"  构建来源：commit {_bi.get('git_short', '?')}"
                          f"（{_bi.get('git_branch', '?')}）"
                          f"  构建于 {_bi.get('built_at', '?')}"
                          + ("　⚠️ 含未提交改动" if _bi.get("dirty") else ""))
                    break
        except Exception:                               # noqa: BLE001
            pass                                        # 元信息缺失不影响 --version
        return 0

    return run_gui(argv)


if __name__ == "__main__":
    sys.exit(main())
