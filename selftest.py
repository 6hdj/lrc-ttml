"""
离线自检脚本（不需要音频、不需要网络、不需要 pytest）：

    python selftest.py

覆盖：
1. 歌词切分（标点归属、背景人声、中英混排空格）
2. 动态规划对齐（同音字、漏字、多字、重复句）
3. LRC 锚定模式 / 纯文本全局模式
4. TTML 生成 + 确定性自检
"""
from __future__ import annotations

import random
import re
import shutil
import subprocess
import sys
from typing import List, Tuple

from asr import AsrWord
from matcher import Unit, align_sequences, scan_line
from preprocess import parse_lyrics
from ttml_check import check_ttml
from ttml_writer import (TTMLWriter, format_time, is_amll_relaxed_time,
                         is_valid_time_expression, parse_time)
from matcher import LyricMatcher

FAILED: List[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} {detail}")
        FAILED.append(f"{name} {detail}")


# --------------------------------------------------------------------------
def test_scan_line():
    print("\n[1] 歌词切分")
    u = scan_line("站在旅途的尽头")
    check("中文按字切分", [x.core for x in u] == list("站在旅途的尽头"), str([x.core for x in u]))

    u = scan_line("有你为我践行(一路践行)")
    check("括号识别为背景人声",
          [x.core for x in u if x.is_bg] == list("一路践行") and not u[0].is_bg)

    u = scan_line("想一起书写更多的奇迹，")
    check("标点附在前一个字上", u[-1].core == "迹" and u[-1].trailing == "，", repr(u[-1]))

    u = scan_line("Hello beautiful world")
    check("英文按词切分", [x.core for x in u] == ["Hello", "beautiful", "world"], str([x.core for x in u]))
    check("英文单词之间的空格保留", u[1].leading == " " and u[2].leading == " ", repr([x.leading for x in u]))

    u = scan_line("面对 下一个 岔路口")
    check("中文之间的分隔空格不输出", all(x.leading == "" for x in u), repr([x.leading for x in u]))

    u = scan_line("想去 see the world")
    check("中英混排保留空格", [x.core for x in u] == ["想", "去", "see", "the", "world"]
          and u[2].leading == " ", str([(x.core, x.leading) for x in u]))

    u = scan_line('"我们 就在这里 "')
    check("引号不作为独立单元", [x.core for x in u] == list("我们就在这里"), str([x.core for x in u]))


def test_dp():
    print("\n[2] 动态规划对齐")
    targets = [Unit(core=c) for c in "站在旅途的尽头"]
    sources = [Unit(core=c) for c in "站在旅途的尽头"]
    m = align_sequences(targets, sources)
    check("完全一致时一一对应", m == list(range(len(targets))), str(m))

    # 同音字（不同字）仍然应该对齐，而不是被当成插入/删除
    targets = [Unit(core=c) for c in "你曾害怕做不到的事情"]
    sources = [Unit(core=c) for c in "不再害怕做不到的事情"]
    m = align_sequences(targets, sources)
    check("错别字仍能对齐", sum(1 for x in m if x is not None) >= len(targets) - 2, str(m))

    targets = [Unit(core=c) for c in "站在旅途的尽头"]
    sources = [Unit(core=c) for c in "站在旅途的尽头站在旅途的尽头"]
    m = align_sequences(targets, sources)
    check("识别重复整句时对齐到先出现的那一遍",
          m == list(range(len(targets))), str(m))

    targets = [Unit(core=c) for c in "站在旅途的尽头"]
    sources = [Unit(core=c) for c in "站旅途尽头"]
    m = align_sequences(targets, sources)
    check("漏字时能跳过", sum(1 for x in m if x is None) == 2, str(m))


# --------------------------------------------------------------------------
def synth_words(lines: List[Tuple[float, str]], char_dur: float = 0.35,
                seed: int = 7, noise: bool = True, repeat: bool = True) -> List[AsrWord]:
    """模拟 faster-whisper 的输出：中文逐字、英文整词、带错字/漏字/复读"""
    rnd = random.Random(seed)
    pool = "的一是不了在人有我他这为之大来以个中上们"
    out: List[AsrWord] = []
    for start, text in lines:
        tokens: List[str] = []
        buf = ""
        for ch in text:
            if ch.isspace() or ch in "（）()，。！？、\"'":
                if buf:
                    tokens.append(buf)
                    buf = ""
                continue
            if ch.isascii() and ch.isalnum():
                buf += ch
                continue
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        if buf:
            tokens.append(buf)

        t = start
        for tok in tokens:
            dur = char_dur * len(tok)
            if noise and len(tok) == 1 and rnd.random() < 0.10:
                tok = rnd.choice(pool)          # 模拟识别错字/同音字
            if noise and rnd.random() < 0.07:
                t += dur                        # 模拟漏字（留出空档）
                continue
            out.append(AsrWord(tok, round(t, 3), round(t + dur, 3), 0.9))
            t += dur
        if noise and repeat and rnd.random() < 0.3:
            for tok in tokens[:3]:              # 模拟 whisper 复读
                dur = char_dur * len(tok)
                out.append(AsrWord(tok, round(t, 3), round(t + dur, 3), 0.4))
                t += dur
    out.sort(key=lambda w: (w.start, w.end))
    return out


def build_lrc(lines: List[Tuple[float, str]]) -> str:
    out = ["[ti:自检歌曲]", "[ar:测试]"]
    for i, (start, text) in enumerate(lines):
        end = lines[i + 1][0] if i + 1 < len(lines) else start + 6.0
        out.append(f"[{int(start // 60):02d}:{start % 60:05.2f}]{text}")
    return "\n".join(out)


def test_end_to_end():
    print("\n[3] 端到端对齐（合成数据）")
    truth = [
        (5.0, "站在旅途的尽头"),
        (10.0, "面对下一个岔路口"),
        (18.0, "张开手心它看似很空"),
        (25.0, "其实已包含所有"),
        (32.0, "一次次向后回望想起最初的模样"),
        (45.0, "目光的方向"),
        (49.0, "染上了霞光"),
        (53.0, "却还是不肯翻开下一幅篇章"),
        (60.0, "不再害怕做不到的事情"),
        (67.0, "有你为我践行(一路践行)"),
        (73.0, "Ho yo mix 你好 world"),
    ]
    lyrics = build_lrc(truth)
    words = synth_words(truth)

    lines = parse_lyrics(lyrics)
    matcher = LyricMatcher(log=lambda *a: None)
    report = matcher.align(lines, words, total_duration=90.0)

    check("LRC 锚定模式被识别", report.anchored)
    check("字级匹配率 > 85%", report.match_rate > 0.85, f"实际 {report.match_rate:.1%}")
    check("每行都能生成时间", all(l.end > l.begin for l in report.lines))

    # 时间精度：把每行的每个"发声字符"按 0.35s 匀速排布，得到真值起点
    def worst_error(rep, noise_desc):
        worst = 0.0
        for (start, text), al in zip(truth, rep.lines):
            expected, t = [], start
            for ch in text:
                if ch.isspace() or ch in "（）()，。！？、\"'":
                    continue
                expected.append(t)
                t += 0.35
            k = 0
            for u in al.units:
                if u.is_bg:
                    continue
                if k >= len(expected):
                    break
                if u.matched:
                    worst = max(worst, abs(u.start - expected[k]))
                k += len(u.core)
        return worst

    clean_words = synth_words(truth, noise=False)
    report_clean = matcher.align(parse_lyrics(lyrics), clean_words, total_duration=90.0)
    w_clean = worst_error(report_clean, "clean")
    check("无噪声时匹配率 100%", report_clean.match_rate > 0.99, f"{report_clean.match_rate:.1%}")
    check("无噪声时时间误差 < 0.02s", w_clean < 0.02, f"最大 {w_clean:.3f}s")

    w_noisy = worst_error(report, "noisy")
    check("有噪声（17% 错字/漏字）时误差 < 1.2s", w_noisy < 1.2, f"最大 {w_noisy:.3f}s")

    # 行不重叠
    bad = 0
    for a, b in zip(report.lines, report.lines[1:]):
        if max(u.end for u in a.units) > min(u.start for u in b.units) + 0.001:
            bad += 1
    check("相邻行不重叠", bad == 0, f"{bad} 处重叠")

    # 背景人声
    bg_line = report.lines[9]
    check("背景人声被标记", any(u.is_bg for u in bg_line.units) and len(bg_line.bg_units) == 4,
          str([u.core for u in bg_line.bg_units]))

    # 行内：按文本顺序必须单调递增，且不越出行的 begin/end
    bad = []
    for al in report.lines:
        prev_end = None
        for u in al.units:
            if u.start is None or u.end is None:
                bad.append(f"{al.text[:8]} 缺时间")
                break
            if u.start < al.begin - 0.001 or u.end > al.end + 0.001:
                bad.append(f"{al.text[:8]} 越界 {u.text}")
                break
            if prev_end is not None and u.start < prev_end - 0.001:
                bad.append(f"{al.text[:8]} 时间倒挂 {u.text}")
                break
            if u.end <= u.start:
                bad.append(f"{al.text[:8]} 时长为负 {u.text}")
                break
            prev_end = u.end
    check("行内按文本顺序单调且不越界", not bad, "; ".join(bad[:3]))

    # 识别时间戳互相重叠时（whisper DTW 常见）仍要产出合法结果
    overlapped = synth_words(truth, noise=False)
    for w in overlapped:
        w.start = max(0.0, w.start - 0.08)
    rep_ov = matcher.align(lines, overlapped, total_duration=90.0)
    ttml_ov = TTMLWriter().write(rep_ov.lines, title="t", language="zh-Hans", duration=90.0)
    check("ASR 时间戳重叠时输出仍合法", check_ttml(ttml_ov).ok,
          str([e.__dict__ for e in check_ttml(ttml_ov).errors[:3]]))

    print("\n[4] 纯文本（无时间戳）全局对齐")
    plain = "\n".join(t for _, t in truth)
    lines2 = parse_lyrics(plain)
    report2 = matcher.align(lines2, words, total_duration=90.0)
    check("纯文本模式可用", not report2.anchored and report2.match_rate > 0.8, f"{report2.match_rate:.1%}")

    print("\n[5] TTML 生成 + 自检")
    writer = TTMLWriter(mode="amll")
    ttml = writer.write(report.lines, title="自检歌曲", artists=["测试歌手"], album="测试专辑",
                        language="zh-Hans", platform_ids={"ncmMusicId": "123"}, author="tester",
                        duration=90.0)
    res = check_ttml(ttml)
    check("AMLL 模式自检通过", res.ok, str([e.__dict__ for e in res.errors[:3]]))
    check("TTML 行数正确", res.stats.get("lines") == len(truth), str(res.stats))
    check("背景人声 x-bg 存在", 'ttm:role="x-bg"' in ttml)
    check("根元素带 itunes:timing", 'itunes:timing="Word"' in ttml)
    check("无 BOM", not ttml.startswith("\ufeff"))

    ttml_apple = TTMLWriter(mode="apple").write(report.lines, title="自检歌曲", artists=["测试歌手"],
                                                language="zh-Hans", duration=90.0)
    res2 = check_ttml(ttml_apple)
    check("Apple 模式自检通过", res2.ok, str([e.__dict__ for e in res2.errors[:3]]))
    check("Apple 模式使用 Apple/AMLL 官方命名空间",
          'xmlns:itunes="http://music.apple.com/lyric-ttml-internal"' in ttml_apple)
    check("只有一个唱段时不瞎猜 songPart", "itunes:songPart" not in ttml_apple)
    explicit_part = TTMLWriter(mode="apple", song_part="Chorus").write(
        report.lines, title="自检歌曲", language="zh-Hans", duration=90.0)
    check("显式指定 songPart 时写入", 'itunes:songPart="Chorus"' in explicit_part)

    print("\n[6] 时间码（W3C TTML1 §10.3.1）")
    check("format_time 基本格式", format_time(65.4321) == "00:01:05.432", format_time(65.4321))
    check("format_time 进位", format_time(59.9999).startswith("00:01:00"), format_time(59.9999))
    check("parse_time 往返", abs(parse_time(format_time(123.456)) - 123.456) < 0.001)
    check("parse_time 支持 offset-time", abs(parse_time("1.5m") - 90.0) < 0.001)
    check("hh:mm:ss[.fff] 是合法 TTML1", is_valid_time_expression("00:01:05.432"))
    check("MM:SS.mmm 不是合法 TTML1", not is_valid_time_expression("01:05.432"))
    check("裸秒不是合法 TTML1", not is_valid_time_expression("12.5"))
    check("offset-time 必须带单位", is_valid_time_expression("12.5s") and is_valid_time_expression("250ms"))
    check("非法分钟秒数被拒", not is_valid_time_expression("00:75:00.000"))
    check("AMLL 宽松写法可被识别", is_amll_relaxed_time("01:05.432") and is_amll_relaxed_time("65.432"))

    strict_ttml = TTMLWriter().write(report.lines, title="时间码自检", language="zh-Hans", duration=90.0)
    times = re.findall(r'(?:begin|end|dur)="([^"]+)"', strict_ttml)
    bad_times = [t for t in times if not is_valid_time_expression(t)]
    check("输出里所有时间戳都是合法 TTML1", not bad_times, str(bad_times[:5]))
    check("严格模式下自检通过", check_ttml(strict_ttml, strict=True).ok,
          str([e.__dict__ for e in check_ttml(strict_ttml).errors[:3]]))


def _synth_voice(f0: float, formants, seconds: float, sr: int = 16000, seed: int = 0):
    """合成一段"人声"：谐波堆 + 共振峰包络，用来测试演唱者分段"""
    import numpy as np
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    sig = np.zeros_like(t)
    for h in range(1, 40):
        f = f0 * h
        if f > sr / 2 - 100:
            break
        amp = 0.0
        for fc, bw, g in formants:
            amp += g / (1.0 + ((f - fc) / bw) ** 2)
        if amp <= 1e-4:
            continue
        sig += (amp / h) * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    sig += 0.01 * rng.standard_normal(len(t))
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 1.3 * t)      # 轻微起伏，模拟咬字
    return (sig * env * 0.3).astype("float32")


def test_diarize():
    print("\n[10] 演唱者分段（CAM++ 声纹）")
    import os
    import numpy as np
    import diarize as dz

    if not dz.available():
        print("  (未找到 CAM++ 模型，跳过声纹测试)")
        check("无模型时自动分段应保守返回单人", dz.diarize(np.zeros(16000 * 4), [(0, 2), (2, 4)]).n_singers == 1)
        return

    import wave
    from speaker_campplus import cosine, embed

    md = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "campplus")

    def wav(name):
        with wave.open(os.path.join(md, name), "rb") as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        return a

    a, b, c = (wav("speaker1_a_cn_16k.wav"), wav("speaker1_b_cn_16k.wav"),
               wav("speaker2_a_cn_16k.wav"))

    s_same, s_diff = cosine(embed(a), embed(b)), cosine(embed(a), embed(c))
    check("同一人声纹相似度 > 阈值", s_same > dz.CAMPP_THRESHOLD, f"{s_same:.3f}")
    check("不同人声纹相似度 < 阈值", s_diff < dz.CAMPP_THRESHOLD, f"{s_diff:.3f}")
    check("两者差距 > 0.3", s_same - s_diff > 0.3, f"{s_same - s_diff:.3f}")

    def stack(segs):
        audio = np.concatenate(segs)
        spans, pos = [], 0.0
        for s in segs:
            spans.append((pos, pos + len(s) / 16000))
            pos += len(s) / 16000
        return audio, spans

    solo, sp1 = stack([a, b, a, b])
    r1 = dz.diarize(solo, sp1)
    check("同一个人 4 段 → 判定 1 个声部", r1.n_singers == 1, r1.reason)

    duet, sp2 = stack([a, c, b, c])
    r2 = dz.diarize(duet, sp2)
    check("两个人交替 → 判定 2 个声部", r2.n_singers == 2, r2.reason)
    check("两个人交替 → 分组完全正确", r2.labels == [0, 1, 0, 1], str(r2.labels))
    check("簇间相似度明显低于簇内", r2.sim_between + 0.25 < r2.sim_within,
          f"{r2.sim_within:.2f} vs {r2.sim_between:.2f}")

    r3 = dz.diarize(duet, sp2, n_singers=3)
    check("强制人数时按指定簇数输出", r3.n_singers == 3, str(sorted(set(r3.labels))))

    # 兜底路径（没有模型时）必须保守：不自动拆分
    fallback = dz._diarize_acoustic(solo, sp1, 0, 16000)
    check("兜底音高/音色路径不自动拆分", fallback.n_singers == 1, fallback.reason)
    forced = dz._diarize_acoustic(solo, sp1, 2, 16000)
    check("兜底路径支持强制人数", forced.n_singers == 2, str(forced.labels))


