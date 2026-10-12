# AGENTS.md — 本仓库所有 AI 助手的必读约定

> 项目：**普通歌词（TXT/LRC）+ 音频 → Apple Music / AMLL 逐字 TTML**（离线，faster-whisper + DP 对齐 + CAM++ 声纹）。
> 详细原理、规范原文依据、逐条对照、症状对照表：**[`docs/TTML1-AMLL歌词格式规范.md`](docs/TTML1-AMLL歌词格式规范.md)**（先读它）。
> 本仓库可能同时有多个 AI 会话在改文件 —— **写文件前先重读，改完必须跑测试**。

---

## 0. 三条纪律

1. **改完必须验证**：
   ```powershell
   python selftest.py                                          # 必须：全部自检通过 ✓
   python main.py --check-only <新生成的文件>.ttml --strict-time  # 必须：0 个错误
   ```
2. **不要回退已修好的格式规则**（见 §1）。要改规则，先改文档再改代码。
3. **多会话并行**：每次 `write`/`edit` 前重读文件；改完立刻跑测试；重要结论写进 `docs/`。

---

## 1. 输出 TTML 的铁律（违反 = 播放器不显示 / 文件非法）

| # | 规则 | 依据 |
| --- | --- | --- |
| 1 | 时间戳默认 **`HH:MM:SS.mmm`**；`MM:SS.mmm`、裸 `4.370`、`10.0s` **都不是合法 TTML1** | TTML1 §10.3.1 |
| 2 | `clock-time` 的**小时段不可省略**；`offset-time` 必须带单位（`12.5s`/`250ms`） | TTML1 §10.3.1 |
| 3 | 时间必须被父元素包住：`span ⊆ p ⊆ div ⊆ body@dur`，且 `begin < end` | TTML1 §10.4 + AMLL 规范 §6 |
| 4 | 同一演唱者的 `<p>` 不能重叠（不同演唱者可以）。**判定只比主唱层**：和声 `x-bg` 唱到下一句之后属"因混音而重叠"，规范豁免（`ttml_check` + `ttml_normalize.overlap_pair(main_only=True)` 都如此） | AMLL 规范 §6-4 |
| 5 | `<tt>` 必须有 `xml:lang`（BCP-47，如 `zh-Hans`） | TTML1 §7.2.2（XSD 里 required） |
| 6 | 每个 `<p>` 必须带 `itunes:key="L1"…`，**从 L1 连续递增、跨 div 不断号**；只写 `xml:id` 会导致 **AMLL 整行丢弃** | AMLL 规范 §7.2 + `packages/ttml/src/parser.ts` |
| 7 | 每个 `<p>` 必须带 `ttm:agent`（单人歌也要 `v1`），且 head 里有对应 `<ttm:agent>` 定义 | AMLL 规范 §7.2 |
| 8 | **背景人声不能单独成行**：必须是某一行的子内容 `<span ttm:role="x-bg">`（AMLL 解析时 x-bg 不计入本行文本，单独成行 = 空行 = 看不见）。**不要再加"和声单独成行"的开关**（曾有过 `--bg-layout line`，生成的正是这种坏文件，已删除） | AMLL 规范 §7.3 + parser.ts |
| 9 | 文本/属性写入一律走 `ttml_writer._esc()/_attr()`（会清掉 XML 1.0 非法字符再转义） | XML 1.0 |
| 10 | UTF-8 **无 BOM**；`<p>` 内部写成一行（span 后换行会丢空格） | AMLL 规范 §2/§3.3 |
| 11 | `itunes:*` 用 `http://music.apple.com/lyric-ttml-internal`（Apple/AMLL 参考实现的写法） | 生态事实 |
| 12 | 对唱用**不同的 `ttm:agent`**（Apple Music 的左右分栏靠这个触发）；Apple 内部角色 `x-duet-a/b` 由 `--markers` 控制，**`auto`（默认）在 `--mode apple` 下解析成 `both`**、AMLL 模式下是 `agent`（守 AMLL 官方推荐） | AMLL 规范 §7.2 + Apple 生态 |
| 13 | **行必须包住自己的字**（`span ⊆ p`）。行尾装不下时**先整组回推**，仍装不下就**让行尾长出去**；绝不能把多个字压进同一个 `[end-min,end]` 窗口（那样同层字时间会重叠） | TTML1 §10.4 + `ttml_normalize.clamp_units_into_line` |
| 14 | **全曲只有 `v1` 就绝对不会有左右分栏**。元数据里有多位艺人却判成单人演唱时，`pipeline.duet_hint()` 必须给出"[v1]/[v2] 标记 / --singer-mode 2"的可操作提示（声纹分不开的对唱是最容易"以为生成了对唱其实没有"的地方）。⚠️ 提示里的**原因必须照实说**：关闭声纹时根本没跑声纹，不能写"声纹判定为单人演唱" | Apple Music 对唱体验 |
| 15 | **绝不能用"未知 role 的包装 span"包住主唱 span**（如 `<span ttm:role="x-duet-a">`）。AMLL 解析器对未知 role 走 `processWordElement`，它**不递归子节点**、只在包装层自己带 begin/end 时才产出一个词 → 整行被当成**一个词**，逐字时间轴全丢、**播放器一片空白**。实测 32 行 266 字 → 解析出 "32 个字"。对唱角色**只能挂在 `<p>` 上**（`--markers role|both`）；工具里"写在 span 上"的开关已删除，`ttml_check` 会把你手写的这种文件报成错误，`tools/verify_amll_parse.py` 也会判 ✗ | `@applemusic-like-lyrics/ttml` `processElementNode`/`processWordElement` + `tools/verify_amll_parse.py` |
| 16 | **每次生成都要用真 AMLL 解析器验一遍**（`pipeline` 第 6.5 步自动做）：逐行比较"字数 vs 文本长度"，任何一行塌成一个词就报警告。自己的校验器只查规范，查不出"播放器里空白" | `tools/verify_amll_parse.py` |

---

## 2. 文件职责地图

