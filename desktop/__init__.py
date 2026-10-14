"""Lyric2TTML 桌面应用（PySide6）。

分层（**依赖方向只能从上往下**，反向依赖一律禁止）：

    desktop/pages/        界面页面（Qt 控件）
    desktop/main_window.py 主窗口 + 导航 + 信号接线
    desktop/workers/      后台 worker（把服务接到任务中心上）
    desktop/services/     业务服务：任务中心 / 模型管理 / 后端调用 / 环境 / 日志
    desktop/paths.py      路径解析（程序目录 vs 数据目录）
    desktop/config.py     用户配置读写

⚠️ **``desktop/services`` 与 ``desktop/paths``/``config`` 不导入 Qt**，
可以在无图形环境（CI、远程 shell）里直接跑测试。要保证这一条，
否则"桌面功能"就永远只能靠人工点。

入口：
    python -m desktop              → 开桌面窗口
    python desktop/selftest.py     → 离线自检（不开窗口）
"""
from __future__ import annotations

__version__ = "1.1.1"
APP_NAME = "Lyric2TTML"
ORG_NAME = "Lyric2TTML"
