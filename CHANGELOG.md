# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

> 说明：本项目在首次公开发布前已经过长期迭代，下面把公开发布前的主要阶段
> 一并整理出来，便于使用者了解各功能的能力边界与踩过的坑。

---

## [未发布]

**修复 WinUI 编辑台三个用户反馈缺陷：删不掉词、声部角色错位、文本操作全失败。**

### 修复

- **编辑台没有删除入口**：生成出来的歌词里多余的字/行以前无法删除。工具栏新增
  「🗑 删除这个字」「🗑 删除这一行」（先选字/行，二次确认）。时间分配（同层后字往前顶）
  与 key 重编号**全部走服务端** `delete_word` / `delete_line`，前端不自己算时间。
- **改声部后 Apple Music 左右分栏错位**（用户实测："选 1 声部显示成 2 声部，
  选 2 声部显示成 1 声部"）：有两个独立原因，都已修：
  1. 改 `ttm:agent` 时**没有同步行级 `ttm:role`**（`x-duet-a/b`），写出
     `ttm:agent="v2" ttm:role="x-duet-a"` 这种自相矛盾的行，而 Apple Music 按 role
     分栏 ⇒ 整行跑到相反那一栏。现在改声部时 role 同步更新（v1→x-duet-a /
     v2→x-duet-b / 旁白→x-narration / 合唱→清空）；且 **agent 没变但 role 已矛盾**
     的行也允许保存 ⇒ 把声部重新选一遍即可修复旧文件。
  2. **声部编号本身没有稳定语义**：自动分声部直接用 k-means 簇号（`v{lab+1}`），
     簇号任意 ⇒ 先开口的声部可能被编成 v2；而 Apple Music 的对唱左右栏按声部
     **首次出现的顺序**排栏 ⇒ v1 的行走到了第二栏。现在 `pipeline` 按「首次出现」
     重排 v1/v2（`renumber_by_first_seen`）；`ttml_writer` 的 head agent 声明也
     改成**升序 v1, v2, v3…**（原来按首次出现，v2 先唱就排在最前）。
     旧文件用 `tools/fix_agent_order.py` 批量重排（支持 `.ttml` 与编辑台草稿
     `.edit.json`，自动备份，回写前比对新旧错误集合、不引入新错误）。
- **WinUI 的文本/和声操作一直失败**：`HttpEditBackend.TextOpAsync` 把 payload
  **平铺**在请求体根上，后端 `_post_text` 只读 `payload` 嵌套 ⇒ 每次都以
  "找不到这一行"被拒（保存和声、删除和声、以及新增的删除都受影响）。已按后端契约
  改为嵌套，并抽出纯函数 `TextOpBody.Build` 让测试锁住请求体形状。
- **编辑台试听播 1~2 秒就停**：本地直读把路径转成 `file://` URI，而 Media Foundation
  对**非 ASCII（中文）路径**的 percent-encoded URL 解析失败（实测
  `HRESULT 0x80070003 / SourceNotSupported`；同一文件放 ASCII 路径就正常）⇒ 回退 HTTP
  流；60+MB 的无损 FLAC 边播边供供不上，于是播一两秒就停。改用
  `StorageFile.GetFileFromPathAsync` + `MediaSource.CreateFromStorageFile`
  （Unicode 路径原生支持）——实测同一中文路径 238s 正常打开、播放位置正常前进。
- **帮助/诊断的"5 项需要处理"是误报**（两个原因都修）：
  1. 页面把后端字段读错了（读 `scope`，实际字段是 `level`），也**没有按级别区分** ——
     把"打包版不含 torch/demucs"这类**可选**项都算成"需要处理"；
  2. 冻结后端的 `doctor` 搜索路径**不包含运行库安装目录**
     （`%LOCALAPPDATA%\Lyric2TTML\runtime\…`），torch/CUDA 明明已装好、运行库页
     也显示"真实加载通过"，医生仍报缺失。
  现在正确解析 `key/name/why/level/ok/detail/fix`（必需/可选/信息三档），并用
  「运行库 / 分离 Worker」的**真实状态**校正（torch→运行库 torch、cuBLAS→运行库 cuda、
  demucs→Worker）；缺的才计数，cuDNN 确实没装（可选）就如实显示。处理建议也改成
  本界面能点到的「运行库 → 安装/修复」（不再照抄安装器的"开始补全"）。
