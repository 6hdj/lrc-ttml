"""快速体检一份 TTML：命名空间、时间格式、背景人声/对唱落地情况、严格自检结果。

    python tools/inspect_ttml.py outputs/xxx.ttml
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from ttml_check import check_ttml  # noqa: E402


def main(path: str) -> int:
    text = open(path, encoding="utf-8").read()
    res = check_ttml(text, strict=True)
    ns = re.search(r'xmlns:itunes="([^"]+)"', text)
    t = re.search(r'begin="([^"]+)"', text)

    print(f"文件              : {path}")
    print(f"itunes 命名空间   : {ns.group(1) if ns else '(缺失)'}")
    print(f"时间戳样例        : {t.group(1) if t else '(无)'}")
    print(f"<p> 行数          : {text.count('<p ')}")
    print(f"x-bg 次数         : {text.count('x-bg')}")
    print(f"x-duet 次数       : {text.count('x-duet')}")
    print(f"x-translation     : {text.count('x-translation')}")
    print(f"agents            : {sorted(set(re.findall(r'xml:id=\"(v[0-9]+)\"', text)))}")
    print(f"p 缺 ttm:agent    : {len(re.findall(r'<p (?![^>]*ttm:agent)', text))}")
    print(f"p 缺 itunes:key   : {len(re.findall(r'<p (?![^>]*itunes:key)', text))}")
    print(f"整行只有 x-bg 的 p: {len(re.findall(r'<p [^>]*><span ttm:role=\"x-bg\">.*?</span></p>', text))}")
    print(f"含 XML 声明/BOM   : {'BOM!' if text.startswith(chr(0xFEFF)) else 'ok'}")
    print("严格校验          :", res.summary())
    for e in res.errors[:5]:
        print(f"  ✗ [{e.where}] {e.issue}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
