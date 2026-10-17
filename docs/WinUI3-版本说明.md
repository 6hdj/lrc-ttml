# WinUI 3 版本说明（v1.2.0）

本版本把界面迁移到 **WinUI 3（原生 Windows 11 风格）**，后端保持同一套算法（TXT/LRC + 音频 → 逐字 TTML），
并通过 HTTP 由 WinUI 壳拉起本机 Python 后端（`Lyric2TTML.Service.exe`）。

## 组件

```
Lyric2TTML/                 ← WinUI 3 壳（C# / XAML，框架依赖 .NET 8）
  Lyric2TTML.exe
  backend/                  ← Python 后端（PyInstaller onedir）
    Lyric2TTML.Service.exe  ← 入口：/api/* HTTP 服务 + --model-cli / --runtime-cli / --worker-cli
    backend/data/           ← 模型缓存（程序目录内，可写）
```

- 壳启动时自动拉起后端（自动选端口），退出时清理；后端路径 `backend\`，也可用 `L2T_BACKEND_ROOT` 覆盖。
- 数据/模型缓存**优先放在程序目录**（`backend\data\models\hf_cache`），避免受系统/安全软件对
  `%LOCALAPPDATA%` 的写入限制影响；已有的旧版 `.hf_cache` 通过只读链接复用。

## 页面

| 页面 | 功能 |
|---|---|
| 开始 | 三步说明 + 快捷入口 |
| 歌词工作台 | 选音频/歌词、参数、开始处理、实时进度与日志、保存 TTML |
| 歌词编辑台 | 逐字渲染、播放高亮、改时间/±10ms/声部/和声/元数据、保存 TTML |
| 任务中心 | 任务列表 / 阶段 / 进度 / 取消 |
| 模型管理 | 7 个模型状态；下载/校验/清理/位置（官方源：魔搭 ModelScope 直链，HF 兜底） |
| 运行库 | torch / CUDA / Demucs Worker 的安装、校验、卸载 |
| 设置 | 主题（选中即预览）、默认参数、歌词保存目录、关于 |
| 帮助/诊断 | `--doctor` 环境体检、后端启停、日志 |

## 构建（源码）

```powershell
# 后端（Python 3.14 + PyInstaller）
python -m PyInstaller --noconfirm --onedir --console --name Lyric2TTML.Service `
  --add-data "index.html;." --add-data "desktop/resources;desktop/resources" `
  --add-data "models;models" --collect-all torch --collect-all demucs `
  --hidden-import numpy.core --hidden-import numpy.core.multiarray `
  --hidden-import desktop.services.model_manager --hidden-import doctor `
  service_main.py
# 壳（需要 .NET 8 SDK + Windows App SDK；命令行 dotnet build 在部分环境有 MSB4062，用 VS Build Tools）
pwsh tools/build_winui.ps1
# 部署（程序运行时也能在线替换被锁文件）
pwsh tools/deploy_winui.ps1
```

## 已知限制（如实说明）

1. **大型运行包不入库/不随 Release 提供**：后端内嵌 torch + CUDA（约 6.4 GB），加上模型后总目录约 9.7 GB，
   超过 GitHub 单文件 2 GiB 限制；仓库提供源码 + 一键部署脚本，模型由官方源按需下载。
2. **本机安全策略**可能只允许本程序写自己的目录：选外部保存目录会回退到默认目录（任务日志会写明原因）。
3. 模型下载以**魔搭 ModelScope 官方直链**为第一优先（已实测 large-v3 2.9 GB / 117 s、medium 1.4 GB / 62 s、
   small 464 MB / 32 s），HF 与 GitHub Release 镜像作为兜底。
4. 保存文件名 = 识别到的「歌手 - 歌名」（音频文件名或元数据），同名自动加 ` (2)`。
