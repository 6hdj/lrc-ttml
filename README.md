# Lyric2TTML

**把普通歌词（TXT / LRC）+ 歌曲音频，转换成 Apple Music / AMLL 可用的逐字 TTML。**

离线运行 · 不需要 API Key · Windows WinUI 3 桌面界面 · 内置模型/运行库管理 · 逐字精修编辑台

![MIT](https://img.shields.io/badge/license-MIT-3da639)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776ab)
![WinUI 3](https://img.shields.io/badge/UI-WinUI%203-0b5cad)
![Offline](https://img.shields.io/badge/runtime-offline-2ea44f)
![Release](https://img.shields.io/badge/release-v1.2.1-blue)

> 🌐 项目主页：https://6hdj.github.io/lrc-ttml/
> 📦 全部版本：https://github.com/6hdj/lrc-ttml/releases

---

## ⬇️ 下载 / 快速开始

### Windows 便携版（推荐）

**[`Lyric2TTML-winui-v1.2.1-portable.zip`](https://github.com/6hdj/lrc-ttml/releases/download/v1.2.1/Lyric2TTML-winui-v1.2.1-portable.zip)**
（约 **326 MB**，WinUI 3 外壳 + Python 后端，解压即用，不含模型与 CUDA）

1. 解压整个 zip，**保留 `winui\` 目录结构**（不要只拷 exe）；
2. **右键 `winui\Lyric2TTML.exe` → 以管理员身份运行**；
3. 第一次使用按「开始」页引导：
   - **模型管理** → 下载 Whisper 模型（large-v3 约 2.9 GB，走 ModelScope 国内源）；
   - **运行库** → 安装/修复 CUDA 运行时；
   - 需要人声分离/和声时，再装 **Demucs Worker**；
4. 回到「歌词工作台」选音频 + 歌词 → 开始处理 → 「💾 保存 TTML」。

> ### ⚠️ 需要以管理员身份启动
>
> **右键 `winui\Lyric2TTML.exe` → 以管理员身份运行**（便携版/安装版都需要）。
> 本程序需要读写程序目录、模型缓存和临时目录；普通权限下会出现模型下载失败、
> TIFA 启动失败或产物无法保存。放在 `C:\Program Files` 等系统目录时必须管理员权限。

### 旧版 Qt 安装包（v1.1.1，仅保留兼容）

[`Lyric2TTML-1.1.1-setup.exe`](https://github.com/6hdj/lrc-ttml/releases/download/v1.1.1/Lyric2TTML-1.1.1-setup.exe)
（190 MB；旧界面，新功能以 WinUI 版为准。）

### 从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-desktop.txt   # 只有旧版 Qt 桌面需要
python main.py --help
```

系统还需要 **ffmpeg**（音频解码）和可用的 **NVIDIA GPU / CUDA**（本项目的默认路径强制使用 GPU；CPU 模式不是正式路径）。

---

## 现在能做什么

| 能力 | 说明 |
|---|---|
| 🎤 **双对齐引擎** | 默认 **faster-whisper + DP**；实验性 **TIFA 歌声音素对齐**（逐字更准，需要逐句 LRC + 外部 Python） |
| 🎼 **TIFA 逐行语言路由** | 中文 / 英文 / 日文按行识别，分别用 `zh` / `en` / `ja` 跑 TIFA |
| 🎧 **TIFA 主唱 + Whisper 和声** | 歌词里已有 `（和声）` / `[和声]` 时，主唱用 TIFA，`x-bg` 时间用 Whisper |
| 🫧 **自动转录和声（实验）** | 歌词没写和声时，用 Whisper raw ASR 找“多出来的人声”，补成 `x-bg` |
| 🕳️ **长间奏三点倒数** | 检测句尾静音，裁掉 TIFA clip 的间奏尾巴，保留 gap，渲染器能显示三点倒数 |
| 🧠 **逐字时间轴** | 中文按字、英文按词；多字 TIFA word 用 `phones` 音素边界切分，字符不一致时 DP 模糊映射 |
| 🎼 **LRC 锚点** | 有 LRC 行级时间戳时按乐句切片，把歌词本身当提示词 |
| 🎼 **背景人声 / 对唱** | `（和声）` → `x-bg`；`[v1]/[v2]` → `ttm:agent` |
| 🧩 **分层歌词** | `--layered-lyrics`：允许主歌词/和声/附加句同时出现，同 agent 的 `<p>` 可重叠；`p` 只包主唱，`x-bg` 可伸到 `p` 外 |
| 🌐 **翻译 / 音译** | Apple Music `<iTunesMetadata>` 或行内 `x-translation` |
| ✅ **确定性自检** | AMLL 规范校验 + 真 AMLL 解析器验收（开发环境） |
| 🪟 **WinUI 3 桌面** | 工作台 / 编辑台 / 任务 / 模型 / 运行库 / 设置 / 诊断，深浅色主题 |
| 💾 **显式保存** | 生成先写 `.pending`，点保存才落正式文件；编辑不丢原始结果 |

### 实测精度（外部真值：QQ 音乐 `.qrc` 逐字时间）

| 歌曲 | Whisper 逐字起始散布 | TIFA 逐字起始散布 | Whisper ≤0.2s | TIFA ≤0.2s |
|---|---:|---:|---:|---:|
| 让风告诉你 | 0.268 s | **0.111 s** | 54.9% | **88.2%** |
| 我不曾忘记 | 0.188 s | **0.062 s** | 61.0% | **97.3%** |
| 失眠飞行 | 0.137 s | **0.078 s** | 76.7% | **96.1%** |

> 这些是“去掉整体录音平移后的相对散布”，不是绝对准确率；评测脚本见
> [`tools/eval_word_timing.py`](tools/eval_word_timing.py)，完整说明见
> [`docs/实验-TIFA对齐引擎.md`](docs/实验-TIFA对齐引擎.md)。

---

## 界面

WinUI 3 桌面（全部真实运行截图，见 [`docs/winui/`](docs/winui/)）：

![WinUI 3 · 歌词工作台](docs/winui/02-workspace.png)
![WinUI 3 · 歌词编辑台](docs/winui/03-editor.png)
![WinUI 3 · 模型管理](docs/winui/05-models.png)
![WinUI 3 · 运行库](docs/winui/06-runtime.png)

编辑台支持逐字高亮、改时间、±10 ms、改声部、和声、文本编辑和元数据；
所有修改先写草稿，点保存才写 TTML。

旧版 Qt 界面（v1.1.1，仍可下载）：[`docs/desktop/`](docs/desktop/)。

---

## 工作原理

```text
音频 ─┬─ Demucs 人声分离（可选）
      │
      ├─ Whisper（默认）── 词级时间 ── DP 强制对齐 ── 精修 ── 声纹分段 ─┐
      │                                                                │
      └─ TIFA（实验）── TextGrid 音素边界 ── 逐字映射 ── 和声/间奏处理 ─┤
                                                                       ▼
歌词 ── 解析 / 标记 / 背景人声合并 ──────────────────────────────── TTML 生成
                                                                       │
                                                              规范自检 + AMLL 解析验收
```

TIFA 模式额外处理：

1. 按行切 clip，±0.2s 上下文；
2. 有分离人声轨时，按 20ms RMS 找最后有声帧，裁掉长间奏；
3. 同演唱者重叠只压缩越界尾段，不整行缩放；
4. 歌词有和声标记 → Whisper 只替换 `x-bg` 时间；
5. 没有和声标记 → Whisper raw ASR 未匹配词可按短语补成 `x-bg`（实验，默认开，
   `L2T_AUTO_HARMONY=0` 可关）。

TIFA 需要：

- 模型管理下载 TIFA 模型（`models-v1` Release，约 163 MB，**非商业许可**，见下文）；
- 设置里指定一个装了 TIFA 依赖的外部 Python：
  ```powershell
  pip install g2pflow==0.3.0 "g2pflow[ja]" unidic-lite librosa soundfile textgrid `
    lightning torchmetrics omegaconf loguru einops h5py colorednoise dask `
    tensorboardX antlr4-python3-runtime resampy praat-parselmouth tensorboard scipy numba
  ```
- 有逐句 LRC；失败自动回退 Whisper。

---

## 模型与许可证（重要）

项目自身代码是 **MIT**，但模型和部分工具是第三方资产，**不因被调用而变成 MIT**：

| 资产 | 许可 | 说明 |
|---|---|---|
| Whisper large-v3 / medium / small | MIT | 模型管理下载，不随仓库 |
| Demucs htdemucs | MIT | 可选人声分离 |
| CAM++ 声纹 | Apache-2.0 | 可选分声部 |
| **TIFA-1.0-ST 模型** | **CC BY-NC-SA 4.0（非商业）** | **不是 OSI 开源许可证**，不能商用；模型单独下载 |
| TIFA runtime | MIT | 源码来自 openvpi/TIFA |
| AMLL 官方 TTML 解析器 | **AGPL-3.0-only** | 仅开发验收工具，不是 MIT 代码，发布包不捆绑 |
| PySide6 / Qt（旧版桌面） | LGPL-3.0 / GPL / 商业 | 旧版 v1.1.1 |
| PyInstaller | GPL-2.0+（bootloader exception） | 打包工具 |
| WinUI 3 / Windows App SDK / .NET | MIT | 当前桌面外壳 |
| FFmpeg | LGPL/GPL（取决于构建） | 用户自备，不内置 |

**⚠️ 如果你要商用/收费分发：不能使用 TIFA 模型**；请替换模型或取得上游授权。
完整清单、版本、链接与分发说明见 **[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)**。

演示视频/宣传片中的旁白、配乐和界面素材均为本项目自制或自己生成，不含第三方商业音乐。

---

## 项目结构

```text
main.py / web_server.py / index.html    CLI 与本地 Web 入口
pipeline.py                            主流水线（解析 → 分离 → ASR/TIFA → 对齐 → 声纹 → 输出 → 自检）
asr.py                                 faster-whisper 封装（GPU、切片、退化兜底）
timing_align.py                        TIFA 适配：逐行切窗、语言路由、和声、间奏裁剪
matcher.py                             歌词切分 + 动态规划强制对齐
diarize.py / speaker_campplus.py       CAM++ 声纹分声部（可选）
separate.py / audio_proxy.py           Demucs 人声分离 / ffmpeg 代理
ttml_writer.py / ttml_check.py         TTML 生成与规范校验
ttml_model.py / ttml_reader.py         编辑态模型与反解
ttml_normalize.py / edit_api.py        时间轴修复 R1–R7 / 编辑台后端
preprocess.py / textread.py            LRC/纯文本解析（GBK/GB18030 探测）
winui/                                 WinUI 3 外壳（C#/XAML）+ 后端 HTTP 客户端 + 单测
desktop/                               旧版 Qt 桌面（v1.1.1 兼容）
tools/                                 评测、诊断、参数联调、AMLL 解析验收
docs/                                  规范、设计、评测、版本说明
models/                                模型权重（gitignore，不随仓库）
uploads/ outputs/                      用户音频/歌词/产物（gitignore，绝不提交）
```

WinUI 版通过本机 HTTP 调用 Python 后端；**算法与 Web 版是同一套**。
构建/打包说明见 [`docs/WinUI3-版本说明.md`](docs/WinUI3-版本说明.md) 和
[`AGENTS.md`](AGENTS.md)。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/实验-TIFA对齐引擎.md`](docs/实验-TIFA对齐引擎.md) | TIFA 接入、逐行语言、和声/间奏算法与评测 |
| [`docs/调研-Timing对齐引擎.md`](docs/调研-Timing对齐引擎.md) | Timing 对齐引擎调研 |
| [`docs/TTML1-AMLL歌词格式规范.md`](docs/TTML1-AMLL歌词格式规范.md) | TTML1 / AMLL 逐条规范 |
| [`docs/AppleMusic-TTML-样例规范.md`](docs/AppleMusic-TTML-样例规范.md) | Apple Music 真实样例结构、与严格 TTML1 的差异、和声/元数据规则 |
| [`docs/WinUI3-版本说明.md`](docs/WinUI3-版本说明.md) | WinUI 3 架构、构建、便携包 |
| [`docs/项目总结.md`](docs/项目总结.md) / [`docs/模块说明.md`](docs/模块说明.md) | 项目/模块说明 |
| [`docs/声纹评估协议.md`](docs/声纹评估协议.md) | CAM++ 分声部评估协议 |
| [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | **第三方组件/模型/素材许可证** |
| [`CHANGELOG.md`](CHANGELOG.md) | 版本变更 |

---

## 自检与验证

```powershell
python selftest.py                                   # 离线自检（无网络/大模型）
python desktop\selftest.py                           # 服务层
python main.py --check-only out.ttml --strict-time   # 严格校验时间表达式
python tools/test_timing_align.py                    # TIFA 映射/间奏/和声单测
python tools/verify_amll_parse.py out.ttml           # 可选：真 AMLL 解析器验收（AGPL 工具）
```

提交前请读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

---

## 已知限制

- **TIFA 模型不是 MIT**：CC BY-NC-SA 4.0，非商业；商用请移除。
- TIFA 需要外部 Python + 依赖 + 逐句 LRC；没装/失败会自动回退 Whisper。
- **自动和声是实验功能**：它分不清“漏写的主唱句”和“叠唱和声”，都会补成
  `x-bg`；生成后请在编辑台核对，或用 `L2T_AUTO_HARMONY=0` 关闭。
- 分声部约 90%：音色接近的对唱可能错行；人数需要在界面明确指定。
- 两人同时张嘴的齐唱/和声无法用声纹拆开；和声时间依赖 Whisper/TIFA 的叠唱识别。
- 长间奏裁剪依赖分离人声轨；没有分离轨时仍可能拖尾。
- 本项目的默认路径要求 NVIDIA GPU/CUDA；CPU 不是正式支持路径。

---

## 贡献 / 许可证

欢迎 Issue 和 PR；提交前请读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

- 本项目代码：[MIT License](LICENSE)，Copyright (c) 2026 HXJ。
- 第三方组件/模型/素材：见 **[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)**；
  **MIT 不覆盖 TIFA 模型（CC BY-NC-SA 4.0）、第三方模型权重或 AGPL 工具**。
- 音频/歌词/TTML 的版权归原权利人；本工具只处理你合法提供的文件，
  请自行确认使用权，不提交无权公开的内容。
