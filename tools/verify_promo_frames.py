#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立验收工具：宣传片成片（promo/out/Lyric2TTML-promo.mp4）vs 基线（Lyric2TTML-宣传片.mp4）。

只读基线：`Lyric2TTML-宣传片.mp4`、`.demo/out/audio.wav`、`promo/src/**`。
只写：`.tmp/verify/**`（+ 本文件自身）。

子命令
------
  baseline      基线与成片的容器/编码指纹（ffprobe），写 `.tmp/verify/container.json`
  blocks        语音块检测自证：在基线音频上跑检测器，与 9 个已知真值块比对
  audio         解出新片音轨，与 `.demo/out/audio.wav` 对齐后逐样本比较 + 语音块比对
  frames        从两片各抽同一批时间点帧（fps=4，0.25s 网格），算 MAE / 直方图差异
  determinism   同一帧渲染两次（remotion still），比较哈希
  pair          生成并排对照图 / 差异热力图（`.tmp/verify/pairs/*.png`）
  all           以上全部（determinism 需要 promo/src 存在）

语音块检测器（参数在下面常量区，全部写进报告）
------------------------------------------------
  解码：ffmpeg → 单声道 44100Hz s16le → float
  分帧：20ms 非重叠（882 样本），Hann 窗
  特征：2.5–8kHz 频段平均功率（rfft 频点均值）→ dB
  噪声底：帧 dB 的 20 分位；判定阈值 = 噪声底 + 12 dB
  合并：间隔 ≤ 0.50s 的相邻活动段合并；丢弃 < 0.30s 的段
  边界：起点 = 首活动帧中心，终点 = 末活动帧中心 − 0.10s（收口 trim）
  自证：基线音频上必须检出 9 块，且与真值逐块 |Δ| ≤ 0.15s

合并容差怎么定的（实测边界，`.tmp/verify/merge_tolerance.py`）
--------------------------------------------------------------
  * 真值里最小真实间隔 = **0.80s**（49.09→49.94），所以容差必须明显小于 0.80；
  * aac 有损编码会在第 5 块（22.80→32.24）里造出 **0.46s** 的 2.5–8kHz 凹陷，
    容差 < 0.46 会把一个块切成两个（`merge_gap=0.35` 时 aac 解出 10 块 ≠ 真值 9 块）；
  * 实测安全窗口 = **[0.40, 0.60]**：0.35 在 aac 上炸（10 块），0.65 在母带上炸（8 块）。
  * 取窗口中点 **0.50**，两侧各留 0.10s 余量；`cmd_audio` 会同时打印 0.35/0.50/0.65
    三档结果的敏感性表，避免"参数挑出来的结论"。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image

try:  # Windows 控制台常是 GBK，避免非 GBK 字符把验收脚本打断
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_VIDEO = os.path.join(ROOT, "Lyric2TTML-宣传片.mp4")
BASE_WAV = os.path.join(ROOT, ".demo", "out", "audio.wav")
NEW_VIDEO = os.path.join(ROOT, "promo", "out", "Lyric2TTML-promo.mp4")
V = os.path.join(ROOT, ".tmp", "verify")

# ---------------- 检测器参数（写进报告） ----------------
SR = 44100
FRAME_N = 882           # 20ms @44.1k
BAND_LO, BAND_HI = 2500.0, 8000.0
NOISE_PCT = 20          # 噪声底分位
MARGIN_DB = 12.0        # 阈值 = 噪声底 + MARGIN_DB
MERGE_GAP = 0.50        # 合并容差（秒）；见文件头"合并容差怎么定的"
MIN_DUR = 0.30          # 最短语音块（秒）
TRIM_END = 0.10         # 末端收口（秒）
FD = FRAME_N / SR

# 基线音频语音块已知真值（秒）
TRUTH_BLOCKS = [(1.01, 3.97), (7.68, 13.14), (13.95, 16.62), (17.69, 22.00),
                (22.80, 32.24), (41.68, 49.09), (49.94, 51.21), (52.71, 56.34),
                (57.21, 57.89)]

# scene-builder 文档里用来核对版式的帧号（promo/src/scenes/README.md §验证帧号）
SPOT_FRAMES = [90, 270, 390, 450, 570, 660, 840, 960, 1110, 1320, 1380, 1530, 1650, 1770, 1830]

BASE_CONTAINER = dict(width=1920, height=1080, fps="30/1", duration=62.0,
                      nb_frames=1860, v="h264 High yuvj420p",
                      a="aac LC 44100Hz stereo 191964bps")


# ---------------- 通用 ----------------
def run(cmd, log=None, timeout=None, cwd=None, env=None):
    """跑外部命令；stdout 可重定向到文件句柄（不用管道，绕开沙箱命名管道限制）。"""
    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    f = None
    if log is not None:
        f = open(log, "wb")
        kwargs["stdout"] = f
        kwargs["stderr"] = subprocess.STDOUT
    try:
        return subprocess.run(cmd, timeout=timeout, **kwargs).returncode
    finally:
        if f is not None:
            f.close()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def fingerprint(path):
    st = os.stat(path)
    return dict(path=os.path.relpath(path, ROOT).replace("\\", "/"),
                bytes=st.st_size,
                last_write_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                sha256=sha256(path))


def ensure(d):
    os.makedirs(d, exist_ok=True)
    return d


def ffprobe(path):
    out = os.path.join(V, "_probe.json")
    ensure(V)
    rc = run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
              "-of", "json", path], log=out)
    if rc != 0:
        raise RuntimeError("ffprobe 失败 rc=%d: %s" % (rc, path))
    with open(out, "r", encoding="utf-8") as f:
        return json.load(f)


def probe_nb_frames(path):
    """探测视频帧数（优先 nb_frames，缺失时用 时长×帧率 估算）。"""
    p = ffprobe(path)
    for st in p["streams"]:
        if st["codec_type"] == "video":
            if st.get("nb_frames"):
                return int(st["nb_frames"])
            dur = float(st.get("duration") or p["format"]["duration"])
            num, den = (st.get("avg_frame_rate") or st.get("r_frame_rate") or "30/1").split("/")
            return int(round(dur * float(num) / float(den)))
    return None


