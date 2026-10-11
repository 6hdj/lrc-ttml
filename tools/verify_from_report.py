"""用真实转换报告重建 AlignedLine，跑新 writer，做独立的结构 + 时间校验。"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from matcher import AlignedLine, Unit                      # noqa: E402
from ttml_check import check_ttml                          # noqa: E402
from ttml_writer import TTMLWriter, is_valid_time_expression  # noqa: E402

NS_TT = "{http://www.w3.org/ns/ttml}tt"
NS_TT_DIV = "{http://www.w3.org/ns/ttml}div"
NS_TT_P = "{http://www.w3.org/ns/ttml}p"
NS_TT_SPAN = "{http://www.w3.org/ns/ttml}span"
NS_TTM = "{http://www.w3.org/ns/ttml#metadata}"


def load_report(path):
    data = json.load(open(path, encoding="utf-8"))
    lines = []
    for line in data["lines"]:
        units = []
        for i, u in enumerate(line["units"]):
            units.append(Unit(core=u["text"], index=i, is_bg=bool(u.get("bg")),
                              start=u.get("start"), end=u.get("end"),
                              matched=bool(u.get("matched"))))
        lines.append(AlignedLine(text=line["text"], agent="v1", units=units,
                                 begin=line["begin"], end=line["end"],
                                 matched=sum(1 for u in units if u.matched),
                                 total=len(units)))
    return data, lines


def secs(t):
    parts = str(t).split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def independent_check(text, label):
    """不依赖 ttml_check 的独立校验：well-formed + 时间表达式 + 区间包含。"""
    problems = []
    root = ET.fromstring(text)
    if root.tag != NS_TT:
        problems.append(f"根元素命名空间错误: {root.tag}")
    if not root.get("{http://www.w3.org/XML/1998/namespace}lang"):
        problems.append("缺少 xml:lang")
    for div in root.iter(NS_TT_DIV):
        for p in div.findall(NS_TT_P):
            pb, pe = secs(p.get("begin")), secs(p.get("end"))
            if db_pe := div.get("end"):
                if pb < secs(div.get("begin")) - 1e-6 or pe > secs(db_pe) + 1e-6:
                    problems.append("p 超出 div")
            for sp in p.iter(NS_TT_SPAN):
                if sp.get("begin") is None:
                    continue                      # x-bg / x-translation 包层
                sb, se = secs(sp.get("begin")), secs(sp.get("end"))
                if not is_valid_time_expression(sp.get("begin")) or \
                   not is_valid_time_expression(sp.get("end")):
                    problems.append(f"span 时间非法: {sp.get('begin')}~{sp.get('end')}")
                if se <= sb:
                    problems.append(f"span 时长非正: {sp.get('begin')}~{sp.get('end')}")
                if sb < pb - 1e-6 or se > pe + 1e-6:
                    problems.append(f"span 超出 p: {sp.get('begin')}~{sp.get('end')} in {p.get('begin')}~{p.get('end')}")
    for m in re.findall(r'(?:begin|end|dur)="([^"]+)"', text):
        if not is_valid_time_expression(m):
            problems.append(f"非 TTML1 时间表达式: {m!r}")
    print(f"  [{label}] 独立校验：{'OK' if not problems else 'FAIL'}")
    for pr in problems[:6]:
        print("      -", pr)
    return not problems


CONFIGS = [
    ("amll-default", TTMLWriter()),
    ("amll-mmss", TTMLWriter(time_format="mmss")),
    ("amll-seconds", TTMLWriter(time_format="seconds")),
    ("amll-inline-tr", TTMLWriter(translation_style="inline", markers="both")),
    ("amll-duet-roles", TTMLWriter(markers="both")),
    ("apple-songpart", TTMLWriter(mode="apple", song_part="Verse")),
]

fail = 0
for report in ("outputs/在出发之前.report.json", "outputs/它像一颗.report.json"):
    if not os.path.exists(report):
        print("skip missing", report)
        continue
    data, lines = load_report(report)
    title = os.path.basename(report).replace(".report.json", "")
    print(f"\n=== {report}（{len(lines)} 行）===")
    for label, writer in CONFIGS:
        text = writer.write(lines, title=title, artists=["Hanser"], album=title,
                            language="zh-Hans", duration=data.get("duration") or 300.0,
                            platform_ids={"ncmMusicId": "1"}, author="tester", author_id="1")
        strict_ok = is_amll = True
        strict_ok = check_ttml(text, strict=True).ok
        if label in ("amll-mmss", "amll-seconds"):
            is_amll = check_ttml(text, strict=False).ok
        ok = independent_check(text, label)
        res = check_ttml(text, strict=("mmss" not in label and "seconds" not in label))
        print(f"      checker(strict={('mmss' not in label and 'seconds' not in label)}): "
              f"{len(res.errors)} 错误 / {len(res.warnings)} 警告 -> {res.summary()}")
        for e in res.errors[:4]:
            print("      !", e.where, e.issue)
        if not ok:
            fail += 1
        out = os.path.join(".tmp", f"regen-{title}-{label}.ttml")
        open(out, "w", encoding="utf-8", newline="\n").write(text)

print("\n独立校验失败数:", fail)
sys.exit(1 if fail else 0)
