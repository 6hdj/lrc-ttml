"""找出界面**真正可见**的字符串里漏下的 Markdown 记号（精确版）。

    python tools/scan_ui_markdown.py

只认这些"真的会显示给用户"的调用形式：
    label(...) / h2(...) / h1(...) / hint(...) / setText(...) / setToolTip(...)
    / setPlaceholderText(...) / setWindowTitle(...) / QMessageBox.*(...) / button(...)

⚠️ 这个工具被自己坑过两轮，所以判据写得刻意保守：
  1. 只看**这些调用所在的行**（不看任意字符串字面量）——
     否则 docstring、注释里的 Markdown 全会误报；
  2. 注释行直接跳过。
  误报比漏报更糟：一个满屏误报的检查工具，人看一眼就永久忽略了。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MD = re.compile(r"\*\*[^*\n]{1,40}\*\*|``[^`\n]{1,40}``")

# 这些调用会把参数原样显示在界面上（QLabel/QMessageBox/QPushButton 都是纯文本）
VISIBLE_CALL = re.compile(
    r"\b(label|h1|h2|hint|button|setText|setToolTip|setPlaceholderText"
    r"|setWindowTitle|question|information|warning|critical|appendPlainText"
    r"|addItem|setStatusTip|setWhatsThis)\s*\(")


def scan_file(path: str, rel: str) -> list:
    hits = []
    lines = open(path, encoding="utf-8").read().split("\n")
    in_doc = False
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        code = ln.split("#")[0]
        starts = code.count('"""') + code.count("'''")
        if in_doc:
            if starts % 2 == 1:
                in_doc = False
            continue
        if s.startswith("#") or s == "":
            continue
        if starts % 2 == 1:
            in_doc = True
            continue
        if not VISIBLE_CALL.search(ln):
            continue
        for m in MD.finditer(code):
            hits.append((rel, i, m.group(0), s[:78]))
    return hits


def main() -> int:
    hits = []
    for root, _dirs, files in os.walk(os.path.join(HERE, "desktop")):
        for fn in files:
            if not fn.endswith(".py") or fn in ("uicheck.py", "selftest.py"):
                continue
            p = os.path.join(root, fn)
            hits.extend(scan_file(p, os.path.relpath(p, HERE)))

    print("=" * 74)
    print("  界面可见文案里漏下的 Markdown 记号")
    print("=" * 74)
    if not hits:
        print("  ✅ 没找到（Qt 控件是纯文本，星号会原样显示给用户）")
        return 0
    for rel, i, md, src in hits:
        print(f"  {rel}:{i}  {md}")
        print(f"      {src}")
    print(f"\n  共 {len(hits)} 处 —— 改成「」或去掉记号。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
