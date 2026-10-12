# `promo/src/scenes/` —— 6 个场景组件

`task-2` 的产物：把基线宣传片 `tools/demo_video/slides.html` 的 6 个场景逐帧复刻成 Remotion 组件。

| 文件 | 场景 | 区间 | 基线 DOM id |
| --- | --- | --- | --- |
| `S0Opening.tsx` | 开场：tag / h1 / sub | 0–7s | `#s0` |
| `S1Problem.tsx` | 痛点：「整行亮」vs「逐字亮」 | 7–17s | `#s1` |
| `S2Input.tsx` | 做什么：只要两样东西 | 17–25s | `#s2` |
| `S3Features.tsx` | 能力列表（5 行） | 25–41s | `#s3` |
| `S4Stats.tsx` | 实测数字（3 个） | 41–52s | `#s4` |
| `S5Ending.tsx` | 结尾：标题 / 地址 / 许可 | 52–62s | `#s5` |
| `_shared.tsx` | `progress` / `progressEased` / `EASE_CUBIC` / `<Scene>` | — | — |
| `index.ts` | re-export 六个 + `SCENE_COMPONENTS` | — | — |

## 接口契约（已冻结）

* 每个场景签名 `React.FC<{t: number}>`，`t` = 全局秒数（`useCurrentFrame() / fps`）。
* **场景外壳由场景自己包**：`<Scene id="s0" t={t}>` = core 的 `SceneWindow`（显隐 + 0.7s 淡入 +
  26px 上移）+ 自动从 core 的 `SCENES` 取区间。`Composition.tsx` **不再**包一层
  （包两层会让 opacity 变平方、上移变 52px）。
* 纯函数：所有可见状态只由 `t` 决定。没有 CSS transition/animation、没有 `Date.now()`、
  没有随机数；缓动一律走 Remotion 的 `interpolate` / `Easing`。
* 文案照抄基线，一个标点都不改（含 ` + `、` → `、` · ` 两侧的空格）。
* 视觉常量一律取自 `core/theme.ts`（`COLORS` / `SIZE` / `WEIGHT` / `LINE_HEIGHT` /
  `SPACING` / `RADIUS`），**不在场景里写裸数字**。

## 版式测量记录（为什么是这些值）

下面全部是 **Pillow 像素测量**（参考帧 `.tmp/promo_ref/tNNN.jpg`，1920×1080，NNN = 第 NNN 秒），
不是肉眼估的。改字号/行高之前请先看这四张表。

### 表 1 — 列表行几何（t037，`#s3`）

| 量 | 实测 |
| --- | --- |
| 行框 1px 边框所在 y | 260 / 383,402 / 525,544 / 667,686 / 809,828 / 951 |
| 行高 | **≈123.8px**（边框间距） |
| 相邻两行间距 | **18~19px** |
| 行距（pitch） | **142px** = 123.8 + 18 |
| 整块内容 | y≈127…951，中心 **539**（视口中心 540）→ 确实垂直居中 |

* **关键坑：垂直外边距会塌陷**。`.row { margin: 18px 0 }` 的相邻两条 18px 塌成 **18px**
  （不是 36px）。行距 = 行高 + 18，不是行高 + 36。
* 行高不是写死的：= `2×26(padding)` + 内容行盒（52px emoji 与 46px 标题里更高的那个，
  normal 行高 ≈1.37em）。**不要给 `.row` 加行高**，交给 Chromium 算，才能和基线一致。

### 表 2 — 颜色采样（每带取最亮 40 像素的中位数）

| 位置 | 实测 RGB | 结论 |
| --- | --- | --- |
| 行主标题 | (241,240,252) | `#e8e8f0` = `COLORS.text` ✓ |
| h2 | (240,241,249) | `#e8e8f0` ✓ |
| `.box` 内文字 | (239,255,255) | `#eafcff` = `COLORS.boxText` ✓ |
| `.cline .cap` | (129,144,163) | 更贴 **`#7d879f`**（`capText`）→ 用基线值，不用需求清单里的 `#9aa4bf` |
| 行右侧说明 | (150,155,178) | 介于 `#8b95ad` 与 `#9aa4bf` 之间 → 保留基线 `#8b95ad`（`rowDesc`） |
| `.sub` 内 `.dim` span（t061 第 3 行） | (118,124,151) | `#6b7590` = `COLORS.dim` ✓ |
| s5 末行「MIT 开源…」 | (117,122,152) | **dim**：HTML 里重复的 `class="dim"` 被解析器丢弃，但基线 JS 用 `classList.add('dim')` 补上了 |
| `#url` | (174,216,213) | `#9fd3c7` = `COLORS.url` ✓ |

