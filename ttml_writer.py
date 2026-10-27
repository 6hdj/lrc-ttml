"""
TTML 生成器（W3C TTML1 + AMLL TTML Database 规范 / Apple Music 逐字歌词）。

规范依据
--------
W3C **TTML1 (Third Edition)** https://www.w3.org/TR/ttml1/

* §5.1 命名空间：``ttml`` / ``ttml#metadata`` / ``ttml#styling`` / ``ttml#parameter``；
  非 TT 命名空间的扩展属性（``itunes:*``）在合法性判定中会被剪除，因此可以安全携带。
* §7.1 结构：``tt`` → ``head?`` ``body?``；``body``/``div`` → ``div``/``p``；``p``/``span`` → 文本 + ``br``/``span``。
* §7.2.2 ``xml:lang`` 是 ``<tt>`` 上的**必需**属性，取值必须是 BCP-47 语言标签。
* §10.3.1 ``<timeExpression>``::

      clock-time   : hours ":" minutes ":" seconds ( fraction | ":" frames ( "." sub-frames )? )?
      offset-time  : time-count fraction? metric          # metric ∈ h | m | s | ms | f | t

  即 **小时段不可省略**，且 offset-time **必须带单位**。
  ⇒ ``MM:SS.mmm``、裸 ``SS.mmm`` 都不是合法的 TTML1 时间表达式。
* §10.4 时间区间：``begin < end``，且子元素区间必须落在父元素区间内。

AMLL TTML DB 规范 https://github.com/amll-dev/amll-ttml-db/wiki/格式规范

* 时间戳接受 ``ss`` / ``mm:ss`` / ``hh:mm:ss``（可带 ``.xxx``），串里**禁止出现字母**
  （所以 ``10.0s`` 反而不被 AMLL 接受）。
* 两者取交集 ⇒ 本实现默认输出 ``HH:MM:SS.mmm``：既严格合法，又 AMLL / Apple 都能解析。

其它约定：命名空间 ``itunes``（``http://music.apple.com/lyric-ttml-internal``，与 Apple Music
和 AMLL 参考实现一致）+ ``amll``；``<head><metadata>`` 里放 ``ttm:agent`` 与
``amll:meta``（musicName / artists / album 必填）；``<body dur>`` → ``<div begin end>`` →
``<p begin end ttm:agent itunes:key>`` → 每个字一个 ``<span>``；背景人声
``<span ttm:role="x-bg">``；翻译优先 Apple 样式 ``<iTunesMetadata><translations><text for="L1">``，
也支持行内 ``<span ttm:role="x-translation">``；UTF-8 无 BOM；``<p>`` 内部不换行
（span 之间的空白会被解析器丢弃）。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape, quoteattr

from matcher import AlignedLine, Unit
from lyric_style import MASK

NS_TTML = "http://www.w3.org/ns/ttml"
NS_TTM = "http://www.w3.org/ns/ttml#metadata"
NS_TTS = "http://www.w3.org/ns/ttml#styling"
# Apple / AMLL 参考实现（packages/ttml）与线上歌词库里的 itunes:* 都在这个命名空间：
NS_ITUNES = "http://music.apple.com/lyric-ttml-internal"
# AMLL 规范文档里出现的另一个写法，解析器两种都认（按 local-name 兜底）
NS_ITUNES_EXT = "http://itunes.apple.com/lyric-ttml-extensions"
NS_ITUNES_META = NS_ITUNES                                     # <iTunesMetadata> 的默认命名空间
NS_AMLL = "http://www.example.com/ns/amll"
NS_XML = "http://www.w3.org/XML/1998/namespace"

# 兼容旧名字 / 读取旧文件时两种 itunes 命名空间都认
NS_ITUNES_AMLL = NS_ITUNES
NS_ITUNES_APPLE = NS_ITUNES
NS_ITUNES_ALL = (NS_ITUNES, NS_ITUNES_EXT)

# amll:meta 输出顺序。
# ⚠️ 前 5 个是 **AMLL 官方定义**的键（isrc 是 ISRC 码，后 4 个是平台歌曲 ID）。
# kuwoMusicId / kugouMusicId 是**本项目的扩展**：AMLL 规范里没有这两个键，
# 所以别的软件会忽略它们。放进这个列表是为了：① 输出顺序稳定；
# ② `ttml_reader` 往返时认得它们（不在列表里的键读回来就丢了）。
META_KEYS = ["isrc", "ncmMusicId", "qqMusicId", "appleMusicId", "spotifyId",
             "kuwoMusicId", "kugouMusicId"]
AUTHOR_KEYS = ["ttmlAuthorGithub", "ttmlAuthorGithubLogin"]

# 段落名建议值
SONG_PARTS = ("Intro", "Verse", "PreChorus", "Chorus", "Refrain", "Bridge", "Instrumental", "Outro")

# ---------------------------------------------------------------- 时间表达式
# W3C TTML1 §10.3.1：clock-time = hours ":" minutes ":" seconds ( fraction | ":" frames ( "." sub-frames )? )?
_TTML_CLOCK_RE = re.compile(
    r"^(?P<hours>\d{2,}):(?P<minutes>\d{2}):(?P<seconds>\d{2})"
    r"(?:\.(?P<fraction>\d+))?(?::(?P<frames>\d{2,})(?:\.(?P<subframes>\d+))?)?$"
)
# W3C TTML1 §10.3.1：offset-time = time-count fraction? metric
_TTML_OFFSET_RE = re.compile(r"^(?P<count>\d+(?:\.\d+)?)(?P<metric>ms|h|m|s|f|t)$")
# AMLL 规范额外容忍、但 W3C TTML1 不合法的宽松写法：裸秒 / 分:秒
_AMLL_RELAXED_RE = re.compile(r"^(?:\d+|\d{1,2}:\d{2})(?:\.\d+)?$")

DEFAULT_FRAME_RATE = 30.0        # 仅用于解析 ":frames" / "f"（TTML1 默认 ttp:frameRate=30）
MIN_INTERVAL = 0.001             # 1ms：保证 begin < end（TTML1 §10.4）

# XML 1.0 不允许的字符（混进去会让文件直接不是 well-formed XML）
_ILLEGAL_XML_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_safe(value) -> str:
    """清掉 XML 1.0 非法字符（ASR 结果里偶尔混进控制字符）。"""
    if value is None:
        return ""
    return _ILLEGAL_XML_RE.sub("", str(value))


def _esc(value) -> str:
    """文本节点转义。"""
    return escape(xml_safe(value))


def _attr(value) -> str:
    """属性值转义（含引号处理）。"""
    return quoteattr(xml_safe(value))


def format_time(seconds: float, style: str = "clock") -> str:
    """秒 → TTML 时间表达式。

    ``clock``   → ``HH:MM:SS.mmm``（W3C TTML1 §10.3.1 合法，AMLL / Apple 也接受；默认）
    ``mmss``    → ``MM:SS.mmm``（AMLL 宽松写法，**不是**合法 TTML1）
    ``seconds`` → ``SS.mmm``（AMLL 宽松写法，**不是**合法 TTML1）
    ``auto``    → Apple/AMLL 常见混合写法：<1min 用 ``SS.mmm``，<1h 用 ``M:SS.mmm``，
                  ≥1h 用 ``H:MM:SS.mmm``（分钟/小时不补前导零）

    先按毫秒四舍五入再拆分，避免 59.9999 出现 ``00:60.000`` 这类越界分钟/秒。
    """
    if seconds is None:
        seconds = 0.0
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        seconds = 0.0
    total_ms = int(round(max(0.0, seconds) * 1000))
    total_seconds, ms = divmod(total_ms, 1000)
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if style == "seconds":
        return f"{total_seconds}.{ms:03d}"
    if style == "auto":
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}.{ms:03d}"
        if minutes:
            return f"{minutes}:{secs:02d}.{ms:03d}"
        return f"{secs}.{ms:03d}"
    if style == "mmss":
        return f"{hours * 60 + minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def is_valid_time_expression(value: str, time_base: str = "media") -> bool:
    """按 W3C TTML1 §10.3.1 严格校验时间表达式（clock-time / offset-time）。"""
    v = str(value or "").strip()
    if not v:
        return False
    m = _TTML_CLOCK_RE.match(v)
    if m:
        if m.group("fraction") is not None and m.group("frames") is not None:
            return False                                  # 小数秒与帧段互斥
        if int(m.group("minutes")) > 59 or int(m.group("seconds")) > 60:
            return False
        if m.group("frames") is not None and time_base != "smpte":
            return False                                  # 帧段仅 timeBase="smpte" 合法
        return True
    return bool(_TTML_OFFSET_RE.match(v))


def is_amll_relaxed_time(value: str) -> bool:
    """AMLL TTML DB 规范允许、但 W3C TTML1 不合法的宽松写法（``MM:SS.mmm`` / ``SS.mmm``）。"""
    v = str(value or "").strip()
    if not v or is_valid_time_expression(v):
        return False
    return bool(_AMLL_RELAXED_RE.match(v))


def parse_time(text: str) -> float:
    """TTML 时间表达式 → 秒。

    支持 TTML1 的 ``hh:mm:ss[.fff]`` / ``hh:mm:ss:ff[.s]`` / ``10s`` / ``250ms``，
    并兼容 AMLL 宽松写法的 ``mm:ss.fff`` 与裸秒 ``12.5``。
    """
    v = str(text or "").strip()
    if not v:
        return 0.0
    m = _TTML_CLOCK_RE.match(v)
    if m:
        secs = int(m.group("hours")) * 3600 + int(m.group("minutes")) * 60 + int(m.group("seconds"))
        if m.group("fraction"):
            secs += float("0." + m.group("fraction"))
        if m.group("frames"):
            secs += int(m.group("frames")) / DEFAULT_FRAME_RATE
        if m.group("subframes"):
            secs += float("0." + m.group("subframes")) / DEFAULT_FRAME_RATE
        return secs
    m = _TTML_OFFSET_RE.match(v)
    if m:
        scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001,
                 "f": 1.0 / DEFAULT_FRAME_RATE, "t": 1.0 / DEFAULT_FRAME_RATE}
        return float(m.group("count")) * scale[m.group("metric")]
    parts = v.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def agent_type(agent_id: str) -> str:
    """xml:id → ttm:agent type（person / group / other）"""
    if agent_id == "v1000":
        return "group"
    if agent_id == "v2000":
        return "other"
    return "person"


class TTMLWriter:
    def __init__(
        self,
        mode: str = "amll",
        markers: str = "agent",
        line_id: str = "both",
        translation_style: str = "head",
        time_format: str = "clock",
        song_part: str = "",
        inline_translation_lang: str = "",
        agent_types: Optional[Dict[str, str]] = None,
        label_sections: bool = True,
        layered: bool = False,
    ):
        """
        ``mode``         ``amll``=AMLL DB 格式；``apple``=按段落分 div 的提交格式
        ``markers``      ``auto``（默认，按 mode 决定）/ ``agent`` / ``role`` / ``both``。
                         **Apple Music 的对唱左右分栏依赖 Apple 内部角色
                         ``ttm:role="x-duet-a"/"x-duet-b"``**，所以 ``mode="apple"`` 时
                         ``auto`` 解析成 ``both``；AMLL 模式仍按官方推荐只写 ``ttm:agent``。
        ``line_id``      ``itunes:key`` / ``xml:id`` / ``both``（默认）
        ``translation_style`` ``head`` / ``inline`` / ``both``
        ``time_format``  ``clock``(HH:MM:SS.mmm，W3C 合法，默认) / ``mmss``(MM:SS.mmm，仅 AMLL)
                         / ``seconds``(SS.mmm，仅 AMLL)
        ``song_part``    固定写入 div 的 itunes:songPart（留空=按段落/重复自动判断）
        ``layered``      **分层歌词**：允许同一 agent 的 `<p>` 时间重叠；`<p>` 的
                         begin/end 只按主唱单元算，`x-bg` 可以伸到 `<p>` 外。
                         这对应真实 Apple/AMLL 的“主歌词/和声/附加句同时出现”，
                         默认关闭，严格 W3C 行为不变。

        关于 Apple 内部角色 ``x-duet-a/b``：``markers`` 要求时**挂在行的 `<p>` 上**，
        这是唯一安全的位置。**绝不要**写成包住主唱 span 的包装层 ——
        AMLL 解析器（`@applemusic-like-lyrics/ttml`）对未知 role 走
        ``processWordElement``，它**不递归子节点**、只在包装层自己带 begin/end 时
        才产出一个词 → 整行被当成**一个词**，逐字时间轴全丢、**播放器一片空白**
        （实测 32 行 266 字 → 解析出 "32 个字"）。所以这里不提供"写在 span 上"的开关。

        背景人声**永远**写成主唱 ``<p>`` 内的 ``<span ttm:role="x-bg">``
        （AMLL 规范 §7.3）。不要提供"和声单独成行"的开关：AMLL 解析器遇到
        整行只有 x-bg 的 ``<p>`` 时主歌词为空、整行丢弃，播放器里就是看不见。
        """
        if mode not in ("amll", "apple", "apple-internal"):
            raise ValueError("mode 必须是 'amll' / 'apple' / 'apple-internal'")
        self.apple_internal = mode == "apple-internal"
        if self.apple_internal:
            # 真实 Apple/AMLL 内部导出：MM:SS、单 div、仅 itunes:key、匿名 agent
            time_format = "mmss"
            line_id = "itunes:key"
            markers = "agent"
            label_sections = False
            layered = True
        if markers == "auto":
            markers = "both" if mode == "apple" else "agent"
        if markers not in ("agent", "role", "both"):
            raise ValueError("markers 必须是 'auto' / 'agent' / 'role' / 'both'")
        if line_id not in ("itunes:key", "xml:id", "both"):
            raise ValueError("line_id 必须是 'itunes:key' / 'xml:id' / 'both'")
        if translation_style not in ("head", "inline", "both"):
            raise ValueError("translation_style 必须是 'head' / 'inline' / 'both'")
        if time_format not in ("clock", "mmss", "seconds", "auto"):
            raise ValueError("time_format 必须是 'clock' / 'mmss' / 'seconds' / 'auto'")
        self.mode = mode
        self.markers = markers
        self.line_id = line_id
        self.translation_style = translation_style
        self.time_format = time_format
        self.song_part = song_part
        # False = 不猜段落名（回写源文件本来没有 songPart 的情况，见 _split_sections）
        self.label_sections = label_sections
        self.layered = bool(layered)
        self.inline_translation_lang = inline_translation_lang
        # agent id → type 的覆盖表（例如参考文件里 v2 是 type="other"）
        self.agent_types: Dict[str, str] = dict(agent_types or {})
        self._paragraphs: List[int] = []       # 每行对应的段落号（write 时填充）

    # ------------------------------------------------------------------ 工具
    def t(self, seconds: float) -> str:
        return format_time(seconds, self.time_format)

    # ------------------------------------------------------------------ 主入口
    def write(
        self,
        lines: Sequence[AlignedLine],
        title: str = "",
        artists: Optional[Sequence[str]] = None,
        album: str = "",
        language: str = "zh-Hans",
        platform_ids: Optional[Dict[str, str]] = None,
        author: str = "",
        author_id: str = "",
        duration: Optional[float] = None,
        translation_lang: str = "",
        songwriters: Optional[Sequence[str]] = None,
        extra_meta: Optional[Dict[str, str]] = None,
        include_agents: bool = True,
    ) -> str:
        artists = [a for a in (artists or []) if a]
        platform_ids = {k: v for k, v in (platform_ids or {}).items() if v}
        # ⚠️ songwriters 必须是**列表**。传字符串时上面的列表推导会**逐字符拆开**，
        # 产出 `<songwriter>示</songwriter><songwriter>例</songwriter>…` 这种垃圾 ——
        # 而且不报错（真踩过：手写调用时传了 str）。这里兜一道。
        if isinstance(songwriters, str):
            songwriters = [s.strip() for s in songwriters.replace("，", ",").split(",")]
        songwriters = [s for s in (songwriters or []) if s]

        lines = [l for l in lines if l.units and l.end > l.begin]
        if not lines:
            raise ValueError("没有可输出的歌词行（所有行都没有时间戳）")

        # AMLL 规范 §7.2：即使是单人歌，每个 <p> 也要带 ttm:agent="v1" 并在 head 里定义 v1
        for l in lines:
            if not getattr(l, "agent", ""):
                l.agent = "v1"
        self._paragraphs = [getattr(l, "paragraph", 0) for l in lines]

        # 归一化每行的时间区间：保证 begin < end，且 span ⊆ p ⊆ div（TTML1 §10.4 / AMLL 规范 §6）
        intervals = [self._line_interval(l, self.layered) for l in lines]
        content_end = max(e for _, e in intervals)
        # 规范：单曲时专辑名应与歌曲名相同
        if not album and title:
            album = title

        out: List[str] = []
        if not self.apple_internal:
            out.append('<?xml version="1.0" encoding="UTF-8"?>')
        if self.apple_internal:
            tt_attrs = [
                ("xmlns", NS_TTML),
                ("xmlns:ttm", NS_TTM),
                ("xmlns:itunes", NS_ITUNES),
                ("xmlns:amll", NS_AMLL),
            ]
        else:
            tt_attrs = [
                ("xmlns", NS_TTML),
                ("xmlns:ttm", NS_TTM),
                ("xmlns:tts", NS_TTS),
                ("xmlns:itunes", NS_ITUNES),
                ("xmlns:amll", NS_AMLL),
                ("xml:lang", language or "zh-Hans"),
                ("itunes:timing", "Word"),
            ]
        out.append("<tt " + " ".join(f"{k}={_attr(v)}" for k, v in tt_attrs) + ">")

        # ---------------------------------------------------------- head
        out.append("  <head>")
        out.append('    <metadata xmlns="">' if self.apple_internal else "    <metadata>")
        if include_agents:
            for xml in self._agent_elements(lines, artists):
                out.append("      " + xml)
        for k, v in self._meta_pairs(title, artists, album, platform_ids, author, author_id, extra_meta):
            out.append("      " + f'<amll:meta key={_attr(k)} value={_attr(v)}/>')
        itunes_meta = self._itunes_metadata(lines, translation_lang, songwriters)
        if itunes_meta:
            out.extend("      " + ln for ln in itunes_meta)
        out.append("    </metadata>")
        out.append("  </head>")

        # ---------------------------------------------------------- body
        # AMLL 规范 §2：body/@dur 必须 ≥ 文件中最后一个时间戳的结束时间
        dur_value = max(float(duration), content_end) if duration else None
        dur_attr = f" dur={_attr(self.t(dur_value))}" if dur_value else ""
        out.append(f"<body{dur_attr}>")

        for section, group in self._split_sections(list(enumerate(lines, 1))):
            div_begin = min(intervals[i - 1][0] for i, _ in group)
            div_end = max(intervals[i - 1][1] for i, _ in group)
            attrs = [("begin", self.t(div_begin)), ("end", self.t(div_end))]
            if self.apple_internal:
                attrs.insert(0, ("xmlns", ""))
            if section and not self.apple_internal:
                attrs.append(("itunes:songPart", section))
            out.append("  <div " + " ".join(f"{k}={_attr(v)}" for k, v in attrs) + ">")
            for key_index, line in group:
                line_begin, line_end = intervals[key_index - 1]
                out.append("    " + self._p_inline(line, key_index, line_begin, line_end))
            out.append("  </div>")

        out.append("</body>")
        out.append("</tt>")
        return "\n".join(out) + "\n"

    # -------------------------------------------------------------- 元数据
    def _meta_pairs(self, title, artists, album, platform_ids, author, author_id, extra_meta):
        pairs: List[Tuple[str, str]] = []
        if title:
            pairs.append(("musicName", title))
        for a in artists:
            pairs.append(("artists", a))
        if album:
            pairs.append(("album", album))
        for key in META_KEYS:
            if platform_ids.get(key):
                pairs.append((key, str(platform_ids[key])))
        for key in META_KEYS:
            platform_ids.pop(key, None)
        for key, val in platform_ids.items():        # 未列出的自定义 ID 也保留
            if val:
                pairs.append((key, str(val)))
        if author_id:
            pairs.append(("ttmlAuthorGithub", str(author_id)))
        if author:
            pairs.append(("ttmlAuthorGithubLogin", author))
        for k, v in (extra_meta or {}).items():
            if v:
                pairs.append((k, str(v)))
        return pairs

    def _agent_elements(self, lines: Sequence[AlignedLine], artists: Sequence[str]) -> List[str]:
        used: List[str] = []
        for l in lines:
            if l.agent and l.agent not in used:
                used.append(l.agent)
        if not used:
            used = ["v1"]
        # `ttm:name` 只给"人"（person/character/organization）：group（合唱 v1000）
        # 与 other（v2000）不是某位歌手，按顺序硬套会把歌手名安到合唱上。
        # 旁白/念白（x-narration）也不是歌手，同样要排除 —— 否则会写出
        # "旁白 = hanser" 这种荒谬声明。
        narration = {l.agent for l in lines
                     if "narration" in (getattr(l, "role", "") or "").lower()}
        persons = [a for a in used
                   if a not in narration
                   and (self.agent_types.get(a) or agent_type(a))
                   in ("person", "character", "organization")]

        # **声明顺序 = 升序 v1, v2, v3, …**，不是"首次出现顺序"。
        # 为什么：Apple Music 的对唱左右栏按声部顺序排 —— 若 v2 先开口就排到第一栏，
        # 编辑台里「一声部」(v1) 的行会在 Apple Music 显示到第二栏（用户实测 bug：
        # "选 1 声部显示成 2 声部，选 2 声部显示成 1 声部"）。
        # v1000（合唱）/v2000 按数字自然排最后；非 vN 形式的未知 id 按字典序排在后面。
        def _agent_order(a: str) -> Tuple[int, int, str]:
            m = re.fullmatch(r"v(\d+)", a or "")
            return (0, int(m.group(1)), "") if m else (1, 0, a or "")

        out = []
        for agent in sorted(used, key=_agent_order):
            if self.apple_internal:
                # Apple 内部样例的 agent 是匿名自闭合；非 v1 常写 type="other"
                atype = self.agent_types.get(agent) or ("person" if agent == "v1" else "other")
                out.append(f'<ttm:agent type={_attr(atype)} xml:id={_attr(agent)}/>')
                continue
            atype = self.agent_types.get(agent) or agent_type(agent)
            name = ""
            if agent in persons:
                idx = persons.index(agent)
                name = artists[idx] if idx < len(artists) else ""
            if name:
                out.append(
                    f'<ttm:agent type={_attr(atype)} xml:id={_attr(agent)}>'
                    f'<ttm:name type="full">{_esc(name)}</ttm:name></ttm:agent>'
                )
            else:
                out.append(f'<ttm:agent type={_attr(atype)} xml:id={_attr(agent)}/>')
        return out

    def _itunes_metadata(self, lines: Sequence[AlignedLine], translation_lang: str,
                         songwriters: Sequence[str]) -> List[str]:
        """Apple Music 样式的翻译 / 音译 / 词曲作者（放在 <head><metadata> 里）"""
        if self.translation_style not in ("head", "both"):
            return []
        pairs = [(i, l) for i, l in enumerate(lines, 1) if l.translation]
        if not pairs and not songwriters:
            return []
        lang = translation_lang or "zh-Hans"
        out = [f'<iTunesMetadata xmlns={_attr(NS_ITUNES_META)}>']
        if pairs:
            out.append("  <translations>")
            out.append(f'    <translation type="subtitle" xml:lang={_attr(lang)}>')
            for idx, line in pairs:
                out.append(f'      <text for={_attr("L" + str(idx))}>{_esc(line.translation)}</text>')
            out.append("    </translation>")
            out.append("  </translations>")
        if songwriters:
            out.append("  <songwriters>")
            for s in songwriters:
                out.append(f"    <songwriter>{_esc(s)}</songwriter>")
            out.append("  </songwriters>")
        out.append("</iTunesMetadata>")
        return out

    # -------------------------------------------------------------- 结构
    def _split_sections(self, indexed_lines) -> List[Tuple[str, List[Tuple[int, AlignedLine]]]]:
        """按歌曲结构切成 ``<div>``。

        Apple 准则：「唱段之间必须分段」。切分依据（按优先级）：
        1. 源歌词里的**空行**（作者已经标好了段落）
        2. 段落之间大于 4 秒的停顿（间奏）
        段落名：文本重复出现的段落判为 ``Chorus``，其余 ``Verse``；
        用户在 ``--song-part`` 里指定时以用户为准。
        """
        items = list(indexed_lines)

        # ⚠️ 这里**不能**在 song_part 有值时直接 `return [(song_part, items)]`：
        # `song_part` 在两种情况下非空 —— ① 用户显式 `--song-part X`；
        # ② **编辑台读入了一份本来就带 itunes:songPart 的 Apple 格式文件**
        #    （`parse_ttml` 会把源文件的写法记进 write_options）。
        # 老写法的后果：编辑台打开 Apple 文件、随便改一个字再保存，
        # **整首歌的唱段分段会被合并成一个 <div>** —— 用户没碰过的东西被改了。
        # 正确做法：仍然按段落切，只是把**段名**统一用 song_part。
        groups: List[List[Tuple[int, AlignedLine]]] = []
        if self.apple_internal:
            return [("", items)]
        use_para = len(set(self._paragraphs)) > 1
        current: List[Tuple[int, AlignedLine]] = []
        cur_para = None
        for idx, line in items:
            para = self._paragraphs[idx - 1] if idx - 1 < len(self._paragraphs) else 0
            if current:
                # 有源歌词段落时按段落切；否则只在 apple 模式下按间奏停顿切
                # （按停顿猜出来的段落名不可靠，所以不写 songPart）
                cut = (para != cur_para) if use_para else (
                    self.mode == "apple" and line.begin - current[-1][1].end > 4.0)
                if cut:
                    groups.append(current)
                    current = []
            cur_para = para
            current.append((idx, line))
        if current:
            groups.append(current)

        if len(groups) <= 1:
            return [(self.song_part, items)]
        # 段名的优先级：
        #   ① 用户显式指定了 song_part → 每段都用它（这是用户明确要的名字）
        #   ② 否则按停顿猜出来的段落**不写名字**（猜的名字不可靠，见 --song-part 说明）
        #   ③ 否则按 label_sections 决定是"照源文件保持"还是"自己判 Verse/Chorus"
        if self.song_part:
            labels = [self.song_part] * len(groups)
        elif not use_para:
            labels = ["" for _ in groups]
        elif self.label_sections:
            labels = self._label_sections(groups)
        else:
            labels = [""] * len(groups)
        return list(zip(labels, groups))

    @staticmethod
    def _label_sections(groups: List[List[Tuple[int, AlignedLine]]]) -> List[str]:
        """重复出现的段落 = Chorus，其余 = Verse（启发式，仅用于 itunes:songPart）"""
        import re as _re
        from collections import Counter
        sigs = []
        for g in groups:
            text = "".join(l.text for _, l in g)
            sigs.append(_re.sub(r"[\s\.,，。!?！？、]", "", text).lower())
        counts = Counter(sigs)
        return ["Chorus" if counts[s] > 1 else "Verse" for s in sigs]


    # -------------------------------------------------------------- 时间区间
    @staticmethod
    def _line_interval(line: AlignedLine, main_only: bool = False) -> Tuple[float, float]:
        """一行的有效时间区间：由行内所有 span 推导，并保证 begin < end（TTML1 §10.4）。

        ``main_only``（分层歌词）：只按主唱层算 `<p>` 的区间，`x-bg` 可以伸到 p 外。
        """
        units = [u for u in line.units if not (main_only and u.is_bg)]
        starts = [float(u.start) for u in units if u.start is not None]
        ends = [float(u.end) for u in units if u.end is not None]
        begin = max(0.0, min(starts) if starts else float(line.begin or 0.0))
        end = max(ends) if ends else float(line.end or 0.0)
        if end <= begin:
            end = begin + MIN_INTERVAL
        return begin, end

    @staticmethod
    def _unit_interval(u: Unit, begin: float, end: float) -> Tuple[float, float]:
        """把 span 的时间夹进父 <p> 的区间（AMLL 规范 §6-2：子元素必须被父元素包住）。"""
        s0 = float(u.start) if u.start is not None else begin
        s1 = float(u.end) if u.end is not None else end
        highest = max(begin, end - MIN_INTERVAL)
        s0 = min(max(s0, begin), highest)
        s1 = min(max(s1, s0 + MIN_INTERVAL), end)
        return s0, s1

    def _p_inline(self, line: AlignedLine, key_index: int,
                  begin: Optional[float] = None, end: Optional[float] = None) -> str:
        """一行歌词 → 单行内联 XML（span 之间不能有换行，否则空白会丢）"""
        if begin is None or end is None:
            begin, end = self._line_interval(line, self.layered)
        key = f"L{key_index}"
        attrs = [("begin", self.t(begin)), ("end", self.t(end))]
        if self.markers in ("agent", "both") and line.agent:
            attrs.append(("ttm:agent", line.agent))
        # Apple 内部角色也可以直接挂在行上（对唱左右分栏的另一种写法）
        role_on_p = self.markers in ("role", "both") and line.role
        if role_on_p:
            attrs.append(("ttm:role", line.role))
        if self.line_id in ("xml:id", "both"):
            attrs.append(("xml:id", key))
        if self.line_id in ("itunes:key", "both"):
            attrs.append(("itunes:key", key))
        head = "<p " + " ".join(f"{k}={_attr(v)}" for k, v in attrs) + ">"

        def span_xml(u: Unit, prefix: str = "", suffix: str = "",
                     clamp: bool = True) -> str:
            if clamp:
                s0, s1 = self._unit_interval(u, begin, end)
            else:
                s0 = float(u.start) if u.start is not None else begin
                s1 = float(u.end) if u.end is not None else end
                if s1 <= s0:
                    s1 = s0 + MIN_INTERVAL
            return self._span_inline(u, s0, s1, prefix=prefix, suffix=suffix)

        body: List[str] = []
        main_units = [u for u in line.units if not u.is_bg]
        main_xml = "".join(span_xml(u) for u in main_units)
        main_begin = min((self._unit_interval(u, begin, end)[0] for u in main_units),
                         default=None)
        bg = [u for u in line.units if u.is_bg]
        bg_xml = ""
        bg_begin = bg_end = None
        if bg:
            parts: List[str] = []
            bounds: List[Tuple[float, float]] = []
            for i, u in enumerate(bg):
                s0, s1 = self._unit_interval(u, begin, end)
                if self.layered:
                    # 分层歌词：和声允许伸到主唱 <p> 之外
                    s0 = float(u.start) if u.start is not None else begin
                    s1 = float(u.end) if u.end is not None else end
                    if s1 <= s0:
                        s1 = s0 + MIN_INTERVAL
                bounds.append((s0, s1))
                parts.append(self._span_inline(
                    u, s0, s1,
                    prefix="(" if i == 0 else "",
                    suffix=")" if i == len(bg) - 1 else ""))
            bg_begin = min(b[0] for b in bounds)
            bg_end = max(b[1] for b in bounds)
            # AMLL 参考实现（generator.test.ts.snap）与规范 §7.1 都给 x-bg 包装层写 begin/end
            bg_xml = (f'<span ttm:role="x-bg" begin={_attr(self.t(bg_begin))} '
                      f'end={_attr(self.t(bg_end))}>' + "".join(parts) + "</span>")

        # 行内内容：主唱 span + 背景和声 span（+ 行内翻译）。
        # 注意：**不要**再套一层带 role 的 span —— 见构造函数说明与 AGENTS.md 铁律 #15。
        # 规范 §7.1：和声在主唱**之前**唱，就放在主唱前面；否则放 <p> 末尾。
        # 比较的是"和声开始"与"主唱开始"，不是 <p> 的 begin（<p> 区间是两者并集）。
        if bg_xml and bg_begin is not None and main_begin is not None and bg_begin < main_begin:
            body.append(bg_xml)
            body.append(main_xml)
        else:
            body.append(main_xml)
            body.append(bg_xml)

        if line.translation and self.translation_style in ("inline", "both"):
            lang_attr = f" xml:lang={_attr(self.inline_translation_lang)}" if self.inline_translation_lang else ""
            body.append(f'<span ttm:role="x-translation"{lang_attr}>{_esc(line.translation)}</span>')
        return head + "".join(body) + "</p>"

    def _span_inline(self, u: Unit, start: float, end: float,
                     prefix: str = "", suffix: str = "") -> str:
        # Apple 准则：被消音屏蔽的词语整个用星号替换
        text = prefix + (MASK if getattr(u, "muted", False) else u.text) + suffix
        return (f'<span begin={_attr(self.t(start))} '
                f'end={_attr(self.t(end))}>{_esc(text)}</span>')

    # 兼容旧接口
    def save(self, content: str, path: str):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return path


def write_ttml(lines, **kwargs) -> str:
    mode = kwargs.pop("mode", "amll")
    layered = bool(kwargs.pop("layered", False))
    return TTMLWriter(mode=mode, layered=layered).write(lines, **kwargs)