def summarize_probe(probe):
    s = dict(format_name=probe["format"]["format_name"],
             duration=float(probe["format"]["duration"]),
             size=int(probe["format"]["size"]),
             bit_rate=int(probe["format"].get("bit_rate", 0)))
    for st in probe["streams"]:
        if st["codec_type"] == "video":
            s["video"] = dict(codec=st["codec_name"], profile=st.get("profile"),
                              width=st["width"], height=st["height"],
                              pix_fmt=st.get("pix_fmt"),
                              r_frame_rate=st["r_frame_rate"],
                              avg_frame_rate=st["avg_frame_rate"],
                              duration=float(st.get("duration", 0) or 0),
                              nb_frames=st.get("nb_frames"))
        elif st["codec_type"] == "audio":
            s["audio"] = dict(codec=st["codec_name"], profile=st.get("profile"),
                              sample_rate=int(st["sample_rate"]),
                              channels=st["channels"],
                              bit_rate=int(st.get("bit_rate", 0)),
                              duration=float(st.get("duration", 0) or 0))
    return s


# ---------------- 音频：解码 / 特征 / 块检测 ----------------
def _src_stamp(src):
    st = os.stat(src)
    return dict(path=os.path.abspath(src), size=st.st_size, mtime=round(st.st_mtime, 6))


def _raw_fresh(src, raw_out):
    """缓存判据必须是"**源文件身份**"，不能只看 raw 文件存不存在。

    ⚠️ 真踩过的坑：`new_stereo44k.raw` 在 dry-run（--new 指向基线）时生成，
    正式验收时源换成了成片，但 raw 还在 → 直接复用旧 PCM，
    于是把 v1 成片的音轨误判成"与基线 aac 逐字节相同（SNR 244dB, max 0 LSB）"。
    """
    if not os.path.exists(raw_out):
        return False
    mp = raw_out + ".src.json"
    if not os.path.exists(mp):
        return False
    try:
        with open(mp, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return False
    return old == _src_stamp(src)


def _write_stamp(src, raw_out):
    with open(raw_out + ".src.json", "w", encoding="utf-8") as f:
        json.dump(_src_stamp(src), f, indent=1)


def decode_mono(src, raw_out, sr=SR, force=False):
    if force or not _raw_fresh(src, raw_out):
        ensure(os.path.dirname(raw_out))
        log = raw_out + ".log"
        rc = run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn",
                  "-ac", "1", "-ar", str(sr), "-f", "s16le", raw_out], log=log)
        if rc != 0 or not os.path.exists(raw_out):
            raise RuntimeError("ffmpeg 解码失败 rc=%d（日志 %s）" % (rc, log))
        _write_stamp(src, raw_out)
    return np.fromfile(raw_out, dtype="<i2").astype(np.float64) / 32768.0


def decode_stereo_pcm(src, raw_out, sr=SR, force=False):
    """解码为 16bit 立体声交织（用于逐样本比较）。"""
    if force or not _raw_fresh(src, raw_out):
        ensure(os.path.dirname(raw_out))
        log = raw_out + ".log"
        rc = run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn",
                  "-ac", "2", "-ar", str(sr), "-sample_fmt", "s16",
                  "-f", "s16le", raw_out], log=log)
        if rc != 0 or not os.path.exists(raw_out):
            raise RuntimeError("ffmpeg 立体声解码失败 rc=%d（日志 %s）" % (rc, log))
        _write_stamp(src, raw_out)
    a = np.fromfile(raw_out, dtype="<i2")
    return a.reshape(-1, 2)


def band_db(x, sr=SR):
    """2.5–8kHz 频段平均功率 → dB（20ms 非重叠帧，Hann 窗）。"""
    n = len(x)
    nfr = (n - FRAME_N) // FRAME_N + 1
    idx = np.arange(nfr) * FRAME_N
    seg = x[idx[:, None] + np.arange(FRAME_N)[None, :]] * np.hanning(FRAME_N)
    spec = np.fft.rfft(seg, axis=1)
    freqs = np.fft.rfftfreq(FRAME_N, 1.0 / sr)
    mask = (freqs >= BAND_LO) & (freqs <= BAND_HI)
    power = (np.abs(spec[:, mask]) ** 2).sum(axis=1) / int(mask.sum())
    t = (idx + FRAME_N / 2.0) / sr
    return t, 10.0 * np.log10(power + 1e-20)


