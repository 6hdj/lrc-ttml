# Apple Music 歌词 TTML 示例格式（对照分析）

> 本文依据三份真实的 Apple Music / AMLL TTML DB 歌词文件整理，用于区分
> **Apple Music 内部导出格式**、**AMLL TTML DB 格式** 与 **W3C TTML1 严格格式**。
> 本项目自身的默认输出仍以 W3C TTML1 + AMLL 规范为准；本文是“样例事实”，
> 不是 W3C 规范原文。

示例文件的结构（33 行、277 个 span、5 段和声）：

```xml
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xmlns:amll="http://www.example.com/ns/amll">
  <head>
    <metadata xmlns="">
      <ttm:agent type="person" xml:id="v1"/>
      <ttm:agent type="other" xml:id="v2"/>
      <amll:meta key="musicName" value="Before Setting Out"/>
      <amll:meta key="musicName" value="在出发之前"/>
      <amll:meta key="artists" value="陶典"/>
      ...
      <amll:meta key="ttmlAuthorGithub" value="136059186"/>
      <amll:meta key="ttmlAuthorGithubLogin" value="MiaowCham"/>
    </metadata>
  </head>
  <body dur="04:28.790">
    <div xmlns="" begin="00:04.250" end="04:28.790">
      <p begin="00:04.250" end="00:07.690" ttm:agent="v1" itunes:key="L1">
        <span begin="00:04.250" end="00:04.640">站</span>
        ...
      </p>
      ...
    </div>
  </body>
</tt>
```

## 1. 这份样例的实际规则

| 项 | 样例写法 | 说明 |
|---|---|---|
| XML 声明 | **没有** `<?xml ...?>` | 直接以 `<tt` 开头 |
| 根命名空间 | `ttml`、`ttm`、`itunes`、`amll` | **没有** `xmlns:tts` |
| 根属性 | **没有** `xml:lang`、`itunes:timing` | 与 W3C TTML1 的“xml:lang 必需”不同 |
| `<head>` | 只有 `<metadata xmlns="">` | 没有 `ttm:title`/`ttm:copyright` |
| agent | `<ttm:agent type="person" xml:id="v1"/>`、`type="other" xml:id="v2"` | 自闭合、**不写 `ttm:name`** |
| amll 元数据 | 多行 `musicName` / `artists` / `album` | 可重复，保留双语值；平台 ID、ISRC、作者 GitHub 信息 |
| `<body>` | `dur="04:28.790"` | **`MM:SS.mmm`**，不是 `HH:MM:SS.mmm` |
| `<div>` | **整首只有一个**，`xmlns="" begin end` | 不按段落/间奏拆分，没有 `itunes:songPart` |
| `<p>` | `begin end ttm:agent itunes:key` | **没有 `xml:id`**；`itunes:key` 从 L1 连续 |
| `<span>` | `begin end` + 文本 | 一个字/词一个 span；空格附在前一个 span 的文本里（如 `对 `、`个 `） |
| 背景和声 | `<span ttm:role="x-bg" begin end>` 包住子 span | 5 段；括号写在子 span 文本里，如 `(一` … `)` |
| 翻译/音译 | 样例中没有 | 有的话放 `head/metadata/iTunesMetadata` 或行内 `x-translation` |
| 对唱 | `p ttm:agent="v1"/"v2"` | 用不同 agent；样例 v2 的 type 是 `other` |
| 时间包含 | `span ⊆ p ⊆ div ⊆ body[dur]` | 与 TTML1 一致 |
| 文本 | 中文逐字、英文按词 | 与 AMLL 解析器约定一致 |

### 样例中的两个“例外”

1. **缺 `xml:lang`**：W3C TTML1 要求 `<tt>` 有 `xml:lang`；这份 Apple/AMLL 生态文件
   省略了它。我们的 `ttml_check --strict` 会报错，但 Apple/AMLL 播放器可以接受。
2. **`MM:SS.mmm`**：W3C TTML1 只接受 `hh:mm:ss[.fff]` / offset-time；这是 Apple/AMLL
   宽松写法。我们的默认输出用 `HH:MM:SS.mmm`，以同时满足 W3C 与 AMLL。

因此：**“Apple Music 样例能播” ≠ “它是 W3C 合法 TTML1”**。如果目标是提交给
Apple Music / AMLL DB，按样例；如果目标是严格 W3C/通用 TTML 工具，保留我们的
`HH:MM:SS.mmm` + `xml:lang`。

## 2. 和声的正确嵌套

```xml
<p begin="00:01:08.000" end="00:01:13.550" ttm:agent="v1" itunes:key="L12">
  <span begin="00:01:08.000" end="00:01:08.430">主</span>
  <span begin="00:01:08.430" end="00:01:08.900">唱</span>
  <span ttm:role="x-bg" begin="00:01:10.590" end="00:01:13.550">
    <span begin="00:01:10.590" end="00:01:11.030">(一</span>
    <span begin="00:01:11.030" end="00:01:11.890">路</span>
    <span begin="00:01:11.890" end="00:01:13.550">行)</span>
  </span>
</p>
```

