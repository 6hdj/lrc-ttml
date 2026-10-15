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

# ⚠️ 尺寸/间距/圆角 token 的**唯一来源**（与主题无关，所以不在调色板里）。
# 改名成 `_T` 是为了显式说明"这是 qss() 要用的表"，别在页面里直接 import ——
# 页面要间距就用 QSS 或布局的 setSpacing(T["SP3"])，别抄数字。
from desktop.pages.tokens import T as _T

# --------------------------------------------------------------- 配色（唯一来源）
#
# ⚠️⚠️ **深浅色主题：一个色号都不能少**。
# 页面代码里到处是 `C["accent"]`、`C["ok"]` 这种写法（**这是好事** ——
# 全仓只有 `base.py` 有硬编码色号，所以换主题只要改这里）。
# 但那条规矩只在"**每个键两边都有**"时才成立：
# 浅色漏掉任何一个键，运行时就是 `KeyError`（一进界面就崩）。
# `tools/test_theme.py` 就是守着这一条 —— 两个主题的键集必须完全一致，
# 且对比度要够（浅色主题最容易犯的错是"灰字打在浅灰底上看不见"）。
DARK = {
    "bg": "#14161a",
    "panel": "#1b1e24",
    "panel2": "#22262e",
    "line": "#2c323c",
    "text": "#e8eaee",
    "dim": "#9aa3b0",
    "faint": "#6b7480",
    # ⚠️ 原来是 `#4c9aff`：白字压在它上面只有 **2.85:1**，连 WCAG 对
    # 大字号/UI 组件的 3:1 门槛都不到（`tools/test_theme.py` 抓到的）。
    # 压暗一点点既过门槛，又保住"强调色当链接/文字色用在深底上"（4.82:1）。
    # 这两头是一对矛盾 —— 想压到白字 4.5:1 就得用到 `#3a76c4` 以下，
    # 那时当链接色只剩 3.9:1，等于拆东墙补西墙。
    "accent": "#4785d6",
    "accent_dim": "#2b5f9e",
    "ok": "#3ecf8e",
    "warn": "#e2b53e",
    "err": "#ef5350",
    "cyan": "#22d3ee",
    # ⚠️ 「压在**强调色**上的字」必须单独一个键：深浅两套主题方向相反。
    # 旧实现把 `#ffffff` 写死在 QSS 里，于是浅色主题下主按钮
    # （蓝底白字）**在白底页面上看不清按钮上的字**（截图里"保存""重新体检"
    # 那两个字几乎全白 —— 真踩到，靠看图才发现）。
    "on_accent": "#ffffff",

    # ---------------- 2026-10-14 UI 重构新增：层级与交互态 ----------------
    # ⚠️ 规范要求"页面背景柔和中性、内容区层级清晰、主色只用于关键操作"。
    # 原来只有 bg/panel/panel2 三档，做不出"侧边栏比页面略深、卡片比页面亮"
    # 这种细腻层级，于是各处手填颜色（就是"页面拼接感"的来源）。
    # 这几档**必须两套主题都有**，否则 `test_theme.py` 的键集断言会挂。
    "bg_side": "#101216",     # 侧边栏：比页面背景略深一档
    "hover": "#252a33",       # 悬停底（中性，不抢主色）
    "hover_strong": "#2c323c",
    "selected": "#22314a",    # 选中/当前项底（主色的极淡版）
    "border_strong": "#3a424e",
    "field": "#161920",       # 输入框底（比卡片略深，像"凹下去"）
}

