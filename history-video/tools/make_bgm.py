"""自合成配乐：给介绍片铺一条 60 秒的器乐垫音（无人声、无版权问题）。

为什么自己合成：这支片要发到公开平台，随手拿一首歌当 BGM 会踩版权；而合成出来的
东西是我们自己生成的，不涉及任何第三方权利。做法与 `tools/demo_video/build.py`
里的 `synth_bgm()` 同一套思路（正弦叠加的和弦垫音），但按介绍片的 10 场节奏重排。

用法：
    python history-video/tools/make_bgm.py                 # → history-video/public/bgm.wav
    python history-video/tools/make_bgm.py --duration 62   # 改时长

设计：
* 和弦床随剧情走：开场单音（只有根音）→ 中段转调加厚 → 实测段明亮 → 尾声回到单音收束；
* 每场切换时给一个很轻的低频脉冲（"章节感"），不做鼓点（鼓点会跟字幕抢注意力）；
* 整体 -18 dBFS 左右，人声/旁白将来叠上去也不会被压住；
* 首尾各 1.5 秒淡入淡出。
"""
from __future__ import annotations

import argparse
import os
import wave

import numpy as np

SR = 44100
ROOT_HZ = 110.0  # A2

# 场景（与 src/epic/timing.ts 的 SCENES 对齐）：(起始秒, 时长秒, 和弦, 强度)
# 和弦写法 = 相对根音的半音偏移；0=根音，7=五度，-5=低四度……
SECTIONS = [
    (0.0, 3.5, [0, 12], 0.55),          # 序：只有根音 + 八度，留白
    (3.5, 8.0, [0, 7, 12], 0.70),       # 打轴
    (11.5, 5.5, [0, 3, 7], 0.72),       # 声音（小三度，略阴）
    (17.0, 5.0, [-5, 0, 7], 0.66),      # 对比
    (22.0, 5.5, [0, 5, 12], 0.74),      # 结构
    (27.5, 6.0, [-3, 2, 7], 0.76),      # 对唱
    (33.5, 5.0, [0, 4, 9], 0.74),       # 和声
    (38.5, 7.0, [0, 3, 10], 0.78),      # 编辑台
    (45.5, 6.0, [0, 7, 14], 0.86),      # 实测（明亮）
    (51.5, 8.5, [0, 12], 0.52),         # 尾声：回到根音 + 八度，收束
]


def hz(semi: int) -> float:
    return ROOT_HZ * (2.0 ** (semi / 12.0))


def synth(duration: float) -> np.ndarray:
    n = int(duration * SR)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float64)

    # 和弦床：每段内部缓慢呼吸，段与段之间交叉 0.8s 淡化，避免硬切
    for start, dur, tones, level in SECTIONS:
        a, b = int(start * SR), int(min(duration, start + dur) * SR)
        if b <= a:
            continue
        seg = t[a:b] - t[a]
        env = np.minimum(1.0, seg / 0.8) * np.minimum(1.0, (seg[-1] - seg) / 0.8)
        env = np.clip(env, 0.0, 1.0)
        tone = np.zeros_like(seg)
        for j, s in enumerate(tones):
            f = hz(s)
            # 每个音两路：基频 + 轻微失谐，声音才不"电子"
            tone += (0.6 ** j) * np.sin(2 * np.pi * f * seg)
            tone += (0.22 ** j) * np.sin(2 * np.pi * f * 1.003 * seg)
        out[a:b] += tone * env * level * 0.085

    # 章节脉冲：每次换场给一个 60Hz 的短促低频，标出"翻页"
    for start, _dur, _tones, _level in SECTIONS[1:]:
        a = int(start * SR)
        b = int(min(duration, start + 0.45) * SR)
        if b <= a:
            continue
        seg = np.arange(b - a) / SR
        pulse = np.sin(2 * np.pi * 60.0 * seg) * np.exp(-seg * 9.0)
        out[a:b] += pulse * 0.10

    # 很慢的整体起伏，避免听起来像循环贴片
    out *= 0.82 + 0.18 * np.sin(2 * np.pi * t / 27.0)

    # 首尾淡入淡出
    fade = int(1.5 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)

    # 归一到一个安全的电平：留足余量（将来叠旁白也不会削顶）
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.30).astype(np.float32)


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--out", default=os.path.join(here, "public", "bgm.wav"))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    track = synth(args.duration)
    pcm = (track * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    with wave.open(args.out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())

    size = os.path.getsize(args.out)
    peak_db = 20 * np.log10(float(np.max(np.abs(track))) + 1e-9)
    print(f"→ {args.out}")
    print(f"  {args.duration:.1f}s / {SR}Hz / 立体声 / {size / 1024 / 1024:.2f}MB / 峰值 {peak_db:.1f} dBFS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