- **人声分离报 `invalid_output：写出/校验失败：Error: file does not start with RIFF id`**
  （FLAC/MP3 输入时必现）：反汇编 Worker 0.1.0 确认根因 —— 它在算"输入时长"时用
  **stdlib `wave` 打开输入文件**（`getnframes()/getframerate()`），而 `wave` 只认
  RIFF/WAV，输入 FLAC 时直接抛 `file does not start with RIFF id`，被包装成
  "写出/校验失败"，于是分离/句尾拖音补全整体跳过。Worker 是外部冻结包（源码不在本仓库），
  改字节码风险太高 ⇒ 新增**输入格式包装器**：`DemucsWorker.exe` 发现输入不是 WAV 时
  先用 ffmpeg 转成 16-bit PCM WAV 再转发给真实 Worker（原 exe 改名
  `DemucsWorker.core.exe` 保留），`--selftest/--version` 原样转发（运行库页校验不受影响），
  临时文件用完即删。实测：同一 FLAC 分离成功（ok:true, 输出 6.8MB RIFF WAV），
  端到端转换 `[分离] 完成（Worker cuda，9.8s）`、句尾拖音补全 24 处、匹配率 100%。
  部署/回滚脚本：`tools/deploy_worker_wrapper.ps1`（源码 `tools/demucs_worker_wrapper/`）。
  ⚠️ 在「运行库」页卸载/重装 Worker 会覆盖包装器，重装后需重跑一次部署脚本；
  根本修复应在 Worker 侧改用 `soundfile` 读输入时长。

### 新增

- `tools/fix_agent_role.py`：**批量修复已有坏文件**，不必逐行重选声部。只修自相矛盾的
  duet 角色（v1↔x-duet-b、v2↔x-duet-a、v1000 带 duet 角色）；空 role / 旁白 /
  非 duet 角色原样保留，不改任何时间；首次写回自动备份 `.bak-agent-role`，`--dry` 只报告。
  实测：2 处矛盾修复正确、二次运行幂等、AMLL 模式（不写 role）自动跳过。
- `tools/fix_agent_order.py`：**批量修复已有文件的声部编号顺序**（按首次出现重排
  v1/v2，让「一声部」稳定对应 Apple Music 第一栏）。支持最终 `.ttml` 与编辑台草稿
  `.edit.json`；首次写回自动备份 `.bak-agent-order`；回写前比对新旧自检错误集合，
  **不引入新错误**才落盘；`--dry` 只报告。实测：真文件 v2→v1/v1→v2 正确交换、
  head 升序、行数/文本/时间不变、二次运行幂等。
- WinUI 纯逻辑测试新增 59 条（agent↔role 映射、`/api/text` 请求体形状、删字的层内下标
  换算、doctor JSON 字段/级别/运行库校正）；`selftest` 新增 head agent 声明顺序、
  簇编号按首次出现重排的断言。总数：C# 137 → **196/196**；Python 自检全绿。
  三类"故意弄坏"的变异对照都能被 C# 测试抓到（含 doctor 校正：拿不到运行库状态时
  不得乱校正的阴性对照）。

---

## [1.2.0] - 2026-10-25

**TIFA 歌声对齐实验引擎（可选）+ 评测/模型管理配套。**

### 新增

- 新增 `timing_align.py`：接入 TIFA-1.0-ST 歌声音素对齐（实验后端），
  按 LRC 行锚点切 clip、解析 TextGrid texts tier、映射回本项目的 Unit/TTML；
  失败自动回退 Whisper。
- CLI `--align-engine timing-tifa`；Web/WinUI 工作台新增「对齐引擎」选项。
- 模型管理新增 `tifa-aligner` 条目：只做文件 + SHA256 校验，手动安装（模型不随包分发）。
- `tools/eval_word_timing.py` 支持 whisper vs TIFA 的 QRC 真值对比。
- WinUI 冻结后端支持「外部 Python 引擎」：设置里指定 TIFA Python，
  后端通过 `L2T_TIFA_PYTHON` 拉起 TIFA；逐行语言路由（zh/ja/en）也已接入。

### 实测

- 《让风告诉你》《我不曾忘记》《失眠飞行》三首：逐字起始散布
  whisper 0.268/0.188/0.137s → TIFA 0.138/0.111/0.088s；
  ≤0.2s 命中率 54.9/61.0/76.7% → 81.0/85.7/93.6%。

### 限制