要点：

- `x-bg` 是 **`p` 的子内容**，不能单独成 `p`（单独成行 AMLL 会当空行丢掉）；
- `x-bg` 包装层也写 `begin/end`，且包住所有子 span；
- 括号属于和声文本，不是独立 span；
- 和声的时间允许与下一行/主唱重叠（规范豁免）。

## 3. 与当前项目输出的差异

以同一首歌、`--mode apple` 生成的文件对比：

| 项 | Apple 样例 | 当前 `--mode apple` | 影响 |
|---|---|---|---|
| XML 声明 | 无 | 有 | 一般无影响 |
| 根 `xmlns:tts` / `xml:lang` / `itunes:timing` | 无 | 有 | 样例不需要；我们为了 W3C 合法保留 |
| `<div>` 数量 | 1 个整首 | 按 >4s 停顿拆多个 | 样例不拆；我们的 Apple 提交模式拆段落 |
| `<div>` `xmlns=""` | 有 | 无 | 序列化差异 |
| `<p>` `xml:id` | 无 | 有（`line_id=both` 默认） | 样例只靠 `itunes:key` |
| 时间格式 | `MM:SS.mmm` | 默认 `HH:MM:SS.mmm` | 样例是 Apple/AMLL 宽松写法 |
| agent 名称 | 不写 `ttm:name` | 写 `ttm:name` | 样例匿名；名称不是必需 |
| 元数据 | 双语重复值、作者 GitHub 登录 | 单值为主 | 信息量不同，不是结构错误 |

## 4. 如果我们增加“Apple 内部导出”模式，需要这样生成

1. 根只保留 4 个命名空间，不写 `xml:lang` / `itunes:timing` / `xmlns:tts`；
2. `body dur`、`div/p/span` 时间统一 `MM:SS.mmm`；
3. **整首一个 `<div>`**，`div` 上 `xmlns=""`、`begin/end` 覆盖全曲；
4. `<p>` 只写 `begin/end/ttm:agent/itunes:key`（不写 `xml:id`）；
5. agent 自闭合，可选不写 `ttm:name`；
6. 元数据保留多值 `musicName/artists/album`、平台 ID、ISRC、作者 GitHub；
7. 文本/空格/`x-bg` 嵌套与上面样例一致；
8. 不套 `<tts>`/样式；翻译才用 `iTunesMetadata`；
9. 校验器必须使用 **Apple/AMLL 宽松 profile**，不能直接套 W3C strict。

> 当前项目的默认输出仍选择 **W3C 严格 + AMLL 兼容**；如果确实要逐字节贴近
> Apple 内部样例，应新增一个独立的 `apple-internal` 输出 profile，而不是改默认
> `amll`/`apple` 行为（否则会破坏现有严格校验与 AMLL DB 约定）。

## 5. 第二份样例：完整 AMLL / Apple 导出

第二份样例（英文歌 + 多语翻译 + 段落）与第一份**不是同一种风格**：

| 项 | 第一份（中文单曲） | 第二份（英文歌 + 翻译） |
|---|---|---|
| 根属性 | 无 `xml:lang` / 无 `itunes:timing` / 无 `tts` | `xml:lang="en"`、`itunes:timing="Word"`、有 `tts` |
| `body dur` | `04:28.790` | `2:53.540` |
| div | **1 个整首** | **6 个**，带 `itunes:songPart="Verse/Chorus/FalseChorus/FinalChorus"` |
| 时间写法 | 全部 `MM:SS.mmm` | **混合**：`32.130`（<1min 裸秒）、`1:04.600`（≥1min 用 `M:SS.mmm`） |
| p 属性顺序 | `begin end ttm:agent itunes:key` | `begin end itunes:key ttm:agent` |
| agent | v1 person + v2 other，自闭合 | 只有 v1 person，自闭合 |
| 英文 span | —（中文） | 按**音节/片段**切：`Cor`+`rupt`、`bi`+`na`+`ry`；**空格在 span 外** |
| 元数据 | 双语 musicName/album + 平台 ID | 更多重复的 musicName/album/平台 ID + 中文 credit key（作词/作曲/制作人/录音/混音…） |
| 翻译 | 无 | `iTunesMetadata/translations`：ja / ko / zh-Hant / zh-Hans |
| 和声 | 5 段 x-bg | 无 |
| 对唱 | v1/v2 | 无（全 v1） |

### 从第二份样例补充的规则

1. **Apple/AMLL 不是只有一种时间格式**：
   - 同一文件里 `<1min` 可以写裸秒 `32.130`；
   - `≥1min` 写 `1:04.600`；
   - 另一份样例统一写 `00:04.250` / `04:28.790`；
   - 说明 Apple/AMLL 解析器接受 **`SS.mmm` / `M:SS.mmm` / `MM:SS.mmm` / `HH:MM:SS.mmm`** 这类宽松写法；
   - 但 W3C TTML1 只认 `HH:MM:SS.mmm` / offset-time。

