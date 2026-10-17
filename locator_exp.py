"""定位判据实验（**只读**）—— 审核批准的"独立判据实验"阶段。

严格边界（用户审核结论）：
  * **不修改** `midword_sustain.py`（检测模块保持原样）；本文件是**独立实验**。
  * **不修改任何正式歌词时间**、不写文件、不接入 pipeline、不做影子修正。
  * 只评估**待验证的设计方向**，不宣称任何"定位准确率"（没有人工确认的真值）。
  * 无法可靠定位时返回 **待人工复核**，**不使用固定偏移 / 中点回退**。

待评估约束：
  R1 窗口边缘保护   —— 不接受紧贴 search_range 两端（margin 内）的候选
  R2 下降趋势验证   —— 候选附近必须有**持续、可重复**的能量下降（不是"低于阈值"就算）
  R3 候选可解释性   —— 输出局部曲线摘要 + 相对原边界偏移 + 触发原因
  R4 拒绝不确定     —— 不满足 R1/R2 ⇒ 明确"待人工复核"，不猜
用法：python locator_exp.py
"""
from __future__ import annotations

import glob
import io
import os
import sys
import wave

import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import midword_sustain as ms                      # noqa: E402
from ttml_reader import parse_ttml                # noqa: E402
from tail_extend import rms_envelope              # noqa: E402

TID = "1a79c31c92"
SR = 16000
WIN, HOP = 0.10, 0.02
SEARCH_BEFORE, SEARCH_AFTER = 0.30, 0.60
EDGE_MARGIN = 0.06          # R1：候选离窗口端点至少 3 帧
DROP_RATIO = 0.35           # R2：下降段相对前段的相对降幅
CONFIRM_FRAMES = 3          # R2：下降需连续确认帧数
NOISE_TOL = 0.02            # R2：允许的小幅反弹（相对 p75）


def load_stem(tid: str):
    """按"时长相关性 + 采样率 + 可读 + 最近修改"挑一条人声轨（不混用多条）。"""
    best = None
    for p in sorted(set(glob.glob(os.path.join(REPO, "outputs", "**", "*vocal*.wav"), recursive=True))):
        try:
            with wave.open(p, "rb") as w:
                sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
            if abs(n / sr - 295.8) > 3.0:
                continue
            mt = os.path.getmtime(p)
            if best is None or mt > best[3]:
                best = (p, sr, ch, mt)
        except Exception:
            continue
    if best is None:
        return None, 0, ""
    p, sr, ch, _mt = best
    with wave.open(p, "rb") as w:
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr, p


def curve(times, rms, a: float, b: float):
    m = (times >= a) & (times <= b)
    return times[m], rms[m]


def rule_r0(times, rms, boundary, lo, hi):
    """R0 = 现有判据（对照，保持原样）：窗口内首个"连续 N 帧低于 p75*(1-drop)"。"""
    t, v = curve(times, rms, lo, hi)
    if len(v) < 3:
        return None, "帧不足"
    thr = float(np.percentile(v, 75)) * 0.75
    run = 0
    for i, below in enumerate(v <= thr):
        run = run + 1 if below else 0
        if run >= 3:
            return float(t[i - run + 1]), f"R0 现有判据：连续 3 帧 < {thr:.6f}"
    return None, "R0 现有判据：窗口内无连续 3 帧低于阈值"


def rule_r1r2(times, rms, boundary, lo, hi):
    """R1+R2：先要求下降**趋势**，再要求候选不在窗口边缘；否则拒绝。"""
    t, v = curve(times, rms, lo, hi)
    if len(v) < 8:
        return None, "帧不足（<8）"
    p75 = float(np.percentile(v, 75))
    for i in range(2, len(v) - 1):
        tc = float(t[i])
        if tc - lo < EDGE_MARGIN or hi - tc < EDGE_MARGIN:      # R1 边缘保护
            continue
        pre = v[max(0, i - 8):i]
        post = v[i:i + 8]
        if len(pre) < 3 or len(post) < CONFIRM_FRAMES:
            continue
        pre_med = float(np.median(pre))
        post_med = float(np.median(post))
        if pre_med <= 1e-9:
            continue
        if (pre_med - post_med) / pre_med < DROP_RATIO:         # R2 需要足够降幅
            continue
        # 持续性：候选之后不能很快反弹回接近前段水平
        if len(post) and float(np.max(post)) > pre_med * (1 - DROP_RATIO) + NOISE_TOL * p75:
            continue
        return tc, (f"R1+R2：非边缘(距端 {tc-lo:.2f}/{hi-tc:.2f}s) "
                    f"pre_med={pre_med:.6f} → post_med={post_med:.6f} "
                    f"降幅 {(pre_med-post_med)/pre_med:.0%} ≥ {DROP_RATIO:.0%}，post 未反弹")
    return None, "R1+R2：窗口内没有「非边缘 + 持续下降」的位置 ⇒ 待人工复核"