- TIFA 分支需要逐句 LRC；当前按中文普通话为主（混唱加了 `-L en`）；
  暂不跑人声分离/拖音补全；模型许可独立，不能随 MIT 安装包分发。

## [1.1.1] - 2026-10-14

**安装阶段不再要求下载大件；中国大陆可以正常下载 GPU 运行时。**

这一版由两个用户反馈驱动：① "软件还没打开就要下 3.5 GB"；
② "CUDA 运行库下不动 —— `WinError 10061 目标计算机积极拒绝`"。

### 变更：安装阶段只管"能不能打开"

- **`envcheck` 新增作用域分层**：`launch`（影响能否打开）/ `convert`
  （处理歌词才要）/ `extra`（可选增强）。
  安装程序**只拦 `launch`**；语音模型（3 GB）与 GPU 运行时（529 MB）
  属于 `convert`，改为在软件内「模型」页下载。
  ⚠️ 未登记的检查项**保守归 `convert`**（宁可少拦，不多拦）。
- 安装完成页文案改为「安装完成，**可以打开了**」，并写明大件到哪下。
- 安装清单里这两类显示为 **`↓ 待下载（「模型」页）`**，与 `✕ 缺失` 区分。
- 新增 `--launch-check`：带 `scope` 的 JSON，安装程序据此判断。

### 修复：中国大陆下载 GPU 运行时

- **新增国内镜像源**：腾讯云 / 阿里云 PyPI 镜像作为备用源
  （实测吞吐 3.91 MB/s / 0.17~8.7 MB/s；GitHub 直连在大陆常被拒）。
- **修掉"多来源回退实际不生效"**：原来第一个来源的 url 全失败就
  **直接抛异常**，第二个来源根本没机会被尝试 —— 也就是说
  "在清单里加镜像源"是**装饰**。现在会依次回退，全部失败才报错，
  并列出**试过哪些来源**。
- **修掉镜像 wheel 的 DLL 布局识别**：自己的 zip 是 `bin/*.dll`，
  PyPI wheel 是 `nvidia/cublas/bin/*.dll`，校验只看前者 ⇒
  明明下好了却判"缺少文件"，**又去重下 528 MB**。新增 `_hoist_dlls()`。
- **修掉假就绪**：`_verify_installed()` 只看**总字节数**，
  把 `bin/` 整个挪走字节数不变 ⇒ 报"文件齐全"，而声明的
  `verify_files` **从未被检查过**。新增 `_missing_declared_files()`。

### 变更：不再下载 cuDNN

- 实测（真 RTX 4060 + 真音频）：`cuBLAS+cuDNN` 1.43~1.50s vs
  **只有 cuBLAS** 1.43~1.59s —— 没有差别，而 cuDNN 白占 1095 MiB。
  安装程序与运行组件都只下 cuBLAS（约 528 MB，原来约 2 GB）。
- 修正错误文案：「没有它只能 CPU，慢很多」→ 本项目**没有 CPU 降级**，
  缺运行库是**直接报错**。

### 文档

- README 更新下载入口与版本号；补 v1.1.1 变更说明。
- 新增 `tools/check_artifact_freshness.py`：产物带上 `build_info.json`
  （构建 commit），并在交付报告里核对"产物内 commit = 当前源码"。
  `--version` 现在会打印构建来源。

---

## [1.1.0] - 2026-10-12

**新增桌面版（PySide6 原生窗口）+ 模型管理器。**

这一版把"浏览器里点来点去"变成"双击图标就进软件"，并补上安装期与运行期
缺失的那一块：**模型的检查、下载、校验、缓存复用**。

### 新增：桌面应用 `desktop/`

- **原生窗口**（PySide6）：欢迎页 / 制作 / 任务 / 模型 / 设置 五页导航。
  所有耗时操作都跑在后台线程，界面不卡；进度、日志、取消都在任务中心里。
- **任务中心**（`desktop/services/task_manager.py`）：统一的任务状态机
  （排队 / 运行 / 校验 / 完成 / 失败 / 取消），可取消、可持久化
  （`state/tasks.json`），**重启后"进行中"的任务会收成"已中断"**，
  不会永远转圈。
- **模型管理器**（`desktop/services/model_manager.py`）+ 清单
  （`desktop/resources/model_manifest.json`）：每个模型声明
  来源 / 缓存位置 / 校验方式，状态 `未安装 → 下载中 → 校验中 → 已就绪`。
  `READY` 的判据是"文件齐全 + 字节数一致 +（有 SHA256 就核 SHA256）"，
  **不是"下载函数没报错"**；半截的 `.part` 永远不会被当成已安装。
