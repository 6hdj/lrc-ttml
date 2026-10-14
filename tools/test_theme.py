"""守"深浅两套主题都成立"这条约定。

    python tools/test_theme.py

## 为什么要专门测（两个主题最容易出的两类错）

1. **键少了 → 一进界面就崩**。页面代码到处写 `C["accent"]`，浅色主题少一个键
   就是 `KeyError`。而 `STATUS_COLOR` 之类是**模块级 dict**，漏了只在切主题时炸。
2. **对比度不够 → 字看不见**。这是浅色主题的经典坑，而且**不报错**：
   白字打在浅蓝按钮上，截图里才发现（真踩到：主按钮"保存""重新体检"几乎全白）。
   本仓已经因为"属性断言查不出看不见的字"栽过好几次。

所以本测试做三件事：键集一致、**对比度达标**、QSS 两套都能生成且无残留占位符。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def lum(hexcolor: str) -> float:
    """WCAG 相对亮度。"""
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    """WCAG 对比度（1~21）。"""
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# (前景键, 背景键, 最低对比度, 说明)
# 阈值取 WCAG AA：正文 4.5、大字号/次要文字 3.0。
PAIRS = [
    ("text", "bg", 4.5, "正文 / 页面底"),
    ("text", "panel", 4.5, "正文 / 卡片"),
    ("dim", "bg", 3.0, "次要文字 / 页面底"),
    ("dim", "panel", 3.0, "次要文字 / 卡片"),
    # ⚠️ 「主按钮文字 / 强调底」用 **3.0** 而不是 4.5：
    # 按钮文字是**大字号加粗**（`font-weight: 600` + 22px 内边距），
    # WCAG 对"大字号/UI 组件"的门槛就是 3:1，不是正文的 4.5。
    # 而且实测：要把白字压到 4.5 得把强调色压到 `#3a76c4` 以下，
    # 那时强调色**当链接色用**在深底上就只剩 3.9:1 —— 拆东墙补西墙。
    ("on_accent", "accent", 3.0, "主按钮文字 / 强调底（截图里踩过：几乎全白）"),
    ("ok", "panel", 3.0, "成功色 / 卡片"),
    ("warn", "panel", 3.0, "警告色 / 卡片"),
    ("err", "panel", 3.0, "错误色 / 卡片"),
    ("line", "panel", 1.1, "描边 / 卡片（只需能分辨）"),
]


def main() -> int:
    import desktop.pages.base as b
    print("=" * 74)
    print("  深浅主题：键集 / 对比度 / QSS")
    print("=" * 74)

    # ---- 1. 键集一致 ----
    print("\n[1] 键集一致（少一个键 = 换主题时 KeyError）")
    dk, lk = set(b.DARK), set(b.LIGHT)
    check(dk == lk, f"两套主题键集完全相同（{len(dk)} 个键）",
          f"只在一侧: {sorted(dk ^ lk)}")

    # ---- 2. STATUS_COLOR 的键必须都在调色板里 ----
    print("\n[2] 状态色表与调色板一致")
    for theme in ("dark", "light"):
        b.apply_theme(theme)
        missing = [k for k, v in b.STATUS_COLOR.items() if v not in b.C.values()]
        check(not missing, f"{theme}: STATUS_COLOR 的取值都来自当前调色板",
              f"不在调色板里的: {missing}")

    # ---- 3. 对比度 ----
    print("\n[3] 对比度（WCAG；不达标的地方截图里就是「字看不见」）")
    for theme in ("dark", "light"):
        pal = b.THEMES[theme]
        print(f"  -- {theme} --")
        for fg, bg, need, why in PAIRS:
            if fg not in pal or bg not in pal:
                check(False, f"{theme}: {fg}/{bg} 键存在", "键缺失")
                continue
            c = contrast(pal[fg], pal[bg])
            check(c >= need, f"{theme}: {why}  {c:.2f}:1 (需 ≥{need})",
                  f"{pal[fg]} on {pal[bg]}")

    # ---- 4. QSS ----
    print("\n[4] QSS 生成")
    for theme in ("dark", "light"):
        q = b.qss(theme)
        left = sorted(set(re.findall(r"\{[a-z_]+\}", q)))
        check(not left, f"{theme}: QSS 无残留占位符", f"{left[:6]}")
        check(q.count("{") == q.count("}"),
              f"{theme}: QSS 花括号配对",
              f"{{={q.count('{')} }}={q.count('}')}")
        for sel in ("QPushButton#primary", "QFrame#card", "#nav", "QProgressBar"):
            check(sel in q, f"{theme}: QSS 含 {sel}")
    check(b.qss("dark") != b.qss("light"), "两套主题生成了不同的 QSS")

    # ---- 5. 原地切换（页面绑的是同一个 dict 对象）----
    print("\n[5] apply_theme 必须**原地**改（重新赋值会让页面拿着旧色）")
    ref = b.C
    b.apply_theme("light")
    check(b.C is ref, "apply_theme 后 C 仍是同一个对象")
    check(b.C["bg"] == b.LIGHT["bg"], "C 已变成浅色值")
    ref2 = b.STATUS_COLOR
    b.apply_theme("dark")
    check(b.STATUS_COLOR is ref2, "STATUS_COLOR 仍是同一个对象")
    check(b.C["bg"] == b.DARK["bg"], "已切回深色")

    # ---- 6. auto 解析 ----
    print("\n[6] auto 解析")
    check(b.resolve_theme("auto") in ("dark", "light"), "auto 能解析成具体主题")
    # ⚠️ 2026-10-14 UI 重构：默认观感从深色改成**浅色**（任务书要求
    # "默认优先设计浅色主题"）。空值/乱写都退回 light，不再退回 dark。
    check(b.resolve_theme("") == "light", "空值退回浅色（不崩）")
    check(b.resolve_theme("乱写") == "light", "无法识别的值退回浅色")

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