def detect_blocks(t, db, duration=None, noise_pct=NOISE_PCT, margin=MARGIN_DB,
                  merge_gap=MERGE_GAP, min_dur=MIN_DUR, trim_end=TRIM_END):
    """阈值 + 合并 + 收口 → [(start, end), ...]（秒）。"""
    noise = float(np.percentile(db, noise_pct))
    thr = noise + margin
    act = db > thr
    runs, i, n = [], 0, len(act)
    while i < n:
        if act[i]:
            j = i
            while j + 1 < n and act[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    if not runs:
        return [], dict(noise_db=noise, thr_db=thr)
    merged = [runs[0][:]]
    for a, b in runs[1:]:
        if (a - merged[-1][1] - 1) * FD <= merge_gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    dur = duration if duration else (t[-1] + FD / 2 + 1.0)
    out = []
    for a, b in merged:
        s = max(0.0, t[a] - FD / 2)
        e = min(dur, t[b] + FD / 2 - trim_end)
        if e - s >= min_dur:
            out.append((round(s, 3), round(e, 3)))
    return out, dict(noise_db=round(noise, 2), thr_db=round(thr, 2),
                     n_runs=len(runs), n_blocks=len(out))


def blocks_cmp(det, truth=TRUTH_BLOCKS):
    """逐块比对（数量必须相等才逐块配）。"""
    rows = []
    if len(det) != len(truth):
        return dict(n_det=len(det), n_truth=len(truth), matched=False,
                    max_abs_start=None, max_abs_end=None, rows=rows)
    for i, (d, u) in enumerate(zip(det, truth), 1):
        rows.append(dict(i=i, det=[d[0], d[1]], truth=[u[0], u[1]],
                         d_start=round(d[0] - u[0], 3), d_end=round(d[1] - u[1], 3)))
    return dict(n_det=len(det), n_truth=len(truth), matched=True,
                max_abs_start=max(abs(r["d_start"]) for r in rows),
                max_abs_end=max(abs(r["d_end"]) for r in rows),
                rows=rows)


def blocks_delta(a, b):
    """两份语音块列表的差异（数量/边界）。"""
    if len(a) != len(b):
        return dict(n_a=len(a), n_b=len(b), same_count=False,
                    max_abs_start=None, max_abs_end=None)
    ds = [abs(x[0] - y[0]) for x, y in zip(a, b)]
    de = [abs(x[1] - y[1]) for x, y in zip(a, b)]
    return dict(n_a=len(a), n_b=len(b), same_count=True,
                max_abs_start=round(max(ds), 3) if ds else 0.0,
                max_abs_end=round(max(de), 3) if de else 0.0)


# ---------------- 帧：抽取 / 比较 ----------------
def sample_times(fps=4, start=0.25, duration=BASE_CONTAINER["duration"]):
    """采样时刻（秒）：start 起、每 1/fps 一点，< duration。"""
    ts = []
    i = 0
    while True:
        t = start + i / float(fps)
        if t >= duration:
            break
        ts.append(t)
        i += 1
    return ts


def frame_indices(times, src_fps=30.0, n_frames=None):
    """时刻 → 源帧号（四舍五入，非 Python 的银行家舍入）。"""
    idx = [int(np.floor(t * src_fps + 0.5)) for t in times]
    if n_frames is not None:
        idx = [n for n in idx if 0 <= n < n_frames]
    return idx


def select_expr(indices, debug=None):
    """把帧号列表变成 ffmpeg select 表达式。

    247 个 `eq(n,X)+…` 的链式表达式会把 ffmpeg 的表达式解析器打爆
    （实测 `Error while parsing expression` + `Cannot allocate memory`），
    所以先试**紧凑等价式**：0.25s 网格在 30fps 下 = n=round(7.5k)，
    即「n%15∈{8,0} 且 n>0」（n=8,15,23,30,38,45,…）。
    若与给定帧号完全一致就用它；否则退化成每 60 个一组的 eq 链（返回多段）。
    返回 (expr_list, mode)；mode 用于日志。
    """
    cand = [n for n in range(1, max(indices) + 1) if n % 15 in (0, 8)]
    if indices and cand == list(indices):
        return ["gt(n\\,0)*(eq(mod(n\\,15)\\,8)+eq(mod(n\\,15)\\,0))"], "mod15"
    chunks = [indices[i:i + 60] for i in range(0, len(indices), 60)]
    return ["+".join("eq(n\\,%d)" % n for n in c) for c in chunks], "eq-chunks/%d" % len(chunks)


def extract_frames(video, outdir, indices, force=False):
    """按**显式帧号**抽帧（无损 PNG 直出），输出 f_0001.png… 与 indices 一一对应。

    ⚠️ 不用 `-vf fps=4`：实测它取的是比标称时刻**早 4 帧**（0.133s）的源帧
    （k=2→n=11, k=40→n=296, k=247→n=1848），虽然两片同规则、比对仍成立，
    但报告里的时间标签会系统性偏 0.133s。显式帧号没有这个歧义。
    数量不符直接报错（不做静默比较）。
    """
    ensure(outdir)
    existing = sorted(f for f in os.listdir(outdir) if f.endswith(".png"))
    # ⚠️ 缓存判据 = 源视频 SHA256 + 帧号列表指纹。只看"是否已有 PNG/数量是否相等"会出大事：
    #    成片改版后帧数往往还是 1860、网格还是 247 点，于是**悄悄拿旧成片的帧去比**，
    #    结论会是"MAE≈0、完美一致"——这正是最危险的假阳性。
    stamp = dict(video_sha256=sha256(video), n=len(indices),
                 first=indices[0] if indices else None, last=indices[-1] if indices else None,
                 idx_sha=hashlib.sha256(",".join(str(x) for x in indices).encode()).hexdigest()[:16])
    sp = os.path.join(outdir, ".source.json")
    if existing and not force and len(existing) == len(indices) and os.path.exists(sp):
        try:
            with open(sp, "r", encoding="utf-8") as f:
                if json.load(f) == stamp:
                    return existing
        except Exception:
            pass
        print("[cache] %s 的抽帧缓存与源不符（改了成片/换了帧号）→ 重新抽帧" % os.path.relpath(outdir, ROOT))
    for f in existing:
        os.remove(os.path.join(outdir, f))
    exprs, mode = select_expr(indices)
    if len(exprs) == 1:
        tmpdirs = [outdir]
    else:  # 分段落盘后统一重命名，保证顺序
        tmpdirs = [ensure(os.path.join(outdir, "_part%02d" % i)) for i in range(len(exprs))]
    log = os.path.join(V, "_frames.log")
    total = 0
    with open(log, "wb") as lf:
        for d, expr in zip(tmpdirs, exprs):
            for f in os.listdir(d):
                if f.endswith(".png"):
                    os.remove(os.path.join(d, f))
            p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video,
                                "-vf", "select='%s'" % expr, "-fps_mode", "passthrough",
                                "-pix_fmt", "rgb24", os.path.join(d, "p_%04d.png")],
                               stdout=lf, stderr=lf)
            if p.returncode != 0:
                raise RuntimeError("抽帧失败 rc=%d（日志 %s, filter=%s）" % (p.returncode, log, mode))
            total += len([f for f in os.listdir(d) if f.endswith(".png")])
    if total != len(indices):
        raise RuntimeError("抽帧数量不符：期望 %d 实际 %d（filter=%s）" % (len(indices), total, mode))
    seq = 0
    for d in tmpdirs:
        for f in sorted(f for f in os.listdir(d) if f.startswith("p_") and f.endswith(".png")):
            seq += 1
            src = os.path.join(d, f)
            dst = os.path.join(outdir, "f_%04d.png" % seq)
            os.replace(src, dst)
    for d in tmpdirs[1:]:
        os.rmdir(d)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(stamp, f, indent=1)
    return sorted(f for f in os.listdir(outdir) if f.endswith(".png"))


