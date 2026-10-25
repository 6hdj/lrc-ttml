# 实验：TIFA 对齐引擎接入与评测

> 日期：2026-10-25　状态：**已接入产品（实验选项）**；默认仍是 Whisper
> 参考：`docs/调研-Timing对齐引擎.md`；上游：openvpi/TIFA（运行库 MIT）、
> Timing（适配思路 MIT，https://github.com/Zennmn/Timing）

---

## 1. 结论：TIFA 确实更准

用 QQ 音乐 `.qrc` 逐字时间当外部真值，三首歌的**逐字起始散布**（去掉整体录音平移后）：

| 歌曲 | Whisper 散布 | TIFA 散布 | Whisper ≤0.2s | TIFA ≤0.2s |
|---|---:|---:|---:|---:|
| 让风告诉你 | 0.268 s | **0.129 s** | 54.9% | **83.9%** |
| 我不曾忘记 | 0.188 s | **0.105 s** | 61.0% | **83.0%** |
| 失眠飞行 | 0.137 s | **0.082 s** | 76.7% | **95.4%** |

- 字级正确性（100% 映射）三首均通过；
- 逐字结束散布同样下降（0.284→0.150、0.194→0.114、0.148→0.097）；
- 行结束中位偏差仍受“不同录音整体偏移”影响，不能直接当准确率。

复现：

```powershell
python tools/eval_word_timing.py
```

---

## 2. 基于新模型修正的下游算法

TIFA 的逐字边界比 Whisper 准，原来为 Whisper 写的一些“补救算法”会对它
产生反效果；这一版按新模型调整：

| 原算法 | 对 TIFA 的问题 | 现在 |
|---|---|---|
| `tail_extend` 句尾拖音补全 | TIFA 已有真实音素终点，再用能量/基频续尾会**拉过头** | TIFA 分支默认跳过，并在 notes 说明 |
| 同 agent 重叠“整行按比例缩放” | 会把本来准确的所有字一起拉偏 | 只压缩**越界的那一小段尾字**，前面的字保留 TIFA 原值 |
| 整行在下一行之后（LRC 注释行插错） | 无宽度可压，仍报重叠 | 压成 1ms 占位，保证 TTML 合法 |
| 长间奏时 clip 右边界取 LRC 下一行开始 | TIFA 会把最后 2~3 个字拉过整段间奏，渲染器不再显示“三点倒数” | 有分离人声轨时，用 20ms RMS 找最后有声帧，把 clip 收到 `末声+0.4s`；保留间奏 gap |
| `onset_align` / Whisper DP / 精修 | 对 TIFA 是重复甚至有害 | TIFA 分支不走这些步骤 |
| 和声 `x-bg` | TIFA 是单序列强制对齐，叠唱时会把和声字混进主唱轨迹 | 检测到和声层时自动跑一次 Whisper，只替换 `x-bg` 层时间；主唱仍保留 TIFA |
| TMP/TEMP/MPLCONFIGDIR | 受限账户下 matplotlib/tempfile 直接失败 | 强制指向已经写探测过的 work 目录 |

效果（同一批歌）：逐字起始散布进一步下降
0.138→0.129、0.111→0.105、0.088→0.082；三首歌格式均 **0 错误**。
开启人声分离后（《我不曾忘记》）：逐字起始散布 **0.062s**，≤0.2s **97.3%**，
长间奏行被正确截断（gap 10.4s 保留，渲染器可显示三点倒数）。

> 间奏裁剪需要**分离后的人声轨**（`--separate-vocals` / WinUI「人声分离」）。
> 原曲间奏本身有音乐能量，无法用 RMS 准确判断人声结束；不开分离时 TIFA
> 仍可能把尾字拖过间奏（这是下一步可加 VAD/人声频带检测的点）。

### 和声：TIFA 主唱 + Whisper `x-bg`

TIFA 对每个字很准，但它是**单序列**强制对齐：歌词里带括号的和声会和主唱
串成一条音素序列，叠唱时和声字的轨迹会被主唱“吃掉”。Whisper 的 ASR/DP
在叠唱里能识别出和声字。

现在 TIFA 分支检测到歌词含背景层（括号 / `[和声]` / `[bg]`）时：

1. 主唱仍用 TIFA；
2. 额外跑一次 Whisper（同一份音频/分离轨），只取 `is_bg` 单元的时间；
3. 按行文本匹配后替换 `x-bg` 层，主唱单元完全不动；
4. 跑不下来时自动保留 TIFA 的和声结果，并写 note，不影响导出。

