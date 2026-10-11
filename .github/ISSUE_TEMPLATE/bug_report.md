---
name: Bug 报告
about: 报告一个可复现的问题
title: '[Bug] '
labels: bug
assignees: ''
---

<!--
⚠️ 请务必填「复现步骤」和「具体位置」。
   实测经验：泛泛的描述（如"识别不准""有点漂移"）通常复现不出来；
   而「第几行、哪个字、预期 vs 实际」这种具体信息，基本都能定位并修好。

   提交前建议先跑一遍：
     python selftest.py
   如果自检就是红的，请先说明——那可能不是你的问题，而是仓库当前状态的问题。
-->

## 问题描述

<!-- 一两句话说明现象。 -->

## 复现步骤

1.
2.
3.

**你跑的命令**（请完整贴出）：

```bash

```

## 具体位置（重要）

| 项 | 内容 |
| --- | --- |
| 第几行 / 哪句歌词 | |
| 哪个字 | |
| 预期时间 | |
| 实际时间 | |
| 影响范围 | <!-- 全曲 / 只有这一处 / 某几行 --> |

<!--
时间信息怎么拿：
  python main.py --check-only 你的文件.ttml        # 会列出每行每字的时间
  python tools/inspect_ttml.py 你的文件.ttml       # 体检结构
  python tools/case_report.py 你的文件.ttml "行内关键词"   # 打印该行的能量/基频与各判据结论
-->

## 环境

| 项 | 内容 |
| --- | --- |
| 操作系统 | <!-- 例：Windows 11 23H2 --> |
| Python 版本 | <!-- python -V --> |
| GPU 型号 | <!-- 有的话；很多对齐问题与 compute_type 有关 --> |
| 相关参数 | <!-- 例：--separate-vocals --singer-mode 2 --style apple --> |
| 输入格式 | <!-- .txt / .lrc（编码？）/ 有无翻译文件 --> |

## 自检结果

```
python selftest.py          # 贴最后几行
python main.py --check-only <文件>.ttml --strict-time    # 贴「格式通过/未通过」那行
```

## 其它信息

<!-- 日志、截图、TTML 片段都可以。
     注意：请**不要**贴有版权的完整歌词；截取出问题的那一两行就够。 -->
