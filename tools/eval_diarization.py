"""跨歌曲的「分声部」客观评估（固定协议）。

## 为什么要有这个

单首歌上把 3 个错行调对，不能证明方法变好 —— 那是过拟合。
所以这里固定一套**跨歌曲**的评估：人工真值来自 AMLL TTML DB（社区审核过的
`ttm:agent` 标注），音频来自用户已上传的文件，两者用歌词文本重合度确认是同一版本。

## 评估协议（改模型/改算法前后都必须跑同一套，不许挑指标）

1. **真值来源**：AMLL TTML DB 的 `<p ttm:agent>`。**按歌曲划分**，同一首歌不既当
   开发集又当验证集。
2. **基线固定**：整行 embedding + 原始混音 + k-means(k=2)，即当前生产路径。
   候选模型只换 embedding 提取那一步，其余（跨度、聚类、评估代码）一律不动。
3. **指标**（全部要报，不许只报总准确率）：
   * 总体逐行一致率
   * **每歌手召回率** —— 某个歌手召回明显低 = 他的发声状态被拆进了另一簇
   * **高音独唱 / 普通独唱** 分组准确率（按该行 F0 相对本歌中位数分高低）
   * **连续错行段**数量与长度（错行成段出现说明是段落级问题，不是逐行噪声）
   * 混淆矩阵（谁被当成了谁）
4. **采用标准（事先约定，事后不许改）**：
   * 总体准确率提升 < 2 个百分点 → 不换；
   * 提升只来自 1 首歌 → 不换（要看每首歌的最小提升）；
   * 任一歌的总体准确率下降 > 3 个百分点 → 不换；
   * 必须同时改善目标错误类型（本次是"某歌手被系统性判错"），且不显著损害普通独唱。
   四条全过才考虑替换生产模型。

## 用法

    python tools/eval_diarization.py              # 全部跑（缺产物会先自动转换）
    python tools/eval_diarization.py --only 让风告诉你
    python tools/eval_diarization.py --no-run     # 只用已有产物重算指标
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

SR = 16000
EVAL_DIR = os.path.join(HERE, "outputs", "eval")
GT_DIR = os.path.join(HERE, ".tmp", "db_ttml")

# 歌曲清单：人工真值来自 AMLL TTML DB；音频/歌词是用户已上传的同一版本
# （版本一致性由歌词 2-gram 重合度确认，见 _pair_check 的输出：0.94~0.98）
SONGS = [
    {"name": "在出发之前", "gt": "ncm-lyrics__2124643510",
     "audio": "uploads/1791543877_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav",
     "lyrics": "uploads/1791543884_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.lrc",
     "title": "在出发之前", "artists": ["陶典", "hanser"]},
    {"name": "失眠飞行", "gt": "ncm-lyrics__1365898499",
     "audio": "uploads/1791473992_失眠飞行 - KBShinya&hanser.wav",
     "lyrics": "uploads/1791474015_失眠飞行 - KBShinya&hanser.lrc",
     "title": "失眠飞行", "artists": ["KBShinya", "hanser"],
     "note": "⚠ 版本存疑：DB 那条的署名是「接个吻，开一枪/沈以诚/薛黛霏」，"
             "音频是 KBShinya&hanser 的版本。歌词 2-gram 重合 0.98 说明行结构一致，"
             "但换人翻唱时 v1/v2 的角色分配未必沿用原唱 —— "
             "所以这首歌的数字只能当参考，报总体时要同时给「含它 / 不含它」两个数。"},
    {"name": "我不曾忘记", "gt": "ncm-lyrics__2014336709",
     "audio": "uploads/1791545307_花玲 _ 张安琪 _ 沐霏 - 我不曾忘记.flac",
     "lyrics": "uploads/1791545313_花玲 _ 张安琪 _ 沐霏 - 我不曾忘记.lrc",
     "title": "我不曾忘记", "artists": ["花玲", "张安琪", "沐霏"]},
    {"name": "让风告诉你", "gt": "ncm-lyrics__1818031620",
     "audio": "uploads/1791546257_花玲 _ 喵酱油 _ 宴宁 _ Kinsen - 让风告诉你.flac",
     "lyrics": "uploads/1791546269_花玲 _ 喵酱油 _ 宴宁 _ Kinsen - 让风告诉你.lrc",
     "title": "让风告诉你", "artists": ["花玲", "喵酱油", "宴宁", "Kinsen"]},
]


SONG_NOTE = {s["name"]: s["note"] for s in SONGS if s.get("note")}


def _local(t: str) -> str:    return t.split("}", 1)[1] if "}" in t else t


def _secs(t: str) -> float:
    p = [float(x) for x in str(t).split(":")]
    return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else (p[0] * 60 + p[1] if len(p) == 2 else p[0])


def load_lines(path: str) -> List[dict]:
    """读一份 TTML 的每行：时间 + agent + 文本（不含和声）"""
    root = ET.fromstring(io.open(path, encoding="utf-8-sig", errors="replace").read())
    out = []
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        main, bg = [], []
        for s in p:
            if _local(s.tag) != "span":
                continue
            role = (s.get("{http://www.w3.org/ns/ttml#metadata}role") or "").lower()
            (bg if "x-bg" in role else main).append("".join(s.itertext()))
        out.append({
            "begin": _secs(p.get("begin") or "0"),
            "end": _secs(p.get("end") or "0"),
            "agent": p.get("{http://www.w3.org/ns/ttml#metadata}agent") or "",
            "text": "".join("".join(main).split()),
            "has_bg": bool(bg),
        })
    out.sort(key=lambda x: x["begin"])
    return out


def get_audio(song: dict) -> Optional[str]:
    p = song["audio"]
    return p if os.path.exists(p) else None


def ensure_output(song: dict, run: bool) -> Optional[str]:
    """跑一遍生产路径，产物放 outputs/eval/<歌>.ttml（存在就复用）"""
    os.makedirs(EVAL_DIR, exist_ok=True)
    out = os.path.join(EVAL_DIR, f"{song['name']}.ttml")
    if os.path.exists(out):
        return out
    if not run:
        return None
    audio = get_audio(song)
    if audio is None:
        print(f"   ✗ 找不到音频：{song['audio']}")
        return None
    cmd = [sys.executable, "main.py", "--audio", audio, "--lyrics", song["lyrics"],
           "--output", out, "--title", song["title"],
           "--auto-agents", "--separate-vocals"]
    for a in song["artists"]:
        cmd += ["--artist", a]
    print(f"   … 转换 {song['name']}（约 1 分钟）")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if not os.path.exists(out):
        print("     ✗ 失败：" + (r.stdout or r.stderr or "")[-400:])
        return None
    return out


def pair(mine: List[dict], gt: List[dict]) -> List[Tuple[int, int, float]]:
    """按时间重叠把我们的行与真值行配对（一行只配一次）"""
    pairs = []
    used = set()
    for i, m in enumerate(mine):
        best, bov = None, 0.0
        for j, g in enumerate(gt):
            if j in used:
                continue
            ov = min(m["end"], g["end"]) - max(m["begin"], g["begin"])
            if ov > bov:
                best, bov = j, ov
        if best is not None and bov > 0:
            used.add(best)
            pairs.append((i, best, bov))
    return pairs


def line_f0(audio: np.ndarray, a: float, b: float) -> float:
    import diarize
    seg = audio[int(a * SR):int(b * SR)]
    if len(seg) < SR // 2:
        return 0.0
    v, _ = diarize.f0_track(seg, SR)
    v = v[(v > 70) & (v < 1000)]
    return float(np.median(v)) if len(v) else 0.0


def _embed_lines(song: dict, spans: List[Tuple[float, float]], embedder: str,
                 mode: str, source: str = "mix", variant: str = "byol"):
    """按 ``embedder`` 给每行提一个声纹向量。

    返回 ``(E, valid)``：``E`` 只含有效行的向量，``valid`` 是它们对应的行下标。
    有些行太短（<0.2s）提不出向量，**不能因此丢掉整首歌** —— 只在这些行上不评分。

    除这一步之外，跨度、聚类、配对、评分**一律不变** —— 这是"只换模型"的纪律。

    ``source="vocals"`` 用 Demucs 隔离人声轨。**这不是"只换模型"**：
    它同时换了输入条件。列出来是为了回答"这个模型是不是只在干净人声上才行"
    （它的训练数据就是隔离人声轨），报告时必须和 ``mix`` 分开看。
    """
    audio_path = song["audio"]
    if source == "vocals":
        import glob as _glob
        stem = _glob.glob(os.path.join(HERE, "outputs", "*.vocals.wav"))
        stem = [p for p in stem if song["name"] in os.path.basename(p)]
        if not stem:
            return None, []
        audio_path = stem[0]

    if embedder == "campplus":
        import diarize
        from asr import WhisperASR
        audio = WhisperASR(log=lambda *a: None).load_audio(audio_path)
        vs = [diarize.line_embedding(audio, b, e) for b, e in spans]
    elif embedder.startswith("ssl-singer"):
        import singer_ssl as S
        audio = S.load_wave_44k(audio_path)
        vs = [S.embed_span(audio, b, e, mode=mode, variant=variant) for b, e in spans]
    else:
        raise SystemExit(f"未知 embedder：{embedder}")
    valid = [i for i, v in enumerate(vs) if v is not None]
    if len(valid) < 5:
        return None, []
    return np.stack([vs[i] for i in valid]), valid


def eval_song(song: dict, run: bool, embedder: str = "campplus",
              mode: str = "whole", source: str = "mix", variant: str = "byol",
              verbose: bool = True) -> Optional[dict]:
    from asr import WhisperASR

    mine_path = ensure_output(song, run)
    gt_path = os.path.join(GT_DIR, song["gt"] + ".ttml")
    if mine_path is None or not os.path.exists(gt_path):
        return None

    # 行跨度固定取已生成产物的（即 ASR+对齐的结果），换模型时不重跑 ASR
    mine_all = load_lines(mine_path)
    gt = load_lines(gt_path)
    spans = [(m["begin"], m["end"]) for m in mine_all]

    E, valid = _embed_lines(song, spans, embedder, mode, source, variant)
    if E is None:
        print("   ✗ 有效声纹太少，跳过")
        return None
    if len(valid) < len(mine_all):
        print(f"    （{len(mine_all) - len(valid)} 行太短提不出声纹，仅在这些行上不评分）")

    # 行下标 → E 里的行号
    pos = {li: k for k, li in enumerate(valid)}

    # 聚类：与生产完全同一套 k-means（k=2）
    import diarize
    pred_raw = np.array(diarize._kmeans(E, 2))

    # 只保留"既配上真值、又有声纹"的行
    mine = mine_all
    all_pairs = pair(mine, gt)
    pairs = [(i, j, ov) for i, j, ov in all_pairs if i in pos]
    if len(pairs) < 5:
        print("   ✗ 配对太少，跳过")
        return None

    labels = sorted({gt[j]["agent"] for _, j, _ in pairs})
    lmap = {a: i for i, a in enumerate(labels)}
    y = np.array([lmap[gt[j]["agent"]] for _, j, _ in pairs])
    pred = np.array([pred_raw[pos[i]] for i, _, _ in pairs])

    # 允许整体标签置换（v1/v2 的编号本身没有语义）
    import itertools
    k = max(len(labels), 2)
    best_acc, best_pred = -1.0, pred
    for perm in itertools.permutations(range(k), k):
        p = np.array([perm[x] for x in pred])
        a = float((p == y).mean())
        if a > best_acc:
            best_acc, best_pred = a, p

    # 每行基频（只读一次 16k 音频）
    audio16 = WhisperASR(log=lambda *a: None).load_audio(song["audio"])
    f0 = np.array([line_f0(audio16, mine[i]["begin"], mine[i]["end"]) for i, _, _ in pairs])

    correct = (best_pred == y)
    return {"song": song["name"], "n": len(pairs), "acc": best_acc, "labels": labels,
            "y": y, "pred": best_pred, "correct": correct, "f0": f0,
            "high": (f0 > (float(np.median(f0[f0 > 0])) if (f0 > 0).any() else 0.0)),
            "per_singer": {name: (int(correct[y == ci].sum()), int((y == ci).sum()))
                           for ci, name in enumerate(labels) if (y == ci).sum()},
            "runs": _runs(correct),
            "conf": {(labels[t], f"c{int(p)}"): 1 for t, p in zip(y, best_pred)},
            "gt_agents": [gt[j]["agent"] for _, j, _ in pairs],
            "has_bg": [gt[j]["has_bg"] for _, j, _ in pairs],
            "embedder": embedder, "mode": mode}


def _runs(correct: np.ndarray) -> List[int]:
    """连续错误段的长度列表"""
    runs, cur = [], 0
    for c in correct:
        if c:
            if cur:
                runs.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        runs.append(cur)
    return runs


def report(rows: List[dict]) -> None:
    total_n = sum(r["n"] for r in rows)
    total_ok = sum(int(r["correct"].sum()) for r in rows)
    print("\n" + "=" * 74)
    print(f"【总体】{len(rows)} 首 / {total_n} 行：{total_ok}/{total_n} = "
          f"{total_ok / total_n * 100:.1f}%")
    for r in rows:
        print(f"   {r['song']:<12} {r['acc'] * 100:5.1f}%   （{r['n']} 行）")

    # 版本存疑的歌单独剔出来再报一次，避免污染总体结论
    shaky = [r for r in rows if SONG_NOTE.get(r["song"])]
    solid = [r for r in rows if not SONG_NOTE.get(r["song"])]
    if shaky and solid:
        sn = sum(r["n"] for r in solid)
        so = sum(int(r["correct"].sum()) for r in solid)
        print(f"\n【剔除版本存疑的歌】{len(solid)} 首 / {sn} 行："
              f"{so}/{sn} = {so / sn * 100:.1f}%"
              f"   （剔除：{'、'.join(r['song'] for r in shaky)}）")
    for r in rows:
        note = SONG_NOTE.get(r["song"])
        if note:
            print(f"   {note}")
    print("=" * 74)

    print("\n【每歌手召回】（某歌手召回明显低 = 他的发声状态被拆进了另一簇）")
    for r in rows:
        s = "  ".join(f"{k} {v[0]}/{v[1]}（{v[0] / v[1] * 100:.0f}%）"
                      for k, v in r["per_singer"].items())
        print(f"   {r['song']:<12} {s}")

    print("\n【按音区分组】（高/低以本歌 F0 中位数为界）")
    for r in rows:
        hi, lo = r["high"], ~r["high"]
        a_hi = r["correct"][hi].mean() * 100 if hi.any() else float("nan")
        a_lo = r["correct"][lo].mean() * 100 if lo.any() else float("nan")
        print(f"   {r['song']:<12} 高音区 {a_hi:5.1f}%（{int(hi.sum())} 行）"
              f"   低音区 {a_lo:5.1f}%（{int(lo.sum())} 行）"
              f"   差 {a_hi - a_lo:+.1f}")

    print("\n【连续错行段】（段长 ≥2 说明是段落级问题，不是逐行噪声）")
    for r in rows:
        segs = [x for x in r["runs"] if x >= 2]
        print(f"   {r['song']:<12} 错 {int((~r['correct']).sum())} 行，"
              f"成段 {segs if segs else '无'}")

    print("\n【混淆矩阵】（真值 → 我们）")
    for r in rows:
        print(f"   {r['song']:<12} " +
              "  ".join(f"{t}→{p}:{n}" for (t, p), n in sorted(r["conf"].items())))

    print("\n【和声行（x-bg）上的表现】")
    for r in rows:
        m = np.array(r["has_bg"])
        if m.any():
            print(f"   {r['song']:<12} 带和声的行 {int(m.sum())} 行，"
                  f"其中判对 {int(r['correct'][m].sum())} 行"
                  f"；不带和声 {int((~m).sum())} 行判对 {int(r['correct'][~m].sum())}")
        else:
            print(f"   {r['song']:<12} 无和声行")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-run", action="store_true", help="只用已有产物")
    ap.add_argument("--embedder", default="campplus",
                    choices=["campplus", "ssl-singer"],
                    help="campplus=基线；ssl-singer=Sony CSL 歌手身份表征（实验）")
    ap.add_argument("--mode", default="whole", choices=["whole", "4s"],
                    help="ssl-singer 的输入方式：whole=整行（与基线同跨度，默认）；"
                         "4s=模型原生 4 秒切片再平均")
    ap.add_argument("--variant", default="byol",
                    choices=["byol", "contrastive", "contrastive-vc",
                             "unif-align", "vicreg"],
                    help="ssl-singer 的 5 个预训练变体。⚠️ 在同一批歌上挑最好的那个"
                         "属于探索性比较，**不算独立验证**")
    ap.add_argument("--source", default="mix", choices=["mix", "vocals"],
                    help="mix=原始混音（默认，与基线同条件）；"
                         "vocals=Demucs 隔离人声轨（同时换了输入条件，必须分开解读）")
    args = ap.parse_args()

    print("=" * 74)
    print(f" 分声部评估　embedder={args.embedder}　source={args.source}"
          + (f"　variant={args.variant}　mode={args.mode}"
             if args.embedder != "campplus" else ""))
    print(" 真值：AMLL TTML DB 人工标注　聚类：k-means(k=2)　跨度：已有产物的行跨度")
    print("=" * 74)

    rows = []
    for s in SONGS:
        if args.only and args.only not in s["name"]:
            continue
        print(f"\n[{s['name']}]")
        r = eval_song(s, run=not args.no_run, embedder=args.embedder,
                      mode=args.mode, source=args.source, variant=args.variant)
        if r:
            rows.append(r)
            print(f"   逐行一致率 {r['acc'] * 100:.1f}%  （{r['n']} 行配对）")

    if not rows:
        print("\n没有可评估的歌曲")
        return 1
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