# 浅色主题：**不是把深色反过来那么简单**，三条要注意的：
#   * 状态色（ok/warn/err）要**加深**：`#3ecf8e` 这种亮绿在浅底上几乎看不见；
#   * `accent` 要压暗一点，否则白底上的亮蓝字对比度不够；
#   * `panel` 要比 `bg` **更亮**（浅色主题里"卡片浮起来"的方向和深色相反 ——
#     深色是卡片更亮，浅色是卡片更白、底色更灰）。
LIGHT = {
    "bg": "#f4f5f7",
    "panel": "#ffffff",
    "panel2": "#eef0f3",
    "line": "#d6dae0",
    "text": "#1f2329",
    "dim": "#5c646f",
    "faint": "#8a929c",
    "accent": "#1f6feb",
    "accent_dim": "#9dc1f5",
    "ok": "#1a7f4b",
    "warn": "#9a6b00",
    "err": "#c0392b",
    "cyan": "#0e7490",
    # 浅色主题的主按钮：强调色 `#1f6feb` 是**深蓝** → 白字对比度最好
    # （不能用 `text`：那会让深字压在深蓝上，读不出来）。
    "on_accent": "#ffffff",

    # ---------------- 2026-10-14 UI 重构新增：层级与交互态 ----------------
    # ⚠️ 规范要求"页面背景柔和中性、内容区层级清晰、主色只用于关键操作"。
    # 原来只有 bg/panel/panel2 三档，做不出"侧边栏比页面略深、卡片比页面亮"
    # 这种细腻层级，于是各处手填颜色（就是"页面拼接感"的来源）。
    # 这几档**必须两套主题都有**，否则 `test_theme.py` 的键集断言会挂。
    "bg_side": "#eceef1",     # 侧边栏：比页面背景略深一档
    "hover": "#e7eaee",       # 悬停底（中性，不抢主色）
    "hover_strong": "#dde1e7",  # 悬停（用于侧边栏选中前的过渡感）
    "selected": "#e3edfd",    # 选中/当前项底（主色的极淡版）
    "border_strong": "#c3c9d1",  # 需要更明显分隔时
    "field": "#ffffff",       # 输入框底（浅色下与 panel 同色才干净）
}

THEMES = {"dark": DARK, "light": LIGHT}

# ⚠️ `C` 是**当前生效**的调色板。页面全部 `from ... import C`，
# 所以在进程内换主题**不能**只改 `C` 的指向（别的模块早就绑定了旧 dict 对象）
# —— 见 `apply_theme()` 的做法：**原地清空再填充**，保持同一个 dict 对象。
C = dict(DARK)

