"""ONNX 后端 vs torch 后端：**在真实音频上**比数值与速度。

    python tools/compare_campplus_backends.py

为什么要单独一个脚本（用户任务书明确要求）：
"重点检查 PyTorch 与 ONNX Runtime 的数值一致性"、并"检查 CPU 推理、GPU 推理"。
合成随机音频上已经验过（max|Δ| ≤ 2.3e-05），但**真实歌声**的分布不同
（有伴奏、有拖音、有静音段），必须用真素材再验一遍。

顺带量速度：CAM++ 很小，重点是确认 ONNX 在 **CPU** 上也不慢到影响体验
（生产里每行提一次声纹，一首歌 30~50 行）。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import numpy as np  # noqa: E402

AUDIO = os.path.join(
    HERE, "uploads",
    "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")


def read_audio(path: str, sr: int = 16000) -> np.ndarray:
    import av
    with av.open(path) as c:
        st = c.streams.audio[0]
        rs = av.AudioResampler(format="s16", layout="mono", rate=sr)
        ch = []
        for fr in c.decode(st):
            for rf in rs.resample(fr):
                ch.append(rf.to_ndarray().reshape(-1))
    return np.concatenate(ch).astype("float32") / 32768.0


def main() -> int:
    import speaker_campplus as sp
    if not os.path.isfile(AUDIO):
        print(f"没有素材：{AUDIO}")
        return 2

    wave = read_audio(AUDIO)
    print(f"音频：{len(wave) / sp.SR:.1f} 秒")
    print(f"ONNX 文件：{sp._ONNX}（存在={os.path.exists(sp._ONNX)}）")

    # 挑若干真实片段：跨度不同长短（含非 100 整数倍帧数，正是补丁修的场景）
    spans = [(5.0, 8.0), (20.0, 28.0), (40.0, 40.5), (60.0, 75.0),
             (100.0, 101.2), (150.0, 165.0)]
    feats = []
    for b, e in spans:
        seg = wave[int(b * sp.SR):int(e * sp.SR)]
        f = sp.fbank(seg, sp.SR)
        feats.append((b, e, f))

    print("\n" + "=" * 74)
    print("  数值一致性（真实音频）")
    print("=" * 74)
    print("  ⚠️ 要分两个尺度看（这一点很容易判错）：")
    print("     * **原始输出**的 max|Δ| 会随该行向量范数放大"
          "（真实歌声范数 ~几十，合成音频只有 ~11）——")
    print("       直接拿绝对误差比会得出「不一致」的错结论；")
    print("     * 而**下游真正用的**是 `embed()` 返回的 **L2 归一化**向量，")
    print("       所以判据必须是「归一化后的距离」或余弦相似度。")
    print()
    print("  片段(s)          帧数   原始 max|Δ|   归一化 max|Δ|   余弦")
    print("  " + "-" * 62)
    worst_raw, worst_unit, worst_cos = 0.0, 0.0, 1.0
    for b, e, f in feats:
        v_onnx = sp._embed_onnx(f)
        v_torch = sp._embed_torch(f)
        raw = float(np.abs(v_onnx - v_torch).max())
        n1 = v_onnx / (np.linalg.norm(v_onnx) + 1e-9)
        n2 = v_torch / (np.linalg.norm(v_torch) + 1e-9)
        unit = float(np.abs(n1 - n2).max())
        c = float(np.dot(n1, n2))
        worst_raw = max(worst_raw, raw)
        worst_unit = max(worst_unit, unit)
        worst_cos = min(worst_cos, c)
        print(f"  {b:6.1f}–{e:6.1f}  {f.shape[0]:5d}   {raw:.3e}     "
              f"{unit:.3e}     {c:.8f}")
    print(f"\n  最坏 原始 max|Δ|   = {worst_raw:.3e}")
    print(f"  最坏 归一化 max|Δ| = {worst_unit:.3e}")
    print(f"  最坏 cos           = {worst_cos:.8f}")
    # 判据：归一化后（= 下游实际使用的量）足够接近即可
    verdict = worst_unit < 1e-3 and worst_cos > 0.99999
    print(f"  {'✅ 归一化后一致（下游用的就是归一化向量）' if verdict else '❌ 不一致'}")

    print("\n" + "=" * 74)
    print("  速度（每行提一次声纹；一首歌约 30~50 行）")
    print("=" * 74)

    def bench(fn, feat, n=8):
        fn(feat)                                  # 预热
        t0 = time.perf_counter()
        for _ in range(n):
            fn(feat)
        return (time.perf_counter() - t0) / n * 1000.0     # ms

    mid = feats[1][2]                             # 8 秒片段
    t_onnx = bench(sp._embed_onnx, mid)
    try:
        t_torch = bench(sp._embed_torch, mid)
    except Exception as ex:                        # noqa: BLE001
        t_torch = float("nan")
        print(f"  （torch 后端不可用：{type(ex).__name__}）")
    print(f"  ONNX(CPU) : {t_onnx:7.1f} ms/行")
    print(f"  torch     : {t_torch:7.1f} ms/行")
    if t_torch == t_torch:
        print(f"  倍率      : {t_torch / t_onnx:.2f}× （>1 表示 ONNX 更快）")
    print(f"\n  估算一首 40 行的歌：ONNX {t_onnx * 40 / 1000:.2f}s"
          f" / torch {t_torch * 40 / 1000:.2f}s")

    print("\n" + "=" * 74)
    print("  结论")
    print("=" * 74)
    print(f"  ✅ ONNX 后端与 torch 在真实音频上等价"
          f"（归一化后 max|Δ|={worst_unit:.2e}，cos={worst_cos:.8f}），"
          f"且**不依赖 PyTorch**"
          if verdict else "  ❌ 数值不一致 —— 不能替换")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
