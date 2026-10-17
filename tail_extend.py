"""句尾拖音补全 —— 把被硬切掉的尾音接回去。

## 问题（实测，不是推测）

`pipeline._line_span` 会把每行的结束无条件卡在下一行的开始：

```python
if nxt and nxt > lo:
    hard_end = min(hard_end, nxt)
```

于是歌手拖着长音、下一句已经起唱时，上一句的尾字被硬切。
在 4 首评估集上实测：**218 行里 51 处（24%）行尾卡在下一行开始**，
其中 **37/51（73%）边界处人声能量根本没下降** —— 声音是连着的，被切了。
另有一半以上的歌里出现尾字被压到 0.25s 以下。

## 做法

不改对齐模型，只在**对齐之后**做后处理（用户方案里的"后处理先行"）：

1. 从人声轨算滑动 RMS 包络；
2. 从该行最后一个主唱字的 end 往后走，只要能量还在阈值之上就继续延伸
   （允许短暂低于阈值，用 ``bridge`` 跨过换气/辅音的小凹陷）；
3. 按 agent 决定能延伸多远：
   * **同一个歌手**：不能超过下一行开始（TTML 规则 4：同 agent 的 `<p>` 不许重叠）；
   * **不同歌手**：**允许重叠** —— 这正是双人叠唱该有的样子（Apple 左右分栏靠不同
     `ttm:agent`，不同 agent 的 `<p>` 本来就可以重叠）。

## 为什么必须用人声轨

在原始混音上量"人声能量"会被伴奏带跑：间奏照样有能量，会把尾音一路延到间奏结束。
所以**只在有 Demucs 人声轨时才启用**；没有就跳过并在日志里说明。

## 验到什么程度（重要，别高估）

用 QQ 音乐的 `.qrc` 逐字时间戳当外部真值（`tools/eval_word_timing.py`）实测：

* **能站住的**：尾字被压扁是无歧义的错误，补全后显著改善 ——
  尾字 <0.25s 的行 **49 → 16**，<0.15s 的 **20 → 6**，中位尾字时长 0.23→0.42s。
* **站不住的**：**拖音长度无法外部验证**。因为 **QRC 自己也截断拖音** ——
  在 146 个 QRC 行结束点上量人声能量，**63% 没有下降**（我们自己的产物是 73%）。
  所以"更像 QRC"不等于"更对"，拿它否定本模块就是复制它的错误。
* **副作用**：以 QRC 为基准，去掉整体时间平移后的散布会变大（0.40→0.51s）。
  在尺子本身不干净的前提下，这个数字**不能**作为否定依据，但也不该假装它不存在。

结论：`short_thresh` 默认 0.30s，**只修明显不合理的短尾字**，不做无差别的能量延伸。
要彻底解决拖音长度，得换更根本的办法（放宽 ASR 窗口让模型自己看见尾音，
或用音高连续性判断"同一口气还在唱"），不是纯能量能搞定的。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

SR_DEFAULT = 16000


def rms_envelope(audio: np.ndarray, sr: int, win: float = 0.10,
                 hop: float = 0.02) -> Tuple[np.ndarray, np.ndarray]:
    """返回 (时间轴, RMS)。窗口 100ms / 步进 20ms。"""
    w, h = max(1, int(win * sr)), max(1, int(hop * sr))
    n = max(0, (len(audio) - w) // h + 1)
    if n <= 0:
        return np.zeros(0), np.zeros(0)
    idx = np.arange(w)[None, :] + (np.arange(n) * h)[:, None]
    frames = audio[idx]
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    times = (np.arange(n) * h + w / 2) / sr
    return times, rms


def _median_rms(audio: np.ndarray, sr: int, a: float, b: float,
                times: np.ndarray, rms: np.ndarray) -> float:
    m = (times >= a) & (times <= b)
    if not m.any():
        return 0.0
    return float(np.median(rms[m]))


def find_tail_end(audio: np.ndarray, sr: int, t0: float, *,
                  thr: float, max_extend: float, bridge: float,
                  times: Optional[np.ndarray] = None,
                  rms: Optional[np.ndarray] = None) -> float:
    """从 ``t0`` 往后走，返回能量跌落点（相对 t0 的延伸秒数）。"""
    if times is None or rms is None:
        times, rms = rms_envelope(audio, sr)
    if len(times) == 0:
        return 0.0
    i0 = int(np.searchsorted(times, t0))
    if i0 >= len(times):
        return 0.0
    limit = t0 + max_extend
    last_loud = t0
    dip_start = None
    for i in range(i0, len(times)):
        t = float(times[i])
        if t > limit:
            break
        if rms[i] >= thr:
            last_loud = t
            dip_start = None
        else:
            if dip_start is None:
                dip_start = t
            elif t - dip_start > bridge:
                break                      # 凹陷太久，判定真的唱完了
    return max(0.0, last_loud - t0)


def sustain_end(f0: np.ndarray, hop: float, t0: float, *,
                conf: Optional[np.ndarray] = None, min_conf: float = 0.55,
                tol_cents: float = 250.0, bridge: float = 0.30,
                max_extend: float = 2.5, min_extend: float = 0.08,
                smooth: float = 0.30, confirm: int = 3) -> float:
    """从 ``t0`` 起，只要**基频还在同一个音上**就继续延伸，返回延伸秒数。

    这是比纯能量更可靠的拖音判据 —— 纯能量分不清"上一句还在拖音"和
    "下一句已经起唱"（两者都是高能量、都"有声"）。但音高能分：

    * **拖长音（含转音）**：音高在一段时间内是**稳定的**（转音也只是缓慢游走）；
    * **新句子起唱**：逐字变化，音高在字与字之间大幅跳动。

    ⚠️ **必须用 ``conf`` 过滤野值**：``f0_track`` 会返回置信度，低置信帧经常
    给出八度错的假音高。实测病例〈我不曾忘记〉「膀」在 75.630 处被检成 421Hz
    （conf 仅 0.39，真值 ~235Hz），音分差 +902 → 直接误判"换音了"而停止延伸。
    丢掉 conf 是这个 bug 的根因。

    规则：与**最近 ``smooth`` 秒内已确认音高的中位值**比，偏离超过 ``tol_cents``
    就判定离开这个音；短暂无声（换气/辅音/漏帧）允许用 ``bridge`` 跨过。
    滚动中位让音高可以**缓慢漂移**（转音），但拒绝突变。
    """
    if f0 is None or len(f0) == 0:
        return 0.0
    hop = max(1e-3, float(hop))
    i0 = max(0, int(round((t0 - 0.02) / hop)))
    limit_i = min(len(f0), int(round((t0 + max_extend) / hop)) + 1)
    if i0 >= limit_i:
        return 0.0

    recent: List[float] = []          # 最近已确认的音高（cents）
    last_ok = t0
    first_voiced = None
    bad_run = 0
    # ⚠️ 参考音高要**攒够 min_ref 帧**才能用来判"离开这个音"。
    # 只有 1~2 帧时，第一帧本身可能就是野值（真踩过：〈我不曾忘记〉「逢」的
    # 150.650 帧被检成 153.8Hz 而 conf=0.56 刚好过门槛 → 参考被污染成 153.8，
    # 后面真实的 228.6 全被判成"跳出音域"，拖音直接变成 0）。
    # 中位数要 ≥3 个样本才抗单个野值，所以这里取 5。
    min_ref = max(3, confirm)
    for i in range(i0, limit_i):
        t = t0 + (i - i0) * hop
        f = float(f0[i])
        if f <= 0 or (conf is not None and i < len(conf) and conf[i] < min_conf):
            if t - last_ok > bridge:
                break                  # 长时间没有可信音高 → 唱完了
            continue
        c = 1200.0 * float(np.log2(f / 110.0))       # 任意参考音高，只看差值
        if first_voiced is None:
            first_voiced = t
        if len(recent) >= min_ref:
            ref = float(np.median(recent[-max(1, int(smooth / hop)):]))
            if abs(c - ref) > tol_cents:
                # ⚠️ **必须连续 ``confirm`` 帧都跳出音域才算结束**：
                # 单个低置信野值不该终止一个音。实测病例：整曲计算时那个
                # 421Hz 八度错的帧 conf 恰好 0.50，卡在门槛上通过，
                # 音分差 +1111 → 直接把「膀」的拖音判成 0。
                # 同样的音频切片段算时它的 conf 是 0.39 —— 靠单一门槛太脆。
                bad_run += 1
                if bad_run >= confirm:
                    break
                continue
            bad_run = 0
        recent.append(c)
        last_ok = t
    if first_voiced is None or first_voiced - t0 > 0.12:
        return 0.0
    ext = last_ok - t0
    return ext if ext >= min_extend else 0.0


def extend_line_tails(lines: Sequence, audio: np.ndarray, sr: int = SR_DEFAULT, *,
                      max_extend: float = 2.5, min_extend: float = 0.06,
                      rel_thr: float = 0.25, bridge: float = 0.20,
                      cross_agent_max: float = 1.5,
                      last_line_max: float = 8.0,
                      short_thresh: Optional[float] = 0.30,
                      use_pitch: bool = True,
                      allow_same_agent_overlap: bool = False,
                      log=None) -> List[dict]:
    """就地延伸每行的尾音。返回改动清单（便于日志/复核）。

    ``lines``：``AlignedLine`` 序列（有 ``begin/end/agent/units``）。
    ``audio``：**人声轨**（不是原始混音）。

    ⚠️ ``short_thresh``：**只修"尾字短得明显不合理"的行**（默认 <0.30s）。
    为什么加这个限制 —— 实测教训：不加限制地对所有行做能量延伸，
    用 QQ 音乐 .qrc 逐字真值衡量，**去掉整体时间平移后的散布反而变差**
    （让风告诉你 0.396s→0.649s，失眠飞行 0.227s→0.260s）。
    原因是纯能量分不清"上一句还在拖音"和"下一句已经起唱"，
    对本来就正常的行会补过头。而"尾字只有 0.1s"是**无歧义的错误**
    （再快的中文咬字也不会一个音节 0.1 秒），只修这一批既治了真病，
    又不会把正常行改坏。
    """
    fixes: List[dict] = []
    if audio is None or len(audio) < sr // 2:
        return fixes
    times, rms = rms_envelope(audio, sr)
    if len(times) == 0:
        return fixes
    f0 = hop = None
    conf = None
    if use_pitch:
        try:
            from diarize import HOP_, f0_track
            f0, conf = f0_track(np.asarray(audio, dtype=np.float32), sr)
            hop = HOP_
            if len(f0) == 0:
                f0 = conf = None
        except Exception:
            f0 = conf = None

    for i, line in enumerate(lines):
        try:
            line.overlap_with_next = False   # 复位：防重入/重跑留下陈旧标记
        except Exception:
            pass
        units = [u for u in (line.units or [])
                 if not getattr(u, "is_bg", False) and u.end is not None
                 and u.start is not None]
        if not units:
            continue
        last = max(units, key=lambda u: u.end)
        t0 = float(last.end)
        dur = t0 - float(last.start)

        nxt = lines[i + 1] if i + 1 < len(lines) else None
        same_agent = (nxt is not None
                      and (getattr(nxt, "agent", None) or "")
                      == (getattr(line, "agent", None) or ""))

        # ⚠️ short_thresh 只是**能量判据**的兜底：纯能量分不清"上一句拖音"和
        # "下一句起唱"，无差别延伸会把正常行补过头（实测 QRC 散布 0.396→0.649）。
        # **换成音高判据后就不需要这个门槛了** —— 音高能分清两者，
        # 而且被压扁的字（<0.30s）和**没被压扁但拖得很长**的字（如病例里的「膀」
        # 0.58s 却拖了 1.4s）都能正确延伸。
        if not use_pitch and short_thresh is not None and dur >= short_thresh:
            continue

        # 该行自己的响度水平 → 相对阈值（避免不同歌/不同段落的绝对音量差异）
        line_rms = _median_rms(audio, sr, line.begin, line.end, times, rms)
        if line_rms <= 1e-6:
            continue
        thr = max(rel_thr * line_rms, 1e-5)

        nxt = lines[i + 1] if i + 1 < len(lines) else None
        # ⚠️ **末行（没有下一行）要允许长得多的拖音**：没有"下一行"来约束，
        # 能量的自然跌落就是终点。实测《空心》结尾「把我当成一棵树吧」的「吧」
        # 拖了 **7.35s**（255.67→263.03），被 max_extend=2.5 硬生生截到 258.17，
        # 再加上下面的短字门槛，最后只留了 0.46s。
        cap = last_line_max if nxt is None else max_extend

        ext_e = find_tail_end(audio, sr, t0, thr=thr, max_extend=cap,
                              bridge=bridge, times=times, rms=rms)
        ext_p = (sustain_end(f0, hop, t0, conf=conf, max_extend=cap,
                             bridge=max(bridge, 0.30)) if f0 is not None else 0.0)

        # 判据选择：
        # * **末行（没有下一行）**：没有"下一行"会被盖掉，能量的自然跌落就是终点，
        #   两个判据取 max。实测〈空心〉结尾「把我当成一棵树吧」的「吧」拖了
        #   **7.35s**（255.67→263.03，能量到 262.67 才掉），而音高判据在那个
        #   Live 录音上因**八度乱跳**只给 0.08s —— 只能靠能量。
        # * 有下一行：音高判据优先（它能分清"上一句拖音"和"下一句起唱"，
        #   能量做不到 —— 实测「膀」用能量会被延到 2.28s，吞掉 v2 整句）。
        if nxt is None:
            ext = max(ext_e, ext_p)
        elif f0 is not None and ext_p >= min_extend:
            ext = ext_p
        elif allow_same_agent_overlap and same_agent:
            # ⚠️ 收紧（2026-10-17）：**同歌手 opt-in 只接受音高判据**，禁止能量兜底。
            # 能量分不清"还在拖音"和"下一句已起唱"（两者都是高能量）。实测 L56
            # 「吗 在某一天」：音高在 +0.10s 已跳到另一个音（ext_p=0），却因为尾字
            # 0.207s<short_thresh 落到能量兜底 → 延伸 1.50s（撞 1.5s 上限），
            # 完整盖住下一行前 3 个字 —— 旧结论里"能量会吞下一句"的复现。
            # 所以 opt-in 下同歌手的边只信"音高在同一口气上"，没有音高证据就不延长；
            # 跨歌手、末行、以及默认路径（flag=False）都不走这个分支。
            ext = 0.0
        elif f0 is None:
            ext = ext_e
        elif short_thresh is not None and dur < short_thresh:
            ext = ext_e
        else:
            ext = 0.0
        if ext < min_extend:
            continue

        hard = t0 + cap
        overlap = False
        if nxt is not None:
            same_agent = (getattr(nxt, "agent", None) or "") == (getattr(line, "agent", None) or "")
            if same_agent and not allow_same_agent_overlap:
                hard = min(hard, float(nxt.begin))      # 同歌手：不许重叠（旧行为）
            elif same_agent:
                # 实验开关：允许**单人连唱/拖音的跨行重叠**
                # （收紧后只有音高判据能走到这里；见上面的判据选择）
                # 上限仍受 cross_agent_max 约束，避免无限吞掉下一句
                hard = min(t0 + ext, float(nxt.begin) + cross_agent_max)
            else:
                # 不同歌手：允许重叠（双人叠唱），但不能盖掉下一行太多
                hard = min(hard, float(nxt.begin) + cross_agent_max,
                           float(nxt.end) if nxt.end else hard)

        new_end = min(t0 + ext, hard)
        if new_end - t0 < min_extend:
            continue
        # 边级证据语义**统一为"实际越过下一行 begin"**（同歌手/跨歌手同一规则）：
        # 只有真的越界才置 overlap。旧实现里跨歌手分支无条件 overlap=True，
        # 导致 summarize()/日志把"允许重叠"误报成"发生了重叠"（计数虚高）。
        overlap = bool(nxt is not None and new_end > float(nxt.begin) + 1e-6)
        if overlap:
            try:
                line.overlap_next_begin = float(nxt.begin)
            except Exception:
                pass

        old = t0
        last.end = new_end
        try:
            line.overlap_with_next = bool(overlap)   # 边级证据：(本行, 下一行) 这一对允许重叠
        except Exception:
            pass
        if line.end < new_end:
            line.end = new_end
        fixes.append({"line": i, "text": getattr(line, "text", ""),
                      "agent": getattr(line, "agent", ""),
                      "old_end": old, "new_end": new_end,
                      "extend": new_end - old, "overlap": overlap})
        if log:
            log(f"　　[尾音] L{i + 1}「{getattr(line, 'text', '')[-6:]}」"
                f"{old:.3f}→{new_end:.3f}（+{new_end - old:.3f}s"
                f"{'，与下一行重叠' if overlap else ''}）")
    return fixes


def summarize(fixes: Sequence[dict]) -> str:
    if not fixes:
        return "没有需要补的尾音"
    ov = sum(1 for f in fixes if f["overlap"])
    tot = sum(f["extend"] for f in fixes)
    return (f"补了 {len(fixes)} 处尾音，共 {tot:.2f}s"
            f"（平均 {tot / len(fixes):.2f}s/处"
            f"{'，其中 ' + str(ov) + ' 处与下一行时间重叠（需人工复核是否侵占了下一句）' if ov else ''}）")