_QSS_TEMPLATE = """
/* ============================================================================
   全局基础：字体、正文色、背景
   ⚠️ 尺寸/间距/圆角一律走「token」（来自 `desktop/pages/tokens.py`），
      颜色走调色板（随主题变）。**不要在页面里另写一套数字** ——
      那正是重构前"每个页面各写一套、拼在一起不像一套软件"的根源。
   ============================================================================ */
/* ⚠️⚠️ 容器控件默认**必须透明**（2026-10-15 靠"看截图"查出的真 bug）。
   Phase 1 的模板写的是 `QWidget { background: {{bg}}; }` —— 那是**通配**规则，
   会命中**每一个**控件，包括 `row()` / `col()` 造出来的匿名容器。
   后果（截图里一眼就能看到、任何属性断言都测不出来）：
   · 白色卡片内部出现一条条**灰色横带**（容器自己在画页面底色），
     输入行、按钮行、提示行全都被"贴"了灰底，界面看着像拼出来的；
   · 主按钮的蓝色底也被盖掉（后来查明主因是 `QStackedWidget` 上的
     控件级样式表，见 main_window.py，但这条通配规则同样在放大问题）。
   现在：**只有明确要画底的那几层**负责背景 —— 窗口、页面栈、侧边栏、卡片。
   所有交互控件（输入框/下拉/进度条/表格/文本域…）都有自己的规则，不受影响。 */
QWidget {
    background: transparent;
    color: {{text}};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: {{FS_BODY}};
}
/* 真正"画底"的层（其余一律透明，靠这层透出来） */
QMainWindow, QDialog, QStackedWidget { background: {{bg}}; }
QLabel { background: transparent; }
QLabel#h1 { font-size: {{FS_H1}}; font-weight: 600; }
QLabel#h2 { font-size: {{FS_H2}}; font-weight: 600; }
QLabel#dim { color: {{dim}}; }
QLabel#faint { color: {{faint}}; font-size: {{FS_SMALL}}; }
QLabel#mono {
    font-family: Consolas, "Courier New", monospace;
    font-size: {{FS_SMALL}};
    color: {{dim}};
}
/* 侧边栏：比页面背景深一档，做出"导航区 vs 内容区"的层级 */
QWidget#side { background: {{bg_side}}; }

/* ============================================================ 卡片 ======== */
QFrame#card {
    background: {{panel}};
    border: 1px solid {{line}};
    border-radius: {{R_MD}};
}
QFrame#cardFlat {
    background: {{panel2}};
    border: 1px solid {{line}};
    border-radius: {{R_SM}};
}

/* ============================================================ 按钮 ========
   ⚠️ 伪状态必须**写全**（Phase 1 审查发现原模板 `:pressed` 是 0 个 ——
      点下去没有任何反馈，这是"点击没反应"的主要来源）。
   ⚠️ `#primary:disabled` 这类必须显式写：QSS 里 ID 选择器优先级高于
      `:disabled` 伪类，不写就会让禁用按钮保留启用态的文字色（深蓝底+深色字）。
   ========================================================================= */
QPushButton {
    background: {{panel}};
    border: 1px solid {{line}};
    border-radius: {{R_SM}};
    padding: {{SP1}} {{SP3}};
    min-height: {{H_CTRL}};
    color: {{text}};
}
QPushButton:hover { background: {{hover}}; border-color: {{border_strong}}; }
QPushButton:pressed { background: {{hover_strong}}; }
QPushButton:focus {
    border-color: {{accent}};
    outline: none;
}
QPushButton:disabled {
    color: {{faint}}; background: {{panel2}}; border-color: {{line}};
}
QPushButton#primary {
    background: {{accent}};
    border: 1px solid {{accent}};
    color: {{on_accent}};
    font-weight: 600;
    padding: {{SP1}} {{SP5}};
}
QPushButton#primary:hover { background: {{accent_dim}}; border-color: {{accent_dim}}; }
QPushButton#primary:pressed { background: {{accent_dim}}; }
QPushButton#primary:disabled {
    background: {{panel2}}; border: 1px solid {{line}}; color: {{faint}};
}

/* ---------------- 侧边栏导航 ---------------- */
QPushButton#nav {
    background: transparent; border: none;
    border-radius: {{R_SM}};
    padding: {{SP2}} {{SP3}};
    text-align: left;
    color: {{dim}};
    min-height: {{H_CTRL}};
}
QPushButton#nav:hover { background: {{hover}}; color: {{text}}; }
QPushButton#nav:checked {
    background: {{selected}};
    color: {{accent}};
    font-weight: 600;
}
/* 危险操作（彻底清理等）：红字，但**底仍是中性** —— 不要一整块红 */
QPushButton#danger { color: {{err}}; border-color: {{err}}; }
QPushButton#danger:hover { background: {{hover}}; border-color: {{err}}; }
QPushButton#danger:pressed { background: {{hover_strong}}; }

/* ============================================================ 输入控件 === */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background: {{field}};
    border: 1px solid {{line}};
    border-radius: {{R_SM}};
    padding: {{SP1}} {{SP2}};
    min-height: {{H_CTRL}};
    selection-background-color: {{accent}};
    selection-color: {{on_accent}};
}
QPlainTextEdit, QTextEdit { min-height: 0; }
QLineEdit:hover, QComboBox:hover { border-color: {{border_strong}}; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {
    border-color: {{accent}};
}
QLineEdit:disabled, QComboBox:disabled {
    color: {{faint}}; background: {{panel2}};
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: {{panel}};
    border: 1px solid {{line}};
    border-radius: {{R_SM}};
    padding: {{SP1}};
    selection-background-color: {{selected}};
    selection-color: {{text}};
    outline: none;
}
QCheckBox, QRadioButton { spacing: {{SP2}}; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
/* 开关（QCheckBox 当开关用时靠 objectName="switch"）*/
QCheckBox#switch { spacing: {{SP2}}; }

/* ============================================================ 进度条 ===== */
QProgressBar {
    background: {{panel2}};
    border: 1px solid {{line}};
    border-radius: {{R_SM}};
    height: 10px;
    text-align: center;
    color: {{text}};
    font-size: {{FS_SMALL}};
}
QProgressBar::chunk { background: {{accent}}; border-radius: 5px; }

/* ============================================================ 滚动条 =====
   细、无按钮、悬停才明显 —— 避免抢注意力（规范："避免厚重装饰"） */
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical {
    background: {{line}}; border-radius: 5px; min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: {{border_strong}}; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal {
    background: {{line}}; border-radius: 5px; min-width: 32px;
}
QScrollBar::handle:horizontal:hover { background: {{border_strong}}; }

/* ============================================================ 表格/列表 == */
QTableWidget, QListWidget, QTreeWidget {
    background: {{panel}};
    border: 1px solid {{line}};
    border-radius: {{R_SM}};
    gridline-color: {{line}};
    outline: none;
}
QTableWidget::item:selected, QListWidget::item:selected {
    background: {{selected}}; color: {{text}};
}
QTableWidget::item:hover, QListWidget::item:hover { background: {{hover}}; }
QHeaderView::section {
    background: {{panel2}};
    border: none;
    border-bottom: 1px solid {{line}};
    padding: {{SP1}} {{SP2}};
    color: {{dim}};
    font-size: {{FS_SMALL}};
}
QSplitter::handle { background: {{line}}; }

/* ============================================================ 状态栏/提示 */
QStatusBar { background: {{panel}}; border-top: 1px solid {{line}}; }
QStatusBar QLabel { color: {{dim}}; font-size: {{FS_TINY}}; }
QToolTip {
    background: {{panel}};
    color: {{text}};
    border: 1px solid {{border_strong}};
    border-radius: {{R_SM}};
    padding: {{SP1}} {{SP2}};
}
/* 菜单（下拉/右键）：QSS 不管的话会跟着系统走成白底，在深色主题里很突兀 */
QMenu {
    background: {{panel}}; border: 1px solid {{line}};
    border-radius: {{R_SM}}; padding: {{SP1}};
}
QMenu::item { padding: {{SP1}} {{SP4}}; border-radius: {{SP1}}; }
QMenu::item:selected { background: {{selected}}; }

/* ============================================================ 分区标题 ==
   "① 选文件"这类步骤标题：底色略深，形成分组而不是又一张卡片 */
QLabel#section {
    font-size: {{FS_BODY}};
    font-weight: 600;
    color: {{text}};
    padding: {{SP1}} 0;
}
"""


