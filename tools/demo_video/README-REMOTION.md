# 宣传片（Remotion 版）规格与时间轴

> 用途：把已有的 62 秒宣传片 `Lyric2TTML-宣传片.mp4` 重建为**可参数化、可重渲染**的 Remotion 工程。
> 本文是**唯一权威版式/时间轴来源**。基线实现只读参考：`tools/demo_video/slides.html`、`record.js`、`build.py`。
> 任何"改字号/改时间点"的决定先改本文，再改代码。

---

## 1. 基线是什么、为什么要重做

| | 旧方案（基线） | 新方案（Remotion） |
| --- | --- | --- |
| 画面 | 手写 HTML + 一个 `seek(t)` 全量函数 | React 组件 + `useCurrentFrame()` |
| 渲染 | Edge + CDP 逐帧截图（1.7 帧/秒，62s 要约 18 分钟） | `npx remotion render`（并行、可切片、可换帧率） |
| 改文案 | 改 HTML + 重录 1860 帧 | 改 props/常量，重渲染 |
| 预览 | 无（只能整片重录） | `npx remotion studio` 实时拖时间轴 |
| 声音 | PowerShell SAPI TTS + numpy 合成 BGM | **沿用同一份母带**（见 §5，不改音频） |

重做的动机是**工程化**，不是画面升级：观众看到的每一帧要尽量与基线一致（验收标准见 §7）。

---

## 2. 成片规格

| 项 | 值 |
| --- | --- |
| 分辨率 / 帧率 | 1920×1080 / 30 fps |
| 时长 | 62.00 s = 1860 帧（视频与音频解耦：`videoSeconds` 可独立于音频时长，见 §6） |
| 视频编码 | H.264（yuv420p，crf 19 量级），`+faststart` |
| 音频 | 44.1 kHz 立体声 AAC 192k（母带 `promo/public/audio.wav`） |
| composition id | `Lyric2TTML` |
| 字体 | 系统 `Microsoft YaHei`（**不联网取字体**） |

---

## 3. 视觉 token（照抄基线 `slides.html`）

| token | 值 |
| --- | --- |
| 背景 | `#0a0a12` |
| 正文 | `#e8e8f0` |
| 主强调（青） | `#22d3ee` |
| 次强调（绿） | `#6cd97e` |
| 次要文字 | `#9aa4bf` |
| 更暗文字 | `#6b7590` |
| 网格 | 白色 1px 线，`opacity .05`，格 80×80px |
| 光晕 1（青） | 1100×1100，`rgba(34,211,238,.16)`，`blur(120px)` |
| 光晕 2（紫） | 900×900，`rgba(124,92,255,.15)`，`blur(120px)` |
| 卡片底 | `rgba(255,255,255,.035)` + 边框 `rgba(255,255,255,.07)`，圆角 20–22px |
| 红卡（痛点） | 底 `rgba(255,90,90,.06)` + 边框 `rgba(255,90,90,.28)` |
| 青卡（逐字） | 底 `rgba(34,211,238,.07)` + 边框 `rgba(34,211,238,.34)` |
| 行内高亮块 `.box` | 底 `rgba(34,211,238,.20)`，文字 `#eafcff`，圆角 12px |
| 进度条 | 底部 6px，`linear-gradient(90deg,#22d3ee,#6cd97e)`，宽 = `t/62*1920` |
| 场景内边距 | `padding: 0 140px`，内容垂直居中、水平居中 |

字号：`h1` 116px/800（结尾 92px）、`h2` 74px/700、`.cline` 50px、`.row` 46px、`.sub` 40px、`.tag` 34px（`letter-spacing 8px`）、`.row .d`/`.stat .l` 34px、`.cap` 30px、`.stat .n` 130px/800。

动画基线语义（`seek(t)`，逐帧纯函数）：
* 场景进入：0.7 s `easeOutCubic` 淡入 + 26px 上移；场景结束后 `opacity=0`
* 逐字：`reveal(chars, p, per=0.045)`，字 `opacity 0→1` + `translateY(26px)→0`
* 列表行：`translateX(60px)→0`，0.75 s
* 数字：`scale(0.72)→1`，0.7 s
* 光晕漂移：`glow1: left=-160+120*sin(t/11), top=-260+90*cos(t/13)`；`glow2: right=-200+110*sin(t/9+1.2), bottom=-220+100*cos(t/15)`

---

## 4. 场景与旁白对齐表

旁白块是**从母带实测**的（2.5–8 kHz 高频能量 + 20 ms 帧，见 §7），不是估的。
`github.com/6hdj/lrc-ttml` 的旁白（第 6 段）落在 52.7 s，故结尾场景比其他场景更紧：**必须有选择地保留/压缩出场**（§6 给出方案）。