2. **英文 span 可以比“单词”更细**：
   - 样例把 `Corrupt` 拆成 `Cor` + `rupt`，`binary` 拆成 `bi`+`na`+`ry`；
   - **空格是 span 之间的普通文本**，不在 span 内（中文没有空格，所以第一份看不到）；
   - `AMLL` 解析器按文本拼接后仍然是正常单词；逐字时间轴因此可以精确到音节；
   - 本项目当前英文按“词”一个 span；要完全贴近样例，需要用 TIFA `phones`/音节边界再细分。

3. **div 的段落是可选信息**：
   - 第二份用 6 个 div + `itunes:songPart`；
   - 第一份整首 1 个 div、无 songPart；
   - 两种都能被解析；段落划分不是硬性要求，但 `songPart` 有助于 Apple 的段落展示。

4. **中文 credit 可以写进 `amll:meta`**：
   - 第二份有 `作词` / `作曲` / `制作人` / `录音` / `母带处理` / `混音` / `演唱` / `编曲` 等 key；
   - 这些是 AMLL DB 的扩展元数据，不是 TTML1 元素；写不写不影响播放，但提交 AMLL DB 时应保留。

5. **翻译是整首一组**：
   - `<iTunesMetadata><translations><translation type="subtitle" xml:lang="ja"><text for="L1">…</text>...`
   - `for` 必须对应 `<p>` 的 `itunes:key`；第二份示例有 4 种语言。
   - `<transliterations/>` 可以为空；`<songwriters>` 单独列。

### 第三份样例：内联翻译 + 和声内翻译

第三份（中文歌 + 英文和声）与第一份同属**最小 Apple 内部导出**：

| 项 | 第三份 |
|---|---|
| 根 | 只有 ttml/ttm/itunes/amll；无 `xml:lang`、无 `itunes:timing`、无 `tts` |
| 时间 | 全部 `MM:SS.mmm`（`00:24.200` / `03:16.770`） |
| div | 1 个整首，`xmlns=""` |
| p/span | 56 行 / 381 span；中文逐字，英文按词；`itunes:key` 连续 |
| 和声 | 4 段 `x-bg`；其中 **`x-bg` 内嵌 `x-translation`** |
| 翻译 | **行内** `<span ttm:role="x-translation" xml:lang="zh-CN">…</span>`，没有 `iTunesMetadata` |
| agent | v1 person + v2 other，自闭合 |

关键规则：

1. **翻译有两种合法位置**：
   - 头部：`<iTunesMetadata><translations><translation ...><text for="L1">`
   - 行内：`<p>…<span ttm:role="x-translation" xml:lang="zh-CN">…</span></p>`
   - 第三份还把 `x-translation` 放进 `x-bg` 内，用来翻译和声；
2. **行内翻译的 `xml:lang` 用具体语言标签**（示例 `zh-CN`；我们的项目默认写 `zh-Hans`，两者都是合法 BCP-47，但提交时要统一）；
3. **英文/混排的空格同样在 span 外**（如 `<span>Keep</span> <span>waving</span>`）；
4. **样例里存在不合法的占位**：
   - `L36` 是 `<p begin="00:00.000" end="00:00.000">Increasing…</p>`（零时长、文本没有 span）；
   - 另一处 `x-bg begin="00:00.000" end="00:00.000"`；
   - 这些是**数据错误/占位**，不要当作规则；我们的生成器会拒绝零时长 span，不应模仿。

### 结论：没有一份“唯一正确”的 Apple 文件

- 第一、第三份是**最小 Apple 内部导出**：无 xml:lang/tts、MM:SS、单 div、匿名 agent；
- 第二份是**完整 AMLL/Apple 导出**：严格根属性、混合自由时间、多 div/songPart、多语翻译；
- 翻译既可以在 head 的 `iTunesMetadata`，也可以在行内 `x-translation`（甚至 x-bg 内）；
- 三份共同的不变量才是硬规则：`tt > head/metadata + body`、`p` 有 `itunes:key` 和
  `ttm:agent`、`span ⊆ p ⊆ div ⊆ body@dur`、key 连续、文本/空格一致、翻译标签对齐。

## 6. 提交前检查清单

- [ ] `<p>` 都有 `itunes:key`（L1 连续），都有 `ttm:agent`，head 定义了该 agent
- [ ] 每行文本与 span 文本完全一致（空格也要保留）
- [ ] `span ⊆ p ⊆ div ⊆ body@dur`，`begin < end`
- [ ] 同一 agent 的 `<p>` 不重叠；`x-bg` 允许跨行重叠
- [ ] 和声用 `x-bg` 子 span，不单独成行
- [ ] 元数据有 `musicName` / `artists` / `album`，至少一个平台 ID
- [ ] 用目标格式的 profile 校验：Apple 内部样例允许 `MM:SS` + 无 `xml:lang`；
      W3C/通用工具要求 `HH:MM:SS` + `xml:lang`
