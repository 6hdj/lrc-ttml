#!/usr/bin/env python3
"""timing_align 纯函数回归（不加载 TIFA 模型）。

覆盖：
- TextGrid texts tier 解析
- TIFA 逐字边界 → 原文字符映射（含空格/标点）
- scan_line 单元定位（空格不会让后续字错位）
- 同 agent 行重叠按比例压缩

运行：python tools/test_timing_align.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from timing_align import (  # noqa: E402
    _build_line, _enforce_agent_monotonic, _map_tifa_line, _parse_textgrid,
)
from matcher import AlignedLine, Unit  # noqa: E402

PASS = []


def ok(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"FAIL {name} {detail}")
    PASS.append(name)
    print(f"  OK   {name}" + (f"  —— {detail}" if detail else ""))


def test_textgrid():
    sample = '''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0.0
xmax = 4.13
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "texts"
        xmin = 0
        xmax = 4.13
        intervals: size = 2
            intervals [1]:
                xmin = 0.0
                xmax = 1.0
                text = "站"
            intervals [2]:
                xmin = 1.0
                xmax = 2.0
                text = "在"
'''
    with tempfile.NamedTemporaryFile("w", suffix=".TextGrid", delete=False,
                                     encoding="utf-8") as f:
        f.write(sample)
        path = f.name
    try:
        from pathlib import Path
        tiers = _parse_textgrid(Path(path))
        ok("TextGrid 解析", tiers.get("texts") == [(0.0, 1.0, "站"), (1.0, 2.0, "在")],
           str(tiers.get("texts")))
    finally:
        os.unlink(path)


def test_mapping_with_spaces():
    text = "面对 下一个 岔路口"
    intervals = [(0.0, 0.4, "面"), (0.4, 0.9, "对"), (0.9, 1.2, "下"),
                 (1.2, 1.5, "一"), (1.5, 2.0, "个"), (2.0, 2.6, "岔"),
                 (2.6, 3.0, "路"), (3.0, 3.8, "口")]
    ct, matched = _map_tifa_line(text, intervals, 10.0, (10.0, 14.0), print)
    ok("TIFA 映射成功", matched)
    ok("空格/标点不参与发音", ct[2] is not None and ct[6] is not None)
    ok("首字时间", ct[0] == (10.0, 10.4), str(ct[0]))
    line = _build_line(text, "v1", "", 0, ct, True, (10.0, 14.0))
    # 空格的 unit 被 scan_line 折叠，不能让后续字错位
    ok("含空格行映射", line.units[2].core == "下" and line.units[2].start == 10.9,
       f"{line.units[2].core} {line.units[2].start}")
    ok("行尾字", line.units[-1].core == "口" and line.units[-1].end == 13.8,
       f"{line.units[-1].core} {line.units[-1].end}")


def test_mismatch():
    ct, matched = _map_tifa_line("你好", [(0.0, 1.0, "再见")], 0.0, (0.0, 2.0), print)
    ok("不匹配不伪造", (not matched) and ct is None)


def test_english_case_and_words():
    text = "Hello world, this is a test."
    intervals = [(0.0, 0.5, "hello"), (0.5, 1.0, "world"),
                 (1.2, 1.5, "this"), (1.5, 1.7, "is"),
                 (1.7, 1.9, "a"), (1.9, 2.2, "test")]
    ct, matched = _map_tifa_line(text, intervals, 0.0, (0.0, 2.2), print)
    ok("英文大小写无关映射", matched)
    ok("英文 Hello 起始", ct and ct[0] == (0.0, 0.1), str(ct[0] if ct else None))
    # 多字 word 内部按字符均分，不能所有字符同一区间（否则同层字重叠）
    ok("英文多字词不共用区间", ct and ct[1][0] >= ct[0][1] and ct[2][0] >= ct[1][1])


def test_japanese_mapping():
    text = "こんにちは世界"
    intervals = [(0.0, 0.3, "こ"), (0.3, 0.6, "ん"), (0.6, 0.9, "に"),
                 (0.9, 1.2, "ち"), (1.2, 1.5, "は"), (1.5, 1.8, "世"), (1.8, 2.1, "界")]
    ct, matched = _map_tifa_line(text, intervals, 0.0, (0.0, 2.1), print)
    ok("日文映射", matched and ct and ct[0] == (0.0, 0.3), str(ct[0] if ct else None))


def test_line_language():
    from timing_align import _line_language
    ok("语言检测 en", _line_language("hello world") == "en")
    ok("语言检测 zh", _line_language("你好世界") == "zh")
    ok("语言检测 ja", _line_language("こんにちは") == "ja")
    ok("语言检测 zh-en", _line_language("hello 世界") == "zh-en")


def test_vocal_end():
    import numpy as np
    from timing_align import _vocal_end
    sr = 16000
    # 前 1.0s 有声（正弦），后面 3s 静音：应裁到 1.0s 附近
    t = np.arange(int(1.0 * sr)) / sr
    a = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    samples = np.concatenate([a, np.zeros(int(3.0 * sr), np.float32)])
    end = _vocal_end(samples, sr, 0.0, 4.0)
    ok("间奏静音裁剪", 0.95 <= end <= 1.1, f"end={end:.3f}")


def test_layered_lyrics():
    from matcher import AlignedLine, Unit
    from ttml_writer import TTMLWriter
    from ttml_check import check_ttml
    l1 = AlignedLine(text="主(和)", agent="v1", begin=0.0, end=3.5, units=[
        Unit(core="主", start=0.0, end=2.0),
        Unit(core="和", start=2.5, end=3.5, is_bg=True)])
    l2 = AlignedLine(text="副", agent="v1", begin=1.5, end=2.8,
                     units=[Unit(core="副", start=1.5, end=2.8)])
    strict = TTMLWriter(mode="amll", line_id="itunes:key").write(
        [l1, l2], title="t", artists=["a"], duration=4.0)
    layered = TTMLWriter(mode="amll", line_id="itunes:key", layered=True).write(
        [l1, l2], title="t", artists=["a"], duration=4.0)
    ok("分层：严格模式仍报同行重叠",
       bool(check_ttml(strict, strict=False).errors))
    ok("分层：layered 模式允许同行重叠/和声出界",
       not check_ttml(layered, strict=False, layered=True).errors)


def test_fuzzy_mapping():
    from timing_align import _map_tifa_line
    times, matched = _map_tifa_line(
        "abc", [(0.0, 1.0, "a"), (1.0, 2.0, "c")], 0.0, (0.0, 2.0), lambda m: None)
    ok("TIFA 漏字模糊映射", matched and times is not None
       and times[0] == (0.0, 1.0) and times[2] == (1.0, 2.0)
       and times[1][0] == 1.0 and times[1][1] == 1.0 + 0.001,
       f"{times}")


def test_phone_split():
    from timing_align import _map_tifa_line
    times, matched = _map_tifa_line(
        "かな", [(0.0, 1.0, "かな")], 0.0, (0.0, 1.0), lambda m: None,
        phones=[(0.0, 0.2, "k"), (0.2, 0.5, "a"),
                (0.5, 0.7, "n"), (0.7, 1.0, "a")])
    ok("多字词按音素切分", matched and times is not None
       and abs(times[0][0] - 0.0) < 1e-6 and abs(times[0][1] - 0.5) < 1e-6
       and abs(times[1][0] - 0.5) < 1e-6 and abs(times[1][1] - 1.0) < 1e-6,
       f"{times}")


def test_auto_harmony():
    from asr import AsrWord
    from matcher import AlignedLine, AlignReport, Unit
    from timing_align import _add_auto_harmony
    line = AlignedLine(text="主", agent="v1", begin=0.0, end=2.0,
                       units=[Unit(core="主", start=0.0, end=1.0)])
    report = AlignReport([line], 1, 1, True)
    words = [AsrWord(text="主", start=0.0, end=1.0, prob=0.9),
             AsrWord(text="和", start=0.5, end=0.8, prob=0.9),
             AsrWord(text="声", start=0.8, end=1.2, prob=0.9)]
    notes = []
    added = _add_auto_harmony(report, words, notes, lambda m: None)
    ok("自动转录和声写入 x-bg", added == 2 and len(line.bg_units) == 2,
       f"added={added} bg={len(line.bg_units)}")
    ok("自动和声有说明", any("自动转录和声" in n for n in notes))


def test_bg_merge():
    from matcher import AlignedLine, AlignReport, Unit
    from timing_align import _merge_background_timings
    main = Unit(core="主", start=0.0, end=1.0)
    tbg = Unit(core="和", start=1.0, end=4.0, is_bg=True)
    line = AlignedLine(text="主(和)", agent="v1", units=[main, tbg], begin=0.0, end=4.0)
    wbg = Unit(core="和", start=1.1, end=1.8, is_bg=True)
    wline = AlignedLine(text="主(和)", agent="v1", units=[Unit(core="主", start=0.9, end=1.9), wbg], begin=0.9, end=1.9)
    _merge_background_timings(
        AlignReport([line], 2, 2, True), AlignReport([wline], 2, 2, True), [], lambda m: None)
    ok("和声时间来自 Whisper", line.units[-1].start == 1.1 and line.units[-1].end == 1.8,
       f"{line.units[-1].start}-{line.units[-1].end}")
    ok("主唱时间保持 TIFA", line.units[0].start == 0.0 and line.units[0].end == 1.0)


def test_monotonic():
    a = AlignedLine(text="a", agent="v1", begin=0.0, end=10.0,
                    units=[Unit(core="a", start=0.0, end=10.0)])
    b = AlignedLine(text="b", agent="v1", begin=5.0, end=8.0,
                    units=[Unit(core="b", start=5.0, end=8.0)])
    notes = []
    fixed = _enforce_agent_monotonic([a, b], notes, lambda m: None)
    ok("同 agent 重叠压缩", fixed == 1 and abs(a.end - 5.0) < 1e-9,
       f"fixed={fixed} a.end={a.end}")
    ok("压缩后字仍在行内", a.units[0].start >= 0 and a.units[0].end <= a.end + 1e-9)
    # 上一行整行都在下一行之后（注释行插错位置）：必须压成占位而不是留重叠
    c = AlignedLine(text="c", agent="v1", begin=10.0, end=10.5,
                    units=[Unit(core="c", start=10.0, end=10.5)])
    d = AlignedLine(text="d", agent="v1", begin=9.8, end=11.0,
                    units=[Unit(core="d", start=9.8, end=11.0)])
    _enforce_agent_monotonic([c, d], [], lambda m: None)
    ok("整行在下一行之后也能去重叠", c.end <= d.begin + 1e-9 and c.units[0].end <= c.end + 1e-9,
       f"c.end={c.end} d.begin={d.begin}")


def main():
    test_textgrid()
    test_mapping_with_spaces()
    test_mismatch()
    test_english_case_and_words()
    test_japanese_mapping()
    test_line_language()
    test_vocal_end()
    test_phone_split()
    test_layered_lyrics()
    test_fuzzy_mapping()
    test_auto_harmony()
    test_bg_merge()
    test_monotonic()
    print(f"\n全部通过 ✓（{len(PASS)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
