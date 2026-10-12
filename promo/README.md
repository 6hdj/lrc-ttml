# Lyric2TTML 宣传片（Remotion 版）

把基线宣传片（`Lyric2TTML-宣传片.mp4`，62 秒 / 1920x1080 / 30fps）**从"HTML + Edge 逐帧截图 + ffmpeg"
改成一条可参数化、可重渲染的 Remotion 工程**。画面、时间轴、颜色与基线逐项对齐，
但每一帧都改由 React 组件按帧号算出来。

* 基线源码：`tools/demo_video/`（`slides.html` 是画面的唯一真源，`record.js` 逐帧截图，`build.py` 配音+编码）
* 本工程：`promo/`（Remotion 4.0.534 + React 19）

---

## 1. 工程结构

```
promo/
├── package.json          # 依赖与 scripts（studio / render / still）
├── tsconfig.json
├── remotion.config.ts    # 渲染行为（JPEG 中间帧、覆盖输出、可选浏览器路径）
├── public/
│   └── audio.wav         # 62.00s / 44.1kHz 立体声；.demo/out/audio.wav 的逐字节副本
└── src/
    ├── index.ts          # registerRoot(RemotionRoot)
    ├── Root.tsx          # 注册 composition id="Lyric2TTML"（1920x1080@30fps，默认 1860 帧）
    ├── Composition.tsx   # 背景 + 6 个场景 + 进度条 + 音轨；props.videoSeconds 决定时长
    ├── core/             # ★ 被所有场景复用的视觉层（task-1）
    │   ├── theme.ts        # 颜色/字号/字重/间距/圆角/网格/光晕/画布常量（逐条来自 slides.html）
    │   ├── timeline.ts     # 6 段场景区间 + clamp/easeOut/enter（纯函数）
    │   ├── Backdrop.tsx    # 两个漂移光晕 + 网格（可选暗角）
    │   ├── RevealText.tsx  # 逐字淡入 + 上移 26px（按进度，逐帧确定）
    │   ├── ProgressBar.tsx # 底部 6px 渐变进度条
    │   ├── SceneWindow.tsx # 场景外壳：何时显示 + 怎么进场（统一实现，避免 6 份漂移）
    │   └── SmokeScene.tsx  # 仅用于 core 单独自检的占位场景，成片不使用
    └── scenes/           # ★ 6 个业务场景（task-2，scene-builder）
        ├── index.ts      # re-export 六个场景
        ├── S0Opening.tsx S1Problem.tsx S2Input.tsx
        ├── S3Features.tsx S4Stats.tsx S5Ending.tsx
        └── _shared.tsx   # 场景公共层（动画小工具 + Scene 外壳）
```

## 2. 常用命令

```powershell
cd promo
npm install                      # 依赖（本机沙箱里见 §7，要加 --ignore-scripts）

npx remotion studio              # 交互预览（改一行立刻看到）
npx remotion compositions        # 列出 composition 与时长

npx remotion still Lyric2TTML out/frame90.png --frame=90     # 单帧出图
npx remotion render Lyric2TTML out/short.mp4 --frames=0-89   # 只渲前 3 秒（试水）
npx remotion render Lyric2TTML out/Lyric2TTML-promo.mp4      # 整片 62 秒
```

Windows 上若要把输出接管道再看，注意本机沙箱的限制（§7），推荐重定向到文件：

```powershell
cmd /c "npx --no-install remotion render Lyric2TTML out/short.mp4 --frames=0-89 > ..\.tmp\render.log 2>&1"
Get-Content ..\.tmp\render.log -Tail 40
```

## 3. 参数化

| props | 默认 | 说明 |
| --- | --- | --- |
| `videoSeconds` | `62` | 成片时长（秒）。经 `calculateMetadata` 变成 `durationInFrames` |
| `vignette` | `false` | 可选暗角。**默认关闭**，开了会偏离基线 |

