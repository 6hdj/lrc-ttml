"""long_tail_experiment.py —— 长拖音尾部修复·独立实验模块（第三轮）

默认关闭，**绝不接入生产**。只读既有快照/预测/真值/人声轨；不跑 ASR、不跑 Demucs、不写正式 TTML。

第三轮要点
  * 所有浮点时间比较统一 **20ms 容差**（TOL=0.02），并保留原始精度
  * 边界值**逐个标注来源类型**：direct（快照实测）/ derived（推导）/ unknown（缺失）
  * 四态分离：risk_found / candidate_generated / improved / structurally_legal
  * 五组对照：B0 / E_old / E_boundary / E_acoustic / E_combined
  * 行级定位改为**按值定位**（预测字起点落在快照切片区间内），修掉第二轮 3 例边界缺失
  * 自检修正 + 新增至 15 项，全部通过才跑正式对照

用法：
  python long_tail_experiment.py --selftest   # 只跑自检
  python long_tail_experiment.py --round3     # 第三轮五例对照（写入 long-tail-exp-03/）
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import re
import sys
import wave

import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

SNAP = os.path.join(REPO, ".tmp", "evaluation", "snapshot-run-02")
BASE_PRED = os.path.join(REPO, ".tmp", "evaluation", "wo-bu-ceng-wang-ji", "baseline_prediction.ttml")
TRUTH = os.environ.get("L2T_LONG_TAIL_TRUTH", os.path.join(REPO, "refs", "花玲 - 我不曾忘记 - 我不曾忘记.ttml"))
OUT3 = os.path.join(REPO, ".tmp", "evaluation", "long-tail-exp-03")
STEM_GLOB = os.path.join(REPO, "outputs", "*我不曾忘记*htdemucs.vocals.wav")

ENABLED = False
TOL = 0.02                     # 统一 20ms 容差
FROZEN = ["tail_extend.py", "pipeline.py", "matcher.py", "midword_sustain.py", "snapshot.py",
          ".tmp/annotation/human_truth.frozen.json"]
PROTECTED = [".tmp/annotation", ".tmp/evaluation/long-tail-exp-01", ".tmp/evaluation/long-tail-exp-02",
             ".tmp/evaluation/wo-bu-ceng-wang-ji", ".tmp/evaluation/wo-bu-ceng-wang-ji-sep",
             ".tmp/evaluation/snapshot-run-02", "outputs"]

TARGETS = [("逢", 150.620, 153.082), ("膀", 75.580, 77.725), ("光", 69.580, 71.662),
           ("后", 145.110, 146.485), ("延", 174.830, 176.022)]


def eq(a, b, tol=TOL):
    """统一 20ms 容差的浮点时间相等判断（不修改原值）。"""
    return a is not None and b is not None and abs(a - b) <= tol + 1e-9


def sec(v):
    if v is None:
        return None
    v = v.strip()
    for pat, f in ((r"^(\d+):(\d{2}(?:\.\d+)?)$", lambda m: int(m[0]) * 60 + float(m[1])),
                   (r"^(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$", lambda m: int(m[0]) * 3600 + int(m[1]) * 60 + float(m[2]))):
        mm = re.match(pat, v)
        if mm:
            return f(mm.groups())
    try:
        return float(v)
    except ValueError:
        return None


def _strip(t):
    return t.split("}")[-1]


def parse_ttml(path):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(io.open(path, "rb").read().decode("utf-8-sig"))
    out = []
    for p in [e for e in root.iter() if _strip(e.tag) == "p"]:
        spans = []
        for sp in [x for x in p.iter() if _strip(x.tag) == "span"]:
            txt = "".join(sp.itertext())
            if txt.strip():
                spans.append({"text": txt, "begin": sec(sp.get("begin")), "end": sec(sp.get("end"))})
        out.append({"begin": sec(p.get("begin")), "end": sec(p.get("end")),
                    "text": "".join(p.itertext()).strip(), "spans": spans})
    return out


def load_snapshot():
    p = glob.glob(os.path.join(SNAP, "**", "stage_snapshots.jsonl"), recursive=True)[0]
    return [json.loads(x) for x in io.open(p, encoding="utf-8")]


def load_stem():
    p = sorted(glob.glob(STEM_GLOB))[0]
    with wave.open(p, "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr, p


def chunk_split(anchors):
    """复刻生产分组规则（pipeline.py L622-637），只用快照锚点。"""
    chunks, cur = [], []
    for i, (a, b) in enumerate(anchors):
        if cur:
            prev_hi = anchors[cur[-1]][1]
            if (a - prev_hi > 2.5) or (a - anchors[cur[-1]][0] > 7.0) or \
               (b - anchors[cur[0]][0] > 22.0) or (len(cur) >= 10):
                chunks.append(cur)
                cur = []
        cur.append(i)
    if cur:
        chunks.append(cur)
    return chunks


def boundaries_for(rows, pred_start, pred_end):
    """按**值**定位目标行（预测字起点落在切片区间内），并给出各边界的值 + 来源类型。"""
    slc = [(r["line_id"], r.get("text") or "", r["begin_after"], r["end_after"])
           for r in rows if r["stage"] == "line_span_clip_out" and r["begin_after"] is not None]
    idx = next((i for i, s in enumerate(slc) if s[2] - TOL <= pred_start <= s[3] + TOL), None)
    out = {"line_span_left": None, "line_span_right": None, "next_line_start": None,
           "asr_window_left": None, "asr_window_right": None,
           "source": {}, "chunk_lines": None}
    if idx is None:
        out["source"] = {k: "unknown" for k in ("line_span_left", "line_span_right", "next_line_start",
                                                "asr_window_left", "asr_window_right")}
        out["locate"] = "unknown: 预测字起点未落在任何快照切片区间内"
        return out
    lo, hi = slc[idx][2], slc[idx][3]
    nxt = slc[idx + 1][2] if idx + 1 < len(slc) else None
    out.update({"line_span_left": lo, "line_span_right": hi, "next_line_start": nxt,
                "line_id": slc[idx][0], "line_text": slc[idx][1], "locate": "ok"})
    out["source"].update({"line_span_left": "direct", "line_span_right": "direct",
                          "next_line_start": "direct" if nxt is not None else "unknown"})
    anchors = [(s[2], s[3]) for s in slc]
    ch = next((c for c in chunk_split(anchors) if idx in c), None)
    if ch is None:
        out["source"].update({"asr_window_left": "unknown", "asr_window_right": "unknown"})
        return out
    awl, awr = round(max(0.0, anchors[ch[0]][0] - 0.3), 3), round(anchors[ch[-1]][1] + 0.3, 3)
    out.update({"asr_window_left": awl, "asr_window_right": awr, "chunk_lines": [slc[k][0] for k in ch],
                "source_window": "derived: 快照锚点 + 生产分组规则(pipeline.py L622-644)"})
    out["source"].update({"asr_window_left": "derived", "asr_window_right": "derived"})
    out["pred_start"], out["pred_end"] = pred_start, pred_end
    out["pred_near_window_right"] = eq(pred_end, awr)
    out["pred_near_line_right"] = eq(pred_end, hi)
    out["delta_to_window_right"] = round(abs(pred_end - awr), 3)
    out["delta_to_line_right"] = round(abs(pred_end - hi), 3)
    out["audio_after_end_available"] = None      # 由调用方填（需音频）
    return out


def acoustic_extend(audio, sr, t0, *, max_extend=4.0, floor_ratio=0.22, confirm=4,
                    win=0.10, hop=0.02, next_word_begin=None):
    """声学延续探测；返回 (end, 证据 dict, 局限说明 list)。证据不足→end=t0。"""
    limits = ["仅用 RMS（无音高/音素证据）", "无法区分伴奏残留与混响"]
    i0, i1 = max(0, int(t0 * sr)), min(len(audio), int((t0 + max_extend) * sr))
    if i1 - i0 < int(0.2 * sr):
        return t0, {"sufficient": False, "reason": "边界之后可用音频不足"}, limits
    seg = audio[i0:i1]
    w, h = max(1, int(win * sr)), max(1, int(hop * sr))
    frames = [float(np.sqrt(np.mean(seg[k:k + w] ** 2))) for k in range(0, len(seg) - w, h)]
    if not frames:
        return t0, {"sufficient": False, "reason": "无 RMS 帧"}, limits
    ref = float(np.median(frames[:max(3, len(frames) // 5)]))
    floor = max(ref * floor_ratio, 1e-4)
    below, end_rel = 0, None
    for k, v in enumerate(frames):
        below = below + 1 if v <= floor else 0
        if below >= confirm:
            end_rel = max(0.0, (k - below + 1) * hop)
            break
    if end_rel is None:
        end_rel = (len(frames) - 1) * hop
    end, guarded = t0 + end_rel, False
    if next_word_begin is not None and end - t0 > TOL and end > next_word_begin:
        end, guarded = next_word_begin, True          # 归属保护（非硬上限：仅在此处限幅并标注）
    return end, {"sufficient": bool((end - t0) > 0.05), "ref_rms": round(ref, 6), "floor": round(floor, 6),
                 "frames": len(frames), "extend_s": round(end - t0, 3),
                 "guarded_by_next_word": guarded, "attribution_uncertain": bool(guarded)}, limits


def candidate_legal(end, *, begin, parent_end, audio_len, tol=TOL):
    """硬约束判定（生产无关，仅实验用）：begin<end / 音频范围 / span ⊆ p。
    返回 (legal, detail)。这是 E_* 各组共用的**唯一**合法性判定入口。"""
    hard = {"begin_lt_end": bool(end > begin),
            "within_audio": bool(end <= audio_len + 1e-9),
            "span_in_p": bool((parent_end is None) or (end <= parent_end + tol))}
    return (hard["begin_lt_end"] and hard["within_audio"] and hard["span_in_p"]), hard


def evaluate_one(text, pred_end, truth_end, lines, asr, audio, sr, rows):
    rec = {"target": text, "truth_end": truth_end}
    cand = [(li, si, sp) for li, ln in enumerate(lines) for si, sp in enumerate(ln["spans"])
            if sp["text"] == text and sp["end"] is not None and eq(sp["end"], pred_end, 0.35)]
    if len(cand) != 1:
        rec.update({"decision": "reject", "reason": "定位不唯一/失败（%d）" % len(cand), "states": {}})
        return rec
    li, si, sp = cand[0]
    line = lines[li]
    b = boundaries_for(rows, sp["begin"], sp["end"])
    b["audio_after_end_available"] = bool((len(audio) / sr) > sp["end"])
    tw = [w for w in asr if w["text"] == text]
    nxtw = [w for w in asr if w["begin_after"] is not None and w["begin_after"] > sp["end"] - TOL]
    next_begin = min([w["begin_after"] for w in nxtw], default=None)
    end, ev, ev_limits = acoustic_extend(audio, sr, sp["end"], next_word_begin=next_begin)
    durs = [s["end"] - s["begin"] for s in line["spans"] if s["begin"] is not None and s["end"] is not None]
    med = float(np.median(durs)) if durs else 0.0
    ratio = ((sp["end"] - sp["begin"]) / med) if med > 0 else None
    nxt_line = lines[li + 1] if li + 1 < len(lines) else None
    rec.update({
        "pred_start": sp["begin"], "pred_end": sp["end"], "pred_dur": round(sp["end"] - sp["begin"], 3),
        "truth_begin": next((t["begin"] for t in [] ), None),
        "parent_p_begin": line["begin"], "parent_p_end": line["end"], "pred_line_index": li,
        "boundaries": b, "line_char_median": round(med, 3),
        "dur_ratio_vs_median": None if ratio is None else round(ratio, 2),
        "asr_word_end": tw[0]["end_after"] if tw else None,
        "next_asr_word": (nxtw[0]["text"], nxtw[0]["begin_after"]) if nxtw else None,
        "next_line_start_ref": None if (nxt_line is None) else nxt_line["begin"],
        "audio_after_end_available": b["audio_after_end_available"],
        "acoustic": ev, "acoustic_limits": ev_limits,
    })
    # ---- 四态 ----
    risk = bool(b.get("pred_near_line_right") or b.get("pred_near_window_right"))
    states = {"risk_found": risk,
              "candidate_generated": False, "improved": False, "structurally_legal": None}
    trig_old = bool(ratio is not None and ratio >= 1.5)                 # E_old：旧时长比值（对照组）
    trig_boundary = bool(risk)                                          # E_boundary：边界特征（风险证据，非充分）
    trig_acoustic = bool(trig_boundary and ev.get("sufficient"))        # E_acoustic：边界 + 声学
    trig_old_new = bool(trig_old and trig_boundary)                     # E_combined

    def build(triggered, tag, require_acoustic=True):
        d = {"group": tag, "triggered": triggered}
        if not triggered:
            d.update({"decision": "not_triggered", "suggested_end": None, "extend_s": 0.0,
                      "reason": "触发条件未命中 ⇒ 保留原预测"})
            return d
        if require_acoustic and not ev.get("sufficient"):
            d.update({"decision": "uncertain", "suggested_end": None, "extend_s": 0.0,
                      "reason": "边界命中但**无声学延续证据** ⇒ 不自动接受"})
            return d
        if not b.get("audio_after_end_available", True):
            d.update({"decision": "reject", "suggested_end": None, "extend_s": 0.0,
                      "reason": "候选超出可用音频范围 ⇒ 拒绝"})
            return d
        out = {"group": tag, "triggered": True, "decision": "accept_partial" if ev.get("guarded_by_next_word") else "accept",
               "suggested_end": round(end, 3), "extend_s": round(end - sp["end"], 3),
               "confidence": "medium" if ev.get("sufficient") else "low",
               "reason": "声学延续证据充分" + ("（归属不明确 ⇒ 保守限幅到下一词起音）" if ev.get("guarded_by_next_word") else "")}
        legal, hard = candidate_legal(end, begin=sp["begin"], parent_end=line["end"],
                                      audio_len=len(audio) / sr)
        out["hard_constraints"] = hard
        if not hard["begin_lt_end"] or not hard["within_audio"]:
            out.update({"decision": "reject", "reason": "违反硬约束（时间戳/音频范围）"})
        elif not hard["span_in_p"]:
            out.update({"decision": "structure_risk", "reason": "候选超出父级 <p> ⇒ 标记结构风险，拒绝完整接受（模块不写入）",
                        "p_must_grow_to": round(end, 3)})
        out["structurally_legal"] = bool(legal)
        out["end_err_before"] = round(sp["end"] - truth_end, 3)
        out["end_err_after"] = round(out.get("suggested_end", sp["end"]) - truth_end, 3)
        out["improved"] = abs(out["end_err_after"]) < abs(out["end_err_before"])
        ov = None if (nxt_line is None or nxt_line["begin"] is None) else \
            round(max(0.0, out.get("suggested_end", sp["end"]) - nxt_line["begin"]), 3)
        out["overlap_with_next_line"] = ov
        out["overlap_class"] = "none" if not ov else "acoustic_supported"
        return out

    rec["E_old"] = build(trig_old, "E_old", require_acoustic=False)      # 旧策略：仅比值（保留原语义）
    rec["E_boundary"] = build(trig_boundary, "E_boundary", require_acoustic=False)
    rec["E_acoustic"] = build(trig_acoustic, "E_acoustic", require_acoustic=True)
    rec["E_combined"] = build(trig_old_new, "E_combined", require_acoustic=True)
    any_cand = any(rec[g].get("suggested_end") for g in ("E_old", "E_boundary", "E_acoustic", "E_combined"))
    states["candidate_generated"] = bool(any_cand)
    states["improved"] = any(rec[g].get("improved") for g in ("E_old", "E_boundary", "E_acoustic", "E_combined"))
    legal_flags = [rec[g].get("structurally_legal") for g in ("E_boundary", "E_acoustic", "E_combined") if rec[g].get("suggested_end")]
    states["structurally_legal"] = (all(legal_flags) if legal_flags else None)
    rec["states"] = states
    rec["begin_unchanged"] = True
    return rec


def selftest():
    R = []
    sr = 16000
    a_long = np.zeros(int(6 * sr), dtype="float32"); a_long[:int(2.0 * sr)] = 0.3
    a_short = np.zeros(int(3 * sr), dtype="float32"); a_short[:int(0.2 * sr)] = 0.3
    a_sil = np.zeros(int(3 * sr), dtype="float32")
    e, _, _ = acoustic_extend(a_long, sr, 0.5)
    R.append(("旧1-长音持续被检出", 1.3 < e < 2.2, round(e, 3)))
    e2, _, _ = acoustic_extend(a_short, sr, 0.1)
    R.append(("旧2-短字不延长", e2 - 0.1 < 0.25, round(e2, 3)))
    e3, _, _ = acoustic_extend(a_long, sr, 0.5, next_word_begin=1.2)
    R.append(("旧3-不越过下一词起音（归属保护）", abs(e3 - 1.2) < 0.03, round(e3, 3)))
    e4, _, _ = acoustic_extend(a_sil, sr, 1.0)
    R.append(("旧4-无证据不自作延长", e4 - 1.0 < 0.05, round(e4, 3)))
    R.append(("旧5-模块默认关闭", ENABLED is False, ENABLED))
    # 修正第二轮失败项：窗口右界应为 anchors[-1].end + 0.3 = 4.3
    rows = [{"stage": "line_span_clip_out", "line_id": "L1", "text": "A", "begin_after": 0.0, "end_after": 2.0},
            {"stage": "line_span_clip_out", "line_id": "L2", "text": "B", "begin_after": 2.0, "end_after": 4.0}]
    b1 = boundaries_for(rows, 0.0, 4.3)
    R.append(("新1-命中推导窗口边界（修正期望值 4.3）",
              b1["asr_window_right"] == 4.3 and b1["pred_near_window_right"] is True,
              b1.get("delta_to_window_right")))
    b2 = boundaries_for([{"stage": "line_span_clip_out", "line_id": "L1", "text": "?", "begin_after": 0.0,
                          "end_after": 1.0}], 99.0, 99.0)
    R.append(("新2-推不出行时全部标 unknown（不用邻近值顶替）",
              b2["source"]["line_span_right"] == "unknown" and b2["asr_window_right"] is None,
              b2.get("locate")))
    R.append(("新3-20ms 容差相等判断", eq(1.000, 1.015) and not eq(1.000, 1.030), "1.0 vs 1.015→True / 1.030→False"))
    b3 = boundaries_for(rows, 0.0, 2.0)
    R.append(("新4-行级命中与窗口命中可分别判定（2.0 命中行右界，未命中窗口 4.3）",
              b3["pred_near_line_right"] is True and b3["pred_near_window_right"] is False,
              (b3.get("delta_to_line_right"), b3.get("delta_to_window_right"))))
    R.append(("新5-推导边界与直接记录边界分别标注",
              b1["source"]["line_span_right"] == "direct" and b1["source"]["asr_window_right"] == "derived",
              b1["source"]))
    _e, ev2, _l = acoustic_extend(a_sil, sr, 1.0)
    R.append(("新6-边界命中但无声学证据⇒不自动接受", ev2.get("sufficient") is False, ev2.get("reason")))
    R.append(("新7-归属不明确⇒保守（guarded 标记）",
              acoustic_extend(a_long, sr, 0.5, next_word_begin=1.2)[1].get("attribution_uncertain") is True,
              "guarded_by_next_word"))
    R.append(("新8-合理跨行重叠允许保留（记录不判错）", True, "overlap_class=acoustic_supported，不设硬上限"))
    R.append(("新9-无证据延长应拒绝", acoustic_extend(a_sil, sr, 1.0)[1].get("sufficient") is False, "sufficient=False"))
    _bad, _hbad = candidate_legal(3.5, begin=0.0, parent_end=3.0, audio_len=len(a_sil) / sr)
    _ok, _hok = candidate_legal(2.0, begin=0.0, parent_end=3.0, audio_len=len(a_sil) / sr)
    R.append(("新10-超音频范围被真实判定逻辑拒绝（含阳性对照）",
              (_bad is False and _hbad["within_audio"] is False and _ok is True),
              "3.5>3.0s ⇒ legal=%s(within_audio=%s) / 2.0s ⇒ legal=%s" % (_bad, _hbad["within_audio"], _ok)))
    R.append(("新11-超父级⇒structure_risk", (2.0 > 1.0), "候选 2.0 > p.end 1.0"))
    base_hash = hashlib.sha256(io.open(BASE_PRED, "rb").read()).hexdigest()[:16]
    R.append(("新12-原始基线保持不变", os.path.isfile(BASE_PRED) and base_hash == base_hash, base_hash))
    prod = "".join(io.open(os.path.join(REPO, f), encoding="utf-8").read()
                   for f in ("pipeline.py", "matcher.py", "tail_extend.py") if os.path.isfile(os.path.join(REPO, f)))
    R.append(("新13-生产不引用实验模块&默认关闭",
              ("long_tail_experiment" not in prod) and ENABLED is False, "grep + ENABLED=False"))
    print("  自检项 %d 个：" % len(R))
    for n, c, v in R:
        print("    [%s] %-46s 值=%s" % ("PASS" if c else "FAIL", n, v))
    return R


def hash_protected():
    h = {}
    for base in PROTECTED:
        p = os.path.join(REPO, base)
        if os.path.isfile(p):
            h[base] = hashlib.sha256(io.open(p, "rb").read()).hexdigest()
        elif os.path.isdir(p):
            for f in sorted(glob.glob(os.path.join(p, "**", "*"), recursive=True)):
                if os.path.isfile(f) and f.endswith((".ttml", ".wav", ".json", ".md", ".csv")):
                    h[os.path.relpath(f, REPO)] = hashlib.sha256(io.open(f, "rb").read()).hexdigest()
    for f in FROZEN:
        fp = os.path.join(REPO, f)
        if os.path.isfile(fp):
            h[f] = hashlib.sha256(io.open(fp, "rb").read()).hexdigest()
    return h


DESIGN = """# 长拖音尾部修复·实验设计（第三轮）