def load_rgb(path):
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def mae_hist(a, b, bins=256):
    d = np.abs(a - b)
    mae = float(d.mean())
    mx = int(d.max())
    # 直方图差异：每通道归一化直方图的 L1 距离/2，再取通道均值（0..1）
    hd = []
    for c in range(3):
        ha = np.bincount(a[:, :, c].ravel(), minlength=bins).astype(np.float64)
        hb = np.bincount(b[:, :, c].ravel(), minlength=bins).astype(np.float64)
        ha /= max(ha.sum(), 1); hb /= max(hb.sum(), 1)
        hd.append(0.5 * float(np.abs(ha - hb).sum()))
    return mae, mx, float(np.mean(hd))


def diff_localization(a, b, thr=8):
    """差异的空间分布：是"局部文字差"还是"整体位移/整体色调差"。

    返回：差异能量集中度（最大 1% 像素占总差的比）、>thr 像素占比、以及这些像素的包围盒
    （相对画面宽高的比例，便于判断"差在哪一块"）。
    """
    d = np.abs(a - b).astype(np.float64).max(axis=2)
    tot = float(d.sum()) + 1e-9
    flat = np.sort(d.ravel())[::-1]
    k = max(1, int(len(flat) * 0.01))
    conc = float(flat[:k].sum() / tot)
    mask = d > thr
    frac = float(mask.mean())
    ys, xs = np.nonzero(mask)
    h, w = d.shape
    if len(ys) == 0:
        return dict(energy_top1pct=round(conc, 4), frac_gt_thr=0.0, bbox=None)
    return dict(energy_top1pct=round(conc, 4), frac_gt_thr=round(frac, 6),
                bbox=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                bbox_frac=[round(float(xs.min()) / w, 3), round(float(ys.min()) / h, 3),
                           round(float(xs.max()) / w, 3), round(float(ys.max()) / h, 3)])


def scene_of(t, scenes):
    for s in scenes:
        if s["start"] <= t < s["end"]:
            return s["name"]
    return "?"


def fft_peak_lag(ref, sig):
    """corr[k] = sum_n sig[n+k]*ref[n] 的峰值位置 k（FFT 实现，含负位移回绕）。

    语义：若 sig[i] == ref[i - lag]（sig 相对 ref 延迟 lag 个样本），则返回 k = lag + offset，
    其中 offset 是调用方为搜索窗口额外前置的样本数（见 cmd_audio）。
    """
    n_fft = 1
    while n_fft < len(ref) + len(sig) - 1:
        n_fft *= 2
    R = np.fft.irfft(np.fft.rfft(sig, n_fft) * np.conj(np.fft.rfft(ref, n_fft)), n_fft)
    j = int(np.argmax(np.abs(R)))
    # 索引 0..len(sig)-1 是正位移 k=j；再往后（回绕段）是负位移 k=j-n_fft
    return j if j <= len(sig) - 1 else j - n_fft