def evaluate(boundary: float, times, rms, label: str):
    lo, hi = boundary - SEARCH_BEFORE, boundary + SEARCH_AFTER
    out = {"label": label, "boundary": boundary, "search_range": [round(lo, 3), round(hi, 3)]}
    t, v = curve(times, rms, lo, hi)
    if len(v):
        out["stats"] = {"frames": int(len(v)), "min": round(float(v.min()), 6),
                        "max": round(float(v.max()), 6), "median": round(float(np.median(v)), 6)}
        j = int(np.argmin(np.abs(t - boundary)))
        out["rms_at_boundary"] = round(float(v[j]), 6)
        out["rms_at_left"] = round(float(v[0]), 6)
        out["rms_at_right"] = round(float(v[-1]), 6)
    for name, fn in (("R0_现有判据", rule_r0), ("R1R2_边缘保护+趋势", rule_r1r2)):
        c, why = fn(times, rms, boundary, lo, hi)
        out[name] = {"candidate": None if c is None else round(c, 3),
                     "offset_vs_original": None if c is None else round(boundary - c, 3),
                     "reason": why}
    return out


def main() -> int:
    p = os.path.join(REPO, "outputs", TID + ".ttml")
    doc = parse_ttml(io.open(p, encoding="utf-8").read())
    audio, sr, apath = load_stem(TID)
    print("=" * 78)
    print("定位判据实验（只读；不修改任何正式数据；不接入 pipeline）")
    print("=" * 78)
    print(f"  人声轨：{os.path.relpath(apath, REPO) if apath else '（未找到 ⇒ 无法做 RMS 实验）'}")
    if audio is None:
        print("  ✗ 没有可用人声轨 ⇒ 实验无法执行（如实报告，不伪造）")
        return 2
    print(f"  采样率 {sr}Hz  时长 {len(audio)/sr:.3f}s")
    times, rms = rms_envelope(audio, sr, win=WIN, hop=HOP)
    print(f"  RMS：win={WIN}s hop={HOP}s（{int(HOP*1000)}ms/帧）")

    # ① 全部"句中"候选（复用检测模块，仅读取其结果；模块本身未改动）
    res = ms.redistribute_midword_sustain(doc.lines, audio=None)
    targets = []
    for c in res["candidates"]:
        if c.get("tail_excluded") or c.get("row_too_short"):
            continue
        if c.get("hit"):
            targets.append(c)
    print(f"\n  句中候选（检测模块给出，未改动该模块）：{len(targets)} 例")
    for c in targets:
        print(f"    第{c['row']}行[{c['index']}] {c['text']!r}  原 {c['begin']:.3f}→{c['end']:.3f} "
              f"({c['duration']:.3f}s)  中位×{c['ratio_median']:.2f} 前字×{c['ratio_prev']:.2f}")

    print("\n" + "-" * 78)
    print("逐例评估：R0（现有） vs R1+R2（边缘保护 + 下降趋势）")
    print("-" * 78)
    results = []
    for c in targets:
        r = evaluate(float(c["begin"]), times, rms, f"第{c['row']}行[{c['index']}] {c['text']!r}")
        results.append(r)
        print(f"\n  · {r['label']}   原边界 {r['boundary']:.3f}   search_range {r['search_range']}")
        print(f"    RMS 统计 {r.get('stats')}   边界处 {r.get('rms_at_boundary')}  "
              f"左端 {r.get('rms_at_left')}  右端 {r.get('rms_at_right')}")
        for k in ("R0_现有判据", "R1R2_边缘保护+趋势"):
            d = r[k]
            print(f"    {k}: 候选={d['candidate']}  偏移={d['offset_vs_original']}  依据={d['reason']}")

    # ② 汇总（只报候选与拒绝原因，不宣称准确率）
    n_r0 = sum(1 for x in results if x["R0_现有判据"]["candidate"] is not None)
    n_r12 = sum(1 for x in results if x["R1R2_边缘保护+趋势"]["candidate"] is not None)
    edge_r0 = sum(1 for x in results
                  if x["R0_现有判据"]["candidate"] is not None
                  and (x["R0_现有判据"]["candidate"] - x["search_range"][0] < EDGE_MARGIN
                       or x["search_range"][1] - x["R0_现有判据"]["candidate"] < EDGE_MARGIN))
    print("\n" + "=" * 78)
    print("汇总（**无人工确认真值 ⇒ 只报候选数/拒绝原因，不宣称准确率**）")
    print("=" * 78)
    print(f"  评估例数：{len(results)}")
    print(f"  R0 现有判据给出候选：{n_r0} 例；其中**位于窗口边缘**（< {EDGE_MARGIN}s）：{edge_r0} 例")
    print(f"  R1+R2 给出候选：{n_r12} 例；其余 {len(results)-n_r12} 例 ⇒ **待人工复核**（拒绝，不猜 ✓）")
    print("  ⚠️ 本实验仅 1 首歌、单一人声轨来源；样本不足以确定普适阈值（审核已指出 ✓）")
    print("  ⚠️ 未写入任何文件；未修改 midword_sustain.py / tail_extend.py / pipeline.py ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
