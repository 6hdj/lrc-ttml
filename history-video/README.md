# Lyric2TTML 介绍片（Remotion）

给 **Lyric2TTML**（普通歌词 + 音频 → Apple Music / AMLL 逐字时间轴）做的 60 秒介绍片。
纯代码生成，无外部素材；配乐也是自己合成的（见 §6）。

**1920×1080 / 30fps / 60 秒 / 1800 帧 / 十场，每场一种独立的画面形式。**

> 风格参考用户提供的 Remotion 工程《人类简史 · 史诗版》（微信收到的模板）。
> 学的只是**形式语言**，内容全部换成 Lyric2TTML 自己的东西 —— 对照见 §3。
> 完整规格（每场的镜头、时间点、token）在 **[SPEC.md](SPEC.md)**，那是唯一权威来源。

---

## 1. 跑起来

```powershell
cd history-video

npm install          # 首次（本机 ≈2 分钟；node_modules 已可就地复用）
npm run studio       # 预览：能拖时间轴、改代码即时刷新（调画面就用这个）
npm run typecheck    # 类型检查

# 单帧自检（比整片渲染快几十倍，改画面时用）
npx remotion still src/index.ts Lyric2TTMLIntro out/check.png --frame=750

# 十场各抽一张 + 整片渲染
node tools/render.cjs            # 只出十张自检图（约 1 分钟）
node tools/render.cjs --video    # 再出 out/Lyric2TTML-Intro.mp4
```

⚠️ 本机受限沙箱里 **`npx remotion render` 会 `spawn EPERM`**（Remotion CLI 内部要用管道
spawn esbuild service，而沙箱禁止"子进程 stdio:pipe"）。所以仓库里给了
`tools/render.cjs`：它走 `@remotion/bundler` + `@remotion/renderer` 的**进程内 API**，
绕开这条限制。**不受限的环境里直接用 CLI 即可，两条路等价。**

⚠️ 跑渲染前把临时目录指到工作区内，否则 Remotion 自带 ffmpeg 写系统临时目录会被拒
（实测第 78 帧报 `Permission denied`）：

```powershell
$env:TMP='<repo>\.tmp\remotion-tmp'; $env:TEMP=$env:TMP
```

---

## 2. 十场

| # | 场 | 时长 | 帧 | 画面形式 |
| --- | --- | --- | --- | --- |
| 1 | 序 | 3.5s | 0–104 | 一点光炸成星尘，标题逐字从模糊中锐化 |
| 2 | 打轴 | 8.0s | 105–344 | 巨型计时器 + 标尺扫过，逐字落下标记 |
| 3 | 声音 | 5.5s | 345–509 | 波形从左画出，逐字亮起并落到时间轴 |
| 4 | 对比 | 5.0s | 510–659 | 左右分屏：整行亮 vs 逐字亮，扫描线实时对比 |
| 5 | 结构 | 5.5s | 660–824 | TTML 标签块逐个搭出来，逐字点亮 |
| 6 | 对唱 | 6.0s | 825–1004 | 声纹点阵聚成两簇 → 左右分栏 |
| 7 | 和声 | 5.0s | 1005–1154 | 主线 + 嵌套和声轨错位叠唱（含错误示范） |
| 8 | 编辑台 | 7.0s | 1155–1364 | 编辑器界面 + 光标点字、批量改声部 |
| 9 | 实测 | 6.0s | 1365–1544 | 三根柱状 + 数字滚动（100% / 85% / 0） |
| 10 | 尾声 | 8.5s | 1545–1799 | 色彩汇聚成一点光，片名与开源信息 |

---

## 3. 从参考片学到的、以及刻意改掉的地方

**学的（形式语言）**

* 近黑底 + 一层极淡的 `radial-gradient` 环境光；
* 开场"一点光炸开成星尘 + 标题逐字从 `blur(16px)` 锐化"；
* 巨号数字配 `tabular-nums` + **前几帧残影**做运动模糊；
* SVG 描线动画（`strokeDasharray/strokeDashoffset`）+ 沿线跑的发光头 + 实时读数气泡；
* 左上角固定角标（大字标题 + 小字副标题）、右上角等宽读数、底部来源小字；
* **叠化由总装统一处理**：下一场淡上来盖住上一场，退场那场同时 `blur(18px) + scale 1.08`
  —— 这样两段居中大字不会叠成一团糊。

**刻意改掉/加强的**

| 参考片的问题 | 本片的做法 |
| --- | --- |
| 十场各写各的进场动画，同片里出现好几种"进场手感" | 抽了 `epic/shared.tsx`：`appear/blurIn/riseIn/seg` + `CornerLabel/Readout/Caption/CharRun`，**手感统一**，各场只在画面形式上求差异 |
| 时序表与总时长要人工对齐，容易改漏 | `timing.ts` 的 `SCENES` 表 + `INTRO_DURATION`，并在文件头写明"改 duration 要同步改后面所有 from" |
| 静音渲染（`BGM = null`） | 自带一段 Python 合成的器乐垫音（10 个和弦床按场次推进，段落切换处有轻低频脉冲） |
| 首帧渲染器要下 150MB Chrome | 这台机器已缓存好 `chrome-headless-shell`；`remotion.config.ts` 里不写死本机路径，保持可移植 |

---

## 4. 文件结构

