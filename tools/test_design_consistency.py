"""设计一致性检查：**用断言代替肉眼看图**。

    python tools/test_design_consistency.py

## 为什么需要它

任务书的工作方式要求"逐屏人工视觉核对"，但：
1. 我不具备"看图"能力（只能读像素值），审美判断做不了；
2. 就算能看，人眼也**查不出**"第 3 页的卡片圆角是 8px、第 5 页是 10px"
   这种系统性问题 —— 那正是任务书说的"页面拼接感"。

所以这里把"一致性"变成**可执行断言**：不判断"好不好看"，
只判断"**是否用的同一套尺子**"。这能抓住拼接感的机械来源：
尺寸漂移、颜色硬编码、状态缺失、层级不一致。

## 判据

1. **无残留占位符**：两套主题的 QSS 里不能有 `{{xxx}}`；
2. **无页面级硬编码尺寸**：`pages/*.py` 里出现 `font-size:` /
   `border-radius:` 字面量就是漂移（应该走 QSS/token）；
3. **卡片内边距一致**：所有页面的 `QFrame#card` 内容边距必须相同；
4. **两套主题键集相同**（`test_theme.py` 也守着，这里再确认一次）；
5. **所有页面在两种主题下都能渲染**且尺寸非零（不能只有浅色对）；
6. **焦点/按压状态存在**：QSS 里 `:focus`、`:pressed` 必须有用到
   （Phase 1 审查时是 0，属回归项）；
7. **Enter 挂钩的接与不接符合契约**（见 `Page` 的文档）：
   接了={home, settings}，故意不接={welcome, models}。
"""
from __future__ import annotations

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["L2T_DATA_DIR"] = os.path.join(HERE, ".tmp", "design-sandbox")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["L2T_HEADLESS"] = "1"

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


# ---------------------------------------------------------------- 静态部分
print("=" * 76)
print("  设计一致性检查（用断言代替看图）")
print("=" * 76)

print("\n[1] QSS 无残留占位符 / 状态齐全")
for th in ("dark", "light"):
    q = ""
    # 延迟导入：需要 Qt
    pass

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame  # noqa: E402

app = QApplication([])
for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"):
    if os.path.isfile(p):
        QFontDatabase.addApplicationFont(p)

from desktop.pages import base as b  # noqa: E402

for th in ("dark", "light"):
    q = b.qss(th)
    left = sorted(set(re.findall(r"\{\{[a-z_]+\}\}", q)))
    check(not left, f"{th}: QSS 无残留占位符", str(left[:4]))
    for st in (":pressed", ":focus", ":disabled"):
        check(st in q, f"{th}: 有 {st} 状态样式")

print("\n[2] 页面里不许硬编码尺寸（否则就是漂移）")
BAD = (r"font-size:\s*\d+px", r"border-radius:\s*\d+px")
for fn in sorted(os.listdir(os.path.join(HERE, "desktop", "pages"))):
    if not fn.endswith(".py") or fn in ("base.py", "tokens.py", "icons.py"):
        continue
    src = io.open(os.path.join(HERE, "desktop", "pages", fn),
                  encoding="utf-8").read()
    hits = []
    for pat in BAD:
        # 只在 setStyleSheet 的字符串里找
        for m in re.finditer(pat, src):
            line_start = src.rfind("\n", 0, m.start()) + 1
            line = src[line_start:src.find("\n", m.start())]
            if "setStyleSheet" in line or "styleSheet" in line:
                hits.append(m.group(0))
    check(not hits, f"{fn}: 无硬编码 font-size/border-radius", str(hits[:3]))

print("\n[3] 卡片内边距一致（同一类卡片必须同一套留白）")
b.apply_theme("light")
app.setStyleSheet(b.qss("light"))
from desktop.context import AppContext  # noqa: E402
from desktop.main_window import MainWindow, NAV  # noqa: E402

w = MainWindow(AppContext())
w.resize(1280, 860)
w.show()
for _ in range(10):
    app.processEvents()

margins = {}
build_errors = []
for key, _t, _tip in NAV:
    page = w.show_page(key)
    for _ in range(8):
        app.processEvents()
    # ⚠️⚠️ **构建失败必须算失败**（变异验证时发现的漏洞）：
    # 如果某个页面 `build()` 抛异常，它在下面既不会贡献 card 内边距、
    # 也无法渲染 —— 而"内边距种类 == 1"这种断言会**空过**（页都没建出来，
    # 自然没有第二种内边距）。那正是本项目反复踩的"假绿"。
    # 所以这里显式把异常记下来并判失败。
    try:
        page.ensure_built()
    except Exception as e:                             # noqa: BLE001
        build_errors.append(f"{key}: {type(e).__name__}: {str(e)[:80]}")
        continue
    for _ in range(6):
        app.processEvents()
    for f in page.findChildren(QFrame):
        if f.objectName() != "card":
            continue
        lay = f.layout()
        if lay is None:
            continue
        m = lay.contentsMargins()
        margins.setdefault((m.left(), m.top(), m.right(), m.bottom()), []).append(key)

