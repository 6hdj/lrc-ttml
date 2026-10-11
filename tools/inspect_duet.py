"""对唱格式体检：把一份 TTML 里"对唱/和声是怎么表达的"全部打印出来。

用途：拿到一份**能在 Apple Music 正常左右分栏**的参考文件后，跑一遍就知道它的确切形状，
再照它对齐本项目的生成器。

    python tools/inspect_duet.py refs/apple-duet-ref.ttml
    python tools/inspect_duet.py outputs/在出发之前.ttml

关注点（决定 Apple Music 分不分栏）：
1. `<ttm:agent>` 声明了哪些、`type` 是什么、有没有 `ttm:name`
2. 每行 `<p>` 上的 `ttm:agent` 取值分布（全 v1 = 一定不分栏）
3. `x-duet-a` / `x-duet-b` 到底**写在哪一层**：`<p>` 自己 / 包装 span / 叶子 span
4. `x-bg` 的写法（包装层有没有 begin/end、括号是半角还是全角、套了几层）
5. itunes 命名空间用的是哪一个
"""
from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

NS_TTM = "http://www.w3.org/ns/ttml#metadata"
NS_XML = "http://www.w3.org/XML/1998/namespace"


def local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def role_of(el) -> str:
    return (el.get("{" + NS_TTM + "}role") or el.get("ttm:role") or "").lower()


def attr_like(el, name: str) -> str:
    """按 local-name 找属性（前缀不规范也能读到）"""
    for k, v in el.attrib.items():
        if local(k) == name:
            return v
    return ""


def inspect(ref: str):
    print("=" * 70)
    print("文件:", ref)
    if not os.path.exists(ref):
        print("  ✗ 文件不存在")
        return 1
    text = open(ref, encoding="utf-8-sig").read()

    # ---------------------------------------------------------------- 命名空间
    m = re.search(r"<tt\b[^>]*>", text)
    print("\n[1] 根元素属性")
    if m:
        for a in re.findall(r'([\w:.-]+)="([^"]*)"', m.group(0)):
            if a[0].startswith("xmlns") or a[0] in ("xml:lang", "itunes:timing"):
                print("    %-18s %s" % (a[0], a[1]))

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print("  ✗ XML 解析失败:", e)
        return 1

    # ---------------------------------------------------------------- agent 声明
    print("\n[2] <ttm:agent> 声明")
    agents = []
    for el in root.iter():
        if local(el.tag) == "agent":
            aid = el.get("{" + NS_XML + "}id") or attr_like(el, "id")
            tname = ""
            for c in el:
                if local(c.tag) == "name":
                    tname = (c.text or "").strip()
            agents.append((aid, attr_like(el, "type"), tname))
    for aid, t, n in agents:
        print("    xml:id=%-4s type=%-10s name=%s" % (aid, t or "(缺)", n or "(无)"))
    if not agents:
        print("    (没有任何 agent 声明 —— 播放器无法区分演唱者)")

    # ---------------------------------------------------------------- 每行的 agent
    ps = [el for el in root.iter() if local(el.tag) == "p"]
    print("\n[3] <p> 的 ttm:agent 分布（共 %d 行）" % len(ps))
    using = Counter(attr_like(p, "agent") or "(缺)" for p in ps)
    for k, v in using.most_common():
        print("    agent=%-8s %d 行" % (k, v))
    if len(using) <= 1:
        print("    ⚠ 只有一个取值 → Apple Music 不会出现左右分栏")

    # ---------------------------------------------------------------- x-duet 位置
    print("\n[4] x-duet / x-narration 角色写在哪一层")
    found = 0
    for el in root.iter():
        r = role_of(el)
        if not r or not any(k in r for k in ("x-duet", "x-narration")):
            continue
        found += 1
        tag = local(el.tag)
        has_kids = any(local(c.tag) == "span" for c in el)
        b = attr_like(el, "begin")
        layer = {"p": "行 <p> 自己", "span": ("包装 span（含子 span）" if has_kids else "叶子 span")}.get(
            tag, tag)
        print("    %-22s role=%-12s begin=%s" % (layer, r, b or "(无)"))
    if not found:
        print("    (没有 x-duet —— 若参考文件也没有，说明 Apple 只靠 ttm:agent)")

    # ---------------------------------------------------------------- x-bg 写法
    print("\n[5] x-bg 写法")
    bg = [el for el in root.iter() if "x-bg" in role_of(el)]
    print("    出现 %d 次" % len(bg))
    for el in bg[:2]:
        b = attr_like(el, "begin")
        kids = [c for c in el if local(c.tag) == "span"]
        raw = ET.tostring(el, encoding="unicode")
        print("    包装层 begin=%s 子 span=%d 个" % (b or "(无)", len(kids)))
        print("      ", raw[:240].replace("\n", " "))
        txt = "".join(el.itertext())
        if txt:
            half = txt.strip().startswith("(") and txt.strip().endswith(")")
            full = txt.strip().startswith("（") and txt.strip().endswith("）")
            print("      括号：半角=%s 全角=%s" % (half, full))

    # ---------------------------------------------------------------- 对唱样例
    print("\n[6] 对唱样例（按 agent 分组各取一行原始 XML）")
    seen = set()
    for p in ps:
        a = attr_like(p, "agent")
        if a in seen or len(seen) >= 3:
            continue
        seen.add(a)
        print("    --- agent=%s ---" % a)
        print("      ", ET.tostring(p, encoding="unicode")[:300].replace("\n", " "))

    # ---------------------------------------------------------------- 校验
    print("\n[7] 用本项目校验器跑一遍")
    try:
        from ttml_check import check_ttml
        for strict in (True, False):
            res = check_ttml(text, strict=strict)
            print("    strict=%-5s %d 错误 / %d 警告" % (strict, len(res.errors), len(res.warnings)))
            for e in res.errors[:4]:
                print("        ✗", e.where, e.issue)
    except Exception as e:
        print("    校验器调用失败:", type(e).__name__, e)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(max(inspect(p) for p in sys.argv[1:]))