### 表 3 — 场景 1 对比块几何（t013：预测 vs 实测）

预测全部来自基线 CSS；`.cline` **没有** line-height，所以按字体 normal ≈1.32em 算
（cap 30px→39.6、正文 50px→66）。

| 元素 | 预测 y | 实测 y |
| --- | --- | --- |
| h2 行盒（74×1.25=92.5） | 256…348 | — |
| 红框（padding 34×2 + cap 39.6 + 14 + 正文 66 = 187.6） | 398…586 | **396…585**（高 189）✓ |
| 两框间距（`.compare gap` 46） | 46 | 47 ✓ |
| 青框（正文里 `.box` 是 inline-block，多 2px padding → 191.6） | 632…824 | **632…825**（高 193）✓ |

差 ≤2px ⇒「中文行盒 = 字体 normal 行高 ≈1.32em」这个模型是对的，
**所以基线没写 line-height 的地方，我们也不能写**。

### 表 4 — 场景 4 标签行距（t051，这条抓出过一个真 bug）

| 量 | 实测 | normal（1.32×34 = 44.9） | `.sub` 的 1.6（= 54.4） |
| --- | --- | --- | --- |
| `.stat .l` 两行标签中心距 | **45.5px** | ✓ | ✗ |

给 `.stat .l` 顺手写上 `lineHeight: 1.6` 会让统计块高 ≈19px 并整体下移 —— 已修正。
同帧其它对照（预测 / 实测）：标签第 1 行中心 618.8 / **619.5**、第 2 行 663.7 / **665.0**、
注解 752 / **747**。

### 由此得出的规则

显式 `line-height` **只加在基线 CSS 真的写了 line-height 的地方**：

| 有 line-height（照抄） | 没有 line-height（必须留空） |
| --- | --- |
| `h1` 1.18、`h2` 1.25、`.sub` 1.6、`.stat .n` 1 | `.tag`、`.cline`、`.cline .cap`、`.row`、`.stat .l`、`#url` |

留空时 Chromium 用字体 normal（Microsoft YaHei ≈1.32em）。**字体只用系统 Microsoft YaHei，
不联网取字体**；若换机器缺字体，行高会整体变化（那时先查字体，别改数值）。

## 故意保留的差异（3 处）

1. **逐字出现默认走 `RevealText` 的平滑档（`snap=false`）**：开亮**时刻**与基线逐帧相同
   （阈值 `0.02 + i*per*0.55`，与基线 `reveal()` 消掉 `/N*N` 后等价），只是每个字多花约
   1 个步长（≈1.4 帧 @30fps）淡入到位。若要 100% 硬照搬基线的"到点即亮"，加 `snap` 即可
   （Lead 决定：等 verifier 的逐帧 MAE，若某帧 >3 再切）。
2. **行右侧说明加 `whiteSpace: nowrap`**：基线靠"文字短"不折行，显式声明只防以后改字号时
   突然折行，不改变观感。
3. **场景区间从 core 的 `SCENES` 取**，没有在本目录再抄一份 s0–s5 时间点（避免两处漂移）。

## 验证帧号（30fps，`frame = 30 × 秒`；`tNNN.jpg` 就是第 NNN 秒）

| 场景 | 帧号（参考帧） |
| --- | --- |
| s0 | 90 (t003) |
| s1 | 270 (t009 仅红框) / 390 (t013 两框) / 450 (t015 青框滑入到位) |
| s2 | 570 (t019 仅 h2) / 660 (t022 三行齐) |
| s3 | 840 (t028 1 行) / 960 (t032 3 行) / 1110 (t037 5 行) |
| s4 | 1320 (t044 1 个) / 1380 (t046 2 个) / 1530 (t051 3 个 + 注解) |
| s5 | 1650 (t055) / 1770 (t059) / 1830 (t061) |

```powershell
# 静态检查（不 spawn 子进程，沙箱内可用）
node node_modules\typescript\bin\tsc --noEmit -p tsconfig.json

# 逐帧出图（需要 esbuild/Chromium，沙箱里要放宽权限）
npx remotion still Lyric2TTML out/s3-t037.png --frame=1110
npx remotion compositions
```