def qss(theme: Optional[str] = None) -> str:
    """按主题生成样式表（默认用当前生效的 `C`）。

    ⚠️⚠️ **不要用 `str.format`**：QSS 自己到处都是花括号（`QWidget {{ ... }}`），
    和 Python 的 `{{name}}` 混在一起，只要有一处漏了双写，`.format` 就会
    把它吃掉 —— 生成出来的样式表带着残留的 `{{accent}}`，
    Qt 只会在 stderr 打一句 `Could not parse application stylesheet`，
    **界面看着还能用**，但那些控件没吃到样式（真踩到：浅色主题内容区一片空白）。

    现在用 `str.replace`：与 QSS 自己的花括号**完全无关**，
    少一个键也不会崩（找不到就原样留着，肉眼一看就能发现）。
    """
    pal = THEMES.get(theme or "", C)
    out = _QSS_TEMPLATE
    # ⚠️ **先替换尺寸 token，再替换颜色**（2026-10-14 重构加）。
    # `T` 是"与主题无关"的尺寸/间距/圆角（见 `tokens.py`）；
    # 颜色在调色板里、随主题变。两者用**同一个 `{{key}}` 语法**，
    # 所以如果有键名撞车，颜色（后替换）会赢 —— 这正是我们要的优先级。
    for k, v in _T.items():
        out = out.replace("{{" + k + "}}", str(v))
    for k, v in pal.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def resolve_theme(name: str) -> str:
    """把配置里的主题名解析成 `dark` / `light`。

    支持 `auto`：跟随 Windows 的"应用模式"（浅色/深色）。
    ⚠️ 读注册表 `HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme`，
    **读不到就退回深色**（本程序的默认观感），不要因为读不到就崩。
    """
    n = (name or "").strip().lower()
    if n in ("dark", "light"):
        return n
    if n == "auto":
        try:
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            winreg.CloseKey(k)
            return "light" if int(val) == 1 else "dark"
        except Exception:                               # noqa: BLE001
            # ⚠️ 2026-10-14 重构：读不到注册表时退回**浅色**（新默认观感）。
            return "light"
    # 未识别主题名 → 退回浅色（新默认）
    return "light"