```
history-video/
├── SPEC.md                 ★ 规格与时间轴（唯一权威来源）
├── package.json            scripts: studio / render / still / typecheck
├── remotion.config.ts      渲染行为（JPEG 中间帧、覆盖输出）
├── public/
│   └── bgm.wav             自合成配乐（tools/make_bgm.py 生成）
├── tools/
│   ├── render.cjs          ★ 十场抽帧自检 + 全片渲染（进程内 API）
│   └── make_bgm.py         合成配乐
└── src/
    ├── index.ts            入口
    ├── Root.tsx            注册 composition `Lyric2TTMLIntro`
    ├── constants.ts        画布 / 字体 / 调色板 / 环境光 / 角标位置
    └── epic/
        ├── timing.ts       ★ 场景表 + 总时长 + BGM 开关 + 稳定伪随机 rnd()
        ├── shared.tsx      ★ 十场共用的动画件与视觉件
        ├── Lyric2TTMLIntro.tsx  ★ 总装：按场景表挂载 + 统一叠化
        ├── Overture.tsx      1  序
        ├── Counting.tsx      2  打轴
        ├── Waveform.tsx      3  声音
        ├── LineVsChar.tsx    4  对比
        ├── Structure.tsx     5  结构
        ├── Duet.tsx          6  对唱
        ├── Background.tsx    7  和声
        ├── Editor.tsx        8  编辑台
        ├── Numbers.tsx       9  实测
        └── Finale.tsx        10 尾声
```

**改东西的时候只需要动这些**：

* **改节奏** → 只改 `epic/timing.ts` 的 `SCENES`（各场内部动画按"本场第几帧"驱动，不受影响）；
* **改某一场的画面** → 改对应的那个场景文件，其余九场不受影响；
* **改全片手感**（进场帧数、角标、环境光）→ `epic/shared.tsx` 与 `constants.ts`。

---

## 5. 三个坑（改之前先看）

1. **动画必须是 `frame` 的纯函数**。禁止 `Math.random()` / `Date.now()` / `setTimeout` /
   CSS `transition|animation` —— Remotion 会并发渲染不同帧，非确定性会让成片随机闪烁、
   而且同一帧重渲两次结果不同。要随机用 `timing.ts` 的 `rnd(n)`。
2. **场景组件内部不做淡入淡出**。叠化统一由 `Lyric2TTMLIntro.tsx` 处理，组件自己再做一层
   会变成双重叠化（画面发灰、进场变慢一倍）。
3. **改时序别超出总帧数**。动画按起始帧往后推；`duration` 改了要同步改后面所有 `from`，
   且 `INTRO_DURATION` 必须等于最后一场的 `from + duration`，否则结尾几秒是空的。

4. **最后一场的淡出归总装管**，但窗口别压到最后一帧：`Lyric2TTMLIntro.tsx` 里最后一场的
   退场原本写到 `INTRO_DURATION - 2`，等于**第 1799 帧才淡到 0 ⇒ 末帧全黑**（实跑抓到，
   用 `still --frame=1799` 一看是纯黑）。现在是 `INTRO_DURATION - 3` 收尾，
   末帧仍有画面。**改总装那两行前先渲末帧确认。**

5. **抽帧自检要避开"动画中段"**，否则会把正常过渡误判成 bug。第二版验收踩过两次：
   - 场 8 在帧 1280 看像"行宽越出面板"（其实是框选高亮动画中段），帧 1320 才是终态；
   - 场 10 在帧 155 看像"底部小字被压暗"（其实小字 148→180 正在淡入）。
   规则：**核对某一帧前，先看该元素的起止帧**；`tools/render.cjs` 的 `CHECKS` 表已刻意
   避开每场最后 20 帧的退场叠化区。

### 已知无害噪音

* 渲染时可能出现一条 `debug.log`（Chrome 的 `Failed to create directory: ...\Temp\puppeteer_dev_chrome_profile-*`）。
  那是**把 `TMP` 指到工作区内**导致的：Chrome 想在临时目录下建 profile 缓存失败，于是把错误写进了 CWD。
  它**不影响渲染结果**（成片正常、确定性复核通过），清理掉即可；已在 `.gitignore` 里忽略。


---

## 6. 配乐

`public/bgm.wav` 由 `tools/make_bgm.py` 生成（numpy 正弦叠加的和弦垫音 + 轻低频章节脉冲）：

```powershell
python tools/make_bgm.py            # 60 秒
python tools/make_bgm.py --duration 75
```

* **为什么要自己合成**：这支片要公开发布，随手拿一首歌当 BGM 会踩版权；合成的没有这个问题。
* 电平整体压在峰值 **−10.5 dBFS**，将来叠旁白也不会削顶。
* 想换成品音乐：把文件放进 `public/`，改 `epic/timing.ts` 的 `BGM = "你的文件.mp3"`；
  设成 `null` 就是静音渲染，不会报错。**`.ncm` 用不了**（网易云加密格式）。

---

## 7. 数字的出处（这一版里出现的所有数据）

| 画面上的数字 | 出处 |
| --- | --- |
| 字级匹配率 100%（字全唱对时） | `tools/eval_word_timing.py`（以 QQ `.qrc`/网易 `.yrc` 逐字时间戳为外部真值） |
| 对唱分声部 85%（与人工标注一致） | `tools/eval_diarization.py`（真值：AMLL TTML DB 人工标注） |
| 0 联网需求 / 全程离线 | 流水线设计事实（本地 faster-whisper + CAM++，无需 API Key） |

这些数字**不要为了好看改动** —— 仓库里都有可复现的评估脚本。
