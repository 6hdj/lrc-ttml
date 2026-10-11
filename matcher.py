"""
歌词 ↔ 音频 强制对齐引擎。

思路
----
1. 把歌词切分成"对齐单元"：中文按字、英文按词、标点附在前一个字上。
2. 把 ASR 的词级时间戳同样展开成对齐单元（中文词再按字均分）。
3. 用带仿射思想（线性罚分）的 Needleman–Wunsch 动态规划把两者**单调对齐**：
   相同字→高分；不同字→中等分（识别可能是同音字/错字，但位置仍然对）；
   多出来的识别词→低分删除（ASR 常把一句唱两遍或插入语气词）。
4. 有 LRC 行级时间戳时按行分窗口对齐（精度最高，且行时间完全尊重原 LRC）；
   没有时退化为全曲全局对齐。
5. 未匹配上的字用相邻已匹配字插值，最后做重叠消除、窗口收敛等后处理。

所有时间都来自真实音频，不依赖任何大模型。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from asr import AsrWord
# 时间轴后处理的三个算法与编辑台共用同一份实现（见 ttml_normalize 模块头）
from ttml_normalize import clamp_units_into_line, normalize_group, overlap_pair

# --------------------------------------------------------------------------
# 可选：有声调的拼音辅助（装了 pypinyin 精度更高，没装也能正常工作）
# --------------------------------------------------------------------------
try:  # pragma: no cover
    from pypinyin import lazy_pinyin as _lazy_pinyin

    _PINYIN_CACHE: Dict[str, str] = {}

    def _pinyin(ch: str) -> str:
        if not _is_cjk(ch):
            return ch
        v = _PINYIN_CACHE.get(ch)
        if v is None:
            try:
                v = "".join(_lazy_pinyin(ch))
            except Exception:
                v = ch
            _PINYIN_CACHE[ch] = v
        return v

    HAS_PINYIN = True
except Exception:  # pragma: no cover
    HAS_PINYIN = False

    def _pinyin(ch: str) -> str:
        return ch


# --------------------------------------------------------------------------
# 字符判定
# --------------------------------------------------------------------------
_CJK_RANGES = (
    (0x3400, 0x4DBF),    # 扩展A
    (0x4E00, 0x9FFF),    # 基本汉字
    (0xF900, 0xFAFF),    # 兼容汉字
    (0x3040, 0x30FF),    # 日文假名
    (0x31F0, 0x31FF),
    (0xAC00, 0xD7AF),    # 韩文音节
    (0x1100, 0x11FF),
    (0x20000, 0x2FA1F),  # 扩展B+
)

# 括号：用于标记背景人声（不作为文本输出）
_OPEN_BRACKETS = "（([【"
_CLOSE_BRACKETS = "）)]】"

_ASCII_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def _is_latin_word(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum())


def _is_attachable(ch: str) -> bool:
    """标点 / 符号：不作为独立对齐单元，附在相邻字上"""
    if ch in _OPEN_BRACKETS + _CLOSE_BRACKETS:
        return False
    return unicodedata.category(ch)[0] in ("P", "S")


# --------------------------------------------------------------------------
# 对齐单元
# --------------------------------------------------------------------------
@dataclass
class Unit:
    """一个对齐单元：一个字（中文）或一个词（英文）"""
    core: str                    # 用于匹配的文本
    line: int = 0
    index: int = 0               # 行内序号
    leading: str = ""            # 前置标点/空格
    trailing: str = ""           # 后置标点/空格
    is_bg: bool = False          # 背景人声
    start: Optional[float] = None
    end: Optional[float] = None
    prob: float = 0.0            # 匹配置信度
    matched: bool = False
    muted: bool = False          # 被消音屏蔽（Apple 准则：用星号替换整个词）

    @property
    def text(self) -> str:
        return self.leading + self.core + self.trailing

    @property
    def duration(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return max(0.0, self.end - self.start)


def scan_line(text: str, line_index: int = 0) -> List[Unit]:
    """把一行歌词切成对齐单元（保留标点、空格、括号归属）"""
    units: List[Unit] = []
    i = 0
    in_bg = False
    pending_lead = ""
    pending_space = False
    n = len(text)

    def _push(core: str) -> Unit:
        nonlocal pending_lead, pending_space
        lead = pending_lead
        if pending_space and units:
            # 中文之间原有的分隔空格不保留（AMLL 中文歌词不用空格），
            # 但中英混排/英文单词之间的空格必须保留
            prev_core = units[-1].core
            if prev_core and core and not (_is_cjk(prev_core[-1]) and _is_cjk(core[0])):
                lead = " " + lead
        u = Unit(core=core, line=line_index, index=len(units), leading=lead, is_bg=in_bg)
        pending_lead = ""
        pending_space = False
        units.append(u)
        return u

    while i < n:
        ch = text[i]
        if ch in _OPEN_BRACKETS:
            in_bg = True
            i += 1
            continue
        if ch in _CLOSE_BRACKETS:
            in_bg = False
            i += 1
            continue
        if ch.isspace():
            pending_space = True
            i += 1
            continue
        if _is_cjk(ch):
            _push(ch)
            i += 1
            continue
        m = _ASCII_WORD.match(text, i)
        if m and _is_latin_word(ch):
            _push(m.group(0))
            i = m.end()
            continue
        if _is_attachable(ch):
            if units:
                # 标点附到前一个单元（背景人声同理）
                units[-1].trailing += ch
            else:
                pending_lead += ch
            i += 1
            continue
        # 其他字符（希腊/西里尔/emoji 等）当作单字单元
        _push(ch)
        i += 1

    return units


# --------------------------------------------------------------------------
# ASR 词 → 对齐单元
# --------------------------------------------------------------------------
def expand_asr_words(words: Sequence[AsrWord]) -> List[Unit]:
    """把 ASR 词展开成单元：中文词按字均分时间，英文词整体保留"""
    units: List[Unit] = []
    for w in words:
        tokens = _split_mixed(w.text)
        if not tokens:
            continue
        total = max(0.0, w.end - w.start)
        # 以字符数为权重分配时间（中文逐字，英文按词长近似）
        weights = [max(1, len(t)) for t in tokens]
        wsum = float(sum(weights))
        cursor = w.start
        for t, wt in zip(tokens, weights):
            dur = total * (wt / wsum)
            units.append(Unit(core=t, start=round(cursor, 3), end=round(cursor + dur, 3),
                              prob=w.prob, matched=True))
            cursor += dur
    return units


def _split_mixed(text: str) -> List[str]:
    """把 "OK啦" 拆成 ["OK", "啦"]；纯文本过滤掉所有非语音字符"""
    out: List[str] = []
    buf = ""
    for ch in text or "":
        if _is_cjk(ch):
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch.isalnum() or ch in "'’-":
            buf += ch
        else:
            if buf:
                out.append(buf)
                buf = ""
    if buf:
        out.append(buf)
    return out


# --------------------------------------------------------------------------
# 相似度
# --------------------------------------------------------------------------
def similarity(a: str, b: str) -> float:
    """两个对齐单元的相似度。

    取值直接影响 DP 的取舍：识别结果里经常混进"幻觉词"（例如把音乐识别成
    "issippi"、把和声识别成"优优独播剧场"）。**跨语种/完全不相关**的配对给出
    负分，DP 就会选择"丢弃这个词"，而不是把歌词硬贴到它上面。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    la, lb = a.lower(), b.lower()
    if la == lb:
        return 0.97

    a_cjk, b_cjk = _is_cjk(a[0]), _is_cjk(b[0])
    if len(a) == 1 and len(b) == 1:
        if a_cjk != b_cjk:
            return -0.2                 # 汉字 vs 拉丁字母：基本不可能是同一个音
        if a_cjk and b_cjk:
            if HAS_PINYIN:
                pa, pb = _pinyin(a), _pinyin(b)
                if pa == pb:
                    return 0.85         # 同音字：极可能只是识别/用字差异
                if pa[:1] == pb[:1] or pa[-1:] == pb[-1:]:
                    return 0.5          # 声母或韵母相同
            return 0.4                  # 不同汉字：位置信息仍然有效（识别错字）
        return 0.25                     # 两个不同的拉丁字符

    if a_cjk != b_cjk:
        return -0.2
    # 多字单元（英文词 / 多字识别结果）
    if la in lb or lb in la:
        return 0.6
    common = sum(1 for x, y in zip(la, lb) if x == y)
    return 0.3 + 0.4 * (common / max(len(la), len(lb)))


