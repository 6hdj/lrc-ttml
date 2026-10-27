"""编辑台后端逻辑（P1）。

设计原则：**这里全是纯函数**，不碰 HTTP，方便离线自测；
`web_server.py` 只做"取参 → 调这里 → 返回 JSON"。

分工（见 docs/竞品调研与优化方案.md §5.5）：
* ``load_doc``     读取编辑态：内存 → ``outputs/<id>.edit.json`` → 由 TTML 重建
* ``validate``     只算不写（供前端实时预览，前端节流 ≥300ms）
* ``apply_doc``    规范化 + 校验 + 原子落盘，并把最新 TTML 回给调用方

⚠️ **微调阶段绝不重跑 ASR**：这里全部是毫秒级操作。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any, Dict, Optional, Tuple

from ttml_check import check_ttml
from ttml_model import LAYER_BG, LAYER_MAIN, EditDoc
from ttml_normalize import normalize_timeline
from ttml_reader import TTMLParseError, doc_to_aligned, doc_to_ttml, parse_ttml

# 结构化错误契约（前端按这四个字段定位问题）
ERROR_FIELDS = ("error", "where", "detail", "hint")


class EditError(Exception):
    """带定位信息的错误：{error, where, detail, hint}"""

    def __init__(self, error: str, where: str = "", detail: str = "",
                 hint: str = "", code: int = 400):
        super().__init__(error)
        self.error = error
        self.where = where
        self.detail = detail
        self.hint = hint
        self.code = code

    def to_dict(self) -> dict:
        return {"error": self.error, "where": self.where,
                "detail": self.detail, "hint": self.hint, "code": self.code}


# --------------------------------------------------------------------- 读写
def atomic_write_text(path: str, text: str) -> None:
    """先写同目录临时文件再 os.replace —— 中途崩溃不会留下半截文件"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def doc_from_ttml(text: str) -> EditDoc:
    try:
        return parse_ttml(text)
    except TTMLParseError as e:
        raise EditError("TTML 解析失败", detail=str(e),
                        hint="确认文件是 UTF-8 的 TTML；也可以先跑 "
                             "python main.py --check-only <文件> 看结构问题") from e


def backfill_align_hints(doc: EditDoc, aligned) -> int:
    """把对齐报告里的 ``matched`` / ``prob`` / ``muted`` 补回编辑态。

    为什么需要
    ----------
    编辑台的颜色提示（红=插值补的字、黄=ASR 可信度低）读的是
    ``EditWord.matched`` / ``EditWord.prob``。但这两个字段**不存在于 TTML 文本里**，
    而 ``parse_ttml`` 只能写死 ``matched=True``、``prob=0``（ttml_reader.py 的
    ``_fill_words`` / ``_walk_collect``）。于是"从 TTML 文本重建编辑态"这条路
    必然把它们丢光 —— 界面上的红/黄**永远不会出现**，而"哪个字不可信"恰恰是
    微调阶段最该看到的信息。

    修法：TTML 文本仍然用来建文档骨架（它带着元数据、翻译、源文件写法），
    但**对齐信息从内存里的 ``result.report.lines`` 按层回填**，不再走文本往返。

    匹配依据（已在 16 份真实产物上验证 0 失配）：
    ``doc_to_aligned`` 会跳过翻译层、其余字**按文档顺序**逐一映射，
    且写器不会重排字序，所以"同层的第 i 个字"在两边一一对应。
    计数不一致时**保守跳过该行**（宁可不标颜色，也不能把错位的 prob 贴到别的字上）。

    返回成功回填的行数。
    """
    if not doc.lines or not aligned:
        return 0

    def layer_of_doc_word(w) -> str:
        return LAYER_BG if (w.layer == LAYER_BG or w.is_bg) else LAYER_MAIN

    def layer_of_unit(u) -> str:
        return LAYER_BG if u.is_bg else LAYER_MAIN

    filled = 0
    for line, al in zip(doc.lines, aligned):
        for want in (LAYER_MAIN, LAYER_BG):
            rw = [w for w in line.words if layer_of_doc_word(w) == want]
            lw = [u for u in al.units if layer_of_unit(u) == want]
            if len(rw) != len(lw):
                continue
            for dst, src in zip(rw, lw):
                dst.matched = bool(getattr(src, "matched", True))
                dst.prob = float(getattr(src, "prob", 0.0) or 0.0)
                dst.muted = bool(getattr(src, "muted", False))
        filled += 1
    return filled