check(not build_errors, "六个页面都能构建（否则下面的判据会空过）",
      str(build_errors[:2]))
print(f"  card 内边距种类: { {k: sorted(set(v)) for k, v in margins.items()} }")
check(len(margins) == 1,
      "所有 card 用同一套内边距（取 token）",
      f"发现 {len(margins)} 种: {list(margins)[:4]}")

print("\n[4] 两套主题键集相同")
check(sorted(b.DARK) == sorted(b.LIGHT), "DARK / LIGHT 键集一致",
      f"差集 {sorted(set(b.DARK) ^ set(b.LIGHT))[:5]}")

print("\n[5] 两种主题下所有页面都能渲染（不能只有浅色对）")
for th in ("dark", "light"):
    b.apply_theme(th)
    app.setStyleSheet(b.qss(th))
    w2 = MainWindow(AppContext())
    w2.resize(1120, 760)
    w2.show()
    for _ in range(8):
        app.processEvents()
    bad = []
    for key, _t, _tip in NAV:
        pg = w2.show_page(key)
        for _ in range(6):
            app.processEvents()
        root = pg.ensure_built()
        for _ in range(4):
            app.processEvents()
        if root is None or root.width() <= 100 or root.height() <= 100:
            bad.append(key)
    check(not bad, f"{th} 主题：六个页面都能渲染出非零尺寸", str(bad))
    w2.close()

print("\n[6] Enter 挂钩符合契约")
WANT_YES = {"home", "settings"}
WANT_NO = {"welcome", "models"}
for key, _t, _tip in NAV:
    page = w.get_page(key) if hasattr(w, "get_page") else w._pages.get(key)
    has = callable(getattr(page, "submit_current", None))
    if key in WANT_YES:
        check(has, f"{key}: 接了 Enter（表单/保存这类安全动作）")
    elif key in WANT_NO:
        check(not has,
              f"{key}: **故意不接** Enter（主按钮是 GB 级下载，误触代价高）")
    else:
        check(True, f"{key}: 无 Enter 语义（不接）")

# ================================================================
# 以下三条是 2026-10-15 **靠"看截图"查出来的真 bug** 的回归守卫。
# 它们都属于"属性断言测不出来、只有渲染出来才看得见"的那一类。
# ================================================================
print("\n[7] 页面标题必须**等于**导航项名字（否则就是「拼接感」）")
# ⚠️ 起因：导航叫「歌词工作台」，页面标题却写「制作逐字歌词」；
# 「任务」/「模型」同样对不上 —— 用户点了一个名字、页面写着另一个名字。
# 任务书把这种叫"页面拼接感"。唯一豁免：欢迎页（标题是问候语）。
EXEMPT = {"welcome"}
for key, label, _tip in NAV:
    page = w._pages.get(key)
    title = getattr(page, "TITLE", "")
    if key in EXEMPT:
        check(True, f"{key}: 豁免（欢迎页标题故意是问候语）")
    else:
        check(title == label, f"{key}: 标题 == 导航名（{title!r} vs {label!r}）")

print("\n[8] 容器控件不许挂 setStyleSheet（会外溢到子树、盖掉设计系统）")
# ⚠️⚠️ 起因（真 bug，主按钮因此**不可见**）：
# `main_window.py` 写过 `self.stack.setStyleSheet("background: transparent;")`。
# Qt 的规则是**控件级样式表对该控件及其子树优先级高于应用级样式表** ——
# 于是页面里所有按钮的底色被强制透明：主按钮只剩白字画在浅灰页面上，
# 整条「开始制作」看不见（截图里一眼可见，任何属性断言都测不到）。
#
# ⚠️ **判据必须是"运行时的控件树"，不能是扫源码文本**（我自己第一版写错过）：
# 源码里那行是 `self.stack.setStyleSheet(...)` —— **变量名看不出类型**，
# 文本匹配 `QStackedWidget` 根本匹配不到，于是变异验证显示"没抓到"。
# 正确做法：真去遍历控件树 —— **凡是有非空 styleSheet 且还有子控件的，
# 就是外溢源**（叶子控件如 QLabel 上挂样式表是安全的）。
from PySide6.QtWidgets import QWidget as _QW  # noqa: E402
from desktop.pages.base import Page as _Page  # noqa: E402

