# 部署 WinUI 3 版本（壳 + 后端）到 dist-winui\Lyric2TTML
#
# 为什么需要脚本：程序运行时会锁住 exe/dll，robocopy 会一直重试（甚至"卡死"）。
# 本脚本等程序退出后再复制；也可以用 -Force 直接结束进程（会丢失未保存的编辑，慎用）。
#
# 用法：
#   pwsh tools/deploy_winui.ps1
#   pwsh tools/deploy_winui.ps1 -Force        # 直接结束 Lyric2TTML* 再复制
#   pwsh tools/deploy_winui.ps1 -TimeoutSec 600
param(
    [switch]$Force,
    [int]$TimeoutSec = 1800
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dest = Join-Path $repo "dist-winui\Lyric2TTML"
$shellSrc = Join-Path $repo "winui\Lyric2TTML.Shell\bin\x64\Release\net8.0-windows10.0.22621.0\win-x64"
$backendSrc = Join-Path $repo "dist-service\Lyric2TTML.Service"

foreach ($p in @($shellSrc, $backendSrc, $dest)) {
    if (-not (Test-Path $p)) { throw "缺少路径：$p（先构建壳/后端）" }
}

function Wait-AppExit([int]$timeoutSec, [bool]$force) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while (Get-Date) -lt $deadline {
        $procs = Get-Process -Name "Lyric2TTML", "Lyric2TTML.Service" -ErrorAction SilentlyContinue
        if (-not $procs) { return $true }
        if ($force) {
            Write-Host "结束进程：$($procs | ForEach-Object { $_.Name + '#' + $_.Id } | Join-String -Separator ', ')"
            $procs | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            continue
        }
        Write-Host "程序还在运行（$($procs.Count) 个进程），等待退出…（也可按 Ctrl+C，关掉程序后重跑）"
        Start-Sleep -Seconds 5
    }
    return $false
}

if (-not (Wait-AppExit $TimeoutSec $Force)) { throw "等待程序退出超时；请先关闭 Lyric2TTML 再重试" }

Write-Host "== 清理上次在线更新留下的 .old 文件 =="
Get-ChildItem -Path $dest -Recurse -Filter "*.old" -ErrorAction SilentlyContinue |
    ForEach-Object { try { Remove-Item $_.FullName -Force -ErrorAction Stop } catch { } }

Write-Host "== 复制壳 =="
& robocopy $shellSrc $dest /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "复制壳失败（robocopy exit=$LASTEXITCODE）" }

Write-Host "== 复制后端（6GB+，约几分钟）=="
& robocopy $backendSrc (Join-Path $dest "backend") /E /XF "index.html" /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "复制后端失败（robocopy exit=$LASTEXITCODE）" }
Copy-Item (Join-Path $repo "index.html") (Join-Path $dest "backend\index.html") -Force

Write-Host "== 修复模型缓存链接（大模型只读复用，不复制 3GB）=="
$hfNew = Join-Path $dest "data\models\hf_cache"
New-Item -ItemType Directory -Force -Path $hfNew | Out-Null
$legacyLarge = Join-Path $dest "backend\.hf_cache\models--Systran--faster-whisper-large-v3"
$link = Join-Path $hfNew "models--Systran--faster-whisper-large-v3"
if (-not (Test-Path $link)) {
    if (Test-Path $legacyLarge) {
        cmd /c mklink /J "$link" "$legacyLarge" | Out-Null
        Write-Host "  已链接 large-v3 缓存"
    } else {
        Write-Host "  ⚠ 旧缓存不存在（新装环境首次会在下载时自动补齐 large-v3）"
    }
}

Write-Host "== 逐页自检 =="
$smoke = Join-Path $dest "pages-smoke.txt"
Remove-Item $smoke -ErrorAction SilentlyContinue
$p = Start-Process -FilePath (Join-Path $dest "Lyric2TTML.exe") -ArgumentList "--pages-smoke" -PassThru `
    -RedirectStandardOutput (Join-Path $env:TEMP "l2t-pages-smoke.out") `
    -RedirectStandardError (Join-Path $env:TEMP "l2t-pages-smoke.err")
$p.WaitForExit(120000) | Out-Null
if (Test-Path $smoke) {
    Get-Content $smoke
    if ($p.ExitCode -ne 0) { throw "页面自检失败（exit=$($p.ExitCode)）" }
} else {
    throw "没有生成 pages-smoke.txt（可能启动即崩）"
}

Write-Host "`n== 部署完成 =="
Write-Host "双击运行：$dest\Lyric2TTML.exe"