# --------------------------------------------------------------------------
# 动态规划对齐
# --------------------------------------------------------------------------
GAP_OPEN = -0.5
# 极小的时间偏好：同样得分时，优先匹配**更早出现**的识别结果。
# 这样 whisper 复读整句时会对齐到先唱的那一遍，而不是被推到后面。
EARLY_BONUS = 1e-4


def align_sequences(targets: Sequence[Unit], sources: Sequence[Unit]) -> List[Optional[int]]:
    """
    单调对齐，返回与 targets 等长的列表，元素是匹配到的 sources 下标或 None。
    惩罚为线性 gap（每跳过一个单元扣 GAP_OPEN），对小规模序列足够且没有 O(n^3) 风险。
    """
    n, m = len(targets), len(sources)
    if n == 0:
        return []
    if m == 0:
        return [None] * n

    def pair_score(i: int, j: int) -> float:
        """targets[i] 与 sources[j]（0-based）配对得分"""
        return similarity(targets[i].core, sources[j].core) + EARLY_BONUS * (m - j) / m

    NEG = float("-inf")
    dp = [[NEG] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        dp[i][0] = GAP_OPEN * i
    for j in range(1, m + 1):
        dp[0][j] = GAP_OPEN * j
    for i in range(1, n + 1):
        row, prev = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            best = prev[j - 1] + pair_score(i - 1, j - 1)
            d = prev[j] + GAP_OPEN
            if d > best:
                best = d
            ins = row[j - 1] + GAP_OPEN
            if ins > best:
                best = ins
            row[j] = best

    # 反向回溯（保证落在最优路径上）
    mapping: List[Optional[int]] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        if abs(dp[i][j] - (dp[i - 1][j - 1] + pair_score(i - 1, j - 1))) < 1e-9:
            mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif abs(dp[i][j] - (dp[i][j - 1] + GAP_OPEN)) < 1e-9:
            j -= 1
        else:
            i -= 1
    return mapping


# --------------------------------------------------------------------------
# 行级结果
# --------------------------------------------------------------------------
@dataclass
class AlignedLine:
    text: str
    agent: str
    units: List[Unit] = field(default_factory=list)
    begin: float = 0.0
    end: float = 0.0
    matched: int = 0
    total: int = 0
    anchored: bool = False
    role: str = ""            # Apple 扩展角色：x-duet-a / x-duet-b / x-narration
    translation: str = ""     # 翻译 / 音译文本（可选）
    paragraph: int = 0        # 源歌词的段落号（空行分隔），用于分 <div>

    @property
    def match_rate(self) -> float:
        return (self.matched / self.total) if self.total else 0.0

    @property
    def main_units(self) -> List[Unit]:
        return [u for u in self.units if not u.is_bg]

    @property
    def bg_units(self) -> List[Unit]:
        return [u for u in self.units if u.is_bg]


@dataclass
class AlignReport:
    lines: List[AlignedLine]
    total_units: int
    matched_units: int
    anchored: bool
    warnings: List[str] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return (self.matched_units / self.total_units) if self.total_units else 0.0


# --------------------------------------------------------------------------
# 主对齐器
# --------------------------------------------------------------------------
class LyricMatcher:
    def __init__(
        self,
        window_pad: float = 0.35,
        min_unit_duration: float = 0.06,
        max_unit_duration: float = 3.5,
        log: Callable[[str], None] = print,
        pinyin_similarity: Optional[Callable[[str, str], float]] = None,
        min_match_score: float = 0.2,
    ):
        self.window_pad = window_pad
        self.min_unit_duration = min_unit_duration
        self.max_unit_duration = max_unit_duration
        self.min_match_score = min_match_score
        self.log = log
        self.similarity = pinyin_similarity

    # ------------------------------------------------------------------ 入口
    def align(self, lines: Sequence, words: Sequence[AsrWord], total_duration: Optional[float] = None) -> AlignReport:
        """
        ``lines``: preprocess.LyricLine 列表（text / agent / line_start / line_end）
        ``words``: faster-whisper 词级时间戳
        """
        warnings: List[str] = []
        anchored = any(getattr(l, "line_start", None) is not None for l in lines)

        unit_lines: List[List[Unit]] = []
        for idx, line in enumerate(lines):
            unit_lines.append(scan_line(line.text, idx))

        # 每行的目标时间窗口（秒）
        windows: List[Tuple[float, float]] = []
        n = len(lines)
        for idx, line in enumerate(lines):
            if anchored:
                start = line.line_start
                end = line.line_end
                if end is None or end <= start:
                    end = (lines[idx + 1].line_start if idx + 1 < n and lines[idx + 1].line_start else None)
                # LRC 的"行结束时间"常常是下一行的开始时间（中间可能是一大段间奏），
                # 用字数估一个合理的演唱时长上限，避免窗口跨越整段间奏
                n_chars = sum(1 for ch in line.text if not ch.isspace())
                est = max(2.0, 0.9 * n_chars + 1.5)
                if end is None or end <= start or end - start > est:
                    end = start + est
                nxt = lines[idx + 1].line_start if idx + 1 < n else None
                if nxt is not None and nxt > start:
                    end = min(end, nxt)
                windows.append((float(start), float(end)))
            else:
                windows.append((0.0, float(total_duration or (words[-1].end if words else 0.0))))

        asr_units = expand_asr_words(words)
        self.log(f"[对齐] 歌词单元 {sum(len(u) for u in unit_lines)} 个，音频识别单元 {len(asr_units)} 个，"
                 f"{'按 LRC 行时间分窗口对齐' if anchored else '全曲全局对齐'}")

        if anchored:
            buckets = self._bucket_by_window(asr_units, windows)
        else:
            buckets = None

        aligned: List[AlignedLine] = []
        matched_total = 0
        unit_total = 0

        if anchored:
            for idx, line in enumerate(lines):
                units = unit_lines[idx]
                lo, hi = windows[idx]
                src = buckets[idx] if buckets else []
                self._align_line(units, src, lo, hi, allow_fallback=True)
                al = self._finalize_line(line, units, lo, hi, anchored=True)
                aligned.append(al)
                matched_total += al.matched
                unit_total += al.total
                if al.total and al.match_rate < 0.5:
                    warnings.append(f"第 {idx + 1} 行匹配率偏低（{al.match_rate:.0%}）：{line.text[:20]}")
        else:
            # 全局对齐：把每行的单元拼成一条序列，对齐后再按行拆开
            flat: List[Unit] = []
            for units in unit_lines:
                flat.extend(units)
            mapping = align_sequences(flat, asr_units)
            for u, m in zip(flat, mapping):
                if m is None:
                    u.matched = False
                    u.prob = 0.0
                    continue
                su = asr_units[m]
                if similarity(u.core, su.core) < self.min_match_score:
                    u.matched = False
                    u.prob = 0.0
                    continue
                u.start, u.end, u.prob, u.matched = su.start, su.end, su.prob, True
            for idx, line in enumerate(lines):
                units = unit_lines[idx]
                self._interpolate(units, 0.0, total_duration or (asr_units[-1].end if asr_units else 0.0))
                al = self._finalize_line(line, units, 0.0, total_duration or 0.0, anchored=False)
                aligned.append(al)
                matched_total += al.matched
                unit_total += al.total

        self._fix_cross_line_overlaps(aligned)
        self._clamp_units_into_lines(aligned)

        report = AlignReport(lines=aligned, total_units=unit_total, matched_units=matched_total,
                             anchored=anchored, warnings=warnings)
        self.log(f"[对齐] 字级匹配率 {report.match_rate:.1%}")
        return report

    # -------------------------------------------------------------- 内部工具
    def _bucket_by_window(self, asr_units: Sequence[Unit], windows: Sequence[Tuple[float, float]]) -> List[List[Unit]]:
        """把 ASR 单元按行时间窗口分桶。

        窗口**允许重叠**（各自向两边扩 ``window_pad``）：whisper 的字级时间戳
        常常比 LRC 行时间早 0.1~0.3s，硬切会让每行第一个字掉进上一行的桶里。
        多出来的单元由 DP 的 gap 罚分自然丢弃。
        """
        buckets: List[List[Unit]] = [[] for _ in windows]
        for u in asr_units:
            c = (u.start + u.end) / 2.0
            placed = False
            for i, (lo, hi) in enumerate(windows):
                if lo - self.window_pad <= c <= hi + self.window_pad:
                    buckets[i].append(u)
                    placed = True
            if not placed:  # pragma: no cover - 落在所有窗口之外（前奏/尾奏）
                continue
        return buckets

    def _apply_mapping(self, units: List[Unit], sources: List[Unit]) -> List[float]:
        """跑一次 DP 并把结果写回单元；返回被保留配对的相似度列表。

        DP 本身只关心总分，有时会把歌词"贴"到明显无关的幻觉词上（贴上去的收益
        比丢弃两个单元更高）。这里再做一道阈值过滤：相似度太低的一律当作未匹配，
        交给插值处理，结果反而更稳。
        """
        mapping = align_sequences(units, sources)
        sims: List[float] = []
        for u, m in zip(units, mapping):
            if m is None:
                u.matched = False
                u.prob = 0.0
                continue
            su = sources[m]
            sim = similarity(u.core, su.core)
            if sim < self.min_match_score:
                u.matched = False
                u.prob = 0.0
                continue
            u.start, u.end, u.prob, u.matched = su.start, su.end, su.prob, True
            sims.append(sim)
        return sims

    def _align_line(self, units: List[Unit], sources: List[Unit], lo: float, hi: float, allow_fallback: bool = True):
        """对一行做 DP 对齐 + 插值"""
        if not units:
            return
        if not sources:
            # 该窗口内没有识别到任何东西：按行窗口按字数比例分配
            self._proportional(units, lo, hi)
            return

        sims = self._apply_mapping(units, sources)

        if allow_fallback and not self._trustworthy(units, sources, sims, lo, hi):
            self._proportional(units, lo, hi)
            return

        self._interpolate(units, lo, hi)

    def _trustworthy(self, units: List[Unit], sources: List[Unit], sims: List[float],
                     lo: float, hi: float) -> bool:
        """判断这一行的识别结果是否可信。

        整曲识别时 whisper 经常在短句（特别是背景和声）上"编词"
        （例如唱"去看繁星"却识别成"优优独播剧场"）。这种情况下按识别结果
        给时间反而更差，不如在该行的 LRC 窗口内按字数均分。
        """
        total = len(units)
        hit = len(sims)
        if hit < max(1, int(0.35 * total)):
            return False
        span = hi - lo
        avg = sum(sims) / len(sims)
        if span <= 3.5 and avg < 0.45:
            return False
        if span <= 4.0 and len(sources) > 2.5 * total:
            return False
        return True

    def _proportional(self, units: List[Unit], lo: float, hi: float):
        """按单元权重把 [lo, hi] 均分给所有单元（无音频信息时的兜底）"""
        est = max(1.2, 0.8 * len(units) + 0.8)
        hi = min(hi, lo + est) if hi > lo else lo + est
        if hi <= lo:
            hi = lo + est
        weights = [max(1.0, len(u.core) * 1.0) for u in units]
        total = sum(weights)
        cursor = lo
        for u, w in zip(units, weights):
            dur = (hi - lo) * (w / total)
            u.start = round(cursor, 3)
            u.end = round(cursor + dur, 3)
            u.matched = False
            u.prob = 0.15
            cursor += dur

    def _interpolate(self, units: List[Unit], lo: float, hi: float):
        """为未匹配的单元插值填充时间"""
        idx_matched = [i for i, u in enumerate(units) if u.start is not None and u.end is not None and u.matched]
        if not idx_matched:
            self._proportional(units, lo, hi)
            return

        first, last = idx_matched[0], idx_matched[-1]

        # 头部：第一个匹配之前的单元
        if first > 0:
            start_bound = max(lo, units[first].start - 0.6 * first)
            span = units[first].start - start_bound
            need = self.min_unit_duration * first + 0.05
            if span < need:
                # 前面完全没空间：多半是第一个匹配字被错配到了一个很早/很长的识别词上，
                # 从它身上借一点时间给前面的字（只缩短、不移动其它字）
                u0 = units[first]
                dur0 = (u0.end - u0.start) if u0.end is not None else 0.0
                borrow = min(0.45 * first, max(0.0, dur0 - 0.3))
                if borrow > 0:
                    u0.start = round(u0.start + borrow, 3)
                    span += borrow
                    start_bound = units[first].start - span
            span = max(span, 0.0)
            step = span / first if first else 0.0
            for k in range(first):
                u = units[k]
                u.start = round(start_bound + step * k, 3)
                u.end = round(start_bound + step * (k + 1), 3)
                u.matched = False

        # 中部：两个匹配单元之间
        for a, b in zip(idx_matched, idx_matched[1:]):
            gap = b - a - 1
            if gap <= 0:
                continue
            left_end = units[a].end
            right_start = units[b].start
            span = right_start - left_end
            need = self.min_unit_duration * gap + 0.05
            if span < need:
                # 两侧匹配字挤在一起，中间没有空间：从它们各自身上借一点
                deficit = need - span
                ua, ub = units[a], units[b]
                take_a = min(max(0.0, (ua.end - ua.start) - 0.3), deficit / 2.0)
                ua.end = round(ua.end - take_a, 3)
                take_b = min(max(0.0, (ub.end - ub.start) - 0.3), deficit - take_a)
                ub.start = round(ub.start + take_b, 3)
                left_end = ua.end
                right_start = ub.start
                span = max(0.0, right_start - left_end)
            if span <= 0:
                for k in range(a + 1, b):
                    units[k].start = round(left_end, 3)
                    units[k].end = round(left_end + self.min_unit_duration, 3)
                    units[k].matched = False
                continue
            step = span / (gap + 1)
            for k in range(a + 1, b):
                off = k - a
                units[k].start = round(left_end + step * (off - 0.5), 3)
                units[k].end = round(left_end + step * (off + 0.5), 3)
                units[k].matched = False

        # 尾部：最后一个匹配之后的单元
        if last < len(units) - 1:
            tail = len(units) - 1 - last
            end_bound = min(hi, units[last].end + 0.6 * tail)
            span = max(0.0, end_bound - units[last].end)
            need = self.min_unit_duration * tail + 0.05
            if span < need:
                u0 = units[last]
                dur0 = (u0.end - u0.start) if u0.start is not None else 0.0
                borrow = min(0.45 * tail, max(0.0, dur0 - 0.3))
                if borrow > 0:
                    u0.start = round(u0.start + borrow, 3)
                    span += borrow
            step = span / tail if tail else 0.0
            for k in range(last + 1, len(units)):
                off = k - last
                u = units[k]
                u.start = round(units[last].end + step * (off - 1), 3)
                u.end = round(units[last].end + step * off, 3)
                u.matched = False

    def _finalize_line(self, line, units: List[Unit], lo: float, hi: float, anchored: bool) -> AlignedLine:
        """行内后处理：窗口收敛、消除重叠、保证 begin<end"""
        main = [u for u in units if not u.is_bg]
        bg = [u for u in units if u.is_bg]

        for group in (main, bg):
            self._normalize_group(group, lo, hi)

        timed = [u for u in units if u.start is not None]
        if timed:
            begin = min(u.start for u in timed)
            end = max(u.end for u in timed)
        else:
            begin, end = lo, hi

        if anchored:
            # 行时间不得越过 LRC 给的范围（留一点余量）
            begin = max(begin, lo - 0.15)
            end = min(end, hi + 0.15)
            if end <= begin:
                end = begin + 0.3

        al = AlignedLine(
            text=line.text,
            agent=getattr(line, "agent", "v1") or "v1",
            units=units,
            begin=round(begin, 3),
            end=round(end, 3),
            matched=sum(1 for u in units if u.matched),
            total=len(units),
            anchored=anchored,
            role=getattr(line, "role", "") or "",
            paragraph=getattr(line, "paragraph", 0) or 0,
        )
        return al

    def _normalize_group(self, group: List[Unit], lo: float, hi: float):
        """同一组（主唱 / 背景）内部：**按文本顺序**摆放时间，消除重叠并收敛到窗口。

        实际算法在 :func:`ttml_normalize.normalize_group` —— 与编辑台的
        「时间轴自动修复」**共用同一份实现**，避免"自动生成允许、手动改就报错"。
        """
        normalize_group(group, lo, hi, self.min_unit_duration, self.max_unit_duration)

    def _fix_cross_line_overlaps(self, lines: List[AlignedLine]):
        """同一演唱者的相邻行不得重叠（同时保证字不越出行的 begin/end）。

        注意要同时考虑主唱层和背景和声层：整行都是和声的 ``<p>`` 也不能和
        上一行重叠。
        """
        for a, b in zip(lines, lines[1:]):
            if a.agent != b.agent:
                continue
            new_a_end = overlap_pair(a.units, b.units, a.begin, self.min_unit_duration)
            if new_a_end is not None:
                a.end = min(a.end, new_a_end)

    def _clamp_units_into_lines(self, lines: List[AlignedLine]):
        """最后一道保险：任何字的时间都必须落在所属行的 begin/end 之内，且按文本顺序单调"""
        for al in lines:
            al.begin, al.end = clamp_units_into_line(al.units, al.begin, al.end,
                                                     self.min_unit_duration)


    # ------------------------------------------------------------ 单行精修
    def align_one_line(self, line, line_index: int, words: Sequence[AsrWord],
                       lo: float, hi: float, anchored: bool = True) -> AlignedLine:
        """重新对齐单独一行（用于低匹配率行的二次精修）"""
        units = scan_line(line.text, line_index)
        src = expand_asr_words(words)
        self._align_line(units, src, lo, hi)
        return self._finalize_line(line, units, lo, hi, anchored)


# --------------------------------------------------------------------------
# 便捷函数
# --------------------------------------------------------------------------
def align_lyrics(lines: Sequence, words: Sequence[AsrWord], total_duration: Optional[float] = None,
                 log: Callable[[str], None] = print) -> AlignReport:
    return LyricMatcher(log=log).align(lines, words, total_duration)

