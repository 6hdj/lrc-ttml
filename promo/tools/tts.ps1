# 逐句合成中文旁白（Windows 自带 SAPI，无第三方 TTS 依赖）
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File promo/tools/tts.ps1 -OutDir .demo/narr2
#
# 与 tools/demo_video/build.py 的做法一致，但**独立成脚本**，方便改文案后立刻重录：
#   1. 不联网、不装包，用 Microsoft Huihui Desktop 中文语音；
#   2. 每句一个 wav，之后再按时间轴摆放（间隔留白由摆放决定，不靠句尾静音）；
#   3. Rate=-2 是可读性默认值（比默认慢一点，配 34–40px 的字幕更跟得上）；
#      要改语速用 -Rate。
#
# ⚠️ 本文件必须存成 **UTF-8 带 BOM**：PowerShell 5.1 对无 BOM 的脚本按 GBK 解码，
#    含中文的字符串会直接语法错误或变乱码（本项目踩过）。
# ⚠️ 不要改成"清单文件 + Get-Content 循环 + -split"：build.py 的注释里记录过，
#    那种写法在这台机器上会**静默产出 0 字节 wav**。句子少，展开写最稳。
#
# 用法（改文案时）：只改下面 $Lines 数组，别动结构；改完跑：
#   powershell -NoProfile -ExecutionPolicy Bypass -File promo/tools/tts.ps1 -OutDir .demo/narr2
#   python promo/tools/measure_narration.py .demo/narr2
param(
    [string]$OutDir = ".demo/narr2",
    [string]$Voice  = "Microsoft Huihui Desktop",
    [int]$Rate      = -2
)

$ErrorActionPreference = "Stop"

# ── 文案（每句一条；顺序 = 出场顺序）────────────────────────────
# 当前 6 句是基线《Lyric2TTML-宣传片》的原文；改内容重做时在这里替换。
$Lines = @(
    "把普通歌词，变成逐字时间轴。",
    "现在的歌词文件大多只有整行时间，播放器里只能整句高亮。唱到哪个字，你是看不出来的。",
    "这个工具只要两样东西：一段音频，一份歌词。所有时间戳都来自真实音频，不是按字数估算的。",
    "逐字时间轴、对唱分声部、背景和声，还有一个人工精修的编辑台。",
    "实测：字全唱对时匹配率百分之百，对唱分声部和人工标注的一致率百分之八十五。而且全程离线。",
    "Lyric2TTML，开源，MIT 协议。欢迎试用。"
)

Add-Type -AssemblyName System.Speech
$sr = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $sr.SelectVoice($Voice) } catch {
    Write-Host "警告：找不到语音 '$Voice'，用系统默认中文语音" -ForegroundColor Yellow
}
$sr.Rate = $Rate

if (-not (Test-Path -LiteralPath $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}

Write-Host "语音：$($sr.Voice.Name)  Rate=$Rate"
Write-Host "输出目录：$((Resolve-Path -LiteralPath $OutDir).Path)"

for ($i = 0; $i -lt $Lines.Count; $i++) {
    $path = Join-Path (Resolve-Path -LiteralPath $OutDir).Path "n$i.wav"
    $sr.SetOutputToWaveFile($path)
    $sr.Speak($Lines[$i])
    $sr.SetOutputToNull()
    $size = (Get-Item -LiteralPath $path).Length
    $flag = if ($size -lt 1000) { "  ← ⚠️ 可疑（<1KB）" } else { "" }
    Write-Host ("  n{0}.wav  {1,8} B   {2}" -f $i, $size, $Lines[$i].Substring(0, [Math]::Min(18, $Lines[$i].Length)) + $flag)
}
$sr.Dispose()
Write-Host "完成。下一步：python promo/tools/measure_narration.py $OutDir"
