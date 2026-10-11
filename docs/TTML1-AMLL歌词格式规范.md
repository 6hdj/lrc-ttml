# TTML1 / AMLL 歌词格式规范与实现说明

> **这份文档给谁看**：本仓库（`AMLL` = 普通歌词 + 音频 → Apple Music / AMLL 逐字 TTML）里
> 所有参与改代码的 AI 助手与人类。
> **为什么有它**：TTML 的"语法"不只是 XML 是否 well-formed，还有 W3C TTML1 的
> 时间表达式、内容模型、时间包含关系，以及 AMLL / Apple 生态的额外约定。
> 之前生成的歌词文件就是因为不符合这些规则而"看着没问题、播放器里没效果"。
> **本文件里的规则都是核对过规范原文或生态实现后写下的，改代码时不要回退。**
>
> 配套文件：[`AGENTS.md`](../AGENTS.md)（给 AI 的精简铁律版）。

---

## 1. 规范来源（核对过的原始资料）

| 资料 | 地址 | 用来确定什么 |
| --- | --- | --- |
| W3C **TTML1 (Third Edition)** | https://www.w3.org/TR/ttml1/ | 命名空间、文档结构、`<timeExpression>`、时间区间、元数据 |
| TTML1 官方 XSD | https://cdn.jsdelivr.net/gh/w3c/ttml1@main/spec/xsd/ttml1-datatypes.xsd （`-content` / `-metadata` / `-core-attribs` / `-document` / `-head` / `-metadata-items` / `-timing-attribs` 同目录） | 内容模型、必需属性、属性取值枚举（schema 只是"超集"，见 §3.9） |
| TTML1 官方 RNC | https://cdn.jsdelivr.net/gh/w3c/ttml1@main/spec/rnc/ttml1-datatypes.rnc | 数据类型、`ttm:role` 的 `x-` 扩展规则 |
| **AMLL TTML DB 格式规范**（中文现行版，权威） | https://github.com/amll-dev/amll-ttml-db/wiki/格式规范 | 时间格式、`itunes:key`、背景人声/对唱/翻译写法、硬性审核要求 |
| AMLL 参考实现 `packages/ttml` | `constants.ts` / `parser.ts` / `generator.ts` / `tests/__snapshots__/generator.test.ts.snap`（仓库：amll-dev/applemusic-like-lyrics） | 生态实际怎么写、解析器到底认什么 |
| 线上真实歌词库 | `amll-ttml-db` 的 `ncm-lyrics/186016.ttml`、`am-lyrics/535824738.ttml` | 真实文件的命名空间与写法 |

> 网络上拿不到时不要凭记忆猜规则：**本文档就是核对结果的快照**，照着用即可。

---

## 2. 一分钟速查（最容易踩的 7 条）

| # | 规则 | 依据 |
| --- | --- | --- |
| 1 | 时间戳默认写 **`HH:MM:SS.mmm`**（`00:01:05.432`）。`MM:SS.mmm`、裸秒 `4.370`、`10.0s` **都不是**合法 TTML1 | TTML1 §10.3.1 |
| 2 | `clock-time` 的**小时段不可省略**；`offset-time` **必须带单位**（`12.5s` / `250ms`） | TTML1 §10.3.1 |
| 3 | 时间必须**被父元素包住**：`span ⊆ p ⊆ div ⊆ body@dur`，且 `begin < end` | TTML1 §10.4 + AMLL 规范 §6 |
| 4 | `<tt>` 上的 `xml:lang`（BCP-47）**必需** | TTML1 §7.2.2 / XSD `Core.required.attrib.class` |
| 5 | 每个 `<p>` 必须带 `itunes:key="L1"…`（**连续、跨 div 不断号**），且带 `ttm:agent`（单人歌也要 `v1`） | AMLL 规范 §7.2；AMLL 解析器没有 key 会**整行丢掉** |
| 6 | 背景人声只能是**某一行的子内容**：`<p>` 里的 `<span ttm:role="x-bg">`，**不能单独成行** | AMLL 规范 §7.3 + 解析器实现（§7.2） |
| 7 | 文本里不能出现 XML 1.0 非法字符（`\x00`、`\x0b` 等），写文件用 UTF-8 **无 BOM**，`<p>` 内部不换行 | XML 1.0 / AMLL 规范 §2、§3.3 |

---

## 3. W3C TTML1 要点（逐条，含原文产生式）

### 3.1 命名空间（§5.1 表 1）

| 名称 | 前缀 | URI |
| --- | --- | --- |
| TT | 默认 | `http://www.w3.org/ns/ttml` |
| TT Parameter | `ttp:` | `http://www.w3.org/ns/ttml#parameter` |
| TT Style | `tts:` | `http://www.w3.org/ns/ttml#styling` |
| TT Metadata | `ttm:` | `http://www.w3.org/ns/ttml#metadata` |