def compare_pcm(nb, nn, sr=SR, lag_max_s=0.15):
    """两路 PCM（s16 立体声）逐样本比较。

    先在三处 3s 片段上用 FFT 互相关求整体位移（aac 编码可能带 priming），
    再分别给出 lag=0 与最佳 lag 下的最大绝对差 / RMS / SNR / 超阈值样本占比，
    最后给逐秒 RMS 差（查"段落性篡改"）。
    """
    n = min(len(nb), len(nn))
    b = nb[:n].astype(np.float64)
    v = nn[:n].astype(np.float64)
    lag_max = int(lag_max_s * sr)
    votes = []
    for c0s in (8.0, 25.0, 45.0):
        c0 = int(c0s * sr)
        seg_len = int(3.0 * sr)
        if c0 - lag_max < 0 or c0 + seg_len + lag_max >= n:
            continue
        sig = v[c0 - lag_max:c0 + seg_len + lag_max, 0]
        sig = sig - sig.mean()
        ref = b[c0:c0 + seg_len, 0]
        ref = ref - ref.mean()
        # sig[i] 对应 v 的 c0-lag_max+i；峰值 k 处 sig[k:] 对齐 ref[0:] → lag = k - lag_max
        votes.append(fft_peak_lag(ref, sig) - lag_max)
    best_lag = int(np.median(votes)) if votes else 0
    best_lag = int(np.clip(best_lag, -lag_max, lag_max))

    def at(lag):
        if lag >= 0:
            bb, vv = b[:n - lag], v[lag:n]
        else:
            bb, vv = b[-lag:n], v[:n + lag]
        d = np.abs(bb - vv)
        rms_d = float(np.sqrt((d ** 2).mean()))
        rms_s = float(np.sqrt((bb ** 2).mean()))
        st = dict(max_abs_diff_lsb=float(d.max()),
                  max_abs_diff_dbfs=round(20 * np.log10(max(d.max(), 1e-6) / 32768.0), 2),
                  rms_diff_lsb=round(rms_d, 3), rms_signal_lsb=round(rms_s, 3),
                  snr_db=round(20 * np.log10(max(rms_s, 1e-9) / max(rms_d, 1e-9)), 2),
                  frac_gt_1lsb=round(float((d > 1).mean()), 5),
                  frac_gt_16lsb=round(float((d > 16).mean()), 6),
                  frac_gt_128lsb=round(float((d > 128).mean()), 7),
                  frac_gt_1024lsb=round(float((d > 1024).mean()), 8))
        return bb, vv, st

    bb0, _, s0 = at(0)
    bb, vv, sb = at(best_lag)
    seg = int(1.0 * sr)
    segs = []
    for k in range(0, len(bb) // seg):
        t0 = k * seg
        ra = float(np.sqrt((bb[t0:t0 + seg] ** 2).mean()))
        rc = float(np.sqrt((vv[t0:t0 + seg] ** 2).mean()))
        segs.append(dict(t=k, rms_base_db=round(20 * np.log10(max(ra, 1e-9)), 2),
                         rms_new_db=round(20 * np.log10(max(rc, 1e-9)), 2),
                         rms_delta_db=round(20 * np.log10(max(rc, 1e-9)) -
                                            20 * np.log10(max(ra, 1e-9)), 2)))
    return dict(samples_base=int(len(nb)), samples_new=int(len(nn)),
                lag_votes=[int(x) for x in votes], best_lag_samples=int(best_lag),
                best_lag_ms=round(best_lag * 1000.0 / sr, 2),
                samples_compared=int(len(bb)),
                at_lag0=s0, at_best_lag=sb, segment_rms=segs)


def parse_trusted_blocks(path):
    """从基线音频解析出的语音块（缓存），供外部复用。"""
    p = os.path.join(V, "blocks_baseline.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)["blocks"]
    return None


# ---------------- 子命令 ----------------
def cmd_baseline(args):
    ensure(V)
    res = dict(recorded_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               baseline=fingerprint(BASE_VIDEO),
               baseline_audio=fingerprint(BASE_WAV))
    res["baseline_container"] = summarize_probe(ffprobe(BASE_VIDEO))
    res["baseline_audio_container"] = summarize_probe(ffprobe(BASE_WAV))
    if os.path.exists(NEW_VIDEO):
        res["new"] = fingerprint(NEW_VIDEO)
        res["new_container"] = summarize_probe(ffprobe(NEW_VIDEO))
    else:
        res["new"] = None
        res["new_container"] = None
    with open(os.path.join(V, "container.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_blocks(args):
    ensure(os.path.join(V, "audio"))
    raw = os.path.join(V, "audio", "base_mono44k.raw")
    x = decode_mono(BASE_WAV, raw)
    t, db = band_db(x)
    det, meta = detect_blocks(t, db, duration=len(x) / SR)
    cmp_ = blocks_cmp(det)
    res = dict(source=".demo/out/audio.wav", samples=int(len(x)),
               duration=round(len(x) / SR, 3),
               params=dict(frame_ms=20, band_hz=[BAND_LO, BAND_HI],
                           noise_pct=NOISE_PCT, margin_db=MARGIN_DB,
                           merge_gap_s=MERGE_GAP, min_dur_s=MIN_DUR, trim_end_s=TRIM_END),
               meta=meta, blocks=det, truth=TRUTH_BLOCKS, compare=cmp_)
    with open(os.path.join(V, "blocks_baseline.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    ok = cmp_["matched"] and cmp_["max_abs_start"] <= 0.15 and cmp_["max_abs_end"] <= 0.15
    print("\n[self-proof] voice-block detector vs truth: %s  (max|dStart|=%.3f max|dEnd|=%.3f)"
          % ("PASS" if ok else "FAIL", cmp_["max_abs_start"] or -1.0, cmp_["max_abs_end"] or -1.0))
    return 0 if ok else 1


def cmd_audio(args):
    new_video = getattr(args, "new", None) or NEW_VIDEO
    if not os.path.exists(new_video):
        print("[等待] %s 不存在" % os.path.relpath(new_video, ROOT))
        return 2
    ensure(os.path.join(V, "audio"))
    res = {}
    # 0) 基线视频自带的 aac 音轨（成片音轨同为 aac，必须用同管线基准比对）
    base_aac = os.path.join(V, "audio", "base_from_mp4.wav")
    if not os.path.exists(base_aac):
        rc = run(["ffmpeg", "-y", "-v", "error", "-i", BASE_VIDEO, "-vn", "-acodec",
                  "pcm_s16le", "-ar", str(SR), base_aac], log=os.path.join(V, "_ex_baseaudio.log"))
        if rc != 0:
            raise RuntimeError("解基线音轨失败 rc=%d" % rc)

    def blocks_of(path, tag):
        x = decode_mono(path, os.path.join(V, "audio", "%s_mono44k.raw" % tag))
        t, db = band_db(x)
        bl, meta = detect_blocks(t, db, duration=len(x) / SR)
        return bl, meta, len(x)

    # 1) 三份音轨的语音块（母带 / 基线 aac / 新片 aac）
    bl_base, meta_base, ns_base = blocks_of(BASE_WAV, "base")
    bl_baac, meta_baac, ns_baac = blocks_of(base_aac, "base_aac")
    # 2) 新片音轨
    new_wav = os.path.join(V, "audio", "new_audio.wav")
    if os.path.exists(new_wav):
        os.remove(new_wav)
    rc = run(["ffmpeg", "-y", "-v", "error", "-i", new_video, "-vn", "-acodec",
              "pcm_s16le", "-ar", str(SR), new_wav], log=os.path.join(V, "_ex_audio.log"))
    if rc != 0:
        raise RuntimeError("解新片音轨失败 rc=%d" % rc)
    bl_new, meta_new, ns_new = blocks_of(new_wav, "new")
    # 合并容差敏感性：0.35 会因 aac 的 ~0.46s 凹陷把一个块切成两个；0.65 会把真值里 0.80s 的间隔并掉
    sens = {}
    for tag, path in (("baseline_wav", BASE_WAV), ("baseline_aac", base_aac), ("new_aac", new_wav)):
        x = decode_mono(path, os.path.join(V, "audio", "%s_mono44k.raw" % tag))
        t, db = band_db(x)
        sens[tag] = {}
        for mg in (0.35, 0.50, 0.65):
            b_, _ = detect_blocks(t, db, duration=len(x) / SR, merge_gap=mg)
            sens[tag]["merge_gap_%.2f" % mg] = dict(n=len(b_), blocks=b_,
                                                    vs_truth=blocks_cmp(b_))
    res["voice_blocks"] = dict(
        master_wav=bl_base, baseline_aac=bl_baac, new_aac=bl_new,
        meta=dict(master_wav=meta_base, baseline_aac=meta_baac, new_aac=meta_new),
        samples=dict(master_wav=ns_base, baseline_aac=ns_baac, new_aac=ns_new),
        delta_new_vs_baseline_aac=blocks_delta(bl_baac, bl_new),
        delta_new_vs_master_wav=blocks_delta(bl_base, bl_new),
        new_vs_truth=blocks_cmp(bl_new),
        merge_tolerance_sensitivity=sens)
    # 3) 逐样本比较：新片解码 vs 基线母带；再 vs 基线 mp4 自己的 aac 音轨（同管线、苹果对苹果）
    nb = decode_stereo_pcm(BASE_WAV, os.path.join(V, "audio", "base_stereo44k.raw"))
    nn = decode_stereo_pcm(new_wav, os.path.join(V, "audio", "new_stereo44k.raw"))
    ba = decode_stereo_pcm(base_aac, os.path.join(V, "audio", "base_aac_stereo44k.raw"))
    res["sample_compare_vs_master_wav"] = compare_pcm(nb, nn)
    res["sample_compare_vs_baseline_aac"] = compare_pcm(ba, nn)
    res["segment_rms_vs_master_wav"] = res["sample_compare_vs_master_wav"]["segment_rms"]
    res["segment_rms_vs_baseline_aac"] = res["sample_compare_vs_baseline_aac"]["segment_rms"]
    for k in ("sample_compare_vs_master_wav", "sample_compare_vs_baseline_aac"):
        res[k].pop("segment_rms")
    res["new_video"] = os.path.relpath(new_video, ROOT).replace("\\", "/")
    out_json = "audio_compare.json" if not getattr(args, "new", None) else "audio_compare_dryrun.json"
    with open(os.path.join(V, out_json), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_frames(args):
    new_video = getattr(args, "new", None) or NEW_VIDEO
    if not os.path.exists(new_video):
        print("[等待] %s 不存在" % os.path.relpath(new_video, ROOT))
        return 2
    tag = getattr(args, "tag", None) or "new"
    base_dir = os.path.join(V, "base", "frames")
    new_dir = os.path.join(V, tag, "frames")
    times = sample_times(fps=args.fps, start=args.start)
    idx = frame_indices(times, src_fps=args.src_fps)
    nb_new = probe_nb_frames(new_video)
    idx_new = idx
    if nb_new is not None and max(idx) >= nb_new:
        idx_new = [n for n in idx if n < nb_new]
        print("[WARN] 成片只有 %d 帧（基线 %d）：网格末尾 %d 个点超出成片，已裁剪（后者少于基线 %.3f s）"
              % (nb_new, BASE_CONTAINER["nb_frames"], len(idx) - len(idx_new),
                 (len(idx) - len(idx_new)) / float(args.fps)))
    fb = extract_frames(BASE_VIDEO, base_dir, idx, force=args.force)
    fn = extract_frames(new_video, new_dir, idx_new, force=args.force)
    if len(fb) != len(fn):
        print("[WARN] frame counts differ: base=%d new=%d (compare min)" % (len(fb), len(fn)))
    m = min(len(fb), len(fn))
    scenes = []
    sp = os.path.join(V, "scenes.json")
    if os.path.exists(sp):
        with open(sp, "r", encoding="utf-8") as f:
            scenes = json.load(f)["scenes"]
    rows = []
    worst = []
    for i in range(m):
        a = load_rgb(os.path.join(base_dir, fb[i]))
        b = load_rgb(os.path.join(new_dir, fn[i]))
        # 报告用真实源帧时间（比标称网格时刻更准），网格时刻另存
        t = round(idx[i] / float(args.src_fps), 4)
        t_nom = round(times[i], 4)
        if a.shape != b.shape:
            rows.append(dict(i=i, n=idx[i], t=t, t_nominal=t_nom,
                             error="shape %s vs %s" % (a.shape, b.shape)))
            continue
        mae, mx, hd = mae_hist(a, b)
        rows.append(dict(i=i, n=idx[i], t=t, t_nominal=t_nom, mae=round(mae, 4),
                         max_pix=mx, hist_l1=round(hd, 5), scene=scene_of(t_nom, scenes)))
    ok_rows = [r for r in rows if "mae" in r]
    arr = np.array([r["mae"] for r in ok_rows])
    worst = sorted(ok_rows, key=lambda r: -r["mae"])[:10]
    # 对齐核查：最差 10 点各自 ±2 帧的 MAE，判断是"内容差"还是"整体位移"；
    # 同时做差异定位（局部文字差 vs 整体差），用于区分"淡入误差"与"版式错位"
    align = []
    for r in worst:
        i = r["i"]
        cands = {}
        for off in (-2, -1, 0, 1, 2):
            j = i + off
            if 0 <= j < m:
                a = load_rgb(os.path.join(base_dir, fb[i]))
                b = load_rgb(os.path.join(new_dir, fn[j]))
                if a.shape == b.shape:
                    cands[off] = round(mae_hist(a, b)[0], 4)
        if cands:
            best_off = min(cands, key=lambda k: cands[k])
            a = load_rgb(os.path.join(base_dir, fb[i]))
            b = load_rgb(os.path.join(new_dir, fn[i]))
            loc = diff_localization(a, b) if a.shape == b.shape else None
            align.append(dict(t=r["t"], n=r["n"], mae_at_0=cands.get(0), best_offset=best_off,
                              mae_at_best=cands[best_off], localization=loc))
    hist = np.histogram(arr, bins=[0, 0.5, 1, 2, 3, 5, 8, 16, 64, 256])
    per_scene = {}
    for r in ok_rows:
        per_scene.setdefault(r.get("scene", "?"), []).append(r["mae"])
    per_scene = {k: dict(n=len(v), mae_mean=round(float(np.mean(v)), 4),
                         mae_max=round(float(np.max(v)), 4))
                 for k, v in sorted(per_scene.items())}
    res = dict(fps=args.fps, start=args.start, src_fps=args.src_fps,
               sample_rule="t = %.2f + i/%d, 源帧号 n = round(t*%g)" % (args.start, args.fps, args.src_fps),
               frame_indices=idx, t_nominal=[round(x, 4) for x in times],
               n_points=len(ok_rows),
               new_video=os.path.relpath(new_video, ROOT).replace("\\", "/"),
               n_base=len(fb), n_new=len(fn),
               mae_mean=round(float(arr.mean()), 4), mae_median=round(float(np.median(arr)), 4),
               mae_p90=round(float(np.percentile(arr, 90)), 4),
               mae_p99=round(float(np.percentile(arr, 99)), 4),
               mae_max=round(float(arr.max()), 4),
               n_lt_3=int((arr < 3).sum()), n_3_to_8=int(((arr >= 3) & (arr < 8)).sum()),
               n_ge_8=int((arr >= 8).sum()),
               mae_histogram={"[%g,%g)" % (hist[1][k], hist[1][k + 1]): int(hist[0][k])
                              for k in range(len(hist[0]))},
               per_scene=per_scene,
               spot_frames=[dict(frame=n, t=next((x["t"] for x in ok_rows if x["n"] == n), None),
                                 mae=next((x["mae"] for x in ok_rows if x["n"] == n), None),
                                 max_pix=next((x["max_pix"] for x in ok_rows if x["n"] == n), None),
                                 scene=next((x.get("scene") for x in ok_rows if x["n"] == n), None))
                            for n in SPOT_FRAMES],
               worst10=worst, alignment_check=align, rows=rows)
    out_json = "frames_compare.json" if not getattr(args, "new", None) else "frames_compare_dryrun.json"
    with open(os.path.join(V, out_json), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: res[k] for k in
                      ("fps", "start", "sample_rule", "n_points", "mae_mean", "mae_median",
                       "mae_p90", "mae_p99", "mae_max", "n_lt_3", "n_3_to_8", "n_ge_8",
                       "mae_histogram", "per_scene")},
                     ensure_ascii=False, indent=2))
    print("\n最差 10 个时刻：")
    for r in worst:
        print("  t=%7.2fs  MAE=%8.4f  maxPix=%3d  histL1=%.5f  %s" %
              (r["t"], r["mae"], r["max_pix"], r["hist_l1"], r.get("scene", "?")))
    print("\n对齐核查（最差点的 ±2 帧 MAE + 差异定位）：")
    for a in align:
        loc = a.get("localization") or {}
        bb = loc.get("bbox_frac")
        print("  t=%7.2fs n=%4d  0帧=%.4f  最佳偏移=%+d → %.4f  能量集中度=%.3f  >8 像素占比=%.5f  bbox(比例)=%s" %
              (a["t"], a["n"], a["mae_at_0"], a["best_offset"], a["mae_at_best"],
               loc.get("energy_top1pct", -1), loc.get("frac_gt_thr", -1), bb))
    return 0


def cmd_pair(args):
    """生成并排对照 + 差异热力图。args.points 形如 '3.0,10.0,...'"""
    new_video = getattr(args, "new", None) or NEW_VIDEO
    if not os.path.exists(new_video):
        print("[等待] %s 不存在" % os.path.relpath(new_video, ROOT))
        return 2
    outdir = ensure(os.path.join(V, "pairs"))
    tf = args.points.split(",") if args.points else []
    scene_pairs = []
    sp = os.path.join(V, "scenes.json")
    if os.path.exists(sp):
        with open(sp, "r", encoding="utf-8") as f:
            scene_pairs = [(s["name"], (s["start"] + s["end"]) / 2.0)
                           for s in json.load(f)["scenes"]]
    pts = [(n, t) for n, t in scene_pairs]
    for x in tf:
        if x.strip():
            pts.append(("t%s" % x.strip(), float(x)))
    made = []
    for name, t in pts:
        n = int(np.floor(t * 30.0 + 0.5))   # 与 frames 子命令同一套帧号规则
        a = os.path.join(outdir, "_b.png")
        b = os.path.join(outdir, "_n.png")
        for src, dst in ((BASE_VIDEO, a), (new_video, b)):
            run(["ffmpeg", "-y", "-v", "error", "-i", src,
                 "-vf", "select='eq(n\\,%d)'" % n, "-fps_mode", "passthrough",
                 "-frames:v", "1", "-pix_fmt", "rgb24", dst],
                log=os.path.join(V, "_pair.log"))
        if not (os.path.exists(a) and os.path.exists(b)):
            continue
        ia, ib = load_rgb(a), load_rgb(b)
        mae, mx, hd = mae_hist(ia, ib)
        h, w, _ = ia.shape
        canvas = np.zeros((h, w * 2 + 8, 3), dtype=np.uint8)
        canvas[:, :w] = np.clip(ia, 0, 255).astype(np.uint8)
        canvas[:, w + 8:] = np.clip(ib, 0, 255).astype(np.uint8)
        canvas[:, w:w + 8] = 255
        safe = name.replace("/", "_").replace("\\", "_").replace(":", "_")
        p1 = os.path.join(outdir, "%s_side.png" % safe)
        Image.fromarray(canvas).save(p1)
        d = np.abs(ia - ib).astype(np.uint8)
        p2 = os.path.join(outdir, "%s_diff.png" % safe)
        Image.fromarray(np.clip(d * 4, 0, 255).astype(np.uint8)).save(p2)
        made.append(dict(name=name, t=round(t, 3), mae=round(mae, 4), max_pix=mx,
                         hist_l1=round(hd, 5), side=p1, diff=p2))
    with open(os.path.join(V, "pairs.json"), "w", encoding="utf-8") as f:
        json.dump(made, f, ensure_ascii=False, indent=2)
    print(json.dumps(made, ensure_ascii=False, indent=2))
    return 0


def find_entry():
    for cand in ("src/index.ts", "src/index.tsx", "src/Root.tsx", "src/index.js"):
        p = os.path.join(ROOT, "promo", cand)
        if os.path.exists(p):
            return cand
    return None


def npx_cmd():
    """Windows 上 npx 是 .cmd shim，CreateProcess 直接找 "npx" 会 WinError 2。"""
    for name in ("npx.cmd", "npx.exe", "npx"):
        p = shutil.which(name)
        if p:
            return p
    return "npx"


def cmd_determinism(args):
    # --hash-only：只对已有 PNG 做哈希/像素互比（不渲染、不删任何东西）
    if getattr(args, "hash_only", ""):
        paths = [p.strip() for p in args.hash_only.split(",") if p.strip()]
        paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in paths]
        missing = [p for p in paths if not os.path.exists(p)]
        hashes = [sha256(p) for p in paths if os.path.exists(p)]
        pair = []
        imgs = [(p, load_rgb(p)) for p in paths if os.path.exists(p)]
        for i in range(len(imgs)):
            for j in range(i + 1, len(imgs)):
                a, b = imgs[i][1], imgs[j][1]
                if a.shape != b.shape:
                    pair.append(dict(a=os.path.basename(imgs[i][0]), b=os.path.basename(imgs[j][0]),
                                     error="shape %s vs %s" % (a.shape, b.shape)))
                    continue
                d = np.abs(a - b)
                pair.append(dict(a=os.path.basename(imgs[i][0]), b=os.path.basename(imgs[j][0]),
                                 identical=sha256(imgs[i][0]) == sha256(imgs[j][0]),
                                 max_abs_diff=int(d.max()), mean_abs_diff=round(float(d.mean()), 6),
                                 n_diff_pixels=int((d.sum(axis=2) > 0).sum()),
                                 n_diff_pixels_gt8=int((d.max(axis=2) > 8).sum())))
        res = dict(mode="hash-only", files=[os.path.relpath(p, ROOT).replace("\\", "/")
                                            for p in paths],
                   bytes=[os.path.getsize(p) for p in paths if os.path.exists(p)],
                   sha256=hashes, missing=missing,
                   all_identical=bool(hashes) and len(set(hashes)) == 1, pairwise=pair)
        with open(os.path.join(V, "determinism.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if (res["all_identical"] and not missing) else 3
    entry = args.entry or find_entry()
    if not entry:
        print("[等待] promo/src 入口未找到")
        return 2
    promo = os.path.join(ROOT, "promo")
    if not os.path.isdir(os.path.join(promo, "node_modules")):
        print("[等待] promo/node_modules 不存在")
        return 2
    # npm 缓存目录必须落在工作区内（全局缓存被沙箱拒绝写）
    env = dict(os.environ)
    env.setdefault("npm_config_cache", os.path.join(ROOT, ".tmp", "npm-cache"))
    outdir = ensure(os.path.join(V, "determinism"))
    frame = args.frame
    outs = []
    for k in (1, 2):
        # ⚠️ 用独立命名、且**绝不删除已存在的文件**：
        #    别人的证据文件（Lead/scene-builder 渲出来的 still）不能被我们清掉。
        #    真踩过：旧版本这里先 os.remove 再渲染，我又渲不出来（沙箱 EPERM），
        #    结果把 Lead 已交付的 frame1110_run1.png 弄丢了，只剩 run2。
        o = os.path.join(outdir, "%s_frame%d_run%d.png" % (args.prefix, frame, k))
        if os.path.exists(o):
            os.remove(o)   # 只删自己要写的那一个（同名残留）
        cmd = [npx_cmd(), "remotion", "still", entry, args.composition, o,
               "--frame=%d" % frame]
        rc = run(cmd, log=os.path.join(V, "_still_%d.log" % k), timeout=1800,
                 cwd=promo, env=env)
        if rc != 0 or not os.path.exists(o):
            print("[FAIL] remotion still rc=%d (cwd=%s, log .tmp/verify/_still_%d.log)"
                  % (rc, promo, k))
            return 1
        outs.append(o)
    h1, h2 = sha256(outs[0]), sha256(outs[1])
    same = h1 == h2
    a, b = load_rgb(outs[0]), load_rgb(outs[1])
    diff = np.abs(a - b)
    res = dict(frame=frame, entry=entry, composition=args.composition,
               still_paths=[os.path.relpath(x, ROOT).replace("\\", "/") for x in outs],
               sha_a=h1, sha_b=h2, identical=same,
               max_abs_diff=int(diff.max()), mean_abs_diff=round(float(diff.mean()), 6),
               n_diff_pixels=int((diff.sum(axis=2) > 0).sum()),
               n_diff_pixels_gt8=int((diff.max(axis=2) > 8).sum()))
    if same:
        res["note"] = "两次 still 的 PNG 逐字节相同 → 渲染确定"
    else:
        res["note"] = ("哈希不同；若差异只有极少数像素且 max_abs_diff 很小，属抗锯齿级抖动，"
                       "否则为真不确定（检查是否有 Date.now/Math.random/CSS transition）")
    with open(os.path.join(V, "determinism.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if same else 3


def cmd_all(args):
    """按顺序跑 6 项检查；任一项非 0 都在最后汇总（不中断，便于一次看全）。"""
    steps = []

    def ns(**kw):
        d = dict(new=None, points="", frame=1110, composition="Lyric2TTML", entry=None,
                 fps=4, start=0.25, src_fps=30.0, force=False, tag=None)
        d.update(kw)
        return argparse.Namespace(**d)

    for name, fn, a in (
            ("1 baseline", cmd_baseline, ns()),
            ("7 blocks(self-proof)", cmd_blocks, ns()),
            ("2 audio", cmd_audio, ns()),
            ("3 frames", cmd_frames, ns()),
            ("4 pair", cmd_pair, ns()),
            ("5 determinism", cmd_determinism, ns()),
            ("6 baseline-recheck", cmd_baseline, ns())):
        print("\n" + "=" * 78 + "\n=== %s ===\n" % name + "=" * 78)
        rc = fn(a)
        steps.append((name, rc))
    print("\n" + "=" * 78 + "\n汇总（0=OK，2=成片缺失，3=不确定性，1=失败）")
    for name, rc in steps:
        print("  %-24s rc=%d %s" % (name, rc, "OK" if rc == 0 else "**需看**"))
    return 0 if all(rc in (0, 3) for _, rc in steps) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="宣传片成片独立验收")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("baseline")
    sub.add_parser("blocks")
    sub.add_parser("all")

    p = sub.add_parser("audio")
    p.add_argument("--new", default=None, help="覆盖成片路径（自测用，例如指向基线本身）")

    p = sub.add_parser("frames")
    p.add_argument("--fps", type=int, default=4)
    p.add_argument("--start", type=float, default=0.25)
    p.add_argument("--src-fps", type=float, default=30.0)
    p.add_argument("--force", action="store_true")
    p.add_argument("--new", default=None, help="覆盖成片路径（自测用）")
    p.add_argument("--tag", default=None, help="抽帧输出目录名（默认 new）")

    p = sub.add_parser("pair")
    p.add_argument("--points", default="")
    p.add_argument("--new", default=None, help="覆盖成片路径（自测用）")

    p = sub.add_parser("determinism")
    p.add_argument("--frame", type=int, default=900)
    p.add_argument("--composition", default="Lyric2TTML")
    p.add_argument("--entry", default=None)
    p.add_argument("--prefix", default="indep",
                   help="输出文件名前缀（默认 indep_xxx_runN.png，绝不覆盖别人的文件）")
    p.add_argument("--hash-only", default="",
                   help="逗号分隔的已有 PNG 路径：只做哈希/像素互比，不做渲染")

    args = ap.parse_args(argv)
    fn = dict(baseline=cmd_baseline, blocks=cmd_blocks, audio=cmd_audio,
              frames=cmd_frames, pair=cmd_pair, determinism=cmd_determinism,
              all=cmd_all)[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
