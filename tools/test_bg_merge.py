"""验证：整行括号的背景人声会并进相邻主唱行，并由 writer 输出为同一个 <p> 里的 x-bg。"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from matcher import AlignedLine, LyricMatcher, Unit          # noqa: E402
from preprocess import merge_background_lines, parse_lyrics  # noqa: E402
from ttml_check import check_ttml                             # noqa: E402
from ttml_writer import TTMLWriter                            # noqa: E402

fails = []


def ok(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        fails.append(name)


LRC = """[00:05.00]有你为我践行
[00:07.00]（一路践行）
[00:09.00]那些美好或悲伤的记忆
[00:30.00]远处的回声
[00:01.00]（开场和声）
[00:02.00]第一句主唱
"""

print("[1] parse_lyrics 合并整行括号的背景人声")
lines = parse_lyrics(LRC)
for l in lines:
    print(f"   {l.line_start:>6} ~ {l.line_end}  bg_only={all(t.is_background for t in l.tokens)}  {l.text!r}")
ok("行数从 6 减到 4", len(lines) == 4, str(len(lines)))
merged_second = [l for l in lines if "一路践行" in l.text]
ok("（一路践行）并进了上一行", len(merged_second) == 1 and "有你为我践行" in merged_second[0].text,
   str([l.text for l in lines]))
ok("合并后行尾覆盖到背景人声结束", merged_second[0].line_end == 9.0, str(merged_second[0].line_end))
bg_tokens = [t for t in merged_second[0].tokens if t.is_background]
ok("背景 token 仍带 is_background 标记", [t.text for t in bg_tokens] == list("一路践行"),
   str([t.text for t in bg_tokens]))
leading = lines[0]
ok("开头的背景行并入下一主唱行且放在最前", leading.text == "（开场和声）第一句主唱", repr(leading.text))
ok("合并后起始时间是背景行的时间", leading.line_start == 1.0, str(leading.line_start))
ok("远隔的整行括号不合并", any(l.text.startswith("（") is False and "远处的回声" in l.text for l in lines))

print("\n[2] 背景行时间跨度太大时保持独立")
far = parse_lyrics("[00:00.00]主唱一句\n[00:40.00]（很远的和声）\n")
ok("超过 12s 不合并", len(far) == 2, str([l.text for l in far]))

print("\n[3] 对齐 + writer：背景人声落在同一个 <p> 里")


def mk_unit(text, start, end, bg=False):
    return Unit(core=text, start=start, end=end, is_bg=bg, matched=True)


main_units = [mk_unit("有", 5.0, 5.4), mk_unit("你", 5.4, 5.8)]
bg_units = [mk_unit("一", 7.0, 7.3, True), mk_unit("路", 7.3, 7.6, True),
            mk_unit("践", 7.6, 7.9, True), mk_unit("行", 7.9, 8.2, True)]
al = AlignedLine(text="有你为我践行（一路践行）", agent="v1", units=main_units + bg_units,
                 begin=5.0, end=8.2)
ttml = TTMLWriter().write([al], title="t", artists=["A"], language="zh-Hans", duration=20.0)
p = re.search(r"<p .*?</p>", ttml).group(0)
ok("仍然是同一个 <p>（没有拆行）", ttml.count("<p ") == 1, str(ttml.count("<p ")))
ok("主唱 span 在前、x-bg 在后", p.index("有") < p.index('ttm:role="x-bg"'))
ok("x-bg 用半角括号包住", "(一" in p and "行)" in p, p[:200])
ok("自检通过", check_ttml(ttml).ok, str([e.issue for e in check_ttml(ttml).errors[:3]]))

print("\n[4] 真实歌词文件")
real = "uploads/1791466455_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.lrc"
if os.path.exists(real):
    raw = open(real, encoding="utf-8").read()
    before = parse_lyrics.__wrapped__ if hasattr(parse_lyrics, "__wrapped__") else None
    lines = parse_lyrics(raw)
    bg_only = [l for l in lines if all(t.is_background for t in l.tokens)]
    print(f"   合并后共 {len(lines)} 行，仍整行是背景的：{len(bg_only)} 行")
    ok("真实文件里不再有整行背景的独立行", not bg_only, str([l.text for l in bg_only][:3]))
    hit = [l for l in lines if "一路践行" in l.text]
    ok("（一路践行）已并入主唱行", hit and "有你为我践行" in hit[0].text, str([l.text for l in hit]))

print("\n[5] 合并后对齐：背景人声仍落在自己的时间窗里")
from asr import AsrWord  # noqa: E402

lrc = "[00:05.00]有你为我践行\n[00:07.00]（一路践行）\n[00:10.00]下一句歌词\n"
aligned = LyricMatcher(log=lambda *a: None).align(
    parse_lyrics(lrc),
    [AsrWord("你", 5.0, 5.4), AsrWord("为", 5.4, 5.8), AsrWord("我", 5.8, 6.2),
     AsrWord("践", 6.2, 6.6), AsrWord("行", 6.6, 6.9),
     AsrWord("一", 7.0, 7.3), AsrWord("路", 7.3, 7.6), AsrWord("践", 7.6, 7.9),
     AsrWord("行", 7.9, 8.2),
     AsrWord("下", 10.0, 10.4), AsrWord("一", 10.4, 10.8), AsrWord("句", 10.8, 11.2)],
    total_duration=15.0)
line0 = aligned.lines[0]
bg = list(line0.bg_units)
ok("合并后的行同时有主唱和背景", bool(bg) and bool(line0.main_units),
   f"main={len(line0.main_units)} bg={len(bg)}")
ok("背景人声时间落在 7.0s 之后", bool(bg) and bg[0].start >= 6.9,
   str([(u.core, u.start) for u in bg]))
ok("背景人声仍在整行区间内",
   bool(bg) and bg[0].start >= line0.begin - 1e-6 and bg[-1].end <= line0.end + 1e-6,
   f"{line0.begin}~{line0.end} vs {bg[0].start}~{bg[-1].end}" if bg else "no bg")

ttml2 = TTMLWriter().write(aligned.lines, title="t", artists=["A"], language="zh-Hans",
                           duration=15.0)
ok("输出里没有整行只有 x-bg 的 <p>",
   not re.search(r'<p [^>]*><span ttm:role="x-bg">.*?</span></p>', ttml2), ttml2[:200])
ok("输出通过严格自检", check_ttml(ttml2, strict=True).ok,
   str([e.issue for e in check_ttml(ttml2).errors[:3]]))

print("\n失败：", fails if fails else "无")
sys.exit(1 if fails else 0)
