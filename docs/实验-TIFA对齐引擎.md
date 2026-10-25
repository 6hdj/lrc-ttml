# 实验：TIFA 对齐引擎接入与评测

> 日期：2026-10-25　状态：**已接入产品（实验选项）**；默认仍是 Whisper
> 参考：`docs/调研-Timing对齐引擎.md`；上游：openvpi/TIFA（运行库 MIT）、
> Timing（适配思路 MIT，https://github.com/Zennmn/Timing）

---

## 1. 结论：TIFA 确实更准

用 QQ 音乐 `.qrc` 逐字时间当外部真值，三首歌的**逐字起始散布**（去掉整体录音平移后）：

| 歌曲 | Whisper 散布 | TIFA 散布 | Whisper ≤0.2s | TIFA ≤0.2s |
|---|---:|---:|---:|---:|
| 让风告诉你 | 0.268 s | **0.138 s** | 54.9% | **81.0%** |
| 我不曾忘记 | 0.188 s | **0.111 s** | 61.0% | **85.7%** |
| 失眠飞行 | 0.137 s | **0.088 s** | 76.7% | **93.6%** |

- 字级正确性（100% 映射）三首均通过；
- 逐字结束散布同样下降（0.284→0.180、0.194→0.124、0.148→0.101）；
- 行结束中位偏差仍受“不同录音整体偏移”影响，不能直接当准确率。

复现：

```powershell
python tools/eval_word_timing.py
```

---

## 2. 产品里怎么用

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
5. 模型许可独立：TIFA 运行库 MIT，但 TIFA-1.0-ST 模型/字典按上游发布包条款，**不随 MIT 安装包分发**。

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
4. **模型许可**：若要随产品分发 TIFA 权重，需要单独完成授权核对。