def test_apple_extensions():
    print("\n[7] 演唱者标记与 Apple 扩展（对唱 / 旁白 / 翻译 / xml:id）")
    from preprocess import detect_agent_switch, parse_lyrics

    raw = "\n".join([
        "[duet-a]你是否也在想我",
        "[duet-b]我一直在等你",
        "（Held）",
        "[narration]那一年的夏天",
        "[v1]回到最初的起点",
    ])
    lines = detect_agent_switch(parse_lyrics(raw))
    check("对唱 A 标记", (lines[0].agent, lines[0].role) == ("v1", "x-duet-a"),
          f"{lines[0].agent}/{lines[0].role}")
    check("对唱 B 标记", (lines[1].agent, lines[1].role) == ("v2", "x-duet-b"),
          f"{lines[1].agent}/{lines[1].role}")
    check("标记文本已从歌词中剥离", lines[1].text.startswith("我一直在等你"), lines[1].text)
    # 整行括号 = 背景人声：并进上一主唱行（AMLL：x-bg 是行的子内容，不能单独成行）
    check("整行括号（Held）并进上一行成为背景人声",
          any(t.is_background and t.text == "Held" for t in lines[1].tokens),
          str([(t.text, t.is_background) for t in lines[1].tokens]))
    check("背景行不再单独占一行", len(lines) == 4, str([l.text for l in lines]))
    check("旁白标记", (lines[2].agent, lines[2].role) == ("v3", "x-narration"),
          f"{lines[2].agent}/{lines[2].role}")

    from matcher import AlignedLine, Unit

    def mk(text, agent, role, start, translation=""):
        al = AlignedLine(text=text, agent=agent, begin=start, end=start + 0.4 * len(text),
                         role=role, translation=translation)
        for i, ch in enumerate(text):
            al.units.append(Unit(core=ch, index=i, start=start + 0.4 * i,
                                 end=start + 0.4 * (i + 1), matched=True))
        al.matched = al.total = len(text)
        return al

    als = [
        mk("你是否也在想我", "v1", "x-duet-a", 1.0, "Are you thinking of me too"),
        mk("我一直在等你", "v2", "x-duet-b", 5.0),
        mk("那一年的夏天", "v3", "x-narration", 9.0),
    ]

    ttml = TTMLWriter(mode="amll").write(
        als, title="对唱测试", artists=["歌手A", "歌手B", "旁白"], language="zh-Hans",
        duration=20.0, platform_ids={"ncmMusicId": "123"}, author="me", author_id="456")

    print("\n[8] AMLL TTML Database 规范符合性")
    check("含扩展标记的 TTML 自检通过", check_ttml(ttml).ok,
          str([e.__dict__ for e in check_ttml(ttml).errors[:3]]))
    check("itunes 命名空间与 Apple Music / AMLL 参考实现一致",
          'xmlns:itunes="http://music.apple.com/lyric-ttml-internal"' in ttml)
    check("声明了 xmlns:tts", 'xmlns:tts="http://www.w3.org/ns/ttml#styling"' in ttml)
    check("根元素带 itunes:timing=Word", 'itunes:timing="Word"' in ttml)
    check("p 上同时有 xml:id 与 itunes:key",
          'xml:id="L1"' in ttml and 'itunes:key="L1"' in ttml)
    check("每个 p 都有 ttm:agent", ttml.count("<p ") == ttml.count("ttm:agent="))
    check("album 必填（未给时自动用歌名）", '<amll:meta key="album" value="对唱测试"/>' in ttml)
    check("平台 ID 已写入", 'key="ncmMusicId" value="123"' in ttml)
    check("作者信息已写入",
          'key="ttmlAuthorGithub" value="456"' in ttml and 'key="ttmlAuthorGithubLogin" value="me"' in ttml)

    print("\n[9] 辅助歌词（翻译 / 对唱角色）")
    check("头部 iTunesMetadata 翻译",
          "<iTunesMetadata xmlns=\"http://music.apple.com/lyric-ttml-internal\">" in ttml
          and '<text for="L1">Are you thinking of me too</text>' in ttml)
    check("默认不写 Apple 内部对唱角色", "x-duet-a" not in ttml)

    both = TTMLWriter(mode="amll", markers="both").write(
        als, artists=["A", "B", "C"], language="zh-Hans", duration=20.0)
    check("markers=both 时写出 x-duet-a", 'ttm:role="x-duet-a"' in both)
    check("markers=both 时写出 x-narration", 'ttm:role="x-narration"' in both)
    check("markers=both 仍自检通过", check_ttml(both).ok,
          str([e.__dict__ for e in check_ttml(both).errors[:3]]))

    inline = TTMLWriter(mode="amll", translation_style="inline").write(
        als, artists=["A", "B", "C"], language="zh-Hans", duration=20.0)
    check("inline 翻译写法", 'ttm:role="x-translation"' in inline and "iTunesMetadata" not in inline)
    check("inline 翻译自检通过", check_ttml(inline).ok,
          str([e.__dict__ for e in check_ttml(inline).errors[:3]]))

    check("inline 翻译 & x-bg 都不带时间戳",
          'ttm:role="x-translation">' in inline)

    secs = TTMLWriter(mode="amll", time_format="seconds").write(
        als, artists=["A"], language="zh-Hans", duration=20.0)
    check("seconds 时间格式", '<p begin="1.000" end="3.800"' in secs, secs.split("\n")[-3][:80])
    check("seconds 只是 AMLL 宽松写法（非 TTML1）", not check_ttml(secs, strict=True).ok)
    check("seconds 格式在宽松模式下通过自检", check_ttml(secs, strict=False).ok,
          str([e.__dict__ for e in check_ttml(secs, strict=False).errors[:3]]))

    apple = TTMLWriter(mode="apple").write(
        als, artists=["A", "B", "C"], language="zh-Hans", duration=20.0)
    check("Apple 模式（显式 songPart）通过自检",
          check_ttml(TTMLWriter(mode="apple", song_part="Verse").write(
              als, artists=["A", "B", "C"], language="zh-Hans", duration=20.0)).ok,
          str([e.__dict__ for e in check_ttml(apple).errors[:3]]))

    # 规范要求：同一演唱者的行不能重叠，validator 必须能抓到
    bad = mk("重复行", "v1", "", 1.0)
    res_bad = check_ttml(TTMLWriter().write([als[0], bad], language="zh-Hans", duration=20.0))
    check("validator 能发现同行重叠", not res_bad.ok)


def test_apple_style():
    """Apple《风格标准和准则》里可程序化的部分"""
    print("\n[11] Apple 歌词风格准则")
    from lyric_style import (StyleReport, capitalize_english, lint_text,
                             mask_profanity, normalize_line, strip_trailing_punct)
    from matcher import AlignedLine, Unit

    # 1. 行尾不得用句号/逗号
    check("去掉行尾句号", strip_trailing_punct("I'll keep barking.") == "I'll keep barking")
    check("去掉行尾逗号", strip_trailing_punct("hello,") == "hello")
    check("中文行尾句号也去掉", strip_trailing_punct("站在旅途的尽头。") == "站在旅途的尽头")
    check("保留感叹号/问号", strip_trailing_punct("Are you sure?!") == "Are you sure?!")
    check("中间标点不动", strip_trailing_punct("hello, world,") == "hello, world")

    # 2. 英文首字母大写
    check("行首首字母大写", capitalize_english("i'll keep barking") == "I'll keep barking")
    check("括号内首词大写", capitalize_english("(ooh) yeah") == "(Ooh) yeah")
    check("不破坏已有的大小写", capitalize_english("NASA and iPhone") == "NASA and iPhone")
    check("中文行不受影响", capitalize_english("站在旅途的尽头") == "站在旅途的尽头")

    # 3. 屏蔽词
    masked, n = mask_profanity("I'm the fucking man")
    check("脏话替换为星号", masked == "I'm the **** man" and n == 1, masked)
    masked2, n2 = mask_profanity("这是妈的天气")
    check("中文脏话也屏蔽", "****" in masked2 and n2 == 1, masked2)

    # 4. lint 检查（不修改，只报告）
    issues = lint_text("i'll keep barking.")
    check("lint 能发现行尾标点与大小写问题", len(issues) >= 2, str(issues))
    check("合规行没有 lint 问题", lint_text("I'll keep barking") == [])
    check("脏话会被 lint 提示", any("屏蔽" in i for i in lint_text("fuck this")))

    # 5. 规范化的改动会被完整记录
    rep = StyleReport()
    out = normalize_line("i'll keep barking.", 3, report=rep)
    check("规范化结果正确", out == "I'll keep barking", out)
    check("改动被记录（可追溯）", rep.count == 2, str([c.kind for c in rep.changes]))

    # 6. 背景人声单独一行（Apple 准则）
    def mk(text, start, bg_from=None, paragraph=0):
        al = AlignedLine(text=text, agent="v1", begin=start, end=start + 0.4 * len(text),
                         paragraph=paragraph)
        for i, ch in enumerate(text):
            al.units.append(Unit(core=ch, index=i, start=start + 0.4 * i,
                                 end=start + 0.4 * (i + 1), matched=True,
                                 is_bg=(bg_from is not None and i >= bg_from)))
        al.matched = al.total = len(text)
        return al

    line = mk("有你为我践行一路践行", 10.0, bg_from=6)
    inline = TTMLWriter().write([line], language="zh-Hans", duration=30.0)
    check("和声与主唱写在同一个 <p> 里", inline.count("<p ") == 1)
    check("和声带 x-bg 标记", 'ttm:role="x-bg"' in inline)
    check("混排一行能通过自检", check_ttml(inline).ok,
          str([e.__dict__ for e in check_ttml(inline).errors[:3]]))

    # 6.5【铁律 #8】整行只有和声时不写独立 <p>（AMLL 解析后主歌词为空 = 看不见）
    bg_only = mk("(一路践行)", 10.0, bg_from=0)
    bg_ttml = TTMLWriter().write([bg_only], language="zh-Hans", duration=30.0)
    bg_warn = " ".join(w.issue for w in check_ttml(bg_ttml).warnings)
    check("整行和声会被校验器警告（不该生成这种文件）", "并进相邻主唱行" in bg_warn, bg_warn[:80])
    check("TTMLWriter 不再提供“和声单独成行”开关",
          "bg_layout" not in TTMLWriter.__init__.__code__.co_varnames)

    # 7. 唱段之间分段（空行 → 多个 div）
    def mkp(text, start, para):
        al = mk(text, start)
        al.paragraph = para
        return al

    verse = [mkp("第一段主歌歌词", 0.5, 0), mkp("继续把主歌唱完", 4.0, 0)]
    chorus = [mkp("副歌来了副歌", 8.0, 1), mkp("大家都一起唱", 12.0, 1)]
    chorus2 = [mkp("副歌来了副歌", 16.0, 2), mkp("大家都一起唱", 20.0, 2)]
    song = TTMLWriter(mode="apple").write(verse + chorus + chorus2, language="zh-Hans", duration=30.0)
    check("按空行分成多个 <div>", song.count("<div ") == 3, str(song.count("<div ")))
    check("重复段落判为 Chorus", 'itunes:songPart="Chorus"' in song)
    check("非重复段落判为 Verse", 'itunes:songPart="Verse"' in song)
    check("分段后仍通过自检", check_ttml(song).ok,
          str([e.__dict__ for e in check_ttml(song).errors[:3]]))

    # 8. 消音词星号化
    muted_line = mk("I'm the fucking man", 1.0)
    muted_line.units[7].muted = True          # 模拟"检测到该词被消音"
    muted_ttml = TTMLWriter().write([muted_line], language="en", duration=10.0)
    check("消音词被替换为星号", "****" in muted_ttml, muted_ttml[-260:])

    # 9. 校验器会按风格准则给出警告
    bad = TTMLWriter().write([mk("i'll keep barking.", 1.0)], language="en", duration=10.0)
    warns = [w.issue for w in check_ttml(bad).warnings]
    check("校验器提示行尾标点", any("句号" in w or "逗号" in w for w in warns), str(warns[:3]))
    check("校验器提示英文大小写", any("大写" in w for w in warns), str(warns[:3]))


