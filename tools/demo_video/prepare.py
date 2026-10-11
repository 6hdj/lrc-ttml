"""演示视频素材准备：把示例歌词用 TTS 读成音频，当作"人声"跑真实转换。

为什么这么做（而不是直接拿一首真歌录屏）：
以《在出发之前》为例，那是**商业歌曲**。把它的歌词打进一个要发 B 站的宣传视频里
是有版权风险的。而用自撰歌词 + TTS 朗读音频：

  * 全流程**零版权风险**，可以放心公开；
  * 展示的仍然是**真实的流水线** —— Whisper 真的在识别这段音频，
    DP 对齐真的在算逐字时间，不是假动画。

⚠️ TTS 走 PowerShell 的 System.Speech（Windows 自带 Microsoft Huihui 中文女声）。
   两个坑：
   1. 用 `-Command` 传中文参数会被编码搞坏 —— 必须**落成 .ps1 文件**再 `-File` 跑；
   2. .ps1 必须带 **UTF-8 BOM**，否则 PowerShell 5.1 按 GBK 解码 → 语法错误。

产物（都在 .demo/ 下，不进仓库）：
    .demo/vocal.wav       TTS 朗读音频（当作"人声"）
    .demo/lyrics.txt      示例歌词（纯歌词，无注释）
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(HERE)
OUT = ".demo"
CHUNKS = os.path.join(OUT, "chunks")

# 演示只取前面几段，够用又不冗长
LINES = [
    "夜色落在窗台上",
    "风把旧信翻了几行",
    "我把灯留给你",
    "等一句久违的回响",
    "走过了很远的地方",
    "才懂归途也是远方",
    "若某天我们再遇见",
    "请先说一句别来无恙",
]

PS_TEMPLATE = '''Add-Type -AssemblyName System.Speech
$root = "{root}"
$sr = New-Object System.Speech.Synthesis.SpeechSynthesizer
$sr.SelectVoice("Microsoft Huihui Desktop")
$sr.Rate = -1
$n = 0
Get-Content -LiteralPath "$root/lyrics.txt" -Encoding UTF8 | ForEach-Object {{
  $t = $_.Trim()
  if ($t -ne "") {{
    $out = "$root/chunks/l{{0:D2}}.wav" -f $n
    $sr.SetOutputToWaveFile($out)
    $sr.Speak($t)
    $n++
  }}
}}
$sr.Dispose()
Write-Host "synth $n lines"
'''


def read_wav(path: str):
    with wave.open(path, "rb") as w:
        return w.getparams(), w.readframes(w.getnframes())


def main() -> int:
    os.makedirs(CHUNKS, exist_ok=True)

    lyrics = os.path.join(OUT, "lyrics.txt")
    io.open(lyrics, "w", encoding="utf-8", newline="\n").write("\n".join(LINES) + "\n")

    # 写 .ps1（UTF-8 **带 BOM**，见文件头说明）
    # ⚠️ 路径用**正斜杠**：PowerShell 的双引号字符串里 `\\` 不是转义字符
    # （跟 Python 不一样），写成 `E:\\HXJ\\...` 会得到两个字面反斜杠 → 路径无效，
    # 表现是 TTS 一个文件都没产出、后面读 wav 报 EOFError。
    ps_path = os.path.abspath(os.path.join(OUT, "synth.ps1"))
    ps = PS_TEMPLATE.format(root=os.path.abspath(OUT).replace("\\", "/"))
    io.open(ps_path, "w", encoding="utf-8-sig", newline="\r\n").write(ps)

    print("  用 TTS 合成朗读音频 …")
    have_all = all(os.path.exists(os.path.join(CHUNKS, f"l{i:02d}.wav"))
                   for i in range(len(LINES)))
    if have_all:
        print("    （chunks 已存在，跳过合成）")
    else:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", ps_path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        print("    " + ((r.stdout or "").strip() or (r.stderr or "").strip()[:300]))

    parts = []
    for i, line in enumerate(LINES):
        p = os.path.join(CHUNKS, f"l{i:02d}.wav")
        if not os.path.exists(p):
            print(f"    ! 缺 {p}")
            continue
        params, frames = read_wav(p)
        d = len(frames) / (params.sampwidth * params.nchannels) / params.framerate
        print(f"    {i + 1:2}. {line}   {d:.2f}s")
        parts.append(frames)

    params, _ = read_wav(os.path.join(CHUNKS, "l00.wav"))
    sr, sw, ch = params.framerate, params.sampwidth, params.nchannels
    silence = lambda sec: b"\x00" * int(sr * sec) * sw * ch      # noqa: E731
    joined = silence(0.8)
    for k, fr in enumerate(parts):
        joined += fr
        if k != len(parts) - 1:
            joined += silence(0.45)      # 行间留白，制造"乐句"间隔
    vocal = os.path.join(OUT, "vocal.wav")
    with wave.open(vocal, "wb") as w:
        w.setparams(params)
        w.writeframes(joined)
    print(f"\n  → {vocal}  {len(joined) / (sw * ch) / sr:.1f}s")
    print(f"  → {lyrics}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
