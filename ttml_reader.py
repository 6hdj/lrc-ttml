"""
TTML → ``EditDoc`` 的反向解析器，以及 ``EditDoc`` → ``AlignedLine``/TTML 的回写路径。

为什么需要它
------------
在本文件之前，全仓库只有单个时间串的 ``parse_time``，写入是**单向**的：
生成完就结束，读不回来，也就无法"打开一份已有 TTML 继续微调"。
这是编辑台一切功能的**前提**（见 `docs/竞品调研与优化方案.md` §5.1 / G1）。

设计要点
--------
* 用 ``xml.etree.ElementTree``，与 ``ttml_check.py`` 一致；
* **两种 itunes 命名空间都认**（``ttml_writer.NS_ITUNES_ALL``）：生态里既有
  ``music.apple.com/lyric-ttml-internal`` 也有 ``itunes.apple.com/lyric-ttml-extensions``；
* 时间串一律走 ``ttml_writer.parse_time``，自动兼容 ``HH:MM:SS.mmm`` / ``MM:SS.mmm`` / 裸秒；
* span 分层判定照抄 ``ttml_check._walk_spans``：``x-bg`` → 和声层，
  ``x-translation`` / ``x-roman`` → 翻译层；
* 包装层（有 role、无 begin/end、内部还有 span）**穿透**处理；
* ``ttm:agent`` 缺失时兜底 ``v1``（与 ``matcher`` 一致）；
* 回写**直接构造 ``TTMLWriter``**，不用便利函数 ``write_ttml()``（它有空参问题 G9）。
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Sequence, Tuple

from ttml_model import (LAYER_BG, LAYER_MAIN, LAYER_TRANSLATION, EditDoc,
                        EditLine, EditWord)
from ttml_writer import (META_KEYS, NS_AMLL, NS_ITUNES_ALL, NS_TTM, parse_time)

# 行级 role（Apple 内部扩展）：写在主唱 span 外层
_LINE_ROLES = ("x-duet-a", "x-duet-b", "x-narration")
# 翻译/音译层
_TRANSLATION_ROLES = ("x-translation", "x-roman")


class TTMLParseError(ValueError):
    """TTML 无法解析（结构性问题，不是时间格式问题）"""


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _ns(tag: str) -> str:
    return tag.split("}", 1)[0][1:] if "}" in tag else ""


def _get(el, name: str, default: str = "") -> str:
    """按属性名取值；``itunes:*`` 两种命名空间都试，另做 local-name 兜底"""
    for ns in NS_ITUNES_ALL:
        v = el.get("{" + ns + "}" + name)
        if v is not None:
            return v
    v = el.get("{" + NS_TTM + "}" + name)
    if v is not None:
        return v
    v = el.get(name)
    if v is not None:
        return v
    # local-name 兜底（有些文件前缀声明不规范）
    for k, val in el.attrib.items():
        if _local(k) == name:
            return val
    return default


def _t(el, name: str, default: float = 0.0) -> float:
    raw = _get(el, name)
    return parse_time(raw) if raw else default


def _role_of(el) -> str:
    return (_get(el, "role") or "").lower()


def _strip_bg_parens(words: List[EditWord]) -> None:
    """写入器给和声层整体加了半角括号 ``( ... )``，读回来要还原成原始文本"""
    if not words:
        return
    if words[0].text.startswith("("):
        words[0].text = words[0].text[1:]
    if words[-1].text.endswith(")") and words[-1].text:
        words[-1].text = words[-1].text[:-1]


# ==========================================================================
def parse_ttml(text: str) -> EditDoc:
    """把一份 TTML 文本读成 ``EditDoc``"""
    body = text.lstrip("\ufeff")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise TTMLParseError(f"XML 解析失败：{e}") from e

    doc = EditDoc()
    doc.lang = _get(root, "lang") or root.get("{http://www.w3.org/XML/1998/namespace}lang") or "zh-Hans"
    doc.timing = _get(root, "timing") or "Word"

    # ------------------------------------------------------------------ head
    head = next((c for c in root if _local(c.tag) == "head"), None)
    if head is not None:
        for meta_el in head.iter():
            tag = _local(meta_el.tag)
            if tag == "agent":
                aid = _get(meta_el, "id") or meta_el.get("{http://www.w3.org/XML/1998/namespace}id") or ""
                if not aid:
                    aid = _get(meta_el, "agent")
                name = ""
                for ch in meta_el:
                    if _local(ch.tag) == "name":
                        name = (ch.text or "").strip()
                if aid:
                    doc.agents[aid] = name
                    if aid not in doc.declared_agents:
                        doc.declared_agents.append(aid)
                    atype = (_get(meta_el, "type") or meta_el.get("type") or "").strip()
                    if atype:
                        doc.agent_types[aid] = atype
            elif tag == "meta" and _ns(meta_el.tag) == NS_AMLL:
                k, v = meta_el.get("key"), meta_el.get("value")
                if k is None:
                    continue
                if k in doc.meta:                    # artists 这类会重复出现
                    old = doc.meta[k]
                    if isinstance(old, list):
                        old.append(v)
                    else:
                        doc.meta[k] = [old, v]
                else:
                    doc.meta[k] = v

    # 头部翻译（<iTunesMetadata><translations><text for="L1">）
    head_trans: Dict[str, str] = {}
    if head is not None:
        for el in head.iter():
            if _local(el.tag) == "text" and el.get("for"):
                head_trans[el.get("for")] = el.text or ""

    # 词曲作者（<iTunesMetadata><songwriters><songwriter>…）
    # ⚠️ 以前**完全没读**这根线 —— 编辑台打开一份写了 songwriters 的文件、
    # 随便改一个字再保存，**词曲作者就永久丢了**（用户没碰过的东西被删掉，
    # 和当初 markers/role 被删是同一类 bug）。
    # 存成列表放进 meta；doc_to_ttml 再原样传回去。
    if head is not None:
        sw: List[str] = []
        for el in head.iter():
            if _local(el.tag) == "songwriter":
                s = (el.text or "").strip()
                if s and s not in sw:
                    sw.append(s)
        if sw:
            doc.meta["songwriters"] = sw

    # ------------------------------------------------------------------ body
    body_el = next((c for c in root if _local(c.tag) == "body"), None)
    if body_el is None:
        return doc
    dur = _get(body_el, "dur")
    if dur:
        try:
            doc.duration = parse_time(dur)
        except Exception:
            doc.duration = None

    auto_key = 0
    for div in [c for c in body_el if _local(c.tag) == "div"] or [body_el]:
        paragraph = len({ln.paragraph for ln in doc.lines}) if doc.lines else 0
        for p in div.iter():
            if _local(p.tag) != "p":
                continue
            auto_key += 1
            key = _get(p, "key") or p.get("{http://www.w3.org/XML/1998/namespace}id") or f"L{auto_key}"
            line = EditLine(
                key=key,
                xml_id=p.get("{http://www.w3.org/XML/1998/namespace}id") or "",
                agent=_get(p, "agent") or "v1",
                role=_role_of(p),          # 行级 ttm:role（x-duet-a/b…）；以前漏读了
                begin=_t(p, "begin"),
                end=_t(p, "end"),
                paragraph=paragraph,
                translation=head_trans.get(key, ""),
            )
            _fill_words(p, line)
            line.text = "".join(w.text for w in line.words)
            if line.words:
                if line.end <= line.begin:
                    line.end = line.begin + 0.3
                doc.lines.append(line)

    # head 里定义过但正文没用到的 agent 也保留（回写时要一致）
    for ln in doc.lines:
        doc.agents.setdefault(ln.agent, "")
    doc.write_options = _infer_write_options(root, body_el, doc, head_trans)
    return doc


def _infer_write_options(root, body_el, doc: EditDoc, head_trans: Dict[str, str]) -> Dict[str, object]:
    """推断"这份文件当初是怎么写的"，回写时照原样来。

    为什么必须做：编辑台保存只该改**用户改过的东西**。以前 `make_writer` 把
    ``mode="amll"`` / ``markers="agent"`` 写死，结果保存一份 Apple 模式的
    ``<p ttm:role="x-duet-a">`` 文件时会把角色**整个删掉**——那正是 Apple Music
    左右分栏依赖的东西，等于"修一行声部，毁掉整个分栏"。
    """
    opts: Dict[str, object] = {}

    # mode：有 <iTunesMetadata> 就是 apple 模式写的。
    # ⚠️ 这是**尽力而为**：我们自己的 apple 模式在没有翻译时并不写 <iTunesMetadata>，
    # 所以常常推成 amll。无害——`mode` 只影响"没有源段落时按间奏切 div"，
    # 而多 div 的文件段落信息本来就是有的（走段落切分），单 div 的文件当初也没切过。
    has_itunes_meta = any(_local(e.tag) == "iTunesMetadata" for e in root.iter())
    opts["mode"] = "apple" if has_itunes_meta else "amll"

    # Apple 内部最小导出：无 xml:lang / 无 itunes:timing / 单 div / p 没有 xml:id
    divs = [e for e in root.iter() if _local(e.tag) == "div"]
    ps = [e for e in root.iter() if _local(e.tag) == "p"]
    xml_lang = root.get("{http://www.w3.org/XML/1998/namespace}lang")
    timing = None
    for ns in ("http://music.apple.com/lyric-ttml-internal",
               "http://itunes.apple.com/lyric-ttml-extensions"):
        timing = timing or root.get("{" + ns + "}timing")
    if (xml_lang is None and timing is None and len(divs) <= 1
            and all(not p.get("{http://www.w3.org/XML/1998/namespace}id") for p in ps)):
        opts["mode"] = "apple-internal"

    # markers：看 <p> 上到底有没有 ttm:agent / ttm:role
    ps = [e for e in root.iter() if _local(e.tag) == "p"]
    has_agent = any(_get(p, "agent") for p in ps)
    has_role = any(_role_of(p) for p in ps)
    opts["markers"] = ("both" if (has_agent and has_role)
                       else "role" if has_role
                       else "agent" if has_agent else "agent")

    # line_id：xml:id / itunes:key 各写了没有
    xml_ids = sum(1 for p in ps if p.get("{http://www.w3.org/XML/1998/namespace}id"))
    keys = sum(1 for p in ps if _get(p, "key"))
    if xml_ids and keys:
        opts["line_id"] = "both"
    elif xml_ids:
        opts["line_id"] = "xml:id"
    else:
        opts["line_id"] = "itunes:key"

    # time_format：看所有时间串，区分 clock / mmss / seconds / auto（混合）
    all_times = []
    for e in root.iter():
        if _local(e.tag) not in ("p", "span", "div", "body"):
            continue
        for k in ("begin", "end", "dur"):
            v = e.get(k)
            if v:
                all_times.append(v.strip())
    has_clock = any(":" in t and t.count(":") == 2 for t in all_times)
    has_mmss = any(":" in t and t.count(":") == 1 for t in all_times)
    has_sec = any(":" not in t for t in all_times)
    if has_clock:
        opts["time_format"] = "clock"
    elif has_mmss and has_sec:
        opts["time_format"] = "auto"
    elif has_mmss:
        opts["time_format"] = "mmss"
    elif has_sec:
        opts["time_format"] = "seconds"
    else:
        opts["time_format"] = "clock"

    # layered：真正的分层特征 = 同 agent 的主唱层重叠，或 x-bg 超出 p。
    # 不同 agent 的重叠只说明对唱/多声部，不能据此把文件当 layered
    # （否则我们自己的 duet 产物往返会被改成另一种写法）。
    layered = False
    for i, line in enumerate(doc.lines):
        for w in line.words:
            if w.is_bg and (w.begin < line.begin - 1e-6 or w.end > line.end + 1e-6):
                layered = True
                break
        if layered:
            break
        if i + 1 < len(doc.lines):
            nxt = doc.lines[i + 1]
            if line.agent == nxt.agent:
                main_end = max((w.end for w in line.words if not w.is_bg), default=None)
                next_main = min((w.begin for w in nxt.words if not w.is_bg), default=None)
                if (main_end is not None and next_main is not None
                        and main_end > next_main + 1e-6):
                    layered = True
                    break
    opts["layered"] = bool(layered)

    # 翻译：head 里有没有；行内有没有 x-translation
    inline_tr = any("x-translation" in _role_of(e)
                    for e in root.iter() if _local(e.tag) == "span")
    if head_trans and inline_tr:
        opts["translation_style"] = "both"
    elif inline_tr:
        opts["translation_style"] = "inline"
    else:
        opts["translation_style"] = "head"

    # 段落名：源文件原本写了 itunes:songPart 吗？
    parts = {_get(d, "songPart") for d in ([c for c in body_el if _local(c.tag) == "div"]
                                           or [body_el])}
    parts = {p for p in parts if p}
    if len(parts) == 1:
        opts["song_part"] = parts.pop()
        opts["label_sections"] = True
    else:
        # 没写（或每段名字不同）→ 回写时不要自己猜 Verse/Chorus 加上去
        opts["label_sections"] = False
    return opts


def _fill_words(p, line: EditLine) -> None:
    """把一个 ``<p>`` 的 span 拆成各层的 ``EditWord``"""
    direct_text = (p.text or "").strip()

    def add(el, layer: str, is_bg: bool) -> None:
        begin, end = _t(el, "begin"), _t(el, "end")
        txt = "".join(el.itertext())
        if not txt:
            return
        line.words.append(EditWord(
            text=txt, begin=begin, end=end, is_bg=is_bg, layer=layer,
            matched=True, index=len(line.words),
        ))

    def walk(el, layer: str, is_bg: bool) -> None:
        for child in el:
            if _local(child.tag) != "span":
                walk(child, layer, is_bg)
                continue
            role = _role_of(child)
            has_children = any(_local(c.tag) == "span" for c in child)
            begins = bool(_get(child, "begin") or _get(child, "end"))

            if role and "x-bg" in role:
                _walk_collect(child, line, LAYER_BG, True)
                continue
            if role and any(r in role for r in _TRANSLATION_ROLES):
                if has_children:
                    _walk_collect(child, line, LAYER_TRANSLATION, False)
                else:
                    txt = "".join(child.itertext()).strip()
                    if txt and not line.translation:
                        line.translation = txt
                continue
            if role and any(r in role for r in _LINE_ROLES):
                # 行级 role 包装层：内容仍是主唱
                if not line.role:
                    line.role = role
                walk(child, LAYER_MAIN, False)
                continue
            if not begins and has_children:
                walk(child, layer, is_bg)          # 无时间的包装层，穿透
                continue
            add(child, layer, is_bg)

    walk(p, LAYER_MAIN, False)

    if direct_text and not line.words:
        # 逐行 TTML（只有 <p> 有时间，没有 span）
        line.words.append(EditWord(text=direct_text, begin=line.begin, end=line.end,
                                   index=0, matched=True))


def _walk_collect(el, line: EditLine, layer: str, is_bg: bool) -> None:
    """收某个 role 包装层内部的 span（可能是它自己也带时间的一层）"""
    got = False
    for child in el:
        if _local(child.tag) != "span":
            continue
        txt = "".join(child.itertext())
        if not txt:
            continue
        line.words.append(EditWord(
            text=txt, begin=_t(child, "begin"), end=_t(child, "end"),
            is_bg=is_bg, layer=layer, matched=True, index=len(line.words),
        ))
        got = True
    if not got:
        txt = "".join(el.itertext())
        if txt:
            line.words.append(EditWord(
                text=txt, begin=_t(el, "begin"), end=_t(el, "end"),
                is_bg=is_bg, layer=layer, matched=True, index=len(line.words),
            ))
    if layer == LAYER_BG:
        _strip_bg_parens([w for w in line.words if w.layer == LAYER_BG])


def parse_ttml_file(path: str) -> EditDoc:
    with open(path, "r", encoding="utf-8-sig") as f:
        return parse_ttml(f.read())


# ==========================================================================
# 回写路径：EditDoc → AlignedLine → TTMLWriter
# ==========================================================================
def doc_to_aligned(doc: EditDoc):
    """``EditDoc`` → ``List[AlignedLine]``（回写 TTML 用）"""
    from matcher import AlignedLine, Unit

    out: List[AlignedLine] = []
    for line in doc.lines:
        units: List[Unit] = []
        for i, w in enumerate(line.words):
            if w.layer == LAYER_TRANSLATION:
                continue          # 翻译层不写成逐字 span（由 translation 字段承载）
            units.append(Unit(
                core=w.text, line=0, index=i,
                is_bg=(w.layer == LAYER_BG or w.is_bg),
                start=w.begin, end=w.end,
                prob=w.prob, matched=w.matched, muted=w.muted,
            ))
        out.append(AlignedLine(
            text=line.text, agent=line.agent or "v1", units=units,
            begin=line.begin, end=line.end,
            matched=sum(1 for u in units if u.matched), total=len(units),
            anchored=True, role=line.role, translation=line.translation,
            paragraph=line.paragraph,
        ))
    return out


def make_writer(doc: EditDoc, **overrides):
    """按 ``doc`` + 显式覆盖项构造 ``TTMLWriter``（不用 write_ttml，规避 G9）

    基准取 ``doc.write_options``（parse_ttml 推断出的"源文件当初怎么写的"），
    ``overrides`` 优先级最高。这样编辑台保存不会把用户没动过的写法改掉。
    """
    from ttml_writer import TTMLWriter

    kwargs = dict(
        mode="amll",
        markers="agent",
        line_id="both",
        translation_style="head",
        time_format="clock",
        song_part="",
        inline_translation_lang=doc.lang,
        label_sections=True,
        layered=False,
    )
    kwargs.update(getattr(doc, "write_options", {}) or {})
    kwargs.update(overrides)
    # agent 类型也要带回（真实 AMLL DB 文件里 v2 常是 type="other"，
    # 不传就会被默认成 person，回写时又变成另一种写法）
    kwargs.setdefault("agent_types", dict(doc.agent_types or {}))
    return TTMLWriter(**kwargs)


def doc_to_ttml(doc: EditDoc, **overrides) -> str:
    """``EditDoc`` → TTML 文本"""
    writer = make_writer(doc, **overrides)
    meta = doc.meta
    artists = meta.get("artists") or []
    if isinstance(artists, str):
        artists = [artists]
    platform_ids = {k: meta[k] for k in META_KEYS if k in meta}
    special = set(META_KEYS) | {"musicName", "artists", "album",
                                "songwriters",
                                "ttmlAuthorGithub", "ttmlAuthorGithubLogin"}
    extra = {k: v for k, v in meta.items() if k not in special}
    return writer.write(
        doc_to_aligned(doc),
        title=str(meta.get("musicName", "")),
        artists=list(artists),
        album=str(meta.get("album", "")),
        language=doc.lang,
        platform_ids={k: str(v) for k, v in platform_ids.items()},
        author=str(meta.get("ttmlAuthorGithubLogin", "")),
        author_id=str(meta.get("ttmlAuthorGithub", "")),
        duration=doc.duration,
        translation_lang=doc.lang,
        songwriters=list(meta.get("songwriters") or []),
        extra_meta={k: str(v) for k, v in extra.items()},
    )