实测《在出发之前》（5 个 x-bg 行）：格式 0 错误，日志
`[和声] 已用 Whisper 替换 5 行的 x-bg 时间`。代价是 TIFA 模式在含和声的歌上
会多一次 Whisper 识别（约 40–60s），纯主唱歌曲不会触发。

### 自动转录和声文本（实验）

如果 LRC 里**完全没有和声文本**（只有主唱），TIFA/Whisper 都没有文字可对齐；
现在可以用 Whisper 的 raw ASR 自动补：

1. 自动模式关闭歌词提示的逐句切片（`chunked=False`），避免 prompt 把 ASR
   拉回主唱歌词；
2. 把 TIFA 主唱单元当 target、Whisper 原始词当 source 做单调 DP；
3. 未被主唱歌词匹配上的 source → 候选和声；
4. 保守过滤：置信度、时长、黑名单（字幕/优优独播/Television 等）、必须与
   某一行主唱时间重叠、按时间聚成短语、短语不能是主唱原文的一部分；
5. 写入该行 `x-bg`，日志/notes 标“实验，请核对”。

实测把《在出发之前》主唱歌词删掉一行后，Whisper 自动补回了
`那些美好或悲伤的记忆`（10 个 x-bg 词），格式 0 错误。

⚠️ 自动转录会**同时把“歌词漏写的主唱句”当成和声补进来**（它分不清“漏写的主唱”
和“叠唱的和声”）；这是实验功能，默认开，可用环境变量
`L2T_AUTO_HARMONY=0` 关闭。生成后请在编辑台核对 x-bg。

---

## 3. 产品里怎么用

### CLI

```powershell
python main.py --audio song.wav --lyrics song.lrc `
  --align-engine timing-tifa `
  --title "歌名" --artist "歌手" -o song.ttml
```

### Web / WinUI

- Web「转换参数 → 对齐引擎」：`内置 Whisper（默认）` / `TIFA 歌声对齐（实验）`；
- WinUI 工作台「对齐引擎」下拉：同样两项；
- 选 TIFA 但失败（缺模型、纯文本、语言不支持等）→ **自动回退 Whisper**，日志/notes 写明原因。

对应字段：`ConvertOptions.align_engine`，Web/WinUI 都通过 `params.align_engine` 传入。

### WinUI 冻结后端（方案 A：外部 Python 引擎）

WinUI 便携/安装包里的后端是 PyInstaller 冻结程序，自己没有 Python 解释器。
现在支持在 **设置 → TIFA Python 解释器** 里填一个装了 TIFA 依赖的
`python.exe`；外壳启动后端时会把它作为环境变量 `L2T_TIFA_PYTHON` 传下去：

```
设置 → TIFA Python 解释器：
E:\...\.venv\Scripts\python.exe
```

- 冻结后端里 `timing_align` 会在需要时用这个 Python 拉起 `models/tifa/runtime/infer.py`；
- 模型放在程序目录 `models\tifa`（或后端 `_internal\models\tifa`）；
- 没填/填错/依赖缺失 → 自动回退 Whisper，任务不失败。

**冻结后端实测**（`dist-winui\...\Lyric2TTML.Service.exe` + 外部 `.venv` Python）：

```
TIFA 逐行语言：zh×2
TIFA [zh]：2 个句段（GPU）… 完成 10.9s
TIFA 完成：11.2s，字级映射 100.0%
✓ 完成！匹配率 100.0%，格式通过：0 个错误 / 3 个警告
```

且日志中没有“回退 whisper”。

### 逐行语言路由

`_line_language()` 按每行文本判定：假名→`ja`、纯拉丁→`en`、汉字+拉丁→`zh-en`、
其余按整首设置；按语言分组后分别用 `-l`（混唱加 `-L en`）跑 TIFA，再合并结果：
日志会显示 `TIFA 逐行语言：zh×N、en×M …`。
当前日文/英文的 display 映射仍可能走占位回退（需要继续用 PFML 精确标语言）。

---

## 3. 能力边界（已接 vs 未接）

已接：