`http://www.w3.org/XML/1998/namespace`（`xml:lang` / `xml:id` / `xml:space`）是 XML 自带的，
不在上表里但同样合法。**非 TT 命名空间的元素/属性**（如 `itunes:*`、`amll:meta`、
`<iTunesMetadata>`）按 §4 的剪除规则处理：在判断"抽象文档合法性"时会被剪掉，
所以可以安全携带 —— 它们是**生态扩展**，不是 TTML 语法错误。

### 3.2 文档结构（§7.1）

```
tt            → (head?, body?)
head          → (metadata | ttm:title/desc/copyright/agent)*, parameters*, styling?, layout?
body          → metadata*, animation*, div*
div           → metadata*, animation*, (p | div)*
p / span      → metadata*, animation*, (文本 | br | span)*     ← 混合内容
br            → metadata*, animation*
```

要点：
* `<div>` 里放 `<p>`；**不要用 `<br>` 分行**（AMLL 规范 §7.2 明确要求用 `<p>` 分行）。
* 已经**跳过 `<region>`/`<style>`**：AMLL / Apple 的逐字歌词不需要，保持 `<head>` 里只有 `<metadata>` 最省事。

### 3.3 `<tt>` 的必需属性（§7.2.2）

XSD 里 `tt.attlist` 引用 `Core.required.attrib.class`，其中 `xml:lang` 是 `use="required"`。
值必须是 BCP-47，例如 `zh-Hans`、`ja`、`en-US`、`zh-Hans-CN`。

### 3.4 内容与元数据（§12）

* `<metadata>` 的内容模型是 `xs:any namespace="##other" maxOccurs="unbounded"`：
  **只允许"非 TT 命名空间"的元素**（`ttm:title` 属于 `ttml#metadata`，算 `##other`），
  **不允许裸文本**。所以 `<metadata>` 里放 `<ttm:agent>`、`<amll:meta>`、`<iTunesMetadata>` 都合法。
* `ttm:agent` 的 `type` 取值：`person | character | group | organization | other`（XSD `metadataAgentType`）。
* `ttm:name` 的 `type` 必填，取值：`full | family | given | alias | other`（本项目只用 `full`）。
* `ttm:agent` / `ttm:role` 这两个**属性**可以出现在 `body` / `div` / `p` / `span` / `br` 上
  （XSD `ttm:Metadata.attrib.class`，被这些元素的 attributeGroup 引用）。
* `ttm:agent` 的类型是 **`xs:IDREFS`**（可以是多个 ID 的空格分隔列表）；本项目每行只写一个。
* `ttm:role` 的类型是 **列表**：每一项是
  `metadataRoleBaseType`（`action|caption|description|dialog|expletive|kinesic|lyrics|music|narration|quality|sound|source|suppressed|reproduction|thought|title|transcription`）
  或匹配 `x-\c+` 的 NMTOKEN。
  ⇒ **`x-bg`、`x-translation`、`x-roman`、`x-duet-a`、`x-duet-b`、`x-narration` 全部合法**
  （`x-` 前缀是规范给出的私有扩展写法）。

### 3.5 时间表达式（§10.3.1）—— 本项目踩过的最大坑

规范里的语法（TTML2 §12.3.1 给了逐字产生式，TTML1 的 `clock-time` 与之相同）：

```
<timeExpression> : clock-time | offset-time

clock-time   : hours ":" minutes ":" seconds ( fraction | ":" frames ( "." sub-frames )? )?
offset-time  : time-count fraction? metric
metric       : "h" | "m" | "s" | "ms" | "f" | "t"
```

推论（务必记住）：
* `hours` 是**产生式的一部分，不是可选项** ⇒ `00:04.370`（分:秒.毫秒）**非法**。
* `<time-count>` 后面必须有 `metric` ⇒ 裸 `4.370` **非法**；`4.370s` 才合法。
* `frames` 只在 `ttp:timeBase="smpte"` 时有意义（默认 `media`，所以本项目不输出帧段）。
* `minutes`/`seconds` 是两位数字；`seconds` 允许 `60`（闰秒）。

**AMLL 规范 §3.1** 额外接受三种写法（`ss` / `mm:ss` / `hh:mm:ss`，可带 `.xxx`），
并明确**禁止**时间串里出现字母（所以 AMLL 反过来不接受 `10.0s`）。

> ⇒ **两边取交集 = `HH:MM:SS.mmm`**。这就是本项目默认输出 `00:01:05.432` 的原因：
> 既严格合法 TTML1，又是 AMLL 明确列出、其解析器实测能读的格式。
> 只有显式选择 `--time-format mmss|seconds` 才会输出 AMLL 私有宽松写法，此时自检会降级为警告。

### 3.6 时间区间（§10.4）

* 元素的时间区间由 `begin`/`end`/`dur` 组合决定；`begin` 应早于 `end`。
* 子元素的区间应当落在父元素区间内（AMLL 规范 §6-2 把这条写成硬性要求：
  "`<span>` 必须在 `<p>` 内，`<p>` 必须在 `<div>` 内"）。
