"""统一日志：一个文件 + 内存环形缓冲，界面能实时看到，崩溃时能翻记录。

三件事：
1. **文件日志**（``<数据目录>/logs/desktop.log``）按大小轮转，保留 3 份。
2. **内存缓冲**（最近 N 行）给界面用，不必反复读盘。
3. **未捕获异常兜底**：``install_excepthook()`` 把 traceback 写进日志并在
   界面上弹出来。没有这一步的话，打包后的程序崩了是**完全静默**的
   （控制台窗口都没有），用户只能说"点了没反应"。

本模块**不导入 Qt**；界面通过 ``snapshot()`` 或自己的 QTimer 拉。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional

try:
    from desktop import paths
except ImportError:                                   # 单文件直接跑
    import paths                                      # type: ignore

LOGGER_NAME = "lyric2ttml"
MAX_LINES = 800
_ring: List[Dict[str, Any]] = []
_ring_lock = threading.Lock()
_configured = False
_hook_installed = False
_crash_callback: Optional[Callable[[str], None]] = None


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:                              # noqa: BLE001
            return
        import time
        with _ring_lock:
            _ring.append({"t": time.strftime("%H:%M:%S"), "level": record.levelname,
                          "m": msg})
            if len(_ring) > MAX_LINES:
                del _ring[:-MAX_LINES]


def setup(level: int = logging.INFO, log_path: Optional[str] = None) -> logging.Logger:
    """配置日志（幂等，多调几次没关系）。"""
    global _configured
    log = logging.getLogger(LOGGER_NAME)
    if _configured:
        return log
    log.setLevel(logging.DEBUG)
    log.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    path = log_path or paths.log_path()
    try:
        paths.ensure_dir(os.path.dirname(path))
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        log.addHandler(fh)
    except OSError:
        # 日志文件建不了也不能让程序起不来
        pass

    rh = _RingHandler()
    rh.setFormatter(fmt)
    log.addHandler(rh)
    _configured = True
    log.info("=" * 60)
    log.info("Lyric2TTML 桌面版启动")
    for k, v in paths.describe().items():
        log.info("  %s = %s", k, v)
    return log


def get() -> logging.Logger:
    return setup()


def snapshot(limit: int = MAX_LINES) -> List[Dict[str, Any]]:
    with _ring_lock:
        return list(_ring[-limit:])


def clear() -> None:
    with _ring_lock:
        _ring.clear()


def crash_text(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def install_excepthook(on_crash: Optional[Callable[[str], None]] = None) -> None:
    """把未捕获异常写进日志。

    ⚠️ 打包好的 GUI 程序没有控制台，不装这个钩子的话崩溃**完全无声**。
    ``on_crash`` 用来让界面弹一个"出错了，日志在哪"的框。
    """
    global _hook_installed, _crash_callback
    _crash_callback = on_crash
    if _hook_installed:
        return

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = crash_text(exc)
        try:
            get().error("未捕获异常：\n%s", text)
        except Exception:                              # noqa: BLE001
            pass
        try:
            with open(os.path.join(paths.logs_dir(), "crash.log"),
                      "a", encoding="utf-8") as fh:
                import time
                fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}")
        except OSError:
            pass
        if _crash_callback:
            try:
                _crash_callback(text)
            except Exception:                          # noqa: BLE001
                pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
    # 子线程里的异常（QThread 之外的普通线程）默认只打印到 stderr，
    # 打包后看不见 → 也接进来
    def _thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        text = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback))
        try:
            get().error("线程 %s 未捕获异常：\n%s", args.thread.name if args.thread else "?", text)
        except Exception:                              # noqa: BLE001
            pass
        if _crash_callback:
            try:
                _crash_callback(text)
            except Exception:                          # noqa: BLE001
                pass

    try:
        threading.excepthook = _thread_hook            # type: ignore[assignment]
    except Exception:                                  # noqa: BLE001
        pass
    _hook_installed = True


def log_dir_hint() -> str:
    return paths.logs_dir()
