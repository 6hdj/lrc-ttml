"""用 QQ 音乐的 .qrc 逐字时间戳当外部真值，对比 whisper 与 TIFA。

.qrc 格式：``[行开始ms,行时长ms]字(开始ms,时长ms)字(...)...``（绝对毫秒）。
指标：
  - 行结束偏差（我们 − QRC；负=提前结束）
  - 逐字起始/结束偏差
  - 去掉整体平移后的散布（不同录音常有整体偏移，散布才反映相对时间轴）

用法：
  python tools/eval_word_timing.py                 # 三首歌：whisper 基线 + TIFA(若存在)
  python tools/eval_word_timing.py --songs 失眠飞行
"""
import argparse
import io
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "https://ghproxy.net/https://raw.githubusercontent.com/amll-dev/amll-ttml-db/main/"
CACHE = ".tmp/wordlevel"
SONGS = {"让风告诉你": ("1818031620", "ncm"),
         "我不曾忘记": ("2014336709", "ncm"),
         "失眠飞行": ("1365898499", "ncm")}
DEFAULT_BASE = "outputs/eval"
DEFAULT_TIFA = "outputs/eval-tifa"


def fetch(pid, plat):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"{pid}.qrc")
    if os.path.exists(p):
        return io.open(p, encoding="utf-8", errors="replace").read()
    req = urllib.request.Request(f"{BASE}{plat}-lyrics/{pid}.qrc",
                                 headers={"User-Agent": "probe"})
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode("utf-8", "replace")
    io.open(p, "w", encoding="utf-8", newline="\n").write(t)
    return t


def parse_qrc(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"\[(\d+),(\d+)\](.*)$", line.strip())
        if not m:
            continue
        start, dur, body = int(m.group(1)), int(m.group(2)), m.group(3)
        words = [(w.group(1), int(w.group(2)), int(w.group(3)))
                 for w in re.finditer(r"([^\(\)]*)\((\d+),(\d+)\)", body)]
        txt = "".join(w[0] for w in words)
        if not txt.strip():
            continue
        out.append({"begin": start / 1000, "end": (start + dur) / 1000,
                    "text": txt, "units": [(t, b / 1000, (b + d) / 1000)
                                           for t, b, d in words]})
    return out


def local(t):
    return t.split("}", 1)[1] if "}" in t else t


def secs(t):
    p = [float(x) for x in str(t).split(":")]
    return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else (
        p[0] * 60 + p[1] if len(p) == 2 else p[0])


def parse_ours(path):
    root = ET.fromstring(io.open(path, encoding="utf-8-sig", errors="replace").read())
    out = []
    for p in root.iter():
        if local(p.tag) != "p":
            continue
        main = [s for s in p.iter() if local(s.tag) == "span"
                and "x-bg" not in (s.get("{http://www.w3.org/ns/ttml#metadata}role") or "")]
        txt = "".join("".join(s.itertext()) for s in main)
        if not txt.strip():
            continue
        out.append({"begin": secs(p.get("begin")), "end": secs(p.get("end")),
                    "text": txt,
                    "word_starts": [secs(s.get("begin")) for s in main],
                    "word_ends": [secs(s.get("end")) for s in main],
                    "word_texts": ["".join(s.itertext()) for s in main]})
    out.sort(key=lambda x: x["begin"])
    return out


def norm(s):
    return re.sub(r"[\s\(\)（）\[\]「」“”\"',，。、!！?？~～\-—_/\\|:：;；]", "", s or "")


def pair(qrc, ours):
    from difflib import SequenceMatcher
    res, used = [], set()
    for o in ours:
        best, bs = None, 0.0
        for j, q in enumerate(qrc):
            if j in used:
                continue
            r = SequenceMatcher(None, norm(o["text"]), norm(q["text"])).ratio()
            if r > bs:
                best, bs = j, r
        if best is not None and bs >= 0.6:
            used.add(best)
            res.append((o, qrc[best], bs))
    return res


def _expand(text, starts, ends):
    """把词级 span 展开成逐字（中文一字一个 span；英文按字符重复词起点）。"""
    chars = []
    for t, a, b in zip(text, starts, ends):
        for ch in t:
            if norm(ch):
                chars.append((ch, a, b))
    return chars