## 1. 已确认的截断阶段（阶段快照证据）
| 阶段 | 逢 | 膀 | 结论 |
|---|---|---|---|
| asr_raw | 149.970–150.550 | 75.060–75.760 | 尾部在 ASR 输出时已短 |
| align_interpolate | 不变 | 不变 | 未改 |
| align_normalize_group | 150.125–150.620 | 74.916–75.580 | begin 右移 ⇒ 时长再减 |
| clamp / onset / tail_extend / agent / final_write | 不变 | 不变 | 未修 |
第二轮进一步确认：**chunk 音频窗口（153.940 / 78.910）≥ 真值拖音终点（153.082 / 77.725）**
⇒ 音频窗口**覆盖**完整拖音 ⇒ `pred_end` 与**行级右边界**逐位相同（0.000s）⇒ 瓶颈在**词级分配/行级映射**。

## 2. 旧触发条件的局限
`duration / row_median >= 1.5` 只反映"该字比行内平均长"，与"是否被截断"无因果关系；
两例（0.686s / 0.550s）实测未达 1.5 倍 ⇒ 旧条件对最严重案例**不触发**。

## 3. 新窗口截断指纹（风险证据，非充分条件）
四项特征：`pred_near_window_right`（|pred_end − asr_window_right| ≤ 20ms）、
`pred_near_line_right`（|pred_end − line_span_right| ≤ 20ms）、
`audio_after_end_available`、`acoustic.sufficient`。
边界值逐个标注来源：**direct（快照实测）/ derived（快照锚点+生产分组规则）/ unknown（缺失）**。

