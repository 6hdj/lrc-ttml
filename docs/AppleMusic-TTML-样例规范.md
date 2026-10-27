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

## 4. 已实现的 Apple 内部导出模式

当前项目已实现 **`--mode apple-internal`**（CLI / Web / WinUI）：

- 保留 `--layered-lyrics` 的行为（其实该 mode 默认就分层）；
- 根只保留 4 个命名空间，不写 `xml:lang` / `itunes:timing` / `xmlns:tts`；
- `body dur`、`div/p/span` 时间统一 `MM:SS.mmm`；
- 整首一个 `<div xmlns="" begin end>`；
- `<p>` 只写 `begin/end/ttm:agent/itunes:key`（不写 `xml:id`）；
- agent 仍保留 person/name（强制双声部时 Apple Music 才会显示左右分栏）；
- `x-bg` **默认仍包在主唱 `<p>` 内**（Apple Music 显示和声层需要）；样例里“x-bg 伸到 p 外”的特例不默认复刻；
- 元数据保留 `musicName/artists/album` 与平台 ID/ISRC/作者 GitHub。

> 默认 `amll` / `apple` 行为不变；`apple-internal` 是独立 profile。
> 仍然没有做到逐字节复刻的：英文按音节 span、纯文本 `<p>`、`x-bg` 内翻译、
> 多值双语 metadata；这些属于后续增强。

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

## 6. 七份样例汇总：主导格式其实很明确

把 7 份真实文件放在一起看，**6 份是同一个“最小 Apple 内部导出”**：

| 项 | 6 份主导格式 | 第 2 份（完整导出） |
|---|---|---|
| 根 | 只有 `ttml/ttm/itunes/amll`；无 `xml:lang`/`itunes:timing`/`tts` | 严格根属性 |
| 时间 | 统一 `MM:SS.mmm` | 混合 `SS` / `M:SS` / `body` |
| div | **整首 1 个**（可能有 `xmlns=""`） | 6 个 + `itunes:songPart` |
| p | `begin end ttm:agent itunes:key`，无 `xml:id` | 同左，另有不同属性顺序 |
| agent | 自闭合、无 `ttm:name`；常见 v1 person / v2 other | 仅 v1 person |
| 文本 | 中文逐字 span；英文按词；空格位置不固定 | 英文按音节，空格在 span 外 |
| 翻译 | 多数没有；有的用行内 `x-translation` | 头部 `iTunesMetadata` 4 语言 |
| 和声 | 可选 `x-bg`；可含行内翻译 | 无 |

### 样本集里出现的差异/异常

- **空格位置不固定**：同一个文件里既有 `<span>幕 </span>`（空格在 span 内），也有
  `<span>Keep</span> <span>waving</span>`（空格在 span 外）。解析器按拼接文本处理，
  两种都能工作；关键是不能丢空格。
- **p 可以是纯文本**：有些短句 `Ha` / `Yeah` 直接写
  `<p …>Ha</p>`，没有 `<span>`。这说明 Apple/AMLL 接受“没有逐字时间”的行，
  但我们自己的生成器仍优先输出逐字 span；不要把纯文本 p 当作标准写法。
- **异常占位**：零时长 `p`、零时长 `x-bg`、`p begin=end` 等是数据错误；
  `begin < end` 仍是硬规则。
- **翻译嵌套**：`x-translation` 可以出现在 `p` 末尾，也可以出现在 `x-bg` 内
  （翻译和声）；头部翻译和行内翻译两种方式都出现过。
- **元数据自由度高**：`musicName/artists/album` 可以重复多语言；
  平台 ID 可多值；`作词/作曲/制作人/…` 这类中文 key 也出现过。

> 如果要把本项目的输出做成“与多数真实文件一致”，优先目标应是：
> **最小根属性 + MM:SS + 单 div + p 仅 itunes:key/agent + span 逐字/词 + 可选 x-bg**；
> 头部翻译、songPart、多 div 属于完整导出变体，不是必需。

### 结论：没有一份“唯一正确”的 Apple 文件

- 多数样例是**最小 Apple 内部导出**：无 xml:lang/tts、MM:SS、单 div、匿名 agent；
- 第二份是**完整 AMLL/Apple 导出**：严格根属性、混合自由时间、多 div/songPart、多语翻译；
- 翻译既可以在 head 的 `iTunesMetadata`，也可以在行内 `x-translation`（甚至 x-bg 内）；
- 七份共同的不变量才是硬规则：`tt > head/metadata + body`、`p` 有 `itunes:key` 和
  `ttm:agent`、`span ⊆ p ⊆ div ⊆ body@dur`、`begin < end`、key 连续、文本/空格一致、翻译标签对齐。

## 7. 同时出现的分层歌词（不是对唱）

这七份文件里，**5 份存在 `<p>` 时间重叠**；`ttm:agent` 不能简单当成“两位歌手”：

| 文件 | 重叠对数 | 典型情况 |
|---|---:|---|
| 第一份（中文单曲） | 6 | 不同 agent 的主/副句、含 x-bg 的行跨到下一句 |
| 第二份（英文完整导出） | 0 | 单 agent，无重叠 |
| 第三份（内联翻译） | 6 | 不同 agent、同 agent v2 的两句主歌词重叠 |
| 172369… | 3 | `Ha` 与下一句和声、同 agent v1 的 `Inside you`/`Yeah` |
| 172534… | 0 | 单 agent，无重叠 |
| 172543… | 1 | 同 agent v1 的相邻句重叠 0.057s（可能是取整/交界） |
| 172543979… | 1 | v1 主句与 v2 主句重叠 2.26s |