def test_separate():
    """人声分离模块（装了 demucs 就真跑一遍）"""
    print("\n[12] 人声分离（Demucs）")
    import os
    import numpy as np
    import separate

    check("separate 模块可导入", hasattr(separate, "VocalSeparator"))
    if not separate.available():
        print("  (未安装 demucs，跳过实际分离测试)")
        return
    check("demucs 可用", separate.available())
    check("输出音轨里有 vocals", "vocals" in separate.SOURCES, str(separate.SOURCES))

    # 造一段"人声 + 伴奏"：440Hz 当人声，60Hz 强低频当贝斯
    sr = 16000
    t = np.arange(int(sr * 3.0)) / sr
    voice = 0.25 * np.sin(2 * np.pi * 440 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    bass = 0.5 * np.sin(2 * np.pi * 60 * t)
    mix = (voice + bass).astype(np.float32)

    tmpdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_t_selftest")
    os.makedirs(tmpdir, exist_ok=True)
    mix_path = os.path.join(tmpdir, "mix.wav")
    out_path = os.path.join(tmpdir, "vocals.wav")
    separate._write_wav(mix_path, mix, sr)
    check("能写出 16k 单声道 wav", os.path.getsize(mix_path) > 1000)

    sep = separate.VocalSeparator(log=lambda *a: None)
    sep.separate_to_file(mix_path, out_path)
    check("分离输出文件存在", os.path.exists(out_path) and os.path.getsize(out_path) > 1000)

    import wave
    with wave.open(out_path, "rb") as w:
        n, rate = w.getnframes(), w.getframerate()
        out = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
    check("输出采样率 16k", rate == 16000, str(rate))
    check("输出长度与输入一致（时间轴不变）", abs(len(out) - len(mix)) < sr * 0.3,
          f"{len(out)} vs {len(mix)}")

    def band(x, f0, f1):
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        freqs = np.fft.rfftfreq(len(x), 1 / sr)
        m = (freqs >= f0) & (freqs <= f1)
        return float((spec[m] ** 2).sum())

    low_ratio = band(out, 40, 90) / (band(mix, 40, 90) + 1e-9)
    mid_ratio = band(out, 380, 500) / (band(mix, 380, 500) + 1e-9)
    check("分离后低频能量下降", low_ratio < mid_ratio,
          f"low 保留 {low_ratio:.2f} vs mid 保留 {mid_ratio:.2f}")

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_ttml_roundtrip_and_normalize():
    """[13] TTML 反向解析（往返不变量）+ 时间轴规范化 R1–R7（P0 地基）"""
    print("\n[13] TTML 反向解析与时间轴规范化")
    import os

    from matcher import AlignedLine
    from ttml_model import EditDoc, EditLine, EditWord
    from ttml_normalize import MAX_UNIT_DURATION, normalize_timeline
    from ttml_reader import TTMLParseError, doc_to_ttml, parse_ttml

    # ---------------------------------------------------------------- 构造样本
    def mkline(text, start, agent="v1", bg_from=None, role="", translation="",
               paragraph=0, duration=0.4):
        al = AlignedLine(text=text, agent=agent, begin=start,
                         end=start + duration * len(text), paragraph=paragraph,
                         role=role, translation=translation)
        for i, ch in enumerate(text):
            al.units.append(Unit(core=ch, index=i, start=start + duration * i,
                                 end=start + duration * (i + 1), matched=True,
                                 is_bg=(bg_from is not None and i >= bg_from)))
        al.matched = al.total = len(text)
        return al

    samples = [
        ("AMLL 基本", dict(mode="amll"), [mkline("站在旅途的尽头", 4.0),
                                          mkline("面对下一个岔路口", 8.0)]),
        ("含 x-bg 和声", dict(mode="amll"),
         [mkline("有你为我践行一路践行", 10.0, bg_from=6)]),
        ("Apple + songPart", dict(mode="apple", song_part="Verse"),
         [mkline("第一段主歌", 1.0, paragraph=0), mkline("副歌来了", 5.0, paragraph=1)]),
        ("头部翻译", dict(mode="amll", translation_style="head"),
         [mkline("雨上がりの傘", 1.0, translation="雨后的伞")]),
        ("行内翻译", dict(mode="amll", translation_style="inline"),
         [mkline("雨上がりの傘", 1.0, translation="雨后的伞")]),
        ("对唱 role", dict(mode="amll", markers="both"),
         [mkline("我唱这一句", 1.0, agent="v1", role="x-duet-a"),
          mkline("我接下一句", 4.0, agent="v2", role="x-duet-b")]),
        ("mmss 时间格式", dict(mode="amll", time_format="mmss"),
         [mkline("时间格式测试", 65.0)]),
        ("seconds 时间格式", dict(mode="amll", time_format="seconds"),
         [mkline("时间格式测试", 65.0)]),
    ]

    for name, over, lines in samples:
        text1 = TTMLWriter(**over).write(lines, title="往返测试", artists=["A"],
                                         language="zh-Hans", duration=120.0)
        doc1 = parse_ttml(text1)
        text2 = doc_to_ttml(doc1, **over)
        doc2 = parse_ttml(text2)
        check(f"往返等价：{name}", doc1 == doc2,
              f"{doc1.stats()} vs {doc2.stats()}")
        check(f"写入幂等：{name}", text2 == doc_to_ttml(doc2, **over))
        # mmss / seconds 是 AMLL 宽松写法，本就不是合法 TTML1 → 只在宽松模式校验
        strict = over.get("time_format", "clock") == "clock"
        check(f"往返产物仍合法：{name}", check_ttml(text2, strict=strict).ok,
              str([e.__dict__ for e in check_ttml(text2, strict=strict).errors[:2]]))

    # 和声层有没有被解析出来（不是并进主唱文本）
    doc_bg = parse_ttml(TTMLWriter().write([mkline("有你为我践行一路践行", 10.0, bg_from=6)],
                                           language="zh-Hans", duration=30.0))
    check("x-bg 被解析成和声层", len(doc_bg.lines[0].bg_words) == 4,
          str([(w.text, w.layer) for w in doc_bg.lines[0].words]))
    check("和声文本去掉了写入器补的括号",
          "".join(w.text for w in doc_bg.lines[0].bg_words) == "一路践行",
          "".join(w.text for w in doc_bg.lines[0].bg_words))

    # 坏 XML 要报可读错误，而不是裸栈
    try:
        parse_ttml("<tt><body><p></tt>")
        check("坏 XML 抛 TTMLParseError", False)
    except TTMLParseError:
        check("坏 XML 抛 TTMLParseError", True)
    except Exception as e:
        check("坏 XML 抛 TTMLParseError", False, type(e).__name__)

    # 真实产物：往返 + 不该被规范化修改（H3 假设）
    # ⚠️ tests/fixtures/ 是**随仓库分发**的自造 fixture（tools/make_fixture.py 生成，
    # 零版权风险）—— 有它新克隆的人才能跑到这几组断言；
    # outputs/ 里的真实产物只在作者本机存在，有就一起测。
    for path in ("tests/fixtures/demo.ttml", "tests/fixtures/demo-duet.ttml",
                 "outputs/在出发之前.ttml", "outputs/它像一颗.ttml"):
        if not os.path.exists(path):
            continue
        base = os.path.basename(path)
        text1 = open(path, encoding="utf-8").read()
        doc1 = parse_ttml(text1)
        check(f"真实产物往返等价：{base}", doc1 == parse_ttml(doc_to_ttml(doc1)))
        rep = normalize_timeline(doc1)
        check(f"真实产物 fixes==[]：{base}", rep.fixes == [],
              str([f.describe() for f in rep.fixes[:3]]))
        check(f"真实产物 unresolved==[]：{base}", rep.unresolved == [],
              str([f.describe() for f in rep.unresolved[:3]]))

    # ---------------------------------------------------------------- R1–R7
    def one_line(words, begin=0.0, end=10.0, locked=False, agent="v1", key="L1"):
        line = EditLine(key=key, agent=agent, begin=begin, end=end, locked=locked,
                        text="".join(w[2] for w in words))
        for i, (b, e, t) in enumerate(words):
            line.words.append(EditWord(text=t, begin=b, end=e, index=i))
        return EditDoc(lines=[line], lang="zh-Hans", duration=end)

    # R1：字倒序（end <= begin）
    doc = one_line([(0.0, 1.0, "我"), (1.0, 0.9, "你"), (2.0, 3.0, "他")])
    rep = normalize_timeline(doc)
    check("R1 修好倒序字", len(rep.fixes) == 1 and rep.fixes[0].rule == "R1",
          str([f.describe() for f in rep.fixes]))
    check("R1 修完时间合法", doc.lines[0].words[1].end > doc.lines[0].words[1].begin)

    # R2：行内字重叠
    doc = one_line([(0.0, 1.0, "我"), (0.5, 1.5, "你"), (2.0, 3.0, "他")])
    rep = normalize_timeline(doc)
    check("R2 推开重叠字", len(rep.fixes) == 1 and rep.fixes[0].rule == "R2",
          str([f.describe() for f in rep.fixes]))
    check("R2 修完不重叠",
          doc.lines[0].words[1].begin >= doc.lines[0].words[0].end - 1e-6)

    # R3：字越出行区间
    doc = one_line([(4.0, 4.5, "飞")], begin=0.0, end=3.0)
    rep = normalize_timeline(doc)
    w = doc.lines[0].words[0]
    check("R3 把字夹回行内", w.begin >= -1e-6 and w.end <= 3.0 + 1e-6,
          f"{w.begin}~{w.end}")
    check("R3 记了 R3", any(f.rule == "R3" for f in rep.fixes),
          str([f.describe() for f in rep.fixes]))

    # R5：字太短
    doc = one_line([(0.0, 1.0, "我"), (1.0, 2.0, "你"), (2.0, 2.02, "他")])
    rep = normalize_timeline(doc)
    check("R5 拉长过短的字",
          doc.lines[0].words[2].end - doc.lines[0].words[2].begin >= 0.06 - 1e-6,
          str(doc.lines[0].words[2]))
    check("R5 记了 R5", any(f.rule == "R5" for f in rep.fixes),
          str([f.describe() for f in rep.fixes]))

    # R6：字太长（超过硬上限才自动截）
    doc = one_line([(0.0, 1.0, "我"), (1.0, 5.0, "你")])
    rep = normalize_timeline(doc)
    check("R6 把超长字截到硬上限",
          doc.lines[0].words[1].end - doc.lines[0].words[1].begin <= MAX_UNIT_DURATION + 1e-6,
          str(doc.lines[0].words[1]))
    check("R6 记了 R6", any(f.rule == "R6" for f in rep.fixes),
          str([f.describe() for f in rep.fixes]))

    # R6 灰色地带（2.5s < d <= 3.5s）：只提示，不偷偷截短
    doc = one_line([(0.0, 1.0, "我"), (1.0, 3.8, "你")])
    rep = normalize_timeline(doc)
    check("R6 灰色地带进 unresolved",
          any(f.rule == "R6" for f in rep.unresolved) and rep.fixes == [],
          str([f.describe() for f in rep.unresolved]))
    check("R6 灰色地带没有被截短",
          abs(doc.lines[0].words[1].end - 3.8) < 1e-6)

    # R7：行内长停顿
    doc = one_line([(0.0, 1.0, "我"), (5.0, 6.0, "你")])
    rep = normalize_timeline(doc)
    check("R7 长停顿进 unresolved", any(f.rule == "R7" for f in rep.unresolved),
          str([f.describe() for f in rep.unresolved]))

    # 和声层允许与主唱重叠（AMLL「因混音而使时间重叠的情况除外」）
    doc = EditDoc(lines=[EditLine(key="L1", agent="v1", begin=0.0, end=2.0, text="我唱和声")])
    doc.lines[0].words = [
        EditWord(text="我", begin=0.0, end=1.0, index=0),
        EditWord(text="唱", begin=1.0, end=2.0, index=1),
        EditWord(text="(", begin=0.5, end=1.5, index=2, is_bg=True, layer="bg"),
    ]
    rep = normalize_timeline(doc)
    check("和声与主唱重叠不被压平", rep.fixes == [],
          str([f.describe() for f in rep.fixes]))
    check("和声时间原样保留",
          abs(doc.lines[0].words[2].begin - 0.5) < 1e-6
          and abs(doc.lines[0].words[2].end - 1.5) < 1e-6)

    # 行锁：跳过修复，只报告
    doc = one_line([(0.0, 1.0, "我"), (0.5, 1.5, "你")], locked=True)
    rep = normalize_timeline(doc)
    check("锁定行被跳过", rep.locked_lines == ["L1"], str(rep.locked_lines))
    check("锁定行没被改", doc.lines[0].words[1].begin == 0.5)
    check("锁定行的改动不进 fixes", rep.fixes == [],
          str([f.describe() for f in rep.fixes]))

    # policy
    doc = one_line([(0.0, 1.0, "我"), (0.5, 1.5, "你")])
    rep_off = normalize_timeline(doc, policy="off")
    check("policy=off 不改", doc.lines[0].words[1].begin == 0.5 and rep_off.fixes == [])
    rep_hl = normalize_timeline(doc, policy="highlight")
    check("policy=highlight 不改只报",
          doc.lines[0].words[1].begin == 0.5 and len(rep_hl.fixes) >= 1,
          f"begin={doc.lines[0].words[1].begin} fixes={len(rep_hl.fixes)}")
    check("policy=highlight 报告里说明了要修什么",
          "R2" in rep_hl.summary() or "R1" in rep_hl.summary(), rep_hl.summary())
    try:
        normalize_timeline(doc, policy="bogus")
        check("policy 非法值报错", False)
    except ValueError:
        check("policy 非法值报错", True)

    # 跨行：同 agent 相邻行不重叠（R4）
    doc = EditDoc(lines=[
        EditLine(key="L1", agent="v1", begin=0.0, end=3.0, text="上"),
        EditLine(key="L2", agent="v1", begin=2.0, end=5.0, text="下"),
    ])
    doc.lines[0].words = [EditWord(text="上", begin=0.0, end=3.0, index=0)]
    doc.lines[1].words = [EditWord(text="下", begin=2.0, end=5.0, index=0)]
    rep = normalize_timeline(doc)
    check("R4 收紧上一行末尾", doc.lines[0].end <= 2.0 - 0.01 + 1e-6,
          f"{doc.lines[0].end}")
    check("R4 记了 R4", any(f.rule == "R4" for f in rep.fixes),
          str([f.describe() for f in rep.fixes]))

    # 不同 agent 的行允许重叠（对唱交接处）
    doc = EditDoc(lines=[
        EditLine(key="L1", agent="v1", begin=0.0, end=3.0, text="甲"),
        EditLine(key="L2", agent="v2", begin=2.0, end=5.0, text="乙"),
    ])
    doc.lines[0].words = [EditWord(text="甲", begin=0.0, end=3.0, index=0)]
    doc.lines[1].words = [EditWord(text="乙", begin=2.0, end=5.0, index=0)]
    normalize_timeline(doc)
    check("不同 agent 的行重叠不动", abs(doc.lines[0].end - 3.0) < 1e-6,
          f"{doc.lines[0].end}")

    # EditDoc JSON 序列化往返（编辑态落盘用）
    doc = one_line([(0.0, 1.0, "我"), (1.0, 2.0, "你")])
    doc.meta = {"musicName": "测试", "artists": ["A", "B"]}
    check("EditDoc JSON 往返一致", EditDoc.from_dict(doc.to_dict()) == doc)


def test_duet_and_bg_format():
    """[14] 对唱标记 + 背景人声的输出形状（真实踩过的坑）"""
    print("\n[14] 对唱与背景人声格式")
    from matcher import AlignedLine
    from preprocess import detect_agent_switch, merge_background_lines, parse_lyrics

    # ---------- 1. 标记必须在"合并背景人声"之前剥掉 ----------
    # 坑：`[v1]（一路践行）` 分词后 v1 也算背景 token，会被合并进主唱行；
    #     合并完再剥标记时 [v1] 已在行中间，行首匹配不到 → 标记渲染进歌词 → "(v1一路践行)"
    lrc = "[00:01.00][v1]有你为我践行\n[00:02.00][v1]（一路践行）\n[00:03.00][v2]下一句\n"
    lines = detect_agent_switch(parse_lyrics(lrc))
    check("带标记的和声行被合并进主唱行", len(lines) == 2, f"{len(lines)} 行")
    check("标记没有泄漏进歌词", "v1" not in lines[0].text and "[" not in lines[0].text,
          repr(lines[0].text))
    check("合并后文本正确", lines[0].text == "有你为我践行（一路践行）", repr(lines[0].text))
    check("主唱行拿到了 v1", lines[0].agent == "v1" and lines[0].manual_agent)
    check("第二行标记生效为 v2", lines[1].agent == "v2", lines[1].agent)

    # 不带标记的和声行同样要能合并
    lines2 = detect_agent_switch(parse_lyrics(
        "[00:01.00]有你为我践行\n[00:02.00]（一路践行）\n"))
    check("无标记和声行也能合并", len(lines2) == 1 and "一路践行" in lines2[0].text)

    # 和声在主唱之前（标记 + 位置都在前）
    lines3 = detect_agent_switch(parse_lyrics(
        "[00:01.00][v1]（先唱的和声）\n[00:02.00][v1]主唱这一句\n"))
    check("和声在主唱之前也能合并", len(lines3) == 1, f"{len(lines3)} 行")
    check("和声前置时标记同样不泄漏", "v1" not in lines3[0].text, repr(lines3[0].text))

    # ---------- 2. x-bg 包装层要写 begin/end（与 AMLL 参考实现一致）----------
    def mk(text, start, bg_from=None, duration=0.4, agent="v1", role="", translation=""):
        al = AlignedLine(text=text, agent=agent, begin=start, end=start + duration * len(text),
                         role=role, translation=translation)
        for i, ch in enumerate(text):
            al.units.append(Unit(core=ch, index=i, start=start + duration * i,
                                 end=start + duration * (i + 1), matched=True,
                                 is_bg=(bg_from is not None and i >= bg_from)))
        al.matched = al.total = len(text)
        return al

    line = mk("有你为我践行一路践行", 10.0, bg_from=6)
    ttml = TTMLWriter().write([line], language="zh-Hans", duration=30.0)
    m = re.search(r'<span ttm:role="x-bg"([^>]*)>', ttml)
    check("x-bg 包装层带 begin/end", bool(m) and "begin=" in m.group(1) and "end=" in m.group(1),
          m.group(0) if m else "没找到 x-bg")
    if m:
        b0 = parse_time(re.search(r'begin="([^"]+)"', m.group(1)).group(1))
        b1 = parse_time(re.search(r'end="([^"]+)"', m.group(1)).group(1))
        check("包装层区间合法", b1 > b0)
        # 子 span 必须落在包装层区间里
        inner = ttml.split(m.group(0), 1)[1].split("</span></span>", 1)[0]
        spans = re.findall(r'<span begin="([^"]+)" end="([^"]+)">', inner)
        check("子 span 都在包装层区间内",
              all(b0 - 1e-6 <= parse_time(s) and parse_time(e) <= b1 + 1e-6 for s, e in spans),
              f"{b0}~{b1} vs {spans}")
    check("和声括号只包一层", ttml.count("(一") == 1 and "((一" not in ttml)

    # ---------- 3. 和声在主唱之前 → 文档顺序也在前面（规范 §7.1）----------
    al = AlignedLine(text="先唱的和声主唱这句", agent="v1", begin=1.0, end=5.0)
    for i, ch in enumerate("先唱的和声"):
        al.units.append(Unit(core=ch, index=i, start=1.0 + 0.3 * i, end=1.0 + 0.3 * (i + 1),
                             matched=True, is_bg=True))
    for i, ch in enumerate("主唱这句"):
        al.units.append(Unit(core=ch, index=i, start=3.0 + 0.4 * i, end=3.0 + 0.4 * (i + 1),
                             matched=True, is_bg=False))
    al.matched = al.total = len(al.units)
    t2 = TTMLWriter().write([al], language="zh-Hans", duration=30.0)
    check("和声在主唱之前时排在主唱 span 前面",
          t2.index('ttm:role="x-bg"') < t2.index(">主<"), t2[t2.find("<p"):][:200])
    check("前置和声也通过自检", check_ttml(t2).ok,
          str([e.__dict__ for e in check_ttml(t2).errors[:2]]))

    # ---------- 4. 对唱：不同 agent + 角色 + head 定义 ----------
    from pipeline import duet_hint

    a1 = mk("我唱这一句", 1.0, agent="v1", role="x-duet-a")
    a2 = mk("我接下一句", 4.0, agent="v2", role="x-duet-b")
    duo = TTMLWriter(markers="both").write([a1, a2], artists=["陶典", "hanser"],
                                           language="zh-Hans", duration=10.0)
    check("对唱写两个 agent", 'ttm:agent="v1"' in duo and 'ttm:agent="v2"' in duo)
    check("head 里两个 agent 都有定义且带名字",
          'xml:id="v1"><ttm:name type="full">陶典' in duo
          and 'xml:id="v2"><ttm:name type="full">hanser' in duo)
    check("Apple 角色 x-duet-a/b 都写了", "x-duet-a" in duo and "x-duet-b" in duo)
    check("对唱文件通过自检", check_ttml(duo).ok,
          str([e.__dict__ for e in check_ttml(duo).errors[:2]]))
    solo = TTMLWriter(markers="both").write([a1, mk("还是我唱", 7.0)],
                                            language="zh-Hans", duration=10.0)
    check("单人不写 x-duet-b", "x-duet-b" not in solo and 'xml:id="v2"' not in solo)

    # ---------- 5. markers=auto：Apple 模式才写对唱角色 ----------
    # Apple Music 的左右分栏靠不同的 ttm:agent 触发；Apple 自己的文件里
    # 对唱双方还带 ttm:role="x-duet-a"/"x-duet-b"（Apple 内部扩展）。
    # 所以 --mode apple 默认就该带上，否则提交给 Apple 的对唱文件缺角色信息。
    amll_auto = TTMLWriter(mode="amll", markers="auto").write(
        [a1, a2], artists=["陶典", "hanser"], language="zh-Hans", duration=10.0)
    apple_auto = TTMLWriter(mode="apple", markers="auto").write(
        [a1, a2], artists=["陶典", "hanser"], language="zh-Hans", duration=10.0)
    check("auto：AMLL 模式不写 x-duet（守 AMLL 官方推荐）", "x-duet" not in amll_auto)
    check("auto：Apple 模式写 x-duet-a/b", "x-duet-a" in apple_auto and "x-duet-b" in apple_auto)
    check("auto：两种模式都写 ttm:agent",
          'ttm:agent="v1"' in amll_auto and 'ttm:agent="v2"' in apple_auto)
    check("auto：Apple 模式对唱文件通过自检", check_ttml(apple_auto).ok,
          str([e.__dict__ for e in check_ttml(apple_auto).errors[:2]]))
    check("显式 markers=agent 仍然覆盖 auto",
          "x-duet" not in TTMLWriter(mode="apple", markers="agent").write(
              [a1, a2], language="zh-Hans", duration=10.0))

    # ---------- 7. x-duet-a/b 只能挂在 <p> 上 ----------
    # ⚠️ 真实故障：包在主唱 span 外的写法会让 AMLL 解析器（对未知 role 走
    # processWordElement，且**不递归子节点**）把整行当成"一个词"，
    # 逐字时间轴全丢 → 播放器里歌词一片空白。所以那个开关已被删除，
    # 现在只有一种写法（挂在 <p> 上），并且校验器拦得住别人手写的坏文件。
    check("TTMLWriter 不再有“角色写在哪”的开关（只剩安全写法）",
          "duet_role_on" not in TTMLWriter.__init__.__code__.co_varnames)
    t = TTMLWriter(mode="apple", markers="auto").write(
        [a1, a2], artists=["陶典", "hanser"], language="zh-Hans", duration=10.0)
    check("Apple 模式：角色挂在 <p> 上",
          bool(re.search(r'<p [^>]*ttm:role="x-duet-a"', t)), t[:120])
    check("Apple 模式：不把主唱 span 包起来",
          '<span ttm:role="x-duet-a">' not in t)
    check("挂 <p> 的写法通过自检", check_ttml(t).ok,
          str([e.__dict__ for e in check_ttml(t).errors[:2]]))
    # 手写一份"包 span"的坏文件，校验器必须报错
    bad_role = TTMLWriter().write([mk("我唱这一句", 1.0)], language="zh-Hans", duration=5.0)
    bad_role = bad_role.replace(
        '<span begin="', '<span ttm:role="x-duet-a"><span begin="', 1)
    bad_role = bad_role.replace("</p>", "</span></p>", 1)
    bad_res = check_ttml(bad_role)
    check("校验器拦下“未知 role 包 span”的写法（会吞逐字）",
          any("当成一个词" in e.issue for e in bad_res.errors),
          str([e.issue for e in bad_res.errors[:1]]))

    # 和声压到下一行（主唱层并不重叠）→ 不该被判"同一演唱者行重叠"
    # mk("有你为我践行一路践行", 1.0, bg_from=6)：主唱占 1.0~3.4，和声占 3.4~5.0
    bg_line = mk("有你为我践行一路践行", 1.0, bg_from=6)
    over = mk("下一句", 4.0)             # 在主唱之后、和声之内开始
    for where, markers in (("挂<p>", "auto"), ("不写角色", "agent")):
        t = TTMLWriter(mode="apple", markers=markers).write(
            [bg_line, over], language="zh-Hans", duration=10.0)
        errs = [e.issue for e in check_ttml(t).errors]
        check(f"和声压到下一行（{where}）不误报同行重叠",
              not any("行时间重叠" in e for e in errs), str(errs[:2]))
    # 但主唱层真的重叠时必须报
    real_overlap = mk("真的重叠", 2.0)     # 2.0 < 上一行主唱结束 3.4
    t2 = TTMLWriter(mode="apple", markers="auto").write(
        [bg_line, real_overlap], language="zh-Hans", duration=10.0)
    check("主唱层真重叠仍然报错",
          any("行时间重叠" in e.issue for e in check_ttml(t2).errors),
          str([e.issue for e in check_ttml(t2).errors][:2]))

    # ---------- 9. AMLL 规范 §4.1 / §7.3：合唱与和声的正确归属 ----------
    from preprocess import _all_background
    from ttml_writer import agent_type

    m_lines = detect_agent_switch(parse_lyrics(
        "[00:01.00][v1]主唱这一句\n"
        "[00:02.00][和声]一路践行\n"
        "[00:03.00][合唱]大家一起唱\n"
        "[00:04.00][v2]第二个人唱\n"))
    check("规范 §7.3：[和声] 行并进主唱行（不是独立 agent）", len(m_lines) == 3, f"{len(m_lines)} 行")
    check("规范 §7.3：[和声] 内容进入背景层文本",
          "一路践行" in m_lines[0].text and _all_background(m_lines[0]) is False)
    check("规范 §4.1：[合唱] → agent v1000", m_lines[1].agent == "v1000", m_lines[1].agent)
    check("规范 §4.1：v1000 的 type 是 group", agent_type("v1000") == "group")
    check("[v2] 仍然是 v2", m_lines[2].agent == "v2", m_lines[2].agent)

    # 渲染出来看看：和声必须是同一个 <p> 里的 x-bg，合唱是独立 <p> 且 type=group
    _w = [AsrWord(text=c, start=1.0 + 0.2 * i, end=1.0 + 0.2 * (i + 1), prob=0.9)
          for i, c in enumerate("主唱这一句一路践行大家一起唱第二个人唱")]
    _rep = LyricMatcher(log=lambda *a: None).align(m_lines, _w, 20.0)
    _t = TTMLWriter(markers="auto", mode="apple").write(
        _rep.lines, artists=["甲", "乙"], language="zh-Hans", duration=20.0)
    check("合唱写进独立的 <p> 且带 v1000", 'ttm:agent="v1000"' in _t)
    check("合唱的 agent 声明 type=group", 'type="group" xml:id="v1000"' in _t)
    check("和声写在同一 <p> 内的 x-bg", 'ttm:role="x-bg"' in _t)
    check("歌手名不会被安到合唱（group）上",
          'type="group" xml:id="v1000"><ttm:name' not in _t)
    # 旁白/念白（x-narration）也不是歌手 —— 不能让"旁白 = 某歌手"
    nar = TTMLWriter(markers="auto", mode="apple").write(
        [mk("我唱第一句", 1.0), mk("这是念白", 3.0, agent="v3", role="x-narration")],
        artists=["陶典", "hanser"], language="zh-Hans", duration=10.0)
    check("旁白（x-narration）不会被安上歌手名",
          'xml:id="v3"><ttm:name' not in nar, nar[:400])
    check("旁白仍然声明为 person", 'type="person" xml:id="v3"' in nar, nar[:300])
    check("旁白文件通过自检", check_ttml(nar).ok,
          str([e.issue for e in check_ttml(nar).errors[:2]]))

    # ---------- 10. 规范 §5：head 与行内翻译不能同语言重复 ----------
    dup = TTMLWriter(translation_style="both", inline_translation_lang="ja").write(
        [mk("雨上がりの傘", 1.0, translation="雨后的伞")],
        language="ja", duration=10.0, translation_lang="ja")
    dup_warn = " ".join(w.issue for w in check_ttml(dup).warnings)
    check("规范 §5：head+行内同语言翻译会被警告", "双重翻译" in dup_warn, dup_warn[:90])

    # ---------- 11. 规范 §7.3：背景人声括号规则 ----------
    bad_paren = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"'
        ' xmlns:itunes="http://music.apple.com/lyric-ttml-internal" xml:lang="zh-Hans">'
        '<head><metadata><ttm:agent type="person" xml:id="v1"/></metadata></head>'
        '<body dur="00:00:10.000"><div begin="00:00:01.000" end="00:00:09.000">'
        '<p begin="00:00:01.000" end="00:00:09.000" itunes:key="L1" ttm:agent="v1">'
        '<span begin="00:00:01.000" end="00:00:02.000">嗨</span>'
        '<span ttm:role="x-bg"><span begin="00:00:02.000" end="00:00:03.000">((啊</span>'
        '<span begin="00:00:03.000" end="00:00:04.000">呀</span></span>'
        '</p></div></body></tt>')
    bp = " ".join(w.issue for w in check_ttml(bad_paren).warnings)
    check("规范 §7.3：双重括号会被警告", "不止一层括号" in bp, bp[:90])
    half = bad_paren.replace("((啊", "(啊")
    hp = " ".join(w.issue for w in check_ttml(half).warnings)
    check("规范 §7.3：只写半边括号会被警告", "缺少成对的括号" in hp, hp[:90])

    # ---------- 12. 规范 §4.1：group 的 id 必须是 v1000 ----------
    wrong_group = TTMLWriter(markers="auto").write(
        [mk("大家一起唱", 1.0, agent="v7")], language="zh-Hans", duration=10.0)
    check("type 取值受支持", agent_type("v7") == "person")
    gw = TTMLWriter(markers="auto", mode="apple").write(
        [mk("合唱段落", 1.0, agent="v1000")], language="zh-Hans", duration=10.0)
    check("v1000 声明为 group", 'type="group" xml:id="v1000"' in gw)
    check("v1000 文件通过自检", check_ttml(gw).ok,
          str([e.__dict__ for e in check_ttml(gw).errors[:2]]))

    # ---------- 8. 多艺人却判单人 → 必须给出可操作提示 ----------
    hint = duet_hint(["陶典", "hanser"], 1)
    check("多艺人+单人演唱会警告分栏出不来", "左右分栏" in hint, hint[:40])
    check("提示里给了 [v1]/[v2] 的解法", "[v1]/[v2]" in hint)
    check("单人歌（只有一位艺人）不误报", duet_hint(["Hanser"], 1) == "")
    check("真分出声部时不提示", duet_hint(["陶典", "hanser"], 2) == "")
    check("没给艺人信息时不提示", duet_hint([], 1) == "")