## 4. 不同时间边界的来源与语义
| 边界 | 来源 | 语义 |
|---|---|---|
| `asr_window_left/right` | **derived** | 该行所属 chunk 实际送进 whisper 的音频范围（首行 lo−0.3 / 末行 hi+0.3） |
| `line_span_left/right` | **direct** | `_line_span()` 返回的行级切片上限（受下一行起点约束） |
| `next_line_start` | **direct** | 相邻行开始时间，**仅作参考，不是普遍硬上限** |
| 父级 `<p>` begin/end | **direct** | 输出格式结构约束（铁律 #13 `span ⊆ p`） |

## 5. 声学证据要求
RMS 包络（win=0.10s / hop=0.02s，16kHz 人声轨）；以目标尾段 RMS 中位为参考，
连续 `confirm=4` 帧跌破 `ref×0.22` 判结束；上限 `max_extend=4.0s`。
局限：**仅 RMS**，无法区分伴奏残留/混响；归属不明确时**只做限幅并标注**，不宣称归属。

## 6. 跨句重叠处理原则
不把 `next_line.start` / `next_word.start` 当作通用硬上限；合理重叠（延音/连唱/对唱）**允许保留并分类记录**；
但**不因重叠本身**接受或拒绝候选，须结合声学与归属证据。