| 文件 | 干什么 | 改动后必须 |
| --- | --- | --- |
| `ttml_writer.py` | `AlignedLine[]` → TTML：时间表达式、XML 清洗、区间归一化、命名空间、元数据 | 跑 selftest + 严格校验新文件 |
| `ttml_check.py` | 确定性自检：结构 / §10.3.1 / 包含关系 / 重叠 / `L?` 连续性 | 同上；可故意造坏文件验证能报错 |
| `ttml_model.py` | 编辑态模型 `EditWord/EditLine/EditDoc`（可 JSON 序列化；字段名与 `matcher` 对齐，好让规范化算法两边共用） | 跑 selftest |
| `ttml_reader.py` | **反向解析** `parse_ttml(text) -> EditDoc` + 回写 `doc_to_ttml(doc)`（直接构造 `TTMLWriter`，**别用 `write_ttml()`**，它有丢参问题 G9） | 跑 selftest（往返不变量） |
| `ttml_normalize.py` | 时间轴**自动修复** R1–R7 + 改动清单；`normalize_group`/`overlap_pair`/`clamp_units_into_line` 三个纯函数**生成链路与编辑链路共用** | 跑 selftest |
| `preprocess.py` | LRC/纯文本 → `LyricLine`；`tokenize_chinese()`（括号=背景）、`detect_agent_switch()`（`[v1]/[v2]/[和声]/[旁白]`）、**`merge_background_lines()`** | 跑 selftest |
| `matcher.py` | 歌词 ↔ ASR DP 强制对齐；主唱/背景分开归一化；**按文本顺序排时间**（勿改为按时间排） | 跑 selftest |
| `diarize.py` | CAM++ 声纹聚类判断每行谁唱（`SIM_THRESHOLD = 0.35`） | 跑 selftest |
| `pipeline.py` | 6 步流水线（解析→分人声→ASR→对齐→精修→声纹→输出+自检） | 跑 selftest |
| `main.py` / `web_server.py` / `index.html` | CLI 与本地 Web UI（参数一一对应） | 至少跑 `python main.py --help` |
| `separate.py` | Demucs 人声分离（`--separate-vocals`） | — |
| `edit_api.py` | **编辑台后端纯逻辑**（P1）：载入/校验/保存、乐观锁、原子落盘、结构化错误。不碰 HTTP，可在 selftest 里直接测 | 跑 selftest |
| `edit_text.py` | **编辑台的文本编辑**（P3）：插行/删行/整行改文本、改字/插字/删字、和声（`x-bg`）增删改。**纯函数改 `EditDoc`**，不碰 HTTP；HTTP 入口是 `web_server._post_text`（`POST /api/text`） | 跑 selftest 第 31 组 |
| `textread.py` | **带编码探测的文本读取**。歌词/LRC 在中文 Windows 上大量是 GBK，写死 `utf-8-sig` 会让整个转换崩（`0xce` 那个 UnicodeDecodeError）。**任何读用户文本文件的地方都必须用它**，不要直接 `open(..., encoding="utf-8-sig")` | 跑 selftest |
| `singer_ssl.py` | **实验区（已冻结）**：Sony CSL `ssl-singer-identity` 适配器（TorchScript，1000 维）。**已拒绝**（5 个变体最好 68.8% vs 基线 89.9%，见 `docs/声纹评估协议.md` §7-8）。**禁止被生产模块引用**——自检第 24 组会扫源码强制检查 | 跑 selftest |
| `tools/eval_diarization.py` | **跨歌曲分声部评估**（真值：AMLL TTML DB 人工标注）。有 `--embedder/--variant/--mode/--source`，但**除嵌入那一步外跨度/聚类/评分一律不动**。动声纹前先读 `docs/声纹评估协议.md` | 跑一次确认基线仍是 85.0% |
| `tools/inspect_ttml.py` | 体检一份 TTML（命名空间/时间格式/x-bg/x-duet/agent/整行背景 p） | — |
| `tools/inspect_duet.py` | **对唱格式体检**：agent 声明与取值分布、`x-duet-*` 写在哪一层、`x-bg` 写法、括号半/全角、对唱样例原始 XML。**拿到 Apple 参考文件先用它** | — |
| `tools/verify_amll_parse.py` | **用真正的 AMLL 解析器验一份 TTML 能不能显示**（行数/字数/agent 分布/和声层）。首次自动抓 npm 包到 `.tmp/amll-parser/`，不需要 npm。**改了 TTML 结构后必跑** | — |
| `tools/test_bg_merge.py` | 背景人声合并的回归用例 | — |
| `tools/verify_from_report.py` | 用 `outputs/*.report.json` 重建 TTML 并做独立结构校验（不需要重跑 ASR） | — |
| `tools/audit_params.py` | **参数联调审计**：界面控件 → `params` → `/api/convert` → `ConvertOptions` → 有没有人读。查"纯 UI 控件""接了不用的死字段" | — |
| `tools/test_param_effect.py` | **参数生效性联调**：逐个选项改一遍、断言**产物真的变了**（不是只看有没有传）。含 `main.py --help` 能起来 | — |
| `promo/` | **宣传片的 Remotion 工程**（另一条独立流水线，不参与 TTML 转换）：`src/core` 版式常量、`src/scenes` 六个场景、`public/audio.wav`（旁白+自制配乐母带的副本）。渲染一次约 107 秒 / 1860 帧，比旧的 Edge 逐帧（约 18 分钟）快一个数量级。**改场景前先读 `tools/demo_video/README-REMOTION.md` 的时间轴与 token 表** | `node promo/tools/check-core.cjs`；改完重渲一遍 |
| `promo/tools/check-core.cjs` | **宣传片数值自检**（91 项）：用 `react-dom/server` 真渲染组件，把 style 与 `slides.html` 的基线公式逐项比。**不用 esbuild、不用浏览器**，受限环境里也能跑 | — |
| `promo/tools/render.cjs` | **宣传片全片渲染**（`@remotion/bundler` + `@remotion/renderer` 进程内 API）。⚠️ 跑之前必须把 `TMP`/`TEMP` 指到工作区内（自带 ffmpeg 写系统临时目录会 Permission denied），并显式 `--sample-rate 44100`（否则音频被升到 48 kHz，与基线不一致） | 改场景/改音频后重渲 |
| `tools/verify_promo_frames.py` | **宣传片的独立验收**：容器/音轨语音块/逐帧 MAE/确定性 6 项。含"验证验证器"自测 | 重渲后跑 |
| `selftest.py` | 离线自检（无网络/无大模型） | 任何改动后都要跑 |

---

## 3. 常用命令

```powershell
python selftest.py                                        # 离线自检
python main.py --audio 歌.wav --lyrics 歌.lrc -o out.ttml # 转换（默认 clock=HH:MM:SS.mmm）
python main.py --check-only out.ttml --strict-time        # 严格校验时间表达式
python main.py --check-only out.ttml                      # 宽松校验（AMLL 私有写法只给警告）
python tools/inspect_ttml.py out.ttml                     # 体检：x-bg / x-duet / agent / 整行背景行
python tools/inspect_duet.py refs/参考.ttml               # 对唱格式体检（拿到 Apple 参考文件先用它）
python tools/verify_amll_parse.py outputs/*.ttml          # **用真 AMLL 解析器验能不能显示**（改结构后必跑）
python main.py --audio 歌.wav --lyrics 歌.lrc --markers both --auto-agents --separate-vocals

# 对唱：优先复用社区已有的人工标注（音色接近的对唱靠声纹分不开，见 §4.2）
python main.py --audio 歌.wav --lyrics 歌.lrc --mode apple \
    --agent-from 参考.ttml --artist 陶典 --artist hanser -o out.ttml

# 对唱分栏：靠不同的 ttm:agent 触发（真实 AMLL DB 文件里没有 x-duet-*）。
# 角色只挂在 <p> 上，没有"写在哪一层"的开关了 —— 包成 span 会让歌词整片空白（铁律 #15）。
python main.py --audio 歌.wav --lyrics 歌.lrc --mode apple --markers role -o out.ttml

# 反解 + 规范化（编辑台地基，P0）
python -c "from ttml_reader import parse_ttml, doc_to_ttml; from ttml_normalize import normalize_timeline; \
d=parse_ttml(open('out.ttml',encoding='utf-8').read()); print(normalize_timeline(d).summary())"
```

