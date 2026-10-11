"""
歌词预处理模块
支持纯文本和 LRC 格式输入，输出结构化的歌词行数据
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WordToken:
    """单个字/词标记"""
    text: str
    is_background: bool = False  # 是否背景人声
    is_punctuation: bool = False  # 是否标点


@dataclass
class LyricLine:
    """一行歌词"""
    text: str  # 原始文本
    tokens: List[WordToken] = field(default_factory=list)  # 分词结果
    line_start: Optional[float] = None  # 行开始时间（秒），LRC输入时有
    line_end: Optional[float] = None  # 行结束时间（秒）
    agent: str = "v1"  # 演唱者ID
    role: str = ""  # Apple 扩展角色：x-duet-a / x-duet-b / x-narration
    manual_agent: bool = False  # 这一行的演唱者是否来自歌词里手写的标记
    manual_background: bool = False  # 歌词里标了 [和声]/[bg]：整行是背景人声（x-bg 层）
    paragraph: int = 0  # 段落号（源歌词里的空行分隔，Apple 要求唱段之间分段）
    muted: bool = False  # 该行/该字是否被消音（对齐时检测）


def parse_time_lrc(time_str: str) -> float:
    """解析 LRC 时间戳 [mm:ss.xx] 为秒（小数秒可省略，如 `[00:04]`）"""
    match = re.match(r'(\d+):(\d+(?:\.\d+)?)', time_str)
    if match:
        minutes = int(match.group(1))
        seconds = float(match.group(2))
        return minutes * 60 + seconds
    return 0.0


def tokenize_chinese(text: str) -> List[WordToken]:
    """
    中文歌词分词：按字拆分，识别背景人声（括号内容）和标点
    英文按空格分词
    """
    tokens = []
    i = 0
    in_background = False

    while i < len(text):
        ch = text[i]

        # 检测背景人声开始
        if ch in '（([':
            in_background = True
            i += 1
            continue
        # 检测背景人声结束
        if ch in '）)]':
            in_background = False
            i += 1
            continue

        # 跳过空格
        if ch == ' ':
            i += 1
            continue

        # 标点
        if ch in '，。！？、,.!?~…—-':
            tokens.append(WordToken(text=ch, is_punctuation=True, is_background=in_background))
            i += 1
            continue

        # 中文字符：按字拆分
        if '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff':
            tokens.append(WordToken(text=ch, is_background=in_background))
            i += 1
            continue

        # 英文/数字：按词读取
        if ch.isascii() and (ch.isalnum() or ch in "'’"):
            word = []
            while i < len(text) and text[i].isascii() and (text[i].isalnum() or text[i] in "'’"):
                word.append(text[i])
                i += 1
            tokens.append(WordToken(text=''.join(word), is_background=in_background))
            continue

        # 其他字符
        tokens.append(WordToken(text=ch, is_background=in_background))
        i += 1

    return tokens


def _all_background(line: "LyricLine") -> bool:
    """这一行是否整行都是背景人声（括号包裹，或歌词里标了 [和声]/[bg]）"""
    if getattr(line, "manual_background", False):
        return True
    return bool(line.tokens) and all(t.is_background for t in line.tokens)


# 整行背景人声并入相邻主唱行的时间上限（秒）：超过就当作独立行，避免把远隔的唱段并进来
BG_MERGE_MAX_SPAN = 12.0


def merge_background_lines(lines: List[LyricLine]) -> List[LyricLine]:
    """把「整行都在括号里」的背景人声行并进相邻主唱行。

    AMLL / Apple 的数据模型里，背景人声是**某一行的子内容**
    （``<p>`` 里的 ``<span ttm:role="x-bg">``），不是单独的一行。
    如果让它单独成行，生成的 ``<p>`` 里就只有 ``x-bg``，而 AMLL 解析器
    在 ``case Values.RoleBg`` 时**不会**把背景人声计入该行的 fullText
    （见 amll-dev/applemusic-like-lyrics ``packages/ttml/src/parser.ts``），
    于是这一行主歌词为空、``words`` 为空 —— 播放器里就是"背景人声不显示"。

    规则（对齐 AMLL 规范 §7.3）：
    * 背景行在**主唱行之后** → 追加到上一主唱行的 token 末尾（主唱先唱、和声在后）；
    * 背景行在**主唱行之前** → 并入下一主唱行，token 放在最前面；
    * 找不到合适邻居、或时间跨度超过 ``BG_MERGE_MAX_SPAN`` → 保持独立行。
    """
    if not lines:
        return lines

    def as_bg_text(line: LyricLine) -> str:
        """[和声]/[bg] 标出来的行原文可能没有括号，补一对半角括号，
        分词才会把它判成背景层（规范 §7.3 也要求用半角括号包裹）。"""
        t = line.text.strip()
        if getattr(line, "manual_background", False) and not (t.startswith("(") or t.startswith("（")):
            return f"({t})"
        return line.text

    # 第一趟：向后找最近的主唱行
    merged: List[LyricLine] = []
    for line in lines:
        target = None
        if _all_background(line):
            for cand in reversed(merged):
                if not _all_background(cand):
                    target = cand
                    break
            if target is not None:
                a = target.line_start if target.line_start is not None else 0.0
                b = line.line_start if line.line_start is not None else a
                if (b - a) > BG_MERGE_MAX_SPAN:
                    target = None
        if target is None:
            merged.append(line)
            continue
        target.tokens = list(target.tokens) + tokenize_chinese(as_bg_text(line))
        target.text = target.text + as_bg_text(line)
        if line.line_end is not None:
            target.line_end = line.line_end

    # 第二趟：开头就是背景行的情形，向前并入下一主唱行
    out: List[LyricLine] = []
    i = 0
    while i < len(merged):
        line = merged[i]
        if (_all_background(line) and i + 1 < len(merged)
                and not _all_background(merged[i + 1])):
            nxt = merged[i + 1]
            a = line.line_start if line.line_start is not None else 0.0
            b = nxt.line_start if nxt.line_start is not None else a
            if (b - a) <= BG_MERGE_MAX_SPAN:
                nxt.tokens = tokenize_chinese(as_bg_text(line)) + list(nxt.tokens)
                nxt.text = as_bg_text(line) + nxt.text
                if line.line_start is not None:
                    nxt.line_start = line.line_start
                out.append(nxt)
                i += 2
                continue
        out.append(line)
        i += 1
    return out


def _strip_marker(line: LyricLine) -> bool:
    """剥掉行首的演唱者标记，并把 agent/role 记到行上。

    为什么要在**合并背景人声之前**就剥：
    形如 ``[v1]（一路践行）`` 的和声行，分词后 ``v1`` 也会被判成背景 token
    （``_all_background`` 为真），于是它会被并进主唱行；等合并完再剥标记时，
    ``[v1]`` 已经跑到了行**中间**，``detect_agent_switch`` 只认行首 → 标记
    留在歌词里，最后渲染成 ``(v1一路践行)``。所以必须先剥后并。

    返回是否找到了标记。
    """
    m = re.match(r'^\s*\[([^\]]{1,12})\]\s*', line.text)
    if not m:
        return False
    key = m.group(1).strip().lower()
    if key not in _AGENT_MARKERS and key not in _BACKGROUND_MARKERS:
        return False
    if key in _BACKGROUND_MARKERS:
        # 规范 §7.3：背景人声是**行的子内容**（`<span ttm:role="x-bg">`），不是一个演唱者。
        # 所以 [和声]/[bg] 不写 agent，而是把整行标成背景层，由 merge_background_lines
        # 并进相邻主唱行；并进时若原文没有括号就补一对半角括号（分词据此判定背景）。
        line.manual_background = True
        line.agent = "v1"
        line.role = ""
    else:
        line.agent, line.role = _AGENT_MARKERS[key]
        line.manual_agent = True
    line.text = line.text[m.end():].strip()
    line.tokens = tokenize_chinese(line.text)
    return True


def parse_lyrics(raw_text: str) -> List[LyricLine]:
    """
    解析歌词文本，自动识别 LRC 格式或纯文本
    """
    lines = []
    raw_lines = raw_text.strip().split('\n')

    # 检测是否为 LRC 格式
    # ⚠️ 小数秒必须写成**可选**：`[00:04]` 这种写法在实际歌词里很常见
    # （尤其是手打/老工具导出的 LRC）。原来这里写死 `\.\d+`，
    # 于是整份"无小数秒"的 LRC 会掉进纯文本分支，两个后果都很难看：
    #   1) 丢掉全部行级锚点 → 退化成全曲全局对齐，精度下降；
    #   2) 时间戳文本 `[00:04]` 被当成歌词留在正文里，一路写进 TTML。
    # 这里的判定必须与下面第 239 行的提取正则 `\[(\d+:\d+\.?\d*)\]` 保持同一严格度。
    is_lrc = any(re.match(r'^\[\d+:\d+(?:\.\d+)?\]', line.strip())
                 for line in raw_lines if line.strip())

    if is_lrc:
        # 解析 LRC
        lrc_lines = []
        para = 0
        pending_break = False
        # ⚠️ 独立成行的演唱者标记（`[v1]` / `[v2]` / `[和声]`…）
        # LRC 里 `[...]` 是**时间戳语法**，`[v1]` 不是合法时间戳 →
        # 下面 `for time_str in matches` 一次都不执行 → **整行被静默丢掉**。
        # 后果特别隐蔽：`[v1]` 单独占一行这种在 .txt 里完全正常的写法，
        # 放到 .lrc 里就**悄悄失效**了（表现是全曲只有 v1、对唱不分栏、**且不报错**）。
        # 修法：把它**挂到下一行行首**，交给 `_strip_marker` 用同一套逻辑处理，
        # 不新增任何标记解析分支。
        pending_marker = ""
        for line in raw_lines:
            line = line.strip()
            if not line:
                # 空行 = 唱段分隔（Apple：唱段之间分段）
                if lrc_lines:
                    pending_break = True
                continue
            # 跳过元数据标签 [ti:] [ar:] 等
            if re.match(r'^\[(ti|ar|al|by|offset):', line, re.IGNORECASE):
                continue
            # 解析时间戳和文本，支持多个时间戳 [00:01.00][00:02.00]歌词
            matches = re.findall(r'\[(\d+:\d+\.?\d*)\]', line)
            text = re.sub(r'\[\d+:\d+\.?\d*\]', '', line).strip()
            if not matches:
                m = re.match(r'^\[([^\]]{1,12})\]\s*$', text)
                if m:
                    key = m.group(1).strip().lower()
                    if key in _AGENT_MARKERS or key in _BACKGROUND_MARKERS:
                        pending_marker = text          # 留给下一行
                # 其余无时间戳的行照旧忽略（元数据标签已在上面跳过）
                continue
            if not text:
                continue
            if pending_marker:
                text = pending_marker + text
                pending_marker = ""
            if pending_break and lrc_lines:
                para += 1
                pending_break = False
            for time_str in matches:
                lrc_lines.append((parse_time_lrc(time_str), text, para))

        # 按时间排序
        lrc_lines.sort(key=lambda x: x[0])

        # 排序后重新编号段落（保持时间顺序上的连续性）
        para_map: Dict[int, int] = {}
        for start, text, p in lrc_lines:
            if p not in para_map:
                para_map[p] = len(para_map)
        for idx, (start, text, p) in enumerate(lrc_lines):
            tokens = tokenize_chinese(text)
            end = lrc_lines[idx + 1][0] if idx + 1 < len(lrc_lines) else None
            new_line = LyricLine(text=text, tokens=tokens, line_start=start, line_end=end,
                                 paragraph=para_map[p])
            _strip_marker(new_line)          # 先剥标记，再合并背景人声（见 _strip_marker 注释）
            lines.append(new_line)
    else:
        # 纯文本：按行拆分，空行分段
        para = 0
        for line in raw_lines:
            line = line.strip()
            if not line:
                if lines:
                    para += 1
                continue
            tokens = tokenize_chinese(line)
            new_line = LyricLine(text=line, tokens=tokens, paragraph=para)
            _strip_marker(new_line)
            lines.append(new_line)

    # 整行括号 = 背景人声 → 并进相邻主唱行（AMLL：背景人声是行的子内容，不是独立行）
    return merge_background_lines(lines)


# 演唱者标记 → (ttm:agent, Apple 扩展角色)
# 依据 AMLL 规范：
#   §4.1  agent 的 xml:id 从 v1 连续递增；**group（合唱）用 v1000**，other 用 v2000
#   §7.2  对唱 = 不同的 ttm:agent 挂在 <p> 上
#   §7.3  背景人声是 x-bg **层**，不是演唱者 → 见 _BACKGROUND_MARKERS
_AGENT_MARKERS = {
    "v1": ("v1", "x-duet-a"), "a": ("v1", "x-duet-a"), "duet-a": ("v1", "x-duet-a"),
    "duet1": ("v1", "x-duet-a"), "1": ("v1", "x-duet-a"),
    "男声": ("v1", "x-duet-a"), "男": ("v1", "x-duet-a"), "主唱": ("v1", ""),
    "v2": ("v2", "x-duet-b"), "b": ("v2", "x-duet-b"), "duet-b": ("v2", "x-duet-b"),
    "duet2": ("v2", "x-duet-b"), "2": ("v2", "x-duet-b"),
    "女声": ("v2", "x-duet-b"), "女": ("v2", "x-duet-b"), "对唱": ("v2", "x-duet-b"),
    "v3": ("v3", "x-duet-a"), "3": ("v3", "x-duet-a"),
    # 规范 §4.1：group（合唱）固定 v1000，type="group"
    "合唱": ("v1000", ""), "群唱": ("v1000", ""), "齐唱": ("v1000", ""),
    "group": ("v1000", ""),
    "narration": ("v3", "x-narration"), "旁白": ("v3", "x-narration"),
    "独白": ("v3", "x-narration"), "念白": ("v3", "x-narration"),
}

# 背景人声标记：不产生 agent，改为把整行变成 x-bg 层（规范 §7.3）
_BACKGROUND_MARKERS = {"和声", "bg", "backing", "背景", "伴唱"}


def detect_agent_switch(lines: List[LyricLine]) -> List[LyricLine]:
    """
    检测演唱者切换标记，如 [v1] [v2] / [duet-a] [duet-b] / [旁白]

    标记 → (ttm:agent, Apple 扩展角色)  —— 与 :data:`_AGENT_MARKERS` / :data:`_BACKGROUND_MARKERS` 对齐
        [v1] [a] [duet-a] [男声] [主唱]       → v1,  x-duet-a
        [v2] [b] [duet-b] [女声] [女] [对唱]   → v2,  x-duet-b
        [合唱] [齐唱] [群唱] [group]          → v1000, 无角色（head 里 type="group"）
        [和声] [bg] [backing] [背景] [伴唱]    → **不产生 agent**：整行并进相邻主唱行，
                                                成为 <span ttm:role="x-bg"> 背景层（规范 §7.3）
        [narration] [旁白] [独白] [念白]       → v3, x-narration
    行首没有标记时沿用上一行的演唱者。
    """
    current_agent = "v1"
    current_role = ""
    manual_run = False
    for line in lines:
        agent_match = re.match(r'^\s*\[([^\]]{1,12})\]\s*', line.text)
        if (agent_match
                and agent_match.group(1).strip().lower() in _AGENT_MARKERS):
            key = agent_match.group(1).strip().lower()
            current_agent, current_role = _AGENT_MARKERS[key]
            line.text = line.text[agent_match.end():].strip()
            line.tokens = tokenize_chinese(line.text)
            manual_run = True
        elif (agent_match
                and agent_match.group(1).strip().lower() in _BACKGROUND_MARKERS):
            # [和声]/[bg]：不是演唱者，是背景层 —— 剥掉标记，交给 merge_background_lines
            line.text = line.text[agent_match.end():].strip()
            line.tokens = tokenize_chinese(f"({line.text})")
            line.manual_background = True
            continue
        elif getattr(line, "manual_agent", False):
            # 标记已在 parse_lyrics 里剥掉了（和声行必须先剥才能正确合并），
            # 这里沿用那一行自己带上的 agent/role，不要覆盖回默认值。
            current_agent, current_role = line.agent, line.role
            manual_run = True
        line.agent = current_agent
        line.role = current_role
        line.manual_agent = manual_run
    return lines