### 7.1 三种真实形态

**A. 不同 agent 的两句同时唱（不一定是对唱）**

```xml
<p begin="00:51.740" end="01:00.460" ttm:agent="v2" itunes:key="L9">
  却还是不肯翻开下一幅篇章
</p>
<p begin="00:58.480" end="01:05.090" ttm:agent="v1" itunes:key="L10">
  不再害怕做不到的事情
</p>
```

这里 v2/v1 重叠 1.98s；可能是第二声部/和声，不一定是双人对唱。  
**不能用“agent 不同”直接推成对唱、也不能用声纹强行拆人。**

**B. 上一句是主歌词 + 内嵌和声，下一句主歌词同时出现**

```xml
<p begin="01:05.480" end="01:13.550" ttm:agent="v1" itunes:key="L11">
  ...主歌词...
  <span ttm:role="x-bg" begin="01:10.590" end="01:13.550">
    <span>(一</span><span>路</span><span>践</span><span>行)</span>
  </span>
</p>
<p begin="01:12.390" end="01:18.430" ttm:agent="v2" itunes:key="L12">
  那些美好或悲伤的记忆
</p>
```

**C. 同 agent、两句独立主歌词同时出现**

```xml
<p begin="02:03.810" end="02:04.860" ttm:agent="v2" itunes:key="L39">
  You are my shine ...
</p>
<p begin="02:04.120" end="02:07.660" ttm:agent="v2" itunes:key="L40">
  你给的耳机 ...
</p>
```

第三份里 v2 的两句主歌词重叠；172369 里 v1 的 `Inside you` 和 `Yeah` 也重叠。
所以 **同一 agent 的 `<p>` 也可以重叠**，它不是“同一个人不能同时唱两行”的证据。

### 7.2 样例里还有 x-bg 超出 p 的写法

172369 的第一句：

```xml
<p begin="00:14.573" end="00:16.725" ttm:agent="v1" itunes:key="L1">
  ...主歌词（到 00:16.725）...
  <span ttm:role="x-bg" begin="00:17.060" end="00:17.977">(Hoh-hoh-hoh)</span>
</p>
```

`x-bg` 的开始时间 **晚于 p 的 end**；172543 也有类似情况。  
这说明真实 Apple/AMLL 文件对 `x-bg` 的时间包含关系比严格 TTML1 宽松：
**主唱 p 的 end 可以只包住主唱，和声 span 可以伸到 p 外**。

### 7.3 本项目已支持的分层模式

默认模式仍是严格 W3C；显式打开 **`--layered-lyrics`**（CLI / Web / WinUI）后：

- 允许同一个 `<p>` 与前后 `<p>` 在时间上重叠，但**只对含 `x-bg` 的分层行放宽**；
  纯主唱行的意外尾音重叠仍会压掉（否则 TIFA 的尾字越界会让每句都叠唱）；
- `x-bg` 默认仍包在主唱 `<p>` 内；样例里“x-bg 伸出 p”属于特例，不默认复刻；
- `pipeline` 不再执行 `_enforce_agent_monotonic` 的同 agent 压缩；
- `ttml_check(..., layered=True)` 允许同 agent 行重叠、`x-bg` 出界；
- `ttml_reader` 会从“行重叠 / x-bg 出界”自动推断`layered`，`doc_to_ttml` 回写时保留；
- `normalize_timeline(..., layered=True)` 跳过 R4 同 agent 去重叠，不清洗 `x-bg` 出界；
- 编辑台保存链路（`edit_api`）会带 layered 标记，避免“打开 Apple 文件保存一次就把重叠修掉”。

还剩的差异是**根属性/div/时间格式/agent 命名**这些“最小 Apple 导出”细节；它们属于另一个 profile，不是 layered 本身。默认 `amll`/严格 `apple` 行为不变。

## 8. ASR 补词（原歌词漏句）

默认开启 **`--asr-supplement`**（UI「自动补充缺失歌词」）：

- Whisper 识别到、但不在任何已有歌词行时间窗内的词，按停顿合并成新行；
- 新行文本直接来自 ASR，时间来自 ASR 词级时间，按字/词拆成 span；
- 声部先继承**时间最近**的已有行（v1/v2），之后仍可在编辑台改；
- 与已有主唱字重叠、且文本足够长的疑似漏句也会补成新行；
- 单字复读、与邻近歌词重复、低置信度词会被丢弃，避免把气声当歌词；
- 关闭：`--no-asr-supplement`（旧行为：只输出原歌词已写的行）。

局限：ASR 本身可能只识别出半句或错字；这是“补显示”，不是人工校词。  
新行的声部是邻近行推断，不是对任意音频片段重新做声纹；需要精确归属时在编辑台改。

## 9. 提交前检查清单

- [ ] `<p>` 都有 `itunes:key`（L1 连续），都有 `ttm:agent`，head 定义了该 agent
- [ ] 每行文本与 span 文本完全一致（空格也要保留）
- [ ] `span ⊆ p ⊆ div ⊆ body@dur`，`begin < end`
- [ ] 同一 agent 的 `<p>` 不重叠；`x-bg` 允许跨行重叠（若文件本身是分层歌词，用
      `--layered-lyrics` / layered profile 校验，不要按严格 W3C 去压）
- [ ] 和声用 `x-bg` 子 span，不单独成行
- [ ] 元数据有 `musicName` / `artists` / `album`，至少一个平台 ID
- [ ] 用目标格式的 profile 校验：Apple 内部样例允许 `MM:SS` + 无 `xml:lang`；
      W3C/通用工具要求 `HH:MM:SS` + `xml:lang`