* 同一演唱者的 `<p>` 不能重叠；不同演唱者可以重叠（AMLL 规范 §6-4）。

### 3.7 `xml:id` / `xml:lang` / `xml:space`（§7.2）

* `xml:id` 必须是合法 NCName 且全文唯一 —— 本项目用 `L1`、`L2`…
* `xml:space` 默认 `default`（空白折叠）。本项目把每行 `<p>` 写成**单行内联 XML**，
  避免 span 之间的换行空白被折叠掉（AMLL 规范 §3.3 也这么要求）。

### 3.8 扩展属性怎么放（§4 + XSD）

`Core.attrib.class` 里有 `<xs:anyAttribute namespace="##other" processContents="lax"/>`，
所以任何元素上都能挂 `itunes:*` / `amll:*` 这类外部属性。
TTML1 §4 的合法性判定也会把外语命名空间的属性剪除掉。
⇒ `itunes:timing="Word"`、`itunes:key="L1"`、`itunes:songPart="Verse"`、`amll:obscene="true"` 都没问题。

### 3.9 一个容易误解的点：schema ≠ 全部约束

TTML1 §4.1 的 Note 明确说：**官方 schema 只覆盖语法的一部分，是一份超集**，
用 schema 校验通过 ≠ 合法（例子：`tts:fontFamily` 在 schema 里只是 `xs:string`）。
时间表达式的 XSD 类型就是 `xs:string`，**所以 schema 校验查不出 `MM:SS.mmm` 的问题**，
必须按 §10.3.1 的产生式自己校验（本项目的 `is_valid_time_expression()` 就是干这个的）。

---

## 4. AMLL TTML DB 规范要点（生态硬性要求）

来源：https://github.com/amll-dev/amll-ttml-db/wiki/格式规范

### 4.1 文件骨架

```xml
<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:tts="http://www.w3.org/ns/ttml#styling"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xmlns:amll="http://www.example.com/ns/amll"
    xml:lang="zh-Hans"
    itunes:timing="Word">
  <head><metadata>…</metadata></head>
  <body dur="00:04:48.000">
    <div begin="..." end="..." itunes:songPart="Verse">
      <p begin="..." end="..." itunes:key="L1" ttm:agent="v1">
        <span begin="..." end="...">字</span>…
      </p>
    </div>
  </body>
</tt>
```

* `itunes` 命名空间：**Apple Music 与 AMLL 参考实现用的都是
  `http://music.apple.com/lyric-ttml-internal`**（线上库文件也是）。
  AMLL 规范文档里写的是 `http://itunes.apple.com/lyric-ttml-extensions`，
  其解析器对两种都认（按 local-name 兜底）。**本项目输出前者，校验器两种都接受。**
* `itunes:timing`：`Word` / `Line`（可选，不影响解析行为）。
* `itunes:songPart`（驼峰，当前推荐）；`itunes:song-part`（短横线）已弃用但仍兼容。
* `body@dur` 若存在，**必须 ≥ 文件中最后一个时间戳的结束时间**。

### 4.2 元数据

* `<ttm:agent type="person|group|other" xml:id="v1">` + 可选 `<ttm:name type="full">`。
  约定：`person`/`character`/`organization` 从 `v1` 递增；`group` → `v1000`；`other` → `v2000`。
* `<amll:meta key=… value=…/>`：`musicName`、`artists`、`album` **必填**（单曲时 album 与歌名相同），
  平台 ID（`ncmMusicId`/`qqMusicId`/`appleMusicId`/`spotifyId`）至少一个，另有 `isrc`、
  `ttmlAuthorGithub`、`ttmlAuthorGithubLogin`。
* Apple 样式翻译/音译放在 `<metadata><iTunesMetadata xmlns="http://music.apple.com/lyric-ttml-internal">`：
  `<translations><translation type="subtitle" xml:lang="…"><text for="L1">…</text>`；
  音译用 `<transliterations><transliteration xml:lang="ja-Latn">`；词曲作者用 `<songwriters><songwriter>`。
  **`for` 的值必须等于正文里对应 `<p>` 的 `itunes:key`。**

### 4.3 时间轴硬性规则（规范 §6）

1. `begin` 必须早于 `end`；
2. 子元素时间必须完全被父元素包含；
3. 所有时间戳必须在 `body@dur` 之内；
4. 同一演唱者的 `<p>`/`<span>` 不能重叠（混音造成的重叠除外）。

### 4.4 逐字歌词与空白（规范 §4.1、§3.3）

* 每个音节一个 `<span begin end>`；空格是**有意义的字符**：
  推荐把空格写在 `<span>` 里面（`<span>You </span>`）或写成两个 span 之间的文本节点。
* 不要"美化格式化"`<p>` 内部：span 后面跟换行会让空白丢失。本项目把 `<p>` 写成一行。

---

## 5. Apple Music / AMLL 生态事实（实现层）

### 5.1 apple 内部扩展

