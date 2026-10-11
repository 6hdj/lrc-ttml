"""病例报告：定位某一行，把它的人声包络 + 基频 + 判据结论全部打出来。

用法：
    python tools/case_report.py <ttml> <行内关键词> [--stem 关键词]

为什么值得固化成工具：用户反复给"某首歌某一句尾字拖音被截断"的具体病例。
对着具体病例解剖比看统计量有效得多 —— 但分析时必须**统一时间轴**：
RMS 包络 hop=20ms、F0 帧 hop=10ms，用同一下标取两个数组会得出完全错误的
"稳定音高平台"（我自己踩过）。
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import tail_extend as T                      # noqa: E402
from diarize import HOP_, f0_track           # noqa: E402

SR = 16000


def local(t):
    return t.split("}", 1)[1] if "}" in t else t


def secs(t):
    p = [float(x) for x in str(t).split(":")]
    return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else (p[0] * 60 + p[1] if len(p) == 2 else p[0])


def parse(path):
    root = ET.fromstring(io.open(path, encoding="utf-8-sig", errors="replace").read())
    out = []
    for p in root.iter():
        if local(p.tag) != "p":
            continue
        spans = [s for s in p.iter() if local(s.tag) == "span"
                 and "x-bg" not in (s.get("{http://www.w3.org/ns/ttml#metadata}role") or "")]
        out.append({
            "begin": secs(p.get("begin")), "end": secs(p.get("end")),
            "agent": p.get("{http://www.w3.org/ns/ttml#metadata}agent"),
            "text": "".join("".join(s.itertext()) for s in spans),
            "words": [(secs(s.get("begin")), secs(s.get("end")),
                       "".join(s.itertext())) for s in spans],
        })
    out.sort(key=lambda x: x["begin"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ttml")
    ap.add_argument("keyword")
    ap.add_argument("--stem", default=None, help="人声轨文件名关键词（默认猜）")
    args = ap.parse_args()

    lines = parse(args.ttml)
    idx = next((i for i, L in enumerate(lines) if args.keyword in L["text"]), None)
    if idx is None:
        print(f"没找到含「{args.keyword}」的行")
        return 2
    L = lines[idx]
    nxt = lines[idx + 1] if idx + 1 < len(lines) else None

    print(f"【{args.keyword}】第 {idx + 1} 行  agent={L['agent']}  {L['text']}")
    print(f"  <p> {L['begin']:.3f} → {L['end']:.3f}")
    for b, e, t in L["words"]:
        print(f"    {t:<6} {b:>8.3f} → {e:>8.3f}  ({e - b:.3f}s)")
    if nxt:
        same = (nxt["agent"] or "") == (L["agent"] or "")
        print(f"\n下一行 第 {idx + 2} 行 agent={nxt['agent']}  {nxt['text']}")
        print(f"  <p> {nxt['begin']:.3f} → {nxt['end']:.3f}"
              f"   {'同歌手（不许重叠）' if same else '不同歌手（叠唱，允许重叠）'}")

    stem = args.stem
    cands = [p for p in glob.glob("outputs/*.vocals.wav")
             if (stem in os.path.basename(p)) if stem] or glob.glob("outputs/*.vocals.wav")
    if stem:
        cands = [p for p in glob.glob("outputs/*.vocals.wav") if stem in os.path.basename(p)]
    if not cands:
        print("\n（找不到人声轨，跳过音频分析）")
        return 0
    from faster_whisper.audio import decode_audio
    au = decode_audio(cands[0], sampling_rate=SR)

    last = L["words"][-1]
    T0 = last[0] - 0.5
    seg = au[int(T0 * SR):int((last[1] + 4.0) * SR)]
    rt, rms = T.rms_envelope(seg, SR, win=0.05, hop=0.02)     # hop 20ms
    f0, conf = f0_track(seg, SR)                              # hop 10ms
    ft = np.arange(len(f0)) * HOP_ + 0.02

    print(f"\n【「{last[2]}」之后】起点 {last[0]:.3f}（段内 {last[0] - T0:.3f}）")
    print(f"{'时间':>9} {'RMS':>9} {'F0':>7} {'conf':>6}  备注")
    for rel in np.arange(0.0, 3.0, 0.05):
        t = T0 + rel
        ri = int(np.argmin(np.abs(rt - rel)))
        fi = int(np.argmin(np.abs(ft - rel)))
        f = f0[fi] if fi < len(f0) else 0.0
        c = conf[fi] if fi < len(conf) else 0.0
        mark = ""
        if abs(t - last[1]) < 0.03:
            mark += " ★我们标的结束"
        if nxt and abs(t - nxt["begin"]) < 0.03:
            mark += " ★下一行开始"
        print(f"{t:>9.3f} {rms[ri]:>9.5f} {f:>7.1f} {c:>6.2f}  {mark}")

    ext_e = T.find_tail_end(seg, SR, last[1] - T0,
                            thr=max(0.25 * float(np.median(rms[(rt > 0.05) & (rt < 0.6)])), 1e-5),
                            max_extend=2.5, bridge=0.20, times=rt, rms=rms)
    ext_p = T.sustain_end(f0, HOP_, last[1] - T0, conf=conf, max_extend=2.5)
    print(f"\n【判据】能量 {ext_e:.3f}s（→{last[1] + ext_e:.3f}）"
          f" | 音高 {ext_p:.3f}s（→{last[1] + ext_p:.3f}）")
    print(f"  我们现在标的：{last[1]:.3f}"
          + (f"；下一行开始 {nxt['begin']:.3f}" if nxt else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
