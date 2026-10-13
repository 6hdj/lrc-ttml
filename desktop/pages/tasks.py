"""「任务」页：所有后台任务一张列表 + 选中任务的日志与操作。

为什么需要单独一页：模型下载要 3GB、转换要跑几分钟，用户会**切页去干别的**。
切回来必须能看到"刚才那个任务现在怎么样了、日志里最后一句是什么"。

⚠️ 三条：
1. **历史任务要能在重启后还看得见**（``TaskManager`` 落盘 + 重启把
   "进行中"收成"已中断"）。不然用户关掉软件再打开会以为任务凭空消失。
2. **日志是"选中哪个看哪个"**，不能所有任务日志堆在一起。
3. **取消按钮只在能取消时亮**；已经结束的任务给"打开产物/在文件夹里显示"。
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QPlainTextEdit,
                               QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from desktop.pages.base import (C, Page, STATUS_COLOR, STATUS_ICON, button,
                                col, dim, h2, hint, label, page_header, row)

STATE_LABEL = {
    "queued": "排队中", "running": "进行中", "verifying": "校验中",
    "done": "完成", "failed": "失败", "cancelled": "已取消",
}
KIND_LABEL = {
    "convert": "制作歌词", "model_download": "下载模型",
    "model_cleanup": "清理模型", "env_check": "环境体检",
    "check_ttml": "校验 TTML",
}


class TasksPage(Page):
    TITLE = "任务"
    SUBTITLE = "转换和模型下载都在这里；关了软件再打开，历史还在。"

    def build(self) -> QWidget:
        self._selected: str = ""

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["状态", "任务", "进度", "用时", "开始"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_select)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self.lb_title = h2("（左边选一个任务）")
        self.lb_meta = dim("")
        self.lb_error = label("", wrap=True, color=C["err"])
        self.btn_cancel = button("取消任务", on_click=self._cancel)
        self.btn_open = button("打开产物", on_click=self._open_output)
        self.btn_reveal = button("在文件夹里显示", on_click=self._reveal_output)
        self.btn_clear = button("清掉已完成", on_click=self._clear_done)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("mono")

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(8)
        rlay.addWidget(self.lb_title)
        rlay.addWidget(self.lb_meta)
        rlay.addWidget(self.lb_error)
        rlay.addWidget(row(self.btn_cancel, self.btn_open, self.btn_reveal, None,
                           stretch_index=None))
        rlay.addWidget(self.log, 1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.table)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)
        split.setSizes([420, 520])

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.setSpacing(12)
        lay.addWidget(page_header(self))
        lay.addWidget(hint("点一行看它的日志。「取消」只会停在**步骤之间**"
                           "（正在跑的识别那一步不会被打断），所以按下去"
                           "状态会先变成「取消中...」。"))
        lay.addWidget(split, 1)
        lay.addWidget(row(self.btn_clear, None, stretch_index=None))
        return inner

    # ------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        if not self._built:
            return
        tasks = self.ctx.tasks.list(with_logs=False)
        self.table.setRowCount(len(tasks))
        for r, t in enumerate(tasks):
            state = t.get("state", "")
            c0 = QTableWidgetItem(f"{STATUS_ICON.get(state, '•')} "
                                  f"{STATE_LABEL.get(state, state)}")
            c0.setForeground(_qcolor(STATUS_COLOR.get(state, C["dim"])))
            self.table.setItem(r, 0, c0)
            self.table.setItem(r, 1, QTableWidgetItem(
                f"{KIND_LABEL.get(t.get('kind'), t.get('kind'))} · "
                f"{t.get('title') or ''}"))
            pct = t.get("progress")
            self.table.setItem(r, 2, QTableWidgetItem(
                f"{int(pct)}%" if pct is not None else "-"))
            self.table.setItem(r, 3, QTableWidgetItem(f"{t.get('elapsed', 0):.0f}s"))
            self.table.setItem(r, 4, QTableWidgetItem(
                time.strftime("%H:%M:%S", time.localtime(t.get("created") or 0))))
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole, t.get("id"))
        # 保持选中的那一行
        if self._selected:
            self._reselect()
        elif tasks:
            self.table.selectRow(0)
        else:
            self._show_empty()

    def _reselect(self) -> None:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == self._selected:
                self.table.selectRow(r)
                return
        if self.table.rowCount():
            self.table.selectRow(0)

    def _show_empty(self) -> None:
        self.lb_title.setText("还没有任务")
        self.lb_meta.setText("到「制作」页点开始，或者到「模型」页下载模型。")
        self.lb_error.setText("")
        self.log.clear()
        self.btn_cancel.setEnabled(False)
        self.btn_open.setEnabled(False)
        self.btn_reveal.setEnabled(False)

    # ------------------------------------------------------------ 选择
    def _on_select(self) -> None:
        rows = self.table.selectionModel().selectedRows() \
            if self.table.selectionModel() else []
        if not rows:
            return
        it = self.table.item(rows[0].row(), 0)
        tid = it.data(Qt.ItemDataRole.UserRole) if it else ""
        if not tid or tid == self._selected:
            return
        self._selected = tid
        self._render_detail()

    def _render_detail(self) -> None:
        t = self.ctx.tasks.get(self._selected)
        if t is None:
            self._show_empty()
            return
        d = t.public()
        state = d.get("state", "")
        self.lb_title.setText(f"{STATUS_ICON.get(state, '•')} "
                              f"{KIND_LABEL.get(d.get('kind'), d.get('kind'))}"
                              f" · {d.get('title') or ''}")
        bits = [STATE_LABEL.get(state, state)]
        if d.get("progress") is not None and state not in ("done",):
            bits.append(f"{int(d['progress'])}%")
        bits.append(str(d.get("message") or ""))
        bits.append(f"用时 {d.get('elapsed', 0):.0f}s")
        self.lb_meta.setText("　|　".join(x for x in bits if x))
        self.lb_error.setText(str(d.get("error") or ""))
        self.log.setPlainText("\n".join(
            f"[{x['t']}] {x['m']}" for x in (d.get("logs") or [])))
        self.log.verticalScrollBar().setValue(
            self.log.verticalScrollBar().maximum())
        running = bool(d.get("running"))
        self.btn_cancel.setEnabled(running)
        out = str((d.get("result") or {}).get("output_path") or "")
        self.btn_open.setEnabled(bool(out))
        self.btn_reveal.setEnabled(bool(out))

    # ------------------------------------------------------------ 操作
    def _cancel(self) -> None:
        if self._selected:
            self.ctx.tasks.cancel(self._selected)
            self._render_detail()

    def _output_path(self) -> str:
        t = self.ctx.tasks.get(self._selected)
        return str(((t.result if t else {}) or {}).get("output_path") or "")

    def _open_output(self) -> None:
        p = self._output_path()
        if p:
            self.ctx.open_path(p)

    def _reveal_output(self) -> None:
        p = self._output_path()
        if p:
            self.ctx.reveal(p)

    def _clear_done(self) -> None:
        n = self.ctx.tasks.remove_finished()
        self._selected = ""
        self.refresh()
        if n:
            self.lb_meta.setText(f"已清掉 {n} 条已结束的任务记录")

    # ------------------------------------------------------------ 任务回调
    def on_task(self, t: Dict[str, Any]) -> None:
        if not self._built:
            return
        self.refresh()
        if t.get("id") == self._selected:
            self._render_detail()

    def on_show(self) -> None:
        self.refresh()


def _qcolor(hex_color: str):
    from PySide6.QtGui import QColor
    return QColor(hex_color)