---

## 4. 当前已知问题 / 待办（改代码优先看这里）

1. **背景人声曾经整行独立 → 播放器看不见**：已在 `preprocess.merge_background_lines()` 修复
   （整行括号并入相邻主唱行，跨度上限 `BG_MERGE_MAX_SPAN=12s`）。
   验证：
   ```powershell
   python tools/inspect_ttml.py <输出>.ttml      # "整行只有 x-bg 的 p" 必须为 0
   python tools/test_bg_merge.py                 # 回归用例（含真实 LRC）
   ```
   **合并只是第一步**，合并会连带两个坑，两个都已经踩过并修好（`pipeline._line_span` +
   `ttml_normalize.clamp_units_into_line`，见 §1 规则 13 与 `docs/竞品调研与优化方案.md`）：
   * LRC 里和声与下一句常常只差 0.2 秒（交错标注），若按"下一行开始"截窗口，
     几个和声字会被挤进 0.2 秒里 → **和声层字时间重叠**；
   * 行尾装不下时的"压窗口"补救会把多个字压成同一时间 → 同样报重叠。
   现在：含背景的行按整行字数估窗口、不被下一行截断；字装不下先整组回推、再不行就让行尾长出去。
   参考样例：`outputs/在出发之前.ttml`（32 行、5 处和声并进主唱行、0 错误）。
   注意：**旧生成的文件仍是旧格式**，要看效果必须重新生成。
   `ttml_check.py` 现在也会对"整行只有 x-bg"发警告，收到警告就说明又回退了。
2. **对唱分声部**（2026-10-09 用人工真值彻底重测后**推翻旧结论**，详见下方"分声部实录"）：

   | 做法 | 与 AMLL DB 人工真值的一致率 |
   | --- | --- |
   | ~~Demucs 人声轨 + 窗口平均声纹~~（旧实现） | 54.5%（= 瞎猜） |
   | **原始混音 + 整行单次前向 + k-means**（现实现） | **90.6%**（29/32 行） |

   两个致命 bug 已修：
   * `speaker` 的输入音频错了 —— 必须用**原始混音**，不能用 Demucs 分离轨
     （实测分离轨把共振峰细节平滑掉，掉 37 个点）；
   * `diarize.line_embedding` 原来把每行切成 1.5s 窗口分别提声纹再**平均**
     —— 一行之内音高/音区变化大，平均会把身份方向抵消掉（不管用哪条音频都只剩 50%）。
     现在整行一次前向；`mode="window"` 保留只为对照。
     还有个反着来的细节：旧实现"丢掉低能量窗口"，用的若是**原始混音**，
     留下的恰恰是**伴奏最响**的窗口。
   * 强制人数时用 **k-means**，不用 `_force_k` 的平均连接贪心（后者会退化成 31/1，
     实测 56.2% vs 90.6%）。

   **人数（分几瓣）只能来自元数据，音频内部判不出来**（三种判据全部失败，已实测）：
   * 平均连接阈值扫描：最好 59.4%；
   * "分离质量"（gap / silhouette / inertia 降幅）：**单人歌强判 2 簇反而比真对唱更高**
     （+0.254 vs +0.127）——这个指标根本不能用来判断"是不是两个人"；
   * 成对相似度的 BIC 双峰检验：真对唱判 1 个成分、单人歌判 2 个成分（完全反了）。

   ⇒ 规则：**元数据写了 ≥2 位艺人就按 2 个声部聚类**（`pipeline` 已实现），
   否则按单人。判错了用户可用 `[v1]`/`[v2]` 标记或 `--split-singers N` 覆盖，
   少量错行（本次 3 行、且**连续**）丢进编辑台改最快。

   **已实测否决的两个"改进"（别再试）**：
   * **孤立行平滑**（假设"歌手成段交替"，把左右邻居都与自己不同的行翻成邻居的值）：
     全曲只有 6 行是孤立的，翻它们**反而掉分** —— 基线 85.0% → 去孤点 83.8%、
     中值滤波3 83.1%、中值滤波5 77.5%。**对唱本来就有一句一换的**，这个假设不成立。
   * **基频（F0）当判据或裁判**：单独按中位阈值分类只有 62.5%/78.4%/63.6%/63.6%
     （对 4 首）；只在"声纹余量 < 阈值"时用 F0 投票，最好也只 **85.6%（+1 行）**，
     阈值从 0.06 放到 0.10 就变 84.4%、0.15 变 81.9% —— **1 行的收益落在噪声里，
     那种阈值就是在过拟合单个样本**。用户报的〈在出发之前〉「目光的方向」正是那 1 行
     （声纹 c0=+0.756 / c1=+0.696，判给 v1，真值 v2；**不是临界抖动，是嵌入本身就偏**）。
   * ⇒ 单行错归属**用编辑台点一下最快**（或 `--agent-from` 复用人标注）。

   其他可靠路径（优先级从高到低）：
   1. `--agent-from 参考.ttml`：复用社区已有的人工标注（实测 32/32 行逐行一致）；
   2. 歌词里加 `[v1]`/`[v2]` 标记 + 演唱者分段选「关闭」；
   3. 把「演唱者分段」设为**强制 N 声部**（CLI `--singer-mode 2|3`）；
   4. `--split-singers N`（**旧参数**，等价于 `--singer-mode N`）。
   详见 README「多人演唱」一节与规范文档 §7.2。
