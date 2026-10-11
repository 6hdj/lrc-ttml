<#
  录帧编排（Windows）。build.py 会调它。

      powershell -ExecutionPolicy Bypass -File tools\demo_video\record.ps1 -Fps 30

  ⚠️ 为什么编排放在 PowerShell 而不是 Python：
     **Edge 必须用 Start-Process 启动**。用 Python 的 subprocess.Popen 起同样的
     命令行，Edge 会永远跑不完（不退出也不出图；普通 / DETACHED_PROCESS /
     CREATE_NO_WINDOW 三种 creationflags 都试过）。
     Python 里再套一层 `subprocess.run(["powershell", ...], capture_output=True)`
     去启动 Edge，同样会因为**管道句柄被子进程继承**而卡住。
     所以"起浏览器 → 等端口 → 调 node → 收浏览器"整条链都放在这里。

  ⚠️ .ps1 必须带 UTF-8 BOM：PowerShell 5.1 对无 BOM 的脚本按 GBK 解码，
     中文注释会直接变成语法错误。
#>
param(
  [double]$Fps = 30,
  [string]$OutDir = ".demo/frames",
  [int]$DebugPort = 9345,
  [double]$Total = 62.0
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ROOT
$env:PYTHONIOENCODING = "utf-8"
$env:DEMO_TOTAL = "$Total"

$EDGE = @(
  "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
  "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
  "C:\Program Files\Google\Chrome\Application\chrome.exe",
  "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $EDGE) { Write-Host "找不到 Edge / Chrome"; exit 1 }

$prof = Join-Path $ROOT ".demo\edge_profile"

# ⚠️ 必须先杀干净残留的 Edge：之前跑过截图/录帧的话会有若干个 msedge 挂着，
# 新实例会和它们抢调试端口，表现是 WebSocket 连上了但 Page.enable
# 永远没有回包 → node 报 "CDP 超时: Page.enable"（真踩过）。
Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 900
Remove-Item $prof -Recurse -Force -ErrorAction SilentlyContinue

$slides = (Join-Path $ROOT "tools\demo_video\slides.html").Replace("\", "/")
$url = "file:///$slides"

Write-Host "  浏览器：$EDGE"
$br = Start-Process -FilePath $EDGE -PassThru -WindowStyle Hidden -ArgumentList @(
  "--headless", "--disable-gpu", "--hide-scrollbars",
  "--no-first-run", "--no-default-browser-check",
  "--remote-debugging-port=$DebugPort",
  "--user-data-dir=$prof",
  "--window-size=1920,1080", "--force-device-scale-factor=1",
  "about:blank"
)

$code = 1
try {
  $ready = $false
  foreach ($i in 1..50) {
    Start-Sleep -Milliseconds 400
    try {
      Invoke-RestMethod "http://127.0.0.1:$DebugPort/json/version" -TimeoutSec 2 | Out-Null
      $ready = $true; break
    } catch {}
  }
  if (-not $ready) { Write-Host "  调试端口没起来"; exit 1 }

  & node "tools\demo_video\record.js" "$DebugPort" "$OutDir" "$Fps" "$url"
  $code = $LASTEXITCODE
} finally {
  if ($br -and -not $br.HasExited) { Stop-Process -Id $br.Id -Force -ErrorAction SilentlyContinue }
  Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Remove-Item $prof -Recurse -Force -ErrorAction SilentlyContinue
}
exit $code
