"""ASR 补词：把音频里识别到、但原歌词没有的句子补进 AlignedLine。

用途（用户需求）：
    原 LRC 漏了句子时，Whisper 仍然识别到这些声音；以前它们会被 DP 当噪声丢掉，
    播放器里永远不显示。这里把落在**所有歌词行之外**的 ASR 词按停顿分组，
    生成新的主唱行；声部按时间最近的已有行继承（v1/v2），随后仍可由声纹/编辑台修。

不处理：
    * 已有行内部的漏字（那属于对齐问题，贸然补字会破坏原行）；
    * 低置信度的孤立词/语气词，避免把气声、伴奏误当歌词。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

from matcher import AlignedLine, AlignReport, Unit

_PUNCT = re.compile(r"^[\s\.,，。!?！？、;；:：'\"“”‘’()（）\[\]【】\-—…♪♫]+$")
_CJK = re.compile(r"[\u3400-\u9fff]")


def _norm(text: str) -> str:
    return re.sub(r"[\s\.,，。!?！？、;；:：'\"“”‘’()（）\[\]【】\-—…♪♫]+", "", text or "").lower()


def _is_word(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and not _PUNCT.match(t) and any(c.isalnum() or _CJK.match(c) for c in t)


def _group(words: Sequence, max_gap: float = 0.85) -> List[List]:
    groups: List[List] = []
    cur: List = []
    for w in words:
        if cur and (w.start - cur[-1].end > max_gap
                    or w.end - cur[0].start > 8.0
                    or sum(len(x.text) for x in cur) > 36):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _split_units(words: Sequence) -> List[Unit]:
    """ASR 词 → 逐字/逐词 Unit；中文一字一格，英文一词一格。"""
    units: List[Unit] = []
    for w in words:
        text = (w.text or "").strip()
        if not text or _PUNCT.match(text):
            continue
        chars = list(text) if _CJK.search(text) else [text]
        n = max(1, len(chars))
        dur = max(0.03, float(w.end) - float(w.start))
        step = dur / n
        for k, ch in enumerate(chars):
            if _PUNCT.match(ch):
                continue
            units.append(Unit(core=ch, start=float(w.start) + step * k,
                              end=float(w.start) + step * (k + 1),
                              prob=float(getattr(w, "prob", 1.0) or 0.0),
                              matched=True))
    # ASR 词时间可能互相重叠；按文本顺序整理成不重叠的逐字区间
    prev = None
    for u in units:
        if prev is not None and u.start < prev.end:
            u.start = prev.end
        if u.end <= u.start:
            u.end = u.start + 0.05
        prev = u
    return units


def supplement_report(report: AlignReport, asr_words: Sequence,
                      *, max_gap: float = 0.85, max_new_lines: int = 24,
                      log=None) -> int:
    """把落在已有歌词行之外的 ASR 词补成新行；返回新增行数。"""
    if not asr_words or not report.lines:
        return 0
    lines = sorted(report.lines, key=lambda l: (l.begin, l.end))
    spans = [(l.begin - 0.30, l.end + 0.30, l) for l in lines]
    unit_spans = []
    for l in lines:
        for u in l.units:
            if u.start is not None and u.end is not None:
                unit_spans.append((u.start - 0.02, u.end + 0.02, l))

    outside = []
    bg_by_line = {}
    for w in asr_words:
        if not _is_word(getattr(w, "text", "")):
            continue
        if float(w.end) <= float(w.start):
            continue
        mid = (float(w.start) + float(w.end)) / 2.0
        line_hit = next((l for a, b, l in spans if a <= mid <= b), None)
        if line_hit is not None:
            # 落在某行范围内：时间被已有字覆盖且文本也在该行里 → 已有内容；
            # 时间被覆盖但文本不在该行 → 识别到的叠字/漏句，进入候选。
            covered = any(a <= mid <= b for a, b, _ in unit_spans
                          if _ is line_hit)
            if _norm(w.text) and _norm(w.text) in _norm(line_hit.text):
                continue
            if covered and _norm(w.text) and _norm(w.text) not in _norm(line_hit.text):
                bg_by_line.setdefault(id(line_hit), []).append(w)
                continue
            bg_by_line.setdefault(id(line_hit), []).append(w)
            continue
        if float(getattr(w, "prob", 0.0) or 0.0) < 0.20:
            continue
        outside.append(w)

    # 行内未覆盖的识别词：若与已有主唱字重叠 → 和声/叠唱；
    # 若落在主唱字的空隙里 → 当成“原歌词漏掉的一句”，走新行逻辑。
    bg_added = 0
    for line in lines:
        ws = bg_by_line.get(id(line)) or []
        if not ws:
            continue
        known_line = _norm(line.text)
        main_units = [u for u in line.main_units
                      if u.start is not None and u.end is not None]
        for group in _group(sorted(ws, key=lambda w: w.start), max_gap=max_gap):
            text = "".join((w.text or "").strip() for w in group)
            n = _norm(text)
            if len(n) < 2 or n in known_line:
                continue
            units = _split_units(group)
            if not units:
                continue
            begin = min(u.start for u in units)
            end = max(u.end for u in units)
            overlaps_main = any(begin < u.end and end > u.start for u in main_units)
            if overlaps_main:
                # 压在主唱字上的短词多半是 ASR 变体/重复；但如果整段识别文本
                # 明显更长（≥3 字）且不是复读，仍可能是被 TIFA 拉长吞掉的漏句。
                if (len(n) >= 3 and (end - begin) >= 0.5
                        and len(set(n)) / max(1, len(n)) >= 0.7):
                    outside.extend(group)
                continue
            outside.extend(group)

    added = 0
    known = {_norm(l.text) for l in lines if l.text}
    for group in _group(sorted(outside, key=lambda w: w.start), max_gap=max_gap):
        if not group:
            continue
        text = "".join((w.text or "").strip() for w in group)
        n = _norm(text)
        if len(n) < 2 or n in known:
            continue
        # 单字反复（“着着”“啊啊”）多半是 ASR 重复/语气词，不是漏句
        if len(set(n)) <= 1:
            continue
        # 大量重复字符（“若若朝朝着着…”）是 ASR 复读，不是新句子
        if len(n) >= 6 and len(set(n)) / len(n) < 0.7:
            continue
        # 中英混杂的短串（“eyebr是”）通常是 ASR 噪声
        if (_CJK.search(n) and re.search(r"[A-Za-z]{2,}", n)):
            continue
        avg_prob = sum(float(getattr(w, "prob", 0.0) or 0.0) for w in group) / len(group)
        if avg_prob < 0.35:
            continue
        # 不要用识别文本去“补”一段已有歌词的近似重复
        if any(n in k or k in n for k in known if len(n) >= 2 and len(k) >= 2):
            continue
        units = _split_units(group)
        if not units:
            continue
        begin = min(u.start for u in units)
        end = max(u.end for u in units)
        if end - begin < 0.60:
            continue
        # 与邻近已有行重复（ASR 在换气处重复上一句）
        near = [l for l in lines if abs(l.begin - begin) < 10.0 or abs(l.end - end) < 10.0]
        if any(n in _norm(l.text) or _norm(l.text) in n for l in near if l.text):
            continue
        # 最近的已有行：继承声部。两侧都有时取时间更近的。
        anchor = min(lines, key=lambda l: min(abs(l.begin - begin), abs(l.end - end)))
        agent = getattr(anchor, "agent", "") or "v1"
        line = AlignedLine(text=text, agent=agent, units=units,
                           begin=begin, end=end, matched=len(units),
                           total=len(units), anchored=False,
                           paragraph=getattr(anchor, "paragraph", 0) or 0)
        line.asr_added = True
        lines.append(line)
        known.add(n)
        added += 1
        if added >= max_new_lines:
            break

    total_added = added + bg_added
    if not total_added:
        return 0
    report.lines = sorted(lines, key=lambda l: (l.begin, l.end))
    report.total_units = sum(l.total for l in report.lines)
    report.matched_units = sum(l.matched for l in report.lines)
    if log:
        log(f"      [ASR 补词] 新增 {added} 行缺失句子、{bg_added} 处行内和声")
    return total_added