| 扩展 | 说明 |
| --- | --- |
| `itunes:timing="Word"` | 逐字模式标记 |
| `itunes:key="L1"` | 行号（**AMLL 解析器用它当行的唯一 ID**） |
| `itunes:songPart` | 段落名（Verse/Chorus/…） |
| `ttm:role="x-bg"` | 背景人声（Apple AirPlay 之外的内部扩展） |
| `ttm:role="x-duet-a" / "x-duet-b"` | 对唱双方（Apple 内部扩展；**AMLL 官方推荐的"对唱"表达是用不同的 `ttm:agent`**，这两个角色属于额外信息，只有 `--markers role|both` 才写） |
| `ttm:role="x-translation"` / `"x-roman"` | 行内翻译 / 音译 |
| `amll:obscene="true"` / `amll:empty-beat="n"` | AMLL 自己的扩展属性 |
| `<span tts:ruby="container|base|textContainer|text">` | Ruby（假名/拼音注音），TTML2 特性 |

### 5.2 AMLL 解析器的真实行为（`packages/ttml/src/parser.ts`）

这些行为决定了"文件怎么写才能在播放器里显示出来"：

1. **`itunes:key` 没有 = 整行被丢弃**
   `processLineElement()` 第一句就是 `const id = this.getAttr(p, NS.ITUNES, "key"); if (!id) return;`
   ⇒ 只写 `xml:id` 不写 `itunes:key` 的文件在 AMLL 里会**丢行**。
2. **属性查找有 local-name 兜底**
   `getAttr()` 先按命名空间找，失败再按"前缀:名"找，最后**遍历所有属性比 local-name**。
   ⇒ 两种 `itunes` 命名空间都能读，但**必须**有这个局部名（`key`、`timing`、`songPart`）。
3. **时间解析**：`parseTime()` 先看是否以 `s` 结尾（`15.8s`），否则用正则
   `^(?:(?:(?<hours>\d+):)?(?<minutes>\d+):)?(?<seconds>\d+(?:\.\d+)?)$`
   ⇒ `hh:mm:ss.mmm`、`mm:ss.mmm`、`ss.mmm` 三种都认。
4. **背景人声不写进本行文本**
   `processElementNode()` 里 `case RoleBg` 只做 `state.backgroundVocal = parseBackgroundVocal(el)`，
   **不往 `state.fullText` 里加东西**。
   ⇒ 如果一个 `<p>` 里**只有** `x-bg`，解析结果是：这一行 `text=""`、`words=undefined`、
   只有一个 `backgroundVocal`。主歌词为空、播放器就"看不到内容"。
   **这就是必须把整行括号的背景人声并进相邻主唱行的原因。**
5. 段落名：优先读 `itunes:song-part`，其次 `itunes:songPart`。
6. `<p>` 的 `begin`/`end` 通过 `getAttributeNS(XML, "begin")` + `getAttribute("begin")` 读取。

### 5.3 AMLL 自己生成的文件长什么样

`packages/ttml/tests/__snapshots__/generator.test.ts.snap` 里的快照（节选）：

```xml
<tt xmlns:amll="…/amll" xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xmlns:ttm="…/ttml#metadata" xmlns:tts="…/ttml#styling" itunes:timing="Word"
    xmlns="http://www.w3.org/ns/ttml">
  <head><metadata>
    <ttm:agent type="person" xml:id="v1"/>
    <iTunesMetadata xmlns="http://music.apple.com/lyric-ttml-internal">…</iTunesMetadata>
    <amll:meta key="musicName" value="…"/>
  </metadata></head>
  <body dur="3.000">
    <div begin="1.000" end="3.000">
      <p begin="1.000" end="3.000" itunes:key="L1" ttm:agent="v1">
        <span begin="1.000" end="2.000">你</span><span begin="2.000" end="3.000">好</span>
        <span ttm:role="x-translation" xml:lang="en">你好</span>
        <span ttm:role="x-bg" begin="3.000" end="5.000">
          <span begin="3.000" end="4.000">(世</span><span begin="4.000" end="5.000">界)</span>
        </span>
      </p>
    </div>
  </body>
</tt>
```

注意：**它自己用裸秒**（`1.000`），但解析器同样接受 `00:00:01.000`。
本项目选择 `HH:MM:SS.mmm` 是为了同时满足 W3C 与 AMLL 两边；
**背景人声嵌在同一个 `<p>` 里、括号用半角、翻译用行内 span** 这几条与它完全一致。

---

## 6. 本项目实现地图

