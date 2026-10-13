"""正式分发所需的控制台 / 窗口行为处理。

    from win_console import attach_parent_console
    attach_parent_console()            # 命令行入口处调一次

## 要解决的问题

主程序是**同一个 exe**，既要能当窗口程序用（双击 → 出界面），
又要能当命令行工具用（`Lyric2TTML.exe --doctor` / `--check-only ...`）。

PyInstaller 的 `console=` 只能二选一：
* `console=True`  → 双击时**弹一个黑色控制台窗口**（用户明确说不要这个）；
* `console=False` → exe 是 GUI 子系统，**从命令行跑时看不到任何输出**
  （`--doctor`、`--version` 的结果全丢了）。

## 做法

用 `console=False` 打包（双击没有黑窗），然后在入口处**手工附加到父控制台**：
如果本进程是被人从 cmd/PowerShell 里启动的（`ATTACH_PARENT_PROCESS`），
就把父控制台接上，于是命令行输出照常可见；双击启动时没有可附加的父控制台，
`AttachConsole` 失败、什么也不做 —— 也就没有黑窗。

⚠️ 附加成功之后还要**重绑 `sys.stdout/stderr`**：Python 在启动时已经按
"没有控制台"初始化过它们（GUI 子系统下 `sys.stdout` 是 `None`），
不重绑的话 `print()` 仍然什么都不输出。

⚠️ 另外要**抑制 Windows 在退出时给 exe 多等几秒**的行为：
GUI 子系统程序从命令行启动时，cmd **不会**等它结束就返回提示符。
这不是 bug 但会让 `--doctor` 的结果和提示符错位；所以附加成功后
我们就是控制台程序了，cmd 会正常等待。
"""
from __future__ import annotations

import os
import sys

_ATTACH_PARENT_PROCESS = -1            # Windows API 常量


def attach_parent_console() -> bool:
    """尝试附加到父进程的控制台。返回是否成功（失败是**正常情况**）。

    非 Windows / 非冻结环境直接返回 False（源码模式本来就带控制台）。
    """
    if os.name != "nt":
        return False
    if not getattr(sys, "frozen", False):
        return False                       # 源码模式：python.exe 自带控制台
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # ⚠️ 返回 0 表示"没有可附加的父控制台" —— 双击启动时就是这样，
        # **这是正常路径**，不是错误，所以不抛异常、不打印。
        if not kernel32.AttachConsole(_ATTACH_PARENT_PROCESS):
            return False
        _rebind_std_streams()
        return True
    except Exception:                       # noqa: BLE001
        return False


def _rebind_std_streams() -> None:
    """把 sys.stdout/stderr/stdin 重新绑到（刚附加上的）控制台。

    ⚠️ GUI 子系统下 Python 启动时 `sys.stdout` 是 `None`，
    不重绑的话 `print()` 静默丢弃 —— 这正是"打包版 `--doctor` 什么都不输出"
    的原因（真踩过）。
    """
    for name, stream, mode in (("stdout", "CONOUT$", "w"),
                               ("stderr", "CONOUT$", "w"),
                               ("stdin", "CONIN$", "r")):
        cur = getattr(sys, name, None)
        if cur is not None:
            continue                        # 已经有流了，别动它
        try:
            fh = open(stream, mode, encoding="utf-8", errors="replace",
                      buffering=1 if mode == "w" else -1)
            setattr(sys, name, fh)
        except OSError:
            pass


def ensure_console_if_cli(argv: list) -> bool:
    """CLI 入口调用：**只有确实是命令行用法**时才附加控制台。

    这样把"双击启动"和"命令行启动"彻底分开：
    * 双击（无参数，或只有界面开关）→ 不附加 → 没有黑窗；
    * `--doctor` / `--version` / `--check-only ...` → 附加 → 输出可见。
    """
    ui_flags = ("--web", "--gui", "--server", "--uicheck", "-uicheck",
                "--selftest", "-selftest", "--model-cli", "--runtime-cli",
                "--uninstall", "-uninstall")
    if not argv or any(a in ui_flags for a in argv):
        return False
    return attach_parent_console()
