# Timing 对齐引擎研究笔记

> 研究对象：[`Zennmn/Timing`](https://github.com/Zennmn/Timing)（MIT，Python）
> 研究日期：2026-10-25　基线：`7ad5f7e`（2026-10-21，"Add Apple line-timed TTML lyric import"）
> 目的：拆解它的**对齐核心与引擎设计**，评估哪些能移植到 Lyric2TTML，哪些不能。

---

## 0. 一句话结论

Timing 的引擎 = **「三种声学对齐后端 + 一套统一文本/音素中间表示 + 严格的失败语义」**：

- **MMS-300M CTC**：逐句强制对齐（帧级 Viterbi），也负责给 TIFA 做句段粗定位；
- **TIFA-1.0-ST**：带 LRC 锚点的**歌声联合对齐**（音素级 spans，10ms 网格，主路线）；
- **SOFA**：纯文本/无逐句时间时的歌声对齐（音素序列 DP）；
- 三者输出统一成 `Line/Unit`（display 文本原样保留 + 发音/音素 + flags），再走报告与 AMLL TTML 导出。

它最值得学的不是模型本身，而是这套**工程化的对齐状态机**：先校验、再按锚点切 clip、失败按行局部重试、仍失败则保留可复核占位时间，**绝不伪造时间**；以及把「语言判定 / 读音 / 分词」和「时间预测」彻底分开（LLM 只做前者）。

---

## 1. 总体数据流

```
音频+歌词
  │
  ├─ request_plan.plan_request()        先校验（格式/语言/路线/歌词体积/锚点一致性）
  │
  ├─ pipeline.run()
  │   ├─ audio.decode()                 ffmpeg → 单声道 16k PCM
  │   ├─ sources.prepare_sources()      三选一：lead / vocals / source
  │   │    ├─ bs_lead.py  BS-Roformer   只留主唱（默认）
  │   │    ├─ separation.py Kim MelBand 去伴奏留全部人声
  │   │    └─ 原曲 / 导入人声（必须同起点同时长）
  │   └─ alignment.align_*()            按 plan.aligner 分派
  │        ├─ tifa     : CTC 粗定位 + TIFA 音素级精细对齐（需逐句时间）
  │        ├─ ctc      : MMS-CTC 逐句 Viterbi
  │        ├─ sofa     : SOFA 音素序列 DP（纯文本可用）
  │        ├─ llm_direct: LLM 判语言/读音/拆分 → 本地 MMS+TIFA 定时
  │        └─ llm_import: 导入网页 LLM 结果 → 本地 MMS+TIFA 定时
  │
  └─ reporting.finish() → ttml.py       报告 JSON + AMLL TTML 初稿
```

关键约束（`request_plan.py`）：

| 路线 | 歌词要求 | 说明 |
|---|---|---|
| `tifa` | **必须逐句有 start/end**（LRC/TTML/JSON） | 句内音素级；纯文本不可 |
| `ctc` | 必须逐句有 start/end | 单句 ≤30 秒（模型限制） |
| `sofa` | 纯文本 / 逐句均可 | 纯文本路线 |
| `llm_direct` / `llm_import` | 必须逐句有 start/end | LLM 只给语言/读音/拆分，时间仍由本地模型算 |

`auto` 规则：全部行有锚点 → `tifa`；否则 → `sofa`。**主唱歌词必须「全部有锚点」或「全部无锚点」**，混用直接报错。

---

## 2. 统一中间表示（最值得抄的设计）

`timing/schema.py`：

```python
Unit(text, spoken, reading="", start=None, end=None, flags=[], source_pronunciation="")
Line(text, start=None, end=None, agent="v1", role="main", parent=None,
     language="auto", translation="", roman="", words=[Unit], flags=[])
```

- **display 文本与发音分离**：`text` 是最终要写进 TTML 的原文（标点、大小写、括号都保留）；`spoken` 是模型实际读的音节/假名/拼音文本；`reading` 是音素串。所有对齐只操作 `Unit`，最后用断言保证 `''.join(u.text) == line.text`，**不允许跳字/替换歌词**。
- `flags` 是给人工复核与导出用的证据标签：`tifa_native_alignment`、`tifa_unresolved_unit`、`lrc_review_placeholder`、`ctc_single_frame_check`、`long_sustain_check`、`tifa_crop_edge_check`、`custom_pronunciation`、`llm_audio_reading_candidate` 等。
- `agent`（v1/v2/v3）+ `role`（main/bg）+ `parent` 表达对唱与背景和声。**注意：这是输入声明的结构，不是自动声纹识别**（README 原话："显式结构不代表模型自动识别或分离不同歌手"）。
- `Line.language` 支持 `auto/mixed/ja/en/zh/zh-en`，行级优先于整首；`zh` 是普通话。

文本正规化（`timing/text.py`）：

- `kept()` = NFKC + 只留字母/数字/撇号 → 模型输入；
- 中文：`chinese.py` 按字切分，多字词按词典/短语读音（`pypinyin`）；
- 日语：`fugashi + unidic-lite` 分词，`kana/pron` 取读音；`japanese_units(fine=True)` 对汉字组保持不可拆（今日=きょう），假名按 mora 细拆；`ー` 等无声标记有专门 re-attach 逻辑（`tifa_worker.restore_silent_marks`）；
- 英语：`english_syllables.split_english` 按 CMUdict/音节规则拆，可选 `pyphen`；数字用 `numeric_lyrics` 转英文读法；
- 读音覆写：`dictionaries/pronunciations.json`（ja/en/zh 三类）。

> 这是它比"直接用 Whisper 识别再对齐"更可控的地方：**歌词文本是确定的**，模型只负责把已定好的音素序列贴到音频上；错误主要是"读音"和"音频"，不是"听错字"。

---

## 3. 音源准备与时间线（决定对齐上限）

`timing/sources.py` 三种模式：

| 模式 | 处理 | 用途 |
|---|---|---|
| `lead`（默认） | BS-Roformer Karaoke 从原曲/导入轨提取**主唱**（尽量去和声） | 主流对齐 |
| `vocals` | Kim MelBand 去伴奏，**保留主唱+和声** | 需要和声/伴唱时 |
| `source` | 不改动原曲/导入轨 | 调试或已有人声 |

严谨点：

- 音频统一用 ffmpeg 解成单声道 16k s16（`audio.decode`）；TIFA/SOFA 再按需 44.1k float；
- `match_timeline()` 只允许重采样端点取整误差，**不允许音轨整体位移**；BS/Kim 输出还会校验 frames/samplerate/channels 完全一致；
- `role=bg` 在 `lead` 模式下必须用 Kim 完整人声或导入完整人声，**没有"原曲减主唱"回退**；
- 分离失败直接报错，不静默换轨（README 明确）。

> 我们的 pipeline 同样有分离可选，但"分离失败静默回退"和"时间线一致性校验"值得对照检查。

---

## 4. 引擎一：MMS-300M CTC（逐句强制对齐 + 粗定位）

`timing/ctc.py`

### 4.1 算法

1. 文本 → `units + roman labels`：中文按字（拼音字母串）、日文按读音、英文按字符/音节；每个 unit 对应一段 roman label 串；
2. 音频单句（≤30s）→ MMS-300M（`transformers.AutoModelForCTC`）→ `log_probs[T,V]`；
3. 对 `targets = 每个字符的 vocab id` 跑标准 CTC Viterbi（numba `@njit`）：

```text
states = 2*len(targets)+1   # blank + target 交替
初始化：state0 = logp[0, blank]，state1 = logp[0, target0]
递推：dp[t,s] = max(dp[t-1,s], dp[t-1,s-1], (s>1 且 label[s]!=blank 且 label[s]!=label[s-2]) ? dp[t-1,s-2] : -inf)
              + logp[t, label[s]]
回溯得到每帧的 state path
```

4. 每个 target 的帧区间 = `flatnonzero(path == 2*i+1)` → 首帧/末帧；再按 unit 的 roman 长度把字符 span 合并成 unit span；
5. `stride = prod(model.config.conv_stride)/16000`（MMS 为 20ms），所以 `start = 帧号*stride`；
6. 输出 flags：`ctc_single_frame_check`（span ≤1 帧）、`ctc_acoustic_alignment`；无时间修补。

### 4.2 用途

- `--aligner ctc`：逐句对齐；
- 更常见的是 **TIFA 的粗定位器**：在 LRC 句范围内用 CTC 找出"最后一个字/音素的峰"，再向后找静音点作为 clip 右边界（`quiet_crop_end`），避免把长音尾切掉。

### 4.3 限制

- 必须 CUDA；单句 ≤30 秒；逐句锚点必须有；
- MMS 的**许可为 CC-BY-NC-4.0（非商用）**——不能随 MIT 项目分发/商用；
- 20ms 网格只是模型帧长，**不是精度承诺**。

---

## 5. 引擎二：TIFA-1.0-ST（主路线，歌声联合对齐）

`timing/tifa.py`（编排）+ `timing/tifa_worker.py`（子进程推理）

### 5.1 为什么需要它

MMS/CTC 对**唱歌**（长音、转音、混唱、日英混排）容易糊；TIFA 是专门做歌声音素级对齐的模型：输入音频 + 音素路径，输出每个音素的起止（10ms 网格），还能在多个候选读音之间用 `levenshtein` 选一条最匹配的路径。

### 5.2 Clip 构造（核心工程）

每一行单独做一个 clip，但带**邻句上下文**：

```text
neighbours = 当前行 ± 1 行（同 agent、同"中文/非中文"语言组、总跨度 ≤59s）
left  = max(0, 第一邻句.start - 0.12)
right = quiet_crop_end(音频, coarse_end=CTC 最后峰, limit=最后一邻句.end + 0.35)
        → 从 coarse_end+0.08s 起找连续 180ms RMS < max(峰值-23dB, -65dB)，再加 0.15s
若 right-left 不在 [0.1, 60] 秒 → 报错
```

- `text` 保留邻句原文；`target_start/end` 用 `kept()` 后的字符偏移标出**本行**在合并文本中的位置；
- 每行语言 scope：`zh/zh-en → ['zh','en']`，否则 `['ja','en']`；
- 右侧不能直接用 LRC 的下一行 start 截断（LRC 的下一行开始不代表上一句尾音结束），所以要"峰后找静音"；
- 背景行用 background 音轨。

### 5.3 文本 → TIFA 音素（G2P 插件化）

`tifa_worker.main()` 动态组装上游 TIFA 的 G2P 配置：

| 语言 | 转换器 | 说明 |
|---|---|---|
| 中文普通话 | `timing.chinese_tifa`（自定义 converter） | 拼音 + 原生中文词典；兼容 `llm_audio_reading` 候选 |
| 日语 | `japanese-mecab`（unidic-lite）+ 自定义四类 converter | 贷款词读音、日语数字读音、听音候选、共享 `ー` 处理 |
| 英语 | CMUdict（`ds_cmudict-07b.txt`）+ 音节拆分 | 数字走动态注入的 `english_numbers.txt` 字典，不修改模型文件 |

显式控制：`oov_handling="raise"`（不静默跳过）。同时把 `readings/pronunciations.json` 与 LLM 听音候选注入，用于纠正多音字/特殊读法。

### 5.4 对齐推理（上游 TIFA API）

```python
backend, vocabulary, config = load_inference_model(model.pt, scope=FA)
backend = backend.cuda().eval()
dataset = AudioTextDataset(...)      # 每个 clip + 文本
item = dataset[i]
spec = backend.spectrogram(batch.waveform, batch.duration)
scored = backend.score(spec, paths=..., words=..., candidates=..., unit="levenshtein")
tokens, owners, groups = materialize_paths(...)
aligned = backend.align(spec, tokens=..., groups=..., unit="second", skip_penalty=0.5)
spans = aligned.spans[0].cpu().tolist()      # 每个音素 [start,end]
agreement = aligned.agreement[0]            # 模型内部指标，不是准确率
```

- **候选读音**：模型为每个 word 给多个候选音素路径，`score(unit="levenshtein")` 选与声学最匹配的一条；选中的候选会记录 `custom_pronunciation` / `llm_audio_reading_candidate` / `llm_direct_candidate`；
- **`skip_penalty=0.5`**：允许模型跳过不可发音/静音单元，而不是把时间硬塞给它；跳过的音素进入 `skipped`；
- **display_plan**：TIFA 的 word 拆分（尤其日语汉字组、英文音节、`ー`）和原文 display 文本不是一一对应，worker 会生成"display 分段计划"（`groups`/`group_count`），`map_result()` 再映射回原文字符；**映射不完整就报错，绝不替换歌词**。

### 5.5 结果映射与校验（`map_result` / `complete_results`）

- 输入：`row["spans"]`（音素边界）、`row["texts"]`（模型分词）、`selected`（选中的读音）、`owners`（音素→word）、`groups`；
- `restore_display(original, labels)`：把每个发音字符映射回原文位置（保留标点/大小写/撇号），
  `normalized == expected` 不成立就抛错；
- 每个 Unit 的 start/end = 它拥有的首/末音素边界（+clip offset）；若 `partial=True`，无效/跳过音素对应的字保留 `start=None` 并标 `tifa_unresolved_unit`；
- 行级校验：
  - `validate_clip()`：字边界必须有限、单调、落在 clip 内；起音不能越过下一行；
  - 主唱行按 `agent` 检查**行间时间次序**，冲突行剔除；
  - `line.start/end` 用首/末 unit 修正；`crop_edge` 标记是否贴着 clip 边缘（可能被切）；
  - `flags`：`tifa_native_alignment` / `tifa_unresolved_unit` / `tifa_context_retry_check` / `tifa_crop_edge_check` / `long_sustain_check`（>2.5s）。

### 5.6 失败处理状态机（最值得抄）

```
初跑（全部行）
  └─ 失败行 → 重试 3 次：window pad = 0.12s / 0.35s / 0（原范围）
        └─ 成功 → 标 tifa_context_retry_check
        └─ 仍失败 → partial 尝试：保留该行内所有有效 unit，无效 unit 标 tifa_unresolved_unit（时间 null）
              └─ 仍不行 → 该行保留输入 LRC 整句时间，标 lrc_review_placeholder
                     （ttml 导出为零时长编辑占位，JSON 时间 null）
```

全程不"平均分摊"、不"补造时间"；失败信息写入 `completion.json`（`checks`/`attempts`），报告可追溯。

### 5.7 并行/资源

- TIFA 跑在**独立子进程** `tifa_worker.py`：把 TIFA 的 `deps/runtime` 插到 `sys.path`，避免与 SOFA 的模块/库冲突；
- 每个 clip 跑完释放 GPU（`model.cpu(); empty_cache()`），峰值显存记录在 `raw.json`；
- 批处理是串行的（单 GPU 队列）。

---

## 6. 引擎三：SOFA（纯文本/歌声对齐）

`timing/singing.py`（`SingingAligner`）

- `phonemize()` 把整首歌词变成**unit 序列 + 每 unit 的音素 + 行归属**：
  - 行间插入 `("sp","SP")`，行内中文按字（普通话拼音音素）、日语按 mora、英语按音节；
  - 每个 unit 的音素必须有对应；映射不完整就报错（不跳字）；
- `SingingAligner._align_native()`：按语言加载上游 SOFA 的 `LitForcedAlignmentTask`，单语言完整推理；
- 混唱时对 `ja`/`en`（或 `zh`/`en`）两套专家分别出 logits/edge，再合并：
  ```python
  sub = logits_all[:, native_ids]      # 当前语言音素子集
  sub -= logmax; sub -= logsumexp     # log_softmax
  blanks.append(sub[:, native_ids.index(0)])   # 该专家判"非本语言/静音"
  ...
  emissions[:,0] = np.maximum.reduce(blanks)   # 任一专家认为静音即可
  edge = np.maximum.reduce(edges)
  # 自定义 _decode(ids, emissions, edge_prob) 做音素序列 DP
  ```
- 边界精修：`frame_indices + frac(edge_diff)`，再换算成秒；
- 每个 unit 的 span = 它所有音素 span 的首尾；`long_consonant_check`（>0.4s 的非元音）、`long_sustain_check`（>3s）；
- 缺音素直接报错，**不伪造时间**；无逐句锚点时这是唯一可用路线。

---

## 7. 路线选择与失败语义

- `auto`：有逐句锚点 → TIFA；纯文本 → SOFA。
- `ctc`：有锚点、要快/省显存时；单句 ≤30s。
- `llm_direct`：整首音频+歌词交给多模态 LLM 判**语言/读音/拆分**；时间仍由本地 MMS+TIFA 算；不自动分批/补发；无逐句锚点不可用。
- `llm_import`：网页 LLM 生成 JSON 导入，**不调用 API、不需要 Key**；同样本地定时。
- 失败语义：
  - 校验失败：在加载模型/分离/LLM 之前报错；
  - 分离失败：报错，不换轨；
  - 对齐失败：按行降级（TIFA 重试 → partial → LRC 占位）；
  - 导出：TTML 是"初稿"，失败字零时长占位，JSON 时间 null，报告注明。

---

## 8. LLM 的边界（值得学）

- LLM **只做**：语言判断、读音选择、分词/音节拆分（+ 可选的听音候选），**绝不返回时间**；
- 两个入口：`llm_direct`（API，一次发整首音频+歌词；600s 读取超时、64MiB 编码上限）与 `llm_import`（网页模型，无 Key）；
- 提示词模板 `fine`/`original` 独立保存、哈希绑定、报告记录实际模板；
- "LLM 不改写歌词"：返回的分段必须能和规范化的歌词行做连续分段校验；非法 JSON 行拒绝，不自动补发；
- 优点：把 LLM 的能力限制在它擅长的"读音/分词"，时间交给声学模型；缺点：依赖外部服务/网络，和"纯离线"目标冲突（但 import 模式可规避 API Key）。

---

## 9. 工程与验证

- 模型：5 组固定模型（MMS/TIFA/SOFA/BS/Kim），pin revision + SHA256，manifest 校验；安装器只检查文件，不把"目录存在"当完整；
- 安装：`setup.ps1` 检查 PowerShell 7/uv/FFmpeg/Git、真实 CUDA 运算、再下载模型；失败保留 `.part` 支持续传；安装与启动互斥；
- 测试：约 485 项 Python 测试 + 94 项页面测试（发布说明），覆盖 anchors、TIFA 映射/回退、CTC、SOFA、中文/日语/数字/英文音节、LLM 协议、安装流程、模型 catalog、发布检查；
- 发布检查器：扫权重/媒体/密钥/大文件，防止把模型/歌曲/Key 提交；
- 文档：architecture/models/usage/installation/publishing + THIRD_PARTY 许可表，写得很细；
- 子进程隔离：TIFA worker 独立进程，避免依赖冲突；
- 失败可追溯：`completion.json`、`raw.json`、`checks/attempts`、每行 flags。

---

## 10. 许可（必须注意）

| 组件 | 许可 |
|---|---|
| Timing 自有源码 | MIT |
| MMS-300M | **CC-BY-NC-4.0（非商用）** |
| TIFA 运行库源码 | MIT；TIFA-1.0-ST 模型/资源按发布包各自条款 |
| SOFA 源码 | MIT；日中英权重/字典按各自条款（下载器校验 SHA256） |
| BS-Roformer 源码 | MIT；主唱权重（becruily）**未明确许可** |
| Kim MelBand 人声权重 | 作者条款，**未确认可商用** |
| 其他依赖 | 各自许可（uv.lock 固定） |

> 结论：**不能**把它的完整模型组合直接塞进你的 MIT 便携包对外分发/商用。
> 若只借用**思路/适配代码**（MIT）没问题；要分发模型，需要逐个确认许可，或只做"用户自行安装模型"的可选后端。

---

## 11. 与 Lyric2TTML 的对比

| 维度 | Timing | Lyric2TTML（我们） |
|---|---|---|
| 定位 | 对齐引擎，输出初稿 | 完整产品：转换 + 编辑 + 保存 + 分发 |
| 输入 | 歌词必须正确；TIFA/CTC 需逐句时间 | TXT/LRC 均可；Whisper 识别 + DP 对齐 |
| 核心对齐 | MMS CTC / **TIFA 歌声模型** / SOFA | faster-whisper + 自研 DP 强制对齐 |
| 语言 | 中（普通话）/日/英/混唱 | 以中文为主 |
| 对唱/声部 | **显式 JSON 的 agent/role，无自动声纹** | CAM++ 自动分声部（约 90%） |
| 句尾拖音 | 未专门处理（TIFA 音素边界可能覆盖长音） | 专门的 `tail_extend`（能量+音高连续性） |
| 重叠/和声 | 输入声明 `role=bg` + parent | 证据门控 `allow_same_agent_overlap` + x-bg |
| 编辑器 | 无（明确说初稿要试听校对） | 逐字编辑台（时间/声部/和声/文本/草稿） |
| 分发 | 源码 checkout + setup.ps1 | WinUI 3 便携包/安装包 + 模型管理页 |
| 模型管理 | 5 组固定，安装器下载 | 模型/运行库页，多源、断点续传、校验 |
| 平台 | Win11 + NVIDIA CUDA + PS7 + Python 3.12 | Windows 10/11，CUDA 运行库页内装 |
| 失败语义 | 行级重试/占位，不伪造时间 | 证据门控、失败保留、任务日志 |
| 测试/文档 | 485+94 项，文档/许可极细 | 我们有自己的测试，但文档工程化可借鉴 |
| 许可 | 源码 MIT；MMS 非商用；分离权重不明 | 自有 MIT，模型由用户下载 |

---

## 12. 可借鉴清单（按投入/收益）

### 高收益、低成本
1. **display/spoken 分离 + 严格重建断言**：我们已经有 EditDoc/Unit，但可加 `kept()/spoken/reading/flags` 的显式分层；任何对齐/编辑操作后断言 `''.join(text) == 原歌词`，防止"静默改字"。
2. **clip 级「锚点 + 局部重试 + 占位」状态机**：我们的 Whisper 对齐目前是整首识别+DP；对低置信/失败行可以学它：先按 LRC 行做局部重跑（±0.12/0.35s），仍失败保留整句时间并标记待补轴。
3. **复核 flags 体系**：`tifa_crop_edge_check` / `long_sustain_check` / `ctc_single_frame_check` / `partial_unresolved` 这类"模型内部证据"直接进编辑台标红/低可信，比单一"置信度"更有解释力。
4. **模型 manifest 校验（pin + SHA256）**：我们的 model_manager 已有 SHA，但可在加载前做 manifest 全量校验，并把 mismatch 变成明确错误。
5. **LLM 边界**：如果未来接 LLM，只让它做"读音/分词/语言"，时间永远本地算；并做"无 Key 的网页导入"模式。

### 中收益、中成本
6. **TIFA 作为可选对齐后端**：对逐句 LRC、中文/日英混唱场景，TIFA 的歌声专业度可能高于 Whisper；封装成我们后端的一个 `aligner=tifa` 插件（输入音频+行时间歌词，输出 EditDoc），保留我们现有 pipeline 作为默认。
7. **SOFA 作为纯文本路线**：适合"有歌词但无 LRC"且要求歌声对齐的场景；但纯文本无行时间，需要先做句段定位（可用 Whisper/CTC 粗定位），工程上比 TIFA 更复杂。
8. **日语/英语支持**：若产品要扩语言，TIFA+SOFA 是最直接路径；但会引入 MeCab/unidic、CMUdict、日语数字/贷款词等一整套文本工程。

### 低收益、不建议
9. 整套安装器（uv/WinGet/PS7/CUDA 验证）：我们已有更成熟的打包与模型管理，不必换。
10. MMS-300M：非商用许可，且它主要给 TIFA 做粗定位；我们已有 Whisper/LRC 锚点，收益有限。
11. 它的无编辑器"初稿"定位：我们编辑台是差异优势，不要削弱。

---

## 13. 如果要集成：推荐路线

### 阶段 0：离线评测（1–2 天，零集成风险）
- 用它的 CLI 在同一批歌上跑：有 LRC 用 `--aligner auto`（TIFA），纯文本用 `--aligner sofa`；
- 与我们的产物比：字级边界误差（用 QRC/YRC 或人工标注）、长音尾、对唱/和声、试听；
- 同时核对许可证：如果只做内部评测，模型许可不构成分发问题。

### 阶段 1：适配层原型（3–5 天）
在我们 Python 后端加一个**外部对齐器接口**：

```python
class ExternalAligner(Protocol):
    name: str
    def align(self, audio_path: Path, lines: list[LineAnchor], language: str,
              out_dir: Path) -> list[UnitSpan]: ...
```

- `tifa` 适配：调用 Timing 的 `timing.pipeline` 或直接其 `tifa.align()`（需子进程隔离与 CUDA）；
- 输出映射到我们的 `EditDoc`/`Unit`，复用我们的 `ttml_check`、编辑器、保存流程；
- 失败回退到现有 Whisper pipeline；
- 模型不打包，放在"运行库/模型管理"里让用户自行下载（避免许可与体积问题）。

### 阶段 2：产品化（2–4 周）
- UI：工作台加"对齐引擎：内置 / TIFA（实验）"；
- 评测与灰度：默认内置，TIFA 作为高级选项；
- 许可：TIFA 模型条款、SOFA 权重条款逐个确认；MMS 不随包分发，BS/Kim 权重不随包分发；
- 失败语义：沿用它的"不伪造时间 + 占位 + 报告"策略，进我们的编辑器标红。

---

## 14. 总结

Timing 的对齐核心不是"一个更准的模型"，而是：

1. **确定文本、只对齐音素**（display/spoken 分离 + 严格重建）；
2. **按句切窗、带上下文、先粗后精**（CTC 峰→静音边界→TIFA 音素级）；
3. **失败不伪造**（局部重试 → partial → LRC 占位，全链路 flags/报告）；
4. **LLM 只做读音/分词，不做时间**；
5. **模型 pin/hash + 子进程隔离 + 发布检查** 的工程纪律。

对我们的价值：**TIFA/SOFA 可以作为"歌声对齐后端"实验**，补齐 Whisper 在唱歌上的短板；但它们的模型许可、CUDA-only、4.27 GiB 体积决定了只能做"可选、用户自行安装"的后端，不能替代我们现有的完整产品链路。真正应该先抄的是它的**状态机、flags 与文本保真约束**——这些不依赖模型许可，能直接提升我们现有 pipeline 的可解释性与鲁棒性。

---

## 附：关键源码索引

| 文件 | 作用 |
|---|---|
| `timing/request_plan.py` | 路线/语言/锚点校验，auto 规则 |
| `timing/schema.py` | Unit/Line 数据模型 |
| `timing/text.py` | 分词、display/kept、日语/英语/数字处理 |
| `timing/sources.py` / `audio.py` / `bs_lead.py` / `separation.py` | 音源三模式、时间线校验 |
| `timing/ctc.py` | MMS-CTC Viterbi、帧→unit 映射 |
| `timing/tifa.py` | clip 构造、粗定位、TIFA 调用、重试/占位状态机 |
| `timing/tifa_worker.py` | TIFA 子进程、G2P 插件、音素候选、display_plan |
| `timing/singing.py` | SOFA phonemize、双语专家融合、DP decode |
| `timing/alignment.py` | 路线分派、LLM records 对齐 |
| `timing/reporting.py` / `ttml.py` | 报告与 AMLL TTML 导出 |
| `timing/llm_direct.py` / `llm_import.py` | LLM 边界（只语言/读音/拆分） |
| `scripts/check_publication.py` | 发布前源码/密钥/媒体检查 |
| `tests/` | 485 项 Python 测试覆盖上述逻辑 |
