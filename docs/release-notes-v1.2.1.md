# Lyric2TTML v1.2.1 —— 便携版修复（v1.2.0 的附件是"更新补丁"，不是完整程序）

> ⚠️ **v1.2.0 的附件 `Lyric2TTML-winui-v1.2.0-update.zip` 只是"更新补丁"**
> （只含 WinUI 壳 + 后端 exe，不含后端 `_internal` 运行库），单独运行会报
> `Failed to load Python DLL ... python314.dll`。本次 v1.2.1 提供**解压即用**的便携版。

## 下载哪个？

| 文件 | 用途 |
|---|---|
| **`Lyric2TTML-winui-v1.2.1-portable.zip`（414 MB）** | **解压即用**：壳 + 后端（含 CUDA 运行库组件之外的全部 Python 依赖与模型框架） |
| `Lyric2TTML-winui-v1.2.1-update-patch.zip`（64.8 MB） | 仅壳 + 后端 exe，给**已有安装**覆盖升级用 |

## 便携版首次使用

1. 解压整个 zip，双击 `winui\Lyric2TTML.exe`
2. 模型管理 → 下载 Whisper 模型（魔搭官方源，约 20 MB/s；large-v3 约 2 分钟）
3. 运行库 → CUDA 运行时「安装/修复」（527 MB，腾讯镜像，实测 22 秒）
4. （可选）运行库 → Demucs Worker「安装」（约 86 秒，人声分离用）

## 本版修复

- **便携包内后端找不到模型**：`web_server` 把 `HUGGINGFACE_HUB_CACHE` 默认指到 `_internal\.hf_cache`，
  而下载器写的是 `data\models\hf_cache` ⇒ 现在统一对齐；`service_main` 同步该变量。
- **后装 CUDA 不生效**：ASR 的 CUDA DLL 搜索路径新增 `<数据目录>\runtime\cuda{,/bin}`，
  并在每次创建识别引擎时重新注册 ⇒ 先开程序、再去运行库页装 CUDA，**无需重启**即可用显卡。
- 便携包**不再捆绑 CUDA（2 GB）**，改为运行库页一键安装 ⇒ 包体从 2.37 GB 降到 414 MB（<2 GiB GitHub 限制）。

## 已验证（本版产物）

- 解压后的便携包：逐页自检 **8/8**；后端正常启动；4/7 模型就绪（campplus×3 + demucs），
  Whisper 三个模型均可下载（官方源）。
- 完整流程实测：**安装 CUDA（22 s）→ 下载 whisper-small（17 s）→ 真实转换 0 错误 / 63 行**。
- 完整安装版（本地 9.7 GB 运行目录）：7/7 模型就绪、真实转换 0 错误。

## 说明

- 仓库不再上传 6.4 GB 的完整后端（含 torch/CUDA），超过 GitHub 单文件 2 GiB 限制；
  完整安装版请按 `docs/WinUI3-版本说明.md` 在本地构建，或用旧 Qt 安装包（v1.1.1）。
- 本机安全策略可能禁止程序写外部目录：设置里的「歌词保存到」建议用程序目录内子目录。
