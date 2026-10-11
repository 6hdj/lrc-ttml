"""用真实 report.json 生成一份"背景人声已合并"的参考样例，方便直接丢进播放器验证。"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from matcher import AlignedLine, Unit                      # noqa: E402
from ttml_check import check_ttml                           # noqa: E402
from ttml_writer import TTMLWriter                          # noqa: E402

SRC = "outputs/在出发之前.report.json"
OUT = "outputs/在出发之前.bg-merged.ttml"

data = json.load(open(SRC, encoding="utf-8"))


def to_lines(records):
    out = []
    for rec in records:
        units = []
        for i, u in enumerate(rec["units"]):
            units.append(Unit(core=u["text"], index=i, is_bg=bool(u.get("bg")),
                              start=u.get("start"), end=u.get("end"),
                              matched=bool(u.get("matched"))))
        out.append(AlignedLine(text=rec["text"], agent="v1", units=units,
                               begin=rec["begin"], end=rec["end"],
                               matched=sum(1 for u in units if u.matched), total=len(units)))
    return out


lines = to_lines(data["lines"])
print(f"原始 {len(lines)} 行")

# 与 preprocess.merge_background_lines 同规则：整行背景 → 并进上一主唱行
merged = []
for line in lines:
    all_bg = bool(line.units) and all(u.is_bg for u in line.units)
    target = None
    if all_bg:
        for cand in reversed(merged):
            if not (cand.units and all(u.is_bg for u in cand.units)):
                target = cand
                break
        if target is not None:
            a = target.begin or 0.0
            b = line.begin or a
            if (b - a) > 12.0:
                target = None
    if target is None:
        merged.append(line)
        continue
    target.units = list(target.units) + list(line.units)
    target.text = target.text + line.text
    target.end = max(target.end, line.end)

print(f"合并后 {len(merged)} 行（并入 {len(lines) - len(merged)} 行背景人声）")

ttml = TTMLWriter().write(
    merged, title="在出发之前", artists=["陶典", "hanser", "爆裂菊是也", "HOYO-MiX"],
    album="在出发之前", language="zh-Hans", duration=data.get("duration") or 288.0,
    platform_ids={"ncmMusicId": "1791466447"},
)
open(OUT, "w", encoding="utf-8", newline="\n").write(ttml)

res = check_ttml(ttml, strict=True)
print(f"写入 {OUT}")
print("严格校验：", res.summary())
print("x-bg 次数        :", ttml.count("x-bg"))
print("整行只有 x-bg 的 <p>:", len(re.findall(r'<p [^>]*><span ttm:role="x-bg">.*?</span></p>', ttml)))
print("时间戳样例       :", re.search(r'begin="([^"]+)"', ttml).group(1))
sys.exit(0 if res.ok else 1)