3. **演唱者分段只有 4 个选项，没有"自动判断人数"**（2026-10-11 改）：
   音频里判不出到底几个人（三种判据全部失败，见上），所以人数**由用户明确给**：

   | 选项 | `singer_mode` | 行为 |
   | --- | --- | --- |
   | **关闭**（默认） | `off` | 不跑声纹，直接用歌词里的 `[v1]/[v2]` 标记；没标记就全 v1 |
   | **强制单声部** | `1` | 全曲 `ttm:agent="v1"`（把标记/声纹的结果一律收成 v1） |
   | **强制双声部** | `2` | CAM++ 声纹聚成 2 簇 |
   | **强制三声部** | `3` | CAM++ 声纹聚成 3 簇 |

   * 解析入口统一是 `ConvertOptions.forced_singers()`：**0 = 关闭**，N = 强制 N 簇。
   * ⚠️ `--auto-agents` / `--split-singers` 保留为**旧参数**，只在 `singer_mode`
     没给有效值时才生效（`--auto-agents` → 2，`--split-singers N` → N），
     所以 `tools/eval_diarization.py` 的 85.0% 基线不受影响。
   * ⚠️ **强制 N 会覆盖歌词标记**（"强制"就是强制）；想按标记分就选「关闭」。
     覆盖时**必须打印一句说明**，否则用户会以为标记丢了。
   * ⚠️ **`duet_hint()` 的原因文案必须照实说**：关闭声纹时根本没跑声纹，
     还写"声纹判定为单人演唱"就是误导（真踩过，自检第 32 组守着）。
   * 验证：`python selftest.py` 第 32 组（模式解析表 + 关闭模式 + 文案）；
     `node tools/test_ui_runtime.js` 场景 12（四个选项各选一遍，验请求里的 `singer_mode`）。
     端到端实测：`--singer-mode 3` 在《在出发之前》产出 **v1 8 行 / v2 9 行 / v3 15 行**，
     格式 0 错误。
4. **对唱/分栏的口径**：Apple Music 的左右分栏靠不同的 `ttm:agent`（真实 AMLL DB 文件里
   **完全没有** `x-duet-*`，只用 `v1`/`v2`）。如果确实要写 Apple 内部角色，
   **只能挂在 `<p>` 上**（`--markers role|both`）；包成 span 会让歌词整片消失（铁律 #15）。
   ⚠️ **全曲只有 v1 就一定不会分栏** —— 多艺人却判单人时 `pipeline.duet_hint()` 会当场警告。
5. **歌词文件编码**：中文 Windows 上的 `.lrc` 大量是 **GBK/GB18030**，写死
   `encoding="utf-8-sig"` 会让整个转换崩在
   `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xce ...`。
   现在所有读用户文本的地方都走 `textread.read_text*()`（BOM → 严格 UTF-8 → gb18030 → 兜底），
   并会把识别到的编码写进日志。**新增读文件的地方请一律用它**。
   已知边界：Big5（繁体）会被 gb18030 静默误解成乱码 —— 已用合理性检查标成"可疑"并提示，
   要真正支持得上统计检测库。
6. 旧 `outputs/*.ttml` 里可能是 `MM:SS.mmm`（新校验器在 `--strict-time` 下会报错），重新生成即可。

### P0 地基已完成（2026-10-09）

* 新增 `ttml_model.py` / `ttml_reader.py` / `ttml_normalize.py`，`matcher` 的三个时间轴后处理
  函数已抽成纯函数并被两边共用（生成链路行为不变）。
* 往返不变量（`parse_ttml(write(doc)) == doc`、写入幂等）、R1–R7、和声重叠例外、行锁，
  全部写进 `selftest.py` 第 13 组。
* 验收：两份真实产物 `normalize_timeline(...).fixes == []`（说明正常产物不该被"修"）；
  人为把某字提前后只报那一个字。

### P1 编辑台已完成（2026-10-09）

* `edit_api.py`（纯逻辑）+ 三个端点：`GET /api/edit`、`POST /api/edit`、`POST /api/validate`；
  `index.html` 里有编辑面板，两个模式：
  * **⏱ 时间轴**：点字改时间 → 节流 300ms 自动校验 → 保存；
  * **👥 声部（谁唱的）**：点行上的 `v1/v2/v3` 改这一行谁唱；**点行号选起点、再 Shift 点终点
    可批量改一段**（声纹分错的常是连续几行），另有分声部概览色条一眼看交替。
    改 agent 时角色（`x-duet-a/b`）同步跟着走，不会写出自相矛盾的行。
* 编辑态落盘 `outputs/<id>.edit.json`（原子写），重启后 `source=disk` 恢复；
  文件损坏降级为从 TTML 重建（`source=draft`），**不 500**。
* 错误契约 `{error, where, detail, hint}` + `code`；`do_GET`/`do_POST` 都兜底，
  任何 `EditError` 都变成结构化 JSON 而不是断连接。
* ⚠️ **乐观锁必须拿"存储版本 vs 提交版本"比**，不能拿提交体里的 `doc.version` 自比
  （那样永远相等）。`EditError.code = 409`。
* ⚠️ `EditDoc` 的 JSON 字段是 **`begin`/`end`**（`start` 只是 Python 侧别名），前端别取错。
* ⚠️ **保存必须"只改用户改的"**（真实踩过的 bug）：`make_writer` 曾把
  `mode="amll"` / `markers="agent"` 写死，保存 Apple 模式文件时会把
  `<p ttm:role="x-duet-a">` **整个删掉**——那正是 Apple Music 左右分栏依赖的东西；
  还会凭空加 `itunes:songPart`、把 `v2` 的 `type="other"` 改成 `person`。
  现在 `parse_ttml` 会把"源文件当初怎么写的"记进 `EditDoc.write_options`
  （mode/markers/line_id/time_format/translation_style/song_part/label_sections），
  回写照原样；`agent_types` 也带回去。三份真实产物现在**逐字节往返一致**（自检第 20 组守着）。
  另：`parse_ttml` 以前**没读行级 `ttm:role`**，这也是角色丢失的原因之一，已修。
  已知残留：源文件 `<span ttm:role="x-bg">` 若没写 begin/end，回写会补上（属规范化，
  我们自己的写器一直写时间，规范说这段可选）。
* 验证方式：
  ```powershell
  $env:L2T_PORT="8793"; python web_server.py          # 起服务（另开窗口/后台）
  python tools/test_edit_api_e2e.py                   # 端点闭环（含真解析器复检）
  python tools/test_agent_edit_e2e.py                 # 手动改声部 → 保存 → 保真
  python selftest.py                                  # 17/20 组是编辑台
  ```
* **已做（P2 第一项）**：编辑台**音频试听**——`GET /api/audio?id=` 带 **Range**（206/416、
  支持 `bytes=a-b` / `bytes=a-` / `bytes=-n`、`Accept-Ranges: bytes`），路径经
  `safe_audio_path()` 收敛在 `uploads/` 内防穿越。前端 `<audio>` + 点字跳转 + rAF 实时高亮
  + 循环当前行 + ⟲3s/3s⟳。高亮按 `begin/end` 二分查找，不做 DOM 测量。
  ⚠️ **Range 必须支持**：`<audio>` 拖进度条和 `currentTime = x` 都靠 206，否则 50MB 的 wav 每次 seek 整份重下。
  验证：`tools/test_audio_endpoint.py`（9 组）+ 自检第 25 组。