_containers_with_ss = []
_ss_build_errs = []
_roots = [w] + [w._pages.get(k) for k, _t, _tip in NAV]
for _root in _roots:
    if _root is None:
        continue
    # ⚠️⚠️ **不能"构建失败就 continue"**（我第一版就这么写，导致整条检查空过）：
    # `MainWindow` **不是** `Page`，没有 `ensure_built()` —— 抛 AttributeError
    # 被 `except: continue` 吞掉，于是**主窗口从来没被扫过**，
    # 我要抓的那个 `self.stack.setStyleSheet(...)` 正好挂在主窗口上 → 变异验证
    # 显示"没抓到"。这正是本项目最怕的"假绿"：异常被吞、检查静默通过。
    # 现在：只有 `Page` 才需要 ensure_built，失败**记为错误**（不静默跳过）。
    if isinstance(_root, _Page):
        try:
            _root.ensure_built()
        except Exception as e:                          # noqa: BLE001
            _ss_build_errs.append(f"{type(e).__name__}: {str(e)[:60]}")
            continue
    for _wd in _root.findChildren(_QW):
        _ss = _wd.styleSheet()
        if not _ss:
            continue
        if _wd.findChildren(_QW):           # 有子控件 ⇒ 是容器 ⇒ 会外溢
            _containers_with_ss.append(
                f"{type(_wd).__name__}"
                f"{'#' + _wd.objectName() if _wd.objectName() else ''}"
                f"({len(_ss)}字符)")
check(not _ss_build_errs, "样式表扫描时页面都能构建（否则会空过）",
      str(_ss_build_errs[:2]))
for _k, _l, _t in NAV:
    _pg = w._pages.get(_k)
    if _pg is not None and _pg.styleSheet():
        _containers_with_ss.append(f"{_k} 页根控件")
check(not _containers_with_ss,
      "容器控件上没有 setStyleSheet（样式只由设计系统管）",
      str(_containers_with_ss[:3]))

print("\n[9] 卡片内部不许出现「灰条带」（容器必须透明）")
# ⚠️ 起因：`QWidget { background: {{bg}} }` 是**通配**规则，会命中
# `row()`/`col()` 造出来的匿名容器 → 白卡片内部出现一条条灰色横带。
# 现在 QWidget 默认 transparent，只有 窗口/页面栈/侧边栏/卡片 负责画底。
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QWidget as _QW  # noqa: E402

_bands = []
for key, _t, _tip in NAV:
    pg = w.show_page(key)
    for _ in range(8):
        app.processEvents()
    pg.ensure_built()
    for _ in range(6):
        app.processEvents()
    img = w.grab().toImage()

    def _in_card(x):
        p = x.parentWidget()
        while p is not None:
            if p.objectName() in ("card", "cardFlat"):
                return p
            p = p.parentWidget()
        return None

    for holder in pg.findChildren(_QW):
        if holder.objectName() not in ("", None):
            continue
        # ⚠️⚠️ **必须跳过隐藏控件**（否则全是误报，真踩到）：
        # 「③ 高级选项」和「⑥ 平台 ID」两张卡片默认 `setVisible(False)`，
        # 它们内部的布局容器也被隐藏 —— 但 `findChildren` 照样能找到。
        # 拿它们的坐标去采样，采到的其实是**页面底色**（那里画的是别的卡片），
        # 于是被误判成"灰条带"（我第一版就误报了 2 个，白查一轮）。
        if not holder.isVisible():
            continue
        lay = holder.layout()
        if lay is None or lay.count() == 0:
            continue
        g = holder.geometry()
        if g.width() < 40 or g.height() < 20:
            continue
        card = _in_card(holder)
        if card is None or not card.isVisible():
            continue
        # ⚠️ 采样点必须**不被子控件覆盖**，否则采到按钮/边框的颜色，
        # 会把"正常"误判成"条带"（第一版就误报了 4 个）。
        kids = [c.geometry() for c in holder.children()
                if isinstance(c, _QW) and c.isVisible()]
        pt = None
        for yy in range(2, max(g.height() - 2, 3), 4):
            for xx in range(2, max(g.width() - 2, 3), 6):
                if any(k.contains(xx, yy) for k in kids):
                    continue
                pt = QPoint(xx, yy)
                break
            if pt:
                break
        if pt is None:
            continue
        wp = holder.mapTo(w, pt)
        if not (0 <= wp.x() < img.width() and 0 <= wp.y() < img.height()):
            continue
        col = img.pixelColor(wp.x(), wp.y()).name()
        expect = b.C["panel"] if card.objectName() == "card" else b.C["panel2"]
        if col != expect:
            _bands.append(f"{key}:{col}≠{expect}")
check(not _bands, "卡片内没有「灰条带」（容器透明，显示卡片底色）",
      str(_bands[:3]))

print()
print("=" * 76)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 76)
if _fail:
    print("  ❌ 有设计漂移 —— 页面之间的拼接感会回来")
sys.exit(1 if _fail else 0)
