# 把模型打包上传到 GitHub Release，并可选地写回 model_manifest.json 的 mirror 字段。
#
# 为什么要这个脚本：用户网络连 HuggingFace 不稳（ConnectTimeout / peer closed），
# 建议用 GitHub Release 当下载源。GitHub 单个资产上限 2 GiB，所以 >1.8 GiB 的包自动分卷，
# 由 model_manager._download_mirror 下载分卷后拼接、校验 sha256、解包进 HF 缓存目录。
#
# 用法（在仓库根目录）：
#   pwsh tools/publish_models_github.ps1 -Models whisper-large-v3 -UpdateManifest
#   pwsh tools/publish_models_github.ps1 -Models whisper-large-v3,whisper-medium,whisper-small -UpdateManifest
#   pwsh tools/publish_models_github.ps1 -DryRun          # 只打包+打印，不上传
#
# 前置：gh 已登录（gh auth status），缓存目录里已有对应模型。
param(
    [string]$Repo = "6hdj/lrc-ttml",
    [string]$Tag = "models-v1",
    [string[]]$Models = @("whisper-large-v3"),
    [string]$CacheRoot = "$env:LOCALAPPDATA\Lyric2TTML\.hf_cache",
    [string]$Manifest = "",
    [switch]$DryRun,
    [switch]$UpdateManifest
)
$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Manifest) { $Manifest = Join-Path $repoRoot "desktop\resources\model_manifest.json" }
$manifestPath = (Resolve-Path $Manifest).Path
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json

$work = Join-Path $env:TEMP ("l2t-model-publish-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$limit = 1.8GB
$results = @()

function Split-File([string]$path, [long]$chunk) {
    $parts = @()
    $in = [System.IO.File]::OpenRead($path)
    try {
        $i = 1
        $buf = New-Object byte[] (8MB)
        while ($in.Position -lt $in.Length) {
            $outPath = "$path.part$i"
            $out = [System.IO.File]::Create($outPath)
            try {
                $left = [Math]::Min($chunk, $in.Length - $in.Position)
                while ($left -gt 0) {
                    $want = [int][Math]::Min($buf.Length, $left)
                    $n = $in.Read($buf, 0, $want)
                    if ($n -le 0) { break }
                    $out.Write($buf, 0, $n)
                    $left -= $n
                }
            } finally { $out.Dispose() }
            $parts += $outPath
            $i++
        }
    } finally { $in.Dispose() }
    return $parts
}

# 确保 release 存在
$releaseExists = $false
if (-not $DryRun) {
    gh release view $Tag --repo $Repo --json tagName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $releaseExists = $true }
    if (-not $releaseExists) {
        Write-Host "创建 release $Tag ..."
        gh release create $Tag --repo $Repo --title "MODELS $Tag" `
            --notes "离线模型包（由 tools/publish_models_github.ps1 上传；分卷需拼接后 tar 解包）"
    }
}

foreach ($id in $Models) {
    $spec = $manifest.models | Where-Object { $_.id -eq $id }
    if (-not $spec) { throw "manifest 里没有模型 $id" }
    $cacheKey = $spec.source.cache_key
    $snapRoot = Join-Path (Join-Path $CacheRoot $cacheKey) "snapshots"
    if (-not (Test-Path $snapRoot)) { throw "缓存里没有 $id（$snapRoot 不存在），先下载它再上传" }
    $snap = Get-ChildItem $snapRoot -Directory | Select-Object -First 1
    if (-not $snap) { throw "$id 没有 snapshot 目录" }

    # 只打包 verify 声明的文件（hf_snapshot）或全部文件（hf_presence）
    $want = @()
    if ($spec.verify.files) { $want = @($spec.verify.files.PSObject.Properties.Name) }
    $stage = Join-Path $work "$id\$cacheKey\snapshots\mirror-v1"
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    $copied = 0
    foreach ($f in Get-ChildItem $snap.FullName -File) {
        if ($want.Count -gt 0 -and ($want -notcontains $f.Name)) { continue }
        Copy-Item $f.FullName (Join-Path $stage $f.Name) -Force
        $copied++
    }
    if ($copied -eq 0) { throw "$id 没找到可打包的模型文件（verify 清单对不上）" }

    $tarPath = Join-Path $work "$id.tar"
    Write-Host "打包 $id（$copied 个文件）→ $tarPath"
    # tar 的成员路径相对 $work/$id，解包到 HF 根目录就能得到 <cacheKey>/snapshots/mirror-v1/…
    & tar -cf $tarPath -C (Join-Path $work $id) .
    if ($LASTEXITCODE -ne 0) { throw "tar 打包失败：$id" }
    $size = (Get-Item $tarPath).Length
    $sha = (Get-FileHash $tarPath -Algorithm SHA256).Hash.ToLower()
    $files = @()
    if ($size -gt $limit) {
        Write-Host ("包 {0:N1} GB > 1.8 GiB，分卷…" -f ($size / 1GB))
        $parts = Split-File $tarPath $limit
        $files = @($parts | ForEach-Object { Split-Path $_ -Leaf })
    } else {
        $files = @("$id.tar")
    }

    if ($DryRun) {
        Write-Host "[DryRun] $id  size=$size sha256=$sha files=$($files -join ', ')"
    } else {
        Write-Host "上传 $id（$($files.Count) 个文件）…"
        $paths = @()
        foreach ($f in $files) { $paths += Join-Path $work $f }
        & gh release upload $Tag @paths --repo $Repo --clobber
        if ($LASTEXITCODE -ne 0) { throw "上传失败：$id" }
        Write-Host "上传完成：$id"
    }

    $results += [pscustomobject]@{
        id      = $id
        archive = "$id.tar"
        size    = [long]$size
        sha256  = $sha
        files   = $files
    }
}

if ($UpdateManifest -and $results.Count -gt 0) {
    foreach ($r in $results) {
        $item = $manifest.models | Where-Object { $_.id -eq $r.id }
        $mirror = [ordered]@{
            kind    = "github_release"
            repo    = $Repo
            tag     = $Tag
            archive = $r.archive
            files   = $r.files
            size    = $r.size
            sha256  = $r.sha256
        }
        $item | Add-Member -NotePropertyName mirror -NotePropertyValue $mirror -Force
    }
    $out = $manifest | ConvertTo-Json -Depth 24
    Set-Content -Path $manifestPath -Value $out -Encoding utf8
    Write-Host "已写回 manifest：$manifestPath"
} else {
    Write-Host "`n镜像清单（可手工贴到 manifest 的对应模型）："
    $results | ConvertTo-Json -Depth 6
}
if (-not $DryRun) { Write-Host "`n下载地址示例：https://github.com/$Repo/releases/tag/$Tag" }