* **播放定位高亮**（两个界面都有，统一**青色 `#22d3ee`**）：
  时间轴 = 当前字青底青光 + 当前行淡青底/左竖边；声部界面 = **逐字渲染**（`.ed-aword`）
  所以点任意一个字都能跳转播放，当前字同样高亮，当前行有独立样式（`.agent-line.now`）。
  **播放器上还有一行读数**：`当前：字 · 00:12.400 · 时长 0.140s · L12 · v1 · ⚠插值`——
  长歌词里光靠颜色不一定看得准，有读数最直观。
  ⚠️ **`#editorBody` 只有 420px 高（`max-height:420px; overflow:auto`）**：
  不跟随滚动的话，高亮**会滚出可视区 = 等于看不见**。所以有「跟随滚动」开关（默认开），
  `scrollLineIntoView()` **自己算 `getBoundingClientRect` 增量只滚这个盒子**，
  不用 `scrollIntoView`（那会把整个页面也带着跳）。

  **三条硬约束（需求驱动的，自检第 25 组强制）**：
  1. **状态标识只有一个判定源 `wordStatusClass()`**，两个页面共用 —— 各写一套必然漂移
     （真发生过：时间轴是 `else if` 互斥链、声部是三元累加，同一个字在两页归类不同）。
     **优先级：插值 > 低可信 > 背景和声**，警告压过"这是和声"——
     否则一个插值的和声字显示成绿色，**把最该人工看的错误藏起来**。
  2. **高亮不能改变盒子尺寸**（不能加 `font-weight`/`font-size`/`padding`/`margin`/
     `border-width`）。粗体更宽 → 每换一个字整行重排，密集歌词里画面一直抖。
     用 `background` + `outline`（`outline` 不占布局）+ 提亮文字色。
  3. **状态标识在两个页面同等可见**：时间轴用"底色 + 边框色"，声部逐字不能加边框
     （会撑开行高），所以用 **底色 + `text-decoration` 的颜色**表达同样语义
     （底色 = 时间轴底色，下划线色 = 时间轴边框色），且 `text-decoration` 不改布局。

  ⚠️ **六条踩过的坑，改这块前先看**：
  1. `.editor-player` 写 `display:none` 却没人打开它 → 编辑台里根本看不到播放器；
  2. `.ed-w.now` 写在状态色 `.ed-w.is-bg/.is-weak/.is-interp` **之前**，同权重后者胜出
     → 高亮**恰好在最该看的字上不显示**。现在用 `.ed-words > .ed-w.now` 拉高类数，
     **不依赖源码顺序**；
  3. 高亮原来用绿色 `#6cd97e`，和"背景和声"的绿几乎一样 → 看不出在唱哪里。现在统一青色；
  4. **重复的 `let` 声明会让整段脚本失效、编辑台全死**；插入新函数时还顶掉过
     `function fmt(t) {`。自检用 `node --check` 做真语法检查；
  5. `.ed-w.now` 加过 `font-weight:700` → 每换一个字整行重排（见约束 2）；
  6. **`indexWords()` 里清空 `wordEls`/`lineEls` → 播放高亮完全不亮**（最严重的一个）。
     两个渲染器都是"先建 DOM 并登记元素、最后调 `indexWords()`"，
     在里面 clear 会把刚登记的元素全丢掉，`wordEls[wk]` 变 undefined，
     `classList.add('now')` **静默失败**。元素地图只能由渲染器自己清。
  7. 同源问题：重建 DOM 后 `nowWordKey` 仍是旧值 → `tickHighlight` 认为"没变化"而跳过
     → **切标签页/保存后高亮消失**，直到播放跨到下一个字。
     现在统一走 `syncHighlightAfterRender()`（先清两个 key，再 `indexWords` + `tickHighlight`）。

  ⚠️ **静态检查测不出上面 6、7 这类"看着没生效"** —— CSS 在、语法对，照样一个字不亮。
  必须有**真运行时测试**：`tools/test_ui_runtime.js` + `tools/ui_dom_stub.js`
  在 Node 的最小 DOM 里执行页面真实脚本，断言"播到 t 时哪个字拿到 `.now`"。
  它第一次跑就抓到 6、7 和"读数显示 undefined"三个真 bug。
  已接进自检第 25 组（没装 node 则跳过）。
