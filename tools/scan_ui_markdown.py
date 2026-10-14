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


def scan_manifest(path: str, rel: str) -> list:
    """扫清单 Json 里**会被显示到界面上**的字段。

    ⚠️⚠️ 为什么要加这一段（真漏过）：
    这个工具原来只走 `desktop/**/*.py`。而模型的名称/用途、运行组件的
    名称/用途都在 `resources/*_manifest.json` 里，界面**直接把它们显示出来** ——
    于是 `cuda.purpose` 里的 `**语音识别根本跑不了**` 原样出现在卡片上
    （截图里就是两个字面星号），而**检查工具说"没问题"**。
    "扫了源码就以为覆盖了界面文案"正是这里的盲区。

    只查几个明确会显示的字段（name/purpose/note/…），避免把 JSON 里的
    说明性文字（`_comment`）误报。
    """
    import json
    hits: list = []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return hits

    SHOWN = ("name", "purpose", "note", "detail", "title", "description")

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in SHOWN and isinstance(v, str):
                    for m in MD.finditer(v):
                        hits.append((rel, 0, m.group(0), f"{where}.{k} = {v[:60]}"))
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i2, v in enumerate(node):
                walk(v, f"{where}[{i2}]")

    walk(d, os.path.basename(path))
    return hits


def main() -> int:
    hits = []
    for root, _dirs, files in os.walk(os.path.join(HERE, "desktop")):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, HERE)
            if fn.endswith(".py") and fn not in ("uicheck.py", "selftest.py"):
                hits.extend(scan_file(p, rel))
            # ⚠️ 清单 Json 也要扫：界面会把 name/purpose 直接显示出来
            elif fn.endswith(".json") and "manifest" in fn:
                hits.extend(scan_manifest(p, rel))

    print("=" * 74)
    print("  界面可见文案里漏下的 Markdown 记号")
    print("  （覆盖 desktop/**/*.py 与 resources/*manifest*.json）")
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
