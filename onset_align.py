"""首字起音对齐 —— 只修"文字还没唱、进度已经走了"的偏早行。

## 为什么是单向的

实测（对着**音频**量，不是对着 QRC）：133 行里能判定起音上升沿的 56 行中，
首字偏差中位只有 **+0.030s**，**延后的行（36%）比提前的（14%）还多**。
所以"起始系统性前置"并不成立。

但对 QRC 的对比又显示我们整条时间轴比 QQ 早约 0.2s（同一录音的那首歌）——
两者不矛盾：**QQ 的显示时间本身就比真实起音晚**。我们比 QQ 早，但比音频准。

⇒ 因此这里**只做单向修正**：首字明显早于真实起音才拉后，绝不往后推。
如果做成双向对齐，会去动那 36% 本来就偏后的行，反而更糟。

## 与 tail_extend 的关系

尾音补全管行尾，这里管行首，两者都依赖**分离人声轨**（原始混音里伴奏也会起音）。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from tail_extend import SR_DEFAULT, rms_envelope


def detect_onset(times: np.ndarray, rms: np.ndarray, t0: float, *,
                 level_span: Optional[Tuple[float, float]] = None,
                 back: float = 0.40, fwd: float = 0.60) -> Optional[float]:
    """在 t0 附近找**起音上升沿**（能量从明显低于本行水平抬到明显高于）。

    ⚠️ 不能只判"第一个超过阈值的点"：上一句拖音/伴奏还在响时，窗口内从头就高于阈值，
    那样会永远返回窗起点（实测这个 bug 让三首歌的中位偏差都恰好等于窗长 0.595s）。
    必须要求**前一点低于阈值**，即真的看到上升沿；看不到就返回 None（无法判定）。

    ⚠️ ``fwd`` 必须 >= ``snap_line_starts(max_shift=0.5)``，否则偏早超过 fwd 的行
    根本够不着起音点。

    ⚠️ **电平必须按 ``level_span``（该行自己的跨度）量，不能用检测窗的中位数**：
    一个偏早 0.5s 的行，检测窗里大半是静音，窗口中位数≈0 → 会被误判成
    "这一段没人声"而跳过，正好漏掉最该修的情况（真踩过）。
    """
    m = (times >= t0 - back) & (times <= t0 + fwd)
    if m.sum() < 8:
        return None
    st, sr = times[m], rms[m]

    if level_span:
        lm = (times >= level_span[0]) & (times <= level_span[1])
        ref = rms[lm] if lm.sum() >= 5 else sr
    else:
        ref = sr
    lvl = float(np.median(ref))
    floor = float(np.percentile(sr, 10))
    if lvl <= 1e-6 or lvl < floor * 2.0:
        return None
    thr = floor + 0.40 * (lvl - floor)
    quiet = floor * 1.5
    for i in range(1, len(st)):
        if sr[i] >= thr and sr[i - 1] < quiet:
            return float(st[i])
    return None


def snap_line_starts(lines: Sequence, audio: np.ndarray, sr: int = SR_DEFAULT, *,
                     min_early: float = 0.12, max_shift: float = 0.50,
                     log=None) -> List[dict]:
    """把明显早于真实起音的首字拉后。**只拉后，不推后。**

    ``min_early``：早于起音不到这个量就不动（避免抖动）。
    ``max_shift``：单行最多拉后这么多（防止起音检测失败时把行推飞）。
    """
    fixes: List[dict] = []
    if audio is None or len(audio) < sr // 2:
        return fixes
    times, rms = rms_envelope(audio, sr, win=0.05, hop=0.01)
    if len(times) == 0:
        return fixes

    for i, line in enumerate(lines):
        units = [u for u in (line.units or [])
                 if not getattr(u, "is_bg", False) and u.start is not None]
        if not units:
            continue
        first = min(units, key=lambda u: u.start)
        t0 = float(first.start)
        # 电平按**该行自己的跨度**量（不是检测窗）—— 见 detect_onset 的说明
        on = detect_onset(times, rms, t0,
                          level_span=(float(line.begin), float(line.end or line.begin + 1.0)))
        if on is None:
            continue
        early = on - t0                      # >0 表示我们比真实起音早
        if early < min_early:
            continue
        shift = min(early, max_shift)

        # ⚠️ 移动首字时必须夹住**第二个字的开始**，否则首字会压上去 →
        # `字时间重叠`（真踩过：失眠飞行一次报了 6 个这种错）。
        others = [u.start for u in units if u is not first and u.start is not None]
        limit = min([float(getattr(line, "end", 0.0) or 0.0) or (t0 + 10.0)] + others)
        new_start = t0 + shift
        new_end = min((first.end or new_start) + shift, limit)
        if new_end - new_start < 0.03:
            continue                         # 挤不下就别改，宁可不修也不要造坏文件

        first.start, first.end = new_start, new_end
        if line.begin < first.start:
            line.begin = first.start
        fixes.append({"line": i, "text": getattr(line, "text", ""),
                      "old": t0, "new": first.start, "shift": shift})
        if log:
            log(f"　　[起音] L{i + 1}「{(getattr(line, 'text', '') or '')[:8]}」"
                f"首字 {t0:.3f}→{first.start:.3f}（拉后 {shift:.3f}s）")
    return fixes


def summarize(fixes: Sequence[dict]) -> str:
    if not fixes:
        return "没有需要拉后的起音"
    tot = sum(f["shift"] for f in fixes)
    return f"首字起音对齐 {len(fixes)} 处，共拉后 {tot:.2f}s（平均 {tot / len(fixes):.2f}s/处）"