* **音频跨重启**：`_convert` 会写 `outputs/<id>.src.json`（音频/歌词的绝对路径）。
  服务重启后 `TASKS` 是空的，但编辑态能从磁盘恢复，音频也要能 —— `_audio_from_sidecar()`
  就是干这个的，否则会出现"编辑台打得开、但没声音、播放高亮不动"。
  ⚠️ sidecar 里的路径**照样过 `safe_audio_path()`**（只在 uploads/ 内）；task id 里带
  `/`、`\`、`..` 一律拒绝。
  验证：`python tools/run_restart_test.py`（**真的杀进程再起一个**，不是模拟）。
  ⚠️ 测重启**必须换进程**：在测试进程里 `W.TASKS.pop()` 只改了测试进程自己的副本，
  服务器那份还在，HTTP 照样 200 —— 这是上一版的假通过。
  ⚠️ 停服务用 `Popen.terminate()`，**不要用 `CTRL_BREAK_EVENT`**：它发给整个控制台
  进程组，会把跑测试的 shell 一起打断（实测把 PowerShell 打进了调试模式）。
* **句尾拖音补全**（`tail_extend.py`，流水线 5.7，CLI `--extend-tails` 默认开）：
  分句时 `_line_span` 把每行结束**无条件**卡在下一行开始，歌手拖长音时尾字被硬切。
  实测 4 首 218 行里 51 处（24%）卡在下一行开始，其中 **73% 的边界上人声能量没下降**。
  做法：从**分离人声轨**算滑动 RMS 包络，尾字往后延伸，**同歌手不越过下一行、不同歌手允许重叠**。
  ⚠️ **不要动 `_line_span` 本身**：那里声纹还没跑，根本不知道谁是哪个 agent。
  ⚠️ **只修尾字 <0.30s 的行**（`short_thresh`）：无差别延伸实测会把正常行补过头。
  ⚠️ **拖音长度无法外部验证**：QQ 的 `.qrc` 逐字真值**自己也截断拖音**（146 个行结束点里
  **63% 能量未降**，我们 73%）。所以"更像 QRC"≠"更对"。能站住的只有：
  尾字 <0.25s 的行 **49 → 16**、<0.15s 的 **20 → 6**。
* **拖音判据已升级为「音高连续性」**（`tail_extend.sustain_end`，2026-10-10）：
  纯能量**分不清**"上一句还在拖音"和"下一句已起唱"（两者都高能量、都"有声"）。
  改用基频：**拖长音的音高在一段时间内稳定**（转音只是缓慢游走），**新句子是逐字跳变的**。
  * 与"最近 0.3s 已确认音高的中位值"比，偏离 >250 音分算离开这个音；滚动中位允许**缓慢转音**；
  * ⚠️ **必须用 `conf` 过滤 + 要求连续 `confirm=3` 帧都跳出音域**才算换音。
    单个低置信野值不该终止一个音 —— 实测〈我不曾忘记〉「膀」在 75.630 被检成
    **421Hz**（真值 ~235，八度错），音分差 +1111，直接把拖音判成 0。
    更阴的是**同一段音频整曲算 conf=0.50、切片段算 conf=0.39**，靠单一阈值必然翻车。
  * ⚠️ **音高判据优先，绝不能 `max(能量, 音高)`**：能量判据一旦下一句开唱就一路不降，
    会把尾音延到下一句身上（实测「膀」被延成 2.28s，吞掉 v2 整句）。
    只有"尾字 <`short_thresh`（明显被压扁）"时才用能量兜底。
  * ⚠️ **参考音高要攒够 `min_ref`(=5) 帧才能用来判"离开这个音"**：
    只有 1~2 帧时，第一帧本身可能就是野值。真踩过第二个病例——
    〈我不曾忘记〉「会想和你重逢」的「逢」：**150.650 那一帧被检成 153.8Hz**
    而 conf=0.56 刚好过门槛 → 参考被污染成 153.8 → 后面真实的 228.6 全被判成
    "跳出音域"，3 帧就 break，拖音变成 **0**。中位数要 ≥3 个样本才抗单个野值。
  * **两个用户病例都已验证修好**（都是 A 拖长音、B 已起唱的叠唱场景）：
    | 病例 | 改前 | 改后 |
    | --- | --- | --- |
    | 「落在我的肩膀」尾字**膀** | 74.998→75.580（0.58s） | 74.998→**76.080**（1.08s） |
    | 「会想和你重逢」尾字**逢** | 150.140→150.620（0.48s） | 150.140→**150.840**（0.70s） |
    两处都**越过了对方声部下一行的起点**，正确形成叠唱（前端同时高亮两行）。
  * ⚠️ **末行（没有下一行）要用 `max(能量, 音高)`，上限放到 `last_line_max=8s`**：
    没有"下一行"会被盖掉，**能量的自然跌落就是终点**。真踩过第三个病例——
    〈空心〉(Live) 结尾「把我当成一棵树吧」的「吧」**拖了 7.35s**（255.67→263.03，
    能量到 262.67 才掉），而那个 Live 录音的基频**在八度之间来回跳**
    （`372 410 276 139 139 276 … 125 124 123 246 250 124`），音高判据只给 **0.08s**；
    再加上 `short_thresh=0.30` 门槛（「吧」0.46s 不小于 0.30）和 `max_extend=2.5s`，
    最后只留了 **0.46s**，切掉 6.9 秒。现在 253.450→**263.150**（+7.02s）。
  * ⚠️ **试过但失败的方案，别再走一遍**：想用一个"音高轨迹可不可信"的分类器
    自动在音高/能量之间切换。试了跳变占比、p90-p10 跨度、p75-p25、MAD、SD、
    超出 ±250/400 音分的比例 —— **没有一个能把好坏病例分开**
    （「逢」的 p90-p10 是 1517 音分，「空心「吧」」只有 1344；「空心」的 MAD 是 0）。
    原因：0.8s 的窗口**把"音高的结束点"也包进去了**，好病例的窗口里本来就有换音那一刻。
    所以最终取了**无歧义的结构性判据**（末行 vs 有下一行），而不是给"可信度"硬调阈值。
  * **诊断工具**：`python tools/case_report.py <ttml> <行内关键词> --stem <歌名>`
    把该行的人声包络 + 基频 + 两个判据的结论全部打出来。
    ⚠️ 分析时必须**统一时间轴**：RMS 包络 hop=20ms、F0 帧 hop=10ms，
    用同一下标取两个数组会"看出"一个根本不存在的稳定音高平台（我自己踩过）。
    ⚠️ 也**不要**用"我们的结束点到下一行开始之间还有没有能量"当指标 ——
    那中间多半是**下一行自己在唱**（同歌手尤其如此）。
  * **三个用户病例都已验证修好**（前两个是叠唱，第三个是 Live 末行长拖音）：
    | 病例 | 改前 | 改后 |
    | --- | --- | --- |
    | 「落在我的肩膀」尾字**膀** | 0.58s（74.998→75.580） | **1.08s**（→76.080） |
    | 「会想和你重逢」尾字**逢** | 0.48s（150.140→150.620） | **0.70s**（→150.840） |
    | 〈空心〉「把我当成一棵树吧」尾字**吧** | 0.46s（255.670→256.130） | **7.48s**（→263.150） |
* **同歌手去重叠必须按 agent 追踪**（`_enforce_agent_monotonic`）：叠唱时时间线上是
  v1/v2 交错（L34 v2 → L35 v1 → L36 v1 → L37 v2），**只比相邻条目会漏**——
  实测 L34(v2) 拖到 124.650、L37(v2) 从 114.530 开始，报"同一演唱者 v2 行时间重叠"。
  现在按 agent 记录"上一个同 agent 的行"来比。
* **制作人员行过滤**（`_is_credit_line`）：漏掉的三类会挤在 0.13/0.18/0.22 秒被当歌词强对齐
  → 产出重叠坏行。补了 `EMPTY_LABEL_RE`（`演唱：` 冒号后为空）和 `CAST_LINE_RE`
  （`迪娜泽黛 —— 半甜气泡安小琪` 这种角色—配音演员表）。
  实测《我不曾忘记》：**格式错误 3 → 0，字级匹配率 86.4% → 93.8%**（制作信息原来在污染对齐）。
  自检第 27 组（含"真歌词不许误杀"的反向用例）。
* **`tools/eval_word_timing.py`**：用 QQ `.qrc`/网易 `.yrc` **逐字时间戳**当外部真值
  （`.tmp/wordlevel/` 缓存）。⚠️ 必须先"验证尺子"：仅当音频与 QRC 来源**同一录音**时才可比
  （艺人署名一致）；且偏差要**去掉整体平移**再看散布，否则会把"录音不同的整体偏移"
  误当成"打轴不准"。`让风告诉你` 中位 −0.228s 其实主要是整体偏移。
* **双人叠唱并行高亮 + 咬字/拖音两阶段**（`findWordsAt` / `currentWordsAt` / `wordPhase`）：
  * **并行高亮**：同一时刻可能有多个字在唱（v1 拖长音、v2 已起唱），
    按"每行取一个"返回，两个页面同时亮各自声部的当前字，播放器读数形如
    `当前：v1 尽（拖音） · v2 我 · 00:01.800 · …`。视图跟随改成
    `scrollLinesIntoView`（**保证所有当前行都在视野内**，只看第一行会让下面声部滚出可视区）。
  * **两阶段**：用该行的**中位字长**判断 —— 超过中位字长的部分就是拖出来的音。
    咬字段用实底色 + 描边，拖音段（`now-tail`）用更淡的底色、不描边，
    既体现完整时长又不跟主进度抢注意力。
  * ⚠️ **`wordIndex` 按 begin 排序，`end` 不是有序的** —— 不能"按前一个字的 end 提前停"。
    真 bug：`尽`1.8~3.0 排在 `等`2.4~2.8 前面，扫到 `等` 就 break，`尽` 永远不亮。
    必须用**后缀最大 end** `wordMaxEnd[i] = max(e[i..])` 才能安全早退。
  * ⚠️ 高亮状态从"单个 key"改成**集合**（`nowWordState = {"行:字": "bite"|"tail"}`），
    `syncHighlightAfterRender` 要重置它，否则重建 DOM 后不重新点亮。
  验证：`node tools/test_ui_runtime.js`（30 项，含叠唱并行、两阶段、切页不丢、不误亮）。
* **连续处理多首歌：界面必须跟着换**（`refreshEditorIfOpen`）：
  ⚠️ `currentTaskId` 更新**是不够的** —— 编辑面板的内容是"打开时拉一次"的，
  不主动重拉就会一直显示上一首歌。转换完成（`pollTask` 的 done 分支）必须调
  `refreshEditorIfOpen()`：先把 `editDoc`/`wordIndex`/`wordEls`/`nowWordState` 全清掉，
  再 `openEditor()` 按新 id 重拉（不清就会残留"63 行 / 471 字"这类上一首的数字）。
  ⚠️ 清空与重拉之间是异步的，**这中间的渲染不能崩** —— `renderEditor`/`renderAgentLines`
  都必须有 `if (!editDoc) return;`（真踩过：测试里直接抛 TypeError）。
* **文件名 → 歌名/艺人的自动识别**（`splitTitleArtists` + `fillIfAuto`）：
  两个真踩过的坑：① 以前**只在字段为空时才填** → 处理第二首歌时字段里还是上一首的值，
  **永远不更新**；② 以前写死"歌名 - 艺人"顺序 → 遇到"艺人 - 歌名"就**把两者调换**。
  现在：记住上次**自动**填入的值，只有字段为空或仍等于旧自动值才覆盖（不擦用户手输的，
  但换歌一定会更新）；顺序按「含 `(Live)`/`feat.`/现场 的一侧是歌名」
  「含多个名字分隔符（`&`/`、`/`_`/`,`）的一侧是歌手」判断。
  ⚠️ 两边都没有线索时是**本质歧义**（`于文文 - 体面` 与 `体面 - 于文文` 长得一样），
  默认取音乐文件下载的通行约定 **`艺人 - 歌名`**（如「周杰伦 - 晴天.mp3」）。
  验证：`node tools/test_ui_runtime.js` 场景 9/10（真实文件名 + 换歌 + 不覆盖手输）。

### P3 编辑台文本编辑 + 和声编辑（2026-10-10）

原歌词常**缺行、错字**，人工精修必须能改文字；和声（`x-bg`）虽然自动生成准确度尚可，
但错了也得能手改。三处新增：

* `edit_text.py`（**纯函数改 `EditDoc`**，不碰 HTTP）：`set_word_text` / `insert_word` /
  `delete_word` / `set_line_text` / `insert_line` / `delete_line` / `set_bg_text` /
  `remove_bg`，统一入口 `apply_op(doc, op, payload)`。
* `POST /api/text`（`web_server._post_text`）：与 `/api/edit` 同一套乐观锁 + 结构化错误；
  一次调用 = 改模型 + `normalize_timeline` + `check_ttml` + 原子落盘，返回新 doc/version。
* `index.html` 第三个标签 **✏️ 文本**：每行一个整行输入框 + 一个和声输入框 + 逐字 chip。
  **点字改字、字间 ＋ 插字、字上 × 删字、右侧 ＋ 下方插行 / ✕ 删行**。

**三条不变量（每个操作都必须守住，自检第 31 组逐个验）**：
1. **层内不重叠、时间单调**：同层的字按文本顺序时间递增且 `begin < end`。
   插入新字**从相邻字里切一半**（有后字切后字前半，否则切前字后半），
   **绝不能"随便给个时间"** —— 那会立刻产出 `字时间重叠` 的坏文件。
2. **`span ⊆ p`**：新字区间必须落在行内；不够就**撑开行尾**（铁律 #13）。
3. **key 连续**：`itunes:key` 由写入器按 `doc.lines` 顺序自动编号（`enumerate(lines,1)`），
   所以插行/删行**只要改列表顺序**就够了；`rekey()` 只是让编辑台显示正确。

⚠️ **和声不需要单独的分组结构**：写入器把一行里**所有 `is_bg` 的字自动包成一个
`<span ttm:role="x-bg">`**（括号也是自动加的，见 `ttml_writer._p_inline`）。
所以"和声编辑"就是编辑该行 `layer=bg` 的字，`set_bg_text` 直接把该行和声层整体替换。

⚠️ **新加的字一律 `matched=False`**（没有 ASR 证据），编辑台会按"插值"标红 ——
这是对的：人工补的字本来就该被看一眼。

⚠️ **整行清空、删掉最后一行都要报 `TextEditError`**：前者会写出没有文字的 `<p>`
（AMLL 里就是空行），后者让文档没法导出。前端也拦一道（空文本不发请求）。

验证：`node tools/test_ui_runtime.js` 场景 11（29 项，含"请求带乐观锁版本""空行不发请求"）；
`python selftest.py` 第 31 组（每个操作都验不变量 + 0 错误 + 往返）。

### P4 参数联调 + 平台 ID + 编辑台体验（2026-10-11）

* **平台 ID 增加酷我/酷狗**（`kuwoId` / `kugouId` → `amll:meta` 的 `kuwoMusicId` /
  `kugouMusicId`）。⚠️ **这两个键不是 AMLL 官方定义的**（官方只有 isrc / ncmMusicId /
  qqMusicId / appleMusicId / spotifyId）—— 我们的写器会原样保留，真 AMLL 解析器
  **验证过会忽略它们、不影响歌词显示**，但别的软件不认，别指望通用。
  ⚠️ `web_server` 的平台 ID 列表**从 `ttml_writer.META_KEYS` 取，不要另抄一份** ——
  抄一份的下场是加平台时只改了写器、这里忘了加，界面填了 ID 却写不进文件。
* **多平台 ID 检索：默认不做自动回填**。① 本项目卖点是**离线、无需 API Key**，
  接第三方检索就打破了；② 更要命的是**检索会搜错歌**（同名/翻唱/Live 遍地），
  **填错的 ID 比空着更糟**（指向另一首歌）。所以界面做的是
  **「🔍 各平台搜索」一键跳转**（零依赖、永远可用，选哪首留给人）。
  真自动回填走 `L2T_METING_API`（自建 [Meting-Agent](https://github.com/ELDment/Meting-Agent)）
  —— `/api/lookup` **默认明确回"未配置"，绝不假装查过**；前端**只填空字段、不覆盖手填**。
* **语言加了「自动检测」（界面默认）**：映射到 whisper 的 `language=None`。
  ⚠️ 写死 `zh` 会让**中英混唱**的英文段按中文解码、明显掉字。
  `asr.is_auto_language()` / `whisper_language_code("auto") == ""` 是判据；
  `xml:lang` 用**实际检测到的语言**（`asr.last_language` + `pipeline._ttml_lang()`），
  ⚠️ 绝不能把 `auto` 原样写进 `xml:lang`（不是合法 BCP-47）。
  **命令行默认仍是 `zh`**（不改动评估基线，README 有说明）。
* **删掉重复的脏话选项**：`mutedSwitch`（检测被消音词）与 `explicit_policy=mask`
  **最终效果一样**（都是把词换成 `****`），界面只留后者；
  `--detect-muted` 仍在命令行可用。
* **播放条布局**：`当前演唱文字` 从 `.editor-player`（flex-wrap）里**移出，独立一行**
  （`.editor-now`，**固定 22px 高**）。❗原因：flex 项默认 `min-width:auto` 不收缩，
  文字一长就①挤窄进度条 ②把控制条挤成两行 → 条高变化 → 下面三个页面**上下闪动**。
  定高 + 省略号 + 全文挂 `title` 才不会抖。
* **✏️ 文本编辑页也有播放高亮**（复用 `#0e3a46` + `#22d3ee`，与另两页同一套）：
  ⚠️ chip 登记进 `wordEls` 的键**必须是扁平 `line.words` 下标**
  （`indexWords` 就是这么建键的）；传"层内下标"会让**含和声的行整体错位、亮错字**。
  ⚠️ 顺带修了 `currentWordsAt`：原来**按行去重**，同一行里主唱+和声并行时只亮主唱 ——
  现在按 **行+层**去重，两层各自亮。
