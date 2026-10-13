# LyricAlignment 集成评估（第三方项目 · 事实核查版）

> 评估对象：<https://github.com/navi0105/LyricAlignment>
> 评估日期：2026-10-13
> 评估人：技术评估（只做调研，**未修改本项目任何代码**；本文件是本次唯一写入）
> 评估方式：读 GitHub API 原文（源码逐文件读）、arXiv 论文全文、Zenodo 元数据、PyPI/GitHub API 元数据、本机 import 探针。**没有下载权重，没有运行它的推理，没有做任何 A/B 对比** —— 本文所有"它侧"的数字都来自论文 / README / 作者回复，属于**引证**而非**实测**。

---

## 1. 一句话结论

**只能作为可选能力，且默认关闭、权重不进发行版：它确实提供了我们现有 DP 链路没有的东西——不依赖 ASR 文本假设的"帧级声学强制对齐"（20 ms 分辨率），可以当作第三个对齐后端做实验；但它给不了识别（比我们的 large-v3 弱，作者自己的 issue 里也承认换大模型对齐反而更差）、给不了说话人信息（无 agent 概念，对唱/和声用不上），代码**没有任何许可证**、权重又派生自非商业数据（MUSDB18 = `other-nc`），所以**不能打包进公开发行版、也不能复制它的代码**，只有先在自建基准上跑赢现有 DP 才值得继续投入。**

---

## 2. 它到底是什么

### 2.1 身份