- **路径策略**（`desktop/paths.py`）：程序目录 ≠ 数据目录。
  安装版把配置/日志/输出放 `%LOCALAPPDATA%\Lyric2TTML`（`Program Files` 只读），
  放 `portable.txt` 则全部就地存放；**模型缓存优先复用程序目录下已有的
  `.hf_cache`**，老用户升级不用重下 3GB。
- **环境体检**（`desktop/services/environment_service.py` + `gpu_probe.py`）：
  显卡驱动 / CUDA 运行库 / ffmpeg / 磁盘 / 目录可写性 / 每个模型，
  全部翻成"人话 + 怎么办"。轻量检查不碰 torch（不卡界面），
  显卡实测放后台。**不需要 CUDA Toolkit**（驱动提供 `nvcuda.dll`，
  cuBLAS/cuDNN 随程序分发）。
- **后端调用**（`desktop/services/backend_service.py`）与
  **参数映射**（`desktop/services/convert_params.py`）：界面参数到
  `ConvertOptions` 的映射**只此一份**，Web / 桌面 / 命令行三条入口共用；
  自检会比对双方字段，**漏映射一个参数就报错**（这类问题表现为
  "界面选了不生效"，最难查）。
- **产物保护**：TTML 先写 `.tmp` 再原子替换，转换失败**不会覆盖**
  上一次做好的文件。
- **崩溃可查**：`desktop/services/log_service.py` 装了系统级 +
  线程级异常钩子，写 `logs/desktop.log` 与 `logs/crash.log`；
  打包后没有控制台，不装钩子的话崩溃是完全静默的。
- **离线自检**：`python desktop/selftest.py`（14 组，不联网、不开窗口、
  不用大模型），覆盖路径、配置降级、任务状态机、模型校验、
  参数漂移、产物保护、版本一致性，以及一整套**真下载**用例
  （本地 HTTP 服务：断点续传 / SHA256 校验 / 服务器不支持 Range / 取消）。
- **另外三条自检**（都不需要显示设备）：
  ```powershell
  python desktop\selftest.py      # 服务层 14 组（不导入 Qt）
  python desktop\uicheck.py       # 界面 14 项（offscreen + 渲染断言）
  python tools\test_uninstall.py  # 卸载 6 项（**默认保留模型**是数据安全问题）
  python tools\accept_frozen.py <exe>   # 打包版验收（含真转换 + 严格校验）
  ```

### 说明

- 仍然保留原来的浏览器编辑台：桌面窗口里点「编辑台」用系统浏览器打开，
  这样**不引入 QtWebEngine**（否则安装包会再大几百 MB）。
- 识别算法、TTML 写出、校验流程**一行没改**；桌面版只是新的入口与外壳。
- **卸载默认保留模型**（约 3GB）：只有明确选"连模型一起删除"才清掉；
  安装目录里已有的模型会先搬到用户数据目录，**搬不动就什么都不删**。
  数据安全由 `tools/test_uninstall.py` 的 6 项用例守着。

### 打包版实测（2026-10-12）

`Lyric2TTML.spec` 支持 `L2T_WITH_QT=1` 把 Qt 打进包里（默认不带 Qt）。
对**真的打包 exe** 跑 `python tools/accept_frozen.py <exe>` 的结果：

| 检查 | 结果 |
| --- | --- |
| 包结构（`_internal` / `models` / `index.html` / `desktop`） | 全部就位 |
| `--version` / `--help` | 通过（冻结后控制台编码不炸） |
| `--doctor --json` | 21 项检查可读；提示项只剩"Demucs 未内置 / torch 视角看不到显卡 / 语音识别模型待下载" |
| `--uicheck`（**Qt 平台插件**） | 通过（打包版真的把窗口建出来，13 项全绿） |
| 真转换《在出发之前》 | 通过，产物 18.8 KB |
| 严格校验产物 | **0 个错误**（打包版自己 + 仓库校验器各一遍） |

⚠️ 下面这些**只在冻结后才出现**的问题都已修掉（详见 `AGENTS.md` §4.5）：

- `runpy.run_path` 在包内部找不到 `__main__` → `--uicheck` **卡死不动**
  （失败还让进程不退出，看起来就是"点了没反应"）；
- 桌面版自己的开关（`--uicheck` / `--selftest` / `--version`）被转发给
  `main.py` 的 argparse → `unrecognized arguments`；
