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


def _joined_call_text(lines: list, start: int) -> str:
    """把从 `start` 行开始的**整段调用文本**拼出来，直到括号闭合。

    ⚠️⚠️ 为什么必须这么做（真漏过，而且是**第三次**）：
    原来只检查"含 `label(` 的**那一行**"。可是界面文案常常写成多行拼接：

        label("语音识别**必须**用显卡跑（CPU 跑一首歌要几分钟），"
              "而 CUDA 运行库不在安装包里。")

    星号在**第二行**，那一行既没有 `label(` 也没有其它可见调用名 ⇒
    **扫描器直接跳过**，于是一个会显示成字面星号的文案被判为"没问题"。
    本项目已经因为这类漏检在界面上三次显示过字面 `**`（模型卡、运行组件卡、本次）。
    所以这里改成：从调用开始行往后拼，**按括号配平**找到结尾，
    只保留**字符串字面量内部**的文本（跳过属性名等标识符）。
    """
    buf = []
    depth = 0
    seen_open = False
    for ln in lines[start - 1:start + 40]:
        # 去掉行内注释（字符串里的 # 不管，保守起见只处理行尾注释）
        code = ln.split("#")[0] if "#" not in ln.split('"')[0] else ln
        buf.append(code)
        for ch in code:
            if ch == "(":
                depth += 1
                seen_open = True
            elif ch == ")":
                depth -= 1
        if seen_open and depth <= 0:
            break
    text = "\n".join(buf)
    # 只取双引号/单引号包裹的内容，避免把标识符也算进来
    import re as _re
    parts = _re.findall(r'"([^"\n]*)"|\'([^\'\n]*)\'', text)
    return "".join(a or b for a, b in parts)


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
        # ⚠️ 扫**整段调用**（含后续拼接行），不只是这一行
        text = _joined_call_text(lines, i)
        for m in MD.finditer(text):
            hits.append((rel, i, m.group(0), text[max(0, m.start() - 20):
                                                  m.end() + 30].replace("\n", " ")))
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