| 文件 | 职责 | 关键点 |
| --- | --- | --- |
| `preprocess.py` | 歌词解析：LRC/纯文本 → `LyricLine` | `tokenize_chinese()`（括号=背景人声）、`detect_agent_switch()`（`[v1]/[v2]/[和声]/[旁白]` 标记）、**`merge_background_lines()`（整行括号并入相邻主唱行）** |
| `asr.py` | faster-whisper 词级时间戳 | 可对人声分离后的音轨跑 |
| `separate.py` | Demucs 人声/伴奏分离（`--separate-vocals`） | 分离结果缓存在 `outputs/*.vocals.wav` |
| `matcher.py` | 歌词 ↔ ASR 的 DP 强制对齐 | `Unit`（`core/leading/trailing/is_bg/muted`）、`AlignedLine`（`agent/role/paragraph/translation`）、主唱与背景**分开归一化**、按**文本顺序**排时间 |
| `diarize.py` | CAM++ 声纹聚类判断"这句谁唱" | `SIM_THRESHOLD = 0.35` 平均连接聚类；`--split-singers N` 可强制分组 |
| `ttml_writer.py` | `AlignedLine[]` → TTML 文本 | 时间表达式层、XML 清洗、时间区间归一化、命名空间、元数据 |
| `ttml_check.py` | 确定性自检 | `check_ttml(text, strict=True)`；结构 + §10.3.1 + 包含关系 + 重叠 + `L?` 连续性 |
| `lyric_style.py` | Apple 歌词风格准则（大小写、行尾标点、脏话屏蔽 `MASK`） | `--style apple` / `--explicit-policy mask` |
| `pipeline.py` | 6 步流水线 | 解析 → 分离人声 → ASR → 对齐 → 精修 → 声纹分段 → 输出 + 自检 |
| `main.py` | CLI | 见 `--help`；`--check-only` 可单独校验文件 |
| `web_server.py` + `index.html` | 本地 Web UI | 参数与 CLI 一一对应 |
| `tools/inspect_ttml.py` | 体检一份 TTML（命名空间/时间格式/x-bg/x-duet/agent/整行背景 p） | 每次生成后跑 |
| `tools/test_bg_merge.py` | 背景人声合并回归用例（含真实 LRC） | 改 preprocess 后跑 |
| `tools/verify_from_report.py` | 用 `outputs/*.report.json` 重建 TTML + 独立结构校验（不需要 ASR） | 改 writer 后跑 |
| `tools/make_bg_demo.py` | 生成"背景人声已合并"的参考样例 | 演示用 |
| `selftest.py` | 离线自检（不联网、不用大模型） | **改完代码必须跑** |

### 6.1 `ttml_writer.py` 里不可回退的实现

```python
format_time(seconds, style)          # clock → "HH:MM:SS.mmm"；mmss / seconds 是 AMLL 私有宽松写法
is_valid_time_expression(v)          # TTML1 §10.3.1 严格校验（含 offset-time 单位、帧段限制）
is_amll_relaxed_time(v)              # AMLL 宽松写法识别（用于降级为警告）
parse_time(v)                        # 读回：hh:mm:ss[.fff] / hh:mm:ss:ff / 12.5s / 250ms / 兼容宽松写法
xml_safe() / _esc() / _attr()        # XML 1.0 非法字符清洗 + 转义（所有文本/属性都必须走这里）
TTMLWriter._line_interval()          # 行的 p 区间由 span 反推，保证 begin < end
TTMLWriter._unit_interval()          # span 夹进 p 区间，零时长撑到 1ms
write()                              # body@dur = max(传入时长, 内容结束时间)；缺 agent 补 v1
```

### 6.2 `ttml_check.py` 的严格/宽松两种模式

* `check_ttml(text, strict=True)`：默认。时间表达式按 TTML1 §10.3.1 报**错误**。
* `check_ttml(text, strict=False)`：AMLL 宽松写法降级为**警告**（用于校验外部文件）。
* CLI：`python main.py --check-only 文件.ttml`（宽松） / 加 `--strict-time`（严格）。

---

## 7. 背景人声与对唱（重点章节）

### 7.1 背景人声（`x-bg`）

**正确形态**（AMLL 规范 §7.3 + AMLL 参考实现）：背景人声是**某一行的子内容**，
写在同一个 `<p>` 里；如果它在主唱之前就放在主唱 span 前面，否则放在 `<p>` 末尾。
括号用半角 `(` `)`，只包一层。

**`x-bg` 包装层要写 `begin`/`end`，并且必须包住它的子 span**：
AMLL 参考实现的快照（§5.3）与下面的示例都带；解析器 `case RoleBg` 会把整层交给
`parseBackgroundVocal(el)`，没有时间戳的一层拿不到自己的区间。本项目自 2026-10-09 起写入，
`ttml_check.py` 也会校验"子 span 是否超出包装层区间"。

**⚠️ 歌词里的演唱者标记必须在合并之前剥掉**：`[v1]（一路践行）` 这类和声行，
分词后 `v1` 也会被判成背景 token（`_all_background` 为真），整行会被并进主唱行；
若等合并完再剥，`[v1]` 已跑到行中间，`detect_agent_switch()` 只认行首 → 标记渲染进歌词，
输出成 `(v1一路践行)`。所以 `preprocess._strip_marker()` 在 `parse_lyrics()` 里逐行先剥、后并。