def test_agent_from_reference():
    """[15] 复用参考 TTML 的演唱者归属（--agent-from）

    为什么需要：声纹对音色接近的对唱实测分不开 —— 拿 AMLL DB 的人工标注当
    真值量过，《在出发之前》CAM++ 只有 51.5%（=瞎猜），最好的音色特征无监督
    也只有 66.7%。社区已有标注时，复用它是唯一可靠的自动化路径。
    """
    print("\n[15] 复用参考文件的演唱者归属")
    import os
    import tempfile

    from matcher import AlignedLine, AlignReport
    from pipeline import LyricToTTML, ConvertOptions

    def mk(text, begin, end, agent="v1"):
        al = AlignedLine(text=text, agent=agent, begin=begin, end=end)
        for i, ch in enumerate(text):
            al.units.append(Unit(core=ch, index=i,
                                 start=begin + (end - begin) * i / max(1, len(text)),
                                 end=begin + (end - begin) * (i + 1) / max(1, len(text)),
                                 matched=True))
        al.matched = al.total = len(text)
        return al

    # 参考文件：4 行，其中第 2、3 行**文本完全相同**（副歌重复），但 agent 不同
    ref_lines = [mk("站在旅途的尽头", 1.0, 3.0, "v1"),
                 mk("相同的副歌", 4.0, 6.0, "v1"),
                 mk("相同的副歌", 7.0, 9.0, "v2"),
                 mk("最后一句", 10.0, 12.0, "v2")]
    ref_ttml = TTMLWriter(markers="both").write(
        ref_lines, title="参考", artists=["甲", "乙"], language="zh-Hans", duration=20.0)
    tmp = os.path.join(tempfile.gettempdir(), "_ref_agents.ttml")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(ref_ttml)

    # 本次对齐结果：同样 4 行，时间略有偏移
    my_lines = [mk("站在旅途的尽头", 1.2, 3.2),
                mk("相同的副歌", 4.1, 6.1),
                mk("相同的副歌", 7.1, 9.1),
                mk("最后一句", 10.1, 12.1)]
    report = AlignReport(lines=my_lines, total_units=sum(l.total for l in my_lines),
                         matched_units=sum(l.matched for l in my_lines), anchored=True)

    conv = LyricToTTML(ConvertOptions())
    notes = []
    n = conv._apply_agents_from_reference(report, tmp, notes)
    check("参考文件的行都能套上", n == 4, f"{n} 行")
    check("按文本套 agent", [l.agent for l in report.lines] == ["v1", "v1", "v2", "v2"],
          str([l.agent for l in report.lines]))
    check("重复句用时间破平局（第2、3行文本相同但 agent 不同）",
          report.lines[1].agent == "v1" and report.lines[2].agent == "v2")
    check("agent 类型被继承（参考里没声明 v3 之类也不会出错）",
          conv._ref_agent_types.get("v1") == "person", str(conv._ref_agent_types))
    check("notes 里说明了来源", any("参考文件" in x for x in notes), str(notes[:1]))

    # 参考文件里没有 agent 时不能崩（手写一份：<p> 上完全没有 ttm:agent）
    no_agent = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"'
        ' xmlns:itunes="http://music.apple.com/lyric-ttml-internal" xml:lang="zh-Hans">'
        '<head><metadata/></head><body dur="00:00:05.000">'
        '<div begin="00:00:01.000" end="00:00:04.000">'
        '<p begin="00:00:01.000" end="00:00:02.000" itunes:key="L1">'
        '<span begin="00:00:01.000" end="00:00:02.000">没有 agent 的行</span></p>'
        '</div></body></tt>')
    p2 = os.path.join(tempfile.gettempdir(), "_ref_no_agent.ttml")
    with open(p2, "w", encoding="utf-8", newline="\n") as f:
        f.write(no_agent)
    notes2 = []
    n2 = LyricToTTML(ConvertOptions())._apply_agents_from_reference(
        AlignReport(lines=[mk("没有 agent 的行", 1.0, 2.0)], total_units=7,
                    matched_units=7, anchored=True), p2, notes2)
    check("参考文件没有 agent 时安全跳过", n2 == 0 and any("没有 ttm:agent" in x for x in notes2),
          str(notes2))

    for p in (tmp, p2):
        try:
            os.remove(p)
        except OSError:
            pass


def test_amll_parser_optional():
    """[16] 用真 AMLL 解析器验"播放器里能不能显示"（依赖 node + 首次抓包，没有就跳过）"""
    print("\n[16] 真 AMLL 解析器验证（可选）")
    import os
    import sys as _sys

    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    if tools not in _sys.path:
        _sys.path.insert(0, tools)
    try:
        from verify_amll_parse import parse_check
    except Exception as e:
        print(f"  (导入失败 {type(e).__name__}，跳过)")
        return

    good = TTMLWriter(markers="auto").write(
        [_mk_duet_line("我唱这一句", 1.0), _mk_duet_line("我接下一句", 4.0)],
        language="zh-Hans", duration=10.0)
    r = parse_check(good, tag="_selftest_good.ttml")
    if r is None:
        print("  (未准备 AMLL 解析器或没有 node，跳过 —— 跑 "
              "python tools/verify_amll_parse.py --setup 可启用)")
        return
    check("真解析器：能解析出歌词行", r.get("lines", 0) > 0, str(r))
    check("真解析器：逐字时间轴完整（平均 ≥2 字每行）",
          r.get("perLine", 0) >= 2, f"平均 {r.get('perLine')} 字每行")
    check("真解析器：没有被丢字的行", r.get("emptyLines", 1) == 0, str(r))
    check("真解析器：判定为可显示", bool(r.get("ok")), str(r))

    # 反例：每行的主唱 span **整体**包进一个未知 role 的 span（无 begin/end）——
    # 每行都会塌成一个词，必须被判成不可显示（这正是当初让播放器空白一片的真实格式）
    import re as _re
    bad = _re.sub(
        r"(<p [^>]*>)(.*?)(</p>)",
        lambda m: m.group(1) + '<span ttm:role="x-duet-a">' + m.group(2) + "</span>" + m.group(3),
        good, flags=_re.S)
    rb = parse_check(bad, tag="_selftest_bad.ttml")
    if rb is not None:
        check("真解析器：未知 role 包 span（每行塌成一个词）会被判成不可显示",
              not rb.get("ok"), str(rb))
        check("真解析器：能指出是哪些行塌陷了",
              rb.get("collapsedLines", 0) > 0, str(rb))


def _mk_duet_line(text, start):
    from matcher import AlignedLine
    al = AlignedLine(text=text, agent="v1", begin=start, end=start + 0.4 * len(text))
    for i, ch in enumerate(text):
        al.units.append(Unit(core=ch, index=i, start=start + 0.4 * i,
                             end=start + 0.4 * (i + 1), matched=True))
    al.matched = al.total = len(text)
    return al


def test_edit_api():
    """[17] 编辑台后端（P1）：validate 只算不写、乐观锁、原子落盘、损坏降级"""
    print("\n[17] 编辑台后端（edit_api）")
    import json
    import os
    import shutil

    import edit_api as E

    # ⚠️ 用**工作区内**的临时目录：DSH 沙箱不允许在系统 temp 下新建子目录
    tmpd = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", "selftest_edit")
    shutil.rmtree(tmpd, ignore_errors=True)
    os.makedirs(tmpd, exist_ok=True)
    edit_path = os.path.join(tmpd, "t.edit.json")
    ttml_path = os.path.join(tmpd, "t.ttml")

    doc = TTMLWriter(markers="auto").write(
        [_mk_duet_line("我唱第一句", 1.0), _mk_duet_line("我唱第二句", 4.0)],
        artists=["甲", "乙"], language="zh-Hans", duration=10.0)
    ttml = doc
    d0 = E.doc_from_ttml(ttml)
    check("TTML 能载入成 EditDoc", d0.stats()["lines"] == 2, str(d0.stats()))

    # validate 只算不写
    d1 = E.doc_from_ttml(ttml)
    d1.lines[0].words[1].begin = d1.lines[0].words[0].begin - 0.6
    snapshot = d1.lines[0].words[1].begin
    out = E.validate(d1)
    check("validate 报出并修复了 1 处", len(out["normalize"]["fixes"]) == 1,
          out["normalize"]["summary"])
    check("validate 没改调用方的 doc", d1.lines[0].words[1].begin == snapshot)
    check("validate 返回修好的 doc 供前端刷新",
          out["doc"]["lines"][0]["words"][1]["begin"] != snapshot)

    # 空文档 → 结构化错误而不是崩溃
    try:
        E.validate(E.EditDoc())
        check("空文档抛结构化错误", False)
    except E.EditError as e:
        check("空文档抛结构化错误（不是 ValueError）", "hint" in e.to_dict(), str(e.to_dict()))

    # 乐观锁：必须拿**存储版本**比
    E.check_version(3, 3)
    check("版本一致不报错", True)
    try:
        E.check_version(3, 2)
        check("版本不一致报 409", False)
    except E.EditError as e:
        check("版本不一致报 409", e.code == 409, str(e.code))
    check("stored_version 读磁盘", E.stored_version(None, edit_path) == 0)

    # 保存闭环
    doc2 = E.doc_from_ttml(ttml)
    r1 = E.apply_doc(doc2, edit_path, ttml_path)
    check("保存后版本 = 1", r1["version"] == 1, str(r1["version"]))
    check(".edit.json 与 .ttml 都写了",
          os.path.exists(edit_path) and os.path.exists(ttml_path))
    check("落盘的 TTML 仍通过严格校验", r1["check"]["ok"],
          str(r1["check"]["errors"][:1]))
    check("stored_version 从磁盘读到 1", E.stored_version(None, edit_path) == 1)

    # 重新载入（模拟重启后继续编辑）
    d2, source = E.load_doc(None, edit_path)
    check("重启后能从磁盘恢复", source == "disk" and d2.version == 1,
          f"{source} v{d2.version}")

    # 损坏的 .edit.json 必须降级为从 TTML 重建，而不是抛
    with open(edit_path, "w", encoding="utf-8") as f:
        f.write("{ 坏掉的 JSON")
    d3, source3 = E.load_doc(open(ttml_path, encoding="utf-8").read(), edit_path)
    check("损坏的编辑态降级为 draft", source3 == "draft" and d3.stats()["lines"] == 2,
          f"{source3} {d3.stats()}")

    # doc 结构错误 → 结构化错误
    for bad in ({}, {"version": 1}, None):
        try:
            E.doc_from_dict(bad)
            check(f"doc_from_dict({bad!r}) 应报错", False)
        except E.EditError as e:
            check(f"doc_from_dict({type(bad).__name__}) 结构化报错", "hint" in e.to_dict())

    # JSON 往返：字段名必须是 begin/end（前端按这个取）
    rt = E.EditDoc.from_dict(doc2.to_dict())
    check("EditDoc JSON 往返一致", rt.to_dict() == doc2.to_dict())
    check("JSON 里字的时间字段是 begin/end",
          "begin" in doc2.to_dict()["lines"][0]["words"][0])

    # 原子写
    p = os.path.join(tmpd, "atomic.txt")
    E.atomic_write_text(p, "hello")
    check("atomic_write_text 生效", open(p, encoding="utf-8").read() == "hello")
    check("原子写不留临时文件",
          not [f for f in os.listdir(tmpd) if f.startswith(".tmp_")],
          str(os.listdir(tmpd)))

    shutil.rmtree(tmpd, ignore_errors=True)


def test_diarize_fix():
    """[18] 分声部修复不回归：整行 embedding（不是窗口平均）+ k-means（不是平均连接）

    这两条都是**用人工真值实测出来的**（《在出发之前》，AMLL DB 参考文件）：
      整行 90.6% / 窗口平均 50.0%；k-means 90.6% / 平均连接 56.2%。
    这里不重跑音频（离线自检不能依赖大模型），只把"代码形状"和算法行为锁住。
    """
    print("\n[18] 分声部：整行 embedding + k-means（防回归）")
    import inspect

    import diarize

    sig = inspect.signature(diarize.line_embedding)
    check("line_embedding 默认走整行（mode='whole'）",
          sig.parameters["mode"].default == "whole", str(sig.parameters["mode"].default))
    src = inspect.getsource(diarize.line_embedding)
    check("整行分支只用一次 _spk.embed（不做窗口平均）",
          "mode == \"whole\"" in src and src.count("_spk.embed") >= 2)
    check("窗口平均路径保留但只在 mode='window' 下走",
          'if mode == "whole"' in src and "return _spk.embed(seg, sr)" in src)
    check("注释里写清了实测数字（90.6% / 50.0%）",
          "90.6%" in src and "50.0%" in src)

    check("diarize 模块有 _kmeans", hasattr(diarize, "_kmeans"))
    dsrc = inspect.getsource(diarize.diarize)
    check("指定人数时用 _kmeans 而不是 _force_k",
          "_kmeans(E, n_singers)" in dsrc, dsrc[:0])
    check("_force_k 仍保留（只为对照/兼容）", hasattr(diarize, "_force_k"))

    # 算法行为：k-means 在"两团分得很开"的合成数据上必须分对
    import numpy as np
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.05, (10, 8)) + np.eye(8)[0]
    b = rng.normal(0, 0.05, (10, 8)) - np.eye(8)[0]
    X = np.vstack([a, b])
    lab = np.array(diarize._kmeans(X, 2))
    truth = np.array([0] * 10 + [1] * 10)
    acc = max((lab == truth).mean(), (1 - lab == truth).mean())
    check("_kmeans 在可分数据上 100%", acc == 1.0, f"{acc}")
    check("_kmeans 标签紧凑从 0 开始", set(lab.tolist()) == {0, 1}, str(set(lab.tolist())))
    check("_kmeans k=1 时全 0", set(diarize._kmeans(X, 1)) == {0})

    # 注释里必须留下"人数只能靠元数据"的结论，免得后人又去调阈值
    check("diarize 说明了人数判定为何不可靠",
          "BIC" in inspect.getsource(diarize) or "元数据" in inspect.getsource(diarize)
          or "判不出来" in inspect.getsource(diarize))