def apply_theme(name: str) -> str:
    """切换主题：**原地**更新 `C` 与 `STATUS_COLOR`，返回真正生效的主题名。

    ⚠️⚠️ 必须**原地改**（`C.clear()` + `C.update()`），不能 `C = THEMES[...]`：
    各页面写的是 `from desktop.pages.base import C`，那绑的是**这个 dict 对象**；
    重新赋值只会换掉 `base.C` 的指向，页面手里的还是旧的那份 ⇒
    **界面看起来"没换主题"**（只有样式表变了，代码里直接取色的地方还是旧的）。
    `STATUS_COLOR` 同理。
    """
    t = resolve_theme(name)
    C.clear()
    C.update(THEMES[t])
    STATUS_COLOR.clear()
    STATUS_COLOR.update(_build_status_maps()[0])
    return t


# ⚠️ **向后兼容的常量**：`QSS` 原来是个字符串，`app.py` / `uicheck.py`
# 都 `from ... import QSS`。改成函数后会 ImportError（自检直接挂）。
# 这里保留同名常量（值的来源仍是模板，不会与主题漂移），
# 新代码请用 `qss()` —— 它能按主题生成。
QSS = qss("dark")

# 状态 → 颜色（任务 / 模型 / 体检三处共用一套，避免"同一个状态两种颜色"）
#
# ⚠️⚠️ 这两张表**必须每次从 `C` 现算**，不能在 import 时定死：
# 它们在下面定义成了函数 `_status_maps()` 的产物 —— 因为换主题时
# `apply_theme()` 是**原地改 `C`**，如果这里写成 `"ok": C["ok"]`，
# 那些字符串在 import 那一刻就被固化了，换主题后状态色**还是旧的**
# （表现是"浅色主题下状态点还是刺眼的亮绿"，很难查）。
def _build_status_maps() -> tuple:
    colors = {
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
    icons = {
        "queued": "...", "running": "▶", "verifying": "⟳", "done": "√",
        "ready": "√", "failed": "✗", "error": "✗", "cancelled": "⊘",
        "not_installed": "•", "warn": "!", "ok": "√", "info": "•",
        "downloading": "↓",
    }
    return colors, icons

# ⚠️⚠️ 状态图标只能用**确认有字形**的字符（截图为证，见 `.tmp/exp_symbol_candidates.py`）。
#
# 真踩过：第一版用了 `✓`(U+2713) / `⚠`(U+26A0) / `…`(U+2026) / `·`(U+00B7)，
# 而**本程序的主字体 Microsoft YaHei UI 没有这些字形** → 界面上显示成
# **空心方框**（"已就绪 ▯"、"▯ 还有问题要处理"），看起来像界面坏了。
# 实测渲染图（`.tmp/symbol_test.png`）确认：
#
#   能用：√  ✗  ▶  ⟳  ⊘  ↓  ！  ※  ∆  ▲  ○  ×  ●  ◆  ...  -  》  「  •
#   不能用：✓  ✔  ⚠  …  ·  —（方框）   」→ 怪字符
#
# ⇒ 加新图标前**先渲染看一眼**，别照抄别处的符号。

# 图标与颜色分开导出：图标是**常量**（不受主题影响），颜色要跟着主题走。
STATUS_ICON = _build_status_maps()[1]


def current_status_color() -> dict:
    """当前主题下的"状态 → 颜色"表（**每次现算**，见上面的说明）。"""
    return _build_status_maps()[0]


# ⚠️ 兼容旧调用：`STATUS_COLOR` 是模块级 dict，而页面里写的是
# `STATUS_COLOR.get(...)` —— 所以这里**不能**只导出函数。
# 做法：导出一个**共享的 dict 对象**，`apply_theme()` 会原地把它更新成新主题的值
# （原地更新才能让已经 `from ... import STATUS_COLOR` 的模块也看到新颜色）。
STATUS_COLOR = current_status_color()


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
    icon = STATUS_ICON.get(state, "•")
    return label(f"{icon} {text}".strip(), color=color)


def empty_state(title: str = "", body: str = "") -> QWidget:
    """统一的**空状态**块（没有任务 / 没下模型 / 没有结果时用）。

    ⚠️ 为什么单独做（Phase 1 审查）：空状态散落在各页各写各的 ——
    home/tasks/models 里各有 5/1/5 处"还没…"文案，样式、间距都不一样，
    拼在一起不像一套。现在统一成：居中的标题 + 次要说明，配色走 token。
    """
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 18, 24, 18)
    lay.setSpacing(4)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    t = label(title, kind="dim")
    t.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(t)
    if body:
        b = label(body, kind="faint", wrap=True)
        b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(b)
    return w


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

    ## 键盘快捷键契约（2026-10-14 加）

    主窗口统一注册，页面**按需**实现下面两个可选挂钩（没实现 = 本页不响应）：

    * ``submit_current() -> bool`` —— **Enter** 触发"本页的主操作"。
    * ``cancel_current() -> bool`` —— **Esc** 触发"取消/退回"。

    返回 True 表示"我处理了"（主窗口就不再走默认行为）。

    ⚠️ **Enter 只接"确认/提交表单"这一类动作，不接"启动昂贵后台任务"**：
    * 接了：``home``（填完表单点开始）、``settings``（保存，幂等且廉价）；
    * **故意没接**：``models`` / ``welcome`` —— 它们的主按钮是
      "下载 3 GB 模型 / 500 MB 运行时"，**误触 Enter 就开始下几个 GB**，
      代价太高。这类页面的下载必须靠**明确点击**。
    * ``tasks``/``help`` 没有"主操作"语义，不接。

    这条"接与不接"的判据写在测试里（``tools/test_design_consistency.py``），
    以后新增页面时会被提醒先想清楚。
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
           ``build()`` 里写的占位文字上（"正在读取模型状态..."），
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
    def card_with(self, *widgets, spacing: int = 8, margins=None):
        # ⚠️ 卡片内边距**统一走 token**（2026-10-14 重构）。
        # 原来默认 `(14,12,14,12)`，个别页面还会传 `(16,14,16,14)` 之类 ——
        # 这是"页面拼接感"的一个来源：同一类卡片各页留白不一样。
        # 现在默认用 CARD_PAD_X/Y；确需不同的页面显式传。
        if margins is None:
            margins = (_T["CARD_PAD_X"], _T["CARD_PAD_Y"],
                       _T["CARD_PAD_X"], _T["CARD_PAD_Y"])
        f = card()
        lay = QVBoxLayout(f)
        lay.setContentsMargins(*[int(v.rstrip("px")) if isinstance(v, str)
                                 else v for v in margins])
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