```xml
<p begin="00:01:05.510" end="00:01:12.170" itunes:key="L11" ttm:agent="v1">
  <span begin="00:01:05.510" end="00:01:05.890">有</span>…<span begin="00:01:09.450" end="00:01:09.850">行</span>
  <span ttm:role="x-bg" begin="00:01:10.950" end="00:01:12.170">
    <span begin="00:01:10.950" end="00:01:11.110">(一</span>
    <span begin="00:01:11.110" end="00:01:11.450">路</span>
    <span begin="00:01:11.450" end="00:01:11.890">践</span>
    <span begin="00:01:11.890" end="00:01:12.170">行)</span>
  </span>
</p>
```

**曾经的问题**：LRC 里"和声"常常是**独立一行**（`[01:10.95]（一路践行）`）。
旧实现把它当成一行独立的歌词 → 输出：

```xml
<p begin="00:01:10.950" end="00:01:12.170" itunes:key="L12" ttm:agent="v1">
  <span ttm:role="x-bg">…</span>          <!-- 只有背景人声，没有主歌词 -->
</p>
```

AMLL 解析这种行时 `text=""`、`words` 为空（见 §5.2 第 4 条），播放器里**背景人声不显示**。

**已实施的修复**：`preprocess.merge_background_lines()`（在 `parse_lyrics()` 末尾调用）
把"整行都在括号里"的行并进相邻主唱行：

* 背景行在主唱行之后 → 追加到上一主唱行的 token 末尾（并延长该行的 `line_end`）；
* 背景行在主唱行之前 → 并进下一主唱行，token 放最前面（并前移 `line_start`）；
* 两行开始时间相差超过 `BG_MERGE_MAX_SPAN = 12.0` 秒 → 不合并，保持独立行。

合并后 token 上仍带 `is_background=True`，对齐时主唱/背景分别归一化，
写文件时自然落到同一个 `<p>` 的 `<span ttm:role="x-bg">` 里。

**效果对照**（同一份 `report.json`，只差这一步）：

| | `<p>` 行数 | `x-bg` | 整行只有 `x-bg` 的 `<p>` | 严格校验 |
| --- | --- | --- | --- | --- |
| 修复前 `outputs/在出发之前.ttml` | 37 | 5 | **5** ← 播放器看不见 | 0 错误 |
| 修复后 `outputs/在出发之前.bg-merged.ttml` | 32 | 5 | **0** | 0 错误 |

回归用例：`tools/test_bg_merge.py`（含真实 LRC）；selftest [7] 覆盖"整行括号并进上一行"；
`ttml_check.py` 也会对"整行只有 x-bg"发**警告**，收到就说明回退了。

### 7.2 对唱（谁唱的）

AMLL/Apple 表达"对唱"的**标准差**是**不同的 `ttm:agent`**：

```xml
<ttm:agent type="person" xml:id="v1"/>   <!-- 歌手 A -->
<ttm:agent type="person" xml:id="v2"/>   <!-- 歌手 B -->
…
<p begin="…" end="…" itunes:key="L3" ttm:agent="v2">…</p>
```

`ttm:role="x-duet-a"/"x-duet-b"` 是 Apple 的内部扩展，**只有 `--markers role|both` 才会写**；
默认 `--markers agent` 只写 agent（AMLL 官方推荐做法，播放器照样会区分两人）。

本项目的三条路径：

1. **歌词里手写标记**（最可控）：行首写 `[v1]`/`[v2]`/`[duet-a]`/`[duet-b]`/`[男声]`/`[女声]`/`[对唱]`/`[和声]`/`[旁白]`，
   `preprocess.detect_agent_switch()` 会剥离标记并写 `agent/role`；一旦有任何一行带标记，就**跳过**声纹自动分段。
2. **`--auto-agents`**：用 CAM++ 声纹对每一行做嵌入聚类（`diarize.py`，阈值 0.35）自动给出 `v1`/`v2`…。
3. **`--split-singers 2`**：强制聚成 2 组（当自动判断"判定为单人演唱"但你知道是两个人时用）。

**已知短板（截至 2026-10-09）**：真实对唱曲目（如 `在出发之前`，陶典 & hanser）在
**人声分离后的 vocal 轨上被聚成 1 类**（`diarize` 报告"判定为单人演唱，簇内平均相似度 0.58"），
于是整首只有 `v1`，听感上"没有对唱"。

> **补充实证（2026-10-09，排查过一遍）**：把 `diarize` 直接喂 **LRC 原始行跨度**（含开头
> 作词/作曲与结尾制作人员行）时会"判出 2 个声部"，但那 11 行**全是制作人员信息**
> （作词/作曲/编曲/伴唱/录音棚…），不是第二个歌手 —— 属于**假阳性**。
> 也就是说：这首歌在对齐后的真实演唱片段里，CAM++ 确实分不出第二个人。
> 因此这不是"流水线传错跨度"，而是方法本身的限制（两位歌手音色接近 + 和声段太短）。

可选处置：

