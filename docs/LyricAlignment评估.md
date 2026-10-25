# LyricAlignment 集成评估（第三方项目 · 事实核查版）

> 评估对象：<https://github.com/navi0105/LyricAlignment>
> 评估日期：2026-10-13　｜　评估人：技术评估（只做调研，**没有修改本项目任何代码**；本文件是本次唯一写入）
> 取证方式：GitHub API（仓库元数据 / 完整文件树 / issue 与评论）、仓库 **README 与 7 个源码文件原文**（经 `ghproxy.net` 代理读 raw，本机直连 `raw.githubusercontent.com` 与 GitHub HTML 页均失败）、arXiv 论文全文（ar5iv）、Zenodo API（权重记录 + MUSDB18 记录）、PyPI JSON API、hf-mirror API、本机 import/体积/GPU 探针。
> **本次没有做的事（贯穿全文的红线）**：**没有下载 5.4 GiB 权重、没有跑过它任何一次推理、没有做过与本项目的任何 A/B 对比、没有克隆它的仓库、没有运行 `pip install -r requirements.txt`**。因此本文所有"它侧"的精度数字都是**论文/README 自报值（引证）**，不是实测值；所有显存、速度、体积增量都标了是"实测"还是"推算/未验证"。

---

## 0. 一句话结论

**它是一个"给定了歌词、只要时间轴"的普通话字级声学强制对齐模型（Whisper medium + 自研 GRU + 帧级 Viterbi，20 ms 分辨率），只对应我们 `matcher.py` 这一个环节；它没有任何说话人概念（对唱/和声/`ttm:agent` 完全帮不上），也不解决歌词识别（相对未微调的 Whisper medium 在 MIR-1k mix 上只赢 0.3 个 CER 点，而我们的基线是 large-v3）。代码仓库「没有任何许可证」（不能复制代码、不能随发行版分发），权重虽然被 Zenodo 标成 CC-BY-4.0，但训练用的 Opencpop 与 MUSDB18 都是非商业条款，**元数据与上游来源冲突**。再加上权重 5.43 GiB、依赖含 openai-whisper/transformers/librosa/numba/pandas、且首次运行要联网拉 Whisper medium 与 bert-base-chinese，它与本项目"离线、免 Key、模型不进安装包"的既定叙事冲突明显。结论：**不值得直接集成；只值得用一个便宜、可证伪的受控实验去验证「帧级声学对齐是否真的比我们现有 DP 更准」（§6），权重的分发问题在澄清之前一律不碰。****

---

## 1. 它是什么

### 1.1 身份与活跃度（全部为 API 实测）

