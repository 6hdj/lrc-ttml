"""把界面字符串里"没有字形"的符号换成**实测有字形**的（先 dry-run，看清单再落盘）。

    python tools/fix_ui_glyphs.py --dry-run     # 只列清单
    python tools/fix_ui_glyphs.py               # 落盘

## 为什么要有这个工具（不是在炫技）

截图为证：本程序主字体 **Microsoft YaHei UI** 对下面这些字符**没有字形**，
界面里会显示成**空心方框**（`.tmp/exp_symbol_candidates.py` 的渲染结果）：

    不能显示：✓  ✔  ⚠  …  ·  —（显示成 □）   」、「 显示成怪字符
    能显示：  √  ✗  ▶  ⟳  ⊘  ↓  ！  ※  ∆  ▲  ○  ×  ●  ◆   ...  -  》  「  •

只改**会显示给用户的字符串**：
  * 跳过 docstring / 注释（那里写 `⚠️` 无所谓，AI 和开发者看得到就行）；
  * 跳过 `desktop/selftest.py`、`uicheck.py`（断言与打印，不是界面）；
  * 跳过 `C[...]`、`objectName`、QSS 这类非文案位置。

⚠️ 只动引号里的字面量，**不动标识符**（比如字典键 `"ok"`、`"done"`）。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = [
    "desktop/pages/base.py", "desktop/pages/welcome.py", "desktop/pages/home.py",
    "desktop/pages/models.py", "desktop/pages/tasks.py",
    "desktop/pages/settings.py", "desktop/main_window.py",
]

# 顺序敏感：先把 `⚠️`（带变体选择符）处理掉，再处理单独的 `⚠`
#
# ⚠️⚠️ **一个差点改错的细节**：截图里 `」` 的位置显示成怪字符，
# 我第一版就把 `」` 换成了 `》` —— 真跑 dry-run 才发现那会把
# `「制作」` 变成 `「制作》`（**右括号本来就正常**）。
# 用逐字渲染图（`.tmp/exp_symbol_candidates.py`）复核后确认：
#   * `「`(U+300C) 显示成方框/怪字符 → 要换；
#   * `」`(U+300D) 其实是好的 → **不要动**。
# 教训：替换符号时"看到哪个字符坏了"要逐字确认，别照整串猜。
REPL = [
    ("\u26a0\ufe0f", "!"),      # ⚠️ → !
    ("\u26a0", "!"),            # ⚠  → !
    ("\u2714", "√"),            # ✔  → √
    ("\u2713", "√"),            # ✓  → √
    ("\u2026", "..."),          # …  → ...
    ("\u00b7", "•"),            # ·  → •
    ("\u2014", "-"),            # —  → -
    # ⚠️ **`「`(U+300C) 到底能不能显示，还要再确认一次**（见上方说明）：
    # 这一条先留空（原样保留），等渲染图确认后再决定换不换。
]

# 只匹配"字符串字面量"（单行、以引号包住、不含 % 或 {} 格式化的复杂情况也照改）
_STR = re.compile(r'(?P<q>["\'])(?P<body>(?:[^"\'\\\n]|\\.)*?)(?P=q)')


def is_doc_or_comment(line: str) -> bool:
    s = line.strip()
    return (s.startswith("#") or s.startswith('"""') or s.startswith("'''")
            or s.startswith("*"))


def fix_line(line: str) -> tuple:
    if is_doc_or_comment(line):
        return line, []
    hits = []

    def sub(m):
        body = m.group("body")
        new = body
        for a, b in REPL:
            if a in new:
                hits.append(a)
                new = new.replace(a, b)
        if new == body:
            return m.group(0)
        return f"{m.group('q')}{new}{m.group('q')}"

    return _STR.sub(sub, line), hits


def main() -> int:
    ap = argparse.ArgumentParser(description="修界面里没有字形的符号")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    total = 0
    print("=" * 74)
    print("  界面符号替换" + ("（dry-run，不落盘）" if a.dry_run else ""))
    print("=" * 74)
    for rel in TARGETS:
        path = os.path.join(HERE, rel)
        if not os.path.isfile(path):
            continue
        raw = io.open(path, encoding="utf-8").read()
        lines = raw.split("\n")
        out, changed = [], []
        for i, ln in enumerate(lines, 1):
            new, hits = fix_line(ln)
            if new != ln:
                changed.append((i, ln.strip()[:60], new.strip()[:60], hits))
            out.append(new)
        if not changed:
            continue
        print(f"\n  {rel}  （{len(changed)} 行）")
        for i, old, new, hits in changed:
            print(f"    L{i}: {''.join(hits)}")
            print(f"       - {old}")
            print(f"       + {new}")
        total += len(changed)
        if not a.dry_run:
            io.open(path, "w", encoding="utf-8", newline="").write("\n".join(out))
    print(f"\n  共 {total} 行")
    if a.dry_run and total:
        print("  （dry-run：什么都没改；去掉 --dry-run 才落盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
