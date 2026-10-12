"""测量旁白的语音块边界（宣传片时间轴的真值来源）。

背景：宣传片每一段的"该什么时候切场景"取决于旁白实际读到哪。凭感觉排时间轴
必然出现"字还没说完就切走"。这个脚本给出一份**实测**的语音块表，
排时间轴时按它对齐。

做法（与 tools/verify_promo_frames.py 里验收用的检测器同一套判据，便于互相对照）：
* 20ms 帧 + Hann 窗，算 **2.5–8kHz 频段平均功率** —— 人声的擦音/齿音在这个频段，
  而合成的和弦垫音（正弦波）几乎没有，所以这一个判据就能把旁白从配乐里分出来；
* 噪声底 = 帧 dB 的 20 分位，阈值 = 噪声底 + 12dB；
* 合并容差 0.50s（⚠️ 不能更小：aac 编码会在某些句中间造出约 0.46s 的假凹陷，
  容差 0.35s 会把一句话切成两块 —— 验收报告里实测过）；
* 最短 0.30s，末端收口 -0.10s。

用法：
    python promo/tools/measure_narration.py .demo/narr2                  # 逐句 wav
    python promo/tools/measure_narration.py .demo/out/audio.wav --json   # 成片母带
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import wave

import numpy as np


def read_wav(path: str):
    with wave.open(path, "rb") as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"{path}: 只支持 16-bit PCM（当前 {sw * 8}-bit）")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def speech_blocks(a: np.ndarray, sr: int, merge_gap: float = 0.50,
                  min_dur: float = 0.30, tail: float = 0.10, thresh_db: float = 12.0):
    """返回 [(start, end), ...]（秒）。判据见模块 docstring。"""
    hop, win = int(0.02 * sr), int(0.04 * sr)
    n = max(1, (len(a) - win) // hop)
    if n <= 0:
        return []
    hi = np.zeros(n)
    w = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    band = (freqs > 2500) & (freqs < 8000)
    for k in range(n):
        seg = a[k * hop:k * hop + win] * w
        hi[k] = np.abs(np.fft.rfft(seg))[band].mean() ** 2
    db = 10 * np.log10(hi + 1e-12)
    floor = np.percentile(db, 20)
    on = db > floor + thresh_db

    tol, blocks, i = int(merge_gap / 0.02), [], 0
    while i < n:
        if not on[i]:
            i += 1
            continue
        j, last = i, i
        while j < n:
            if on[j]:
                last = j
            if j - last > tol:
                break
            j += 1
        s, e = i * hop / sr, max(i * hop, last * hop + win) / sr - tail
        if e - s > min_dur:
            blocks.append((round(s, 3), round(e, 3)))
        i = j
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="wav 文件，或装着 n*.wav 的目录")
    ap.add_argument("--merge-gap", type=float, default=0.50)
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    if os.path.isdir(args.target):
        files = sorted(glob.glob(os.path.join(args.target, "n*.wav")),
                       key=lambda p: int(os.path.basename(p)[1:-4]))
        if not files:
            raise SystemExit(f"{args.target} 里没找到 n*.wav")
    else:
        files = [args.target]

    out = []
    for p in files:
        a, sr = read_wav(p)
        blocks = speech_blocks(a, sr, args.merge_gap)
        dur = len(a) / sr
        out.append({"file": p, "duration": round(dur, 3), "blocks": blocks})
        if not args.json:
            speech = sum(e - s for s, e in blocks)
            print(f"{os.path.basename(p)}  时长 {dur:6.2f}s  语音 {speech:6.2f}s "
                  f"({speech / dur * 100:4.0f}%)  块 {len(blocks)}")
            for s, e in blocks:
                print(f"    {s:6.2f} → {e:6.2f}   ({e - s:5.2f}s)")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))

    if not args.json and len(out) > 1:
        total = sum(o["duration"] for o in out)
        speech = sum(e - s for o in out for s, e in o["blocks"])
        print(f"\n合计：{len(out)} 句，音频总长 {total:.2f}s，其中语音 {speech:.2f}s")
        print("排时间轴时：句子起点之间要留 ≥0.8s 换气，句内不要压 —— 详见 "
              "tools/demo_video/README-REMOTION.md §4 的对齐表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
