<#
  给 README 演示段截图（Windows / Edge 无头 + CDP）。

      powershell -ExecutionPolicy Bypass -File tools\shoot_screens.ps1
      powershell -ExecutionPolicy Bypass -File tools\shoot_screens.ps1 -Only 03

  做法：**真的把 web_server 起起来**，用无头 Edge 打开真实页面截图 ——
  不画假的界面图，截图里就是用户会看到的东西。

  编辑台那几张需要一个任务：脚本把 tests/fixtures/demo-duet.ttml 复制成
  outputs/_shot_demo.ttml，然后访问 /?task=_shot_demo&mode=<标签页>
  （?task= 支持见 index.html 的 openTaskFromUrl）。

  ⚠️ 四条踩过的坑，改这个脚本前先看：
  1. **必须用 Start-Process 启动 Edge，不能用 Python 的 subprocess.Popen**：
     同样的命令行，Popen 起的 Edge **永远跑不完**（不退出、也不出图；
     普通 / DETACHED_PROCESS / CREATE_NO_WINDOW 三种 creationflags 都试过）。
     Start-Process（分离启动）稳定 1 秒内出图。
  2. **不要给 Edge 传 `--virtual-time-budget`，也不要用 `--headless=new`**：
     两者都会让 Edge **卡住不退出**。而旧版 `--headless` 是"load 事件后截图"，
     截不到异步渲染出来的编辑台 —— 所以改成 Edge 常驻 + Node 走 CDP 精确等待。
  3. **不要用管道接 Edge 的输出**（capture_output / |）：
     Edge 的子进程会一直占着管道句柄，调用方等 EOF 会永久阻塞。
  4. **.ps1 必须带 UTF-8 BOM**：Windows PowerShell 5.1 对无 BOM 的 .ps1
     按本机 ANSI 代码页（简体中文 = GBK）解码，中文注释被解成乱码 → 直接语法错误。
#>
param(
  [string]$Only = "",
  [int]$Port = 8899,
  [int]$DebugPort = 9333
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT
$env:PYTHONIOENCODING = "utf-8"
$env:L2T_PORT = "$Port"

$DEMO_ID = "_shot_demo"
$URL = "http://127.0.0.1:$Port"
$OUT = Join-Path $ROOT "docs\screenshots"

$EDGE = @(
  "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
  "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
  "C:\Program Files\Google\Chrome\Application\chrome.exe",
  "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $EDGE) { Write-Host "找不到 Edge / Chrome"; exit 1 }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Write-Host "需要 node"; exit 1 }

New-Item -ItemType Directory -Path $OUT -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ROOT "outputs") -Force | Out-Null
Copy-Item "tests\fixtures\demo-duet.ttml" "outputs\$DEMO_ID.ttml" -Force
Remove-Item "outputs\$DEMO_ID.edit.json" -ErrorAction SilentlyContinue

Write-Host "  浏览器：$EDGE"
Write-Host "  启动服务…"
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$srv = Start-Process -FilePath $py -ArgumentList "web_server.py" -PassThru -WindowStyle Hidden
$browser = $null
$code = 1
try {
  $up = $false
  foreach ($i in 1..40) {
    Start-Sleep -Milliseconds 500
    try { Invoke-WebRequest "$URL/" -TimeoutSec 2 -UseBasicParsing | Out-Null; $up = $true; break } catch {}
  }
  if (-not $up) { Write-Host "  服务没起来"; exit 1 }
  Write-Host "  服务已起：$URL"

  # Edge 常驻 + 开调试端口（脚本结束时收掉）
  $prof = Join-Path $ROOT ".tmp\shotprof"
  Remove-Item $prof -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "  启动浏览器（无头 + CDP :$DebugPort）…"
  $browser = Start-Process -FilePath $EDGE -PassThru -WindowStyle Hidden -ArgumentList @(
    "--headless", "--disable-gpu", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check",
    "--remote-debugging-port=$DebugPort",
    "--user-data-dir=$prof", "--window-size=1280,1050",
    "about:blank"
  )
  $ready = $false
  foreach ($i in 1..40) {
    Start-Sleep -Milliseconds 400
    try {
      Invoke-RestMethod "http://127.0.0.1:$DebugPort/json/version" -TimeoutSec 2 | Out-Null
      $ready = $true; break
    } catch {}
  }
  if (-not $ready) { Write-Host "  调试端口没起来"; exit 1 }

  & node "tools\shoot_screens.js" "$DebugPort" "$OUT" "$URL"
  $code = $LASTEXITCODE
} finally {
  if ($browser -and -not $browser.HasExited) { Stop-Process -Id $browser.Id -Force -ErrorAction SilentlyContinue }
  Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  if ($srv -and -not $srv.HasExited) { Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue }
  foreach ($f in @("$DEMO_ID.ttml", "$DEMO_ID.edit.json", "$DEMO_ID.src.json")) {
    Remove-Item (Join-Path $ROOT "outputs\$f") -ErrorAction SilentlyContinue
  }
  Remove-Item (Join-Path $ROOT ".tmp\shotprof") -Recurse -Force -ErrorAction SilentlyContinue
}
if ($code -eq 0) { Write-Host "`n  完成 → $OUT" } else { Write-Host "`n  有失败项"; exit 1 }