* 在歌词里加 `[v1]`/`[v2]` 标记（立即生效、最可靠）；
* `--split-singers 2` 强制两簇，再人工核对；
* 调 `diarize.SIM_THRESHOLD`（0.35）或在**全混音**而不是分离人声上做声纹（两条轨各有胜负）；
* 同时张嘴的齐唱/和声无法按行拆分（一行里混了两个声纹），这是方法本身的限制。

### 7.3 翻译 / 音译

* 行内：`<span ttm:role="x-translation" xml:lang="zh-Hans">…</span>`、`x-roman`。
* 头部（Apple 样式）：`<iTunesMetadata><translations><translation type="subtitle" xml:lang="…"><text for="L1">…</text>`。
* **不要同一语言两种都写**，否则 AMLL 会双重翻译（规范 §5 的警告）。
* 背景人声的翻译：在 `<text>` 里用 `<span ttm:role="x-bg">` 包住对应文本。

### 7.4 对照官方《AMLL TTML DB 格式规范》逐条核验（2026-10-09 补）

用户提供了上游规范原文（`apoint123` 维护的 wiki 页）。逐条比对后**修正了 3 处真实偏离**：

| 规范条款 | 我们原来的实现 | 现在 |
| --- | --- | --- |
| §4.1 `group (合唱)` → `type="group" xml:id="v1000"` | `[合唱]` 被当成 **agent v2** ❌ | → `v1000`，head 里 `type="group"` ✅ |
| §7.3 背景人声是**行的子内容** `x-bg` 层，不是演唱者 | `[和声]`/`[bg]` 被当成 **agent v2** ❌ | → 整行并进相邻主唱行成为 `x-bg`；原文没括号会自动补一对半角括号 ✅ |
| §4.1 `ttm:name` 用于演唱者 | 按 `used` 顺序硬套 `artists[i]`，**歌手名会被安到 group 上** ❌ | 只给 `person`/`character`/`organization` ✅ |

新增的 3 条校验（都只警告、不阻断）：

| 规则 | 依据 |
| --- | --- |
| `type="group"` 的 id 应为 `v1000`；`v1000` 的 type 应为 `group`；`other` 用 `v2000`；type 只能取五个值 | §4.1 |
| 同一语言在 head 翻译与行内 `x-translation`/`x-roman` 各写一份 → 会双重翻译 | §5 Caution |
| 背景人声必须**只包一层半角括号**，不能套两层、不能只写半边 | §7.3 |

**规范原文确认的两件事**（此前我只能靠推断）：

1. **对唱 = 不同的 `ttm:agent` 挂在 `<p>` 上**（§7.2）。规范全文**没有** `x-duet-a`/`x-duet-b` ——
   那是 Apple 内部扩展。所以"没有左右分栏"的根因永远是 **`<p>` 上只有 `v1`**。
2. `x-bg` 包装层的 `begin`/`end` **是可选的**（§7.3）；我们写上去属于允许的超集，
   且与 AMLL 参考实现一致，保留。括号要求半角、只包一层 —— 与我们一致。

**已知的无害差异**：规范骨架示例把 `xmlns:itunes` 绑到
`itunes.apple.com/lyric-ttml-extensions`，我们绑到 `music.apple.com/lyric-ttml-internal`
（AMLL 生成器快照用的就是后者）。解析器按 local-name 兜底，两者都能读；
`<iTunesMetadata>` 的默认命名空间两边都是后者。

---

## 8. 自检清单（每次生成新文件后过一遍）

```powershell
# 1) 离线单测（不需要音频/网络/大模型）
python selftest.py                     # 期望：全部自检通过 ✓

# 2) 严格校验生成的文件（把 <文件> 换成实际路径）
python main.py --check-only <文件>.ttml --strict-time
#    期望：格式通过：0 个错误

# 3) 结构化体检：命名空间 / 时间格式 / x-bg / x-duet / agent / 整行背景行
python tools/inspect_ttml.py <文件>.ttml
#    期望：时间戳样例像 00:00:04.370；"整行只有 x-bg 的 p" = 0；"p 缺 ttm:agent" = 0
```

（`tools/inspect_ttml.py`、`tools/test_bg_merge.py`、`tools/verify_from_report.py`
都不需要重跑 ASR：最后一个用 `outputs/*.report.json` 就能重建 TTML 做回归。）

人工检查清单：

- [ ] 所有 `begin/end/dur` 都是 `HH:MM:SS.mmm`
- [ ] `span ⊆ p ⊆ div ⊆ body@dur`，没有 `begin == end`
- [ ] 每个 `<p>` 都有 `itunes:key`（L1 起连续）与 `ttm:agent`，且 head 里定义了该 agent
- [ ] **没有任何 `<p>` 是"整行只有 x-bg"**
- [ ] 对唱曲目里能看到多个 `xml:id="vN"` 与对应的 `ttm:agent="vN"`
- [ ] 需要 Apple 内部角色时用了 `--markers both`（否则只有 agent）
- [ ] UTF-8 无 BOM；`<p>` 内部单行
- [ ] `.report.json` 里的 `notes` 没有"判定为单人演唱"而实际是多人歌（有的话按 §7.2 处置）

