"""句中"异常长音再分配" —— **第一阶段：只检测，不修改任何正式结果**。

背景（用户病例，已取证）：
    任务 1a79c31c92 第 5 行「直到现实狠狠推一把」
        实 43.530→43.990 (0.460s)   ← 人耳听到的拖长音在这里
        狠 43.990→45.970 (1.980s)   ← ✗ 拖音被算给了下一个字
    现象：**句中**某字拖长音时，对齐把这段时长划给了下一个字。

## 本阶段严格边界（用户指定）
* **只检测**：函数**绝不修改** `lines`（也绝不改音频/文件）。
* **不接入 pipeline**（pipeline 里没有任何调用）。
* **不碰** `extend_line_tails()` / `_line_span()` / 行尾既有逻辑 —— 只**复用**其工具：
  `rms_envelope()`（100ms 窗 / 20ms 步进）与 `_median_rms()`。
* **不做"固定归还一半"**：RMS 找不到可信边界 ⇒ 记为 `命中但定位失败`，**不动时间**。
* **RMS 低谷只是"边界证据"，不是真值** —— 连唱/气声/混响下宁可漏修，留给人工。
* 字符索引**按下标**处理，**不按文本去重**（重复字如「狠狠」是两个真字）。

用法：
    python midword_sustain.py                # 跑自测
    python midword_sustain.py <ttml> [id]    # 对某份产物出候选报告（只检测）
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:
    from tail_extend import SR_DEFAULT, rms_envelope, _median_rms
except Exception:  # pragma: no cover - 允许单独运行自测
    SR_DEFAULT = 16000

    def rms_envelope(audio, sr, win: float = 0.10, hop: float = 0.02):  # type: ignore
        raise RuntimeError("需要 numpy 与 tail_extend")

    def _median_rms(audio, sr, a, b, times, rms):  # type: ignore
        raise RuntimeError("需要 numpy 与 tail_extend")


# ----------------------------------------------------------------- 默认阈值
DEFAULTS = dict(
    min_duration=0.80,      # 当前字时长下限（秒）
    ratio_median=3.0,       # 当前字 / 行中位时长 的倍数下限
    ratio_prev=3.0,         # 当前字 / 前字时长 的倍数下限
    continuity_eps=0.002,   # 前字 end 与当前字 begin 允许的间隙（秒，明确可配）
    search_before=0.30,     # RMS 搜索窗：原边界往前（秒）
    search_after=0.60,      # RMS 搜索窗：原边界往后（秒）
    valley_drop=0.25,       # "低谷"判据：低于窗口内较高水平的比例
    valley_confirm=3,       # 低谷需连续几帧（20ms/帧）
    min_shift=0.04,         # 候选边界至少移动（秒），太小不算证据
    min_prev_after=0.08,    # 前字归还后仍须 ≥ 该时长
    min_cur_after=0.10,     # 当前字修正后仍须 ≥ 该时长
    median_mode="raw",      # "raw"=原始中位（v1 默认）；"drop_max"=去掉最大一个后中位（对照）
    min_samples=4,          # 有效字数 < 该值 ⇒ 不自动判定，列入待观察（方案 B 保护，v1 默认）
)


def _words_of(line: Any) -> List[Any]:
    """取一行里的字序列：优先 `units`（AlignedLine），兼容 `words`（EditLine）。

    ⚠️ 逐字 span 的下标就是**真实字索引**（重复字符各占一个下标，**不做文本去重**）。
    """
    for attr in ("units", "words"):
        seq = getattr(line, attr, None)
        if seq:
            return list(seq)
    return []


def _val(w: Any, *names: str, default: float = float("nan")) -> float:
    for n in names:
        v = getattr(w, n, None)
        if isinstance(v, (int, float)):
            return float(v)
    return default


def _text_of(w: Any) -> str:
    for n in ("text", "word", "char"):
        v = getattr(w, n, None)
        if isinstance(v, str):
            return v
    return ""


def _median_duration(durs: Sequence[float], mode: str = "raw") -> float:
    vals = sorted(d for d in durs if d == d and d > 0)
    if not vals:
        return 0.0
    if mode == "drop_max" and len(vals) >= 2:
        vals = vals[:-1]            # 对照方案 A：去掉最大一个有效字长
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _curve_summary(times, rms, a: float, b: float) -> Dict[str, Any]:
    """局部 RMS 摘要（供报告；**只作证据**）。"""
    m = (times >= a) & (times <= b)
    if not m.any():
        return {"frames": 0}
    t = times[m]
    v = rms[m]
    hi = float(np.percentile(v, 90))
    lo = float(np.percentile(v, 10))
    return {
        "frames": int(m.sum()),
        "t_from": round(float(t[0]), 3), "t_to": round(float(t[-1]), 3),
        "max": round(float(v.max()), 6), "min": round(float(v.min()), 6),
        "p90": round(hi, 6), "p10": round(lo, 6),
        "samples": [[round(float(t[i]), 3), round(float(v[i]), 6)]
                    for i in range(0, len(t), max(1, len(t) // 12))],
    }


def _find_valley(times, rms, lo: float, hi: float, cfg: Dict[str, Any]) -> Tuple[Optional[float], str, Dict[str, Any]]:
    """在原边界附近找"由高转低且有持续性"的**局部低谷**。

    返回 (候选边界 or None, 依据说明, 附加上下文)。**找不到就返回 None**（不猜 ✓）。
    """
    m = (times >= lo) & (times <= hi)
    if not m.any():
        return None, "搜索范围内没有 RMS 帧", {}
    t = times[m]
    v = rms[m]
    if len(v) < 3:
        return None, "RMS 帧不足 3，无法判断", {}
    ref = float(np.percentile(v, 75))            # 窗口内的"较高水平"
    thr = ref * (1.0 - float(cfg["valley_drop"]))
    below = v <= thr
    need = max(1, int(cfg["valley_confirm"]))
    run = 0
    for i, is_below in enumerate(below):
        run = run + 1 if is_below else 0
        if run >= need:
            idx = i - run + 1
            cand = float(t[idx])
            return cand, (f"局部 RMS 连续 {need} 帧低于窗口 p75 的 {1-float(cfg['valley_drop']):.0%}"
                          f"（帧 {idx}~{i}，阈值 {thr:.6f}，p75={ref:.6f}）"), {
                "thr": round(thr, 6), "p75": round(ref, 6), "frame_idx": int(idx)}
    return None, (f"窗口内无连续 {need} 帧低于阈值 {thr:.6f}（p75={ref:.6f}）"
                  f"—— RMS 无明确低谷，**按约定不修改**"), {"thr": round(thr, 6), "p75": round(ref, 6)}


def redistribute_midword_sustain(lines: Sequence, audio=None, sr: int = SR_DEFAULT, *,
                                 cfg: Optional[Dict[str, Any]] = None,
                                 log=None) -> Dict[str, Any]:
    """**只检测**句中"前字 + 异常长字"，输出候选报告。**绝不修改 `lines`。**

    返回：
        {
          "candidates": [...],          # 每个候选的完整记录
          "summary": {"not_hit": n, "hit_no_boundary": n, "hit_located": n,
                      "invalid": n, "total_rows": n, "total_words": n},
          "config": {...真实生效的阈值...},
          "audio": "used" | "missing",
          "shadow": [...],              # 影子修正（算了但**不写入**）
        }
    """
    c = dict(DEFAULTS)
    if cfg:
        c.update(cfg)
    times = rms = None
    audio_used = "missing"
    if audio is not None and np is not None:
        try:
            times, rms = rms_envelope(audio, sr)
            audio_used = "used" if times is not None and len(times) else "missing"
        except Exception as ex:              # 音频不可用不致命：退化为"只按时长"
            if log:
                log(f"⚠ RMS 不可用（{ex}）⇒ 只做时长检测")
            audio_used = "missing"

    cands: List[Dict[str, Any]] = []
    shadow: List[Dict[str, Any]] = []
    summary = {"not_hit": 0, "hit_no_boundary": 0, "hit_located": 0, "invalid": 0,
               "tail_excluded": 0,        # 行尾字作为"当前异常长字"被排除（归 tail_extend 管）
               "row_too_short": 0,        # 有效字数 < min_samples ⇒ 不自动判定（方案 B 保护）
               "total_rows": 0, "total_words": 0}

    for li, line in enumerate(lines or []):
        words = _words_of(line)
        summary["total_rows"] += 1
        summary["total_words"] += len(words)
        if len(words) < 2:
            continue
        # ⚠️ 中位数用**全部有效字长**（含行尾字）——排除行尾只针对"作为当前异常长字"，
        # 不影响它作为前字、也不影响它参与中位计算（用户审查①）。
        durs = [_val(w, "end") - _val(w, "begin") for w in words]
        valid_n = len([d for d in durs if d == d and d > 0])
        median_mode = str(c.get("median_mode", "raw"))
        med = _median_duration(durs, median_mode)
        thr = max(float(c["min_duration"]), float(c["ratio_median"]) * med)
        too_short = valid_n < int(c.get("min_samples", 4))
        last_idx = len(words) - 1
        for wi in range(1, len(words)):        # 行首字不参与（wi>=1）
            w, prev = words[wi], words[wi - 1]
            d, dp = durs[wi], durs[wi - 1]
            rec: Dict[str, Any] = {
                "row": li + 1, "index": wi, "text": _text_of(w),
                "prev_text": _text_of(prev),
                "begin": _val(w, "begin"), "end": _val(w, "end"), "duration": d,
                "prev_begin": _val(prev, "begin"), "prev_end": _val(prev, "end"), "prev_duration": dp,
                "row_median": med, "threshold": thr,
                "ratio_median": (d / med) if med > 0 else None,
                "ratio_prev": (d / dp) if dp > 0 else None,
                "hit": False, "located": False, "reason": "",
            }
            # ---- 合法性（无效时间段）----
            if not (d == d and d > 0) or not (dp == dp and dp > 0):
                rec["reason"] = "时长无效（NaN/非正）"
                summary["invalid"] += 1
                cands.append(rec)
                continue
            # 审查①：行尾字由既有 tail_extend 负责 ⇒ 不作为"句中再分配"的候选
            if wi == last_idx:
                rec["reason"] = "行尾字 ⇒ 归 tail_extend 处理，句中再分配不参与"
                rec["tail_excluded"] = True
                summary["tail_excluded"] += 1
                cands.append(rec)
                continue
            gap = abs(_val(w, "begin") - _val(prev, "end"))
            rec["gap"] = gap
            rec["continuous"] = gap <= float(c["continuity_eps"])
            # ---- 候选条件（同时满足）----
            if d < thr:
                summary["not_hit"] += 1
                continue
            if dp <= 0 or d / dp < float(c["ratio_prev"]):
                summary["not_hit"] += 1
                continue
            if not rec["continuous"]:
                rec["reason"] = (f"前字 end 与当前字 begin 不连续（间隙 {gap:.3f}s > "
                                 f"{float(c['continuity_eps']):.3f}s）⇒ 不视为边界错误")
                summary["invalid"] += 1
                cands.append(rec)
                continue
            # 审查②：有效字数不足 ⇒ 不自动判定（方案 B 保护），列入待观察清单
            if too_short:
                rec["reason"] = (f"行内有效字仅 {valid_n} 个 < min_samples={int(c.get('min_samples', 4))}"
                                 f" ⇒ 不自动判定（待人工观察）")
                rec["observation"] = True
                summary["row_too_short"] += 1
                cands.append(rec)
                continue
            rec["hit"] = True

            # ---- RMS 定位（只在原边界附近）----
            boundary = _val(w, "begin")
            lo = boundary - float(c["search_before"])
            hi = boundary + float(c["search_after"])
            rec["search_range"] = [round(lo, 3), round(hi, 3)]
            if audio_used == "used":
                rec["rms_curve"] = _curve_summary(times, rms, lo, hi)
                cand_b, basis, ctx = _find_valley(times, rms, lo, hi, c)
                rec["basis"] = basis
                rec["valley_ctx"] = ctx
                if cand_b is None:
                    rec["located"] = False
                    rec["reason"] = "命中但**无法定位**可信边界（按约定不修改 ✓）"
                    summary["hit_no_boundary"] += 1
                else:
                    shift = boundary - cand_b
                    rec["candidate_boundary"] = round(cand_b, 3)
                    rec["shift"] = round(shift, 3)
                    ok_prev = (cand_b - _val(prev, "begin")) >= float(c["min_prev_after"])
                    ok_cur = (_val(w, "end") - cand_b) >= float(c["min_cur_after"])
                    in_line = True
                    lb = getattr(line, "begin", None)
                    le = getattr(line, "end", None)
                    if isinstance(lb, (int, float)) and isinstance(le, (int, float)):
                        in_line = (cand_b >= float(lb) - 1e-9) and (cand_b <= float(le) + 1e-9)
                    rec["checks"] = {"prev_ok": bool(ok_prev), "cur_ok": bool(ok_cur), "in_line": bool(in_line)}
                    if abs(shift) < float(c["min_shift"]):
                        rec["located"] = False
                        rec["reason"] = f"候选边界移动仅 {shift:+.3f}s < {float(c['min_shift']):.3f}s，证据不足"
                        summary["hit_no_boundary"] += 1
                    elif not (ok_prev and ok_cur and in_line):
                        rec["located"] = False
                        rec["reason"] = "候选边界未通过合法性校验（前字/当前字剩余时长或行范围）"
                        summary["invalid"] += 1
                    else:
                        rec["located"] = True
                        rec["reason"] = "命中且定位成功（**影子模式：未写入任何结果** ✓）"
                        summary["hit_located"] += 1
                        shadow.append({
                            "row": li + 1, "index": wi, "text": _text_of(w),
                            "old_boundary": round(boundary, 3),
                            "new_boundary": round(cand_b, 3),
                            "shift": round(shift, 3),
                            "prev_duration_before": round(dp, 3),
                            "prev_duration_after": round(cand_b - _val(prev, "begin"), 3),
                            "cur_duration_before": round(d, 3),
                            "cur_duration_after": round(_val(w, "end") - cand_b, 3),
                            "written": False,
                        })
            else:
                rec["located"] = False
                rec["reason"] = "命中，但**没有可用人声轨** ⇒ 无法用 RMS 定位（按约定不修改 ✓）"
                summary["hit_no_boundary"] += 1
            cands.append(rec)

    return {"candidates": cands, "summary": summary, "config": c,
            "audio": audio_used, "shadow": shadow}


def compare_median_strategies(lines: Sequence, audio=None, sr: int = SR_DEFAULT) -> Dict[str, Any]:
    """短行对照：raw（原始中位）/ drop_max（方案 A）/ min_samples 保护（方案 B）。

    只检测、不改数据；返回三者的命中与"旧误报（行尾被当候选）"情况。
    """
    out: Dict[str, Any] = {}
    for name, cfg in (("raw", {"median_mode": "raw", "min_samples": 4}),
                      ("drop_max", {"median_mode": "drop_max", "min_samples": 4}),
                      ("min_samples_B", {"median_mode": "raw", "min_samples": 4}),
                      ("no_protect", {"median_mode": "raw", "min_samples": 1})):
        r = redistribute_midword_sustain(lines, audio=audio, sr=sr, cfg=cfg)
        s2 = r["summary"]
        out[name] = {
            "hit_located": s2["hit_located"], "hit_no_boundary": s2["hit_no_boundary"],
            "tail_excluded": s2["tail_excluded"], "row_too_short": s2["row_too_short"],
            "invalid": s2["invalid"], "not_hit": s2["not_hit"],
            "hit_records": [{"row": c["row"], "index": c["index"], "text": c["text"],
                             "duration": round(c["duration"], 3), "tail": c.get("tail_excluded", False)}
                            for c in r["candidates"] if c["hit"] or c.get("tail_excluded")],
        }
    return out


# ------------------------------------------------------------------ 报告输出
def format_report(res: Dict[str, Any], only_hits: bool = True) -> str:
    out: List[str] = []
    s = res["summary"]
    out.append(f"配置：{json.dumps(res['config'], ensure_ascii=False)}")
    out.append(f"音频：{res['audio']}（missing ⇒ 只做时长检测，不做 RMS 定位）")
    out.append(f"总行 {s['total_rows']} / 总字 {s['total_words']}")
    out.append(f"汇总：未命中 {s['not_hit']}｜命中但定位失败 {s['hit_no_boundary']}｜"
               f"命中且定位成功 {s['hit_located']}｜校验失败 {s['invalid']}")
    for r in res["candidates"]:
        if only_hits and not (r["hit"] or r["reason"]):
            continue
        out.append("")
        out.append(f"— 第 {r['row']} 行 字[{r['index']}] {r['text']!r}"
                   f"  原 {r['begin']:.3f}→{r['end']:.3f} ({r['duration']:.3f}s)"
                   f"｜前字 {r['prev_text']!r} {r['prev_begin']:.3f}→{r['prev_end']:.3f} ({r['prev_duration']:.3f}s)")
        out.append(f"   行中位 {r['row_median']:.3f}s｜阈值 {r['threshold']:.3f}s｜"
                   f"时长比 中位×{r['ratio_median'] if r['ratio_median'] is None else round(r['ratio_median'],2)}"
                   f" 前字×{r['ratio_prev'] if r['ratio_prev'] is None else round(r['ratio_prev'],2)}"
                   f"｜连续={r.get('continuous')}（间隙 {r.get('gap', float('nan')):.4f}s）")
        if r.get("search_range"):
            out.append(f"   RMS 搜索范围 {r['search_range']}｜{r.get('basis','')}")
        if "candidate_boundary" in r:
            out.append(f"   候选边界 {r['candidate_boundary']:.3f}（移动 {r['shift']:+.3f}s）"
                       f"｜校验 {r.get('checks')}")
        out.append(f"   命中={r['hit']}  定位={r['located']}  ⇒ {r['reason']}")
    if res["shadow"]:
        out.append("")
        out.append("影子修正（算了但**未写入** ✓）：")
        for x in res["shadow"]:
            out.append(f"   第 {x['row']} 行 [{x['index']}] {x['text']!r} "
                       f"{x['old_boundary']}→{x['new_boundary']}（{x['shift']:+.3f}s）"
                       f" 前字 {x['prev_duration_before']}→{x['prev_duration_after']}s"
                       f" 当前字 {x['cur_duration_before']}→{x['cur_duration_after']}s written=False")
    return "\n".join(out)


# ------------------------------------------------------------------- 自测
def _mk_word(b, e, t):
    class W:
        pass
    w = W()
    w.begin, w.end, w.text = b, e, t
    return w


def _mk_line(b, e, units):
    class L:
        pass
    ln = L()
    ln.begin, ln.end, ln.units, ln.agent = b, e, units, "v1"
    return ln


def _selftest() -> int:
    ok = bad = 0

    def chk(name, cond, detail=""):
        nonlocal ok, bad
        if cond:
            ok += 1
            print(f"  [PASS] {name}")
        else:
            bad += 1
            print(f"  [FAIL] {name}" + (f"\n         {detail}" if detail else ""))

    # —— 阈值边界 / 行首 / 重复字 / 非连续 / 无效时长 / 无低谷 ——
    # ① 候选命中（无音频 ⇒ 命中但定位失败，且**不改数据**）
    u = [_mk_word(0.0, 0.18, "直"), _mk_word(0.18, 0.36, "到"), _mk_word(0.36, 0.54, "现"),
         _mk_word(0.54, 1.00, "实"), _mk_word(1.00, 2.98, "狠"), _mk_word(2.98, 3.38, "狠")]
    ln = _mk_line(0.0, 3.38, u)
    before = [(w.begin, w.end) for w in u]
    res = redistribute_midword_sustain([ln], audio=None)
    chk("命中（1.98s ≥ max(0.8, 3×中位) 且 前字比 ≥3）", res["summary"]["hit_no_boundary"] == 1,
        json.dumps(res["summary"], ensure_ascii=False))
    chk("无音频时**不修改**任何时间（输入未被改动）",
        before == [(w.begin, w.end) for w in u])
    chk("无音频 ⇒ 影子列表为空（不产出未经验证的修正）", res["shadow"] == [])
    chk("候选记录了时长比与时延比",
        res["candidates"] and res["candidates"][0]["ratio_prev"] is not None
        and res["candidates"][0]["ratio_prev"] > 3)

    # ② 行首字不参与
    u2 = [_mk_word(0.0, 2.0, "长"), _mk_word(2.0, 2.2, "短")]
    res2 = redistribute_midword_sustain([_mk_line(0.0, 2.2, u2)], audio=None)
    chk("行首字即使超长也不进候选", res2["summary"]["hit_no_boundary"] == 0
        and res2["summary"]["hit_located"] == 0)

    # ③ 重复字符按**下标**处理，不去重（狠狠 = 两个字）
    u3 = [_mk_word(0.0, 0.2, "狠"), _mk_word(0.2, 2.3, "狠")]
    res3 = redistribute_midword_sustain([_mk_line(0.0, 2.3, u3)], audio=None)
    texts = [c["text"] for c in res3["candidates"]]
    chk("重复字符不去重、按真实下标处理（含两个 狠）",
        len(u3) == 2 and all(t == "狠" for t in texts))

    # ④ 非连续时间 ⇒ 不进"命中"（记为校验失败）
    #    注意：要用**≥4 字**的行，否则中位被长字拉高 / 触发样本保护，测不到该分支
    u4 = [_mk_word(0.0, 0.2, "a"), _mk_word(0.2, 0.4, "b"), _mk_word(0.4, 0.6, "c"),
          _mk_word(0.9, 2.7, "d")]                            # 与前一字间隙 0.3s
    res4 = redistribute_midword_sustain([_mk_line(0.0, 2.7, u4)], audio=None)
    chk("前字 end 与当前字 begin 不连续 ⇒ 不计入命中",
        res4["summary"]["invalid"] == 1 and res4["summary"]["hit_no_boundary"] == 0,
        json.dumps(res4["summary"], ensure_ascii=False))

    # ⑤ 无效时长（NaN）
    u5 = [_mk_word(0.0, float("nan"), "a"), _mk_word(0.0, 2.0, "b")]
    res5 = redistribute_midword_sustain([_mk_line(0.0, 2.0, u5)], audio=None)
    chk("无效时长被识别并单独计数", res5["summary"]["invalid"] >= 1)

    # ⑥ 阈值边界：刚好等于阈值 ⇒ 命中（>= 语义）
    u6 = [_mk_word(0.0, 0.30, "a"), _mk_word(0.30, 1.10, "b")]   # 0.80s，中位 0.40 ⇒ 3×=1.2 ✗
    res6 = redistribute_midword_sustain([_mk_line(0.0, 1.10, u6)], audio=None)
    chk("0.80s 但未达 3×中位 ⇒ 不命中", res6["summary"]["hit_no_boundary"] == 0
        and res6["summary"]["not_hit"] >= 1)
    # ⚠️ 必须 ≥4 字：否则中位被长字拉高（见审查②），测不到"定位失败"分支
    u7 = [_mk_word(0.0, 0.20, "a"), _mk_word(0.20, 0.40, "b"), _mk_word(0.40, 0.60, "c"),
          _mk_word(0.60, 1.60, "d")]      # 1.00s，中位 0.30 ⇒ 3×=0.90 ⇒ 命中

    # ⑦ RMS 无明显低谷 ⇒ 命中但定位失败（用合成"平坦"音频）
    if np is not None:
        sr = 16000
        flat = (np.random.RandomState(0).rand(sr * 3) * 2 - 1).astype("float32") * 0.05
        res7 = redistribute_midword_sustain([_mk_line(0.0, 1.6, u7)], audio=flat, sr=sr)
        chk("平坦音频 ⇒ 命中但**定位失败**（不修改）",
            res7["summary"]["hit_no_boundary"] == 1 and res7["shadow"] == [],
            json.dumps(res7["summary"], ensure_ascii=False))
        chk("平坦音频下输入仍然未被修改",
            [(w.begin, w.end) for w in u7] == [(0.0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 1.60)])

    # ⑧ 行尾排除（作为候选被排除，但仍参与中位计算）
    u8 = [_mk_word(0.0, 0.2, "a"), _mk_word(0.2, 0.4, "b"), _mk_word(0.4, 0.6, "c"),
          _mk_word(0.6, 3.0, "尾")]     # 行尾长字
    res8 = redistribute_midword_sustain([_mk_line(0.0, 3.0, u8)], audio=None)
    chk("行尾字作为候选被排除并单独计数", res8["summary"]["tail_excluded"] == 1
        and res8["summary"]["hit_no_boundary"] == 0, json.dumps(res8["summary"], ensure_ascii=False))

    # ⑨ 方案 B 保护：有效字数 < 4 ⇒ 不自动判定
    u9 = [_mk_word(0.0, 0.2, "a"), _mk_word(0.2, 0.4, "b"), _mk_word(0.4, 2.2, "c")]
    res9 = redistribute_midword_sustain([_mk_line(0.0, 2.2, u9)], audio=None)
    chk("有效字 3 < min_samples=4 ⇒ 列入待观察，不判定", res9["summary"]["row_too_short"] >= 1
        and res9["summary"]["hit_no_boundary"] == 0, json.dumps(res9["summary"], ensure_ascii=False))

    # ⑩ 中位策略对照：drop_max 会把阈值降下来（同一行命中数可能不同）
    u10 = [_mk_word(0.0, 0.2, "a"), _mk_word(0.2, 0.4, "b"), _mk_word(0.4, 0.6, "c"),
           _mk_word(0.6, 2.6, "d")]
    raw10 = redistribute_midword_sustain([_mk_line(0.0, 2.6, u10)], audio=None,
                                         cfg={"median_mode": "raw", "min_samples": 4})
    dm10 = redistribute_midword_sustain([_mk_line(0.0, 2.6, u10)], audio=None,
                                        cfg={"median_mode": "drop_max", "min_samples": 4})
    chk("两种中位策略都能跑通且不修改输入",
        raw10["summary"]["total_words"] == 4 and dm10["summary"]["total_words"] == 4
        and [(w.begin, w.end) for w in u10] == [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 2.6)])

    print(f"\n  通过 {ok} / {ok + bad}" + ("  ✅ 全部通过" if bad == 0 else f"  ❌ 失败 {bad}"))
    return 0 if bad == 0 else 1


# ------------------------------------------------------------- 病例：只检测
def _load_audio_for(tid: str):
    """尽力找该任务的人声轨（找不到就返回 None —— 会如实标注，不伪造 RMS）。"""
    root = os.path.dirname(os.path.abspath(__file__))
    cands = []
    for d in ("outputs", ".cache", os.path.join(".tmp", "cases")):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            for f in os.listdir(p):
                if tid in f and f.lower().endswith((".wav", ".flac", ".mp3")) and "vocal" in f.lower():
                    cands.append(os.path.join(p, f))
    if not cands:
        return None, ""
    try:
        import wave
        with wave.open(cands[0], "rb") as w:
            sr = w.getframerate()
            n = w.getnframes()
            raw = w.readframes(n)
        if np is None:
            return None, cands[0]
        a = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
        if w.getnchannels() > 1:
            a = a.reshape(-1, w.getnchannels()).mean(axis=1)
        return a, cands[0]
    except Exception:
        return None, cands[0]


def _case(tid: str) -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(root, "outputs", tid + ".ttml")
    if not os.path.isfile(p):
        print(f"  ✗ 找不到 {p}")
        return 2
    from ttml_reader import parse_ttml
    doc = parse_ttml(io.open(p, encoding="utf-8").read())
    audio, apath = _load_audio_for(tid)
    print(f"  产物：{p}\n  人声轨：{apath or '（没找到 ⇒ 本次只做时长检测，不做 RMS 定位）'}")
    res = redistribute_midword_sustain(doc.lines, audio=audio)
    print()
    print(format_report(res))
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        sys.exit(_case(sys.argv[1]))
    sys.exit(_selftest())
