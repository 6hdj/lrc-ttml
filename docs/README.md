# 文档索引

本目录放**规范、设计、评测、版本说明**；项目主介绍见根目录 [`README.md`](../README.md)，
第三方许可证与模型许可见 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。

## 对齐 / 算法

| 文档 | 内容 |
|---|---|
| [实验-TIFA对齐引擎.md](实验-TIFA对齐引擎.md) | TIFA 接入、逐行语言路由、和声混合、长间奏裁剪、QRC 评测、自动和声 |
| [调研-Timing对齐引擎.md](调研-Timing对齐引擎.md) | Timing 项目的对齐核心/引擎调研 |
| [LyricAlignment评估.md](LyricAlignment评估.md) | 逐字对齐评估数据与方法 |
| [声纹评估协议.md](声纹评估协议.md) | CAM++ 分声部评估协议与已知结论 |

## TTML 规范 / 生成

| 文档 | 内容 |
|---|---|
| [TTML1-AMLL歌词格式规范.md](TTML1-AMLL歌词格式规范.md) | TTML1 + AMLL 规范逐条对照、常见错误 |
| [AppleMusic-TTML-样例规范.md](AppleMusic-TTML-样例规范.md) | Apple Music 真实样例结构、MM:SS/无 xml:lang 的宽松写法、和声嵌套 |
| [模块说明.md](模块说明.md) | 各 Python 模块职责与数据流 |

## 桌面 / 版本

| 文档 | 内容 |
|---|---|
| [WinUI3-版本说明.md](WinUI3-版本说明.md) | WinUI 3 架构、构建、便携包、管理员权限 |
| [release-notes-v1.2.1.md](release-notes-v1.2.1.md) | v1.2.1 发布说明 |
| [release-notes-v1.2.0.md](release-notes-v1.2.0.md) | v1.2.0 发布说明 |

## 项目历史 / 审计

| 文档 | 内容 |
|---|---|
| [项目总结.md](项目总结.md) | 项目全貌与阶段性结论 |
| [交付报告.md](交付报告.md) | 交付范围与验证记录 |
| [竞品调研与优化方案.md](竞品调研与优化方案.md) | 同类工具调研与改进方案 |
| [商业化重构审计与体积报告.md](商业化重构审计与体积报告.md) | 体积/依赖/商业化审计 |
| [UI重构报告.md](UI重构报告.md) | WinUI/Qt 界面重构记录 |

## 演示与素材

- `winui/`：WinUI 3 真实运行截图
- `desktop/`、`screenshots/`、`ui-shots/`：旧版 Qt / Web 界面截图
- `installer/`：旧版安装器截图

> 截图中的歌名/歌手是示例数据；音频、歌词、模型权重、用户产物均不在仓库中。
> 演示视频的旁白/配乐为自制，详见 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