---

## 9. "症状 → 原因 → 修法" 对照表

| 症状 | 根因 | 修法 |
| --- | --- | --- |
| 播放器里**背景人声不显示** | 整行括号被当成独立行，输出成"只有 x-bg 的 `<p>`"，AMLL 解析后主歌词为空 | `preprocess.merge_background_lines()`（已实现）；校验"整行只有 x-bg"计数为 0 |
| **没有对唱**、整首都是 `v1` | 声纹分段判成单人；歌词无 `[v1]/[v2]` 标记；`x-duet-*` 只在 `--markers role/both` 输出 | 歌词加标记 / `--split-singers 2` / 调 `diarize.SIM_THRESHOLD`；要 Apple 内部角色就 `--markers both`（详见 §7.2） |
| 文件解析失败 / 播放器报错 | 文本里混入 XML 1.0 非法字符；或 span 越出 `<p>`；或 `begin == end` | 走 `_esc()/_attr()`；writer 的区间归一化已兜底；`--check-only --strict-time` 会报出来 |
| 字在播放时跳来跳去 | span 的时间顺序和文本顺序不一致 | `matcher._normalize_group()` 按文本顺序排（已被注释强调，勿改） |
| 空白/空格丢失 | `<p>` 内部被格式化换行 | 保持 `<p>` 单行；空格写进 `<span>` 内 |
| 时间轴整体偏移 | LRC 有 `[offset:]` 或音频与歌词版本不一致 | 对齐前检查 LRC；`--pause-space` 只影响停顿空格 |
| 校验"通过"但文件其实非法 | 用官方 schema 或旧的正则校验（只查 `\d+:\d+`） | 用 `is_valid_time_expression()` / `--strict-time`（见 §3.9） |
| `MM:SS.mmm` 被判非法 | 文件是**旧版本**生成的 | 重新生成（新 writer 输出 `HH:MM:SS.mmm`） |

---

## 10. 变更纪律

1. 改 `ttml_writer.py` / `ttml_check.py` / `preprocess.py` / `matcher.py` 之后：
   `python selftest.py` 必须全绿；再重新生成一份 TTML 并跑 `--check-only --strict-time`。
2. 改输出格式（时间写法、命名空间、`<p>` 结构）时，**同步更新本文件与 `AGENTS.md`**，
   否则其他会话会按旧规则"修回去"。
3. 不要为了"看起来整齐"给 `<p>` 内部加换行；不要给 `<tt>` 加无意义的 `<region>/<style>`。
4. 拿不准某条规则时，先看 §1 的规范链接；网络不可用时以本文件为准。
5. 多会话并行改同一批文件时，写之前重读文件（避免覆盖别人的改动），写完立刻跑测试。

---

## 附录 A：本文档核对过的具体事实（便于复查）

* TTML1 `tt.attlist` = `Core.required.attrib.class`（`xml:lang` required）+ `ttp:Parameter.attrib.class` + `tts:extent`。
* `metadata.eltype` = `xs:sequence` 的 `xs:any namespace="##other"`（不允许裸文本）。
* `body/div` 内容 = `Metadata.class*`、`Animation.class*`、`div/p*`；`p/span` 为 mixed。
* `ttm:agent/ttm:role` 属性出现在 `body/div/p/span/br` 的 attlist 里。
* `ttd:agent` = `xs:IDREFS`；`ttd:roleItem` = `metadataRoleBaseType | NMTOKEN{pattern="x-\c+"}`。
* TTML1 §10.3.1 的 `clock-time` = `hours ":" minutes ":" seconds ( fraction | ":" frames ( "." sub-frames )? )?`。
* AMLL 规范 §3.1：三种时间写法 + 禁止字母；§4.1/§4.2/§6/§7.2/§7.3 见上文引用。
* AMLL `parser.ts`：`itunes:key` 缺失即 `return`；`getAttr()` 有 local-name 兜底；
  `TIME_REGEX = ^(?:(?:(?<hours>\d+):)?(?<minutes>\d+):)?(?<seconds>\d+(?:\.\d+)?)$`；
  `case Values.RoleBg` 不写 `fullText`。
* AMLL `generator.test.ts.snap`：命名空间、`iTunesMetadata`、`x-bg` 嵌在 `<p>` 内、行内 `x-translation`。

## 附录 B：术语

| 词 | 含义 |
| --- | --- |
| `x-bg` | background vocal，背景/和声人声 |
| `x-duet-a/b` | Apple 内部的对唱角色标记（AMLL 官方更推荐用不同 `ttm:agent`） |
| `x-translation` / `x-roman` | 行内翻译 / 音译 |
| `itunes:key` | 行号，AMLL 用它做行的唯一 ID；缺失会丢行 |
| `clock-time` / `offset-time` | TTML 的两类时间表达式 |
| 严格模式 | 按 W3C TTML1 §10.3.1 校验时间表达式（`--strict-time`） |