def evaluate(path, qrc, label, verbose=True):
    if not os.path.exists(path):
        if verbose:
            print(f"  {label:<14} 文件不存在，跳过（{path}）")
        return None
    ours = parse_ours(path)
    prs = pair(qrc, ours)
    if len(prs) < 5:
        print(f"  {label:<14} 配对不足（{len(prs)}），跳过")
        return None
    line_err = np.array([o["end"] - q["end"] for o, q, _ in prs])
    bad = np.abs(line_err) > 3.0
    line_ok = line_err[~bad]
    keep = [p for p, b in zip(prs, bad) if not b]

    starts, ends = [], []
    for o, q, _ in keep:
        oc = _expand(o["word_texts"], o["word_starts"], o["word_ends"])
        qc = []
        for u in q["units"]:
            for ch in u[0]:
                if norm(ch):
                    qc.append((ch, u[1], u[2]))
        for (ch_o, a_o, b_o), (ch_q, a_q, b_q) in zip(oc, qc):
            if ch_o == ch_q:
                starts.append(a_o - a_q)
                ends.append(b_o - b_q)
    s = np.array(starts)
    e = np.array(ends)
    s = s[np.abs(s) <= 3.0]
    e = e[np.abs(e) <= 3.0]
    s_med = float(np.median(s)) if len(s) else 0.0
    e_med = float(np.median(e)) if len(e) else 0.0
    s_dev = float(np.abs(s - s_med).mean()) if len(s) else 0.0
    e_dev = float(np.abs(e - e_med).mean()) if len(e) else 0.0
    within01 = float((np.abs(s - s_med) <= 0.1).mean() * 100) if len(s) else 0.0
    within02 = float((np.abs(s - s_med) <= 0.2).mean() * 100) if len(s) else 0.0
    line_dev = float(np.abs(line_ok - np.median(line_ok)).mean())
    row = {"label": label, "pairs": len(prs), "dropped": int(bad.sum()),
           "line_med": float(np.median(line_ok)), "line_dev": line_dev,
           "n": len(s), "start_med": s_med, "start_dev": s_dev,
           "end_med": e_med, "end_dev": e_dev,
           "within01": within01, "within02": within02}
    if verbose:
        print(f"  {label:<14} 配对 {row['pairs']:>3} 行（剔除 {row['dropped']}）"
              f" | 行结束 中位 {row['line_med']:+.3f}s 散布 {row['line_dev']:.3f}s"
              f" | 逐字起始 中位 {row['start_med']:+.3f}s 散布 {row['start_dev']:.3f}s"
              f" | 逐字结束 散布 {row['end_dev']:.3f}s"
              f" | ≤0.1s {row['within01']:.1f}% ≤0.2s {row['within02']:.1f}% (n={row['n']})")
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="QRC 真值评测：whisper vs TIFA")
    ap.add_argument("--songs", default="", help="逗号分隔，默认全部三首")
    ap.add_argument("--baseline-dir", default=DEFAULT_BASE)
    ap.add_argument("--tifa-dir", default=DEFAULT_TIFA)
    args = ap.parse_args(argv)
    songs = [s.strip() for s in args.songs.split(",") if s.strip()] or list(SONGS)
    rows = {}
    for song in songs:
        if song not in SONGS:
            print(f"未知歌曲：{song}")
            continue
        pid, plat = SONGS[song]
        print(f"\n【{song}】QRC 真值")
        qrc = parse_qrc(fetch(pid, plat))
        last_units = [q["units"][-1][2] - q["units"][-1][1] for q in qrc]
        print(f"  QRC {len(qrc)} 行，末单位时长：中位 {np.median(last_units):.2f}s"
              f" 最长 {max(last_units):.2f}s")
        base = evaluate(os.path.join(args.baseline_dir, f"{song}.ttml"), qrc, "whisper")
        tifa = evaluate(os.path.join(args.tifa_dir, f"{song}.ttml"), qrc, "TIFA")
        rows[song] = (base, tifa)
        if base and tifa:
            print(f"  → 逐字起始散布：whisper {base['start_dev']:.3f}s → "
                  f"TIFA {tifa['start_dev']:.3f}s；≤0.2s："
                  f"{base['within02']:.1f}% → {tifa['within02']:.1f}%")

    print("\n" + "=" * 78)
    print("汇总（逐字起始，去掉整体平移后）")
    print(f"{'歌曲':<10}{'whisper 散布':>14}{'TIFA 散布':>14}{'whisper ≤0.2s':>16}{'TIFA ≤0.2s':>14}")
    for song, (b, t) in rows.items():
        if not (b and t):
            continue
        print(f"{song:<10}{b['start_dev']:>13.3f}s{t['start_dev']:>13.3f}s"
              f"{b['within02']:>15.1f}%{t['within02']:>13.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