* **两个真 bug（这轮联调抓到的）**：
  1. `pipeline._apply_style` 用了 `tokenize_chinese` 但**没导入** → 只要有一行文字被改
     （`--style apple` / `--explicit-policy mask`）就 `NameError`、**整次转换崩**；
  2. `main.py` 的 `--language` help 里有裸 `%`（`85.0%`）→ argparse
     **`--help` 直接抛 `ValueError: badly formed help string`，整个 CLI 全挂**。
     ⚠️ argparse 的 help 是 `%`-格式化的，要写 `%%`。自检第 33 组现在会跑 `main.py --help`。
* **联调工具（都要保留）**：
  ```powershell
  python tools/audit_params.py        # ① 界面→params→后端→有没有人读（查纯UI/死字段）
  python tools/test_param_effect.py   # ② 每个选项改一遍，断言**产物真的变了**（42 项）
  ```
  实测结论：**没有孤立的纯 UI 参数，也没有"接了没人读"的死字段**。
  ⚠️ `mode`（amll/apple）作用面**很窄**：只影响 ①`markers=auto` 时解析成 agent/both
  ②apple 模式按 >4s 停顿断 `<div>`。**显式指定 markers 后它几乎不再改变产物** ——
  这是事实不是 bug，别照着"它应该大改产物"去改。