def doc_from_dict(data: Any) -> EditDoc:
    if not isinstance(data, dict) or "lines" not in data:
        raise EditError("doc 结构不对", detail=f"收到 {type(data).__name__}",
                        hint="doc 必须是 EditDoc.to_dict() 的产物（含 lines 数组）")
    try:
        return EditDoc.from_dict(data)
    except Exception as e:
        raise EditError("doc 反序列化失败", detail=f"{type(e).__name__}: {e}",
                        hint="检查每个 word 的 start/end 是否为数字") from e


def load_doc(ttml_text: Optional[str], edit_path: Optional[str]
             ) -> Tuple[EditDoc, str]:
    """返回 (doc, source)；source ∈ memory/disk/draft。

    * disk：``outputs/<id>.edit.json`` 存在且能读 → 说明用户改过，优先用它的进度；
    * draft：文件损坏或不存在 → 由 TTML 重建（**不能 500**）；
    * memory：调用方已持有 doc（本函数只做磁盘与重建）。
    """
    if edit_path and os.path.exists(edit_path):
        try:
            with open(edit_path, encoding="utf-8") as f:
                data = json.load(f)
            return doc_from_dict(data.get("doc", data)), "disk"
        except EditError:
            pass                      # 损坏 → 降级重建，不抛
        except (OSError, ValueError):
            pass
    if not ttml_text:
        raise EditError("没有可编辑的内容", code=404,
                        hint="先生成一次 TTML，再进入编辑台")
    return doc_from_ttml(ttml_text), "draft"


# ------------------------------------------------------- 重叠证据（7C.2-B）
_EDIT_SUFFIX = ".edit.json"
_EVIDENCE_POLICY_VERSION = 1


def edit_task_id(edit_path: Optional[str]) -> str:
    """从 ``outputs/<task_id>.edit.json`` 反解 task_id。

    web_server 不需要改动：``apply_doc`` 已经拿到 edit_path，身份由它推导。
    """
    if not edit_path:
        return ""
    base = os.path.basename(str(edit_path))
    if base.endswith(_EDIT_SUFFIX):
        return base[: -len(_EDIT_SUFFIX)]
    return ""


