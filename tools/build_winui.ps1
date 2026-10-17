# 构建 WinUI 3 外壳（Lyric2TTML C# 前端）
#
#   pwsh tools\build_winui.ps1              # 框架依赖发布（默认；.NET 由安装程序当依赖装）
#   pwsh tools\build_winui.ps1 -SelfContained  # 自包含（自带 .NET，体积大，用于排查/离线）
#
# ⚠️⚠️ 两个**实测**坑，不要绕（2026-10-16 都踩过）：
#   ① **必须用 VS Build Tools 的 MSBuild.exe**，不能用 `dotnet build`：
#      `dotnet build` 会去 `<dotnet-sdk>\Microsoft\VisualStudio\v17.0\AppxPackage\` 找
#      `Microsoft.Build.Packaging.Pri.Tasks.dll`，那里**没有这个文件** ⇒
#      `error MSB4062: 未能加载任务 "…ExpandPriContent"`。
#      该 DLL 只随 VS / VS Build Tools 提供（本机已装 17.14.41）。
#   ② **TFM 与 min 版本必须配对**：`net8.0-windows10.0.22621.0` +
#      `TargetPlatformMinVersion=10.0.22000.0`（Win11 22H2 / Win11 起）。
#      若 min 高于 TFM 的平台版本 ⇒ `error NETSDK1135: SupportedOSPlatformVersion … cannot be higher`。
#
# 另：`.NET SDK` 装的是**用户级**（%LOCALAPPDATA%\Microsoft\dotnet，无需管理员），
# 所以要把 DOTNET_ROOT 指过去，VS 的 MSBuild 才找得到 SDK。

[CmdletBinding()]
param(
    [switch]$SelfContained,
    [ValidateSet("x64")] [string]$Platform = "x64",
    [string]$Configuration = "Release",
    [switch]$NoRestore
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$proj = Join-Path $repo "winui\Lyric2TTML.Shell\Lyric2TTML.Shell.csproj"

if (-not (Test-Path $proj)) { throw "找不到工程文件：$proj" }

# ---- ① 找 VS Build Tools 的 MSBuild ----
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$msbuild = $null
if (Test-Path $vswhere) {
    $inst = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath 2>$null
    if ($inst) {
        $cand = Join-Path $inst "MSBuild\Current\Bin\MSBuild.exe"
        if (Test-Path $cand) { $msbuild = $cand }
    }
}
if (-not $msbuild) { throw "找不到 MSBuild（需要 Visual Studio / VS Build Tools 2022 + WinUI 组件）" }
Write-Host "  MSBuild: $msbuild"

# ---- ② 找用户级 .NET SDK ----
$dotnetRoot = $env:LOCALAPPDATA + "\Microsoft\dotnet"
if (-not (Test-Path (Join-Path $dotnetRoot "dotnet.exe"))) {
    # 退回系统安装位置（若将来改成机器级安装）
    if (Test-Path "C:\Program Files\dotnet\dotnet.exe") { $dotnetRoot = "C:\Program Files\dotnet" }
    else { throw "找不到 .NET SDK（期望 $dotnetRoot\dotnet.exe）" }
}
$env:DOTNET_ROOT = $dotnetRoot
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"
$sdks = & (Join-Path $dotnetRoot "dotnet.exe") --list-sdks
Write-Host "  DOTNET_ROOT: $dotnetRoot"
$sdks | ForEach-Object { Write-Host "    SDK $_" }

# ---- ③ 组装参数 ----
$args = @($proj, "/p:Configuration=$Configuration", "/p:Platform=$Platform", "/v:m", "/nologo")
if (-not $NoRestore) { $args += "/restore" }
$outRoot = "bin\sc\"
if ($SelfContained) {
    $args += "/p:SelfContained=true"
    $args += "/p:WindowsAppSDKSelfContained=true"
    $args += "/p:BaseOutputPath=$outRoot"
    Write-Host "  模式：**自包含**（自带 .NET，体积约 160MB）"
} else {
    $args += "/p:SelfContained=false"
    Write-Host "  模式：**框架依赖**（.NET 由安装程序当依赖安装）"
}

# ---- ④ 构建 ----
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$out = & $msbuild @args 2>&1
$sw.Stop()
$errs = $out | Select-String -Pattern " error " 
if ($errs) {
    $errs | Select-Object -First 6 | ForEach-Object { Write-Host "  ✗ $($_.Line.Trim())" -ForegroundColor Red }
    throw "构建失败（用时 $([math]::Round($sw.Elapsed.TotalSeconds,1))s）"
}

# ---- ⑤ 找产物并报告（含体积）----
# ⚠️ **按模式定位精确输出目录**，不能"找最新的 Lyric2TTML.exe"：
# 两种模式产物同名，按最新找会把自包含版（161.9MB）当成框架依赖版报出去 —— 实测踩到。
$base = Join-Path $repo "winui\Lyric2TTML.Shell"
$sub = if ($SelfContained) { "bin\sc" } else { "bin" }
$exe = Join-Path $base "$sub\$Platform\$Configuration\net8.0-windows10.0.22621.0\win-x64\Lyric2TTML.exe"
if (-not (Test-Path $exe)) { throw "构建没有报错，但找不到产物：$exe —— 不要当成功" }

$dir = Split-Path -Parent $exe
$files = Get-ChildItem $dir -Recurse -File
Write-Host ""
Write-Host "  ✅ 构建成功（$([math]::Round($sw.Elapsed.TotalSeconds,1))s）"
Write-Host "  产物: $exe"
Write-Host "  体积: $([math]::Round(($files | Measure-Object Length -Sum).Sum/1MB,1)) MB / $($files.Count) 个文件"
