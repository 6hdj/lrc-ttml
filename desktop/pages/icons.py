"""统一图标：用 QPainter **画**单色线条图标（不加任何依赖）。

## 为什么自己画

Phase 1 审查发现侧边栏用 **emoji**（🏠🎵📋🧩⚙❓）——任务书明确要求
"图标统一使用同一套风格，不混用表情符号和多种图标库"。
而 Qt 本身不带图标字体，引 `QtAwesome` / `qt-material` 会加依赖、增大安装包。
所以用 QPainter 画一套 **24×24 的线条图标**：风格一致（2px 圆头线、
圆角）、颜色由调用方传（随主题变），不依赖任何字体字形。

⚠️ 图标是**程序运行时现画**的，不是 png 资源 —— 这样改色/改粗细
不用重新出图，也绝不会出现"字体没这个字形 → 空心方框"（emoji 就踩过这个坑）。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF)

_SIZE = 24
_STROKE = 1.8


def _canvas(color: QColor) -> tuple:
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, _STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    return p, pm


def _finish(p: QPainter, pm: QPixmap) -> QIcon:
    p.end()
    return QIcon(pm)


def home(color: str) -> QIcon:
    """首页：房子。"""
    p, pm = _canvas(QColor(color))
    # 屋顶
    p.drawPolyline(QPolygonF([QPointF(4, 12), QPointF(12, 5), QPointF(20, 12)]))
    # 墙体
    p.drawPolyline(QPolygonF([QPointF(6, 11), QPointF(6, 19), QPointF(18, 19),
                              QPointF(18, 11)]))
    # 门
    p.drawRect(QRectF(10, 13.5, 4, 5.5))
    return _finish(p, pm)


def workbench(color: str) -> QIcon:
    """歌词工作台：音符。"""
    p, pm = _canvas(QColor(color))
    p.drawEllipse(QRectF(9, 15, 4.5, 4.5))       # 符头
    p.drawLine(QPointF(13.5, 17), QPointF(13.5, 6))  # 符干
    p.drawLine(QPointF(13.5, 6), QPointF(17.5, 8))   # 符尾
    return _finish(p, pm)


def tasks(color: str) -> QIcon:
    """任务中心：清单。"""
    p, pm = _canvas(QColor(color))
    p.drawRoundedRect(QRectF(5, 5, 14, 14), 3, 3)
    p.drawLine(QPointF(8.5, 9.5), QPointF(15.5, 9.5))
    p.drawLine(QPointF(8.5, 12.5), QPointF(15.5, 12.5))
    p.drawLine(QPointF(8.5, 15.5), QPointF(13, 15.5))
    return _finish(p, pm)


def models(color: str) -> QIcon:
    """模型管理：拼图块（四拼）。"""
    p, pm = _canvas(QColor(color))
    p.drawRoundedRect(QRectF(4, 4, 7, 7), 2, 2)
    p.drawRoundedRect(QRectF(13, 4, 7, 7), 2, 2)
    p.drawRoundedRect(QRectF(4, 13, 7, 7), 2, 2)
    p.drawRoundedRect(QRectF(13, 13, 7, 7), 2, 2)
    return _finish(p, pm)


def settings(color: str) -> QIcon:
    """设置：齿轮。"""
    p, pm = _canvas(QColor(color))
    p.drawEllipse(QRectF(8.5, 8.5, 7, 7))         # 内圈
    p.drawEllipse(QRectF(6, 6, 12, 12))           # 外圈
    # 8 根齿
    import math
    for i in range(8):
        a = math.pi * i / 4
        x1, y1 = 12 + 8 * math.cos(a), 12 + 8 * math.sin(a)
        x2, y2 = 12 + 10.5 * math.cos(a), 12 + 10.5 * math.sin(a)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    return _finish(p, pm)


def help(color: str) -> QIcon:
    """帮助 / 诊断：问号。"""
    p, pm = _canvas(QColor(color))
    p.drawEllipse(QRectF(8, 4, 8, 8))             # 上弧
    p.drawLine(QPointF(12, 12), QPointF(12, 15))  # 竖
    p.drawPoint(QPointF(12, 18.5))                # 点
    return _finish(p, pm)


# 统一注册表：key → 画图函数（图标颜色由 `icon()` 传入）
_ICONS = {
    "welcome": home,
    "home": workbench,
    "tasks": tasks,
    "models": models,
    "settings": settings,
    "help": help,
}


def nav_icon(key: str, color: str) -> QIcon:
    """按页面 key 取侧边栏图标（统一风格、随主题色）。"""
    fn = _ICONS.get(key)
    if fn is None:
        return QIcon()
    return fn(color)
