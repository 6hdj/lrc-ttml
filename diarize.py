"""
演唱者分段（speaker diarization）。

主路径：**CAM++ 中文声纹模型**（ModelScope ``iic/speech_campplus_sv_zh-cn_16k-common``，
见 ``models/campplus/``），由 ``speaker_campplus.py`` 加载。
每一行取多个 1.5s 窗口的声纹平均，再用**平均连接（average-linkage）层次聚类**分组，
阈值默认 0.42 —— 实测标定：

    同一歌手不同行       余弦相似度 均值 0.575
    模拟对唱 同人        0.70 ~ 0.75
    模拟对唱 异人        0.19 ~ 0.29   ← 与同人之间有 0.4 以上的间隔

这比"只用音高/音色"可靠得多：单个人唱不同乐句的自然波动不再会被误当成第二个人。

做不到的（需要声源分离，不是声纹能解决的）：
* 两个人**同时**张嘴的齐唱 / 和声堆叠（一行里混了两个声纹）
* 声纹极其接近的两位歌手

模型不存在时会退回旧的音高+音色启发式（``_diarize_acoustic``），并明确标注低置信度。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

SR = 16000
WIN = 1.5            # 每行内取声纹的窗口长度（秒）
HOP = 0.75
MAX_SEC = 8.0        # 每行最多用多少秒
SIM_THRESHOLD = 0.35  # 聚类合并阈值（余弦相似度），实测标定见模块说明
CAMPP_THRESHOLD = 0.31  # 模型官方"是否同一人"阈值，仅作参考

try:
    import speaker_campplus as _spk
except Exception:                                     # pragma: no cover
    _spk = None


def _has_model() -> bool:
    """**每次都问一遍**，不再在 import 时定死。

    ⚠️⚠️ 原来这里是个模块级常量 `_HAS_MODEL = _spk.available()`，在
    **import 那一刻**算一次。2026-10-13 起 CAM++ 有了 ONNX 后端、而且
    后端是**动态选**的（要按"文件在不在 + onnxruntime/torch 能不能用"判断），
    定死就会出两种错：
      * 打包版里 ONNX 图打了进来，常量却还是 False → 分声部**静默退回按标记**
        （实测产物 v1 32 行 / v2 0 行，等于没生效）；
      * 用户在界面里下载完权重/运行时组件后，进程内也不会变 True。
    改成一个函数调用，代价可以忽略（就是几个 `os.path.exists`）。
    """
    if _spk is None:
        return False
    try:
        return bool(_spk.available())
    except Exception:                                 # noqa: BLE001
        return False


# --------------------------------------------------------------------------
@dataclass
class DiarizeResult:
    labels: List[int] = field(default_factory=list)   # 每行歌手编号（按出场顺序 0,1,2…）
    n_singers: int = 1
    confidence: float = 0.0                           # 0~1
    reason: str = ""
    sim_within: float = 0.0                           # 簇内平均相似度
    sim_between: float = 0.0                          # 簇间平均相似度
    method: str = ""                                  # campplus / acoustic
    uncertain: List[int] = field(default_factory=list)  # 归属把握不足的行下标（建议人工核对）


# --------------------------------------------------------------------------
# 声纹提取
# --------------------------------------------------------------------------
def line_embedding(audio: np.ndarray, begin: float, end: float, sr: int = SR,
                   win: float = WIN, hop: float = HOP, max_sec: float = MAX_SEC,
                   mode: str = "whole") -> Optional[np.ndarray]:
    """一行的声纹。

    ``mode="whole"``（默认）：**整行一次前向**。
    ``mode="window"``：切成窗口分别提声纹再平均（旧实现，已弃用）。

    ⚠️ 为什么默认改成整行 —— 用 AMLL DB 的人工真值实测（《在出发之前》32 行跨度，
    强判 2 簇与真值的一致率）：

    ==============================  ==========
    整行 embedding（原始混音）        **90.6%**
    整行 embedding（Demucs 人声轨）    53.1%
    窗口平均（不论哪条音频）           **50.0%**
    ==============================  ==========

    也就是说：**窗口平均会把歌手身份平均掉**（一行之内音高/音区变化很大，
    1.5s 窗口的 CAM++ 向量本身就不稳，平均之后身份方向相互抵消），
    叠加 Demucs 分离对人声共振峰的平滑，就只剩瞎猜。
    另外旧实现还会"丢掉低能量窗口"——用的若是**原始混音**，
    留下的恰恰是**伴奏最响**的窗口，等于专门挑最不像人声的片段去提声纹。
    """
    if not _has_model():
        return None
    total = len(audio) / sr
    end = min(end, begin + max_sec, total)
    if end - begin < 0.3:
        return None

    if mode == "whole":
        seg = audio[int(begin * sr): int(end * sr)]
        if len(seg) < int(0.3 * sr):
            return None
        return _spk.embed(seg, sr)

    # ---- 旧路径（保留以便对照/回退）----
    windows = []
    t = begin
    while t < end:
        seg = audio[int(t * sr): int(min(t + win, end) * sr)]
        if len(seg) >= int(0.4 * sr):
            windows.append(seg)
        t += hop
    if not windows:
        return None
    energies = np.array([float(np.sqrt(np.mean(w ** 2))) for w in windows])
    floor = max(energies.max() * 0.30, 1e-4)
    keep = [w for w, e in zip(windows, energies) if e >= floor]
    vecs = []
    for seg in (keep or windows):
        v = _spk.embed(seg, sr)
        if v is not None:
            vecs.append(v)
    if not vecs:
        return None
    m = np.mean(vecs, axis=0)
    return m / (np.linalg.norm(m) + 1e-9)


def _cosine_matrix(E: np.ndarray) -> np.ndarray:
    E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    return E @ E.T


# --------------------------------------------------------------------------
# 平均连接层次聚类
# --------------------------------------------------------------------------
def cluster_by_average_linkage(S: np.ndarray, threshold: float = SIM_THRESHOLD,
                               min_size: int = 2) -> List[int]:
    """按簇间**平均**相似度合并，直到最佳合并的相似度低于阈值。

    用平均连接而不是单连接，是为了不被个别"串音"的相似偶发高值把两组合并。
    最后把成员过少的簇并入最近的大簇（处理个别提取失败的异常行）。
    """
    n = S.shape[0]
    clusters: List[List[int]] = [[i] for i in range(n)]
    while len(clusters) > 1:
        best = (-2.0, -1, -1)
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                sub = S[np.ix_(clusters[a], clusters[b])]
                avg = float(sub.mean())
                if avg > best[0]:
                    best = (avg, a, b)
        if best[0] < threshold:
            break
        _, a, b = best
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]

    # 小簇合并
    changed = True
    while changed and len(clusters) > 1:
        changed = False
        clusters.sort(key=len)
        if len(clusters[0]) >= min_size:
            break
        small = clusters.pop(0)
        sims = [float(S[np.ix_(small, c)].mean()) for c in clusters]
        target = int(np.argmax(sims))
        clusters[target] = clusters[target] + small
        changed = True

    labels = [0] * n
    for ci, members in enumerate(clusters):
        for m in members:
            labels[m] = ci
    # 按出场顺序重新编号
    order: List[int] = []
    for lab in labels:
        if lab not in order:
            order.append(lab)
    remap = {lab: i for i, lab in enumerate(order)}
    return [remap[l] for l in labels]


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def diarize(audio: np.ndarray, spans: Sequence[Tuple[float, float]],
            n_singers: int = 0, sr: int = SR, threshold: float = SIM_THRESHOLD,
            min_cluster: int = 2, max_singers: int = 6) -> DiarizeResult:
    """
    ``spans``：每行的 (begin, end)，顺序即歌词顺序。
    ``n_singers``：0 = 自动判断；>=2 = 强制聚成这么多组。

    ⚠️ **人数（分几瓣）判不出来，只能靠外部先验（元数据里的艺人数）**。
    用 AMLL DB 的人工真值实测过三种内部判据，全部失败：

    * 平均连接阈值扫描：最好 59.4%；
    * "分离质量"（簇内-簇间 gap / silhouette / inertia 降幅）：
      **单人歌强判 2 簇反而比真对唱更高**（+0.254 vs +0.127）——
      这个指标衡量的是"k-means 分得好不好"，不是"是不是两个人"；
    * 成对相似度的 BIC 双峰检验：真对唱判 1 个成分、单人歌判 2 个（完全反了）。

    所以 ``n_singers=0`` 的自动路径**只适合"明显能分开"的极端情况**；
    正常应由调用方（pipeline）根据元数据把人数传进来。
    音频本身是有信息的（强判 2 簇能到 90.6%），缺的只是"该不该分"这一步。
    """
    n = len(spans)
    if n == 0:
        return DiarizeResult(labels=[], n_singers=1, reason="没有歌词行")
    if n == 1:
        return DiarizeResult(labels=[0], n_singers=1, reason="只有一行")

    if not _has_model():
        return _diarize_acoustic(audio, spans, n_singers, sr)

    embs: List[Optional[np.ndarray]] = [line_embedding(audio, b, e, sr) for b, e in spans]
    valid = [i for i, v in enumerate(embs) if v is not None]
    if len(valid) < 2:
        return DiarizeResult(labels=[0] * n, n_singers=1, method="campplus",
                             reason="有效声纹太少，按单人处理")

    E = np.stack([embs[i] for i in valid])
    S = _cosine_matrix(E)

    if n_singers >= 2:
        # 用 k-means（实测比平均连接贪心好 34 个点，见 _kmeans 文档）
        labels_v = _kmeans(E, n_singers)
        method = "campplus(k-means, 指定人数)"
    else:
        labels_v = cluster_by_average_linkage(S, threshold=threshold, min_size=min_cluster)
        # 太小的簇（个别行的声纹没提干净）并进最近的大簇，避免把独唱误判成对唱。
        # 门槛取 max(2, 10%)：41 行时是 4 行，4 行时是 2 行。
        floor = max(min_cluster, int(round(0.10 * len(labels_v))))
        counts = np.bincount(np.array(labels_v))
        if len(counts) > 1 and counts.min() < floor:
            labels_v = _merge_small_clusters(S, list(labels_v), floor)
        method = "campplus"

    k = len(set(labels_v))
    if k > max_singers:
        # 簇太多：多半是阈值太松，取最大的 max_singers 个簇，其余并入最近簇
        labels_v = _limit_clusters(S, labels_v, max_singers)
        k = len(set(labels_v))

    labels = [0] * n
    for pos, i in enumerate(valid):
        labels[i] = labels_v[pos]

    # 统计簇内/簇间相似度，作为置信度参考
    within, between = _sim_stats(S, np.array(labels_v))
    conf = 1.0 if k == 1 else float(np.clip((within - between) / 0.5, 0.0, 1.0))

    # 每行到两个簇心的"余量"：余量小 = 这一行两个人都像。
    # ⚠️ 这是**弱提示**，别当成置信度：本曲实测 3 个错行的余量排名是 1/32、13/32、23/32，
    # 要全抓得列 24 行（75% 误报）。所以调用方只取最小的 3 行，并明说参考价值有限。
    uncertain: List[int] = []
    if k >= 2:
        cents = np.stack([E[np.array(labels_v) == c].mean(0) for c in range(k)])
        d = np.linalg.norm(E[:, None, :] - cents[None, :, :], axis=2)
        ds = np.sort(d, axis=1)
        margins = (ds[:, 1] - ds[:, 0]) / (ds[:, 1] + 1e-9)
        order = np.argsort(margins)
        uncertain = [valid[i] for i in order[:3]]

    if k == 1:
        reason = f"判定为单人演唱（{len(valid)} 行有效声纹，簇内平均相似度 {within:.2f}）"
    else:
        reason = (f"判定为 {k} 个声部（簇内 {within:.2f} / 簇间 {between:.2f}，"
                  f"阈值 {threshold:.2f}）")
    return DiarizeResult(labels=labels, n_singers=k, confidence=round(conf, 3),
                         reason=reason, sim_within=round(within, 3),
                         sim_between=round(between, 3), method=method,
                         uncertain=uncertain)


def _sim_stats(S: np.ndarray, labels: np.ndarray) -> Tuple[float, float]:
    same, diff = [], []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            (same if labels[i] == labels[j] else diff).append(S[i, j])
    w = float(np.mean(same)) if same else 1.0
    b = float(np.mean(diff)) if diff else 0.0
    return w, b


def _kmeans(E: np.ndarray, k: int, restarts: int = 120, iters: int = 200,
            seed: int = 0) -> List[int]:
    """在声纹向量上做 k-means（多次重启取最小 inertia）。

    为什么强制人数时用它而不是平均连接合并：实测（《在出发之前》32 行跨度，
    与 AMLL DB 人工真值比）——

    ==========================  ==========
    ``_force_k`` 平均连接贪心     56.2%（会退化成 31/1）
    **k-means（本函数）**         **90.6%**（簇 20/12）
    ==========================  ==========

    平均连接在 192 维余弦上会被个别离群行带偏：它一路合并"最像的"两簇，
    最后往往只剩一个孤立的紧凑小簇 + 一个包罗万象的大簇。
    """
    rng = np.random.default_rng(seed)
    X = np.asarray(E, dtype=np.float64)
    n = len(X)
    if k <= 1 or n <= k:
        return [0] * n
    best_lab, best_inertia = None, np.inf
    for _ in range(restarts):
        C = X[rng.choice(n, k, replace=False)].copy()
        lab = np.zeros(n, dtype=int)
        for _ in range(iters):
            d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
            lab = d.argmin(1)
            for c in range(k):
                if (lab == c).any():
                    C[c] = X[lab == c].mean(0)
        inertia = float(((X - C[lab]) ** 2).sum())
        if inertia < best_inertia:
            best_inertia, best_lab = inertia, lab.copy()
    # 保证标签紧凑（0..k-1）且顺序稳定
    return _compact(list(best_lab))


def _compact(labels: List[int]) -> List[int]:
    order: List[int] = []
    for lab in labels:
        if lab not in order:
            order.append(lab)
    remap = {lab: i for i, lab in enumerate(order)}
    return [remap[l] for l in labels]


def _force_k(S: np.ndarray, k: int) -> List[int]:
    """强制分成 k 组：用平均连接一直合并到只剩 k 组"""
    n = S.shape[0]
    clusters: List[List[int]] = [[i] for i in range(n)]
    while len(clusters) > k:
        best = (-2.0, -1, -1)
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                avg = float(S[np.ix_(clusters[a], clusters[b])].mean())
                if avg > best[0]:
                    best = (avg, a, b)
        _, a, b = best
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]
    labels = [0] * n
    for ci, members in enumerate(clusters):
        for m in members:
            labels[m] = ci
    order: List[int] = []
    for lab in labels:
        if lab not in order:
            order.append(lab)
    remap = {lab: i for i, lab in enumerate(order)}
    return [remap[l] for l in labels]


def _limit_clusters(S: np.ndarray, labels: List[int], kmax: int) -> List[int]:
    uniq = sorted(set(labels), key=lambda c: -labels.count(c))
    keep = set(uniq[:kmax])
    centro: Dict[int, List[int]] = {}
    for i, c in enumerate(labels):
        centro.setdefault(c, []).append(i)
    out = list(labels)
    for c in uniq[kmax:]:
        members = centro[c]
        sims = {k2: float(S[np.ix_(members, centro[k2])].mean()) for k2 in keep}
        tgt = max(sims, key=sims.get)
        for m in members:
            out[m] = tgt
    order: List[int] = []
    for lab in out:
        if lab not in order:
            order.append(lab)
    remap = {lab: i for i, lab in enumerate(order)}
    return [remap[l] for l in out]


# --------------------------------------------------------------------------
# 退化路径：没有声纹模型时用音高+音色（不可靠，仅应急）
# --------------------------------------------------------------------------
SR_ = 16000
FRAME = 0.04
HOP_ = 0.01
N_FFT = 512
N_MELS = 26
N_MFCC = 13
F0_MIN, F0_MAX = 70.0, 420.0

_EPS = 1e-9


def _hann(n: int) -> np.ndarray:
    return np.hanning(n).astype(np.float32)


def _frame(x: np.ndarray, frame_len: int, hop: int) -> np.ndarray:
    if len(x) < frame_len:
        x = np.pad(x, (0, frame_len - len(x)))
    n = 1 + (len(x) - frame_len) // hop
    idx = np.arange(frame_len)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def f0_track(x: np.ndarray, sr: int = SR_) -> Tuple[np.ndarray, np.ndarray]:
    frames = _frame(np.asarray(x, dtype=np.float32), int(FRAME * sr), int(HOP_ * sr))
    if len(frames) == 0:
        return np.zeros(0), np.zeros(0)
    win = _hann(frames.shape[1])
    f = (frames - frames.mean(axis=1, keepdims=True)) * win
    n = f.shape[1]
    spec = np.fft.rfft(f, n=2 * n, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), axis=1)[:, :n]
    ac = ac / (ac[:, :1] + _EPS)
    lo, hi = int(sr / F0_MAX), int(sr / F0_MIN)
    seg = ac[:, lo:hi]
    if seg.size == 0:
        return np.zeros(len(frames)), np.zeros(len(frames))
    k = np.argmax(seg, axis=1)
    conf = seg[np.arange(len(frames)), k]
    return np.where(conf > 0.35, sr / (k + lo), 0.0), conf


_MEL_CACHE: Dict[Tuple[int, int], np.ndarray] = {}


def mel_filterbank(sr: int = SR_, n_fft: int = N_FFT, n_mels: int = N_MELS,
                   fmin: float = 50.0, fmax: float = 7600.0) -> np.ndarray:
    key = (sr, n_fft)
    if key in _MEL_CACHE:
        return _MEL_CACHE[key]

    def hz2mel(f):
        return 2595.0 * math.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)

    pts = np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2)
    bins = np.floor((n_fft + 1) * mel2hz(pts) / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for i in range(1, n_mels + 1):
        l, c, r = bins[i - 1], bins[i], bins[i + 1]
        c = max(c, l + 1)
        r = max(r, c + 1)
        for k in range(l, min(c, fb.shape[1])):
            fb[i - 1, k] = (k - l) / (c - l)
        for k in range(c, min(r, fb.shape[1])):
            fb[i - 1, k] = (r - k) / (r - c)
    _MEL_CACHE[key] = fb
    return fb


def _dct_basis(n_out: int, n_in: int) -> np.ndarray:
    n = np.arange(n_in)[None, :]
    k = np.arange(n_out)[:, None]
    basis = np.cos(math.pi * k * (2 * n + 1) / (2 * n_in)).astype(np.float32)
    basis[0] *= 1.0 / math.sqrt(2.0)
    return basis


def mfcc(x: np.ndarray, sr: int = SR_) -> np.ndarray:
    frames = _frame(np.asarray(x, dtype=np.float32), int(FRAME * sr), int(HOP_ * sr))
    if len(frames) == 0:
        return np.zeros((0, N_MFCC), dtype=np.float32)
    spec = np.abs(np.fft.rfft(frames * _hann(frames.shape[1]), n=N_FFT, axis=1)) ** 2
    mel = np.log(spec @ mel_filterbank(sr).T + 1e-10)
    return (mel @ _dct_basis(N_MFCC, mel.shape[1]).T).astype(np.float32)


def spectral_centroid(x: np.ndarray, sr: int = SR_) -> float:
    frames = _frame(np.asarray(x, dtype=np.float32), int(FRAME * sr), int(HOP_ * sr))
    if len(frames) == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(frames * _hann(frames.shape[1]), n=N_FFT, axis=1))
    freqs = np.linspace(0.0, sr / 2.0, spec.shape[1])
    total = spec.sum(axis=1)
    ok = total > 1e-6
    if not ok.any():
        return 0.0
    return float(((spec[ok] * freqs).sum(axis=1) / total[ok]).mean())


def _merge_small_clusters(S: np.ndarray, labels: List[int], floor: int) -> List[int]:
    """把成员数少于 floor 的簇并入与它最相似的大簇"""
    out = list(labels)
    while True:
        counts = np.bincount(np.array(out))
        order = np.argsort(counts)
        small = int(order[0])
        if counts[small] >= floor or len(counts) <= 1:
            break
        members = [i for i, c in enumerate(out) if c == small]
        others = [c for c in range(len(counts)) if c != small]
        sims = {c: float(S[np.ix_(members, [i for i, x in enumerate(out) if x == c])].mean())
                for c in others}
        tgt = max(sims, key=sims.get)
        for m in members:
            out[m] = tgt
        order_old = []
        for lab in out:
            if lab not in order_old:
                order_old.append(lab)
        remap = {lab: i for i, lab in enumerate(order_old)}
        out = [remap[l] for l in out]
    return out


def _diarize_acoustic(audio: np.ndarray, spans, n_singers: int, sr: int) -> DiarizeResult:
    """没有声纹模型时的兜底：音高 + 音色聚类。

    实测这个办法**不可靠**（单人歌的乐句间波动比两个人差 9 个半音还大），
    所以只在模型缺失时使用，并且从不自动拆分，必须由用户指定人数。
    """
    n = len(spans)
    feats = []
    for i, (b, e) in enumerate(spans):
        seg = audio[int(b * sr):int(min(e, b + 3.0) * sr)]
        if len(seg) < int(0.2 * sr):
            feats.append(None)
            continue
        f0, _ = f0_track(seg, sr)
        v = f0 > 0
        f0m = float(np.median(f0[v])) if v.any() else 0.0
        m = mfcc(seg, sr)
        mm = m[v[:len(m)]].mean(0) if len(m) and v[:len(m)].any() else (m.mean(0) if len(m) else np.zeros(N_MFCC))
        feats.append((f0m, spectral_centroid(seg, sr), mm))

    ok = [i for i, f in enumerate(feats) if f is not None]
    if len(ok) < 2:
        return DiarizeResult(labels=[0] * n, n_singers=1, method="acoustic",
                             reason="有效特征太少，按单人处理")

    f0 = np.array([feats[i][0] if feats[i][0] > 0 else np.nan for i in ok])
    f0 = np.where(np.isnan(f0), np.nanmedian(f0), f0)
    cen = np.array([max(feats[i][1], 1.0) for i in ok])
    mf = np.stack([feats[i][2] for i in ok])[:, 1:9]

    def z(v):
        return (v - v.mean()) / (v.std() + 1e-6)

    X = np.concatenate([z(np.log2(np.maximum(f0, 1)))[:, None] * 1.2,
                        z(np.log2(cen))[:, None] * 1.6, z(mf) * 0.7], axis=1)
    S = _cosine_matrix(X)

    if n_singers >= 2:
        labels_v = _force_k(S, min(n_singers, len(ok)))
    else:
        return DiarizeResult(
            labels=[0] * n, n_singers=1, method="acoustic",
            reason="未找到 CAM++ 声纹模型，音高/音色启发式不可靠，按单人处理"
                   "（如需分声部请用 --split-singers N 明确指定人数）")

    labels = [0] * n
    for pos, i in enumerate(ok):
        labels[i] = labels_v[pos]
    return DiarizeResult(labels=labels, n_singers=len(set(labels_v)), method="acoustic",
                         reason=f"⚠ 兜底音高/音色聚类（无静音模型），强制 {len(set(labels_v))} 个声部，必须人工核对")


def available() -> bool:
    """是否可以用 CAM++ 声纹模型（**动态判断**，别缓存成常量）"""
    return _has_model()