| # | 场景 | 时间 | 旁白 | 画面文案 |
| --- | --- | --- | --- | --- |
| s0 | 开场 | 0 – 7 | 0.9「把普通歌词，变成逐字时间轴。」(1.01–3.97) | tag「全程离线 · 无需 API Key」0.25；h1「把歌词做成逐字时间轴」1.0–3.6 逐字；sub 3.6 |
| s1 | 痛点 | 7 – 17 | 7.6「现在的歌词文件大多只有整行时间…看不出来的。」(7.68–16.62) | h2「你的歌词，现在还只能整行亮」7.2–8.8；红卡 9.4；青卡 11.6（右滑 40px） |
| s2 | 输入 | 17 – 25 | 17.6「这个工具只要两样东西…不是按字数估算的。」(17.69–22.00) | h2「只要两样东西」17.2–18.6；sub 19.4；注 21.2 |
| s3 | 能力 | 25 – 41 | 25.6「逐字时间轴、对唱分声部、背景和声，还有一个人工精修的编辑台。」(22.80–32.24 起，尾段到 32.24) | h2「它还能做这些」25.2–26.5；5 行 26.6 起每 2.3 s |
| s4 | 数字 | 41 – 52 | 41.6「实测：…85%。而且全程离线。」(41.68–49.09 + 49.94–51.21) | h2「一些实测数字」41.2–42.5；3 数字 43.0 起每 1.5 s；注 48.6 |
| s5 | 结尾 | 52 – 62 | 52.6「Lyric2TTML，开源，MIT 协议。欢迎试用。」(52.71–56.34 + 57.21–57.89) | h1「Lyric2TTML」52.4–54.4；sub 55.0；url 57.0；「MIT 开源 · 可自由使用与商用」58.6 |

⚠️ 已知与基线的小偏差（**有意保留**）：基线 s5 的 url 在旁白「开源」之后才出现、最后一句话在其后，导致"MIT 开源"文案出现在旁白读完之后。Remotion 版把 s5 的 `sub`/`url`/`MIT` 相应前移（§6），让文字落在旁白覆盖区间内。

---

## 5. 音轨（不改）

母带：`.demo/out/audio.wav`（62.00 s，44100 Hz 立体声，由 `tools/demo_video/build.py` 生成）
= 6 段 Windows SAPI「Microsoft Huihui」中文旁白 + numpy 自合成 Am–F–C–G 垫音（无版权问题），峰值归一到 0.89。

Remotion 侧**只复制不重编码**：`promo/public/audio.wav` ← `Copy-Item .demo/out/audio.wav`。
母带与基线成片**绝不可被改写**（验收项之一，见 §7）。

> ⚠️ 法务备注（写进交付说明）：SAPI 语音的音色属于 Microsoft，用于**本地演示/开源项目介绍**风险低，但若要商用分发，建议换成开源 TTS（Piper / edge-tts 等）重录 6 句旁白——文本在 `.demo/narr/narration.txt`。

---

## 6. 实现约定（Remotion）

* 每帧**纯函数**：只用 `useCurrentFrame()`/`useVideoConfig()`/`interpolate`/`spring`/`Easing`。禁止 CSS `transition`/`animation`、`Date.now()`、`Math.random()`（要随机用 `random(seed)`）。
* `durationInFrames = FPS * videoSeconds`，`videoSeconds` 默认 62，与音频时长解耦（音频短于视频时 `<Audio>` 自然结束）。
* 音频用 `@remotion/media`（或 `remotion` 的 `Audio`）挂 `audio.wav`，`volume` 走 props 便于调。
* 组件边界：
  * `src/core/theme.ts`：`COLORS`、`FONT`、`SIZES`
  * `src/core/Backdrop.tsx`：光晕 + 网格（+ 可选暗角）
  * `src/core/RevealText.tsx`：`{text, progress, per?, style?}`
  * `src/core/ProgressBar.tsx`：底部进度条
  * `src/scenes/S0Opening.tsx … S5Ending.tsx`：`{t: number}`，由 `Composition.tsx` 按场景区间挂载
* 场景切换沿用"每场景自己淡入淡出、同时只有一个可见"的模型（与基线一致，最小化视觉差异）。

---

## 7. 验收标准（`tools/verify_promo_frames.py` 自动跑）