```powershell
# 只渲 3 秒版本（时长与音频无关，音频只是素材）
npx remotion render Lyric2TTML out/3s.mp4 --props='{\"videoSeconds\":3}'
```

**时长与音频解耦**：`durationInFrames = round(FPS * videoSeconds)`，不读 `audio.wav` 的时长。
`public/audio.wav` 是 62.00s，渲染长于 62s 时音频会先放完（Remotion 会让音轨自然结束）。

## 4. 场景接口契约（冻结）

* 每个场景是 `React.FC<{t: number}>`，**`t` 是秒**（= `frame / fps`，由 Composition 传入）。
* 场景**自己**用 `<Scene id="s0" t={t}>…</Scene>`（= `core/SceneWindow` + 从 `core/timeline`
  的 `SCENES` 取区间）包外壳。**Composition 不再包一层** —— 包两层会让 `opacity` 变成 p²。
* 场景只画内容：文字、行、数字。**进场（0.7s 淡入 + 上移 26px）由 SceneWindow 统一负责。**
* 所有可见状态必须只由 `t` 决定：**禁止** CSS `transition`/`animation`、`setTimeout`、
  `Date.now()`、`Math.random()`。要动画就用 `progress(t, a, dur)` 算出一帧的值。
* 视觉常量一律从 `core/theme` 取（`COLORS` / `FONT` / `SIZE` / `SPACING` / `WEIGHT` / `LINE_HEIGHT` /
  `RADIUS` / `GRID` / `GLOW` / `VIDEO`），不要在场景里写死颜色/字号。

## 5. 视觉基线与 slides.html 对照

| 项目 | 值 | slides.html 出处 |
| --- | --- | --- |
| 底色 / 正文 | `#0a0a12` / `#e8e8f0` | `html, body` |
| 青 / 绿 / 灰 / 暗灰 | `#22d3ee` / `#6cd97e` / `#9aa4bf` / `#6b7590` | `.accent/.accent2/.sub/.dim` |
| 中文 / 等宽字体 | `Microsoft YaHei` / `Consolas` | `@font-face 'YH'` / `#url` |
| 网格 | `#fff` 1px，`80px 80px`，`opacity .05` | `#grid` |
| 光晕 | `rgba(34,211,238,.16)` 1100px、`rgba(124,92,255,.15)` 900px，`blur(120px)` | `#glow1/#glow2` |
| 光晕漂移 | `left=-160+120sin(t/11)`、`top=-260+90cos(t/13)`、`right=-200+110sin(t/9+1.2)`、`bottom=-220+100cos(t/15)` | `seek()` |
| 进度条 | 高 6px，宽 `t/TOTAL*1920`，`linear-gradient(90deg,#22d3ee,#6cd97e)` | `#bar` |
| 逐字出现 | 每字 `opacity 0→1`、`translateY(26px)→0`，`per=0.045`，阈值 `0.02+i*per*0.55` | `.ch/.ch.lit` + `reveal()` |
| 场景区间 | `s0 0-7`、`s1 7-17`、`s2 17-25`、`s3 25-41`、`s4 41-52`、`s5 52-62` | `SCENES` |
| 场景进场 | 0.7s 淡入 + `translateY(26px)`，`easeOut(x)=1-(1-x)³` | `enter()`/`easeOut()` |

两处**刻意**的实现差异（视觉等价，不是偏离）：

1. `RevealText` 默认把基线的"到点即亮"改成**在一个步长内淡入归位**（`per*0.55` 的 progress 空间，
   30fps 下约 2 帧）。开亮时刻与基线**逐帧一致**，只是那 2 帧里不再瞬跳。
   想要与基线一模一样（硬跳变）就传 `snap`。
2. 基线 `reveal()` 里 `i * per / max(1,N) * N * 0.55` 的 `N/N` 会相消 ⇒ 等价于 `0.02 + i*per*0.55`，
   本工程按相消后的等价式实现（结果逐帧相同，见 `core/RevealText.tsx` 的注释）。