| 项 | 事实 | 来源 |
| --- | --- | --- |
| 仓库自述 | `Source code of paper "Adapting pretrained speech model for Mandarin lyrics transcription and alignment"` —— **论文的复现代码**，不是库、不是产品 | [repo API](https://api.github.com/repos/navi0105/LyricAlignment) |
| 论文 | Jun-You Wang, Chon-In Leong, Yu-Chen Lin, Li Su, Jyh-Shing Roger Jang（台大 + 中研院），**ASRU 2023** | [arXiv:2311.12488](https://ar5iv.labs.arxiv.org/html/2311.12488) |
| 规模/活跃度 | 19 star / 1 fork / 569 KB / **license: null** / open_issues 0；建于 2023-05-04，**最后 push 2023-12-14** | [repo API](https://api.github.com/repos/navi0105/LyricAlignment) |
| Issue | 全部 3 条（其中 2 条是作者自己的合并 PR），唯一的用户提问 [#3](https://github.com/navi0105/LyricAlignment/issues/3) 已关闭 | [issues API](https://api.github.com/repos/navi0105/LyricAlignment/issues?state=all&per_page=100) |
| 作者表态（原文） | "we have only tried the fine-tuning of the Whisper Large-v2 decoder (freeze encoder) … the results of lyrics transcription are better than using Whisper Medium, but **the accuracy of lyrics alignment is worse**"；"**there are no plans to release other fine-tuned Whisper checkpoints**"（2024-08-09） | [issue #3 comments](https://api.github.com/repos/navi0105/LyricAlignment/issues/3/comments) |
| 权重发布 | Zenodo 记录 10373790，`checkpoints.zip` = **5,825,687,709 B**，含 `alignment/`（对齐）与 `transcription/`（识别）两套 | [Zenodo API](https://zenodo.org/api/records/10373790) |

### 1.2 它做的两件事（论文 §1、§2.3 的定义）

- **ALT / 歌词识别（transcription）**：给音频 → 输出歌词文本。做法是**直接微调 Whisper**（不改结构），交叉熵损失。
- **字级歌词对齐（alignment）**：给音频 **+ 已知歌词** → 输出**每个汉字的 onset / offset**。**这才是与我们同任务的那一半。**

我们这条链路（`preprocess → asr → matcher → tail_extend → ttml_writer`）做的是**对齐**，歌词是用户给的。所以**对本项目有价值的是 `alignment/` 那套权重，不是 `transcription/`。**

### 1.3 对齐模型：架构与推理链路（源码级核实）

| 环节 | 事实 | 来源 |
| --- | --- | --- |
| 主干 | **Whisper medium**（论文 §2.2 原话：选它是因为"我们的显存不大"） | [论文](https://ar5iv.labs.arxiv.org/html/2311.12488)、[README](https://github.com/navi0105/LyricAlignment/blob/main/README.md) |
| 对齐头（自研） | `AlignModel` = Whisper **encoder** → `RNN`（**2 层双向 GRU，hidden 384，dropout 0.15** → **Mish** → Linear）→ 帧级分类 | [module/align_model.py](https://github.com/navi0105/LyricAlignment/blob/main/module/align_model.py) |
| 分类目标 | **声调无关的拼音音节类**（`pypinyin` 转换，论文：**401 个音节类 + 1 个静音类**），损失 = CTC + CE 相加 | [论文 §2.3](https://ar5iv.labs.arxiv.org/html/2311.12488) |
| 推理 | `perform_viterbi*()`：对**整首歌的逐帧后验**做 Viterbi 强制对齐，**帧间隔 hop = 0.02 s（20 ms）** | [utils/alignment.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/alignment.py) |
| 两种后处理 | 非 CTC 版：**class 0 = 静音**，其余为音节类；CTC 版：`log_softmax(prediction[:, :, 1:-1])`，**最后一类当静音**（sigmoid 门控）→ **两个 checkpoint 的类别布局不同，跑哪个必须配对 `--use-ctc-loss`** | 同上 |
| 音频输入 | `librosa.load(sr=16000, mono=True)`；另支持 MIR-1k 双声道（0=单声道 / 1=立体声混音取均值 / 2=只取人声轨）—— 这是**数据集专用通道语义**，不是通用分离 | [utils/audio.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/audio.py)、[inference_alignment_nogt.py](https://github.com/navi0105/LyricAlignment/blob/main/inference_alignment_nogt.py) |
| **人声分离在哪** | **不在推理里**：推理只读音频文件；Demucs 是**离线预处理**（论文 §2.4；仓库里是 `dataset_preprocessing/demucs_dataset.py`）。也就是说**要用它的最佳配置，得我们自己先跑 Demucs**（我们本来就有 `separate.py`） | 同上 + [论文表 2](https://ar5iv.labs.arxiv.org/html/2311.12488) |
| 长音频 | `frame_manual_forward()` 按 Whisper 的 30 s 窗口（`N_FRAMES`）**逐块前向、每块独立 pad/trim**，再把 embedding 拼起来；Viterbi 覆盖整段 | [module/align_model.py](https://github.com/navi0105/LyricAlignment/blob/main/module/align_model.py) |

### 1.4 训练数据与测试数据（论文 §2.5、§2.6）

| 项 | 事实 |
| --- | --- |
| 训练 | **Opencpop 5.2 h**（**一位女专业歌手、录音棚清唱**，44.1 kHz）+ **数据增强**：拿 **MUSDB18 的 50 首 test 集**伴奏按 **SNR 0 / −5 / −10** 混出三个加噪版本 |
| 测试（识别+对齐） | Opencpop test（5 首，清唱）+ **MIR-1k**（2.2 h、**19 位业余歌手**的老录音，复音）；MIR-1k 分 *mix*（真混音）与 *vocal*（用真值人声轨，等价 oracle 分离） |
| 对齐的**人工标注真值** | 论文自建：**MIR-1k 的 17 首 / 共 19 分 33 秒**，逐字 onset/offset 标注（标注文件就在仓库 `dataset_preprocessing/MIR1k_partial_align.json`，201 KB，已在文件树中核实存在） |
| 作者自承认的局限（§3.5） | ① 出现过"作词家/作曲：李宗盛"式幻觉 → **MIR-1k 里可能有歌本来就在 Whisper 预训练数据里，"would … make us overrate the model performance"**；② 中文对 Whisper 并不是低资源（23,446 h 中文语音），结论**不能外推到别的语言** |

来源：[论文全文](https://ar5iv.labs.arxiv.org/html/2311.12488)、[文件树 API](https://api.github.com/repos/navi0105/LyricAlignment/git/trees/main?recursive=1)。

### 1.5 公开权重：有什么、怎么用、缺什么

- **有**：Zenodo `checkpoints.zip`（`alignment/` + `transcription/`），**access_right = open**，元数据许可标 **CC-BY-4.0**，5,825,687,709 B，md5 `577f06b3d76dea21559580350723438b`；下载 159 次 / 独立 88 / 浏览 198（[Zenodo API](https://zenodo.org/api/records/10373790)）。
- **怎么用**（源码 + README 核实）：
  ```bash
  python inference_alignment_nogt.py -f test_data.json --model-dir <model_dir> --use-ctc-loss --device 0
  ```
  JSON 形如 `[{"song_path": "<绝对路径>", "lyric": "<整首歌的歌词字符串>"}]`（`on_offset` 可选，无真值时不写）——[data_processor/record.py](https://github.com/navi0105/LyricAlignment/blob/main/data_processor/record.py)。
- **输出**：每首歌 `print()` 一个 Python 列表 `[[onset, offset, char], …]` 到 **stdout** —— **没有库 API、不写文件、没有逐字置信度**（[inference_alignment_nogt.py](https://github.com/navi0105/LyricAlignment/blob/main/inference_alignment_nogt.py)）。**不需要重新训练**即可推理。
- **但"下载即用"有四道坎（全部源码可见）**：
  1. `--model-dir` 必须同时有 `args.json` 与 `{--model-name}_model.pt`（默认找 `best_model.pt`），可选 `model_args.json`；
  2. `whisper.load_model(whisper_model_name, ...)` 会**另外去下载/读取 Whisper medium 权重**（本机 `~/.cache/whisper` 不存在，实测），即 Zenodo 之外还有一份上游权重；
  3. `bert_base_chinese_pronunce_table.json` 是**当前工作目录相对路径**读的 → **必须 cd 到仓库根**再跑；
  4. `transformers.AutoTokenizer.from_pretrained('bert-base-chinese')` 需要 HF 侧文件（本机实测 **huggingface.co 直连失败**，只有 hf-mirror 通）→ 离线环境必须预置。
- ⚠️ **源码里的一个真 bug（已验证）**：`load_align_model_and_tokenizer()` 在 `model_args.json` 缺失时会用 `args.predict_sil`，但 `inference_alignment_nogt.py` 的 argparse **根本没有 `--predict-sil`** → 该分支一旦走到就是 `AttributeError`。也就是说 **checkpoint 目录缺 `model_args.json` 时，这个"无真值"脚本会直接崩**。
- `requirements.txt` 原文（[来源](https://github.com/navi0105/LyricAlignment/blob/main/requirements.txt)）：`tqdm, numpy, pandas, librosa, torch>=1.13.1, openai-whisper, transformers, jiwer, demucs, soundfile, numba, pypinyin, opencc-python-reimplement, chinese_converter`。
  ⚠️ **实测：`opencc-python-reimplement` 在 PyPI 上 404**（[PyPI JSON](https://pypi.org/pypi/opencc-python-reimplement/json)），正确包名是 `opencc-python-reimplemented`（0.1.7，存在，[PyPI](https://pypi.org/pypi/opencc-python-reimplemented/json)）→ **照 README 原样 `pip install -r requirements.txt` 会失败**，这是"论文代码"而非"可安装依赖"的直接证据。

### 1.6 论文自报的指标（**全部是自报，未见第三方复现**）

| 指标 | Opencpop test（清唱） | MIR-1k mix（复音，主战场） |
| --- | --- | --- |
| 对齐 MAE（字边界，见 §3 定义） | 0.0228 s | **0.0709 s** |
| 同一表里 Whisper 时间对齐解码器 | **0.0193 s（比 Proposed 更好）** | 0.8220 s |
| Proposed 去掉 CTC / 去掉增强 / 用汉字类 | 0.0597 / 0.0239 / 0.0449 | 0.1311 / 0.0993 / 0.1178 |
| 预处理消融（MAE） | Demucs 0.0228 / Spleeter 0.0262 / 直接混音 0.0249 | **Demucs 0.0709 / Spleeter 0.1231 / 混音 0.0980** |
| 识别 CER / PER | 9.3 / 2.0（未微调 Whisper：10.7 / 2.8） | **17.8 / 9.6**（未微调 Whisper：18.1 / 10.0，**只差 0.3 / 0.4 个点**） |
| 时间对齐转写 PCAS-exact / PCAS-pronoun | 86.58% / 91.80% | 77.26% / 81.62% |

来源：[论文表 1–5](https://ar5iv.labs.arxiv.org/html/2311.12488)、[README](https://github.com/navi0105/LyricAlignment/blob/main/README.md)（README 另补：MIR-1k **vocal** 的 CER 9.1% / PER 3.6%）。
⚠️ **未验证**：Zenodo 只写 `"alignment/": lyrics alignment model checkpoint`，**没有说明它对应论文哪一行设置**；"下载下来就是 0.0709 那一行"属于**推断**（论文里 `Proposed` = HT Demucs 版本，是最可能被发布的那个）。

---

## 2. 与现有模块对应表

### 2.1 三件事分开回答（这是本评估最重要的分诊）

| 任务 | 它解决吗 | 证据 | 对应我们的模块 |
| --- | --- | --- | --- |
| **歌词识别（ALT/ASR）** | ⚠️ **有模型，但不建议用**：`transcription/` 权重存在，但相对**未微调 Whisper medium** 只赢 0.3 CER 点，而我们的基线是 **faster-whisper large-v3**；作者本人说换 large-v2 后"转写更好、**对齐更差**"且不再发布 checkpoint | [论文表 3](https://ar5iv.labs.arxiv.org/html/2311.12488)、[issue #3](https://github.com/navi0105/LyricAlignment/issues/3)、[asr.py](asr.py) | `asr.py`（**不替代**） |
| **逐字时间对齐** | ✅ **这是它唯一真正对标我们的能力**：不做 ASR 假设，直接对帧级声学后验做 Viterbi，20 ms 一帧 | [utils/alignment.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/alignment.py)、[论文 §2.3](https://ar5iv.labs.arxiv.org/html/2311.12488) | `matcher.py`（**只可能替换这一个环节**） |
| **说话人分离 / 对唱归属** | ❌ **完全没有**：全仓库没有 speaker/diarization/agent 任何概念，一条音节序列对一段音频，输出只有 `[onset, offset, char]` | [inference_alignment_nogt.py](https://github.com/navi0105/LyricAlignment/blob/main/inference_alignment_nogt.py)、[module/align_model.py](https://github.com/navi0105/LyricAlignment/blob/main/module/align_model.py) | `diarize.py` / `speaker_campplus.py`（**零帮助**，我们的 85.0% 基线照旧要靠自己） |

### 2.2 逐模块对照（"能不能替"只对 `matcher` 有意义）

| 我们的模块（职责） | 现状做法 | LyricAlignment | 判断 |
| --- | --- | --- | --- |
| [matcher.py](matcher.py) | 歌词切单元（**中文按字、英文按词**、标点附前字）↔ ASR 词级时间戳，**Needleman–Wunsch 单调 DP + 拼音相似度**，有 LRC 时按行窗对齐，未匹配字插值，输出带 `matched` 标记与 `match_rate` | 帧级后验 Viterbi（20 ms），**无行窗、无置信度、无英文概念** | **唯一可替换点，但接口不等价**（见下） |
| [asr.py](asr.py) | faster-whisper **large-v3**（CTranslate2），词级时间戳 | Whisper medium + 5.2 h 清唱微调 | **不替代** |
| [diarize.py](diarize.py) / [speaker_campplus.py](speaker_campplus.py) | CAM++ 声纹 + k-means 聚声部（基线 **85.0%**，剔除存疑歌后 89.9%，见 [声纹评估协议.md](docs/声纹评估协议.md) §6） | **无此能力** | 不受影响也不受益 |
| [tail_extend.py](tail_extend.py) | 句尾拖音：**F0 连续性为主 + 能量兜底**，按 agent 追踪、允许叠唱 | 无拖音专门逻辑（MAE 含 offset，但论文没有单独分析 offset/拖音） | 仍需保留 |
| [ttml_writer.py](ttml_writer.py) / [ttml_normalize.py](ttml_normalize.py) | 铁律：`span ⊆ p`、同层不重叠、`itunes:key` 连续、`x-bg` 子内容、`ttm:agent` 对唱 | 输出**只是逐字 `[onset, offset, char]`**，没有行/层/agent 结构 | 仍需保留，且**它的输出必须先过我们自己的窗口/规范化** |
| [preprocess.py](preprocess.py) | LRC/TXT → `LyricLine`；`[v1]/[v2]/[和声]` 标记、括号=背景 | 输入是**一整条歌词字符串**（论文对 ALT 的后处理还"自动删掉英文与空白"） | 仍需保留 |
| [separate.py](separate.py) | Demucs HT 人声分离（`--separate-vocals`） | 论文最佳配置**也要求先分离**（HT Demucs；Spleeter 差 5 个点、混音差 2.7 个点） | **一致**，可复用同一份人声轨做对照 |

### 2.3 换成它以后，我们**仍然一样都不能少**的东西

无逐字置信度（我们的 `matched=False`/"插值/低可信"标记会直接丢失）、无行结构（LRC 行窗要自己加回去）、无 `x-bg` 和声层、无 `ttm:agent`、无拖音判据、无 TTML 规范约束。⇒ **它顶多替换 `matcher` 内部"字时间从哪来"这一步；`matcher` 的单元模型、行窗、插值与规范化一条都省不掉。**

### 2.4 接口不对等的具体代价（适配层不是零成本）

1. **字符集不等价**：它按 `bert-base-chinese` 分词后的 **token** 逐位输出，最后一行 `cur_prediction = [... record.text[i] ...]` **直接用 token 序号去索引原字符串**。中文逐字没问题，但**空格/标点/英文**会让"token 数 ≠ 字符数"，轻则错位、重则 `IndexError`（源码可见，[inference_alignment_nogt.py](https://github.com/navi0105/LyricAlignment/blob/main/inference_alignment_nogt.py)）。
2. **它没有"行"**：要落进我们的 `AlignedLine`，必须自己按 LRC 行窗切分并复用 `ttml_normalize.clamp_units_into_line`（铁律 #13），否则会产出"字时间重叠"的坏文件。
3. **它要求整首歌一次喂**（Viterbi 是全局路径），我们的 DP 是**按行窗**的 —— 误差分布特性不同，实验里必须把这一条作为变量控制住。

---

## 3. 指标可比性（**结论：口径不同，不能直接比大小**）

### 3.1 它的 MAE 到底怎么算（源码级）

```python
# utils/alignment.py
def get_mae(gt, predict):
    for i ...:
        for j ...:
            error += abs(gt[i][j][0]-predict[i][j][0]) + abs(gt[i][j][1]-predict[i][j][1])
            cnt   += 2.0          # ← onset 与 offset 各算一次，等权平均
    return error / cnt
```

即：**MAE = 所有汉字（|Δonset| + |Δoffset|）/ 2 的平均**，真值是**他们自己人工标的 MIR-1k 17 首 / 19 分 33 秒**（以及 Opencpop test 5 首）。注意 `inference_alignment_nogt.py`（无真值那支）**不算 MAE**；MAE 由 `inference_alignment.py` 在有 `on_offset` 时算。

### 3.2 我们现有的评估口径（内部事实）

| 工具 | 真值 | 指标 |
| --- | --- | --- |
| [tools/eval_word_timing.py](tools/eval_word_timing.py) | QQ `.qrc` / 网易 `.yrc` **逐字时间戳**（商业平台，绝对毫秒，最后单位含拖音） | ① **行结束**偏差（我们 − QRC）中位 + **去掉整体平移后的散布**；② **逐字起始**偏差中位 + 去平移后散布（只比 QRC 里是单字单位的位置）；③ 配对用文本相似度 ≥0.6，`|d|>3 s` 的配对失败离群值必须剔除 |
| [tools/eval_diarization.py](tools/eval_diarization.py) | AMLL TTML DB 社区人工 `ttm:agent` 标注 | 逐行一致率（总体 **85.0%**；含版本存疑歌剔除后 89.9%），另有每歌手召回、连续错行段、混淆矩阵 |
| 产物侧 | `ttml_check` + 真 AMLL 解析器 | 结构合法性（0 错误）、行数/字数是否塌成一个词（铁律 #16） |
| ASR 侧 | — | **本项目没有 CER 指标**；AGENTS.md 里记录的是《我不曾忘记》的**字级匹配率 86.4% → 93.8%**（对齐覆盖率，不是 Levenshtein CER） |

### 3.3 为什么两边的数字不能放在一张表里比

| 差异 | 它 | 我们 |
| --- | --- | --- |
| **真值来源** | 论文团队自建人工标注（MIR-1k 17 首 / 19:33，老录音、业余歌手） | 商业平台逐字时间戳（QRC/YRC）+ AMLL DB 社区标注 |
| **音频域** | 单女声清唱（训练）；MIR-1k 复音老录音（测试） | 现代商业中文流行（重混音、和声、对唱、Live） |
| **指标定义** | 字符 **onset 与 offset 等权平均**成一个 MAE | 行结束偏差 与 逐字起始偏差**分开报**，且强调"去掉整体平移后的散布"（因为录音不同会带来常数偏移） |
| **评价对象** | 纯对齐（歌词给定） | **端到端产物**（ASR 误差、行窗、拖音、规范化的影响都在里面） |
| **单位/粒度** | 汉字字符 | `Unit`（中文按字、**英文按词**、标点附前字）+ 主唱/背景两层 + agent |

⇒ **"它 0.0709 s vs 我们 X s"这种句式一律是错的。**唯一合法路径是 §6 的受控实验：**同一音频、同一人声轨、同一行边界、同一把尺子**。

### 3.4 顺带一条容易被忽略的等价性

论文的 "Proposed (char)"（直接预测汉字类）MAE 明显更差（MIR-1k mix 0.1178 vs 0.0709），作者解释是小数据下分类数太多。**这一条对我们的启示不是"用它的 char 模型"，而是"音素/音节类目标比汉字类目标更适合小数据"** —— 与我们 `matcher.similarity()` 用拼音相似度容错是同一个方向（[matcher.py](matcher.py) 第 29–52 行）。

---

## 4. 许可与分发风险（**代码 / 权重 / 训练数据分开判断**）

### 4.1 三份许可，三份都不一样（逐条附来源）

| 对象 | 事实（本次核实到的原文/元数据） | 性质 | 来源 |
| --- | --- | --- | --- |
| **① 源码** | GitHub API `license: null`；**完整文件树（truncated=false）里没有 LICENSE / COPYING**；README 全文（已逐段读完）**没有任何许可章节** ⇒ **无许可 = 默认"保留所有权利"** | **已验证** | [repo API](https://api.github.com/repos/navi0105/LyricAlignment)、[完整文件树](https://api.github.com/repos/navi0105/LyricAlignment/git/trees/main?recursive=1)、[README](https://github.com/navi0105/LyricAlignment/blob/main/README.md) |
| **② 预训练权重** | Zenodo 记录元数据 **`license.id = cc-by-4.0`**、`access_right = open`；单文件 `checkpoints.zip` 5,825,687,709 B；下载 159 / 独立 88 / 浏览 198 | **已验证（元数据）** | [Zenodo API](https://zenodo.org/api/records/10373790) |
| **③ 训练数据（Opencpop，5.2 h）** | 官方许可页 <https://wenet.org.cn/opencpop/liscense/> 本次**不可达**（HTTPS 与 GitHub Pages 均失败）⇒ **官方原文未证实**。两处独立第三方文字：VoxKitchen 数据集目录写 **CC BY-NC-ND 4.0** 且"blocks commercial use and derivative redistribution"；另一个基于 Opencpop 训练的第三方模型 ToU 转述官方规则"**Commercial usage is prohibited**. An exception can be made for those who have gotten permission from Opencpop Team" | **未证实（第三方一致指向非商业）** | [VoxKitchen 数据集目录](https://github.com/XqFeng-Josie/VoxKitchen/blob/main/docs/datasets/opencpop.md)、[第三方 ToU 镜像](https://github.com/lottev1991/opencpop-cjke-multidict/blob/master/ToU_EN.md)、[官方仓库 README](https://github.com/wenet-e2e/opencpop) |
| **③′ 训练数据（MUSDB18，用于加噪增强）** | Zenodo 元数据 **`license.id = other-nc`**，notes 原文：**"provided for educational purposes only and the material contained in them should not be used for any commercial purpose without the express permission of the copyright holders"**；且其内部来源含 **MedleyDB（CC BY-NC-SA 4.0）** 与 The Easton Ellises（**CC BY-NC-SA 3.0**） | **已验证** | [MUSDB18 Zenodo API](https://zenodo.org/api/records/1117372) |
| ④ 上游 Whisper medium | HF 模型卡 **license: apache-2.0**，`763,857,920`（F32）参数 | 已验证 | [hf-mirror API](https://hf-mirror.com/api/models/openai/whisper-medium) |
| ⑤ bert-base-chinese | 只用到 **tokenizer 词表**（模型里没有 BERT 模块）；**其许可本次未核实** | 未核实 | [get_pronunce_table.py](https://github.com/navi0105/LyricAlignment/blob/main/get_pronunce_table.py) |

### 4.2 能不能**集成**进本项目

- **可以做的形态**：把它当作**用户自备的可选后端**（权重与本机仓库都在**用户自己的机器**上，我们不复制它的代码、不分发它的权重、只在本地调用）。这一形态在许可上**不产生我们的再分发行为**，与本项目"离线、不需要 API Key"的立场也不冲突（它全程本地，无 Key ✅）。
- **不可以做的形态**：
  * **复制它的源码文件进我们仓库** —— 无许可就是没有授权（算法思想不受版权保护，但**它的实现文件受**）；
  * **把它的代码当成"我们的实现"再发布**（包括改写成我们风格的逐行翻译）。
- **可以选择的第三种**：**自写**加载器 + 帧级 Viterbi（算法层面可独立实现；但**权重本身的许可问题不会因为自写代码而消失**）。⚠️ 本文不评估"自写 Viterbi 的工程量"，那属于另一件事。

### 4.3 能不能**随商业软件分发**（这是最关键的否决点）

| 分发包内容 | 判断 | 理由 |
| --- | --- | --- |
| 它的**代码** | ❌ **不能** | 无许可证。哪怕只是打包进安装目录，也是再分发 |
| 它的**权重** | ❌ **不建议 / 高风险**，需先澄清 | Zenodo 标 CC-BY-4.0，但**上游训练数据是非商业**（MUSDB18 `other-nc` 是 Zenodo 官方元数据、可直接核实；Opencpop 至少两处独立第三方指向非商业）。**发布者给派生权重贴的许可与上游条款冲突，冲突的解决方式轮不到我们猜**。另外 MUSDB18 内含 **BY-NC-SA（ShareAlike）** 曲目，SA 是否延伸到"用它训练的权重"本身就有争议 |
| 我们**自己**的安装包 | ✅ 目前是干净的 | [requirements.txt](requirements.txt) 已就"网络定义与权重不随仓库分发"写明；安装包本身也不含模型（`desktop/resources/model_manifest.json` 是唯一模型清单） |
| **打破现有叙事**的风险 | ⚠️ 高 | 引一个"代码无许可 + 权重 NC 派生"的组件，会让现在这套已经写清楚的许可叙事（faster-whisper MIT、Whisper MIT/Apache、CAM++ 权重单独声明）出现第一个说不清的黑洞 |

### 4.4 如果一定要试，把风险留在用户侧的边界（建议写法）

1. **我们的仓库与安装包**：零它的代码、零它的权重；
2. **登记处**：如果将来做"一键下载"，只能登记进 `desktop/resources/model_manifest.json`，并把"许可：Zenodo 标 CC-BY-4.0，但训练数据含 MUSDB18（非商业，Zenodo 官方标 `other-nc`）与 Opencpop（官方条款未证实、两处第三方称禁止商用），**商业用途需自行向权利方澄清**"原样写进清单；
3. **不许自动下载、不许静默回退**：`doctor`/`envcheck` 只报"未安装（可选）"，让用户明确知道自己在用第三方权重；
4. **候选替代（许可更干净，未验证）**：[jhuang448/LyricsAlignment-MTL](https://github.com/jhuang448/LyricsAlignment-MTL)（据称 MIT）与其多语言版。**注意：它们的语言覆盖、是否发布权重、对中文流行歌效果，本次一律未核实** —— 这里只是登记线索，不是推荐。

---

## 5. 依赖与体积代价

### 5.1 会新增哪些 pip 依赖（对照本项目现状）

它要装：`openai-whisper, transformers, librosa, soundfile, numba(→llvmlite), pandas, jiwer, opencc-python-reimplemented(需改名), chinese_converter`（另 `tqdm/pypinyin/demucs/torch` 我们已经有，且都是可选项）。

**本机实测（该解释器 = `<Python314 安装目录>\python.exe`，3.14.6；安装时间全部是 2026-08-14，早于本次评估，不是在评估中装的）：**

| 包 | 目录体积（实测） | 状态 |
| --- | --- | --- |
| torch | **496.6 MB** | 已装（但 **2.13.0+cpu**，`cuda_available=False`） |
| llvmlite | **116.2 MB** | 已装（numba 的依赖） |
| onnxruntime | 42.8 MB | 已装 |
| numpy | 30.3 MB | 已装 |
| numba | 26.4 MB | 已装 |
| tokenizers | 7.5 MB | 已装 |
| huggingface_hub | 6.3 MB | 已装 |
| av | 4.1 MB | 已装 |
| tiktoken | 2.3 MB | 已装 |
| **openai-whisper** | **2.0 MB** | **已装（20250625）** |
| faster_whisper | 1.4 MB | 已装（1.2.1） |
| **transformers / librosa / soundfile / pandas / jiwer / opencc / chinese_converter / safetensors / pypinyin / demucs** | — | **未装（本机实测 MISSING）** |

⚠️ **必须说清的两点**：
1. 这个解释器的 torch 是 **CPU 版**，**GPU 存在但本 shell 用不上**（实测：`nvidia-smi` = **RTX 4060 Laptop, 8188 MiB**；`torch.cuda.is_available() = False`）⇒ **本环境无法做有意义的显存/速度实测**（项目本身 GPU-only 的约束在别的环境满足）。
2. 上表是"本机这个解释器"的现状，**不等于打包发行环境的现状**：打包环境的实测清单在 [商业化重构审计与体积报告.md](docs/商业化重构审计与体积报告.md)（PyTorch 452 MiB = 3.0 GiB onedir 的 14.5%、onnxruntime 38 MiB 已在包内）；**"打包后到底会涨多少 MB/GB，本次没有构建验证**。

### 5.2 权重与磁盘

| 项 | 数字 | 性质 |
| --- | --- | --- |
| Zenodo `checkpoints.zip` | **5,825,687,709 B ≈ 5.43 GiB**（含 `alignment/` + `transcription/` 两套） | **已核实**（Zenodo 元数据） |
| **只取 alignment 那套有多大** | **未验证**（没下 5.4 GiB）。⚠️ 不要引用"单个约 2.9 GB"当事实 —— 那是**从"训练脚本用 `torch.save(model.state_dict())` 会连 Whisper 主干一起存"推断出来的**，我没有读 `train_multitask.py`、也没有解压验证 | **推断 / 未验证** |
| 额外要下的 Whisper medium | HF 上 `763,857,920` 参数（F32）。按参数换算：**fp32 ≈ 3.06 GB，fp16 ≈ 1.53 GB**（这是**算术推算**，不是实测的 `.pt` 文件大小） | 参数已核实，体积为推算 |
| bert-base-chinese tokenizer | 词表级文件（百 KB~MB 级） | 未实测 |
| 我们现有的 Whisper large-v3 | 约 3 GB（[asr.py](asr.py) 的提示文案） | 已核实 |
| 安装包现状（对照） | 未裁剪 onedir **3.0 GiB** → 单文件安装程序 **1.82 GB** → 裁剪后 **0.68 GB**；模型另下 ~3 GB | 已核实（[体积报告](docs/商业化重构审计与体积报告.md)） |

### 5.3 安装包与启动时间

- **体积**：即使只做"用户自备权重"，也要把 `transformers + tokenizers + safetensors + huggingface_hub + librosa + soundfile + numba + llvmlite + pandas + jiwer + opencc + chinese_converter` 收进发行版（或做成运行时组件），**量级是数百 MB 解压后**（本机实测 llvmlite 一项就 116.2 MB、numba 26.4 MB）。⚠️ 精确增量**未构建验证**。
- **启动**：`import whisper` / `import transformers` 会把导入时间显著推高（本项目桌面版有"主线程 400 ms 刷新"与惰性加载的纪律，[AGENTS.md](AGENTS.md) §4.5）。⚠️ **未实测**。
- **首次运行必须联网**：`whisper.load_model(...)` 与 `AutoTokenizer.from_pretrained('bert-base-chinese')` 默认都要联网（本机实测 `~/.cache/whisper` 与 HF hub 缓存**都不存在**，huggingface.co **直连失败**）⇒ 与"必须离线可用"的约束直接冲突，除非把这两份上游文件也纳入预置流程。

### 5.4 显存 / 内存（**未实测**，只给依据）

- **官方从来没有给过显存需求**（论文与 README 都没有）⇒ **未查到**。
- 可核实的下界参考：Whisper medium = 763.9 M 参数（已核实）；对齐头（2 层 GRU 384 + Linear）很小。
- 代码结构决定的量级（源码可见）：整首歌的 mel + encoder embedding 会**留在显存/内存里**，长音频线性增长（300 s ÷ 0.02 s = 15,000 帧 × 1024 维 × 4 B ≈ **61 MB** embedding/首歌，推算）；Viterbi 的 DP 矩阵是 `float64`、形状 `T × (2L+1)`（`np.array([[-10000000.0 ...]])` 初始化）→ **5 分钟歌、400 字 ≈ 15000×801×8 B ≈ 96 MB/矩阵 ×2 ≈ 192 MB 内存**（**算术推算，不是实测**）。
- 本机 GPU 是 **8 GB（实测）**，而链路里还有 faster-whisper large-v3 与 Demucs；**是否放得下、会不会 OOM：未验证**。

### 5.5 能不能用 ONNX 等轻量后端？

| 问题 | 事实 | 判断 |
| --- | --- | --- |
| 它提供 ONNX 导出吗？ | **没有**：仓库完整文件树里没有任何导出脚本/onnx 产物；推理是 PyTorch | 已核实 |
| 结构上能不能导出？ | 主干是 **Whisper encoder**（业界有 ONNX 化先例，faster-whisper 用的是 CTranslate2），对齐头是 **2 层 GRU + Linear**（ONNX 标准算子即可）⇒ **原理上可行** | **推测** |
| 我们有没有现成的运行时？ | 本机实测 `onnxruntime 42.8 MB` **已装**；体积报告也写"onnxruntime 本就在包里（38 MiB），②**把 CAM++ 换成 onnxruntime 后端**"是候选路线 | 已核实 |
| 关键提醒 | **ONNX 化解决不了许可问题**：把权重从 `.pt` 转成 `.onnx` 仍然是**同一份派生权重**的分发，训练数据是 NC 这一条不变 | 逻辑结论 |
| 结论 | **可作为"降低运行时体积/避开 torch 依赖"的未来选项，但本次未验证可行性，也不改变 §4 的分发结论** | 推测/待实验 |

---

## 6. 公平对照实验设计（离线、可证伪、不碰生产链路）

> 目标：回答一个**是/否**问题 —— **"在中文流行歌的逐字时间轴上，它的帧级声学对齐是否稳定优于我们现有的 DP（在行窗内、同一人声轨、同一把尺子下）？"**
> 成本估计：半天到一天（环境 + 3 首歌跑通）；**不写进主链路**。

### 6.1 控制变量（少一个就不公平）

| 变量 | 必须固定为 |
| --- | --- |
| 音频 | 同一份**原始音频**；两条臂都用**同一份 Demucs HT 人声轨**（预先用 `separate.py` 生成一次，两边复用同一个 wav；因为论文的最佳配置也要求先分离） |
| 歌词 | 同一份 `preprocess.py` 产出的 `LyricLine`（含 `[v1]/[v2]` 标记剥离后的正文）；喂给它时按 §2.4 的规则拼成字符串（**只保留汉字**，去掉标点/英文/空白，并**记录被去掉的字符数**，因为它按 token 索引原串） |
| 行边界 | 我们的 LRC 行窗。它的原生用法是整首一次 Viterbi ⇒ 报**两个变体**：(a) 原生整首；(b) 本地驱动按行窗调用（窗口两端留 padding）——两者结果都要报，(b) 与我们的 DP 才是同条件比较 |
| 硬件/环境 | 同一个 venv 之外单开一个 venv 装它（**不污染本项目环境**）；同一张 GPU；每首歌跑 **3 次取中位** |
| 评测代码 | 只用我们已有的 `tools/eval_word_timing.py` 口径（**不许为它改指标定义**） |

### 6.2 数据（先"验尺"，否则结论无效）

1. **首选**：有 **QRC/YRC 逐字真值**且与我们手上音频**同一录音**的歌（艺人署名一致 + 歌词 2-gram 重合 ≥0.95）。AGENTS.md 已给出教训：《让风告诉你》QRC 中位 **−0.228 s** 其实主要是**整体偏移**，不是打轴不准 ⇒ **先去掉整体平移再比散布**。
2. **至少 3 首**，且必须覆盖三种结构：**单声部**／**带 `x-bg` 和声**／**对唱（多 agent）**。理由：它没有说话人概念，第 3 类最容易暴露"它的输出根本没法落进我们的 agent 结构"。
3. **可选加厚**：AMLL TTML DB 里有人工逐字标注的歌（它同时给了行级 agent 真值），可与 `tools/eval_diarization.py` 的 4 首评估集复用同一批音频。
4. ⚠️ **不要**用 MIR-1k/Opencpop 来"复现论文数字"当结论：那是它的训练域（且论文自承认可能有 Whisper 预训练污染），**只可用于"我们的驱动脚本是否跑对了"的 sanity check**。

### 6.3 五个必报指标（定义写死，事先约定）

| # | 指标 | 精确定义 | 注意 |
| --- | --- | --- | --- |
| 1 | **歌词字符错误率 CER** | 只在**用它的 `transcription/` 权重**时才存在：ASR 输出 vs 参考歌词（同一归一化：去空白/标点、繁→简），Levenshtein / 参考长度。**同时报我们 large-v3 的同口径 CER** | ⚠️ **对我们的产物没有直接影响**（我们的歌词是给定的）；这一项是"是否值得换识别模型"的独立问题。**不许拿它的 17.8% 和我们的"字级匹配率"比**（不同定义） |
| 2 | **逐字边界 MAE** | **onset MAE 与 offset MAE 分开报**（中位 + 均值 + p90）；**再报"去掉整体平移后的散布"**；对齐点 = QRC/YRC 里是**单字单位**的位置 | ⚠️ **严禁**照抄它的 `(onset+offset)/2` 口径混合比较——口径不同（§3.1） |
| 3 | **有效时间戳覆盖率** | 定义：`落在所属行窗口内 且 有真实声学证据 的逐字时间戳数 / 应输出总字数`。我们侧 = `matched=True`（非插值）比例（`AlignReport.match_rate`）；它侧**没有置信度** ⇒ 用三个代理并报：(a) 输出字符数 == 输入汉字数 的比例；(b) 时间落在行窗（含 padding）内的比例；(c) 能与 QRC 单字单位在 ±0.3 s 内配上的比例（"可核对覆盖率"） | ⚠️ **覆盖率不等于准**：我们的"插值"是**有标记**的，它是**错了也不说**；必须与 #2 一起读 |
| 4 | **处理速度** | 端到端墙钟 + **RTF**（秒/音频秒），中位；分阶段报（Demucs / encoder / Viterbi / 写盘）；`torch.cuda.max_memory_allocated()` 峰值 | 人声轨**预生成**，不计入两臂差异；ASR 阶段对我们单列 |
| 5 | **失败率** | = 出现下列任一情况的样本占比：进程非零退出 / 超时 / OOM / stdout 无法解析成列表 / 字符数不匹配 / 时间非单调或超出音频时长 / 产出 TTML 过不了 `ttml_check --strict-time` 或塌成一个词 | 分母必须写清（按歌还是按字） |

**附加诊断（都要报，不许挑）**：每首歌**逐条**列结果（不许只报总体）；**错行/错字是否成段**（连续段长度比孤立点更说明问题——与 [声纹评估协议.md](docs/声纹评估协议.md) §3 的纪律一致）；以及"它的输出在我们的 `x-bg`/agent 结构上是否根本落不下去"的定性记录。

### 6.4 流程（可复核）

1. 建**独立 venv**（绝不装进本项目环境），`pip install` 时把 `opencc-python-reimplement` 换成 `opencc-python-reimplemented`；仓库克隆到**本项目目录之外**。
2. 从 Zenodo 下 `checkpoints.zip`，只解压 `alignment/`；确认其中有 `args.json` 与 `*_model.pt`（**顺手验证 `model_args.json` 在不在** —— 不在的话按 §1.5 的 bug，`inference_alignment_nogt.py` 会崩，这本身就是一条要记录的失败模式）。
3. 预生成 Demucs 人声轨（一次，两臂共用）。
4. 生成它的输入 JSON（`song_path` + **纯汉字歌词字符串**），**cd 到它的仓库根**运行，捕获 stdout，`ast.literal_eval` 解析。
5. 写一个**只读**的对照脚本，把它的逐字时间**映射回我们的 `Unit`**（中文按字 ↔ 汉字一一对应；标点按我们的规则附前字；**英文部分标记为不可用**），再跑 `ttml_normalize` 让产物合法，然后用 `tools/eval_word_timing.py` 的口径评分。
6. **全程不改生产代码**；实验脚本与产物放项目外的临时目录或 `outputs/eval/`（由项目负责人决定），结论写回本文件。

### 6.5 事先约定的采用标准（**事后不许挪门槛**）

建议直接沿用本项目已用过的"四条全过才换"纪律（[tools/eval_diarization.py](tools/eval_diarization.py) §4 同款）：

1. **主指标**：逐字 **onset** MAE（去平移后散布 + 中位）在 **≥2/3 的歌曲**上比现 DP 低 **≥ 30 ms**；
2. **不许拆东墙**：任何一首歌的 **offset** 指标恶化 ≤ **+50 ms**，且**没有任何一首**的 onset 恶化 > **100 ms**；
3. **覆盖率不下降**（按 #3 定义），且**失败率 = 0**（任何 1 首失败即不接入，除非根因明确是我们适配层的 bug）；
4. **速度**：端到端 ≤ **3×** 现基线；
5. 另加**硬性前置**：许可澄清（§4）没有进展之前，**无论实验多好看都不接入发行版**（可以只留在文档里）。

> ⚠️ 这些阈值是**建议值**，不是从数据里推出来的；一旦决定做实验，**先签字再跑**（这正是 [声纹评估协议.md](docs/声纹评估协议.md) §4 的教训）。

### 6.6 用一句话预判（**这是推测，不是结论**）

它的机制优势（不依赖 ASR 文本假设、20 ms 帧级）最可能在**"ASR 成段漏字/幻觉/词边界系统性偏移"**的歌上体现；而它的劣势（无置信度、无行窗、无说话人、英文退化、域窄）最可能在**对唱/和声/中英混合**的歌上暴露。⇒ **实验至少要有一首"我们已知 ASR 出错"的歌和一首对唱歌**，否则测不出差异。

---

## 7. 结论与建议

### 7.1 结论：**不集成**（作为产品能力）；**只保留一个受控实验的可能性**

三条否决理由，逐条对应上面的证据：

1. **能力面不匹配**：它只对标 `matcher`，**说话人分离（我们最难、基线 85.0% 的那一块）它一点忙都帮不上**；识别侧相对未微调 medium 只有 0.3 个点的边际收益，而我们已经是 large-v3（[论文表 3](https://ar5iv.labs.arxiv.org/html/2311.12488)、[issue #3](https://github.com/navi0105/LyricAlignment/issues/3)）。
2. **证据强度不够**：全部精度数字都是论文/README 自报；测试域是 **MIR-1k 17 首 / 19 分 33 秒的业余老录音**，训练域是**一位女歌手 5.2 h 清唱**；论文自己承认**可能的数据污染会让结果被高估**；本次检索**未见任何第三方复现或对比评测**。**现代商业中文流行歌（重混音/和声/对唱/Live）上没有任何证据。**
3. **许可与体积双重不划算**：**代码无许可（不能复制、不能分发）**；权重虽标 CC-BY-4.0，但训练数据含 **MUSDB18（Zenodo 官方 `other-nc`）**、Opencpop（官方条款未证实、两处第三方称禁止商用）；叠加 **5.43 GiB 权重 + Whisper medium + bert tokenizer + openai-whisper/transformers/librosa/numba/pandas 依赖 + 首次运行必须联网**，与本项目"离线、免 Key、模型不进包、安装包已压到 0.68 GB"的既定方向正面冲突。

### 7.2 建议的三步（按优先级）

1. **（零成本，建议立刻做）**：若动机是"逐字起点不够准"，先做**行内二次精修**——用我们已有的 Demucs 人声轨，在 `matcher` 给出的行窗/字窗内，用能量 + F0 边界重新估计字起点（与 `tail_extend.sustain_end` 同一套信号处理资产）。**许可风险为零、体积增量零**，且能被 `tools/eval_word_timing.py` 立刻验证。
2. **（半天到一天，可选）**：按 §6 跑一次受控实验，**只为回答"要不要继续投资声学对齐后端"**。跑之前：许可与体积问题**不作为本次实验的阻塞项**（实验只在本机），但**结论不得用于"打包分发"的决定**。
3. **（只在实验赢了之后）**：接入形态固定为 **`align_backend` 可选后端、默认 `dp`、权重用户自备、不进安装包、不复制代码**，并按 [AGENTS.md](AGENTS.md) §4.5 的纪律同步 `ConvertOptions` / `desktop/services/convert_params.build_options()` / `selftest.py`（缺权重必须**跳过**而不是失败）。**在此之前，本文件就是这条线的全部产出。**

---

## 8. 证据边界

### 8.1 已核实（每条都有可点开的来源）

| 结论 | 来源 |
| --- | --- |
| 仓库 **license: null**、完整文件树**无 LICENSE/COPYING**、README 全文**无许可章节** | [repo API](https://api.github.com/repos/navi0105/LyricAlignment)、[文件树](https://api.github.com/repos/navi0105/LyricAlignment/git/trees/main?recursive=1)、[README](https://github.com/navi0105/LyricAlignment/blob/main/README.md) |
| 仓库身份/活跃度：19★ / 1 fork / 569 KB / 建 2023-05-04 / **最后 push 2023-12-14** / open issues 0 | [repo API](https://api.github.com/repos/navi0105/LyricAlignment) |
| 全部 3 条 issue（2 条是作者自己的 PR）；#3 里作者原话：large-v2 微调后"转写更好、**对齐更差**"、"**不再发布其他 checkpoint**" | [issues](https://api.github.com/repos/navi0105/LyricAlignment/issues?state=all&per_page=100)、[comments](https://api.github.com/repos/navi0105/LyricAlignment/issues/3/comments) |
| 主干 Whisper **medium**；对齐头 = 2 层双向 GRU(384, dropout 0.15) + Mish + Linear；`embed_dim` 由 `WHISPER_DIM[medium]=1024` 决定 | [align_model.py](https://github.com/navi0105/LyricAlignment/blob/main/module/align_model.py)、[inference_alignment_nogt.py](https://github.com/navi0105/LyricAlignment/blob/main/inference_alignment_nogt.py) |
| 推理：`perform_viterbi*(hop_size_second=0.02)`；非 CTC 版 class 0 = 静音，CTC 版最后一类 = 静音（`[:, :, 1:-1]`）；`get_mae` 把 **onset 与 offset 等权平均** | [utils/alignment.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/alignment.py) |
| 推理 I/O：JSON(`song_path`+`lyric`[+`on_offset`]) → **stdout print** 的 `[[onset, offset, char]]`；**无库 API、无文件输出、无逐字置信度**；拼音表**相对路径**读；**`args.predict_sil` 未在该脚本 argparse 中定义（缺 `model_args.json` 时必崩）** | [inference_alignment_nogt.py](https://github.com/navi0105/LyricAlignment/blob/main/inference_alignment_nogt.py)、[record.py](https://github.com/navi0105/LyricAlignment/blob/main/data_processor/record.py) |
| 音频入口只有 `librosa.load(sr=16000, mono=True)`（+ MIR-1k 专用通道语义 0/1/2）⇒ **Demucs 不在推理路径里**，要自己先分离 | [utils/audio.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/audio.py) |
| 非中文 token 一律落到 **单个 `'bad'` 音节类**（`lazy_pinyin(..., errors=handle_error)`，只有"恰好 1 个拼音"才入表）⇒ **英文/中英混合在设计上不被支持** | [get_pronunce_table.py](https://github.com/navi0105/LyricAlignment/blob/main/get_pronunce_table.py) |
| 论文数字：MAE 0.0228（Opencpop test）/ **0.0709**（MIR-1k mix）；Whisper 解码器 0.0193 / 0.8220；Spleeter 0.1231、混音 0.0980；CER 17.8%（vs 未微调 18.1%）；PCAS-exact 77.26%；训练 = Opencpop 5.2 h + MUSDB18 加噪（SNR 0/-5/-10）；标注真值 = MIR-1k **17 首 / 19 分 33 秒** | [论文全文](https://ar5iv.labs.arxiv.org/html/2311.12488)、[README](https://github.com/navi0105/LyricAlignment/blob/main/README.md) |
| 论文自承认：**可能的数据污染 →"会高估结果"**；中文对 Whisper 不是低资源（23,446 h） | 论文 §3.5 |
| 权重：Zenodo **CC-BY-4.0**、open、`checkpoints.zip` = **5,825,687,709 B**、md5 `577f06b3…`、下载 159 / 独立 88 / 浏览 198；含 `alignment/` 与 `transcription/` | [Zenodo API](https://zenodo.org/api/records/10373790) |
| **MUSDB18 = `other-nc`**，原文"should not be used for any commercial purpose without the express permission of the copyright holders"，内部含 **BY-NC-SA 4.0 / 3.0** 曲目 | [MUSDB18 Zenodo API](https://zenodo.org/api/records/1117372) |
| `requirements.txt` 里 **`opencc-python-reimplement` 在 PyPI 404**；正确名 `opencc-python-reimplemented`（0.1.7 存在） | [PyPI 404](https://pypi.org/pypi/opencc-python-reimplement/json)、[PyPI 正确包](https://pypi.org/pypi/opencc-python-reimplemented/json) |
| Whisper medium：**763,857,920** 参数（F32）、HF 模型卡 **apache-2.0** | [hf-mirror API](https://hf-mirror.com/api/models/openai/whisper-medium) |
| 本项目侧：`asr.py` 默认 **large-v3**；[matcher.py](matcher.py) 是"**中文按字、英文按词**"的 NW DP + 拼音相似度 + 行窗 + 插值；分声部基线 **85.0%**（剔除存疑歌 89.9%）；安装包 1.82 GB → 裁剪 0.68 GB、模型另下 ~3 GB、PyTorch 452 MiB、onnxruntime 38 MiB 已在包内；[requirements.txt](requirements.txt) 已装/可选依赖清单 | [asr.py](asr.py)、[matcher.py](matcher.py)、[声纹评估协议.md](docs/声纹评估协议.md)、[体积报告](docs/商业化重构审计与体积报告.md)、[requirements.txt](requirements.txt) |
| 本机实测：Python 3.14.6；**torch 2.13.0+cpu（CUDA 不可用）**；GPU **RTX 4060 Laptop 8188 MiB**；`~/.cache/whisper` 与 HF 缓存**均不存在**；已装 openai-whisper 20250625 / tiktoken 0.13.0 / numba 0.67.0 / llvmlite(116.2 MB)（安装时间均 2026-08-14）；**未装** transformers / librosa / soundfile / pandas / jiwer / opencc / chinese_converter / safetensors / pypinyin / demucs；**huggingface.co 直连失败**（hf-mirror 通） | 本机探针（本次执行） |

### 8.2 **未验证 / 查不到**（**这些不能当成"没问题"**）

1. **Opencpop 官方许可原文**：官方页 <https://wenet.org.cn/opencpop/liscense/> 本次**不可达**（HTTPS 与 GitHub Pages 均失败；http 版被跨域重定向拦截）⇒ 现有依据只有两处第三方文字。**以官方原文为准之前，这条算未证实。**
2. **它的显存需求**：官方从未给，**未查到、未实测**（§5.4 里所有数字都是推算）。
3. **checkpoint 解压后的单文件大小与目录结构**：**未下载 5.4 GiB，未验证**；"对齐单个约 2.9–3.1 GB"是推断。
4. **`alignment/` 权重对应论文哪一行设置（是否就是 0.0709）**：Zenodo 未说明，**推断**。
5. **中文现代商业流行歌上的真实精度**：**没有任何客观证据**——无第三方复现、无对比评测；本次检索只命中仓库自身、论文索引页、以及一个**同名但无关**的 PyPI 包 [`lyric-align`](https://pypi.org/project/lyric-align/)（未细读，不构成证据）。论文的 0.0709 s 不能外推到"带和声/对唱/重混音的现代中文歌"。
6. **与我们的数字是否可比**：**不可比**（§3.3）；谁更好只能靠 §6 的实验。**本文没有做该实验。**
7. **一次 `pip install` 能不能在 Python 3.14 上装通**（改名之后）：**未实测**；只验证了错误包名 404 与正确包名存在。
8. **打包（PyInstaller）后安装包/启动时间会涨多少**：**未构建验证**（只给了本机 site-packages 的实测体积作为量级参考）。
9. **ONNX/CTranslate2 化的可行性**：**未验证**（结构上可行是推测；且不改变许可结论）。
10. **它的识别权重 vs 我们 large-v3 的 CER**：**未测**；我们这边也**没有 CER 指标**（只有字级匹配率）。
11. **它对拖音（句尾长音）的表现**：论文的 MAE 含 offset，但**没有单独的 offset/拖音分析**；是否比我们 `tail_extend.sustain_end` 更准，**未验证**。
12. **对唱/合唱场景**：MIR-1k 是独唱歌手的复音录音，**论文里没有任何对唱/合唱实验**；"多人合唱下会怎样"属于**推断**（模型结构上只有一条单调序列、无 speaker 概念）。
13. **bert-base-chinese 的许可**、**在被 HF 墙掉的环境里 tokenizer 怎么预置**：未核实/未验证。
14. **被引/第三方采用情况**：本次未取到可靠引用数（Semantic Scholar/OpenAlex 未查），**不作为本文论据**。

### 8.3 本次做过的操作（可复核）与自我约束

- 通过 `api.github.com` 读：仓库元数据、**完整文件树**（`truncated: false`）、issues 与 issue #3 的全部评论。
- 通过 `ghproxy.net` 代理读 raw（本机直连 `raw.githubusercontent.com` 与 GitHub HTML 页失败）：**README 全文**、`requirements.txt`、`module/align_model.py`、`inference_alignment_nogt.py`、`utils/alignment.py`、`utils/audio.py`、`data_processor/record.py`、`get_pronunce_table.py`，以及 Opencpop 官方仓库 README、VoxKitchen 数据集目录、第三方 ToU 镜像。
- 读 [arXiv 全文（ar5iv）](https://ar5iv.labs.arxiv.org/html/2311.12488)；查 Zenodo（权重 + MUSDB18）、PyPI JSON、hf-mirror API。
- 本机**只读**探针：`nvidia-smi`、torch/CUDA 状态、site-packages 目录体积与 `*.dist-info` 安装时间、模型缓存是否存在。
- **没有**克隆它的仓库到本项目、**没有**下载任何权重、**没有**运行它的推理、**没有**修改本项目任何代码或数据。
- 本次唯一写入的文件：**本文件**（`docs/LyricAlignment评估.md`）。