def test_textread():
    """[19] 歌词文件编码探测（GBK 等）—— 写死 utf-8 会让整个转换失败"""
    print("\n[19] 文本编码探测（textread）")
    import io
    import os
    import shutil

    import textread

    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", "selftest_enc")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)

    simp = "[00:01.00]站在旅途的尽头\n[00:05.00]面对下一个岔路口\n"
    trad = "[00:01.00]站在旅途的盡頭\n[00:05.00]面對下一個岔路口\n"

    def put(name, data):
        p = os.path.join(d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    cases = [
        ("gbk.lrc", simp.encode("gb18030"), "尽", "gb18030", False),
        ("u8.lrc", simp.encode("utf-8"), "尽", "utf-8", False),
        ("u8bom.lrc", b"\xef\xbb\xbf" + simp.encode("utf-8"), "尽", "utf-8-sig", False),
        ("u16.lrc", simp.encode("utf-16"), "尽", "utf-16", False),
        ("ascii.lrc", b"[00:01.00]hello world\n", "hello", "utf-8", False),
    ]
    for name, data, want, want_enc, want_dirty in cases:
        p = put(name, data)
        text, enc, dirty = textread.read_text_best_effort(p)
        check(f"{name}：解出正确内容", want in text, repr(text[:30]))
        check(f"{name}：识别为 {want_enc}", enc == want_enc, enc)
        check(f"{name}：不误报损坏", dirty is False, str(dirty))

    # Big5 会被 gb18030 静默误解 —— 但**必须报警**，不能闷着给用户乱码
    p = put("big5.lrc", trad.encode("big5"))
    text, enc, dirty = textread.read_text_best_effort(p)
    check("Big5：解不对但**会被标成可疑**（不静默）", dirty is True,
          f"enc={enc} dirty={dirty} text={text[:20]!r}")

    # 垃圾字节：绝不能抛 UnicodeDecodeError（那正是用户遇到的崩溃）
    p = put("junk.bin", bytes([0xce, 0xce, 0xff, 0xfe, 0x00, 0x81]))
    try:
        text, enc, dirty = textread.read_text_best_effort(p)
        check("垃圾字节不抛异常", True)
        check("垃圾字节被标成可疑", dirty is True, f"enc={enc}")
    except UnicodeDecodeError as e:
        check("垃圾字节不抛异常", False, str(e))

    check("read_text 直接给文本", textread.read_text(
        os.path.join(d, "gbk.lrc")).startswith("[00:01.00]站在"))
    check("describe 说明编码", "GBK" in textread.describe(os.path.join(d, "gbk.lrc")),
          textread.describe(os.path.join(d, "gbk.lrc")))

    shutil.rmtree(d, ignore_errors=True)


def test_roundtrip_fidelity():
    """[20] 编辑台保存的保真度：只该改用户改的，别的一律原样

    这里守的是一个**真实踩过的 bug**：`make_writer` 曾把 ``mode="amll"`` /
    ``markers="agent"`` 写死，于是保存一份 Apple 模式的
    ``<p ttm:role="x-duet-a">`` 文件会把角色**整个删掉**——那正是 Apple Music
    左右分栏依赖的东西，等于"修一行声部，毁掉整个分栏"。同时还会凭空
    加上 ``itunes:songPart``、把 ``v2`` 的 ``type="other"`` 改成 ``person``。
    """
    print("\n[20] 编辑台保存保真度（回写不能改坏源文件）")
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
    from ttml_reader import doc_to_ttml, parse_ttml

    # 一份"Apple 模式 + 对唱角色 + v2 是 other 类型 + 没有 songPart"的文件
    src = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"'
        ' xmlns:itunes="http://music.apple.com/lyric-ttml-internal" xml:lang="zh-Hans"'
        ' itunes:timing="Word">\n'
        '  <head>\n'
        '    <metadata xmlns="http://music.apple.com/lyric-ttml-internal">\n'
        '      <ttm:agent type="person" xml:id="v1"/>\n'
        '      <ttm:agent type="other" xml:id="v2"/>\n'
        '    </metadata>\n'
        '  </head>\n'
        '  <body dur="00:00:20.000">\n'
        '    <div begin="00:00:01.000" end="00:00:19.000">\n'
        '      <p begin="00:00:01.000" end="00:00:05.000" ttm:agent="v1"'
        ' ttm:role="x-duet-a" xml:id="L1" itunes:key="L1">'
        '<span begin="00:00:01.000" end="00:00:03.000">甲</span>'
        '<span begin="00:00:03.000" end="00:00:05.000">乙</span></p>\n'
        '      <p begin="00:00:06.000" end="00:00:10.000" ttm:agent="v2"'
        ' ttm:role="x-duet-b" xml:id="L2" itunes:key="L2">'
        '<span begin="00:00:06.000" end="00:00:08.000">丙</span>'
        '<span begin="00:00:08.000" end="00:00:10.000">丁</span></p>\n'
        '    </div>\n'
        '  </body>\n'
        '</tt>\n')
    out = doc_to_ttml(parse_ttml(src))
    # 手写的夹具没有我们写器的完整头部（会补 xmlns:tts），所以断言"幂等"而不是"逐字节"
    check("回写是幂等的（再解析再写不再变）", doc_to_ttml(parse_ttml(out)) == out)
    check("角色没丢", 'ttm:role="x-duet-a"' in out and 'ttm:role="x-duet-b"' in out)
    check("v2 的 type 保持 other（没被改成 person）",
          'type="other" xml:id="v2"' in out, out[:400])
    check("没有凭空加 songPart", "itunes:songPart" not in out)

    d = parse_ttml(src)
    check("读到行级 ttm:role", {l.role for l in d.lines} == {"x-duet-a", "x-duet-b"},
          str({l.role for l in d.lines}))
    check("推断出 markers=both", d.write_options.get("markers") == "both",
          str(d.write_options))
    check("推断出 line_id=both", d.write_options.get("line_id") == "both",
          str(d.write_options))
    check("源文件没有 songPart → 不自动猜", d.write_options.get("label_sections") is False,
          str(d.write_options))

    # 只改一行的声部，其余必须原样
    d.lines[0].agent = "v2"
    d.lines[0].role = "x-duet-b"
    changed = doc_to_ttml(d)
    check("改声部后角色没被删", 'ttm:role="x-duet-a"' in changed or
          'ttm:role="x-duet-b"' in changed, changed[:200])
    check("改声部后 v2 的 type 仍是 other（没被改成 person）",
          'type="other" xml:id="v2"' in changed, changed[:400])
    check("改声部后没凭空加 songPart", "itunes:songPart" not in changed)
    check("改动只影响这一行的 agent",
          changed.count('ttm:agent="v2"') == src.count('ttm:agent="v2"') + 1,
          f'{changed.count(chr(34).join(["ttm:agent=", "v2", ""]))}')
    # 新增一个 v3 必须自动出现在 head 声明里
    d.lines[1].agent = "v3"
    d.lines[1].role = ""
    with3 = doc_to_ttml(d)
    check("新增 v3 会自动声明", 'xml:id="v3"' in with3, with3[:300])

    # 真实产物（我们自己的写器生成的）必须**逐字节**往返一致 —— 最强的保真度断言
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("在出发之前.duet.ttml", "在出发之前.auto-duet.ttml", "它像一颗.ttml"):
        p = os.path.join(here, "outputs", name)
        if not os.path.exists(p):
            print(f"  (跳过 {name}：文件不存在)")
            continue
        with open(p, encoding="utf-8") as f:
            original = f.read()
        check(f"{name}：逐字节往返一致",
              doc_to_ttml(parse_ttml(original)) == original)


def test_lrc_no_decimal_time():
    """[21] LRC 时间标签的宽容度：`[00:04]`（无小数秒）也必须被认成 LRC

    守的是一个**真实精度 bug**：LRC 判定正则原来写死 `\\.\\d+`（要求小数秒），
    而时间戳提取正则用的是 `\\.?\\d*`（可选）。于是整份"无小数秒"的 LRC
    会掉进纯文本分支，两个后果：
      1) 丢掉全部行级锚点 → 退化成全曲全局对齐，精度明显下降；
      2) 时间戳文本 `[00:04]` 被当成歌词留在正文里，一路写进 TTML。
    两条正则必须保持同一严格度。
    """
    print("\n[21] LRC 时间标签宽容度（无小数秒 / 多时间戳 / 元信息行）")

    # ① 无小数秒：必须仍被认成 LRC
    lines = parse_lyrics("[00:04]甲\n[00:08]乙\n")
    check("无小数秒 LRC：识别为 LRC（有行级锚点）",
          all(l.line_start is not None for l in lines) and len(lines) == 2,
          str([(l.line_start, l.text) for l in lines]))
    check("无小数秒 LRC：时间戳不泄漏进歌词",
          all("[" not in l.text for l in lines),
          str([l.text for l in lines]))
    check("无小数秒 LRC：时间解析正确（[00:08] → 8.0s）",
          [round(l.line_start, 3) for l in lines] == [4.0, 8.0],
          str([l.line_start for l in lines]))

    # ② 有小数秒仍然正确（防止改坏原路径）
    lines2 = parse_lyrics("[00:04.37]甲\n[00:08.19]乙\n")
    check("有小数秒 LRC：仍是 LRC 且保留毫秒",
          [round(l.line_start, 3) for l in lines2] == [4.37, 8.19],
          str([l.line_start for l in lines2]))

    # ③ 混用：两行都要有锚点（旧实现在整份判定上会漏，混用时也会截断精度）
    lines3 = parse_lyrics("[00:04]甲\n[00:08.19]乙\n")
    check("混合精度：两行都有行级锚点",
          all(l.line_start is not None for l in lines3),
          str([(l.line_start, l.text) for l in lines3]))

    # ④ 一行多个时间戳
    lines4 = parse_lyrics("[00:04][00:08.19]同一句\n")
    check("一行多时间戳：展开成 2 行且都不带时间戳文本",
          len(lines4) == 2 and all("[" not in l.text for l in lines4),
          str([(l.line_start, l.text) for l in lines4]))

    # ⑤ 元信息行仍被跳过、空行仍分段
    lines5 = parse_lyrics("[ti:标题]\n[ar:歌手]\n[00:01.00]第一段\n\n[00:05.00]第二段\n")
    check("元信息行 [ti:]/[ar:] 仍被跳过",
          len(lines5) == 2 and not any("ti:" in l.text or "ar:" in l.text for l in lines5),
          str([(l.line_start, l.text, l.paragraph) for l in lines5]))
    check("空行仍然分段（paragraph 变化）",
          sorted({l.paragraph for l in lines5}) == [0, 1],
          str([l.paragraph for l in lines5]))

    # ⑥ 纯文本不能被误判成 LRC（反向保护）
    plain = parse_lyrics("没有时间戳的一行\n另一行\n")
    check("纯文本仍判为无锚点",
          all(l.line_start is None for l in plain),
          str([(l.line_start, l.text) for l in plain]))


def test_edit_backfill_hints():
    """[22] 编辑台带回 matched/prob（红/黄提示的数据源）

    守的是一个**真实功能失效**：TTML 文本里没有 prob/matched，
    `parse_ttml` 只能写死 `matched=True`、`prob=0`。而编辑台的"红=插值补的字 /
    黄=ASR 可信度低"读的正是这两个字段 —— 不回填的话，这两种提示**永远不会出现**。
    修法是从内存里的对齐报告（`result.report.lines`）按层回填。
    """
    print("\n[22] 编辑台带回 matched/prob（红/黄提示）")
    import edit_api as E
    from matcher import AlignedLine
    from ttml_reader import doc_to_aligned, parse_ttml

    def _line(text, start, role=""):
        al = AlignedLine(text=text, agent="v1", begin=start,
                         end=start + 0.4 * len(text), role=role)
        for i, ch in enumerate(text):
            al.units.append(Unit(core=ch, index=i, start=start + 0.4 * i,
                                 end=start + 0.4 * (i + 1),
                                 prob=0.9, matched=True))
        al.matched = al.total = len(text)
        return al

    # 造一行：第 2 个是"插值补的字"（matched=False），第 3 个可信度低（prob=0.3）
    al1 = _line("甲乙丙丁", 1.0)
    al1.units[1].matched = False
    al1.units[1].prob = 0.0
    al1.units[2].prob = 0.3
    al1.matched = sum(1 for u in al1.units if u.matched)
    # 和声行：验证按层回填不会串位
    al2 = _line("戊己", 3.0)
    al2.units[0].is_bg = True
    al2.units[1].is_bg = True
    al2.units[0].matched = False
    al2.units[0].prob = 0.0
    al2.matched = sum(1 for u in al2.units if u.matched)

    ttml = TTMLWriter().write([al1, al2], language="zh-Hans", duration=10.0)

    # ① 先证明"问题真实存在"：只从文本解析，信息确实是丢的
    plain = parse_ttml(ttml)
    check("（问题复现）只从 TTML 文本解析：matched 恒 True、prob 恒 0",
          all(w.matched for ln in plain.lines for w in ln.words)
          and all(w.prob == 0 for ln in plain.lines for w in ln.words))

    # ② 回填后：插值字与低可信字都回来了
    doc = parse_ttml(ttml)
    n = E.backfill_align_hints(doc, [al1, al2])
    check("回填覆盖了报告里的行", n == 2, str(n))

    l0 = doc.lines[0]
    check("插值字被标出（matched=False）",
          l0.words[1].matched is False,
          str([(w.text, w.matched, w.prob) for w in l0.words]))
    check("可信度低的字带回 prob=0.3",
          abs(l0.words[2].prob - 0.3) < 1e-9,
          str([(w.text, w.matched, w.prob) for w in l0.words]))
    check("已匹配字仍是 matched=True",
          l0.words[0].matched is True and l0.words[3].matched is True)

    # ③ 和声层也要正确回填（按层对齐，不能串到主唱上）
    bg0 = doc.lines[1].words[0]
    check("和声层的插值字也被标出（按层不串位）",
          bg0.matched is False and getattr(bg0, "is_bg", False),
          str([(w.text, w.matched, w.is_bg) for w in doc.lines[1].words]))

    # ④ stats 能给出"最该人工看"的字数（前端/日志都用它）
    check("stats()['unmatched'] 反映插值字数", doc.stats()["unmatched"] == 2,
          str(doc.stats()))

    # ⑤ 计数不一致时必须保守跳过，而不是把 prob 贴到别的字上
    doc2 = parse_ttml(ttml)
    E.backfill_align_hints(doc2, [al1])          # 少给一行 → 第二行不参与
    check("报告行数不足时不越界、不串位",
          doc2.stats()["lines"] == 2
          and all(w.matched for w in doc2.lines[1].words),
          str([(w.text, w.matched) for w in doc2.lines[1].words]))

    # ⑥ 回填不能破坏"逐字节往返"（写的还是那份 TTML）
    from ttml_reader import doc_to_ttml
    check("回填后回写仍与源文件一致", doc_to_ttml(doc) == ttml)

    # ⑦ doc_to_aligned 会把这两个字段继续带下去（闭环）
    back = doc_to_aligned(doc)
    check("回写路径仍吃到 matched/prob",
          back[0].units[1].matched is False and abs(back[0].units[2].prob - 0.3) < 1e-9,
          str([(u.core, u.matched, u.prob) for u in back[0].units]))


def test_robustness_fixes():
    """[23] 两处健壮性修复：产物原子写 + 分离缓存键带模型名

    守两个"静默出错"的场景：
    1. 产物 TTML 曾用普通 `open().write` —— 转换中途崩溃会留下**半截文件**，
       而用户看不出它坏了。现在与编辑台保存一样走原子写。
    2. 人声分离缓存文件名曾只有歌名（`{歌名}.vocals.wav`），而复用判据只看
       "文件存在且比音频新" —— 换了 `--demucs-model` 会**静默复用**旧模型的产物。
    """
    print("\n[23] 健壮性：产物原子写 / 分离缓存键带模型名")
    import os
    import shutil

    import edit_api as E
    from pipeline import vocals_cache_path

    here = os.path.dirname(os.path.abspath(__file__))
    tmpd = os.path.join(here, ".tmp", "selftest_robust")
    shutil.rmtree(tmpd, ignore_errors=True)
    os.makedirs(tmpd, exist_ok=True)

    # ---------- ① 原子写：不留半截文件，也不留临时文件 ----------
    target = os.path.join(tmpd, "out.ttml")
    payload = "<tt>" + "x" * 5000 + "</tt>"
    E.atomic_write_text(target, payload)
    check("原子写：内容完整（大文本不截断）",
          open(target, encoding="utf-8").read() == payload)
    leftovers = [f for f in os.listdir(tmpd) if f.startswith(".tmp_")]
    check("原子写：不留 .tmp_ 临时文件", not leftovers, str(leftovers))

    # 覆盖已有文件也必须成功、且内容被替换
    E.atomic_write_text(target, payload + "NEW")
    check("原子写：能覆盖已存在的文件",
          open(target, encoding="utf-8").read().endswith("NEW"))

    # 目录不存在时自动创建（产物目录被删掉也不该崩）
    nested = os.path.join(tmpd, "a", "b", "c.ttml")
    E.atomic_write_text(nested, "hi")
    check("原子写：目录不存在时自动建目录",
          os.path.exists(nested) and open(nested, encoding="utf-8").read() == "hi")

    # 回归：产物写路径必须真的用原子写（防止又被改回 open().write）
    ws = open(os.path.join(here, "web_server.py"), encoding="utf-8").read()
    check("产物 TTML 走的是 atomic_write_text（不是裸 open().write）",
          "edit_api.atomic_write_text(out_path, result.ttml)" in ws)

    # ---------- ② 分离缓存键必须带模型名 ----------
    # ⚠️ 用**假路径**：这里只需要路径的形态，不该写真实音乐目录
    # （开源后那会暴露作者的本机结构）。
    audio = r"D:\music\demo song - artist A & artist B.wav"
    p_base = vocals_cache_path(audio, "htdemucs", tmpd)
    p_ft = vocals_cache_path(audio, "htdemucs_ft", tmpd)
    p_again = vocals_cache_path(audio, "htdemucs", tmpd)
    check("缓存键：同一个模型 → 同一个路径（仍能命中复用）",
          p_base == p_again, os.path.basename(p_base))
    check("缓存键：换模型 → 不同路径（不会静默复用旧产物）",
          p_base != p_ft,
          f"{os.path.basename(p_base)} vs {os.path.basename(p_ft)}")
    check("缓存键：模型名出现在文件名里",
          "htdemucs_ft" in os.path.basename(p_ft),
          os.path.basename(p_ft))
    check("缓存键：仍带 .vocals.wav 后缀（旧查找习惯不变）",
          p_ft.endswith(".vocals.wav"), os.path.basename(p_ft))
    check("缓存键：输出目录按参数走",
          os.path.dirname(p_base) == tmpd, os.path.dirname(p_base))
    # 文件名里的危险字符要被清掉
    p_odd = vocals_cache_path(r"E:\x\a b/c:d*?.wav", "htdemucs", tmpd)
    check("缓存键：文件名不含路径分隔符/通配符",
          not any(c in os.path.basename(p_odd) for c in '\\/:*?"<>|'),
          os.path.basename(p_odd))

    shutil.rmtree(tmpd, ignore_errors=True)