---

## 5. 背景人声/对唱的"正确形状"速查

```xml
<!-- 背景人声：嵌在同一个 <p> 内，半角括号；包装层写 begin/end（与 AMLL 参考实现一致），
     且必须包住子 span。和声在主唱之前唱时，整个 x-bg 放在主唱 span 前面 -->
<p begin="00:01:05.510" end="00:01:12.170" itunes:key="L11" ttm:agent="v1">
  <span begin="00:01:05.510" end="00:01:05.890">有</span>…
  <span ttm:role="x-bg" begin="00:01:10.950" end="00:01:12.170">
    <span begin="00:01:10.950" end="00:01:11.110">(一</span>
    <span begin="00:01:11.110" end="00:01:11.890">路</span>
    <span begin="00:01:11.890" end="00:01:12.170">行)</span>
  </span>
</p>

<!-- 对唱：不同的 ttm:agent；head 里都要定义（有名字就写 ttm:name） -->
<ttm:agent type="person" xml:id="v1"><ttm:name type="full">陶典</ttm:name></ttm:agent>
<ttm:agent type="person" xml:id="v2"><ttm:name type="full">hanser</ttm:name></ttm:agent>
<p begin="…" end="…" itunes:key="L12" ttm:agent="v2">…</p>

<!-- Apple 内部角色（--markers role|both）：**只能挂在 <p> 上**。
     ⚠️ 绝不要写成 <span ttm:role="x-duet-b"> 把主唱 span 包起来 ——
     AMLL 解析器对未知 role 走 processWordElement，它不递归子节点，
     整行会变成一个词、逐字时间轴全丢，播放器里一片空白（铁律 #15）。 -->
<p begin="…" end="…" itunes:key="L12" ttm:agent="v2" ttm:role="x-duet-b">…</p>
```

**歌词里的演唱者标记（`[v1]`/`[v2]`/`[和声]`…）必须在合并背景人声之前剥掉**：
`[v1]（一路践行）` 这种和声行，分词后 `v1` 也会被判成背景 token，于是整行会被并进主唱行；
等合并完再剥就晚了 —— `[v1]` 已经在行**中间**，行首匹配不到，最后渲染成 `(v1一路践行)`。
实现见 `preprocess._strip_marker()`（在 `parse_lyrics` 里逐行调用），
`detect_agent_switch()` 会沿用已剥出来的 agent/role，不会覆盖回默认值。

### 标记 → 规范语义对照（AMLL 规范 §4.1 / §7.2 / §7.3）

| 标记 | 变成什么 | 依据 |
| --- | --- | --- |
| `[v1]` `[男声]` `[主唱]` | `<p ttm:agent="v1">` | §7.2 对唱 = 不同 `ttm:agent` |
| `[v2]` `[女声]` `[对唱]` | `<p ttm:agent="v2">` | 同上（左右分栏就靠这个） |
| `[合唱]` `[齐唱]` `[group]` | `<p ttm:agent="v1000">`，head 里 `type="group"` | §4.1 group 固定 v1000 |
| `[和声]` `[bg]` `[伴唱]` | **不是 agent**：整行并进相邻主唱行，成为 `<span ttm:role="x-bg">` 层 | §7.3 背景人声是行的子内容 |
| `[旁白]` `[独白]` | `<p ttm:agent="v3">` + `x-narration` 角色 | §4.1 + Apple 扩展 |

`ttm:name` **只给 person/character/organization**（`group`/`other` 不是某位歌手，
按顺序硬套会把歌手名安到合唱上）。

**不要**这样写（AMLL 解析后主歌词为空、看不见）：

```xml
<p begin="00:01:10.950" end="00:01:12.170" itunes:key="L12" ttm:agent="v1">
  <span ttm:role="x-bg">…</span>
</p>
```
