"""宣传片组装：中文旁白 + 原创配乐 + ffmpeg 编码。

    python tools/demo_video/build.py            # 录帧 + 配音 + 编码（全流程）
    python tools/demo_video/build.py --no-record  # 已有帧，只重新配音/编码

产出：.demo/out/Lyric2TTML-promo.mp4

--------------------------------------------------------------------------
几条设计约束（都是踩过或刻意选的）
--------------------------------------------------------------------------
* **旁白用 Windows 自带的中文语音**（Microsoft Huihui），不引入任何 TTS 依赖。
  ⚠️ 走 PowerShell 的 System.Speech，且 .ps1 必须带 **UTF-8 BOM**
  （PowerShell 5.1 对无 BOM 的脚本按 GBK 解码，中文注释会直接语法错误）。

* **配乐是自己用 numpy 合成的**（缓慢和弦垫音）。**不能**随手拿一首歌当 BGM ——
  发到 B 站会踩版权，而且开源项目更不该这么干。合成出来的东西没有版权问题。

* **画面逐帧渲染**（record.js 里 seek(t)），不是录屏；见 record.js 的说明。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(HERE)

DEMO = ".demo"
FRAMES = os.path.join(DEMO, "frames")
NARR = os.path.join(DEMO, "narr")
OUTDIR = os.path.join(DEMO, "out")

TOTAL = 62.0
FPS = 30
SR = 44100

# (场景起始秒, 旁白文本)。文案长度要配得上场景时长 ——
# 场景时长见 slides.html 的 SCENES，Huihui 正常语速约 4.5 字/秒。
NARRATION = [
    (0.9, "把普通歌词，变成逐字时间轴。"),
    (7.6, "现在的歌词文件大多只有整行时间，"
          "播放器里只能整句高亮。唱到哪个字，你是看不出来的。"),
    (17.6, "这个工具只要两样东西：一段音频，一份歌词。"
           "所有时间戳都来自真实音频，不是按字数估算的。"),
    (25.6, "逐字时间轴、对唱分声部、背景和声，"
           "还有一个人工精修的编辑台。"),
    (41.6, "实测：字全唱对时匹配率百分之百，"
           "对唱分声部和人工标注的一致率百分之八十五。"
           "而且全程离线。"),
    (52.6, "Lyric2TTML，开源，MIT 协议。欢迎试用。"),
]

PS_TTS = '''Add-Type -AssemblyName System.Speech
$sr = New-Object System.Speech.Synthesis.SpeechSynthesizer
$sr.SelectVoice("Microsoft Huihui Desktop")
$sr.Rate = 1
Get-Content -LiteralPath "{list}" -Encoding UTF8 | ForEach-Object {{
  $p = $_ -split "\\t"
  if ($p.Count -ge 2) {{
    $sr.SetOutputToWaveFile($p[0])
    $sr.Speak($p[1])
  }}
}}
$sr.Dispose()
Write-Host "done"
'''


def run(cmd, **kw):
    print("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.call(cmd, **kw)


PS_HEAD = '''Add-Type -AssemblyName System.Speech
$sr = New-Object System.Speech.Synthesis.SpeechSynthesizer
$sr.SelectVoice("Microsoft Huihui Desktop")
$sr.Rate = 1
'''


def make_narration() -> list:
    """逐句合成旁白，返回 [(start, wav_path, 时长), ...]

    ⚠️ 这里**把每一句都硬编码成三条语句**（SetOutputToWaveFile / Speak /
    SetOutputToNull），不用循环、也不读外部文件。踩过的两种失败写法：

      1. 清单文件 + `Get-Content` 再 `-split "\\t"` 解析 → 解析静默失败，一个文件都没有；
      2. `Get-Content | ForEach-Object { SetOutputToWaveFile; Speak; ... }` →
         脚本报"tts 6 lines"（循环确实跑了 6 次），但**产出的 wav 全是 0 字节**。
         同样的结构在 prepare.py 里是好的，差异没定位到 —— 与其继续查，
         不如用已经验证可行的硬编码写法：只有 6 句话，展开写最稳。
    """
    os.makedirs(NARR, exist_ok=True)
    body = [PS_HEAD]
    for i, (_, text) in enumerate(NARRATION):
        p = os.path.abspath(os.path.join(NARR, f"n{i}.wav")).replace("\\", "/")
        safe = text.replace('"', '""')          # PowerShell 里 " 用 "" 转义
        body.append(f'$sr.SetOutputToWaveFile("{p}")')
        body.append(f'$sr.Speak("{safe}")')
        body.append('$sr.SetOutputToNull()')
        body.append('')
    body += ['$sr.Dispose()', 'Write-Host "tts ok"', '']
    ps_path = os.path.abspath(os.path.join(NARR, "tts.ps1"))
    io.open(ps_path, "w", encoding="utf-8-sig", newline="\r\n").write("\n".join(body))

    print("  合成中文旁白 …")
    # 已经合成过就跳过 —— 便于"先在终端手动跑一次 tts.ps1、再回来组装"。
    # ⚠️ 为什么可能需要手动跑：从 Python 用 subprocess 调 PowerShell 时，
    # SAPI 会建了文件却写成 **0 字节**（同一条命令在终端里手动跑完全正常）。
    # 跟 Edge 那次是同一类环境问题（子进程 + 管道/句柄），没再深挖。
    done = all(os.path.exists(os.path.join(NARR, f"n{i}.wav"))
               and os.path.getsize(os.path.join(NARR, f"n{i}.wav")) > 1000
               for i in range(len(NARRATION)))
    if done:
        print("    （旁白已存在，跳过合成）")
    else:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", ps_path], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=600)
        if r.returncode != 0:
            raise SystemExit("TTS 失败")

    out = []
    for i, (start, text) in enumerate(NARRATION):
        p = os.path.abspath(os.path.join(NARR, f"n{i}.wav"))
        size = os.path.getsize(p) if os.path.exists(p) else -1
        if size < 1000:
            raise SystemExit(f"TTS 没产出 {p}（{size} 字节）")
        with wave.open(p, "rb") as w:
            d = w.getnframes() / w.getframerate()
        print(f"    {start:5.1f}s  {d:4.1f}s  {text[:26]}…")
        out.append((float(start), p, d))
    return out


def read_mono(path: str):
    """读 wav → (float32 单声道, 采样率)。SAPI 出的是 22.05k 单声道。"""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    else:
        raise SystemExit(f"不支持的位深 {sw}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def resample(a: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return a
    n = int(len(a) * sr_out / sr_in)
    x = np.linspace(0, len(a) - 1, n)
    return np.interp(x, np.arange(len(a)), a).astype(np.float32)


def synth_bgm(dur: float) -> np.ndarray:
    """自己合成的柔和和弦垫音（无版权问题）。

    简单的四和弦循环 + 缓慢起伏 + 一个很轻的低音脉冲。
    和弦进行本身不受版权保护，这里也全是自己合成的正弦波。
    """
    t = np.arange(int(dur * SR)) / SR
    # Am - F - C - G，每个和弦 4 秒
    chords = [(220.00, 261.63, 329.63),   # Am
              (174.61, 220.00, 261.63),   # F
              (196.00, 246.94, 329.63),   # C/G 近似
              (196.00, 246.94, 293.66)]   # G
    out = np.zeros_like(t)
    bar = 4.0
    for i in range(int(np.ceil(dur / bar))):
        a, b = int(i * bar * SR), int(min((i + 1) * bar, dur) * SR)
        if b <= a:
            break
        seg = t[a:b] - t[a]
        chord = chords[i % len(chords)]
        # 每个和弦内部做缓慢的淡入淡出，衔接不突兀
        env = np.minimum(1.0, seg / 2.2) * np.minimum(1.0, (bar - seg) / 1.6)
        env = np.clip(env, 0, 1)
        tone = np.zeros_like(seg)
        for j, f in enumerate(chord):
            tone += (0.5 ** j) * np.sin(2 * np.pi * f * seg)
        tone += 0.25 * np.sin(2 * np.pi * chord[0] / 2 * seg)     # 低八度铺底
        out[a:b] += tone * env * 0.09
    # 整体再做一次很慢的起伏，避免听起来像循环贴片
    out *= 0.75 + 0.25 * np.sin(2 * np.pi * t / 23.0)
    # 首尾淡入淡出
    fade = int(1.2 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out.astype(np.float32)


def build_audio(narr: list) -> str:
    dur = TOTAL
    track = np.zeros(int(dur * SR), dtype=np.float32)

    # 旁白
    for start, path, _ in narr:
        a, sr = read_mono(path)
        a = resample(a, sr, SR)
        i = int(start * SR)
        j = min(len(track), i + len(a))
        if j > i:
            track[i:j] += a[: j - i] * 1.0

    # 配乐（压低，别盖住人声）
    track += synth_bgm(dur) * 0.55

    # 简单限幅 + 归一
    peak = float(np.max(np.abs(track))) or 1.0
    track = track / peak * 0.89

    pcm = (track * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    out = os.path.abspath(os.path.join(OUTDIR, "audio.wav"))
    os.makedirs(OUTDIR, exist_ok=True)
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())
    print(f"  → {out}  {dur:.1f}s")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-record", action="store_true", help="跳过录帧")
    ap.add_argument("--fps", type=float, default=FPS)
    args = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)

    if not args.no_record:
        print(f"  用浏览器逐帧渲染 {int(TOTAL * args.fps)} 帧（渲染速度约 1.7 帧/秒，"
              f"预计 {int(TOTAL * args.fps / 1.7 / 60)} 分钟）…")
        # ⚠️ 录帧交给 PowerShell 编排：Edge 必须用 Start-Process 启动，
        # Python 的 Popen 起不动它；详见 tools/demo_video/record.ps1 的注释。
        rc = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", "tools/demo_video/record.ps1",
                  "-Fps", str(args.fps), "-OutDir", FRAMES])
        if rc != 0:
            return rc

    narr = make_narration()
    audio = build_audio(narr)

    frames = sorted(f for f in os.listdir(FRAMES) if f.endswith(".jpg"))
    print(f"  编码视频（{len(frames)} 帧 @ {args.fps}fps）…")
    video = os.path.abspath(os.path.join(OUTDIR, "_video.mp4"))
    rc = run(["ffmpeg", "-y", "-loglevel", "error",
              "-framerate", str(args.fps),
              "-i", os.path.join(FRAMES, "f%05d.jpg"),
              "-c:v", "libx264", "-preset", "slow", "-crf", "19",
              "-pix_fmt", "yuv420p", "-movflags", "+faststart", video])
    if rc != 0:
        return rc

    final = os.path.abspath(os.path.join(OUTDIR, "Lyric2TTML-promo.mp4"))
    print("  合成音轨 …")
    rc = run(["ffmpeg", "-y", "-loglevel", "error",
              "-i", video, "-i", audio,
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
              "-shortest", "-movflags", "+faststart", final])
    if rc != 0:
        return rc
    os.remove(video)

    mb = os.path.getsize(final) / 1024 / 1024
    print(f"\n  完成 → {final}  （{mb:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
