"""
TTML 结果自检（确定性、无网络、无大模型）。

负责回答两个问题：
1. 这份 TTML 是不是**格式合法**、能被 AMLL Player / Apple 解析？
   按 W3C TTML1 校验：``<tt>`` 上的 ``xml:lang``、``<head>/<body>/<div>/<p>`` 结构、
   §7.1 内容模型、§10.3.1 时间表达式（``hh:mm:ss[.fff]`` / 带单位的 offset-time）、
   §10.4 时间区间（``begin < end``，且 ``span ⊆ p ⊆ div``）。
2. 时间轴有没有明显**不合理**的地方（重叠、越界、时长异常）。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ttml_writer import (NS_AMLL, NS_ITUNES_ALL, NS_ITUNES_META,
                         NS_TTML, NS_TTM, NS_TTS, NS_XML,
                         is_amll_relaxed_time, is_valid_time_expression, parse_time)

KEY_RE = re.compile(r"^L(\d+)$")


@dataclass
class CheckIssue:
    severity: str      # error / warning
    where: str
    issue: str


@dataclass
class CheckResult:
    ok: bool = True
    errors: List[CheckIssue] = field(default_factory=list)
    warnings: List[CheckIssue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "ok": self.ok,
            "errors": [i.__dict__ for i in self.errors],
            "warnings": [i.__dict__ for i in self.warnings],
            "stats": self.stats,
        }

    def summary(self) -> str:
        s = f"格式{'通过' if self.ok else '未通过'}：{len(self.errors)} 个错误 / {len(self.warnings)} 个警告"
        if self.stats:
            s += (f"；共 {self.stats.get('lines', 0)} 行、{self.stats.get('words', 0)} 个字，"
                  f"平均每字 {self.stats.get('avg_word_duration', 0):.2f}s")
        return s


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _walk_spans(el, layer: str = "main"):
    """递归遍历 span，标出它属于哪一层：main / bg / translation"""
    for child in el:
        if _local(child.tag) != "span":
            yield from _walk_spans(child, layer)
            continue
        role = (child.get("{" + NS_TTM + "}role") or "").lower()
        child_layer = layer
        if "x-bg" in role:
            child_layer = "bg"
        elif "x-translation" in role:
            child_layer = "translation"
        yield child, child_layer
        yield from _walk_spans(child, child_layer)


def _match_allowed_overlap(allowed, line_no: int, agent: str,
                           prev_end: float, next_begin: float, tol: float = 1e-3) -> bool:
    """该"同演唱者行重叠"是否有**生成侧证据**允许（只认几何+agent 完全对得上的边）。

    失败关闭：任何字段缺失/不匹配/解析失败都返回 False（仍报 error）。
    """
    for a in allowed or ():
        if not isinstance(a, dict):
            continue
        try:
            if int(a.get("next_line")) != int(line_no):
                continue
            if (a.get("agent") or "") != (agent or ""):
                continue
            if abs(float(a.get("prev_end")) - float(prev_end)) > tol:
                continue
            if abs(float(a.get("next_begin")) - float(next_begin)) > tol:
                continue
            return True
        except (TypeError, ValueError):
            continue
    return False


def check_ttml(text: str, strict: bool = True, *, allowed_overlaps=None,
               layered: bool = False) -> CheckResult:
    """校验一份 TTML。

    ``allowed_overlaps``（可选，默认 ``None`` —— 行为与以前**完全一致**）：
    生成侧「拖音证据」允许的同歌手跨行重叠边列表，元素形如
    ``{"next_line": 2, "agent": "v1", "prev_end": 4.61, "next_begin": 3.23}``。
    只有 ``next_line`` / ``agent`` / ``prev_end`` / ``next_begin`` **全部**对得上时，
    该条"同一演唱者行时间重叠"才从 error 降为 warning（**仍可见、可审计**）；
    其它任何错误一律不受影响（不支持"整体放宽"）。
    """
    res = CheckResult()

    if text.startswith("\ufeff"):
        res.errors.append(CheckIssue("error", "文件", "包含 BOM，Apple/AMLL 不接受"))

    body = text.lstrip("\ufeff")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        res.ok = False
        res.errors.append(CheckIssue("error", "XML", f"解析失败: {e}"))
        return res

    if _local(root.tag) != "tt" or not root.tag.startswith("{" + NS_TTML + "}"):
        res.errors.append(CheckIssue("error", "根元素", f"根元素必须是 {NS_TTML} 命名空间下的 <tt>，实际 {root.tag}"))

    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang")
    if lang is None:
        res.errors.append(CheckIssue("error", "根元素", "缺少 xml:lang（BCP-47，如 zh-Hans / en / ja）"))
    elif not re.match(r"^[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*$", lang):
        res.errors.append(CheckIssue("error", "根元素", f"xml:lang={lang!r} 不是合法的 BCP-47 语言标签"))

    timing = None
    for ns in NS_ITUNES_ALL:
        timing = root.get("{" + ns + "}timing")
        if timing:
            break
    if timing not in (None, "", "Word", "Line"):
        res.errors.append(CheckIssue("error", "根元素", f"itunes:timing 值非法: {timing}"))

    # 文本节点里不该出现的空白（span 之间的换行会被解析器丢弃，是允许的）
    if root.find("{http://www.w3.org/ns/ttml}head") is None:
        res.errors.append(CheckIssue("error", "结构", "缺少 <head>"))

    # 演唱者定义
    agents = set()
    agent_types = {}
    for el in root.iter():
        if _local(el.tag) == "agent":
            aid = el.get("{http://www.w3.org/XML/1998/namespace}id")
            if aid:
                agents.add(aid)
                agent_types[aid] = (el.get("type") or "").strip()
            else:
                res.errors.append(CheckIssue("error", "metadata", "ttm:agent 缺少 xml:id"))

    # 规范 §4.1 的 id 分配约定：group（合唱）用 v1000，other 用 v2000
    for aid, atype in agent_types.items():
        if atype == "group" and aid != "v1000":
            res.warnings.append(CheckIssue("warning", "metadata",
                                           f'ttm:agent type="group" 的 id 建议用 v1000（现在是 {aid}）'))
        if aid == "v1000" and atype and atype != "group":
            res.warnings.append(CheckIssue("warning", "metadata",
                                           f'v1000 按规范应是 type="group"（现在是 {atype}）'))
        if atype == "other" and aid != "v2000":
            res.warnings.append(CheckIssue("warning", "metadata",
                                           f'ttm:agent type="other" 的 id 建议用 v2000（现在是 {aid}）'))

    # 规范 §4.1：type 只能是这五个值
    for aid, atype in agent_types.items():
        if atype and atype not in ("person", "character", "organization", "group", "other"):
            res.warnings.append(CheckIssue("warning", "metadata",
                                           f"ttm:agent type 取值不认识：{atype!r}"))

    # amll:meta / head 翻译
    meta_keys = {}
    for el in root.iter("{" + NS_AMLL + "}meta"):
        k = el.get("key")
        if k:
            meta_keys.setdefault(k, []).append(el.get("value", ""))
    for required in ("musicName", "artists", "album"):
        if required not in meta_keys:
            res.warnings.append(CheckIssue("warning", "metadata", f"缺少 amll:meta key=\"{required}\"（AMLL DB 要求必填）"))
    if not any(k in meta_keys for k in ("ncmMusicId", "qqMusicId", "appleMusicId", "spotifyId")):
        res.warnings.append(CheckIssue("warning", "metadata", "没有提供任何平台 ID（AMLL DB 要求至少一个）"))

    head_keys = set()
    for el in root.iter("{" + NS_ITUNES_META + "}text"):
        for_attr = el.get("for")
        if for_attr:
            head_keys.add(for_attr)
        else:
            res.errors.append(CheckIssue("error", "metadata", "翻译 <text> 缺少 for 属性"))

    # 规范 §5 Caution：head 里的翻译/音译与行内 x-translation/x-roman 不能同语言重复，
    # 否则解析器会把 head 的追加到行内列表 → 同一行出现两遍翻译。
    head_trans_langs = set()
    for el in root.iter():
        if _local(el.tag) in ("translation", "transliteration"):
            lg = el.get("{http://www.w3.org/XML/1998/namespace}lang")
            if lg:
                head_trans_langs.add(lg)
    inline_trans_langs = set()
    for el in root.iter():
        if _local(el.tag) != "span":      # <span> 在 TTML 默认命名空间，不是 ttm
            continue
        role = (el.get("{" + NS_TTM + "}role") or "").lower()
        if "x-translation" in role or "x-roman" in role:
            lg = el.get("{http://www.w3.org/XML/1998/namespace}lang")
            if lg:
                inline_trans_langs.add(lg)
    for lg in sorted(head_trans_langs & inline_trans_langs):
        res.warnings.append(CheckIssue(
            "warning", "metadata",
            f"语言 {lg} 的翻译在 head 和行内各写了一份（规范 §5 警告会双重翻译）"))
    if len(head_trans_langs) > 1:
        res.warnings.append(CheckIssue(
            "warning", "metadata",
            f"head 里有多个语言的翻译轨道 {sorted(head_trans_langs)}，"
            f"规范建议同一语言只出现一次"))

    # 时间码格式 + 行检查
    lines = 0
    words = 0
    durations: List[float] = []
    _end_times: List[float] = []
    keys: List[int] = []
    last_end_by_agent = {}

    body_el = None
    for el in root:
        if _local(el.tag) == "body":
            body_el = el
    if body_el is None:
        res.errors.append(CheckIssue("error", "结构", "缺少 <body>"))
        res.ok = not res.errors
        return res

    divs = [el for el in body_el if _local(el.tag) == "div"]
    if not divs:
        res.errors.append(CheckIssue("error", "结构", "<body> 下没有 <div>"))

    for div in divs:
        div_begin = div_end = None
        if div.get("begin") is not None or div.get("end") is not None:
            _valid_time_attr(div, "begin", res, "div", strict)
            _valid_time_attr(div, "end", res, "div", strict)
            div_begin = parse_time(div.get("begin", "0"))
            div_end = parse_time(div.get("end", "0"))
            if div_end <= div_begin:
                res.errors.append(CheckIssue("error", "div",
                                             f"div 时间非法 begin={div.get('begin')} end={div.get('end')}"))
        for p in div:
            if _local(p.tag) != "p":
                continue
            lines += 1
            key = next((p.get("{" + ns + "}key") for ns in NS_ITUNES_ALL
                        if p.get("{" + ns + "}key")), None) or \
                p.get("{" + NS_XML + "}id")
            if not key:
                res.errors.append(CheckIssue("error", f"第{lines}行", "缺少 itunes:key / xml:id"))
            else:
                m = KEY_RE.match(key)
                if not m:
                    res.errors.append(CheckIssue("error", f"第{lines}行", f"行标识格式错误: {key}"))
                else:
                    keys.append(int(m.group(1)))

            if not _valid_time_attr(p, "begin", res, f"第{lines}行", strict) or \
               not _valid_time_attr(p, "end", res, f"第{lines}行", strict):
                pass
            p_begin = parse_time(p.get("begin", "0"))
            p_end = parse_time(p.get("end", "0"))
            if p_end <= p_begin:
                res.errors.append(CheckIssue("error", f"第{lines}行", f"行时间非法 begin={p.get('begin')} end={p.get('end')}"))
            elif div_begin is not None and (p_begin < div_begin - 0.001 or p_end > div_end + 0.001):
                res.errors.append(CheckIssue("error", f"第{lines}行",
                                             f"行区间超出所在 <div>：{p.get('begin')}~{p.get('end')}"
                                             f" / {div.get('begin')}~{div.get('end')}"))

            agent = p.get("{" + NS_TTM + "}agent")
            if agent and agents and agent not in agents:
                res.errors.append(CheckIssue("error", f"第{lines}行", f"引用了未定义的演唱者 {agent}"))

            # 收集 span。分层处理：
            #   main        主歌词（检查顺序 / 重叠 / 时长）
            #   bg          背景人声（可与主歌词时间重叠）
            #   translation 翻译层（整行时间，不与主歌词比顺序）
            #
            # 「带子 span 的 span」= 角色包装层（x-bg / x-duet-a / x-translation …），
            # **本身不算一个字**，即使它带了 begin/end —— AMLL 参考实现的 x-bg
            # 包装层就是有 begin/end 的（generator.test.ts.snap）。真正的字是它的子 span。
            layers = {"main": [], "bg": [], "translation": []}
            # 角色也可能直接挂在行上（`<p ttm:role="x-duet-a">`），这也算"有 role 层"
            has_role_layer = bool((p.get("{" + NS_TTM + "}role") or "").strip())
            for span, layer in _walk_spans(p):
                role = span.get("{" + NS_TTM + "}role") or ""
                if role:
                    has_role_layer = True
                child_spans = list(span)
                if child_spans:
                    # ⚠️ 致命写法：用一个**未知 role** 的 span 包住主唱 span，且自己不写
                    # begin/end。AMLL 解析器对未知 role 走 processWordElement，而它
                    # **不递归子节点**、只在包装层带 begin/end 时才产出词 —— 整行会被
                    # 当成一个词，逐字时间轴全丢，播放器里歌词一片空白。
                    # 实测：32 行 266 字 → 解析出 "32 个字"。（x-bg / x-translation /
                    # x-roman 有专门分支，会正确递归，所以排除它们。）
                    _r = role.lower()
                    _known = any(k in _r for k in ("x-bg", "x-translation", "x-roman"))
                    if _r and not _known and not (span.get("begin") and span.get("end")):
                        res.errors.append(CheckIssue(
                            "error", f"第{lines}行",
                            f'未知 role 的包装 span 会让 AMLL 把整行当成一个词'
                            f'（逐字时间轴全丢、播放器空白）：ttm:role="{role}"。'
                            f'把对唱角色挂到 <p> 上，或干脆不写这个角色'))
                    w0, w1 = span.get("begin"), span.get("end")
                    if w0 and w1:      # 包装层带时间：必须合法且包住子 span
                        for attr in ("begin", "end"):
                            _valid_time_attr(span, attr, res, f"第{lines}行", strict)
                        c0, c1 = parse_time(w0), parse_time(w1)
                        if c1 <= c0:
                            res.errors.append(CheckIssue("error", f"第{lines}行",
                                                         f"包装层时间非法 {w0}~{w1}"))
                        for ch in child_spans:
                            if not ch.get("begin") or not ch.get("end"):
                                continue
                            s0, s1 = parse_time(ch.get("begin")), parse_time(ch.get("end"))
                            if s0 < c0 - 0.001 or s1 > c1 + 0.001:
                                res.errors.append(CheckIssue(
                                    "error", f"第{lines}行",
                                    f"子 span 超出包装层区间: {_text(ch)[:8]} "
                                    f"{ch.get('begin')}~{ch.get('end')} / {w0}~{w1}"))
                    if layer == "translation":
                        layers["translation"].append((p_begin, p_end, _text(span)))
                    continue
                if not span.get("begin"):
                    known_role = any(r in role.lower() for r in
                                     ("x-bg", "x-translation", "x-roman", "x-duet", "x-narration"))
                    if not known_role:
                        res.errors.append(CheckIssue("error", f"第{lines}行",
                                                      f"span 缺少 begin: {_text(span)[:12]}"))
                    if layer == "translation":
                        layers["translation"].append((p_begin, p_end, _text(span)))
                    continue
                for attr in ("begin", "end"):
                    _valid_time_attr(span, attr, res, f"第{lines}行", strict)
                s0, s1 = parse_time(span.get("begin")), parse_time(span.get("end"))
                if s1 <= s0:
                    res.errors.append(CheckIssue("error", f"第{lines}行", f"span 时间非法 {span.get('begin')}~{span.get('end')} ({_text(span)[:8]})"))
                if (not (layered and layer == "bg")
                        and (s0 < p_begin - 0.001 or s1 > p_end + 0.001)):
                    res.errors.append(CheckIssue("error", f"第{lines}行",
                                                 f"span 超出行的 begin/end: {_text(span)[:8]} {span.get('begin')}~{span.get('end')} / {p.get('begin')}~{p.get('end')}"))
                if layer in ("main", "bg"):
                    durations.append(s1 - s0)
                    words += 1
                _end_times.append(s1)
                layers[layer].append((s0, s1, _text(span)))

            spans = layers["main"]
            spans.sort()
            for (a0, a1, at), (b0, b1, bt) in zip(spans, spans[1:]):
                if b0 < a1 - 0.001:
                    res.errors.append(CheckIssue("error", f"第{lines}行", f"字时间重叠: {at[:6]} 与 {bt[:6]}"))
            for layer_name in ("bg", "translation"):
                group = sorted(layers[layer_name])
                for (a0, a1, at), (b0, b1, bt) in zip(group, group[1:]):
                    if b0 < a1 - 0.001:
                        res.errors.append(CheckIssue("error", f"第{lines}行",
                                                     f"{layer_name} 层字时间重叠: {at[:6]} 与 {bt[:6]}"))

            if agent:
                prev = last_end_by_agent.get(agent)
                # AMLL 规范：「因混音而使时间重叠的情况除外」。
                # 这里**只比主唱层的结束时间**：背景和声（x-bg）常常一直唱到
                # 下一句开始之后，那是真实的混音重叠，不该判成"同行重叠"。
                # 主唱层之间仍然必须严格不重叠。
                main_end = max((s1 for _, s1, _ in layers["main"]), default=None)
                if (not layered) and prev is not None and p_begin < prev - 0.001 and not has_role_layer:
                    if _match_allowed_overlap(allowed_overlaps, lines, agent, prev, p_begin):
                        res.warnings.append(CheckIssue(
                            "warning", f"第{lines}行",
                            f"同一演唱者 {agent} 行时间重叠，但该边有生成侧拖音证据允许"
                            f"（上一行结束 {prev:.3f}，本行开始 {p_begin:.3f}）"))
                    else:
                        res.errors.append(CheckIssue("error", f"第{lines}行", f"同一演唱者 {agent} 行时间重叠（上一行结束 {prev:.3f}）"))
                last_end_by_agent[agent] = main_end if main_end is not None else p_end

            # 规范 §7.3：背景人声用**半角括号**包裹，且「不要添加两个或更多括号、
            # 也不要只添加半边括号」。
            if layers["bg"]:
                bg_txt = "".join(t for _, _, t in sorted(layers["bg"]))
                opens = len(bg_txt) - len(bg_txt.lstrip("(（"))
                closes = len(bg_txt) - len(bg_txt.rstrip(")）"))
                if opens > 1 or closes > 1:
                    res.warnings.append(CheckIssue(
                        "warning", f"第{lines}行",
                        f"背景人声套了不止一层括号（规范 §7.3 只允许一层半角）：{bg_txt[:20]}"))
                elif opens == 0 or closes == 0:
                    res.warnings.append(CheckIssue(
                        "warning", f"第{lines}行",
                        f"背景人声缺少成对的括号（只写了半边）：{bg_txt[:20]}"))
                else:
                    inner = bg_txt[opens:len(bg_txt) - closes]
                    if any(c in inner for c in "()（）"):
                        res.warnings.append(CheckIssue(
                            "warning", f"第{lines}行",
                            f"背景人声里还有多余的括号：{bg_txt[:20]}"))
                if bg_txt.startswith("（") or bg_txt.endswith("）"):
                    res.warnings.append(CheckIssue(
                        "warning", f"第{lines}行",
                        "背景人声建议用半角括号 ( )，规范示例都是半角"))

            # AMLL 语义：背景人声必须是某一行的子内容。整行只有 x-bg 时，
            # AMLL 解析器不会把背景人声计入本行文本（parser.ts 的 case RoleBg），
            # 该行主歌词为空 → 播放器里看不到内容。
            if layers["bg"] and not layers["main"]:
                res.warnings.append(CheckIssue("warning", f"第{lines}行",
                                               "整行只有背景人声 x-bg（AMLL 解析后主歌词为空、播放器看不见）；"
                                               "应并进相邻主唱行的 <p>（见 preprocess.merge_background_lines）"))

            # Apple 歌词风格准则（只警告，不阻断）
            text_main = "".join(t for _, _, t in layers["main"])
            if text_main.strip():
                try:
                    from lyric_style import lint_text
                    for issue in lint_text(text_main):
                        res.warnings.append(CheckIssue("warning", f"第{lines}行", issue))
                except Exception:
                    pass
            if not text_main.strip() and not layers["bg"] and not layers["translation"]:
                res.warnings.append(CheckIssue("warning", f"第{lines}行", "这一行没有任何文字内容"))

            # 合理性警告
            for s0, s1, t in spans:
                d = s1 - s0
                if d < 0.08:
                    res.warnings.append(CheckIssue("warning", f"第{lines}行", f"字「{t[:4]}」时长仅 {d:.3f}s，可能过短"))
                elif d > 2.5:
                    res.warnings.append(CheckIssue("warning", f"第{lines}行", f"字「{t[:4]}」时长 {d:.2f}s，可能过长"))
            for (a0, a1, at), (b0, _, bt) in zip(spans, spans[1:]):
                if b0 - a1 > 1.5:
                    res.warnings.append(CheckIssue("warning", f"第{lines}行", f"「{at[:4]}」与「{bt[:4]}」之间停顿 {b0 - a1:.2f}s"))

    if keys and keys != list(range(1, len(keys) + 1)):
        res.errors.append(CheckIssue("error", "行标识", f"itunes:key 不连续: {keys[:8]}…"))

    # head 里的翻译必须指向存在的行
    for k in sorted(head_keys, key=lambda s: (len(s), s)):
        m = KEY_RE.match(k)
        if not m or int(m.group(1)) not in keys:
            res.errors.append(CheckIssue("error", "metadata", f"翻译 <text for=\"{k}\"> 没有对应的歌词行"))

    if body_el.get("dur"):
        dur = parse_time(body_el.get("dur"))
        if words:
            last = max(_end_times) if _end_times else 0.0
            if dur + 0.5 < last:
                res.errors.append(CheckIssue("error", "body", f"dur={body_el.get('dur')} 小于最后一字结束时间 {last:.3f}"))

    res.stats = {
        "lines": lines,
        "words": words,
        "avg_word_duration": (sum(durations) / len(durations)) if durations else 0.0,
        "shortest": min(durations) if durations else 0.0,
        "longest": max(durations) if durations else 0.0,
    }
    res.ok = not res.errors
    return res


def _valid_time_attr(el, attr: str, res: CheckResult, where: str, strict: bool = True) -> bool:
    """按 W3C TTML1 §10.3.1 校验时间表达式。

    ``strict=True``（默认）只接受合法 TTML1 写法：``hh:mm:ss[.fff]``、
    ``hh:mm:ss:ff[.s]``，或带单位的 offset-time（``12.5s`` / ``250ms``）。
    ``strict=False`` 时把 AMLL 宽松写法（``MM:SS.mmm`` / 裸秒）降级为警告。
    """
    v = el.get(attr)
    if v is None:
        res.errors.append(CheckIssue("error", where, f"缺少 {attr} 属性"))
        return False
    if is_valid_time_expression(v):
        return True
    if not strict and is_amll_relaxed_time(v):
        res.warnings.append(CheckIssue("warning", where,
                                       f"{attr}={v!r} 是 AMLL 宽松写法，W3C TTML1 §10.3.1 要求 "
                                       f"hh:mm:ss[.fff] 或带单位的 offset-time"))
        return True
    res.errors.append(CheckIssue("error", where,
                                 f"{attr} 不是合法的 TTML1 时间表达式: {v!r}"
                                 f"（应为 HH:MM:SS.mmm，如 00:01:05.432）"))
    return False


def _text(el) -> str:
    return "".join(el.itertext())
