"""用 QQ 音乐的 .qrc 逐字时间戳当**外部真值**，客观评我们的字级打轴。

.qrc 格式：``[行开始ms,行时长ms]字(开始ms,时长ms)字(...)...``
时间都是**绝对毫秒**。最后那个单位的时长往往很长（1~2s），
因为商业平台把**拖音算进尾字时长** —— 这正是我们被截断的地方。

指标：``我们的行结束 − QRC 的行结束``（秒）。为负 = 我们提前结束（被截断）。
"""
import io
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, ".")
# 注意：脚本在 tools/ 下，要回到项目根，否则 outputs/... 找不到
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = "https://ghproxy.net/https://raw.githubusercontent.com/amll-dev/amll-ttml-db/main/"
CACHE = ".tmp/wordlevel"
SONGS = {"让风告诉你": ("1818031620", "ncm"),
         "我不曾忘记": ("2014336709", "ncm"),
         "失眠飞行": ("1365898499", "ncm")}


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
        # 组：1=字，2=开始ms，3=时长ms
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
    return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else (p[0] * 60 + p[1] if len(p) == 2 else p[0])


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
                    "last_end": max(secs(s.get("end")) for s in main),
                    # 逐字起始 + 原文，供"起始前置"对比
                    "word_starts": [secs(s.get("begin")) for s in main],
                    "word_texts": ["".join(s.itertext()) for s in main]})
    out.sort(key=lambda x: x["begin"])
    return out


def norm(s):
    return re.sub(r"[\s\(\)（）\[\]「」“”\"',，。、!！?？~～\-—_/\\|:：;；]", "", s or "")


def pair(qrc, ours):
    """按文本相似度配对（两边行数可能不同）"""
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


def report(name, path, qrc):
    if not os.path.exists(path):
        return None
    ours = parse_ours(path)
    prs = pair(qrc, ours)
    if len(prs) < 5:
        print(f"  {name}: 配对不足（{len(prs)}），跳过")
        return None
    d = np.array([o["end"] - q["end"] for o, q, _ in prs])
    # ⚠️ 必须剔除配对失败的离群值：文本相似的重复段（副歌）会被配错行，
    # 产生几十秒的假偏差，把均值整个带跑。中位数本身抗离群，均值不行。
    bad = np.abs(d) > 3.0
    d_ok = d[~bad]
    keep = [p for p, b in zip(prs, bad) if not b]
    # ⚠️ 关键诊断：如果偏差几乎是**常数**，那是"录音不同导致的整体时间偏移"，
    # 不是我们的打轴错。这里把中位偏移减掉，看**去掉整体平移后的散布**。
    # 散布小 = 我们的相对时间轴是对的；散布大 = 才是真的打轴不准。
    spread = d_ok - np.median(d_ok)

    # 逐字**起始**偏差（用户反馈的问题 1：起始时间前置）
    # 只比 QRC 里是**单字单位**的位置（多字单位只有一个起始时间，没法逐字比）
    starts = []
    for o, q, _ in keep:
        qi = 0
        for ch in norm(o["text"]):
            if qi >= len(q["units"]):
                break
            u = q["units"][qi]
            if len(u[0]) == 1:
                # 找我们这一行里同序号的字
                if qi < len(o["word_starts"]):
                    starts.append(o["word_starts"][qi] - u[1])
                qi += 1
            else:
                qi += len(u[0])
    s = np.array(starts) if starts else np.array([])
    s_ok = s[np.abs(s) <= 3.0] if len(s) else s
    stxt = ""
    if len(s_ok):
        stxt = (f" | **逐字起始** 中位 {np.median(s_ok):+.3f}s"
                f" 去掉整体平移后散布 {np.abs(s_ok - np.median(s_ok)).mean():.3f}s"
                f"（n={len(s_ok)}）")

    print(f"  {name:<14} 配对 {len(prs):>3} 行（剔除 {int(bad.sum())}）"
          f" | 行结束 中位 {np.median(d_ok):+.3f}s"
          f" 散布 {np.abs(spread).mean():.3f}s{stxt}")
    return d_ok


for song, (pid, plat) in SONGS.items():
    print(f"\n【{song}】QRC 真值")
    qrc = parse_qrc(fetch(pid, plat))
    last_units = [q["units"][-1][2] - q["units"][-1][1] for q in qrc]
    print(f"  QRC {len(qrc)} 行，末单位时长：中位 {np.median(last_units):.2f}s"
          f" 最长 {max(last_units):.2f}s")
    before = report("改前", f"outputs/eval/{song}.ttml", qrc)
    after = report("改后(尾音补全)", f"outputs/eval/{song}.tail.ttml", qrc)
    if before is not None and after is not None:
        print(f"  → 中位偏差 {np.median(before):+.3f}s → {np.median(after):+.3f}s；"
              f"提前结束的行 {int((before < -0.15).sum())} → {int((after < -0.15).sum())}")