def test_experiment_isolation():
    """[21] 实验区隔离：声纹候选模型**不得**进入默认运行路径

    决策依据见 docs/声纹评估协议.md §7：Sony CSL `ssl-singer-identity` 的 5 个变体
    在同一评估集上全部明显落后（最好 68.8% vs 基线 89.9%），已拒绝、路线冻结。
    适配器与权重保留在实验区（`singer_ssl.py` + `models/singer_identity/`），
    但**生产模块一行都不许引用**——否则以后有人顺手接上就把基线换掉了。
    """
    print("\n[24] 实验区隔离（候选声纹模型不得进生产路径）")
    import os
    import re

    here = os.path.dirname(os.path.abspath(__file__))
    forbidden = ("singer_ssl", "singer_identity")
    # 生产链路：跑一次转换会经过的全部模块
    production = ["pipeline.py", "diarize.py", "speaker_campplus.py",
                  "main.py", "web_server.py", "matcher.py", "asr.py",
                  "separate.py", "preprocess.py", "edit_api.py",
                  "ttml_writer.py", "ttml_reader.py"]

    bad = []
    for name in production:
        p = os.path.join(here, name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            src = f.read()
        # 只看代码行，注释里提到模型名不算违规
        for ln, line in enumerate(src.splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if any(k in line for k in forbidden):
                bad.append(f"{name}:{ln}")
    check("生产模块没有引用实验适配器", not bad, "、".join(bad))

    # 生产声纹仍然走 CAM++（显式路径，不是 glob，避免误加载）
    src = open(os.path.join(here, "speaker_campplus.py"), encoding="utf-8").read()
    check("CAM++ 权重路径是写死的（不是 glob）",
          'os.path.join(_HERE, "models", "campplus"' in src, src[:0])
    check("diarize 走 speaker_campplus",
          "import speaker_campplus as _spk" in open(
              os.path.join(here, "diarize.py"), encoding="utf-8").read())

    # 实验权重放在独立目录，且评估工具能正常导入它
    # ⚠️ models/ **不随仓库分发**（体积 + 授权）。判断条件不能写"models/ 存在吗"——
    # 前面的测试组（Demucs）会创建 models/demucs/，于是新克隆里 models/ 会出现，
    # 但里面根本没有生产权重。真正该守的不变量是"**有生产权重时**，
    # 实验权重不许混进生产目录"，所以看 models/campplus/ 在不在。
    exp_dir = os.path.join(here, "models", "singer_identity")
    prod_dir = os.path.join(here, "models", "campplus")
    if os.path.isdir(prod_dir):
        check("实验权重在独立目录 models/singer_identity/", os.path.isdir(exp_dir))
    else:
        print("  (未分发生产权重 models/campplus/，跳过实验权重目录检查)")
    try:
        sys.path.insert(0, os.path.join(here, "tools"))
        import eval_diarization  # noqa: F401
        check("评估工具可导入（实验路径只走它）", True)
    except Exception as e:
        check("评估工具可导入（实验路径只走它）", False, f"{type(e).__name__}: {e}")

    # 默认运行的 CLI 里不应出现候选模型的开关
    # （注意 `--split-singers` 是合法的生产参数，所以不能只匹配 "singer"）
    src = open(os.path.join(here, "main.py"), encoding="utf-8").read()
    check("CLI 没有候选模型的开关",
          not any(k in src for k in ("ssl-singer", "singer_ssl",
                                     "singer-identity", "singer_identity")), src[:0])


def test_audio_path_guard():
    """[22] 编辑台音频端点：防目录穿越 + 前端标记齐全（离线可测，不起 HTTP）"""
    print("\n[25] 音频端点安全与前端接线")
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import web_server as W

    cases = [
        ("uploads/../web_server.py", 403),
        ("C:/Windows/win.ini", 403),
        ("../../../../etc/passwd", 403),
        ("/etc/shadow", 403),
        ("", 404),
        ("uploads/绝对不存在的文件.wav", 404),
    ]
    for raw, want in cases:
        _, err = W.safe_audio_path(raw)
        got = 200 if err is None else err[0]
        check(f"拒绝越界路径 {raw[:30] or '(空)':<32} → {want}", got == want, f"得到 {got}")

    # uploads/ 内的真实文件必须放行
    real = None
    updir = os.path.join(here, "uploads")
    if os.path.isdir(updir):
        for n in os.listdir(updir):
            if n.lower().endswith((".wav", ".flac", ".mp3")):
                real = os.path.join("uploads", n)
                break
    if real:
        p, err = W.safe_audio_path(real)
        check("uploads/ 内的音频放行", err is None and p and os.path.isfile(p), str(err))
    else:
        print("  (uploads/ 里没有音频，跳过放行检查)")

    # Range 解析是内联在 _serve_range 里的，这里检查它按规范声明了 Accept-Ranges
    src = open(os.path.join(here, "web_server.py"), encoding="utf-8").read()
    check("_serve_range 会发送 Accept-Ranges", 'send_header("Accept-Ranges", "bytes")' in src)
    check("支持 206 / 416", "206 if partial else 200" in src and "416" in src)
    check("支持后缀 Range（bytes=-n）", "最后 n 个字节" in src)
    check("播放器提前断开不会抛错", "BrokenPipeError" in src)

    # 前端接线
    html = open(os.path.join(here, "index.html"), encoding="utf-8").read()
    # 页面脚本正文（多处检查要用，提前取；用模块级的 re，_re 在下面才定义）
    _script = re.search(r"<script>(.*?)</script>", html, re.S)
    js = _script.group(1) if _script else ""
    for token, desc in (("id=\"edAudio\"", "<audio> 元素"),
                        ("id=\"seekBar\"", "进度条"),
                        ("function tickHighlight", "实时高亮"),
                        ("function findWord", "按时间二分找字"),
                        ("function loadAudio", "挂 /api/audio"),
                        ("loopWord", "循环当前行")):
        check(f"前端有{desc}", token in html)
    check("点字会跳转并播发", "seekTo(w.begin)" in html and "togglePlay()" in html)
    # 真踩过的坑：给 .editor-player 写了 display:none 却没人打开它 → 编辑台里看不到播放器
    import re as _re
    m = _re.search(r"\.editor-player\s*\{([^}]*)\}", html)
    check("播放器没有被 CSS 藏起来（.editor-player 不含 display:none）",
          bool(m) and "display:none" not in m.group(1).replace(" ", ""),
          m.group(1)[:70] if m else "没找到 .editor-player 样式")
    check("播放器默认可见（display:flex）",
          bool(m) and "display:flex" in m.group(1).replace(" ", ""),
          m.group(1)[:70] if m else "")

    # 播放高亮必须能盖过状态色。
    # 踩过的坑：`.ed-w.now` 写在 `.ed-w.is-bg/.is-weak/.is-interp` 之前，
    # 同权重时后者胜出 → 高亮恰好在最该看的字上不显示。
    # 判据就用"类选择器个数"（. 的个数）比大小，同数再看谁在后面 —— 简单且不会算错。
    def _spec(sel):
        return (sel.count(".") + sel.count("#") + sel.count("["))

    css = _re.search(r"<style>(.*?)</style>", html, _re.S).group(1)
    # ⚠️ 必须先剥注释：注释里的示例选择器（如注释中写的 `.ed-w.is-bg/.is-weak`）
    # 会被规则正则当成选择器的一部分，把类数算大 → 守卫假通过。
    css = _re.sub(r"/\*.*?\*/", "", css, flags=_re.S)
    rules = []          # (顺序, 选择器, 正文)
    for i, rm in enumerate(_re.finditer(r"([^{}]+)\{([^}]*)\}", css)):
        for s in rm.group(1).split(","):
            rules.append((i, s.strip(), rm.group(2)))

    def _has_base(sel, base):
        """base 必须是完整的选择器片段（`.ed-w` 不该匹配到 `.ed-words`）"""
        return bool(_re.search(_re.escape(base) + r"(?![\w-])", sel))

    for base, state in ((".ed-w", ".ed-w.is-weak"), (".ed-w", ".ed-w.is-interp"),
                        (".ed-w", ".ed-w.is-bg"), (".ed-line", ".ed-line.ed-duet")):
        now_sel = [(i, s) for i, s, b in rules
                   if _has_base(s, base) and ".now" in s and "background" in b]
        st = [(i, s) for i, s, b in rules if s == state and "background" in b]
        if not now_sel or not st:
            continue
        n_spec, n_pos = max((_spec(s), i) for i, s in now_sel)
        s_spec, s_pos = _spec(st[0][1]), st[0][0]
        wins = n_spec > s_spec or (n_spec == s_spec and n_pos > s_pos)
        check(f"播放高亮能盖过 {state}（类数 {n_spec} vs {s_spec}，位置 {n_pos} vs {s_pos}）",
              wins, f"now={[s for _, s in now_sel]}")

    # 高亮色不能和状态色撞车
    now_rule = next((b for _, s, b in rules if ".now" in s and "background" in b
                     and ".ed-w" in s), "")
    for color, who in (("#3a5a4a", "背景和声绿"), ("#8a7a2a", "低可信黄"),
                       ("#8a3a3a", "插值红")):
        check(f"高亮色不是{who} {color}", color not in now_rule)
    check("高亮用青色 #22d3ee", "#22d3ee" in now_rule,
          now_rule.replace(chr(10), " ")[:70])

    # 声部界面也要能点字跳转 + 有独立当前行样式
    check("声部界面逐字渲染（.ed-aword）", ".ed-aword" in html)
    check("声部界面点字会跳转", bool(_re.search(
        r"ed-aword[\s\S]{0,700}?seekTo\(", html)))
    check("声部界面当前行有独立样式（agent-line.now）",
          ".ed-line.agent-line.now" in css)

    # 视图跟随：编辑区是 max-height:420px 的滚动框，不跟随的话高亮会滚出可视区
    check("编辑区确实是滚动容器（所以才需要跟随）",
          bool(_re.search(r"\.editor-body\s*\{[^}]*max-height[^}]*overflow:\s*auto",
                          css)))
    check("有跟随滚动开关", 'id="followScroll"' in html)
    check("有把当前行滚进视野的实现", "function scrollLinesIntoView" in html)
    check("跟随滚动只滚编辑区（自己算，不用 scrollIntoView 免带跳整页）",
          "getBoundingClientRect" in html and "box.scrollTop +=" in html)
    # 播放器上的"当前：字 · 时间 · 时长 · 行/声部"读数
    check("播放器有当前字读数", 'id="nowInfo"' in html and "function updateNowInfo" in html)
    check("读数含时长与行号",
          "时长 ${dur.toFixed(3)}s" in html and "L${lead.li + 1}" in html)

    # ==== 渲染层：双人叠唱并行高亮 + 咬字/拖音两阶段 ====
    check("有 findWordsAt（同时刻取所有在唱的字）", "function findWordsAt" in html)
    check("有 currentWordsAt（每行取一个）", "function currentWordsAt" in html)
    # wordIndex 按 begin 排序，end **不是有序的** —— 靠"后缀最大 end"才能安全早退，
    # 否则会漏掉"更早开始、仍在拖长音"的字（真 bug：`尽`1.8~3.0 被 `等`2.4~2.8 挡住）
    check("有后缀最大 end 数组（不漏仍在拖音的早起始字）",
          "wordMaxEnd" in html and "if (wordMaxEnd[i] <= t) break" in html)
    check("高亮状态是集合（能同时多字）",
          "nowWordState = {}" in js and "nowWordKey" not in js)
    check("有 wordPhase 判断咬字还是拖音", "function wordPhase" in html)
    check("拖音用独立 class（now-tail）", "now-tail" in html and "now-tail" in css)
    tail_css = _re.search(r"\.ed-words > \.ed-w\.now\.now-tail\s*\{([^}]*)\}", css)
    check("拖音样式不改盒子尺寸",
          bool(tail_css) and not any(k in tail_css.group(1)
                                     for k in ("font-weight", "font-size",
                                               "padding", "margin")))
    check("图例标出了拖音阶段", "lg-tail" in html and "拖音阶段" in html)

    # ==== 连续处理多首歌：界面必须跟着换 ====
    # 用户报的两个 bug：① 第二首处理完编辑台还显示第一首；② 字段有旧值时不再自动填。
    check("有 splitTitleArtists（能判「艺人-歌名」和「歌名-艺人」两种顺序）",
          "function splitTitleArtists" in html)
    check("自动填充有 dirty 跟踪（换歌会更新、又不覆盖手输）",
          "function fillIfAuto" in html and "_autoFilled" in js)
    check("自动填充不再只在空字段时才填（旧 bug 根因）",
          "!titleEl.value.trim()" not in js)
    check("转换完成后会刷新编辑台", "refreshEditorIfOpen" in js
          and _re.search(r"showResult\(t\);\s*resetButton\(\);[\s\S]{0,200}?refreshEditorIfOpen\(\)",
                         js) is not None)
    check("刷新会清掉上一首的模型/索引/高亮缓存",
          "editDoc = null" in js and "wordIndex = []" in js
          and "nowWordState = {}" in js)
    # refreshEditorIfOpen 先把 editDoc 置空再异步重拉，这中间的渲染不能崩
    check("editDoc 为空时渲染不抛异常（renderEditor 有保护）",
          _re.search(r"function renderEditor\(\)\s*\{[\s\S]{0,400}?if \(!editDoc\) return;", js)
          is not None)
    check("editDoc 为空时 renderAgentLines 也有保护",
          _re.search(r"function renderAgentLines\(\)\s*\{[\s\S]{0,200}?if \(!editDoc\) return;", js)
          is not None)

    # ⚠️ 脚本整体必须能解析：重复的 let 声明会让**整段脚本失效**、编辑台全死。
    # 用 node --check 做真正的语法检查（node 不在就跳过，不硬依赖）。
    # 这条抓到过：插入新函数时把 `function fmt(t) {` 顶掉了。
    script = _re.search(r"<script>(.*?)</script>", html, _re.S)
    node = shutil.which("node")
    if script and node:
        os.makedirs(".tmp", exist_ok=True)
        tmp = os.path.join(".tmp", "_selftest_page.js")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(script.group(1))
        try:
            r = subprocess.run([node, "--check", tmp], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=60)
            check("页面脚本语法正确（node --check）", r.returncode == 0,
                  (r.stderr or "")[:200])
        except Exception as e:
            check("页面脚本语法正确（node --check）", False, f"{type(e).__name__}: {e}")
    else:
        print("  (没装 node，跳过脚本语法检查)")

    # ==== 逐字同步高亮：需求驱动的约束（两页一致 / 不影响布局 / 状态对等）====
    # （js 已在函数开头取好）

    # 1) 状态标识只能有一个判定源，否则两个页面必然漂移
    check("状态标识只有一处定义（wordStatusClass）",
          len(_re.findall(r"function wordStatusClass", js)) == 1)
    _calls = len(_re.findall(r"wordStatusClass\(", js))
    check("两个页面都调用它（时间轴 + 声部）", _calls == 3,   # 1 定义 + 2 调用
          f"出现 {_calls} 次")
    check("旧的各写一套判定逻辑已移除",
          "if (w.is_bg) cls +=" not in js and "? ' is-bg' : ''" not in js)

    # 2) 高亮不能改变盒子尺寸 —— 否则每换一个字整行重排，密集歌词里画面会抖
    for m2 in _re.finditer(r"([^\n{}]*\.now[^{]*)\{([^}]*)\}", css):
        sel, body = m2.group(1).strip(), m2.group(2)
        if sel.startswith("同权重") or "→" in sel:
            continue                      # 注释残留（已剥注释，这里兜底）
        bad = [k for k in ("font-weight", "font-size", "padding", "margin",
                           "border-width") if k in body]
        check(f"高亮不改尺寸 {sel[:34]}", not bad, "含 " + ",".join(bad))

    # 3) 状态标识在两个页面必须同等可见（不能一个只有文字颜色）
    for cls in ("is-bg", "is-weak", "is-interp"):
        tw = _re.search(r"\.ed-w\.%s\s*\{([^}]*)\}" % cls, css)
        aw = _re.search(r"\.ed-aword\.%s\s*\{([^}]*)\}" % cls, css)
        check(f"{cls} 两个页面都有底色（同等可见）",
              bool(tw and "background" in tw.group(1))
              and bool(aw and "background" in aw.group(1)))

    # 4) 警告必须压过"这是和声"，否则插值的和声字会显示成绿色、把错误藏起来
    fn = _re.search(r"function wordStatusClass\(w\)\s*\{(.*?)\}", js, _re.S)
    if fn:
        b = fn.group(1)
        check("插值优先于和声（错误不被绿色藏住）",
              b.index("is-interp") < b.index("is-bg"))
        check("低可信优先于和声", b.index("is-weak") < b.index("is-bg"))
    check("图例里标出了'正在播放'", "lg-now" in html and "正在播放" in html)

    # ==== 真·运行时验证：在最小 DOM 里跑页面脚本，断言高亮真的生效 ====
    # 静态检查（CSS 在不在、语法对不对）**测不出"高亮没生效"**——
    # 第一版就因此全绿，而实际播放时一个字都不亮。这个测试第一次跑就抓到两个真 bug：
    #   1) indexWords() 把 wordEls/lineEls 清空 → classList.add('now') 静默失败；
    #   2) wordIndex 没带 text → 播放器读数显示「当前：undefined」。
    runtime = os.path.join(here, "tools", "test_ui_runtime.js")
    if node and os.path.exists(runtime):
        try:
            r = subprocess.run([node, runtime], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            tail = [l for l in out.splitlines() if "结果：" in l]
            check("运行时：逐字高亮真的生效（两个页面）", r.returncode == 0,
                  (tail[0] if tail else out.strip().splitlines()[-1] if out.strip() else ""))
        except Exception as e:
            check("运行时：逐字高亮真的生效（两个页面）", False,
                  f"{type(e).__name__}: {e}")
    else:
        print("  (没装 node，跳过运行时高亮测试)")


def test_tail_extend():
    """[26] 句尾拖音补全（生产路径上的后处理，必须有回归）"""
    print("\n[26] 句尾拖音补全")
    import numpy as np

    import tail_extend as T

    SR = 16000

    class U:
        def __init__(self, s, e, bg=False):
            self.start, self.end, self.is_bg = s, e, bg

    class L:
        def __init__(self, b, e, agent, units, text=""):
            self.begin, self.end, self.agent, self.units, self.text = b, e, agent, units, text

    def tone(t0, t1, amp=0.3):
        """在 [t0,t1) 放一段有能量的声音"""
        a = np.zeros(int(t1 * SR))
        i0, i1 = int(t0 * SR), int(t1 * SR)
        n = max(0, i1 - i0)
        if n:
            tt = np.arange(n) / SR
            a[i0:i1] = amp * np.sin(2 * np.pi * 440 * tt)
        return a

    # 场景：尾字被压扁（1.9→2.0 只有 0.1s），人声一直持续到 2.8s
    # ⚠️ 尾字必须**短**：默认 short_thresh=0.30s，只修明显不合理的短尾字
    audio = tone(1.0, 2.8)
    lines = [L(1.0, 2.0, "v1", [U(1.0, 1.5), U(1.5, 1.9), U(1.9, 2.0)], "一句"),
             L(3.5, 4.5, "v2", [U(3.5, 4.5)], "二句")]
    fixes = T.extend_line_tails(lines, audio, SR)
    check("检测到并补了被压扁的尾音", len(fixes) == 1, str(len(fixes)))
    if fixes:
        check("尾字 end 被延到能量跌落处（≈2.8s）",
              2.7 <= lines[0].units[-1].end <= 2.9, f"{lines[0].units[-1].end:.3f}")
        check("行 end 跟着延（span ⊆ p）", lines[0].end >= lines[0].units[-1].end - 1e-9)
        check("跨歌手允许重叠", fixes[0]["overlap"] is True)
        check("没有动第二行", abs(lines[1].end - 4.5) < 1e-9)

    # ⚠️ 行为已升级：换成**音高判据**后，是否延伸取决于**音频里还在不在唱**，
    # 不再取决于"尾字够不够短"。所以：
    #   后面真的静音了 → 不动；
    #   后面还在拖同一个音 → 即使尾字已有 0.5s 也要延伸（这正是用户报的「膀」病例）。
    l_ok = [L(1.0, 2.0, "v1", [U(1.5, 2.0)]), L(4.0, 5.0, "v2", [U(4.0, 5.0)])]
    check("尾字之后确实静音 → 不动",
          len(T.extend_line_tails(l_ok, tone(1.0, 2.0), SR)) == 0)
    l_long = [L(1.0, 2.0, "v1", [U(1.5, 2.0)]), L(4.0, 5.0, "v2", [U(4.0, 5.0)])]
    fx_long = T.extend_line_tails(l_long, tone(1.0, 3.0), SR)
    check("尾字之后还在拖同一个音 → 延伸（哪怕尾字已有 0.5s）",
          len(fx_long) == 1 and l_long[0].units[-1].end > 2.5,
          f"{len(fx_long)} 处，end={l_long[0].units[-1].end:.3f}")

    # 同歌手：不许越过下一行开头（铁律 4）
    a2 = tone(1.0, 4.0)
    l2 = [L(1.0, 2.0, "v1", [U(1.0, 1.5), U(1.5, 1.9), U(1.9, 2.0)]),
          L(2.3, 3.0, "v1", [U(2.3, 3.0)])]
    T.extend_line_tails(l2, a2, SR)
    check("同歌手尾音不越过下一行开始",
          l2[0].units[-1].end <= 2.3 + 1e-9, f"{l2[0].units[-1].end:.3f}")

    # 短于 min_extend 的不动
    a3 = tone(1.0, 2.02)
    l3 = [L(1.0, 2.0, "v1", [U(1.8, 2.0)]), L(5.0, 6.0, "v2", [U(5.0, 6.0)])]
    before = l3[0].units[-1].end
    T.extend_line_tails(l3, a3, SR)
    check("延伸太短就不动（避免抖动）", abs(l3[0].units[-1].end - before) < 1e-9)

    # 背景和声字不参与（尾音按主唱字算）
    a4 = tone(1.0, 3.0)
    l4 = [L(1.0, 2.0, "v1", [U(1.0, 1.5), U(1.9, 2.0), U(2.0, 2.1, bg=True)]),
          L(4.0, 5.0, "v2", [U(4.0, 5.0)])]
    T.extend_line_tails(l4, a4, SR)
    check("按最后一个**主唱**字延伸（和声字不算尾）",
          l4[0].units[1].end > 2.05, f"{l4[0].units[1].end:.3f}")

    # 空音频 / 全静音 → 不动
    l5 = [L(1.0, 2.0, "v1", [U(1.9, 2.0)]), L(4.0, 5.0, "v2", [U(4.0, 5.0)])]
    check("全静音不延伸", len(T.extend_line_tails(l5, np.zeros(SR * 6), SR)) == 0)
    l6 = [L(1.0, 2.0, "v1", [U(1.9, 2.0)])]
    check("音频太短直接返回", len(T.extend_line_tails(l6, None, SR)) == 0)

    check("摘要文案可读", "补了" in T.summarize([{"extend": 0.5, "overlap": True}])
          or "没有" in T.summarize([]))


def test_credit_filter():
    """[27] 制作人员/标题行过滤（漏掉的会被当歌词强行对齐 → 产出重叠坏行）"""
    print("\n[27] 制作信息行过滤")
    import pipeline
    from preprocess import LyricLine

    def L(text, t=0.5):
        return LyricLine(text=text, line_start=t, line_end=t + 2)

    # 真踩过的：这几类漏网后，三行挤在 0.13/0.18/0.22s，
    # 对齐结果互相重叠 → ttml_check 报"同一演唱者行时间重叠"（格式错误）
    must_skip = [
        ("演唱：", "冒号后是空的标签行"),
        ("迪娜泽黛 —— 半甜气泡安小琪", "角色——配音 演员表"),
        ("纳西妲 —— 花园花玲", "同上"),
        ("大慈树王 —— 沐霏Moeki_", "同上"),
        ("我不曾忘记 (《原神》游戏2023新春会同人曲) - 花玲/张安琪/沐霏", "歌名 - 歌手"),
        ("词：ChiliChill", "词："),
        ("曲：ChiliChill", "曲："),
        ("贝斯：山口進也", "乐器：人名"),
        ("弦乐录音：王小四@金田录音棚", "录音信息"),
        ("编曲 Arranger：刘佳诺", "中英混排标签"),
    ]
    for text, why in must_skip:
        check(f"跳过 {why}：{text[:20]}", pipeline._is_credit_line(L(text)))

    # 真正的歌词不能被误杀
    must_keep = [
        ("我的破木箱", 0.61),
        ("装满枯萎的花", 3.23),
        ("放不下 光与壤", 5.79),
        ("只是我 不能忘", 40.0),          # 30 秒后
        ("爱：不用说出口", 45.0),          # 30 秒后的冒号行
        ("我——还在等你", 50.0),           # 30 秒后的破折号行
        ("光：穿过树林", 60.0),
    ]
    for text, t in must_keep:
        check(f"保留歌词：{text[:16]}（{t}s）",
              not pipeline._is_credit_line(L(text, t)))


def test_agent_monotonic():
    """[28] 同歌手相邻行不许重叠（铁律 4）—— 我在这一处连踩两个坑，必须有回归"""
    print("\n[28] 同歌手行重叠兜底")
    from matcher import AlignedLine, Unit
    from pipeline import LyricToTTML

    def mk(agent, begin, end, words):
        return AlignedLine(text="".join(w[2] for w in words), agent=agent,
                           begin=begin, end=end,
                           units=[Unit(core=w[2], start=w[0], end=w[1])
                                  for w in words])

    # 真踩过的数据形状：《让风告诉你》L35(v1) 结束 114.570 > L36(v1) 开始 114.300
    a = mk("v1", 113.970, 114.570,
           [(113.970, 114.100, "道"), (114.100, 114.250, "发"),
            (114.250, 114.400, "生"), (114.400, 114.570, "了")])
    b = mk("v1", 114.300, 116.030, [(114.300, 114.500, "吃"), (114.500, 116.030, "饱")])
    lines = [a, b]
    notes = []
    p = LyricToTTML.__new__(LyricToTTML)
    p.log = lambda *x: None
    n = p._enforce_agent_monotonic(lines, notes)
    check("检测到同歌手重叠", n == 1, str(n))
    check("行尾被压到下一行开始", abs(a.end - 114.300) < 1e-6, f"{a.end:.3f}")
    check("字全部包在行内（span ⊆ p）",
          all(u.end <= a.end + 1e-9 for u in a.units),
          str([(round(u.start, 3), round(u.end, 3)) for u in a.units]))
    check("字之间不重叠（逐字压会踩的坑）",
          all(a.units[i].end <= a.units[i + 1].start + 1e-9
              for i in range(len(a.units) - 1)),
          str([(round(u.start, 3), round(u.end, 3)) for u in a.units]))
    check("每个字 begin < end", all(u.start < u.end for u in a.units))
    check("产生了改动说明", any("重叠" in x for x in notes), str(notes))

    # 不同歌手之间的重叠必须保留（双人叠唱本来就该重叠）
    c = mk("v2", 1.0, 5.0, [(1.0, 5.0, "叠")])
    d = mk("v1", 2.0, 3.0, [(2.0, 3.0, "唱")])
    p._enforce_agent_monotonic([c, d], [])
    check("不同歌手的重叠不动", abs(c.end - 5.0) < 1e-9, f"{c.end}")
    check("不同歌手的行也不动", abs(d.begin - 2.0) < 1e-9)

    # 本来就不重叠 → 不动
    e = mk("v1", 1.0, 2.0, [(1.0, 2.0, "甲")])
    f = mk("v1", 2.5, 3.0, [(2.5, 3.0, "乙")])
    check("本来不重叠就不动", p._enforce_agent_monotonic([e, f], []) == 0)

    # ⚠️ **必须按 agent 追踪，不能只比相邻条目**（真踩过）：
    # 《让风告诉你》时间线上是 v2 → v1 → v1 → v2，两个 v2 中间隔着别的行。
    # L34(v2) 拖到 124.650，L37(v2) 从 114.530 开始 → 报"同一演唱者 v2 行时间重叠"。
    g1 = mk("v2", 110.53, 124.65, [(110.53, 124.65, "嘿")])
    g2 = mk("v1", 113.97, 114.30, [(113.97, 114.30, "虽")])
    g3 = mk("v1", 114.30, 116.03, [(114.30, 116.03, "吃")])
    g4 = mk("v2", 114.53, 116.22, [(114.53, 116.22, "不")])
    inter = [g1, g2, g3, g4]
    n = p._enforce_agent_monotonic(inter, [])
    check("隔着别的行也能发现同 agent 重叠（交错分布）", n == 1, str(n))
    check("交错情况下 v2 也没重叠", g1.end <= g4.begin + 1e-9,
          f"{g1.end:.3f} vs {g4.begin:.3f}")
    check("没动到中间的两行 v1",
          abs(g2.begin - 113.97) < 1e-9 and abs(g3.end - 116.03) < 1e-9)


def test_onset_align():
    """[29] 首字起音对齐（**单向**：只拉后偏早的行）"""
    print("\n[29] 首字起音对齐")
    import numpy as np

    import onset_align as O

    SR = 16000

    class U:
        def __init__(self, s, e, bg=False):
            self.start, self.end, self.is_bg = s, e, bg

    class L:
        def __init__(self, b, e, units, text=""):
            self.begin, self.end, self.units, self.text = b, e, units, text

    def tone(t0, t1, amp=0.3):
        a = np.zeros(int(t1 * SR) + SR)
        i0, i1 = int(t0 * SR), int(t1 * SR)
        if i1 > i0:
            tt = np.arange(i1 - i0) / SR
            a[i0:i1] = amp * np.sin(2 * np.pi * 440 * tt)
        return a

    # 真实起音在 2.0，首字标成 1.7（提前 0.3s），第二个字在起音之后 —— 自洽的数据
    au = tone(2.0, 3.0)
    a = L(1.7, 3.0, [U(1.7, 1.95), U(2.05, 2.4), U(2.4, 3.0)], "一句")
    fixes = O.snap_line_starts([a], au, SR)
    check("检测到起音偏早并拉后", len(fixes) == 1, str(len(fixes)))
    check("首字被拉到真实起音附近（≈2.0）",
          a.units[0].start >= 1.95, f"{a.units[0].start:.3f}")
    check("行首跟着动（span ⊆ p）", a.begin <= a.units[0].start + 1e-9)

    # ⚠️ 真踩过的坑：移动首字时没夹住第二个字 → 失眠飞行一次报 6 个字时间重叠
    check("首字没有压到第二个字",
          a.units[0].end <= a.units[1].start + 1e-9,
          f"{a.units[0].end:.3f} vs {a.units[1].start:.3f}")
    check("字之间不重叠（整行检查）",
          all(a.units[i].end <= a.units[i + 1].start + 1e-9
              for i in range(len(a.units) - 1)))

    # ⚠️ 已记录的边界：若**第二个字也早于起音**，夹住它就没空间了 → 宁可不改。
    # （真实数据里第二个字通常已在起音之后；但不能为了修而造出重叠）
    b2 = L(1.5, 2.6, [U(1.5, 1.8), U(1.8, 2.2)], "边界")
    before = (b2.units[0].start, b2.units[0].end, b2.units[1].start)
    O.snap_line_starts([b2], au, SR)
    check("挤不下时不动（宁可不修也不造重叠）",
          (b2.units[0].start, b2.units[0].end, b2.units[1].start) == before,
          str((b2.units[0].start, b2.units[0].end, b2.units[1].start)))

    # 本来就准（或有上升沿但只差一点）→ 不动
    b = L(2.05, 3.0, [U(2.05, 2.35), U(2.35, 2.65), U(2.65, 3.0)], "二句")
    check("本来就准就不动", len(O.snap_line_starts([b], au, SR)) == 0)

    # 偏**晚**的绝不能被拉早（单向原则）
    c = L(2.6, 3.4, [U(2.6, 2.9), U(2.9, 3.4)], "三句")
    n = len(O.snap_line_starts([c], tone(2.0, 3.4), SR))
    check("偏晚的行不动（单向：只拉后不提前）",
          n == 0 and abs(c.units[0].start - 2.6) < 1e-9, f"{c.units[0].start}")

    # 一直在响、看不到上升沿 → 无法判定，不动
    d = L(1.0, 3.0, [U(1.0, 2.0), U(2.0, 3.0)], "四句")
    check("看不到上升沿时不乱改", len(O.snap_line_starts([d], tone(0.0, 4.0), SR)) == 0)

    # 全静音 / 无音频
    e = L(1.0, 2.0, [U(1.0, 2.0)])
    check("全静音不动", len(O.snap_line_starts([e], np.zeros(SR * 5), SR)) == 0)
    check("无音频直接返回", len(O.snap_line_starts([e], None, SR)) == 0)

    # 摘要
    check("摘要可读", "拉后" in O.summarize([{"shift": 0.2}])
          or "没有" in O.summarize([]))


def test_sustain_pitch():
    """[30] 音高连续性判拖音（含"野值不该终止一个音"）"""
    print("\n[30] 音高判据测拖音")

    import numpy as np

    import tail_extend as T

    HOP = 0.01

    def f0_of(spec, n_frames, sr_hz=110.0):
        """spec: [(起始帧, 结束帧, 频率)]，未覆盖的帧为 0（无声）"""
        f = np.zeros(n_frames)
        for a, b, hz in spec:
            f[a:b] = hz
        return f

    # 一个稳定拖长音：从帧 0 到 100（1 秒），之后换音（跳到 1.5 倍 = 大跳）
    n = 400
    f0 = f0_of([(0, 100, 220.0), (100, 400, 330.0)], n)
    ext = T.sustain_end(f0, HOP, 0.0, max_extend=2.5)
    check("稳定长音被判为拖音（≈1.0s）", 0.9 <= ext <= 1.1, f"{ext:.3f}")

    # ⚠️ 真踩过的坑：单个八度错的**野值**不该终止一个音。
    # 病例〈我不曾忘记〉「膀」在 75.630 被检成 421Hz（真值 ~235），
    # 音分差 +1111 → 直接判成"换音了"，拖音变成 0。
    f0b = f0_of([(0, 100, 220.0), (30, 31, 440.0), (100, 400, 330.0)], n)
    extb = T.sustain_end(f0b, HOP, 0.0, max_extend=2.5)
    check("单个野值不终止长音", extb >= 0.9, f"{extb:.3f}")

    # 连续多帧跳出音域才算换音
    f0c = f0_of([(0, 100, 220.0), (50, 90, 440.0), (100, 400, 330.0)], n)
    extc = T.sustain_end(f0c, HOP, 0.0, max_extend=2.5)
    check("连续 40 帧跳出音域 → 在那里结束", 0.45 <= extc <= 0.55, f"{extc:.3f}")

    # 低置信度的帧要被忽略
    conf = np.ones(n)
    conf[30:32] = 0.2                      # 低置信野值
    check("低置信帧被忽略", T.sustain_end(f0b, HOP, 0.0, conf=conf, max_extend=2.5) >= 0.9)

    # ⚠️ 真踩过的坑（用户报的第二个病例〈我不曾忘记〉「逢」）：
    # **开头那一帧本身是野值**时，不能拿它当参考音高。
    # 实测：150.650 帧被检成 153.8Hz 而 conf=0.56 刚好过门槛 → 参考被污染成 153.8，
    # 后面真实的 228.6 全被判成"跳出音域"，拖音直接变成 0。
    # 修法：参考音高攒够 min_ref 帧才用来判"离开这个音"（中位数要 ≥3 样本才抗野值）。
    f0f = f0_of([(0, 1, 153.8), (1, 100, 228.6), (100, 400, 330.0)], n)
    extf = T.sustain_end(f0f, HOP, 0.0, max_extend=2.5)
    check("开头一帧是野值也不会把参考带偏", extf >= 0.9, f"{extf:.3f}")

    # 纯静音 / 一进来就静音 → 不延伸
    check("全无声不延伸", T.sustain_end(np.zeros(n), HOP, 0.0) == 0.0)
    f0d = f0_of([(20, 100, 220.0)], n)
    check("起始 0.2s 无声 → 判定不了，不延伸",
          T.sustain_end(f0d, HOP, 0.0) == 0.0)

    # 转音：音高缓慢游走（每帧 +1 音分）应当**仍算同一个音**
    ramp = np.array([220.0 * (2 ** ((i * 1.0) / 1200.0)) for i in range(100)])
    f0e = np.concatenate([ramp, np.full(n - 100, 0.0)])
    check("缓慢转音仍算同一个音", T.sustain_end(f0e, HOP, 0.0, max_extend=2.5) >= 0.9,
          f"{T.sustain_end(f0e, HOP, 0.0, max_extend=2.5):.3f}")

    check("空数组安全", T.sustain_end(np.zeros(0), HOP, 0.0) == 0.0)


def test_edit_text():
    """[31] 编辑台文本编辑：插行/改字/和声（三条不变量必须守住）"""
    print("\n[31] 编辑台文本编辑")
    import copy
    import io
    import os

    import edit_text as X
    from edit_api import validate
    from ttml_model import LAYER_BG, LAYER_MAIN
    from ttml_reader import doc_to_ttml, parse_ttml

    # 用**随仓库分发**的 fixture（新克隆也跑得到）；本机有真实产物就优先用它
    src = "outputs/在出发之前.duet.ttml"
    if not os.path.exists(src):
        src = "tests/fixtures/demo-duet.ttml"
    if not os.path.exists(src):
        check("有可测的 TTML 输入（fixture 缺失，先跑 tools/make_fixture.py）", False, src)
        return
    with io.open(src, encoding="utf-8") as f:
        base = parse_ttml(f.read())

    def invariants(d, tag):
        """层内不重叠 / span ⊆ p / begin<end / key 连续 / 0 错误 / 往返"""
        bad = []
        for i, line in enumerate(d.lines):
            if line.key != "L%d" % (i + 1):
                bad.append("key")
            if not (line.begin < line.end):
                bad.append("行 begin<end")
            for layer in (LAYER_MAIN, LAYER_BG):
                ws = X.layer_words(line, layer)
                for k, w in enumerate(ws):
                    if not (w.begin < w.end):
                        bad.append("字 begin<end")
                    if w.begin < line.begin - 1e-6 or w.end > line.end + 1e-6:
                        bad.append("span⊄p")
                    if k and ws[k - 1].end > w.begin + 1e-6:
                        bad.append("层内重叠")
        res = validate(d)
        errs = len(res["check"]["errors"])
        back = parse_ttml(doc_to_ttml(d))
        rt = (len(back.lines) == len(d.lines)
              and back.stats()["words"] == d.stats()["words"])
        check(f"{tag}：不变量 + 0 错误 + 往返", not bad and errs == 0 and rt,
              f"{sorted(set(bad))} errs={errs} rt={rt}")

    L = base.lines
    n0 = base.stats()["words"]

    d = copy.deepcopy(base)
    X.set_word_text(d, L[3].key, LAYER_MAIN, 0, "改")
    invariants(d, "改一个字")

    d = copy.deepcopy(base)
    X.insert_word(d, L[3].key, LAYER_MAIN, 1, "新")
    check("插一个字：字数 +1", d.stats()["words"] == n0 + 1,
          str(d.stats()["words"] - n0))
    invariants(d, "插一个字")

    d = copy.deepcopy(base)
    X.insert_word(d, L[3].key, LAYER_MAIN, 0, "三个字")
    check("插多字会拆成逐字（+3）", d.stats()["words"] == n0 + 3,
          str(d.stats()["words"] - n0))

    d = copy.deepcopy(base)
    X.delete_word(d, L[4].key, LAYER_MAIN, 0)
    check("删一个字：字数 -1", d.stats()["words"] == n0 - 1)
    invariants(d, "删一个字")

    d = copy.deepcopy(base)
    X.set_line_text(d, L[5].key, "这是一整行新歌词")
    check("整行改文本：按层跨度重新等分",
          X.layer_words(d.lines[5], LAYER_MAIN)[0].text == "这")
    invariants(d, "整行改文本")

    d = copy.deepcopy(base)
    nxt = d.lines[6]
    new = X.insert_line(d, L[5].key, "插在中间的一行")
    check("插入一行：落在空隙里",
          len(d.lines) == len(base.lines) + 1
          and new.begin >= d.lines[5].end - 1e-6
          and new.end <= nxt.begin + 1e-6,
          f"{new.begin:.3f}~{new.end:.3f} vs 下一行 {nxt.begin:.3f}")
    invariants(d, "插入一行")

    d = copy.deepcopy(base)
    X.insert_line(d, "", "插到最前面")
    check("插到最前面（after_key 空串）", d.lines[0].text == "插到最前面")
    invariants(d, "插到最前面")

    d = copy.deepcopy(base)
    X.delete_line(d, L[7].key)
    check("删一行：key 重编号连续",
          len(d.lines) == len(base.lines) - 1
          and [x.key for x in d.lines] == [f"L{i+1}" for i in range(len(d.lines))])
    invariants(d, "删一行")

    d = copy.deepcopy(base)
    X.set_bg_text(d, L[9].key, "和声新词")
    bw = X.layer_words(d.lines[9], LAYER_BG)
    check("设置和声：逐字且 is_bg 为真",
          "".join(w.text for w in bw) == "和声新词" and all(w.is_bg for w in bw),
          "".join(w.text for w in bw))
    invariants(d, "设置和声")

    d = copy.deepcopy(base)
    X.remove_bg(d, L[10].key)
    check("删和声：该行和声清空", not X.layer_words(d.lines[10], LAYER_BG))
    invariants(d, "删和声")

    one = copy.deepcopy(base)
    one.lines = one.lines[:1]
    X.rekey(one)
    try:
        X.delete_line(one, one.lines[0].key)
        check("删掉最后一行要报错", False, "没有报错")
    except X.TextEditError:
        check("删掉最后一行要报错", True)

    d = copy.deepcopy(base)
    try:
        X.set_line_text(d, L[2].key, "")
        check("整行清空要报错", False, "没有报错")
    except X.TextEditError:
        check("整行清空要报错", True)

    d = copy.deepcopy(base)
    out = X.apply_op(d, "insert_line", {"key": L[1].key, "text": "入口测试"})
    check("apply_op 统一入口可用", out["stats"]["lines"] == len(base.lines) + 1)
    try:
        X.apply_op(copy.deepcopy(base), "no_such_op", {})
        check("未知操作要报错", False, "没有报错")
    except X.TextEditError:
        check("未知操作要报错", True)


def test_singer_mode():
    """[32] 演唱者分段模式：关闭 / 强制1 / 强制2 / 强制3"""
    print("\n[32] 演唱者分段模式")
    import io
    import re
    import types

    from pipeline import ConvertOptions as C
    from pipeline import LyricToTTML

    cases = [("off", False, 0, 0), ("1", False, 0, 1), ("2", False, 0, 2),
             ("3", False, 0, 3), ("", True, 0, 2), ("", False, 3, 3),
             ("", False, 0, 0), ("2", True, 3, 2)]
    got = [C(singer_mode=m, auto_agents=a, split_singers=s).forced_singers()
           for m, a, s, _ in cases]
    check("模式解析：off→0 / 1,2,3→N；旧 --auto-agents/--split-singers 仍可用",
          got == [w for *_, w in cases], str(got))
    check("新参数优先于旧参数",
          C(singer_mode="2", auto_agents=True, split_singers=3).forced_singers() == 2)

    pl = LyricToTTML.__new__(LyricToTTML)
    logs = []
    pl.log = lambda m: logs.append(str(m))

    def fake(agents, manual):
        lines = [types.SimpleNamespace(manual_agent=m, agent=a)
                 for a, m in zip(agents, manual)]
        report = types.SimpleNamespace(
            lines=[types.SimpleNamespace(agent=a, role="") for a in agents])
        return lines, report

    lines, report = fake(["v1", "v2", "v1"], [True, True, True])
    notes = []
    pl._use_lyric_markers(lines, report, notes, ["陶典", "hanser"])
    check("关闭：用歌词里的 v1/v2 标记",
          any("用歌词标记" in n for n in notes)
          and any("实际用到 2 个声部" in n for n in notes)
          and report.lines[1].agent == "v2", str(notes))
    check("关闭：日志里点名了用歌词里的演唱者标记",
          any("歌词里的演唱者标记" in m for m in logs), str(logs))
    check("关闭：不跑声纹（日志里没有 CAM++ 聚类）",
          not any("声纹聚类" in m for m in logs), str(logs))

    lines, report = fake(["v1", "v1"], [False, False])
    notes = []
    pl._use_lyric_markers(lines, report, notes, ["陶典", "hanser"])
    check("关闭且无标记：给出「多艺人却全 v1」的可操作提示",
          any("标记" in n for n in notes), str(notes))

    lines, report = fake(["v1", "v1"], [True, True])
    notes = []
    pl._use_lyric_markers(lines, report, notes, ["A"])
    check("标了演唱者却只有一个声部 → 提醒检查标记写法",
          any("只落到一个声部" in n for n in notes), str(notes))

    html = io.open("index.html", encoding="utf-8").read()
    sel = re.search(r'<select id="singers">(.*?)</select>', html, re.S)
    vals = re.findall(r'<option value="([^"]+)"', sel.group(1)) if sel else []
    check("界面四个选项：关闭/强制单/双/三声部", vals == ["off", "1", "2", "3"], str(vals))
    check("界面不再有「自动判断每行是谁唱的」这个选项",
          "自动判断每行是谁唱的" not in html)
    check("界面把 singer_mode 发给后端", "singer_mode: val('singers')" in html)

    main_src = io.open("main.py", encoding="utf-8").read()
    check("CLI 有 --singer-mode {off,1,2,3}",
          '"--singer-mode"' in main_src and '["off", "1", "2", "3"]' in main_src)
    check("CLI 旧参数标注为【旧参数】", "【旧参数】" in main_src)

    # ⚠️ 单声部提示必须**按实际路径**说原因：关闭声纹时根本没跑声纹，
    # 还写"声纹判定为单人演唱"就是误导（真踩过）。
    from pipeline import duet_hint
    h_off = duet_hint(["A", "B"], 1, "歌词里没有演唱者标记、且你选的是「关闭」")
    h_f1 = duet_hint(["A", "B"], 1, "你选的是「强制单声部」")
    h_vp = duet_hint(["A", "B"], 1)
    check("关闭模式不说「声纹判定」（根本没跑声纹）",
          "声纹判定" not in h_off and "关闭" in h_off, h_off[:40])
    check("强制单声部说「强制单声部」", "强制单声部" in h_f1, h_f1[:40])
    check("声纹路径仍说「声纹判定」", "声纹判定" in h_vp, h_vp[:40])
    check("单一艺人时不给这个提示", duet_hint(["A"], 1) == "")
    check("已分出声部时不给这个提示", duet_hint(["A", "B"], 2) == "")
    check("提示里给的是新参数 --singer-mode 2（不是已废弃的 --split-singers）",
          "--singer-mode 2" in h_off and "--split-singers" not in h_off, h_off[-60:])


def test_param_wiring():
    """[33] 参数联调：平台 ID / 语言 / 脏话选项 / 参数生效性 / CLI 能起来"""
    print("\n[33] 参数联调")
    import io
    import os
    import re
    import subprocess

    from ttml_reader import doc_to_ttml, parse_ttml

    html = io.open("index.html", encoding="utf-8").read()

    # ---- ① 新增平台 ID（酷我 / 酷狗）----
    src = "outputs/在出发之前.duet.ttml"
    if not os.path.exists(src):
        src = "tests/fixtures/demo-duet.ttml"
    if not os.path.exists(src):
        check("有可测的 TTML 输入（fixture 缺失，先跑 tools/make_fixture.py）", False, src)
        return
    doc = parse_ttml(io.open(src, encoding="utf-8").read())
    doc.meta = dict(doc.meta or {})
    ids = {"ncmMusicId": "1", "qqMusicId": "2", "appleMusicId": "3",
           "spotifyId": "4", "kuwoMusicId": "5", "kugouMusicId": "6"}
    doc.meta.update(ids)
    out = doc_to_ttml(doc)
    back = parse_ttml(out)
    ok = all((back.meta or {}).get(k) == v for k, v in ids.items())
    check("六个平台 ID 全都能写入并往返（含新增的酷我/酷狗）", ok,
          str({k: (back.meta or {}).get(k) for k in ids}))
    check("界面有酷我/酷狗录入项",
          'id="kuwoId"' in html and 'id="kugouId"' in html)
    check("界面把两个新 ID 发出去",
          "kuwoMusicId: val('kuwoId')" in html and "kugouMusicId: val('kugouId')" in html)
    check("web_server 的平台 ID 列表来自 ttml_writer.META_KEYS（不另抄一份）",
          "from ttml_writer import META_KEYS as _META_KEYS" in
          io.open("web_server.py", encoding="utf-8").read())
    check("自定义键写进 amll:meta", 'key="kuwoMusicId"' in out)

    # ---- ② 语言：auto 必须真接到 whisper 的自动检测 ----
    from asr import is_auto_language, normalize_language_code, whisper_language_code
    check("language=auto → whisper 收空串（= 自动检测）", whisper_language_code("auto") == "")
    check("language=zh → whisper 收 zh", whisper_language_code("zh") == "zh")
    check("auto 不会原样变成 xml:lang（那不是合法 BCP-47）",
          normalize_language_code("auto") not in ("auto", ""))
    check("界面有「自动检测」且默认选中",
          '<option value="auto" selected>自动检测' in html)
    check("is_auto_language 认得 auto/空/自动检测",
          all(is_auto_language(s) for s in ("auto", "", "自动检测"))
          and not is_auto_language("zh"))

    # ---- ③ 脏话选项不再重复 ----
    check("界面已移除「检测被消音屏蔽的词」那个重复开关", "mutedSwitch" not in html)
    check("脏话处理只剩一个下拉", html.count('id="explicitPolicy"') == 1)
    check("detect_muted 固定 false 发给后端（命令行仍可 --detect-muted）",
          "detect_muted: false" in html)

    # ---- ④ 播放条布局：当前演唱文字独立一行 ----
    check("当前演唱文字已移出播放控制条（独立 .editor-now）",
          '<div class="editor-now">' in html and 'class="ed-now"' not in html)
    check(".editor-now 固定行高（高度不随文字变 → 下方页面不抖）",
          ".editor-now {" in html and "height:22px" in html)
    check("当前演唱文字仍然单行省略 + 全文挂 title",
          "#nowInfo {" in html and "text-overflow:ellipsis" in html
          and "el.title = text" in html)

    # ---- ⑤ 文本编辑页也有播放高亮 ----
    check("文本页的 chip 也登记进高亮元素地图",
          "wordEls[li + ':' + flatIdx] = chip;" in html)
    check("文本页 chip 有 .now / .now-tail 样式，配色与另两页一致",
          ".txt-ch.now {" in html and ".txt-ch.now.now-tail" in html
          and "#0e3a46" in html and "#22d3ee" in html)
    check("文本页渲染后同步高亮（切页不丢）",
          "syncHighlightAfterRender();" in html.split("function renderTextLines")[1][:4200])
    check("同一行主唱+和声可并行高亮（按 行+层 去重）",
          "w.li + ':' + (w.is_bg ? 'bg' : 'main')" in html)

    # ---- ⑥ 参数生效性 + CLI 能起来 ----
    r = subprocess.run([sys.executable, "tools/test_param_effect.py"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("参数生效性测试全绿（42 项）", r.returncode == 0,
          (r.stdout or "").strip().splitlines()[-1] if r.stdout else (r.stderr or "")[-200:])
    r2 = subprocess.run([sys.executable, "tools/audit_params.py"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("参数审计：无死字段、无孤立的纯 UI 参数",
          r2.returncode == 0 and "死字段：无" in (r2.stdout or ""),
          (r2.stdout or "").strip().splitlines()[-2:])
    r3 = subprocess.run([sys.executable, "main.py", "--help"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("python main.py --help 退出码 0（help 里的裸 % 会让整个 CLI 挂）",
          r3.returncode == 0, (r3.stderr or "")[-200:])


def test_lrc_marker_lines():
    """[34] LRC 里"标记单独占一行"不能再被静默丢掉"""
    print("\n[34] LRC 独立成行的演唱者标记")
    from preprocess import merge_background_lines, parse_lyrics

    # ⚠️ 这条守的是一个**静默丢数据**的 bug：LRC 里 `[...]` 是时间戳语法，
    # `[v1]` 不是合法时间戳 → 解析时 `for time_str in matches` 一次都不执行
    # → 整行被丢掉。于是"标记单独占一行"这种在 .txt 里完全正常的写法，
    # 在 .lrc 里会悄悄失效：全曲只有 v1、对唱不分栏、**而且不报任何错**。
    txt = "[v1]\n[00:12.00]夜色落在窗台上\n[v2]\n[00:16.50]风把旧信翻了几行"
    lines = parse_lyrics(txt)
    check("LRC：标记单独一行 → 认出来（不是被丢掉）", len(lines) == 2, str(len(lines)))
    check("LRC：两行分别是 v1 / v2",
          [l.agent for l in lines] == ["v1", "v2"], str([l.agent for l in lines]))
    check("LRC：标记不残留在歌词正文里",
          all("[" not in l.text for l in lines), str([l.text for l in lines]))

    zh = "[男声]\n[00:12.00]夜色落在窗台上\n[女声]\n[00:16.50]风把旧信翻了几行"
    check("LRC：中文标记（[男声]/[女声]）同样认",
          [l.agent for l in parse_lyrics(zh)] == ["v1", "v2"])

    bg = merge_background_lines(parse_lyrics("[v1]\n[00:12.00]夜色落在窗台上\n[和声]\n[00:16.50]一路同行"))
    check("LRC：[和声] 单独一行 → 并成背景层（不是丢行、也不是独立成行）",
          len(bg) == 1 and any(tk.is_background for tk in bg[0].tokens), str(len(bg)))

    meta = parse_lyrics("[ti:测试]\n[ar:某人]\n[00:12.00]夜色落在窗台上")
    check("LRC：[ti:]/[ar:] 元数据标签不会被误当成演唱者标记",
          len(meta) == 1 and meta[0].text == "夜色落在窗台上",
          str([l.text for l in meta]))

    # 反向：正文里出现方括号但**不是**标记 → 不能被吃掉
    weird = parse_lyrics("[00:12.00]这行有 [方括号] 但不是标记")
    check("LRC：正文里的方括号不受影响",
          "方括号" in weird[0].text, weird[0].text if weird else "(空)")

    # 仓库自带的示例歌词必须真的能跑（不然 README 的快速开始是坏的）
    import io
    import os
    for f in ("examples/sample.txt", "examples/sample.lrc"):
        if not os.path.exists(f):
            check(f"示例文件存在：{f}", False)
            continue
        ex = merge_background_lines(parse_lyrics(io.open(f, encoding="utf-8").read()))
        agents = sorted({l.agent for l in ex})
        check(f"示例 {f} 解析出 v1+v2 且含和声行",
              agents == ["v1", "v2"]
              and any(tk.is_background for l in ex for tk in l.tokens),
              f"agents={agents}")


if __name__ == "__main__":
    test_scan_line()
    test_dp()
    test_end_to_end()
    test_apple_extensions()
    test_diarize()
    test_apple_style()
    test_separate()
    test_ttml_roundtrip_and_normalize()
    test_duet_and_bg_format()
    test_agent_from_reference()
    test_amll_parser_optional()
    test_edit_api()
    test_diarize_fix()
    test_textread()
    test_roundtrip_fidelity()
    test_experiment_isolation()
    test_audio_path_guard()
    test_tail_extend()
    test_credit_filter()
    test_agent_monotonic()
    test_onset_align()
    test_sustain_pitch()
    test_edit_text()
    test_singer_mode()
    test_param_wiring()
    test_lrc_marker_lines()
    test_lrc_no_decimal_time()
    test_edit_backfill_hints()
    test_robustness_fixes()
    print("\n" + "=" * 50)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for f in FAILED:
            print("  -", f)
        sys.exit(1)
    print("全部自检通过 ✓")
