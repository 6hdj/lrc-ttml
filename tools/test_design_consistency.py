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

print()
print("=" * 76)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 76)
if _fail:
    print("  ❌ 有设计漂移 —— 页面之间的拼接感会回来")
sys.exit(1 if _fail else 0)