- 包内自带的 CAM++ 权重在 `_internal/models/` 里，而模型管理器与 `doctor`
  只找 exe 旁边 → 误报"声纹模型缺失"；
- `collect_all('PySide6')` 把整个 Addons 拖进来（多约 90MB 用不到的
  QtQuick / 3D / WebView DLL）；
- `--web` 被桌面壳截胡（等于这个开关无效）。

---

## [未发布]

### 计划中

- 逐字时间的可视化拖动（当前只能输入时间）
- 从音频自动检测"这里少了一句歌词"并提示

---

## [1.0.0] - 2026-10-11

**首次公开发布。**

### 核心能力

- **逐字时间轴**：faster-whisper 识别 + 自研 DP 强制对齐，时间戳全部来自真实音频
- **LRC 锚点**：有 LRC 行级时间戳时按乐句切片识别，用歌词当提示词
- **双轨精修**：匹配率偏低的行单独重听那一段再对齐一次
- **背景人声**：`（和声）` → `<span ttm:role="x-bg">`，永不单独成行（单独成行在播放器里看不见）
- **演唱者分段**：CAM++ 声纹 + k-means，四个档位（关闭 / 强制 1 / 2 / 3 声部）
- **人声分离**：可选 Demucs，用于混音复杂或和声多的歌
- **句尾拖音补全**：能量 + **基频连续性**双判据
- **翻译 / 音译层**：Apple Music 的 `<iTunesMetadata><translations>` 样式
- **双输出格式**：AMLL 社区格式 / Apple 提交格式
- **编辑台**（Web UI）：时间轴微调、声部批量改、文本增删改、和声编辑、音频试听与实时高亮

### 质量保障

- `selftest.py`：**34 组**离线自检（不需要网络与大模型）
- `tools/verify_amll_parse.py`：用**真正的 AMLL 解析器**验产物能不能显示
- `tools/eval_diarization.py`：跨歌曲分声部准确率（真值：AMLL TTML DB 人工标注）**85.0%**
- `tools/eval_word_timing.py`：用 QQ `.qrc` / 网易 `.yrc` 逐字真值验证时间轴
- `tools/test_ui_runtime.js`：**真运行时** UI 测试（Node 最小 DOM 里跑页面真实脚本）
- `tools/audit_params.py` / `tools/test_param_effect.py`：参数联调（有没有传 + 生不生效）

### 公开发布前修掉的关键问题（择要）

按"症状"记录，方便遇到同类现象的人检索：

| 症状 | 根因 |
| --- | --- |
| 播放器里歌词**一片空白** | 用未知 role 的包装 span 包住主唱 span，AMLL 解析器不递归子节点，整行塌成一个词 |
| 和声**整行看不见** | 背景人声单独成了 `<p>`，而 x-bg 不计入行文本 → 空行 |
| 对唱**永远不分栏** | 全曲只有 `v1`；声纹分不开时没有任何提示 |
| 转换**直接崩** UnicodeDecodeError | 中文 Windows 的 `.lrc` 大量是 GBK |
| `--style apple` **一用就崩** | `pipeline._apply_style` 用了未导入的 `tokenize_chinese` |
| `main.py --help` **整个 CLI 挂掉** | argparse 的 help 是 `%` 格式化的，文本里的裸 `%` 会抛异常 |
| 编辑台**高亮完全不亮** | `indexWords()` 里清空了元素地图，把刚登记的元素丢了（静默失败） |
| 编辑台**切页后高亮消失** | DOM 重建后缓存未清，`tickHighlight` 认为"没变化"而跳过 |
| 长歌词把进度条**挤变形并上下抖动** | "当前演唱文字"在 flex-wrap 容器里，且 flex 项默认不可收缩 |
| `.lrc` 里 `[v1]` 单独一行**静默失效** | LRC 把 `[v1]` 当时间戳标签，该行被丢弃且不报错 |
| 末行拖音**被砍掉 6.9 秒** | 音高判据在 Live 录音上失效 + `max_extend` 上限 |

### 发布与打包（2026-10-11）

- **`tools/build_release.py`**：构建 Release 附件
  * **轻量版**（默认，约 240 MB / 压缩 94 MB）：ASR + 对齐 + 全部 TTML 功能 + 编辑台；
    `torch` 与 CUDA 运行时不打进去（**函数内 import，缺了不会崩**，只是分声部/人声分离不可用）
  * 完整版（`--full`，约 5 GB）：含 torch，功能全
  * 便携 ZIP（`--portable`）：源码 + 一键安装脚本，能跑全部功能
  * 自带**冒烟测试**：跑 `--help` 并且真的把 Web 服务起起来访问一次首页