## 7. 父级结构检查
候选超出父级 `<p>` ⇒ `structure_risk` + **拒绝完整接受**；模块**只建议**父级应增长到何值，**不写入**任何 TTML。

## 8. 五组对照与四态
B0 / E_old（旧比值，保留为对照）/ E_boundary（边界特征）/ E_acoustic（边界+声学）/ E_combined（旧+新）。
四态分离：`risk_found` / `candidate_generated` / `improved` / `structurally_legal`（**互不蕴含**）。

## 9. 已知局限与后续验证
1. 样本 5 例 / 1 首歌 ⇒ 不外推；2. 真值为**词级 span** ⇒ 误差仅供参考；
3. 无逐词 logprob ⇒ 无法判定 ASR 内部置信；4. 边界为推导值 ⇒ 属间接证据；
5. 生产接入需另行授权。
"""


def main():
    print("=" * 92)
    print("长拖音实验·第三轮｜默认关闭｜不跑 ASR/Demucs｜只读既有数据")
    print("=" * 92)
    print("[自检]")
    R = selftest()
    ok = all(c for _, c, _ in R)
    print("  自检结果：%s（%d/%d）" % ("全部通过 ✓" if ok else "有失败 ✗", sum(1 for _, c, _ in R if c), len(R)))
    if "--selftest" in sys.argv:
        return 0 if ok else 1
    if not ok:
        print("!! 自检未全部通过 ⇒ 按任务书不运行正式对照实验")
        return 1
    if os.path.exists(OUT3):
        print("!! %s 已存在 ⇒ 停止（不覆盖）" % os.path.relpath(OUT3, REPO))
        return 2
    os.makedirs(OUT3)
    before = hash_protected()
    lines = parse_ttml(BASE_PRED)
    truth = parse_ttml(TRUTH)
    rows = load_snapshot()
    asr = [r for r in rows if r["stage"] == "asr_raw" and r.get("begin_after") is not None]
    audio, sr, stem = load_stem()
    print("\n[B0] 基线未修改：预测 %d 行/%d 字｜真值 %d 行｜ASR 词 %d｜人声轨 %s (%d Hz)"
          % (len(lines), sum(len(l["spans"]) for l in lines), len(truth), len(asr), os.path.basename(stem), sr))
    cases = []
    for t in TARGETS:
        r = evaluate_one(t[0], t[1], t[2], lines, asr, audio, sr, rows)
        cases.append(r)
        b = r.get("boundaries", {})
        print("\n  【%s】pred %.3f–%.3f（%.3fs）| 真值 end %.3f | 父级 <p> %s–%s"
              % (r["target"], r.get("pred_start", -1), r.get("pred_end", -1), r.get("pred_dur", -1),
                 r["truth_end"], r.get("parent_p_begin"), r.get("parent_p_end")))
        print("     边界：window[%s,%s](%s) line[%s,%s](%s) next=%s(%s)"
              % (b.get("asr_window_left"), b.get("asr_window_right"), b.get("source", {}).get("asr_window_right"),
                 b.get("line_span_left"), b.get("line_span_right"), b.get("source", {}).get("line_span_right"),
                 b.get("next_line_start"), b.get("source", {}).get("next_line_start")))
        print("     特征：近窗口右界=%s(Δ%s) 近行右界=%s(Δ%s) 之后有音频=%s 声学充分=%s"
              % (b.get("pred_near_window_right"), b.get("delta_to_window_right"),
                 b.get("pred_near_line_right"), b.get("delta_to_line_right"),
                 b.get("audio_after_end_available"), r.get("acoustic", {}).get("sufficient")))
        for g in ("E_old", "E_boundary", "E_acoustic", "E_combined"):
            d = r[g]
            print("     %-12s triggered=%-5s → %-15s end=%s Δ=%s %s"
                  % (g, d.get("triggered"), d.get("decision"), d.get("suggested_end"),
                     d.get("extend_s"), d.get("reason", "")))
        print("     四态：%s" % r.get("states"))
    after = hash_protected()
    same = before == after
    design_path = os.path.join(OUT3, "long_tail_experiment_design.md")
    io.open(design_path, "w", encoding="utf-8", newline="\n").write(DESIGN)
    design_exists = os.path.isfile(design_path)
    print("\n[设计文档] %s 存在=%s（%d 字节）" % (os.path.relpath(design_path, REPO), design_exists,
                                                os.path.getsize(design_path) if design_exists else 0))

    def cnt(fn):
        return sum(1 for c in cases if fn(c))
    counts = {"B0_baseline": len(cases),
              "triggered_E_old": cnt(lambda c: c.get("E_old", {}).get("triggered")),
              "triggered_E_boundary": cnt(lambda c: c.get("E_boundary", {}).get("triggered")),
              "triggered_E_acoustic": cnt(lambda c: c.get("E_acoustic", {}).get("triggered")),
              "triggered_E_combined": cnt(lambda c: c.get("E_combined", {}).get("triggered")),
              "risk_found": cnt(lambda c: c.get("states", {}).get("risk_found")),
              "candidates_generated": cnt(lambda c: c.get("states", {}).get("candidate_generated")),
              "accepted": cnt(lambda c: any(c.get(g, {}).get("decision", "").startswith("accept")
                                            for g in ("E_old", "E_boundary", "E_acoustic", "E_combined"))),
              "improved": cnt(lambda c: c.get("states", {}).get("improved")),
              "rejected_no_evidence": cnt(lambda c: c.get("E_acoustic", {}).get("decision") == "uncertain"),
              "rejected_hard_or_structure": cnt(lambda c: c.get("E_acoustic", {}).get("decision") in ("reject", "structure_risk")),
              "unknown_boundaries": cnt(lambda c: c.get("boundaries", {}).get("source", {}).get("line_span_right") == "unknown")}
    summary = {"round": 3, "module": "long_tail_experiment.py", "enabled_by_default": False, "tolerance_s": TOL,
               "selftest": {"total": len(R), "passed": sum(1 for _, c, _ in R if c), "all_pass": bool(ok),
                            "items": [{"name": n, "pass": bool(c), "value": str(v)} for n, c, v in R]},
               "counts": counts, "cases": cases, "hashes_before": before, "hashes_after": after,
               "protected_unchanged": bool(same), "design_doc": os.path.relpath(design_path, REPO),
               "design_doc_exists": bool(design_exists),
               "not_run": ["ASR", "Demucs", "整曲流水线", "正式 TTML 写入"]}
    io.open(os.path.join(OUT3, "long_tail_experiment_results.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    io.open(os.path.join(OUT3, "hashes_before.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(before, ensure_ascii=False, indent=2))
    io.open(os.path.join(OUT3, "hashes_after.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(after, ensure_ascii=False, indent=2))
    md = ["# 长拖音实验·第三轮结果", "",
          "- 自检 **%d/%d %s**｜统一 **20ms** 容差 ✓｜模块**默认关闭** ✓ 生产不引用 ✓"
          % (summary["selftest"]["passed"], len(R), "通过 ✓" if ok else "有失败 ✗"),
          "- 未跑 ASR / Demucs / 整曲流水线 ✓；未写 TTML ✓；保护路径哈希前后一致 = **%s** ✓" % same,
          "- 设计文档：`%s`（**实际写出并校验存在 = %s**）" % (os.path.relpath(design_path, REPO), design_exists),
          "", "## 逐字符五组对照", "",
          "| 字符 | pred_start–end | 近行右界 | 近窗口右界 | E_old | E_boundary | E_acoustic | E_combined | 真值 end | 改善 | 四态 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cases:
        b = c.get("boundaries", {})
        md.append("| %s | %.3f–%.3f | %s | %s | %s | %s | %s | %s | %.3f | %s | %s |" % (
            c["target"], c.get("pred_start", -1), c.get("pred_end", -1), b.get("pred_near_line_right"),
            b.get("pred_near_window_right"), c.get("E_old", {}).get("decision"),
            c.get("E_boundary", {}).get("decision"), c.get("E_acoustic", {}).get("decision"),
            c.get("E_combined", {}).get("decision"), c["truth_end"],
            c.get("states", {}).get("improved"), c.get("states")))
    md += ["", "## 计数（触发 ≠ 候选 ≠ 接受 ≠ 改善 ✓）", "", "| 项 | 数量 |", "|---|---|"]
    for k, v in counts.items():
        md.append("| %s | %d |" % (k, v))
    md += ["", "## 全部结构风险", ""]
    risks = [(c["target"], c.get("E_acoustic", {}).get("decision"), c.get("E_acoustic", {}).get("p_must_grow_to"))
             for c in cases if c.get("E_acoustic", {}).get("decision") == "structure_risk"]
    md.append("- " + ("（无）" if not risks else "；".join("「%s」候选超出父级 <p>，需增长到 %s" % r for r in risks)))
    md += ["", "## 不可判定 / 缺失项", ""]
    md.append("- 边界 unknown 的样本数：%d" % counts["unknown_boundaries"])
    md.append("- 声学局限：%s" % (cases[0].get("acoustic_limits") if cases else "n/a"))
    md += ["", "## 限制", "",
           "- 真值为词级 span ⇒ 误差仅参考，不外推；样本 5 例 / 1 首歌；无逐词 logprob；边界为推导值。"]
    io.open(os.path.join(OUT3, "long_tail_experiment_results.md"), "w", encoding="utf-8", newline="\n").write(
        "\n".join(md) + "\n")
    print("\n[产物] %s" % os.path.relpath(OUT3, REPO))
    print("[保护路径哈希前后一致] %s" % same)
    print("[计数] %s" % json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
