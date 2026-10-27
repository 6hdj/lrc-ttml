"""
时间轴规范化 / 自动修复 —— 「校验 → 修复 → 复核」闭环里的**修复**那一半。

设计要点（来自 `docs/竞品调研与优化方案.md` §5.2）
------------------------------------------------
「检测」已经在 ``ttml_check.py`` 里有了；「修复」原本只存在于 ``matcher`` 的
生成阶段（``_normalize_group`` / ``_fix_cross_line_overlaps`` /
``_clamp_units_into_lines``），而且产出的是不可回写的中间产物。

于是这里把**那三个函数原样抽成纯函数**，实现"一份算法两处用"：

* 生成链路：``matcher`` 调用它们 —— 行为与抽取前完全一致（selftest 锁死）；
* 编辑链路：``normalize_timeline(doc)`` 调用同一份 —— 保证
  "AI 生成的允许的重叠"和"人工改后的修复"遵循**完全相同**的时间规则。

否则就会出现「自动生成允许的重叠，手动改一下就被判非法」的自相矛盾体验。

规则表
------
======  ==========================  ============================================
规则     含义                        修复策略
======  ==========================  ============================================
R1      字倒序 / ``begin >= end``   按文本顺序前向游标推开
R2      行内字重叠                  同上（main / bg / translation 三层各自处理）
R3      字越出行 ``begin/end``      夹紧到行区间，必要时回推 start
R4      同 agent 相邻行重叠         把上一行末尾收到 ``下一行 begin - 0.01``
R5      字时长过短（< 0.08s）       拉长到最小字长
R6      字时长过长（> 2.5s）        截到最大字长
R7      行内长停顿（> 1.5s）        **不自动修**，只进 ``unresolved``
======  ==========================  ============================================

必须保留的例外
--------------
* **和声层允许与主唱重叠**（AMLL 规范：「因混音而使时间重叠的情况除外」）。
  规范化只按层内处理，绝不把和声层和主唱层压成互不重叠。
* **行锁**：``EditLine.locked`` 为真时跳过 R1–R6，只在报告里提示。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ttml_model import LAYER_BG, LAYER_MAIN, LAYER_TRANSLATION, EditDoc, EditLine, EditWord

# 与 matcher.LyricMatcher 的默认值保持一致（勿单方面改动）
MIN_UNIT_DURATION = 0.06
MAX_UNIT_DURATION = 3.5

# 与 ttml_check.py 的告警阈值保持一致（检测端用 0.08 / 2.5 / 1.5）
CHECK_MIN_DURATION = 0.08
CHECK_MAX_DURATION = 2.5
CHECK_MAX_PAUSE = 1.5


# ==========================================================================
# 三个纯函数：从 matcher 原样抽取，生成链路与编辑链路共用
# ==========================================================================
def normalize_group(words: Sequence, lo: float, hi: float,
                    min_dur: float = MIN_UNIT_DURATION,
                    max_dur: float = MAX_UNIT_DURATION) -> None:
    """同一层内部：**按文本顺序**摆放时间，消除重叠并收敛到窗口。

    注意：必须按文本顺序而不是按时间排序 —— 输出时 span 是按文本顺序写的，
    如果时间顺序和文本顺序不一致，播放时字就会跳来跳去。
    ASR 的词级时间戳之间常有几十毫秒的重叠，这里统一用前向游标推开。

    ``words`` 里必须有 ``start`` / ``end`` / ``index``（``Unit`` 和 ``EditWord`` 都满足）。
    """
    if not words:
        return
    timed = [u for u in words if u.start is not None and u.end is not None]
    if not timed:
        return
    timed.sort(key=lambda u: u.index)

    for u in timed:
        if u.end <= u.start:
            u.end = u.start + min_dur
        if u.end - u.start > max_dur:
            u.end = u.start + max_dur

    cursor: Optional[float] = None
    for u in timed:
        if cursor is not None and u.start < cursor:
            u.start = cursor
        if u.end < u.start + min_dur:
            u.end = u.start + min_dur
        cursor = u.end

    if hi > lo:
        if timed[0].start < lo:
            shift = lo - timed[0].start
            for u in timed:
                u.start += shift
                u.end += shift
        if timed[-1].end > hi:
            total = timed[-1].end - timed[0].start
            avail = max(hi - timed[0].start, min_dur * len(timed))
            if total > 0 and avail < total:
                scale = avail / total
                t = timed[0].start
                for u in timed:
                    d = max(min_dur, (u.end - u.start) * scale)
                    u.start = t
                    u.end = t + d
                    t = u.end

    for u in timed:
        u.start = round(u.start, 3)
        u.end = round(u.end, 3)


def overlap_pair(units_a: Sequence, units_b: Sequence, a_begin: float,
                 min_dur: float = MIN_UNIT_DURATION,
                 main_only: bool = True) -> Optional[float]:
    """``units_a`` 所在行与 ``units_b`` 所在行重叠时，返回收紧后的 ``a_end``。

    收紧不了（没有可用时间戳）就返回 ``None``。

    ``main_only``（默认）**只拿主唱层比**：背景和声（x-bg）常常一直唱到下一句
    开始之后，那是真实的混音重叠 —— AMLL 规范明确豁免，``ttml_check`` 也只比
    主唱层。若把和声算进来的话，生成出来的文件一打开就会被自己"修"一次，
    两条链路的结果就对不上了。

    ⚠️ 收紧时**每个字至少保留 min_dur**，否则多个字会被压成同一个结束时间
    → 同一层字时间重叠。
    """
    def pick(units, key):
        main = [u for u in units if not bool(getattr(u, "is_bg", False)) and getattr(u, key) is not None]
        allu = [u for u in units if getattr(u, key) is not None]
        return main if (main_only and main) else allu

    ua = pick(units_a, "end")
    ub = pick(units_b, "start")
    if not ua or not ub:
        return None
    a_end = max(u.end for u in ua)
    b_start = min(u.start for u in ub)
    if b_start >= a_end:
        return None
    new_a_end = max(a_begin + 0.05, b_start - 0.01)
    for u in ua:
        if u.end > new_a_end:
            u.end = max(new_a_end, (u.start or a_begin) + min_dur)
    return new_a_end


def clamp_units_into_line(units: Sequence, begin: float, end: float,
                          min_dur: float = MIN_UNIT_DURATION,
                          layered: bool = False):
    """最后一道保险：任何字都落在行的 ``begin/end`` 之内，且按文本顺序单调。

    返回修正后的 ``(begin, end)``。

    处理顺序（每层单独做：main / bg）：

    1. 前向游标推开 —— 保证层内按文本顺序单调、不重叠；
    2. 整组超出了行尾，但**行区间装得下整组** → 把整组往回推（这就是规范说的
       R3「夹紧到行区间，必要时回推 start」）；
    3. 行区间**装不下整组** → 让行尾往后长到能包住（TTML1 §10.4：span ⊆ p）。

    ⚠️ 绝不能把几个字压进同一个 ``[end-min_dur, end]`` 窗口 ——
    早先的实现就是"压窗口"，结果同一层出现多个 span 时间完全相同，
    校验器报"字时间重叠"、播放器里字会闪。
    """
    if end <= begin:
        end = begin + 0.3
    for is_bg in (False, True):
        group = [u for u in units
                 if bool(getattr(u, "is_bg", False)) == is_bg
                 and u.start is not None and u.end is not None]
        if not group:
            continue
        group.sort(key=lambda u: u.index)

        # 分层歌词：x-bg 允许超出主唱 <p>，不要把它夹回行内
        if layered and is_bg:
            continue

        cursor = begin
        for u in group:
            if u.start < cursor:
                u.start = cursor
            if u.end <= u.start:
                u.end = u.start + min_dur
            cursor = u.end

        overflow = group[-1].end - end
        if overflow > 1e-9:
            spare = max(0.0, group[0].start - begin)
            shift = -min(overflow, spare)
            if shift:
                for u in group:
                    u.start += shift
                    u.end += shift
            if group[-1].end > end + 1e-9:
                end = group[-1].end        # 行装不下 → 行尾长出去，而不是压扁字

        for u in group:
            u.start = round(u.start, 3)
            u.end = round(u.end, 3)
    return begin, end


# ==========================================================================
# 编辑链路：EditDoc 级规范化 + 改动清单
# ==========================================================================
@dataclass
class Fix:
    line_key: str
    word_index: int          # -1 表示行级修复
    word_text: str
    rule: str                # R1..R7
    old_begin: float
    old_end: float
    new_begin: float
    new_end: float

    def describe(self) -> str:
        if self.word_index < 0:
            return (f"{self.line_key} [{self.rule}] 行时间 "
                    f"{self.old_begin:.3f}~{self.old_end:.3f} → "
                    f"{self.new_begin:.3f}~{self.new_end:.3f}")
        return (f"{self.line_key} [{self.rule}] 第{self.word_index + 1}字"
                f"「{self.word_text}」 {self.old_begin:.3f}~{self.old_end:.3f} → "
                f"{self.new_begin:.3f}~{self.new_end:.3f}")

    def to_dict(self) -> dict:
        return {
            "line_key": self.line_key, "word_index": self.word_index,
            "word_text": self.word_text, "rule": self.rule,
            "old_begin": self.old_begin, "old_end": self.old_end,
            "new_begin": self.new_begin, "new_end": self.new_end,
            "describe": self.describe(),
        }


@dataclass
class NormalizeReport:
    fixes: List[Fix] = field(default_factory=list)
    unresolved: List[Fix] = field(default_factory=list)   # 修不了，交人工
    policy: str = "auto"                                  # auto / highlight / off
    locked_lines: List[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.fixes)

    def summary(self) -> str:
        if self.policy == "off":
            return "时间轴规范化：已关闭（只校验不修复）"
        if not self.fixes and not self.unresolved:
            return "时间轴规范化：无需修复"
        parts = []
        if self.fixes:
            by = {}
            for f in self.fixes:
                by[f.rule] = by.get(f.rule, 0) + 1
            parts.append("已自动修复 %d 处（%s）"
                         % (len(self.fixes),
                            "，".join(f"{k}×{v}" for k, v in sorted(by.items()))))
        if self.unresolved:
            parts.append("另有 %d 处需人工确认" % len(self.unresolved))
        if self.locked_lines:
            parts.append("跳过了 %d 个锁定行" % len(self.locked_lines))
        return "时间轴规范化：" + "；".join(parts)

    def describe(self, limit: int = 12) -> List[str]:
        out = [f.describe() for f in self.fixes[:limit]]
        if len(self.fixes) > limit:
            out.append(f"… 另有 {len(self.fixes) - limit} 处")
        return out

    def to_dict(self) -> dict:
        return {
            "policy": self.policy,
            "summary": self.summary(),
            "fixes": [f.to_dict() for f in self.fixes],
            "unresolved": [f.to_dict() for f in self.unresolved],
            "locked_lines": list(self.locked_lines),
        }


def _classify_before(line: EditLine, i: int, vals: List[tuple],
                     line_begin: float, line_end: float) -> str:
    """按**修复前**的状态判断这个字/行违反了什么规则（用于给 Fix 打标签）"""
    if i < 0:
        return "R4"
    b0, b1 = vals[i]
    rules = []
    if b1 <= b0:
        rules.append("R1")
    layer = line.words[i].layer
    prev = None
    for j in range(i - 1, -1, -1):
        if line.words[j].layer == layer:
            prev = j
            break
    if prev is not None and vals[prev][1] > b0 + 1e-6:
        rules.append("R2")
    if b0 < line_begin - 1e-6 or b1 > line_end + 1e-6:
        rules.append("R3")
    d = b1 - b0
    if d < CHECK_MIN_DURATION:
        rules.append("R5")
    elif d > CHECK_MAX_DURATION:
        rules.append("R6")
    return rules[0] if rules else "R2"


# ---------------------------------------------------------------------------
# 重叠证据（sidecar v2）—— **可选**：只有显式传入 overlap_evidence 时才可能跳过 R4
# ---------------------------------------------------------------------------
EVIDENCE_SCHEMA_VERSION = 2
EVIDENCE_ALGORITHM_VERSION = 2
EVIDENCE_CONTAIN_TOL = 0.001
EVIDENCE_REQUIRED_FIELDS = (
    "prev_key", "next_key", "agent", "agent_next", "prev_end", "next_begin",
    "next_median_char", "covered_chars", "cover_ratio", "threshold_policy",
    "last_word", "word_order", "layer_structure", "locked_prev", "locked_next",
    "algorithm_version",
)


def overlap_evidence_fingerprint(doc: EditDoc) -> str:
    """证据绑定的内容指纹：行序 + key + agent + 行 begin/end + 每字 (text,begin,end,is_bg,layer)。

    **不含** locked / policy / write_options —— 它们分别单独校验（见 check_overlap_evidence）。
    """
    import hashlib
    import json

    rows = []
    for ln in doc.lines:
        rows.append([ln.key, ln.agent or "", round(float(ln.begin), 3), round(float(ln.end), 3),
                     [[w.text, round(float(w.begin), 3), round(float(w.end), 3),
                       bool(getattr(w, "is_bg", False)), w.layer] for w in ln.words]])
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def layer_structure_of(line: EditLine) -> dict:
    """一行的层结构计数：{main, bg, translation}。"""
    out = {"main": 0, "bg": 0, "translation": 0}
    for w in line.words:
        if bool(getattr(w, "is_bg", False)):
            out["bg"] += 1
        elif w.layer == LAYER_TRANSLATION:
            out["translation"] += 1
        else:
            out["main"] += 1
    return out


def _words_inside_line(line: EditLine):
    issues = []
    for w in line.words:
        if w.begin < line.begin - EVIDENCE_CONTAIN_TOL:
            issues.append({"word": w.text, "side": "begin", "delta": round(w.begin - line.begin, 3)})
        if w.end > line.end + EVIDENCE_CONTAIN_TOL:
            issues.append({"word": w.text, "side": "end", "delta": round(w.end - line.end, 3)})
    return (not issues), issues


def check_overlap_evidence(doc: EditDoc, overlap_evidence, *, policy_version: int = 1,
                           identity=None) -> List[dict]:
    """逐边判定是否允许跳过 R4（**默认保守：任何检查失败都回退默认 R4**）。

    返回 ``[{prev_key, next_key, edge_index, allowed, reason}]``。

    校验顺序（文档级 → 边级）：
    1. 证据结构 / schema / algorithm / 默认策略版本；
    2. ``doc.fingerprint`` 与当前文档一致（文档级不匹配 ⇒ **全部边**拒绝）；
    3. ``identity``（调用方校验；为 ``None`` 时记 ``identity_unchecked``，不阻断）；
    4. 每条边：必需字段、行定位（按 ``prev_key``/``next_key``，**不用下标**）、agent、
       ``prev_end``/``next_begin``、末字、字序、层结构、locked 状态、覆盖比与阈值；
    5. **字级包含**（准入条件，不是修复规则）：该行所有字必须落在父级 ``begin/end`` 内。
    """
    decisions: List[dict] = []

    def reject_all(reason: str):
        decisions.append({"prev_key": None, "next_key": None, "edge_index": None,
                          "allowed": False, "reason": reason, "scope": "doc"})
        return decisions

    if not isinstance(overlap_evidence, dict):
        return reject_all("evidence_unparsable")
    if overlap_evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        return reject_all("schema_version_incompatible")
    if overlap_evidence.get("algorithm_version") != EVIDENCE_ALGORITHM_VERSION:
        return reject_all("algorithm_version_incompatible")
    if overlap_evidence.get("default_policy_version") != policy_version:
        return reject_all("policy_version_incompatible")
    meta = overlap_evidence.get("doc")
    if not isinstance(meta, dict):
        return reject_all("doc_field_missing")
    if meta.get("fingerprint") != overlap_evidence_fingerprint(doc):
        return reject_all("content_fingerprint_mismatch")
    identity_note = "identity_unchecked"
    if identity is not None and isinstance(meta.get("identity"), dict):
        if meta["identity"].get("id") != identity:
            return reject_all("doc_identity_mismatch")
        identity_note = "identity_ok"
    edges = overlap_evidence.get("edges")
    if not isinstance(edges, list):
        return reject_all("edges_field_invalid")

    index_by_keys = {}
    for i in range(len(doc.lines) - 1):
        index_by_keys[(doc.lines[i].key, doc.lines[i + 1].key)] = i

    for edge in edges:
        rec = {"prev_key": None, "next_key": None, "edge_index": None,
               "allowed": False, "reason": "invalid", "scope": "edge", "identity": identity_note}
        if not isinstance(edge, dict):
            rec["reason"] = "edge_unparsable"
            decisions.append(rec)
            continue
        rec["prev_key"] = edge.get("prev_key")
        rec["next_key"] = edge.get("next_key")
        missing = [f for f in EVIDENCE_REQUIRED_FIELDS if f not in edge]
        if missing:
            rec["reason"] = "edge_field_missing:" + ",".join(missing[:3])
            decisions.append(rec)
            continue
        idx = index_by_keys.get((edge.get("prev_key"), edge.get("next_key")))
        if idx is None:
            rec["reason"] = "next_line_changed"
            decisions.append(rec)
            continue
        rec["edge_index"] = idx
        a, b = doc.lines[idx], doc.lines[idx + 1]
        if (a.agent, b.agent) != (edge.get("agent"), edge.get("agent_next")):
            rec["reason"] = "agent_changed"
            decisions.append(rec)
            continue
        try:
            if abs(float(a.end) - float(edge["prev_end"])) > EVIDENCE_CONTAIN_TOL:
                rec["reason"] = "prev_end_changed"
                decisions.append(rec)
                continue
            if abs(float(b.begin) - float(edge["next_begin"])) > EVIDENCE_CONTAIN_TOL:
                rec["reason"] = "next_begin_changed"
                decisions.append(rec)
                continue
        except (TypeError, ValueError):
            rec["reason"] = "invalid_numeric"
            decisions.append(rec)
            continue
        last = edge.get("last_word")
        if not isinstance(last, dict) or not a.words or a.words[-1].text != last.get("text"):
            rec["reason"] = "last_word_changed"
            decisions.append(rec)
            continue
        try:
            if abs(float(a.words[-1].end) - float(last.get("end"))) > EVIDENCE_CONTAIN_TOL:
                rec["reason"] = "last_word_changed"
                decisions.append(rec)
                continue
        except (TypeError, ValueError):
            rec["reason"] = "invalid_numeric"
            decisions.append(rec)
            continue
        if [w.text for w in a.words] != edge.get("word_order"):
            rec["reason"] = "word_order_changed"
            decisions.append(rec)
            continue
        if layer_structure_of(a) != edge.get("layer_structure"):
            rec["reason"] = "layer_structure_changed"
            decisions.append(rec)
            continue
        if (bool(a.locked), bool(b.locked)) != (bool(edge.get("locked_prev")), bool(edge.get("locked_next"))):
            rec["reason"] = "locked_state_changed"
            decisions.append(rec)
            continue
        policy = edge.get("threshold_policy")
        if not isinstance(policy, dict) or not isinstance(policy.get("max_cover_ratio"), (int, float)):
            rec["reason"] = "threshold_policy_invalid"
            decisions.append(rec)
            continue
        ratio = edge.get("cover_ratio")
        if ratio is None or edge.get("next_median_char") is None:
            rec["reason"] = "cover_ratio_uncomputable"
            decisions.append(rec)
            continue
        try:
            if float(ratio) > float(policy["max_cover_ratio"]):
                rec["reason"] = "cover_ratio_exceeds_policy"
                decisions.append(rec)
                continue
        except (TypeError, ValueError):
            rec["reason"] = "invalid_numeric"
            decisions.append(rec)
            continue
        contained, issues = _words_inside_line(a)
        if not contained:
            rec["reason"] = "word_outside_parent"
            rec["containment_issues"] = issues
            decisions.append(rec)
            continue
        rec["allowed"] = True
        rec["reason"] = "valid"
        decisions.append(rec)
    return decisions


def normalize_timeline(doc: EditDoc, policy: str = "auto", *,
                       layered: bool = False,
                       overlap_evidence=None, evidence_policy_version: int = 1,
                       evidence_identity=None) -> NormalizeReport:
    """规范化一份 ``EditDoc``，返回改动清单。

    ``policy``：

    * ``auto``      —— **就地修改** ``doc``，并返回 ``fixes``（默认）
    * ``highlight`` —— **不改** ``doc``，只在返回值里列出冲突/将要做的修复
    * ``off``       —— **不改** ``doc``，也不计算修复（校验交给 ``check_ttml``）

    ``overlap_evidence``（可选，默认 ``None`` —— **必须保持默认行为零变化**）：

    只在需要"保住有证据的跨行延长"时传入 sidecar v2 证据。三条约束：

    1. ``None`` ⇒ 与以前**完全一致**（R4 照旧收紧，不进入任何新分支）；
    2. 文档级不匹配（schema/算法/策略版本、内容指纹、身份）⇒ **全部边**回退默认 R4；
    3. 单边失败 ⇒ **只该边**回退；**字级包含是准入条件**（字级越出父级 ⇒ 拒绝保护，
       否则后续 R5′ 仍会压缩字级，造成"报告保护成功但长尾被吃"）。

    注意：**同一层的字**按文本顺序处理（``main`` / ``bg`` / ``translation`` 各自独立），
    所以和声层与主唱层时间重叠**不会**被压平 —— 这是 AMLL 规范允许的
    「因混音而使时间重叠的情况除外」。
    """
    if policy not in ("auto", "highlight", "off"):
        raise ValueError("policy 必须是 auto / highlight / off")
    report = NormalizeReport(policy=policy)

    # highlight / off 都在副本上算，保证调用方拿到的 doc 不被改动
    work = doc if policy == "auto" else copy.deepcopy(doc)

    def snapshot(d: EditDoc):
        snap = {}
        for ln in d.lines:
            snap[ln.key] = (ln.begin, ln.end, [(w.begin, w.end) for w in ln.words])
        return snap

    before = snapshot(work)

    if policy != "off":
        # ---------- 层内：R1 / R2 / R5 / R6（就是生成链路的 _normalize_group）----------
        for line in work.lines:
            if line.locked:
                if line.key not in report.locked_lines:
                    report.locked_lines.append(line.key)
                continue
            for is_bg in (False, True):
                group = [w for w in line.words
                         if bool(w.is_bg) == is_bg and w.layer != LAYER_TRANSLATION]
                normalize_group(group, line.begin, line.end)
            tr = [w for w in line.words if w.layer == LAYER_TRANSLATION]
            if tr:
                normalize_group(tr, line.begin, line.end)

        # ---------- 行间：R4 同 agent 相邻行不重叠 ----------
        # 分层歌词（layered）：同 agent 行允许重叠，不执行 R4。
        # 仅当显式传入 overlap_evidence 时，才可能跳过**通过全部校验**的边；
        # overlap_evidence=None ⇒ 走原有逻辑，一行不改（默认行为零变化的保证）。
        protected_edges = set()
        if overlap_evidence is not None:
            for _dec in check_overlap_evidence(work, overlap_evidence,
                                               policy_version=evidence_policy_version,
                                               identity=evidence_identity):
                if _dec.get("allowed") and _dec.get("edge_index") is not None:
                    protected_edges.add(int(_dec["edge_index"]))
        for _edge_index, (a, b) in enumerate(zip(work.lines, work.lines[1:])):
            if layered:
                break
            if a.agent != b.agent or a.locked or b.locked:
                continue
            if _edge_index in protected_edges:
                continue
            new_end = overlap_pair(a.words, b.words, a.begin)
            if new_end is not None:
                a.end = min(a.end, new_end)

        # ---------- 行内：R3 字必须落在行区间内 ----------
        for line in work.lines:
            if line.locked:
                continue
            line.begin, line.end = clamp_units_into_line(line.words, line.begin, line.end,
                                                          layered=layered)

    # ---------- R7：行内长停顿（不自动修，只提示）----------
    for line in work.lines:
        for is_bg in (False, True):
            group = sorted((w for w in line.words if bool(w.is_bg) == is_bg),
                           key=lambda w: w.index)
            for x, y in zip(group, group[1:]):
                if y.begin - x.end > CHECK_MAX_PAUSE:
                    report.unresolved.append(Fix(
                        line.key, y.index, y.text, "R7",
                        y.begin, y.end, y.begin, y.end))
                    break   # 一行只报一次

    # ---------- R6 的"灰色地带"：超过校验阈值(2.5s)但没到硬上限(3.5s)不自动截 ----------
    # 这类字多半是真实长音或插值产物，悄悄截短会让高亮提前结束，宁可交人工。
    for line in work.lines:
        for w in line.words:
            d = w.end - w.begin
            if CHECK_MAX_DURATION < d <= MAX_UNIT_DURATION:
                report.unresolved.append(Fix(
                    line.key, w.index, w.text, "R6", w.begin, w.end, w.begin, w.end))

    # ---------- 汇总改动（on_call 时对比修复前后；highlight 只报告不落回）----------
    if policy in ("auto", "highlight"):
        for line in work.lines:
            ob, oe, ovals = before.get(line.key, (line.begin, line.end, []))
            if abs(ob - line.begin) > 5e-4 or abs(oe - line.end) > 5e-4:
                report.fixes.append(Fix(line.key, -1, "", "R4", ob, oe,
                                        line.begin, line.end))
            for i, w in enumerate(line.words):
                if i >= len(ovals):
                    break
                b0, b1 = ovals[i]
                if abs(b0 - w.begin) < 5e-4 and abs(b1 - w.end) < 5e-4:
                    continue
                report.fixes.append(Fix(
                    line.key, i, w.text,
                    _classify_before(line, i, ovals, ob, oe),
                    b0, b1, w.begin, w.end))

    return report