- **`launcher.py`**：打包后的统一入口 —— 双击 = 起 Web 界面；带参数 = 走命令行。
  冻结后把 `HERE`/`uploads/`/`outputs/` 重定向到 **exe 所在目录**（包内部是临时/只读的）
- **`?task=<id>` 直接打开编辑台**（`index.html`）：
  服务重启后接着改上次那首、或把编辑台地址存成书签
- **`tools/shoot_screens.ps1` + `.js`**：用无头 Edge 给**真实运行的界面**截图（README 演示段）
- README 增加「界面演示」一节（5 张真实截图）

### 公开发布前修掉的问题（续）

| 症状 | 根因 |
| --- | --- |
| **打包后 `--help` 崩掉** | help 文本里的 `⚠️`(U+26A0) 不在 GBK 里，而冻结进程的 stdout 走控制台代码页 → `UnicodeEncodeError` |
| 重启后编辑台报"没有可编辑的内容" | `_get_edit` 只认 `<id>.edit.json`，不认旁边就有的 `<id>.ttml` |
| `?task=` 打开了编辑台却**完全不可见** | 编辑台在 `#resultSection` 里，那个容器要等转换完成才 `display:block` |
| 单行歌词标记**静默失效** | 纯文本里 `[v1]` 单独一行只会剥出空行、不作用于后续行；LRC 里整行被丢弃 |
| 编辑器保存**合并掉整首歌的唱段分段** | `_split_sections` 在 `song_part` 非空时直接短路成一个 `<div>` |
| 编辑器保存**丢掉词曲作者** | `ttml_reader` 从来没读 `<songwriters>` |

### 已知限制

- 分声部约 **90%**：音色接近的对唱会偶发分错；**人数只能由用户指定**
  （实测三种自动判据全部失败）
- 两人**齐唱/同时张嘴**时无法拆分
- 拖音长度**无法用外部数据验证**（QQ 的 `.qrc` 自己也截断拖音）
- Big5（繁体）歌词会被 gb18030 误解，仅标记为"可疑"
- **轻量版 exe 不含 torch**：分声部与人声分离不可用（要全功能请用 `--full` 或 pip 安装）

### 许可证

MIT。第三方组件见 [README「第三方依赖与许可」](README.md#第三方依赖与许可)。

---

## 公开发布前的开发阶段

以下阶段对应仓库里的 `docs/` 与 `AGENTS.md` 记录，按能力而不是按版本号排列。

### P4 参数联调 + 平台 ID + 编辑台体验（2026-10-11）

- 平台 ID 增加**酷我 / 酷狗**（自定义 `amll:meta` 键，AMLL 官方未定义）
- 多平台 ID 检索：**默认不自动回填**，提供一键跳转各平台搜索
- 语言增加**「自动检测」**（界面默认），中英混唱不再需要手动切
- 合并重复的脏话处理选项
- 「当前演唱文字」独立成行，修复播放条抖动
- **✏️ 文本编辑页**加入播放高亮；修复同一行主唱+和声并行时只亮一个

### P3 编辑台文本编辑 + 和声编辑（2026-10-10）

- 插行 / 删行 / 整行改文本、改字 / 插字 / 删字、和声增删改
- 三条不变量：层内不重叠、`span ⊆ p`、`itunes:key` 连续

### P2 编辑台音频试听

- `GET /api/audio` 支持 **HTTP Range**（拖进度条靠它）
- 点字跳转、rAF 实时高亮、循环当前行

### P1 编辑台（2026-10-09）

- 时间轴微调、声部修改（含批量区间）、乐观锁、原子落盘、结构化错误
- ⚠️ 保存必须"只改用户改的"：曾把 `ttm:role` 整个删掉，破坏 Apple 分栏

### P0 反解与时间轴自动修复

- `ttml_model` / `ttml_reader` / `ttml_normalize`
- 往返不变量、R1–R7 修复规则、生成链路与编辑链路共用同一套算法

### 拖音与起音对齐（2026-10-10）

- 拖音判据从"纯能量"升级为**基频连续性**，并区分"末行 vs 有下一行"
- 三个用户病例全部修好（详见 `AGENTS.md`）