| 项 | 事实 | 来源 |
| --- | --- | --- |
| 论文 | Jun-You Wang, Chon-In Leong, Yu-Chen Lin, Li Su, Jyh-Shing Roger Jang, *Adapting pretrained speech model for Mandarin lyrics transcription and alignment*, **ASRU 2023** | [arXiv:2311.12488](https://ar5iv.labs.arxiv.org/html/2311.12488)、[IEEE 10389800](https://ieeexplore.ieee.org/document/10389800) |
| 仓库自述 | "Source code of paper ..." —— **论文的复现代码**，不是产品、不是库 | [repo](https://github.com/navi0105/LyricAlignment) |
| 规模 | 19 star / 1 fork / 569 KB 源码 / 62 commits | [API](https://api.github.com/repos/navi0105/LyricAlignment) |
| 活跃度 | 2023-05-04 建库 → **最后 push 2023-12-14**；此后只有 2024 年的 issue 回复。作者 2024-08-09 明确表示"即将毕业，没有计划发布其他 checkpoint" | [issues/3](https://github.com/navi0105/LyricAlignment/issues/3) |
| 学术影响 | OpenAlex 记录被引 **1** 次（MIR-MLPop, 2024） | [OpenAlex W4391021537](https://api.openalex.org/works?search=Adapting%20pretrained%20speech%20model%20for%20Mandarin%20lyrics) |
| Issue 总量 | **3 条，全部已关闭**（其中 2 条是自己的合并 PR） | [issues?state=all](https://api.github.com/repos/navi0105/LyricAlignment/issues?state=all&per_page=100) |

### 2.2 模型是什么（不是 FunASR / Paraformer，是"Whisper medium + 自研 GRU"）

- **主干**：Whisper **medium**（论文 §2.2；`WHISPER_DIM['medium'] = 1024`）。论文原话：选 medium 是因为"我们的显存不大"。
- **对齐头（自研）**：`AlignModel` = Whisper encoder → **2 层双向 GRU**（hidden 384，dropout 0.15，Mish）→ Linear → **401 个音节类 + 1 个静音类**。whisper 的 decoder **完全不参与对齐**。
- **训练目标**：帧级音节类预测，CTC loss + CE loss 相加；loss 是"声调无关的拼音音节类"（pypinyin 转换，401 类）而不是汉字，论文说这是为了降低小数据下的分类数。
- **推理**：`perform_viterbi*()` —— 对**整段音频的逐帧后验**做 Viterbi 强制对齐；每帧 **20 ms**（`hop_size_second=0.02`）。
- **预处理**：HT Demucs 提人声（论文 §2.4；消融：HT Demucs 0.0709 / Spleeter 0.1231 / 直接混音 0.0980 MAE）。
- **训练数据**：Opencpop 5.2 h（**一位女专业歌手、单声道清唱**）+ 用 MUSDB18 伴奏做的三个加噪版本（SNR 0 / −5 / −10）。测试：Opencpop test + MIR-1k（2.2 h、19 位业余歌手、复音）。
- **自建测试集**：MIR-1k 人工字级标注子集，**17 首 / 19 分 33 秒**（论文 §2.5；标注文件在仓库 `dataset_preprocessing/`）。

来源：[module/align_model.py](https://github.com/navi0105/LyricAlignment/blob/main/module/align_model.py)、[utils/alignment.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/alignment.py)、[论文全文](https://ar5iv.labs.arxiv.org/html/2311.12488)。

### 2.3 论文报的数字（全部是论文/README 自报，未见第三方复现）

| 指标 | Opencpop test | MIR-1k mix（复音，论文主战场） |
| --- | --- | --- |
| 对齐 MAE（字边界） | 0.0228 s | **0.0709 s** |
| 识别 CER / PER | 9.3% / 2.0% | **17.8% / 9.6%** |
| 识别（未微调 Whisper 基线） | 10.7% / 2.8% | 18.1% / 10.0% |
| 时间对齐转写 PCAS-exact | 86.58% | 77.26% |

仓库 README 补充：MIR-1k **vocal**（用真值人声轨，等于"oracle 分离"）CER 9.1% / PER 3.6%。

**两个必须一起读的自承认局限**（论文 §3.5）：

1. **可能存在预训练数据污染**：模型在 MIR-1k 上出现过"歌词家/作曲：李宗盛"的幻觉，而测试歌并非他所作 → 作者明确写"MIR-1k 里有些歌可能本来就在 Whisper 的预训练数据里……**这会让结果被高估**"。
2. 中文（普通话）对 Whisper 并不是低资源语言（23,446 小时中文语音），所以"低资源也能做"的结论**不能外推到别的语言**。

### 2.4 推理输入输出（读源码得到，非推测）

- 入口：`inference_alignment.py`（需要 `on_offset` 真值才能算 MAE）、`inference_alignment_nogt.py`（**无真值，就是我们要的用法**）、`inference_transcript.py`。
- **输入**：一个 JSON 文件，形如 `[{"song_path": "<绝对路径>", "lyric": "<已知歌词字符串>", "on_offset": [[on,off],…]（可选）}]`（[data_processor/record.py](https://github.com/navi0105/LyricAlignment/blob/main/data_processor/record.py)）；音频用 `librosa.load(sr=16000, mono=True)` 读，**每首歌一段、整段做一次 Viterbi**。
- **输出**：`print()` 到 stdout 的 Python 列表 `[[onset, offset, char], …]` —— **没有库 API、没有写文件、没有 JSON 输出、没有逐字置信度**。
- **运行前提**：`--model-dir` 下要有 `args.json` / `model_args.json` / `best_align_model.pt`；`bert_base_chinese_pronunce_table.json` 是**用相对路径从当前工作目录读**的（必须 cd 到仓库根再跑）；`tokenizer` 只用 `bert-base-chinese` 的**词表**（不需要 BERT 权重）。
- 长音频：`frame_manual_forward()` 按 Whisper 的 30 s 窗口（N_FRAMES）切块、每块**重新从头做位置编码**，再拼接 embedding；Viterbi 覆盖整段。

### 2.5 依赖（requirements.txt 原文）

`tqdm, numpy, pandas, librosa, torch>=1.13.1, openai-whisper, transformers, jiwer, demucs, soundfile, numba, pypinyin, opencc-python-reimplement, chinese_converter`

⚠️ **实测：按原样 `pip install -r requirements.txt` 会在 `opencc-python-reimplement` 这行失败** —— PyPI 上该名字 **404**，正确的包名是 `opencc-python-reimplemented`（v0.1.7，已核实存在）。这是"论文代码"而非"可安装依赖"的直接证据。[requirements.txt](https://github.com/navi0105/LyricAlignment/blob/main/requirements.txt)｜[PyPI 404 对照](https://pypi.org/pypi/opencc-python-reimplement/json)｜[正确包名](https://pypi.org/pypi/opencc-python-reimplemented/json)

---

## 3. 与现有后端的功能对比（识别 / 对齐分开写）

先把两件事分清（论文 §1 的定义也一样）：

- **识别（ALT / ASR）**：给音频，产出**歌词文本**。
- **对齐（Alignment）**：给音频 **+ 已知歌词**，产出**时间轴**。

我们这条流水线的目标任务是后者（`preprocess → asr → matcher → tail_extend → ttml_writer`）。**LyricAlignment 对我们有价值的部分是"对齐"，不是"识别"。**

### 3.1 识别（ASR）维度

| 项 | 本仓库（现状） | LyricAlignment | 判断 |
| --- | --- | --- | --- |
| 模型 | **faster-whisper large-v3**（CTranslate2，词级时间戳） | Whisper **medium**（769 M）+ 5.2 h 清唱微调 | large-v3 参数量/能力高一个档 |
| 中文歌词 CER（各自论文口径） | 未查到同口径数字 | 17.8%（MIR-1k mix）；相对**未微调 Whisper medium 只赢 0.3 个点**（18.1%→17.8%） | **提升幅度是"边际"级** |
| 作者对换大模型的表态 | — | 试过 large-v2 decoder（冻结 encoder）：**转写更好，但对齐更差**；且**不再发布其他 checkpoint** | 别指望它做识别升级 |
| 能不能替代我们的 ASR | — | 能跑，但没有任何证据比 large-v3 好 | **不替代** |

来源：[论文表 3](https://ar5iv.labs.arxiv.org/html/2311.12488)、[issues/3](https://github.com/navi0105/LyricAlignment/issues/3)、本项目 [asr.py](asr.py)。

### 3.2 对齐（Alignment）维度 —— 这里才是真问题

| 项 | 本仓库 `matcher.py`（DP） | LyricAlignment | 差异是否实质 |
| --- | --- | --- | --- |
| DP 跑在什么上 | **ASR 输出的离散词级时间戳** → 文本序列上的 Needleman–Wunsch（仿射罚分） | **逐帧声学后验**（401 音节类 + 静音）→ HMM/CTC 式 Viterbi | **是**（"文本级 DP" vs "帧级声学 Viterbi"） |
| 分辨率 | 受 ASR 词边界限制，字时间由词内均分 + 插值得到 | 帧级 **20 ms** | 是 |
| 是否需要先识别 | **需要**（ASR 错了/漏了/重复了，对齐要跟着擦屁股） | **不需要**（已知歌词直接进 Viterbi） | 是 |
| 对"歌词有错字/漏行" | 有显式 unmatched/插值标记，编辑台标"插值/低可信" | 无置信度输出，**会强行摊平误差且不告诉你** | 我们更强 |
| 说话人 | `diarize.py`（CAM++ 声纹）+ `x-bg` 和声层 + 对唱 `ttm:agent` | **完全没有概念**：一条音节序列对一段音频 | 我们更强 |
| 行结构 / 后处理 | LRC 行窗、`_line_span`、`clamp_units_into_line`、`tail_extend`（F0/能量）、`ttml_normalize` R1–R7 | 无（只有整段一条序列） | 我们更强 |
| 时间来源 | "所有时间都来自真实音频"（ASR 时间戳） | 声学模型后验 | 都算来自音频 |
| 依赖 | faster-whisper（MIT）+ 可选 pypinyin | 额外 5.8 GB 权重 + 4 个未装包 + 无许可证的代码 | 我们更轻 |
| 训练域 | 通用语音/多语种（large-v3） | **一位女歌手 5.2 h 清唱** + MUSDB18 加噪 | 它的域更窄 |

本项目侧依据：[matcher.py](matcher.py) 模块头（"DP 把歌词单元与 ASR 词级时间戳单调对齐；未匹配上的字用相邻已匹配字插值"）、[asr.py](asr.py)、[AGENTS.md](AGENTS.md) §1/§4。

### 3.3 结论：它"能做而我们做不到"的到底是什么

**只有一件事**：**不经过 ASR 文本假设、直接拿声学后验做帧级强制对齐**。

这在下面这些场景理论上比我们强（都是**推断**，未经实测）：

- ASR 成段漏字/重复/幻觉时（我们的 DP 必须删/插，误差会传导）；
- 需要比 ASR 词边界更细的字起点（它天然 20 ms 一帧）；
- 唱腔导致 ASR 词边界系统性偏移时。

**但同一件事也带来我们已有的能力全部失效**：无逐字置信度、无 agent/和声、无行约束、无拖音判据。也就是说：**它顶多替换 `matcher` 这一个环节，前后所有东西（分声部、和声合并、拖音、TTML 规范化）一样都不能少。**

---

## 4. 许可与再分发风险（能否打包进公开发行版）

### 4.1 三份许可，三份都不一样

| 对象 | 许可 | 能否随我们的公开发行版分发 | 来源 |
| --- | --- | --- | --- |
| **源码** | **无任何许可证**（GitHub API `license: null`；递归文件树里**没有 LICENSE / COPYING**；README 里也没有许可声明） | ❌ **不能**。无许可 = 默认"保留所有权利"，复制其 Viterbi/加载器代码进我们的仓库是侵权风险 | [API license 字段](https://api.github.com/repos/navi0105/LyricAlignment)、[完整文件树](https://api.github.com/repos/navi0105/LyricAlignment/git/trees/main?recursive=1) |
| **模型权重**（Zenodo） | 元数据标 **CC-BY-4.0**（access=open，`checkpoints.zip`，5,825,687,709 B，159 次下载 / 88 独立） | ⚠️ **元数据上"可再分发（需署名）"，但与训练数据来源冲突（见下）** | [Zenodo 记录](https://zenodo.org/records/10373790)、[API](https://zenodo.org/api/records/10373790) |
| **训练数据来源** | MUSDB18：Zenodo 官方元数据 **`other-nc`（非商业）**；Opencpop：官方规则页本次**无法访问**，第三方镜像转述官方链接称"**Commercial usage is prohibited**"且"规则同样约束**由此产生的模型**" | ❌ **这是不能打包的真正原因**：论文主结果（Proposed）用的就是 **MUSDB18 加噪**版本训练的权重，而 Opencpop 的规则条文按镜像转述还**明确延伸到 resulting models** | [MUSDB18 Zenodo](https://zenodo.org/records/1117372)、[Opencpop ToU 镜像](https://github.com/lottev1991/opencpop-cjke-multidict/blob/master/ToU_EN.md)（引官方页 <https://wenet.org.cn/opencpop/liscense/>，本次不可达） |
| MIR-1k（测试集 + 他们的标注） | Zenodo 记录 **access = restricted**；标注文件放在无许可的仓库里 | ❌ 不需要用，也别用 | [MIR-1K Zenodo](https://zenodo.org/records/3532216) |
| 上游 Whisper 权重 | **许可标注不一致**：GitHub 仓库 `MIT`，HF 模型卡 `apache-2.0`（两者都不是 NC，实际影响小，但说明"权重许可要看模型卡而不是看代码仓库"） | ✅ | [openai/whisper](https://api.github.com/repos/openai/whisper)、[HF 模型卡（经 hf-mirror）](https://hf-mirror.com/api/models/openai/whisper-medium) |
| bert-base-chinese（只用 tokenizer 词表） | apache-2.0 | ✅ | [hf-mirror API](https://hf-mirror.com/api/models/bert-base-chinese) |

### 4.2 直说结论

1. **打包权重进公开发行版：不做。** 理由不是"Zenodo 写了 CC-BY-4.0 就没事"，而是**上游数据是非商业的**（MUSDB18 `other-nc` 是 Zenodo 官方元数据、可直接核实），而发布者把派生权重标成 CC-BY-4.0 —— **元数据与来源冲突，冲突的解决方式轮不到我们猜**。真要打包，必须先拿到 Opencpop 团队 + MUSDB18 权利方的书面澄清，成本远大于收益。
2. **复制它的代码：不做。** 没有许可证就是没有授权。我们要用就只能**自写**加载器与帧级 Viterbi（算法本身不受版权保护，但它的实现文件受）。
3. **"只是让用户自己装、自己下"这条路是可行且推荐的**：我们不分发任何它的东西，用户在本机装它的仓库 + 从 Zenodo 下权重，我们只在**用户的机器上调用**它。这样许可风险回到用户自己身上，也符合我们"离线、不要 API Key"的立场（它全程本地，无 API Key ✅）。
4. **顺带提醒**：本项目现有的第三方依赖是干净的（faster-whisper MIT、Whisper 权重 MIT/Apache、pypinyin MIT、demucs MIT、CAM++ 权重已在 [requirements.txt](requirements.txt) 里单独注明"不随仓库分发"）。引入一个"代码无许可 + 权重 NC 派生"的组件，会**打破现有这套已经说清楚了的许可叙事**。

---

## 5. 硬件 / 体积代价

| 项 | 数字 | 性质 | 来源 |
| --- | --- | --- | --- |
| `checkpoints.zip` | **5,825,687,709 B ≈ 5.43 GiB**（含 `alignment/` 与 `transcription/` 两个目录） | **已核实**（Zenodo 元数据） | [Zenodo API](https://zenodo.org/api/records/10373790) |
| 单个对齐 checkpoint | **GB 级（推断约 2.9–3.1 GB）** | **推断**：训练脚本里"半精度保存"那行**被注释掉了**（`#model = copy.deepcopy(model).half()`），实际 `torch.save(model.state_dict())` 会把 **Whisper 主干一起存**；Whisper medium fp32 = 2.85 GiB。**解压后单文件大小未验证**（没下 5.8 GB） | [train_multitask.py](https://github.com/navi0105/LyricAlignment/blob/main/train_multitask.py) |
| Whisper medium 权重 | 763,857,920 参数；`model.safetensors` **3,055,544,304 B ≈ 2.85 GiB**（fp32） | 已核实 | [hf-mirror API](https://hf-mirror.com/api/models/openai/whisper-medium) |
| **显存需求** | **官方/仓库完全没有给（未查到）**。可核实的下界参考：Whisper medium 官方 README 写 **Required VRAM ~5 GB**；align RNN（2 层 GRU hidden 384 + Linear）相对很小；但代码会把整段 mel/embedding 留在显存里，长音频线性增长 | **未实测** | [openai/whisper README 模型表](https://github.com/openai/whisper) |
| 训练侧磁盘 | README 写 checkpoint 需 **≥10 GB 磁盘**（是磁盘，不是显存，别混） | 已核实 | [README](https://github.com/navi0105/LyricAlignment/blob/main/README.md) |
| 额外 pip（wheel 下载体积） | transformers 12.0 MB、soundfile 1.0 MB、pypinyin 0.8 MB、librosa 0.4 MB（sdist，还会拉 scipy/soxr 等）、openai-whisper 0.8 MB、demucs 1.2 MB | 已核实（PyPI JSON `urls[].size`） | [PyPI](https://pypi.org/pypi/transformers/json) 等 |
| 只需 BERT 的**词表** | 不需要 BERT 权重（`AutoTokenizer` 只下 tokenizer 文件，~百 KB 级；模型里没有 BERT 模块） | 读码结论 | [align_model.py](https://github.com/navi0105/LyricAlignment/blob/main/module/align_model.py) |
| Python 3.14 兼容性 | **numba 不是问题**：PyPI 最新 0.68.0 的 classifier 含 3.14/3.15；本机装的 0.67.0 在 3.14.6 上 `import numba` 成功。`openai-whisper`（20250625）本机 **import 成功**（其 PyPI classifier 只写到 3.13）。`transformers` 5.19.0 支持 3.14 | 已核实（本机 import 探针 + PyPI 元数据） | [PyPI numba](https://pypi.org/pypi/numba/json)、本机 `python -c "import numba, whisper"` |
| 本机现状（本次评估的解释器） | `python 3.14.6`；**已装**：torch **2.13.0+cpu**、openai-whisper 20250625、numba 0.67.0、huggingface_hub、tokenizers、ctranslate2 4.8.1、onnxruntime、av 18.1.0；**未装**：transformers / librosa / soundfile / demucs / pypinyin | 已核实（import 探针） | 本机实测 |

⚠️ **两点必须说清**：

1. 本机这个解释器的 **torch 是 CPU 版**（`2.13.0+cpu`）—— 就算把 5.8 GB 权重拉下来，在这个 shell 里也只能 CPU 推理，**不能用来做有意义的性能/显存实测**（项目本身的 GPU-only 约束在另一个环境里满足）。
2. **显存数字我没有查到，也没有测到。** 不要拿"Whisper medium ~5 GB"当"LyricAlignment 要 5 GB"用 —— 那只是主干的下界参考。

---

## 6. 结论与建议

### 6.1 结论：**只能作为可选能力（实验后端），默认关闭、不打包权重、不复制代码**

三条理由，逐条对应上面的证据：

1. **对"识别"没有任何价值**：相对未微调的 Whisper medium 只赢 0.3 个 CER 点，而我们的基线是 large-v3；作者本人也说了换大模型对齐更差、不再发布 checkpoint。
2. **对"对齐"有机制上的新东西，但没有"中文流行歌上更好"的证据**：
   * 它自报的最强数字来自 **MIR-1k mix**：17 首、**19 分 33 秒**、19 位业余歌手的**老录音**；
   * 训练集是 **一位女歌手 5.2 h 清唱**（Opencpop）；
   * 论文自己承认**可能存在 Whisper 预训练污染 → 结果可能被高估**；
   * **第三方复现：未找到**（0 条相关 issue、无 HF 镜像、Zenodo 159 次下载、OpenAlex 被引 1 次）。
   * 我们的目标是现代商业中文流行歌（带和声/对唱/重混音），**这一段完全没有证据**。
3. **许可不可打包**：代码无许可 + 权重派生自 `other-nc` 数据（+ Opencpop 规则按镜像转述约束 resulting models）。公开发行版**不能**带。

⇒ 因此：**不是"值得集成"，也不是"完全不建议"**，而是 **"值得用一个便宜的实验去证伪"**。如果实验证明它在我们自己的测试集上明显更好，再谈接入（那时仍然只能"用户自备"）；否则就停在文档里。

### 6.2 先做这个一次性实验（成本 ~半天到 1 天，不写进主链路）

1. **环境**：单独一个 venv（**不要污染本仓库环境**），装 `openai-whisper numba librosa soundfile pypinyin transformers`（注意 `opencc-python-reimplement` 是错的包名），拉它仓库到**项目目录之外**，从 Zenodo 下 `checkpoints.zip`（5.8 GB），只解压 `alignment/`。
2. **对照数据**：用我们已有的尺子 —— [tools/eval_word_timing.py](tools/eval_word_timing.py)（QQ `.qrc`/网易 `.yrc` 逐字真值）+ AMLL DB 人工标注 TTML。**必须先"验尺"**：只有 QRC 与音频**同一录音**（艺人署名一致）才可比，且要**去掉整体平移**再看散布（AGENTS.md 里"让风告诉你 中位 −0.228 s 其实是整体偏移"就是这么发现的）。
3. **最少 3 首**（1 首单声部、1 首带和声、1 首对唱），跑它的 `inference_alignment_nogt.py`（JSON 里塞 `song_path` + `lyric`，人声轨用我们 `separate.py` 的 HT Demucs 输出 —— 它的消融证明 Demucs 人声轨最好，且我们本来就有这一环）。
4. **判据（先写死，别事后挪门槛）**：逐字起点 MAE / 中位绝对偏差，与现有 DP 产物对比；**只比同一个人声轨、同一套行边界**。只有"多数歌上明显更低"才算赢；打平或互有胜负 → **不接入**。
5. 若要看它是否解决"ASR 漏字/幻觉"：专门挑 1 首我们已知 ASR 出错的歌做 A/B。

### 6.3 如果实验证明有效：适配层设计（可插拔、不破坏现有流程）

**原则：它只替换 `matcher` 这一个环节，前后全部不动。**

```
preprocess → asr ──┐
                   ├─→ [对齐后端分派] → tail_extend → ttml_writer
                   │      ├─ "dp"（默认，现有 matcher.py）
                   │      └─ "lyricalign"（可选，外部能力）
                   └─ diarize（声纹/agent 完全不受影响）
```

1. **新模块 `align_lyricalign.py`（放项目根，与 `matcher.py` 同级）**
   * 不 import Qt、不碰 HTTP（和 `desktop/services/*` 同一纪律），可在 `selftest.py` 里被 import 而不触发模型加载。
   * 接口与 `matcher` 对齐，**返回值就是现有的 `AlignedLine` 列表**，这样 `_line_span` / `clamp_units_into_line` / `tail_extend` / `ttml_normalize` / `ttml_writer` **一行都不用改**：
     ```python
     class LyricAlignBackend:
         name = "lyricalign"
         @staticmethod
         def is_available() -> tuple[bool, str]:   # (可用?, 原因文案)
             ...  # 权重目录/依赖缺失 → (False, "未安装 LyricAlignment 权重（可选后端）")
         def align(self, audio_path: str, lines: list[LyricLine], *,
                   vocals_path: str | None = None, device: str = "cuda") -> list[AlignedLine]: ...
     ```
2. **两种调用方式，**先 A 后 B**：**
   * **A（推荐起步，零许可风险）**：`subprocess` 调用**用户自己装的** `inference_alignment_nogt.py`，把 JSON 写到 `outputs/.tmp/`，用 `ast.literal_eval` 解析它 stdout 的 list。缺点要认：每首歌一次前向、stdout 解析脆、必须 `cwd=它的仓库根`（拼音表是相对路径读的）、要它自己的 venv。
   * **B（只在许可澄清、且 A 证明有效后）**：自写加载器（`whisper.load_model("medium")` + 自建 GRU/Linear 模块对齐 state_dict 键名）+ 自写帧级 Viterbi（纯算法，可复用我们 DP 的代码风格）。**绝不复制它的源码文件。**
3. **按行窗口调用，不要整首一次**：复用我们已有的 LRC 行窗（`matcher` 也是按行窗对齐），窗口两端各留 padding，让它的 blank/silence 类吃掉边缘。理由：a) 省显存（长音频的 mel/embedding 是线性增长的）；b) 它的 Viterbi 是**整段全局**的，整首喂进去会把误差摊到全曲；c) 我们本来就需要行级归属。
4. **必须显式退化的情况**（写进代码注释和文档）：
   * **对唱/多 agent**：它没有说话人概念 ⇒ `singer_mode != off` 或存在多 agent 时**直接回退 DP 并打印原因**；
   * **和声 `x-bg`**：同理，`x-bg` 层时间仍走现有链路；
   * **歌词与音频不匹配**（缺行/错字）：它不会告诉你，我们的 DP 至少会标"插值/低可信" ⇒ 编辑台仍以现有标记为准，**不要**因为换了后端就把置信度标记去掉（`ttml_model` 的 `matched=False` 语义要保留，可让它一律为"未验证"）。
5. **开关与联调（照 AGENTS.md §4.5 的规矩来，否则会踩已知的坑）**：
   * `ConvertOptions` 加 `align_backend: str = "dp"`；CLI `--align-backend dp|lyricalign`；
   * **`desktop/services/convert_params.build_options()` 必须同步加这个字段**，否则 `desktop/selftest.py` 第 8 组的 `field_drift()` 会报错（这是设计如此，漏一个入口就是"界面选了不生效"）；
   * `tools/audit_params.py` / `tools/test_param_effect.py` 会把它当成"纯 UI 参数/死字段"来查 ⇒ 要有真实分支消费它；
   * **`selftest.py` 在权重缺失时必须"跳过"而不是失败**（离线自检不能依赖 5.8 GB 权重）；`--check-only` / `tools/verify_amll_parse.py` 照旧强制跑。
6. **权重管理**：**不进安装包**。如果将来做"一键下载"，唯一合法的登记处是 [desktop/resources/model_manifest.json](desktop/resources/model_manifest.json)（来源/缓存位置/校验/许可都写那儿），并且必须把"许可：Zenodo 标 CC-BY-4.0，但训练数据含 MUSDB18（非商业）/Opencpop，商用需自行澄清"**写在清单里**；`doctor`/`envcheck` 只报"未安装（可选）"，**不许自动下、不许静默回退**（静默回退会让人以为在用新后端）。
7. **验收**：接入后必须重跑 `python selftest.py`、`python main.py --check-only <产物> --strict-time`、`python tools/verify_amll_parse.py`（AGENTS.md §0 的三条纪律），并至少对 1 首真实产物做 `normalize_timeline(...).fixes == []` 的回归。

### 6.4 顺带值得看一眼的替代品（未验证，仅登记线索）

- **[jhuang448/LyricsAlignment-MTL](https://github.com/jhuang448/LyricsAlignment-MTL)**（**MIT**，67★，ICASSP 2022，英语/DALI）与它的多语言版 **[LyricsAlignment-Multilingual](https://github.com/jhuang448/LyricsAlignment-Multilingual)**（**MIT**，ISMIR 2025 LBD，8★，2025-10 仍在更新）。
  **许可干净是本条唯一的价值**；**是否支持中文、是否发布权重、对中文流行歌效果如何：均未验证**（本次未读其训练语言清单与权重发布情况）。如果哪天真的要认真做"声学强制对齐后端"，从这条线开始比从无许可仓库开始更省事。

---

## 7. 证据边界（哪些查到了、哪些没查到/未验证）

### 7.1 已核实（有可点开的来源）

| 结论 | 来源 |
| --- | --- |
| 仓库 **无许可证**（API `license: null`；文件树无 LICENSE/COPYING；README 无许可章节） | [API](https://api.github.com/repos/navi0105/LyricAlignment)、[tree](https://api.github.com/repos/navi0105/LyricAlignment/git/trees/main?recursive=1)、[README](https://github.com/navi0105/LyricAlignment/blob/main/README.md) |
| 权重 Zenodo 元数据 = **CC-BY-4.0**、`checkpoints.zip` = **5,825,687,709 B**、159 下载 / 88 独立 / 198 浏览 | [Zenodo API](https://zenodo.org/api/records/10373790) |
| **MUSDB18 = `other-nc`（非商业）** | [MUSDB18 Zenodo API](https://zenodo.org/api/records/1117372) |
| **MIR-1K Zenodo 记录 = `restricted`** | [MIR-1K Zenodo API](https://zenodo.org/api/records/3532216) |
| 主干 = Whisper **medium**；对齐头 = 2 层双向 GRU(384) + Mish + Linear；401 音节类 + 静音；CTC+CE；推理 Viterbi、hop **20 ms** | [align_model.py](https://github.com/navi0105/LyricAlignment/blob/main/module/align_model.py)、[utils/alignment.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/alignment.py)、[论文](https://ar5iv.labs.arxiv.org/html/2311.12488) |
| 论文数字：MAE 0.0709（MIR-1k mix）/ 0.0228（Opencpop test）；CER 17.8% vs 基线 18.1%；PCAS-exact 77.26%；测试集 17 首 / 19 分 33 秒；训练 5.2 h Opencpop + MUSDB18 加噪 | [论文 §3](https://ar5iv.labs.arxiv.org/html/2311.12488)、[README](https://github.com/navi0105/LyricAlignment/blob/main/README.md) |
| 论文自承认**可能的数据污染**（"可能高估"）+ 中文对 Whisper 非低资源 | 论文 §3.5 |
| 作者原话：large-v2（冻结 encoder）**转写更好、对齐更差**；**不再发布其他 checkpoint** | [issues/3](https://github.com/navi0105/LyricAlignment/issues/3) |
| 仓库活动：62 commits，2023-05-04 → **2023-12-14**；3 条 issue 全关闭 | [API](https://api.github.com/repos/navi0105/LyricAlignment)、[issues](https://api.github.com/repos/navi0105/LyricAlignment/issues?state=all) |
| 推理 I/O：JSON(`song_path`+`lyric`) → **stdout print** 的 `[[onset,offset,char]]`；无库 API、无文件输出、**无逐字置信度**；拼音表按**相对路径**读；`is_mixture` 是 MIR-1k 专用声道语义 | [inference_alignment_nogt.py](https://github.com/navi0105/LyricAlignment/blob/main/inference_alignment_nogt.py)、[utils/audio.py](https://github.com/navi0105/LyricAlignment/blob/main/utils/audio.py)、[record.py](https://github.com/navi0105/LyricAlignment/blob/main/data_processor/record.py)、[dataset.py](https://github.com/navi0105/LyricAlignment/blob/main/dataset.py) |
| 保存的是**含主干的 fp32 全量 state_dict**（半精度那行被注释掉） | [train_multitask.py](https://github.com/navi0105/LyricAlignment/blob/main/train_multitask.py) |
| `requirements.txt` 里 **`opencc-python-reimplement` 在 PyPI 上 404**（正确名 `opencc-python-reimplemented`） | [PyPI 404](https://pypi.org/pypi/opencc-python-reimplement/json)、[正确包](https://pypi.org/pypi/opencc-python-reimplemented/json) |
| Whisper medium：**769 M**（README 表，实际 763,857,920）、官方 **Required VRAM ~5 GB**、权重 3,055,544,304 B；仓库 MIT / HF 模型卡 apache-2.0 | [openai/whisper README](https://github.com/openai/whisper)、[hf-mirror API](https://hf-mirror.com/api/models/openai/whisper-medium) |
| 学术影响：**被引 1 次**（MIR-MLPop 2024）；Zenodo 下载 159 | [OpenAlex](https://api.openalex.org/works?search=Adapting%20pretrained%20speech%20model%20for%20Mandarin%20lyrics)、Zenodo API |
| Python 3.14：numba 0.68.0 classifier 含 3.14/3.15；本机 0.67.0 与 openai-whisper 均 **import 成功** | [PyPI numba](https://pypi.org/pypi/numba/json)、本机 import 探针 |
| 本项目侧现状（DP 跑在 ASR 词级时间戳上、按行窗对齐、插值 + 低可信标记；Demucs 人声分离已存在且与论文预处理一致） | [matcher.py](matcher.py)、[asr.py](asr.py)、[separate.py](separate.py)、[requirements.txt](requirements.txt)、[AGENTS.md](AGENTS.md) |

### 7.2 没查到 / 未验证（**不要当成"没问题"**）

1. **Opencpop 官方许可原文**：官方页 <https://wenet.org.cn/opencpop/liscense/> 本次**无法访问**（同时 archive.org 也不可达），只有**第三方镜像**转述"禁止商用 + 规则约束 resulting models"。⇒ 以官方原文为准之前，这条算**未证实**。
2. **LyricAlignment 的显存需求**：官方从未给出，**未查到**，也**未实测**。文中 ~5 GB 只是 Whisper medium 的官方参考值。
3. **checkpoint 解压后的单文件大小与内部结构**：未下载 5.8 GB，**未验证**；"单个约 2.9–3.1 GB"是**从代码推断**。
4. **中文现代流行歌上的真实精度**：**未找到任何客观证据** —— 没有第三方复现、没有对比评测、没有 HF 镜像（在 hf-mirror 上按 `lyric-alignment` / `lyrics alignment` 检索只找到越南语项目，**检索不等于穷尽**）、OpenAlex 被引 1 次且是数据集论文。所有精度数字都是论文/README **自报**。
5. **与我们现有链路的可比数字**：**没有**。它的 MAE 是"字符边界 MAE"（MIR-1k mix、19.5 分钟），我们的是 QRC/YRC 逐字对照与 AMLL DB 一致率 —— **口径不同，不能直接比大小**。谁更好，只能靠 §6.2 的实验。
6. **语义学者（S2）引用数**：API 连续 429，**未取到**；改用 OpenAlex（被引 1）。
7. **huggingface.co 直连**：本环境**不可达**（走 hf-mirror 才通），所以"是否存在 HF 镜像/改写版"只做了镜像站检索。
8. **它能否真的在这台机器上跑起来**：**未实测**。理由：需要 5.8 GB 权重 + 4 个未安装的包，且本机 torch 是 **CPU 版**；本次只做了 `import` 级探针，**没有跑过它任何一次推理**。
9. **`pip install -r requirements.txt` 修好包名后能否在 Python 3.14 上完整装通**：**未实测**（只验证了包名 404 与各包 `requires_python`）。
10. **它对拖音（句尾长音）的表现**：论文的 MAE 同时含 onset 与 offset，但**没有单独的 offset/拖音分析**；是否比我们现有的 F0/能量判据（`tail_extend.sustain_end`）更准，**未验证**。

### 7.3 我做过的操作（可复核）

- 用 `api.github.com` 读取仓库元数据、完整文件树与 **16 个文件的原文**（raw.githubusercontent 与 jsdelivr 在本环境被拦，故走 API + base64 解码）。其中 **13 个完整落盘到 `%TEMP%\la_eval\`（在项目目录之外）**后逐行阅读，另外 `train_multitask.py` / `evaluate_transcript.py` / `data_processor/record.py` 取回后直接查看（正文未逐一细读的还有 `utils/CER.py`、`utils/postprocess.py`、`inference_transcript.py`）。**没有克隆到本仓库、没有新增任何项目文件。**
- 读 [arXiv 全文（ar5iv）](https://ar5iv.labs.arxiv.org/html/2311.12488)、Zenodo API、PyPI JSON API、OpenAlex、hf-mirror API。
- 在本机跑 `import` 探针（`python -c "import torch/numba/whisper/..."`）与 PyPI/GitHub 元数据查询；**没有下载模型权重、没有运行它的推理、没有修改本项目任何代码**。
- 本次唯一写入的文件：**本文件**（`docs/LyricAlignment评估.md`）。
