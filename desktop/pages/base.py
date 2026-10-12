"""页面公共设施：配色、小控件、以及"页面"的公共父类。

**不要每个页面各写一套卡片/标题样式** —— 三个页面各写一套的结果是
边框粗细、圆角、间距全不一样，看着像三个软件拼起来的。

颜色只有一个来源（``C`` 字典）。改主题就改这里。
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

# --------------------------------------------------------------- 配色（唯一来源）
C = {
    "bg": "#14161a",
    "panel": "#1b1e24",
    "panel2": "#22262e",
    "line": "#2c323c",
    "text": "#e8eaee",
    "dim": "#9aa3b0",
    "faint": "#6b7480",
    "accent": "#4c9aff",
    "accent_dim": "#2b5f9e",
    "ok": "#3ecf8e",
    "warn": "#e2b53e",
    "err": "#ef5350",
    "cyan": "#22d3ee",
}

QSS = f"""
QWidget {{
    background: {C['bg']};
    color: {C['text']};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QLabel#h1 {{ font-size: 20px; font-weight: 600; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#dim {{ color: {C['dim']}; }}
QLabel#faint {{ color: {C['faint']}; font-size: 12px; }}
QLabel#mono {{ font-family: Consolas, "Courier New", monospace; font-size: 12px; }}

QFrame#card {{
    background: {C['panel']};
    border: 1px solid {C['line']};
    border-radius: 8px;
}}
QFrame#cardFlat {{
    background: {C['panel2']};
    border: 1px solid {C['line']};
    border-radius: 6px;
}}

QPushButton {{
    background: {C['panel2']};
    border: 1px solid {C['line']};
    border-radius: 6px;
    padding: 6px 14px;
    color: {C['text']};
}}
QPushButton:hover {{ background: #2a3038; border-color: #3a4250; }}
/* ⚠️ 这几个 `:disabled` 规则必须**显式**写：
   QSS 里 ID 选择器（`#primary`）的优先级高于 `:disabled` 伪类，
   所以不写 `#primary:disabled` 的话，禁用按钮会保留**启用态**的文字色 ——
   实测就是"深蓝底 + 深色字"，按钮上的字几乎看不见（截图里一眼能看到）。 */
QPushButton:disabled {{ color: {C['faint']}; background: #1e2127; border-color: #262a31; }}
QPushButton#primary {{
    background: {C['accent']};
    border: 1px solid {C['accent']};
    /* 深色文字压在亮蓝上，对比度够但**中文字形细、11px 下显得糊**
       （截图里"开始制作""下载识别模型"都偏虚）。改成白字更清楚，
       也是这类"主操作按钮"的通行做法。 */
    color: #ffffff;
    font-weight: 600;
    padding: 9px 22px;
}}
QPushButton#primary:hover {{ background: #63a9ff; color: #ffffff; }}
QPushButton#primary:disabled {{
    background: #24313f; border: 1px solid #2c3a4a; color: #93a1b1;
}}
QPushButton#nav {{
    background: transparent; border: none; border-radius: 6px;
    padding: 9px 12px; text-align: left; color: {C['dim']};
}}
QPushButton#nav:hover {{ background: {C['panel2']}; color: {C['text']}; }}
QPushButton#nav:checked {{
    background: {C['panel2']}; color: {C['text']};
    border-left: 3px solid {C['accent']};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {C['panel2']};
    border: 1px solid {C['line']};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {C['accent_dim']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {C['accent']};
}}
QComboBox QAbstractItemView {{
    background: {C['panel2']}; border: 1px solid {C['line']};
    selection-background-color: {C['accent_dim']};
}}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}

QProgressBar {{
    background: {C['panel2']}; border: 1px solid {C['line']};
    border-radius: 5px; height: 12px; text-align: center; color: {C['text']};
}}
QProgressBar::chunk {{ background: {C['accent']}; border-radius: 4px; }}

QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3a4250; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a5464; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #3a4250; border-radius: 5px; min-width: 30px; }}