| 能力 | 状态 |
|---|---|
| 逐句 LRC 锚点 → TIFA 切窗对齐 | ✅ |
| 逐字映射回本项目 `AlignedLine/Unit` | ✅ |
| 复用 `TTMLWriter` + `ttml_check` + AMLL 解析器验证 | ✅ |
| 失败自动回退 Whisper | ✅ |
| 同 agent 行重叠自动压缩 | ✅ |
| TIFA 之后跑 CAM++ 声纹 / 歌词标记 | ✅ |
| 先 `--separate-vocals` 分离人声再喂 TIFA | ✅ |
| TIFA 之后做句尾拖音补全（需分离人声） | ✅ |
| 按 `ConvertOptions.language` 选 TIFA 主语言（zh/ja/en，混唱加 `-L en`） | ✅（基础版） |
| 逐行语言路由（按行分组跑 TIFA） | ✅（基础版；日/英映射仍可能占位） |
| WinUI 冻结后端 + 外部 Python（设置里指定） | ✅（实测通过） |
| 模型管理条目（文件 + SHA256 校验；手动安装） | ✅ |

未接 / 限制：

1. **必须逐句 LRC**：纯文本没有锚点，TIFA 分支会回退 Whisper；
2. **行级语言路由仍是基础版**：中英混唱用 `-l zh -L en`；逐行 ja/zh 切换要等 PFML；
3. **人声分离/拖音依赖现有 Worker/Demucs**；没有分离轨时自动跳过；
4. **冻结后端需要外部 Python**：设置里指定 `L2T_TIFA_PYTHON`（已实测可用）；没有 Python/依赖时自动回退 Whisper；
5. 模型许可独立：TIFA 运行库 MIT，但 **TIFA-1.0-ST 模型文件是 CC BY-NC-SA 4.0（非商业）**；
   `models-v1` Release 单独分发，不随 MIT 安装包捆绑；商用请勿使用 TIFA。详见根目录
   [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。

---

## 4. 模型管理

`desktop/resources/model_manifest.json` 新增 `tifa-aligner`：

- category: `aligner`；
- **下载按钮**：`on_demand`，从 GitHub Release **models-v1** 的 `tifa-aligner.tar`
  下载（163 MB，SHA256 校验），自动解包到 `models/tifa`；
- 下载源带 GitHub 直链 + gh-proxy / boki / ghproxy.net 兜底；
- `verify.kind: files_sha256`：`model.pt / config.yaml / vocabulary.json / runtime/infer.py`
  逐个核对大小 + SHA256；缺失/损坏会显示具体文件；
- 许可/来源/体积写在条目里，ModelsPage 直接显示。

TIFA 模型包：

```
https://github.com/6hdj/lrc-ttml/releases/tag/models-v1
tifa-aligner.tar  163 MB  sha256:5c856fee3db888c8171b9959e348e6e9bae7e4ce8ef33f102dfc4f2e913582ea
```

解包后：

```
models/tifa/
├─ model.pt            # TIFA-1.0-ST（151 MB）
├─ config.yaml
├─ vocabulary.json
├─ dictionaries/
├─ assets/
└─ runtime/            # openvpi/TIFA 源码
```

`L2T_TIFA_DIR` 可指定其它位置。
依赖（实验环境）：`g2pflow==0.3.0` + `g2pflow[ja]`、`unidic-lite`、`librosa`、`soundfile`、`textgrid`、`lightning`、
`torchmetrics`、`omegaconf`、`loguru` 等；已在本次 `.venv` 安装，**未进正式发布依赖**。

---

## 5. 实测（《在出发之前》产品链路）

| 指标 | 结果 |
|---|---|
| 歌词 | 32 行 / 266 字 |
| 字级映射率 | **100%** |
| TIFA 推理 | 15.2 s（RTX 4060 Laptop，32 个句段，batch=8） |
| 格式自检 | 0 错误 |
| AMLL 解析器 | 32 行 / 243 字，0 行无 words |
| 同 agent 重叠 | 修正 6 处 |
| 三首歌评测 | 见 §1 |

---

## 6. 下一步建议

1. **逐行语言路由**：用 TIFA 的 PFML 标出每行语言，替代整首 `-l/-L`；
2. **打包版支持**：要么随包带一个最小 Python + TIFA 运行时（体积大），
   要么做成“外部引擎”安装项（用户装 Python 后指定解释器）；
3. **继续评测**：补足 5 首以上、不同语种/混唱，用 `tools/eval_word_timing.py` 守住回归；
4. **模型许可（重要）**：TIFA-1.0-ST 权重是 **CC BY-NC-SA 4.0（非商业）**，不是 OSI 开源；
   MIT 只覆盖本项目代码与 TIFA runtime。商用/收费分发不能使用该模型，详见
   [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
