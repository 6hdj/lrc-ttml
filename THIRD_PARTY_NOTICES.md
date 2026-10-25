# 第三方组件、模型与素材声明

Lyric2TTML 自身代码以仓库根目录的 [MIT License](LICENSE) 授权。

**MIT 授权只覆盖本仓库中由本项目作者编写的代码与文档。** 下列第三方组件、
模型权重、词典与素材仍归各自作者所有，按其原始许可证使用；它们**不因被本项目
调用、下载或打包而变成 MIT**。

> 本文件不是法律意见。商用、再分发或提交应用商店前，请自行复核各上游许可证。
> 如果某个模型/组件的许可证与你的使用场景不兼容，请替换或移除它。

---

## ⚠️ 最重要的三条

### 1. TIFA-1.0-ST 模型：CC BY-NC-SA 4.0，**不是** OSI 开源许可证

TIFA 的**运行库源码**（`models/tifa/runtime`，来自
[openvpi/TIFA](https://github.com/openvpi/TIFA)）是 **MIT**。

但 TIFA 的**预训练模型文件**（`model.pt` 等）按上游发布说明适用
**[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)**：

- **BY**：必须给 OpenVPI / TIFA 署名；
- **NC**：**不得用于商业用途**；
- **SA**：修改后的模型文件必须以相同许可证共享。

因此：

- 本项目 **MIT 不覆盖 TIFA 模型文件**；
- `models-v1` Release 中的 `tifa-aligner.tar` 是**非商业**第三方模型分发，
  不是 MIT 资产；
- 若你要把软件用于商业用途/收费分发，**不能使用 TIFA 模型**，需要替换成
  许可兼容的模型或取得上游单独授权；
- 模型文件单独下载，不随 MIT 便携包捆绑；程序在缺少它时自动回退 Whisper。

上游：

- 代码/运行库：https://github.com/openvpi/TIFA （MIT）
- 预训练模型发布与许可证说明：https://github.com/openvpi/TIFA/releases/tag/v1.0.0
- TIFA-1.0-ST 模型许可证：CC BY-NC-SA 4.0
- 模型训练所用公开数据集（GTSinger、MIR-1K、OpenSinger、Opencpop、M4Singer 等）
  各有自己的条款，见上游发布说明。

`tifa-aligner.tar` 中同时包含的 G2P 词典/资源（CMUdict、jyutping 词典、日文词典等）
按上游 TIFA 发布包与各词典原始许可证使用；如再分发请保留上游声明。

### 2. AMLL 官方 TTML 解析器：AGPL-3.0-only（仅开发/验收用）

`tools/verify_amll_parse.py` 在开发环境调用
[`@applemusic-like-lyrics/ttml`](https://github.com/amll-dev/applemusic-like-lyrics)
（**AGPL-3.0-only**）来做“真解析器”验收。

- 它是**可选开发工具依赖**，不是 MIT 代码的一部分；
- 发布包**不应捆绑**该 npm 包（`node_modules` 已在 `.gitignore`，构建时也应排除）；
- 如果你修改并分发它，必须遵守 AGPL-3.0；仅把它当独立验收工具调用，不改变
  本项目自身代码的 MIT 授权；
- 没有它时程序会跳过 AMLL 解析验收并给出提示，不影响 TTML 生成。

### 3. 素材/演示音频：本项目自制，无第三方音乐

`promo/public/audio.wav`、`history-video/public/bgm.wav`、宣传片中的旁白与
配乐均由本项目脚本合成（numpy 和弦垫音 + Windows SAPI 中文旁白），
**不包含第三方商业音乐**；可随项目一起分发。

界面截图来自本项目真实运行界面；图标由本项目提供。

---

## 运行时依赖（Python / 桌面）

| 组件 | 许可证 | 用途 / 说明 |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | ASR 封装 |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT | Whisper 推理后端 |
| [OpenAI Whisper](https://github.com/openai/whisper) / large-v3 权重 | MIT | 默认 ASR 模型；权重首次运行下载，不随仓库 |
| [PyTorch](https://pytorch.org/) | BSD-3-Clause | 声纹/分离/TIFA 外部环境 |
| [Demucs](https://github.com/facebookresearch/demucs) + htdemucs 权重 | MIT | 可选人声分离；权重按需下载 |
| [CAM++ / 3D-Speaker](https://www.modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common) | Apache-2.0 | 可选声纹分声部；权重与网络定义按上游 Apache-2.0 |
| [pypinyin](https://github.com/mozillazg/python-pinyin) | MIT | 中文同音字容错 |
| [NumPy](https://numpy.org/) | BSD-3-Clause | 数值 |
| [PyAV](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause | 音频解码 |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause | 音频读写 |
| [librosa](https://github.com/librosa/librosa) | ISC | TIFA 外部依赖 |
| [onnxruntime](https://github.com/microsoft/onnxruntime) | MIT | ONNX 推理 |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) / [tokenizers](https://github.com/huggingface/tokenizers) | Apache-2.0 | 模型缓存/分词 |
| [PySide6 / Qt](https://doc.qt.io/qtforpython-6/) | LGPL-3.0 / GPL-3.0 / 商业 | **旧版 Qt 桌面**（v1.1.1）；当前主界面是 WinUI 3 |
| [PyInstaller](https://pyinstaller.org/) | GPL-2.0-or-later + bootloader exception | 冻结后端/便携包；按其 exception 分发 |
| [g2pflow](https://github.com/openvpi/g2pflow) | MIT | TIFA 外部环境 G2P |
| [UniDic / unidic-lite](https://pypi.org/project/unidic-lite/) | 上游 UniDic 条款（见包元数据） | TIFA 日文 G2P；仅外部 Python 安装，不随 MIT 包 |
| [textgrid](https://pypi.org/project/textgrid/) | 见包元数据 | TIFA TextGrid 解析 |
| [lightning](https://github.com/Lightning-AI/pytorch-lightning) / [torchmetrics](https://github.com/Lightning-AI/torchmetrics) | Apache-2.0 | TIFA 外部依赖 |
| [ttml 解析器](https://github.com/amll-dev/applemusic-like-lyrics) | **AGPL-3.0-only** | 仅开发验收；见上文 |
| [@xmldom/xmldom](https://github.com/xmldom/xmldom) | MIT | AMLL 解析器依赖（若安装） |
| FFmpeg（系统/可选） | LGPL/GPL（取决于构建） | 音频代理转码；本项目**不内置** ffmpeg.exe，用户自备 |
| Windows App SDK / WinUI 3 / .NET 8 | MIT | WinUI 3 外壳 |
| Microsoft Edge WebView2 | Microsoft 软件许可条款 | WinUI 内部网页/帮助（如启用） |

> 上表只列直接/重要依赖；`requirements.txt` 的传递依赖仍按各自包元数据授权。
> 所有 Python 包许可证可在安装后的 `*.dist-info/METADATA` 中查看。

---

## 模型文件位置与许可

| 模型 | 许可 | 分发方式 |
|---|---|---|
| `whisper-large-v3` / `medium` / `small`（faster-whisper 格式） | MIT（OpenAI Whisper） | 模型管理按需下载，不随仓库 |
| `htdemucs`（Demucs） | MIT | 模型管理/首次使用下载 |
| CAM++（`campplus.onnx` 等） | Apache-2.0（ModelScope 3D-Speaker） | 模型管理/安装包内置 ONNX 图；权重按需下载 |
| **TIFA-1.0-ST** | **CC BY-NC-SA 4.0（非商业）** | GitHub Release `models-v1`/`tifa-aligner.tar`，按需下载 |
| TIFA runtime | MIT | 包含在 `tifa-aligner.tar`；源码见上游 |
| TIFA G2P 词典 | 各上游词典条款 | 包含在 `tifa-aligner.tar`；见 TIFA `docs/G2P.md` |

**再次强调：TIFA 模型不是 MIT，不能用于商业用途。** 如果你要商用：
把 TIFA 相关模型从分发中移除，或在模型管理中禁用/不下载该条目；
软件其余 Whisper/Demucs/CAM++ 路径不受影响。

---

## 代码与算法来源说明

- **TIFA 适配层** `timing_align.py`：本项目实现，算法/工程思路参考
  [Timing](https://github.com/Zennmn/Timing)（MIT）与
  [openvpi/TIFA](https://github.com/openvpi/TIFA)（MIT）；未复制 Timing 的完整代码。
- **TTML 规范**：结构/时间语义参考
  [W3C TTML1](https://www.w3.org/TR/ttml1/) 与
  [AMLL TTML DB 规范](https://github.com/amll-dev/amll-ttml-db)；
  项目只生成标准文本，不包含 Apple/W3C 的代码或素材。
- **Apple 内部角色**：`x-duet-a/b`、`x-narration` 是 Apple 生态扩展字符串，
  本项目只生成这些字符串，不包含 Apple 代码或资源。
- **竞品**：调研过 [amll-ttml-tool](https://github.com/steve-xmh/amll-ttml-tool)
  （GPL-3.0），只参考交互思路，未复制源码；本项目保持 MIT。

---

## 用户内容

`uploads/`、`outputs/`、`refs/` 以及用户生成的 TTML 均**不进入仓库**
（见 `.gitignore`）。音频、歌词、TTML 的版权归原权利人；本项目只处理
你合法提供的文件，不对转换产物主张版权。

如果你在 Issue/PR 中上传了歌曲片段、歌词或截图，请确认你有权公开它们。