1. **容器**：1920×1080 / 30 fps / 62.00 s / h264 + aac 192k。
2. **音频**：新片音轨解出后与母带对齐，语音块起止与母带列表一致（±0.15 s）；AAC 有损允许波形小幅差异，但不允许时长/内容改变。
3. **画面**：与基线在同一时间点抽帧比对 MAE（0.25 s 间隔，≥120 点）：
   * MAE < 3/255 → ✅ 视觉等价
   * 3–8 → ⚠️ 同版式、有细节差异（需列出最差 10 个时刻）
   * \> 8 → ❌ 必须定位到组件
4. **确定性**：同一帧渲染两次产物哈希一致。
5. **基线不可变**：`Lyric2TTML-宣传片.mp4` 与 `.demo/out/audio.wav` 的哈希前后一致。

---

## 8. 环境陷阱（都是实测踩过的）

* **npm 缓存目录**必须在工作区内：`$env:npm_config_cache="<repo>\.tmp\npm-cache"`。默认的 `%LOCALAPPDATA%\npm-cache` 会被沙箱拒绝写（EPERM）。
* **`npm install` 生命周期脚本会 EPERM**：npm 用 `child_process.spawn`（默认管道 stdio）跑 postinstall，沙箱禁止程序开命名管道 → `npm error code EPERM / syscall spawn`。用 `npm install --ignore-scripts` 解决（本项目依赖不需要 postinstall）。
* **本机没有 Google Chrome**，只有 Edge 155。Remotion 需要 chrome-headless-shell（`npx remotion browser ensure` 会自动下载）。下载目录若不可写，指向工作区内并设 `REMOTION_BROWSER_EXECUTABLE`。
* **PowerShell 5.1**：`.ps1` 含中文必须存成 **UTF-8 带 BOM**，否则按 GBK 解码直接语法错误。
* 旧方案的 Edge + CDP 逐帧截图（`record.js`）在 62 s 片子上约 **18 分钟**；Remotion 并行渲染是重做的主要收益之一。

---

## 9. 与旧流程的关系

`tools/demo_video/`（HTML + Edge 逐帧 + ffmpeg）**保留不动**：它是基线成片的可复现来源，也是"逐帧确定性渲染"这一思路的原始实现。
新流程 `promo/` 与它共享同一份音频母带与同一套版式 token；两者产出的片子应当**视觉等价**（§7-3 用 MAE 判定）。

---

## 10. 实测记录（Remotion 首次跑通）

| 项 | 数字 |
| --- | --- |
| `bundle` | 1.2–1.3 s |
| `getCompositions` | 0.4 s（浏览器已缓存）/ 31.6 s（含首次下载 chrome-headless-shell 120 MB） |
| still（热态） | **0.5–0.6 s/帧**；冷启动首次 6.4 s |
| 全片 1860 帧 | **106.6 s（17.4 fps）**，`--preset slow --crf 19`，产物 6.52 MB |
| 对照：旧流程 Edge 逐帧 | 约 18 分钟（1.7 帧/秒） |

产物：`promo/out/Lyric2TTML-promo.mp4`（h264 1920×1080 30fps `bit_rate=683775` + aac 44100Hz 2ch `189589`，`duration=62.020`）。

实测踩到的两个坑（都已修，见 §8 与下条）：

1. **ffmpeg 写临时目录被拒**：Remotion 自带 ffmpeg 往 `%LOCALAPPDATA%\Temp\react-motion-render*\pre-encode.mp4` 写会 `Permission denied`（第 78 帧挂掉）。
   ⇒ 跑渲染前把 `TMP`/`TEMP` 指到工作区内目录。
2. **音频默认升到 48 kHz**：不加参数时 Remotion 用 48 kHz 输出 aac，而基线是 44.1 kHz（母带本身 44.1 kHz，等于白升一次采样）。
   ⇒ 必须显式 `sampleRate: 44100`（CLI 是 `--sample-rate=44100`），否则音频参数与基线不一致。
3. **`duration` 62.020 vs 基线 62.000**：多出的 20 ms 是 AAC 编码器的 priming/尾随填充，**不是内容差异**（视频帧数两边都是 1860）。

### 仓库内可跑的脚本（不需要受限沙箱提权）

```powershell
node promo/tools/check-core.cjs        # 91 项：core 数值/结构与 slides.html 逐项比对（进程内 SSR，不用 esbuild）
node promo/tools/check-encoding.cjs    # 交付文件是否都是合法 UTF-8（无替换符）
node promo/tools/render.cjs            # bundle + 全片渲染（可加 --still 1110:run1.png 做确定性对比）
```

⚠️ `promo/tools/render.cjs` 与 `npx remotion render` 等价；之所以有它，是因为限权环境里
Remotion CLI 内部要用管道 spawn esbuild service，会被沙箱拒（`spawn EPERM`）。不受限环境直接用 CLI 更省事。