QTableWidget, QListWidget {{
    background: {C['panel']}; border: 1px solid {C['line']};
    border-radius: 6px; gridline-color: {C['line']};
}}
QHeaderView::section {{
    background: {C['panel2']}; border: none; border-bottom: 1px solid {C['line']};
    padding: 6px 8px; color: {C['dim']};
}}
QSplitter::handle {{ background: {C['line']}; }}
QStatusBar {{ background: {C['panel']}; border-top: 1px solid {C['line']}; }}
QToolTip {{
    background: {C['panel2']}; color: {C['text']};
    border: 1px solid {C['line']}; padding: 4px 6px;
}}
"""

# 状态 → 颜色（任务 / 模型 / 体检三处共用一套，避免"同一个状态两种颜色"）
STATUS_COLOR = {
    "queued": C["dim"],
    "running": C["accent"],
    "verifying": C["cyan"],
    "done": C["ok"],
    "ready": C["ok"],
    "ok": C["ok"],
    "failed": C["err"],
    "error": C["err"],
    "cancelled": C["warn"],
    "warn": C["warn"],
    "not_installed": C["dim"],
    "downloading": C["accent"],
    "info": C["dim"],
}

STATUS_ICON = {
    "queued": "…", "running": "▶", "verifying": "⟳", "done": "✓",
    "ready": "✓", "failed": "✗", "error": "✗", "cancelled": "⊘",
    "not_installed": "·", "warn": "!", "ok": "✓", "info": "·",
    "downloading": "↓",
}


def card(*, flat: bool = False) -> QFrame:
    f = QFrame()
    f.setObjectName("cardFlat" if flat else "card")
    return f


def label(text: str = "", *, kind: str = "", wrap: bool = False,
          color: Optional[str] = None) -> QLabel:
    lb = QLabel(text)
    if kind:
        lb.setObjectName(kind)
    if wrap:
        lb.setWordWrap(True)
    if color:
        lb.setStyleSheet(f"color: {color};")
    return lb


def h1(text: str = "") -> QLabel:
    return label(text, kind="h1")


def h2(text: str = "") -> QLabel:
    return label(text, kind="h2")


def dim(text: str = "") -> QLabel:
    return label(text, kind="dim")


def hint(text: str = "", wrap: bool = True) -> QLabel:
    lb = label(text, kind="faint", wrap=wrap)
    return lb


def button(text: str, *, primary: bool = False, on_click=None,
           tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(on_click)
    return b


def row(*widgets, spacing: int = 8, stretch_index: int = -2) -> QWidget:
    """水平排一行。

    ``stretch_index`` 指定哪一个控件吃掉多余宽度（默认**倒数第二个**，
    也就是"输入框 + 后面的按钮"这种最常见的组合里那个输入框）。
    传 None 表示**谁都不拉伸**（按钮行必须这样，否则主按钮会被拉成一条长条，
    看起来像坏掉的进度条）。

    ``None`` 这个位置参数表示一个弹性空隙（``addStretch``）。
    """
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    items = list(widgets)
    if stretch_index is not None and stretch_index < 0:
        stretch_index = max(0, len(items) + stretch_index)
    for i, x in enumerate(items):
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, QWidget):
            lay.addWidget(x, 1 if (stretch_index is not None and i == stretch_index)
                          else 0)
        else:
            lay.addStretch(int(x))
    return w


def col(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, QWidget):
            lay.addWidget(x)
        elif isinstance(x, int):
            lay.addSpacing(x)
    return w


def status_dot(state: str, text: str = "") -> QLabel:
    color = STATUS_COLOR.get(state, C["dim"])
    icon = STATUS_ICON.get(state, "·")
    return label(f"{icon} {text}".strip(), color=color)


class Page(QWidget):
    """页面基类。

    约定（**界面层唯一的契约**）：
    * ``TITLE`` / ``SUBTITLE`` 给导航和标题用；
    * ``build()`` 里造控件，只造一次；
    * ``refresh()`` 从服务层拉最新状态并更新控件；
    * ``on_show()`` 每次切到本页时调（默认转调 ``refresh()``）；
    * ``on_task(t)`` 收到任务变更时调（默认什么都不做）。

    ⚠️ **子类不要漏实现 refresh()** —— 这样"切到这一页"和
    "收到任务通知"走同一条刷新路径，不会出现"任务跑完了页面还是旧的"。
    """

    TITLE = "页面"
    SUBTITLE = ""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._built = False

    # ---------------------------------------------------------------- 构建
    def scrolled(self, inner: QWidget) -> QScrollArea:
        """把内容放进滚动区（窗口矮的时候不能把控件挤没）。"""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        sa.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return sa

    def build(self) -> QWidget:
        """构造并返回本页的根控件。子类实现。"""
        raise NotImplementedError

    def ensure_built(self) -> QWidget:
        """构建一次并**挂到本页上**。

        ⚠️⚠️ 这里有两个很贵的坑（都是真踩到的）：

        1. ``build()`` 返回的控件如果只是存进 ``self._root`` 而没有加进页面
           布局，页面就是**一片空白** —— 控件都在、属性都对，
           ``isVisible()`` 却是 False。所有"属性断言"式的检查都发现不了它，
           必须**渲染一次截图**才看得见（``uicheck.py`` 里那条渲染断言）。
        2. ``self._built`` 必须**在调用 build() 之前**置位：``build()`` 结尾
           通常会自己调一次 ``refresh()`` 来显示真实状态，而 ``refresh()``
           开头有 ``if not self._built: return`` 的保护 ——
           顺序错了的话那次刷新会被静默跳过，页面停在
           ``build()`` 里写的占位文字上（"正在读取模型状态…"），
           看起来就像功能坏了。
        """
        if not self._built:
            self._built = True
            try:
                self._root = self.build()
            except Exception:
                self._built = False                      # 允许重试
                raise
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(self._root)
        return self._root

    def refresh(self) -> None:
        """拉最新状态。子类实现（可以是空操作）。"""

    def on_show(self) -> None:
        """切到本页。**默认刷新** —— 子类要额外做事就覆写并自己调 refresh。"""
        self.refresh()

    def on_task(self, t) -> None:                        # noqa: ARG002
        """收到任务变更。默认不做（需要的子类覆写）。"""

    # ------------------------------------------------------------ 弹窗策略
    def say(self, kind: str, title: str, text: str) -> None:
        """统一弹窗入口。

        ⚠️ 在**无头模式**（``L2T_HEADLESS=1``，界面自检用）下，
        ``QMessageBox`` 会**永远阻塞**（没有事件循环来关它），
        所以这里改成打印到 stdout。自检就是靠这一条才不会挂死。
        """
        import os as _os
        if _os.environ.get("L2T_HEADLESS"):
            print(f"[{kind}] {title}: {text[:400]}", flush=True)
            return
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon({
            "info": QMessageBox.Icon.Information,
            "warn": QMessageBox.Icon.Warning,
            "error": QMessageBox.Icon.Critical,
        }.get(kind, QMessageBox.Icon.Information))
        box.setWindowTitle(title)
        box.setText(text)
        box.exec()

    def ask(self, title: str, text: str,
            default_yes: bool = True) -> bool:
        """是/否确认。无头模式下**返回默认值**（不阻塞）。"""
        import os as _os
        if _os.environ.get("L2T_HEADLESS"):
            print(f"[ask] {title}: {text[:200]} → {default_yes}", flush=True)
            return default_yes
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes if default_yes
            else QMessageBox.StandardButton.No)
        return ans == QMessageBox.StandardButton.Yes

    # ---------------------------------------------------------------- 工具
    def card_with(self, *widgets, spacing: int = 8, margins=(14, 12, 14, 12)):
        f = card()
        lay = QVBoxLayout(f)
        lay.setContentsMargins(*margins)
        lay.setSpacing(spacing)
        for x in widgets:
            if x is None:
                lay.addStretch(1)
            elif isinstance(x, QWidget):
                lay.addWidget(x)
            elif isinstance(x, int):
                lay.addSpacing(x)
        return f


def page_header(page: Page) -> QWidget:
    """页面顶部标题块（统一：标题 + 副标题 + 右侧插槽由子类自己加）。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)
    left = col(h1(page.TITLE),
               dim(page.SUBTITLE) if page.SUBTITLE else None, spacing=3)
    lay.addWidget(left, 1)
    return w


def mono(text: str = "") -> QLabel:
    lb = label(text, kind="mono")
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def big_number(value: str, caption: str) -> QWidget:
    """一个大数字 + 小标题（结果页用）。"""
    v = label(value, color=C["text"])
    f = QFont(v.font())
    f.setPointSize(f.pointSize() + 6)
    f.setBold(True)
    v.setFont(f)
    v.setAlignment(Qt.AlignmentFlag.AlignCenter)
    c = label(caption, kind="faint")
    c.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return col(v, c, spacing=0)