基线里已有的小瑕疵按原样保留，不"顺手修"：`#s5` 最后一行 `.sub` 写了两遍 `class` 属性
（`class="sub" … class="dim"`），第二个被浏览器丢弃 ⇒ 那行显示的是 `.sub` 的 `#9aa4bf`，**不是 `dim`**。

## 6. 音频来源与版权

`public/audio.wav` = 基线 `.demo/out/audio.wav` 的**逐字节副本**（`Copy-Item`，未重编码）：

* 62.00s / 44100Hz / 立体声 / 16-bit，10,936,844 字节
* SHA256 `D93D6343510A0D59F291B1DE7CA4A88991CC1B4DB8C17F2264CD022626156CEB`
* 内容 = **Windows 自带中文语音（Microsoft Huihui）合成旁白**（`build.py` 的 `NARRATION`）
  + **numpy 自己合成的四和弦垫音**（`synth_bgm()`，Am-F-C-G 正弦叠加）
* 因此**没有第三方音乐版权问题**，可随开源项目一起分发；重新生成用
  `python tools/demo_video/build.py --no-record`（不需要重新录帧）。

## 7. ⚠️ 本机沙箱注意事项（踩过的坑，照抄结论）

1. **`stdio: 'pipe'` 的子进程在本沙箱一律 `EPERM`**（命名管道被禁）。
   Remotion 打包必经 esbuild 的 service 模式（stdin/stdout 双工管道），所以
   `npx remotion versions / still / render` 在受限会话里会直接 `spawn EPERM`：

   ```
   spawn ...\node_modules\@esbuild\win32-x64\esbuild.exe ["--service=0.28.1","--ping"] stdio=["pipe","pipe","inherit"]
   spawn EPERM
   ```

   实测（`stdio` 三档对照）：`pipe → EPERM`；`ignore`/`inherit → 正常`（`esbuild --version` 能出 `0.28.1`）。
   ⇒ **要渲染就在沙箱外（普通终端）或对该条命令单次提权**，别在受限会话里反复试。
2. **npm 缓存必须落在工作区内**（全局缓存目录不可写）：
   `$env:npm_config_cache="E:\HXJ\文档\OpenCode\AMLL\.tmp\npm-cache"`；
   npm 生命周期脚本会 spawn 子进程 ⇒ 用 `npm install --ignore-scripts`。
3. **本机没有 Google Chrome**，只有 Edge。Remotion 需要 `chrome-headless-shell`
   （`npx remotion browser ensure` 自动下载）。若下载位置不可写，就把它放到工作区内并设
   `REMOTION_BROWSER_EXECUTABLE=<...>\chrome-headless-shell.exe`；
   `remotion.config.ts` 会把该环境变量透传给 Remotion，所以**工程里不写死本机绝对路径**。
4. **不要联网取字体**：中文一律用系统 `Microsoft YaHei`。
5. `.ps1` 若含中文必须存成 **UTF-8 带 BOM**（PowerShell 5.1 会按 GBK 解码无 BOM 脚本）。

## 8. 自检

* `promo/src/core/**` 全部是纯函数 / 按帧计算的组件：同一个 `t` 永远得到同一张画面。
  快速核对"没有引入不确定性"：

  ```powershell
  Select-String -Path src\core\*.tsx, src\core\*.ts -Pattern 'Math\.random|Date\.now|setTimeout|setInterval|requestAnimationFrame|transition|animation'
  # 期望：没有任何匹配
  ```

* `npx tsc --noEmit`（依赖装好后）应 0 错误。

* **渲染自检**（沙箱外执行）：

  ```powershell
  npx remotion still Lyric2TTML out/frame90.png --frame=90     # t=3.0s，场景 s0
  npx remotion still Lyric2TTML out/frame1000.png --frame=1000 # t=33.33s，场景 s3
  npx remotion render Lyric2TTML out/short.mp4 --frames=0-89
  ```