def ttml_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def read_evidence(edit_path: Optional[str]):
    """读 ``.edit.json`` 里的 ``overlap_evidence``。返回 ``(evidence|None, reason)``。

    旧文件没有该字段 ⇒ ``no_overlap_evidence_field``（按无证据处理，默认 R4）。
    """
    if not edit_path or not os.path.exists(edit_path):
        return None, "edit_json_absent"
    try:
        with open(edit_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, "edit_json_unparsable"
    if not isinstance(data, dict):
        return None, "edit_json_unparsable"
    ev = data.get("overlap_evidence")
    if ev is None:
        return None, "no_overlap_evidence_field"
    if not isinstance(ev, dict):
        return None, "evidence_unparsable"
    return ev, "ok"


# 生成侧对"跨行重叠"的**时间包络**（与 tail_extend.cross_agent_max 默认值一致）。
# 证据策略必须用它表达，而不是写死一个字符比 —— 否则"1.5 s 的重叠 + 0.4 s 的中位字长"
# 会被算成 ratio=3.75 而被误拒（真实 63 行产物实测：21 条边里 16 条被误拒、长尾丢 1.51 s）。
OVERLAP_MAX_SECONDS = 1.5


def _edge_from_doc(doc: EditDoc, index: int) -> dict:
    """按当前文档重建一条边的证据记录（保存后重新绑定用）。"""
    import ttml_normalize as _N

    a, b = doc.lines[index], doc.lines[index + 1]
    durs = sorted(w.end - w.begin for w in b.words if w.end > w.begin)
    med = durs[len(durs) // 2] if durs else None
    # 策略与生成侧一致：允许的重叠上限是**秒**（OVERLAP_MAX_SECONDS）；
    # 换算成 cover_ratio 上限 = 1.5 / 中位字长，这样 ratio 判定等价于时间判定。
    max_ratio = round(OVERLAP_MAX_SECONDS / med, 3) if med else 1.0
    return {
        "prev_key": a.key, "next_key": b.key, "agent": a.agent, "agent_next": b.agent,
        "prev_end": round(a.end, 3), "next_begin": round(b.begin, 3),
        "overlap_s": round(a.end - b.begin, 3),
        "next_median_char": None if med is None else round(med, 3),
        "covered_chars": sum(1 for w in b.words if w.begin < a.end - 1e-6),
        "cover_ratio": None if not med else round((a.end - b.begin) / med, 3),
        "threshold_policy": {"max_cover_ratio": max_ratio, "max_overlap_s": OVERLAP_MAX_SECONDS},
        "last_word": {"text": a.words[-1].text, "begin": a.words[-1].begin, "end": a.words[-1].end},
        "word_order": [w.text for w in a.words],
        "layer_structure": _N.layer_structure_of(a),
        "locked_prev": bool(a.locked), "locked_next": bool(b.locked),
        "algorithm_version": _N.EVIDENCE_ALGORITHM_VERSION,
    }


def edge_record_from_doc(doc: EditDoc, index: int) -> dict:
    """公开入口：按文档构造一条边证据记录（生成链路与编辑链路共用，单一真源）。"""
    return _edge_from_doc(doc, index)


SIDECAR_SUFFIX = ".overlap.json"


def read_sidecar_evidence(edit_path: Optional[str]):
    """读生成链路写的 ``<task_id>.overlap.json``（拖音证据 sidecar）。

    返回 ``(evidence|None, reason)``；**不做信任判断**（指纹/身份/TTML SHA 由
    :func:`evaluate_evidence` 统一校验，失败关闭）。
    """
    stem = edit_task_id(edit_path)
    if not stem or not edit_path:
        return None, "sidecar_absent"
    side = os.path.join(os.path.dirname(os.path.abspath(edit_path)), stem + SIDECAR_SUFFIX)
    if not os.path.exists(side):
        return None, "sidecar_absent"
    try:
        with open(side, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, "sidecar_unparsable"
    if not isinstance(data, dict) or not isinstance(data.get("edges"), list):
        return None, "sidecar_unparsable"
    return data, "adopted_from_sidecar"


def _allowed_overlaps_for_check(doc: EditDoc, allowed_indices) -> list:
    """把**已经判定允许**的边转成 ``check_ttml`` 的门控列表（只用于格式自检）。"""
    import ttml_model as _m

    out = []
    for i in allowed_indices or ():
        if i + 1 >= len(doc.lines):
            continue
        a, b = doc.lines[i], doc.lines[i + 1]
        main_end = max((w.end for w in a.words
                        if not getattr(w, "is_bg", False) and w.layer != _m.LAYER_TRANSLATION),
                       default=None)
        if main_end is None:
            continue
        out.append({"next_line": i + 2, "agent": a.agent or "",
                    "prev_end": round(float(main_end), 3),
                    "next_begin": round(float(b.begin), 3)})
    return out


def evaluate_evidence(doc: EditDoc, evidence, *, edit_path: Optional[str] = None,
                      task_id: Optional[str] = None, ttml_text_on_disk: Optional[str] = None,
                      ttml_present: bool = False, sidecar_adopted: bool = False,
                      policy_version: int = _EVIDENCE_POLICY_VERSION) -> dict:
    """**预览与保存共用**的证据判定（失败关闭）。

    校验顺序：身份 → 持久化指纹 → TTML SHA256 → 逐边（含 locked 可验证性）。
    返回 ``{task_id, usable, doc_level_reason, decisions, allowed_indices}``。
    """
    import ttml_normalize as _N

    tid = str(task_id or edit_task_id(edit_path) or "")
    out = {"task_id": tid, "usable": False, "doc_level_reason": None,
           "decisions": [], "allowed_indices": []}
    if evidence is None:
        out["doc_level_reason"] = "evidence_absent"
        return out
    if not isinstance(evidence, dict):
        out["doc_level_reason"] = "evidence_unparsable"
        return out
    meta = evidence.get("doc") if isinstance(evidence.get("doc"), dict) else None
    ident = meta.get("identity") if isinstance((meta or {}).get("identity"), dict) else None
    if not tid:
        out["doc_level_reason"] = "task_id_missing"
        return out
    if not ident or not ident.get("task_id"):
        out["doc_level_reason"] = "identity_missing"
        return out
    if str(ident.get("task_id")) != tid:
        out["doc_level_reason"] = "doc_identity_mismatch"
        return out
    if (meta or {}).get("fingerprint_persisted") != _N.overlap_evidence_fingerprint(doc):
        # 指纹不符有两种可能，必须区分（否则编辑一次就整份失效，违背"逐边失效"）：
        #   a) 磁盘上的 persisted 文档 == 当前 doc ⇒ doc 没被编辑 ⇒ 证据/文件被外部篡改 ⇒ 整份失效；
        #   b) 二者不同 ⇒ doc 在内存里被编辑过 ⇒ 交给逐边校验，只失效受影响的边。
        persisted_doc = None
        if edit_path and os.path.exists(edit_path):
            try:
                with open(edit_path, encoding="utf-8") as f:
                    payload = json.load(f)
                persisted_doc = doc_from_dict(payload.get("doc", payload))
            except (OSError, ValueError, EditError):
                persisted_doc = None
        if persisted_doc is not None and \
                _N.overlap_evidence_fingerprint(persisted_doc) == _N.overlap_evidence_fingerprint(doc):
            out["doc_level_reason"] = "fingerprint_mismatch"
            return out
        out["doc_level_reason"] = None       # 视为内存编辑，继续逐边校验
    if not ttml_present:
        out["doc_level_reason"] = "ttml_unavailable"
        return out
    bound_sha = (meta or {}).get("ttml_sha256")
    if not bound_sha:
        out["doc_level_reason"] = "ttml_sha256_missing"
        return out
    if ttml_sha256(ttml_text_on_disk or "") != str(bound_sha):
        out["doc_level_reason"] = "ttml_out_of_sync"
        return out

    locked_verifiable = bool(edit_path and os.path.exists(edit_path))
    if not locked_verifiable and sidecar_adopted:
        # 生成侧 sidecar 的场景：编辑尚未开始（没有 .edit.json），生成链路从不锁行；
        # 只有每条边的 locked 明确为 False 才视为**可验证**，否则按不可验证拒绝。
        locked_verifiable = all(
            (not e.get("locked_prev")) and (not e.get("locked_next"))
            for e in (evidence.get("edges") or []) if isinstance(e, dict))
    candidates, pre_reject = [], []
    for e in (evidence.get("edges") or []):
        if not isinstance(e, dict):
            pre_reject.append({"prev_key": None, "next_key": None, "edge_index": None,
                               "allowed": False, "reason": "edge_unparsable", "scope": "edge",
                               "fell_back_to_default_R4": True})
            continue
        if not locked_verifiable and ("locked_prev" in e or "locked_next" in e):
            pre_reject.append({"prev_key": e.get("prev_key"), "next_key": e.get("next_key"),
                               "edge_index": None, "allowed": False,
                               "reason": "locked_unverifiable", "scope": "edge",
                               "fell_back_to_default_R4": True})
            continue
        candidates.append(e)
    probe = {
        "schema_version": evidence.get("schema_version"),
        "algorithm_version": evidence.get("algorithm_version"),
        "default_policy_version": evidence.get("default_policy_version"),
        "doc": {"fingerprint": _N.overlap_evidence_fingerprint(doc), "identity": {"id": tid}},
        "edges": candidates,
    }
    decisions = _N.check_overlap_evidence(doc, probe, policy_version=policy_version, identity=tid)
    if decisions and decisions[0].get("scope") == "doc":
        out["doc_level_reason"] = decisions[0].get("reason")
        out["decisions"] = decisions + pre_reject
        return out
    merged = []
    for d in decisions:
        d = dict(d)
        d.setdefault("fell_back_to_default_R4", not d.get("allowed"))
        merged.append(d)
    merged.extend(pre_reject)
    out["decisions"] = merged
    out["allowed_indices"] = sorted({int(d["edge_index"]) for d in merged
                                     if d.get("allowed") and d.get("edge_index") is not None})
    out["usable"] = True
    return out


def _evidence_for_normalize(doc: EditDoc, evidence, allowed_indices, task_id: str):
    """把**已判定的**可保护边组装成 normalize 用的证据（未被允许的边不进入）。"""
    import ttml_normalize as _N

    if not allowed_indices or not isinstance(evidence, dict):
        return None
    edges = [e for e in (evidence.get("edges") or []) if isinstance(e, dict)]
    keep = []
    for idx in allowed_indices:
        if idx + 1 >= len(doc.lines):
            continue
        prev_key, next_key = doc.lines[idx].key, doc.lines[idx + 1].key
        for e in edges:
            if e.get("prev_key") == prev_key and e.get("next_key") == next_key:
                keep.append(e)
                break
    if not keep:
        return None
    return {
        "schema_version": evidence.get("schema_version"),
        "algorithm_version": evidence.get("algorithm_version"),
        "default_policy_version": evidence.get("default_policy_version"),
        "doc": {"fingerprint": _N.overlap_evidence_fingerprint(doc), "identity": {"id": task_id}},
        "edges": keep,
    }


def rebuild_evidence(doc: EditDoc, allowed_indices, task_id: str, ttml_text: str):
    """保存后用**新状态**逐边重校验，只保留仍成立的边，并绑定持久化指纹与 TTML SHA256。"""
    import ttml_normalize as _N

    if not allowed_indices:
        return None
    from datetime import datetime, timezone

    kept = []
    doc_fp = _N.overlap_evidence_fingerprint(doc)
    for idx in allowed_indices:
        if idx + 1 >= len(doc.lines):
            continue
        edge = _edge_from_doc(doc, idx)
        probe = {"schema_version": _N.EVIDENCE_SCHEMA_VERSION,
                 "algorithm_version": _N.EVIDENCE_ALGORITHM_VERSION,
                 "default_policy_version": _EVIDENCE_POLICY_VERSION,
                 "doc": {"fingerprint": doc_fp, "identity": {"id": task_id}},
                 "edges": [edge]}
        dec = _N.check_overlap_evidence(doc, probe, policy_version=_EVIDENCE_POLICY_VERSION,
                                        identity=task_id)
        if dec and dec[0].get("allowed"):
            kept.append(edge)
    if not kept:
        return None
    return {
        "schema_version": _N.EVIDENCE_SCHEMA_VERSION,
        "algorithm": "tail_extend@s7b/opt-in",
        "algorithm_version": _N.EVIDENCE_ALGORITHM_VERSION,
        "default_policy_version": _EVIDENCE_POLICY_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "doc": {"identity": {"task_id": task_id},
                "fingerprint_persisted": doc_fp,
                "ttml_sha256": ttml_sha256(ttml_text),
                "line_count": len(doc.lines)},
        "edges": kept,
    }


def save_doc(doc: EditDoc, edit_path: str, ttml_path: Optional[str] = None, *,
             overlap_evidence=None) -> str:
    """原子落盘：**先 TTML，后 .edit.json**。

    顺序理由：TTML 是正文，证据必须最后落地 —— 若 TTML 写失败则 .edit.json（含证据）
    不会更新，不会出现"证据绑定了旧文档"的状态；若 .edit.json 写失败，下次加载时
    磁盘上的证据指纹与当前文档不匹配 ⇒ 校验失败关闭 ⇒ 回退默认 R4（不会误放行）。
    """
    payload = {"doc": doc.to_dict(), "version": doc.version}
    if overlap_evidence is not None:
        payload["overlap_evidence"] = overlap_evidence
    if ttml_path:
        atomic_write_text(ttml_path, _safe_ttml(doc))
    atomic_write_text(edit_path, json.dumps(payload, ensure_ascii=False, indent=1))
    return edit_path


# ------------------------------------------------------------------- 规范化
def _safe_ttml(doc: EditDoc) -> str:
    """doc → TTML；空文档等异常必须变成结构化 400，不能漏成 500"""
    try:
        return doc_to_ttml(doc)
    except ValueError as e:
        raise EditError(
            "这份文档写不出 TTML", detail=str(e),
            hint="确认至少有一行带时间戳的歌词；空文件或只有 head 的文件无法导出"
        ) from e


def _report_dict(rep) -> dict:
    return {
        "fixes": [f.to_dict() for f in rep.fixes],
        "unresolved": [u.to_dict() for u in getattr(rep, "unresolved", [])],
        "summary": rep.summary(),
    }


def _check_dict(res) -> dict:
    return {
        "ok": res.ok,
        "errors": [e.__dict__ for e in res.errors],
        "warnings": [w.__dict__ for w in res.warnings],
        "summary": res.summary(),
    }


def _protection_dict(verdict: dict, persisted_edges: int = 0,
                     evidence_reason: str = "ok") -> dict:
    """统一的对外可观测结构（预览与保存同一形状）。"""
    decisions = verdict.get("decisions") or []
    allowed = len(verdict.get("allowed_indices") or [])
    return {"decisions": decisions,
            "summary": {"allowed": allowed,
                        "rejected": max(0, len(decisions) - allowed),
                        "persisted_edges": persisted_edges},
            "doc_level_reason": verdict.get("doc_level_reason"),
            "evidence_reason": evidence_reason}


def _read_ttml_on_disk(ttml_path: Optional[str]):
    if not ttml_path or not os.path.exists(ttml_path):
        return None, False
    try:
        with open(ttml_path, encoding="utf-8") as f:
            return f.read(), True
    except OSError:
        return None, False


def validate(doc: EditDoc, policy: str = "auto", *, edit_path: Optional[str] = None,
             task_id: Optional[str] = None, overlap_evidence=None) -> dict:
    """只算不写。**深拷贝**后规范化，绝不动调用方的 doc。

    与 :func:`apply_doc` 共用 :func:`evaluate_evidence` ⇒ 同一文档 + 同一证据 ⇒
    **相同的 allowed 集合与 reason**（7C.2 验收项 T10）。

    返回值里额外带上**修复后的 doc**：前端拖完一个字要立刻显示"已自动修复"后的
    时间，否则界面还停在坏时间上（设计文档 §5.2 明确要求"提示已自动修复 1 处"
    而不是把红色错误丢给用户）。version 不变，因为它没落盘。
    """
    evidence, ev_reason = ((overlap_evidence, "provided")
                           if overlap_evidence is not None else read_evidence(edit_path))
    if overlap_evidence is None and evidence is None:
        # 生成链路写的 sidecar：只在 .edit.json 里没有证据时采纳；信任判断仍由
        # evaluate_evidence 统一做（指纹/身份/TTML SHA 任一不匹配 ⇒ 拒绝保护）。
        side_ev, side_reason = read_sidecar_evidence(edit_path)
        if side_ev is not None:
            evidence, ev_reason = side_ev, side_reason
    ttml_path = (os.path.join(os.path.dirname(edit_path), edit_task_id(edit_path) + ".ttml")
                 if edit_path else None)
    ttml_text, ttml_present = _read_ttml_on_disk(ttml_path)
    verdict = evaluate_evidence(doc, evidence, edit_path=edit_path, task_id=task_id,
                               ttml_text_on_disk=ttml_text, ttml_present=ttml_present,
                               sidecar_adopted=(ev_reason == "adopted_from_sidecar"))
    tid = verdict.get("task_id") or ""
    filtered = _evidence_for_normalize(doc, evidence, verdict["allowed_indices"], tid)
    layered = bool((getattr(doc, "write_options", {}) or {}).get("layered"))
    apple_internal = ((getattr(doc, "write_options", {}) or {}).get("mode") == "apple-internal")
    layered = layered or apple_internal
    work = copy.deepcopy(doc)
    rep = normalize_timeline(work, policy=policy, overlap_evidence=filtered,
                             layered=layered, evidence_identity=tid or None)
    res = check_ttml(_safe_ttml(work), strict=True, layered=layered, apple_internal=apple_internal,
                     allowed_overlaps=_allowed_overlaps_for_check(doc, verdict["allowed_indices"]))
    return {"normalize": _report_dict(rep), "check": _check_dict(res),
            "doc": work.to_dict(), "version": work.version, "stats": work.stats(),
            "overlap_protection": _protection_dict(verdict, evidence_reason=ev_reason)}


def apply_doc(doc: EditDoc, edit_path: str, ttml_path: Optional[str] = None,
              policy: str = "auto", *, task_id: Optional[str] = None,
              overlap_evidence=None) -> dict:
    """保存路径：**先判定证据 → 再就地规范化 → 校验 → 落盘**（先 TTML 后 .edit.json）。

    ⚠️ 顺序约束：判定必须在归一化**之前**完成（不允许先压字级再宣告有效）。
    保存成功后用新状态逐边重校验，只持久化仍然成立的边，并绑定持久化指纹与 TTML SHA256。

    返回 {doc, version, ttml, normalize, check, overlap_protection, stats}；
    ``doc`` 是修好之后的版本，前端要用它刷新界面。
    """
    tid = str(task_id or edit_task_id(edit_path) or "")
    evidence, ev_reason = ((overlap_evidence, "provided")
                           if overlap_evidence is not None else read_evidence(edit_path))
    if overlap_evidence is None and evidence is None:
        side_ev, side_reason = read_sidecar_evidence(edit_path)
        if side_ev is not None:
            evidence, ev_reason = side_ev, side_reason
    ttml_text, ttml_present = _read_ttml_on_disk(ttml_path)
    verdict = evaluate_evidence(doc, evidence, edit_path=edit_path, task_id=tid,
                               ttml_text_on_disk=ttml_text, ttml_present=ttml_present,
                               sidecar_adopted=(ev_reason == "adopted_from_sidecar"))
    filtered = _evidence_for_normalize(doc, evidence, verdict["allowed_indices"], tid)
    layered = bool((getattr(doc, "write_options", {}) or {}).get("layered"))
    apple_internal = ((getattr(doc, "write_options", {}) or {}).get("mode") == "apple-internal")
    layered = layered or apple_internal
    rep = normalize_timeline(doc, policy=policy, overlap_evidence=filtered,
                             layered=layered, evidence_identity=tid or None)
    ttml = _safe_ttml(doc)
    res = check_ttml(ttml, strict=True, layered=layered, apple_internal=apple_internal,
                     allowed_overlaps=_allowed_overlaps_for_check(doc, verdict["allowed_indices"]))
    doc.version = int(getattr(doc, "version", 0) or 0) + 1
    ttml = _safe_ttml(doc)           # version 变化不影响正文，但保持同源
    new_evidence = rebuild_evidence(doc, verdict["allowed_indices"], tid, ttml)
    save_doc(doc, edit_path, ttml_path, overlap_evidence=new_evidence)
    persisted = len((new_evidence or {}).get("edges", []))
    return {"doc": doc.to_dict(), "version": doc.version, "ttml": ttml,
            "normalize": _report_dict(rep), "check": _check_dict(res),
            "overlap_protection": _protection_dict(verdict, persisted, ev_reason),
            "stats": doc.stats()}


def check_version(current: Any, expected: Any) -> None:
    """乐观锁：拿**存储里的** version 和前端带来的比，不一致 → 409。

    ⚠️ 必须是"存储版本 vs 提交版本"，**不能**拿提交体里的 doc.version 自比
    （那样永远相等，锁形同虚设）。
    """
    if expected is None:
        return
    try:
        want = int(expected)
    except (TypeError, ValueError):
        raise EditError("version 不是整数", detail=repr(expected), code=400)
    cur = int(current or 0)
    if want != cur:
        raise EditError(
            "版本冲突：这份文档已被改动", where=f"version={want}",
            detail=f"当前 version={cur}",
            hint="重新加载一次再改（GET /api/edit）", code=409)


def stored_version(task_doc: Optional[EditDoc], edit_path: Optional[str]) -> int:
    """当前权威版本号：内存优先，其次磁盘 .edit.json，最后 0"""
    if task_doc is not None:
        return int(getattr(task_doc, "version", 0) or 0)
    if edit_path and os.path.exists(edit_path):
        try:
            with open(edit_path, encoding="utf-8") as f:
                return int(json.load(f).get("version", 0) or 0)
        except (OSError, ValueError):
            return 0
    return 0


# --------------------------------------------------------------------- 便捷
def trusted_output_dir() -> str:
    """**服务端可信**输出目录。

    优先取 ``web_server`` 的**运行时配置**（桌面版/测试可能覆盖 ``web_server.OUTPUT_DIR``），
    取不到再退化为本包目录下的 ``outputs``（二者默认值相同，已核实）。

    ⚠️ 绝不接受请求体里的路径作为证据读取依据 —— 客户端只能给 ``id``。
    """
    try:
        import web_server as _ws        # 函数内 import：避免模块级循环导入
        d = getattr(_ws, "OUTPUT_DIR", None)
        if d:
            return str(d)
    except Exception:
        pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")


def edit_path_for_task(task_id: str) -> Optional[str]:
    """仅凭 ``task_id`` 构造 ``.edit.json`` 路径（可信目录 + 严格清洗）。

    id 含路径分隔符/异常字符 ⇒ 返回 ``None``（**拒绝**，不猜、不跨任务）。
    """
    raw = "" if task_id is None else str(task_id)
    safe = "".join(c for c in raw if c.isalnum() or c in "-_")
    if not safe or safe != raw:
        return None
    return edit_path_for(safe, trusted_output_dir())


def validate_payload(body: dict) -> dict:
    """POST /api/validate 的入参：{doc} 或 {ttml}，可选 {id}。

    给了 ``id`` ⇒ 用**服务端可信输出目录**定位 ``<id>.edit.json`` 并校验证据，
    使预览与保存得到同一判定；**请求体里的任何路径都不参与解析**。
    未给身份 ⇒ 按"无证据"处理（明确返回 evidence_absent），不会静默放行。
    """
    if body.get("doc"):
        doc = doc_from_dict(body["doc"])
    elif body.get("ttml"):
        doc = doc_from_ttml(str(body["ttml"]))
    else:
        raise EditError("既没给 doc 也没给 ttml",
                        hint="传 {doc: <EditDoc>} 或 {ttml: '<tt>…'} 之一")
    task_id = str(body.get("id") or "")
    edit_path = edit_path_for_task(task_id) if task_id else None
    return validate(doc, policy=str(body.get("policy") or "auto"),
                    edit_path=edit_path, task_id=task_id or None)


def edit_path_for(task_id: str, output_dir: str) -> str:
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_") or "unknown"
    return os.path.join(output_dir, f"{safe}.edit.json")
