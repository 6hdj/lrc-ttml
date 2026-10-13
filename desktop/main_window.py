"""主窗口：左侧导航 + 页面容器 + 底部状态条。

三条约定（都是踩过坑才有的）：

1. **页面懒加载**：只构建第一次切过去的那一页。五页全在启动时构建的话，
   模型页要为每个模型做一次目录扫描，第一屏就慢半秒。
2. **任务通知广播给所有"已构建"的页面**，并且**再刷一次当前页**。
   只通知当前页的话，切回去的那一页状态是旧的（Web 版踩过：
   "任务跑完了但界面还是上一首"）。
3. **关窗口要问一句**：正在转换时直接关掉，用户会以为产物丢了。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QStatusBar,
                               QVBoxLayout, QWidget)

from desktop import __version__
from desktop.context import AppContext
from desktop.pages.base import (C, QSS, STATUS_COLOR, STATUS_ICON, dim, label)

# 导航顺序：开始 → 制作 → 任务 → 模型 → 设置
NAV = [
    ("welcome", "🏠  开始", "第一次用先看这里：环境、模型、怎么用"),
    ("home", "🎵  制作", "选文件、开始转换、看结果"),
    ("tasks", "📋  任务", "转换与下载的进度、日志"),
    ("models", "🧩  模型", "下载 / 校验 / 清理模型"),
    ("settings", "⚙  设置", "目录、默认值、诊断"),
]


class MainWindow(QMainWindow):
    def __init__(self, ctx: Optional[AppContext] = None):
        super().__init__()
        self.ctx = ctx or AppContext()
        self.setWindowTitle(f"Lyric2TTML - 歌词转逐字 TTML  v{__version__}")
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)

        self._pages: Dict[str, QWidget] = {}
        self._nav_buttons: Dict[str, QPushButton] = {}

        # ---------------- 左侧 ----------------
        side = QWidget()
        side.setFixedWidth(184)
        slay = QVBoxLayout(side)
        slay.setContentsMargins(10, 14, 10, 12)
        slay.setSpacing(4)

        title = QLabel("Lyric2TTML")
        title.setStyleSheet("font-size: 15px; font-weight: 700; padding: 2px 6px 10px 6px;")
        slay.addWidget(title)

        for key, text, tip in NAV:
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            slay.addWidget(b)
            self._nav_buttons[key] = b

        slay.addStretch(1)

        self.lb_gpu = QLabel("检查中...")
        self.lb_gpu.setWordWrap(True)
        self.lb_gpu.setStyleSheet(f"color: {C['dim']}; font-size: 11px; padding: 4px 6px;")
        slay.addWidget(self.lb_gpu)
        self.btn_env = QPushButton("环境体检")
        self.btn_env.setObjectName("nav")
        self.btn_env.clicked.connect(lambda: self.show_page("settings"))
        slay.addWidget(self.btn_env)

        # ---------------- 右侧 ----------------
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(side)
        body.addWidget(self.stack, 1)
        holder = QWidget()
        holder.setLayout(body)
        self.setCentralWidget(holder)

        # ---------------- 状态条 ----------------
        sb = QStatusBar()
        self.lb_status = QLabel("就绪")
        self.lb_status.setStyleSheet(f"color: {C['dim']};")
        sb.addWidget(self.lb_status, 1)
        self.lb_counts = QLabel("")
        self.lb_counts.setStyleSheet(f"color: {C['faint']};")
        sb.addPermanentWidget(self.lb_counts)
        self.setStatusBar(sb)

        # ---------------- 接线 ----------------
        # ⚠️ **不要**在这里 ctx.subscribe(界面的处理函数)：
        # TaskManager 的回调是在 worker 线程里调用的，跨线程改 Qt 控件
        # 会让 Qt 直接崩（实测 0xC0000005 退出，报
        # "Cannot create children for a parent that is in a different thread"）。
        # 界面统一走主线程定时器 + drain_changes()。
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.show_page("welcome" if not self.ctx.config.get("first_run_done")
                       else "home")
        QTimer.singleShot(120, lambda: self._bg_env_check())

    # ------------------------------------------------------------ 导航
    def show_page(self, key: str, force: bool = False) -> QWidget:
        page = self._page(key)
        if self.stack.indexOf(page) < 0:
            self.stack.addWidget(page)
        if self.stack.currentWidget() is not page or force:
            self.stack.setCurrentWidget(page)
        for k, b in self._nav_buttons.items():
            b.setChecked(k == key)
        self._current_key = key
        self.lb_status.setText(getattr(page, "TITLE", ""))
        on_show = getattr(page, "on_show", None)
        if callable(on_show):
            try:
                on_show()
            except Exception:                            # noqa: BLE001
                from desktop.services import log_service
                log_service.get().exception("页面 %s 的 on_show 出错", key)
        return page

    def _page(self, key: str) -> QWidget:
        if key in self._pages:
            return self._pages[key]
        page = _make_page(key, self.ctx)
        self._pages[key] = page
        # 让"开始"页的按钮能跳页
        page._nav_target = self                          # noqa: SLF001
        return page

    def current_page(self) -> Optional[QWidget]:
        return self.stack.currentWidget()

    # ------------------------------------------------------------ 任务通知
    def _tick(self) -> None:
        """**主线程**上的统一刷新点。

        做两件事：
        1. 把"有变化的任务"推给所有已构建的页面（``on_task``）；
        2. 顺带刷一次当前页（防止某个页面自己漏了状态）。

        为什么是轮询而不是信号：任务在 worker 线程里跑，直接回调到 Qt
        控件上会崩（跨线程）。轮询 400ms 的代价可以忽略，换来的是
        "不可能崩、也不可能漏"。
        """
        try:
            changes = self.ctx.drain_changes()
        except Exception:                                # noqa: BLE001
            changes = []
        for t in changes:
            self._dispatch(t)
        self._update_counts()
        if changes:
            self.lb_status.setText(
                str(changes[-1].get("message") or "")
                + (f"　{int(changes[-1]['progress'])}%"
                   if changes[-1].get("progress") is not None else ""))
        # 当前页低频兜底刷新
        page = self.current_page()
        fn = getattr(page, "refresh", None)
        if callable(fn):
            try:
                fn()
            except Exception:                            # noqa: BLE001
                pass

    def _dispatch(self, t: dict) -> None:
        """把一条任务变更交给所有已构建的页面（各自只处理自己关心的）。"""
        for key, page in list(self._pages.items()):
            fn = getattr(page, "on_task", None)
            if callable(fn):
                try:
                    fn(t)
                except Exception:                        # noqa: BLE001
                    from desktop.services import log_service
                    log_service.get().exception("页面 %s 处理任务通知出错", key)
        # 启动时那次"含显卡"的体检：结果贴到左下角
        if (t.get("id") == getattr(self, "_env_tid", None)
                and t.get("state") in ("done", "failed")):
            env = (t.get("result") or {}).get("env") or {}
            if env.get("items"):
                self.on_env_done(env["items"])

    def _update_counts(self) -> None:
        n = len(self.ctx.tasks.list(with_logs=False))
        running = self.ctx.running_count()
        self.lb_counts.setText(
            f"{n} 个任务" + (f"（{running} 个进行中）" if running else ""))

    # ------------------------------------------------------------ 环境
    def _bg_env_check(self) -> None:
        """启动后**在后台**跑一次含显卡的体检，结果只用于左下角那行小字。"""
        tid = self.ctx.workers.start_env_check(full=True)
        self._env_tid = tid

    def on_env_done(self, items: List[dict]) -> None:
        gpu = next((i for i in items if i.get("key") == "gpu"), None)
        if not gpu:
            self.lb_gpu.setText("")
            return
        if gpu["ok"]:
            self.lb_gpu.setText("√ " + str(gpu["detail"])[:60])
            self.lb_gpu.setStyleSheet(
                f"color: {C['ok']}; font-size: 11px; padding: 4px 6px;")
        else:
            self.lb_gpu.setText("! " + str(gpu["detail"])[:70])
            self.lb_gpu.setStyleSheet(
                f"color: {C['err']}; font-size: 11px; padding: 4px 6px;")

    # ------------------------------------------------------------ 关闭
    def closeEvent(self, event: QCloseEvent) -> None:   # noqa: N802
        if self.ctx.tasks.has_running():
            ans = QMessageBox.question(
                self, "还有任务在跑",
                "现在还有任务没结束（转换或下载）。\n\n"
                # ⚠️ 别在这里写 Markdown：QMessageBox 是**纯文本**，
                # 写成 `**中断**` 用户看到的就是字面量 `**中断**`（真踩过）。
                # 要强调就用「」，和界面其它地方保持一致。
                "关掉软件会「中断」它们：\n"
                "• 转换中断 → 这次不会生成文件，上一次做好的文件不受影响；\n"
                "• 下载中断 → 已经下好的部分留着，下次接着下。\n\n确定要关吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        try:
            self.ctx.shutdown()
        except Exception:                                # noqa: BLE001
            pass
        event.accept()


def _make_page(key: str, ctx: AppContext):
    if key == "welcome":
        from desktop.pages.welcome import WelcomePage
        return WelcomePage(ctx)
    if key == "home":
        from desktop.pages.home import HomePage
        return HomePage(ctx)
    if key == "tasks":
        from desktop.pages.tasks import TasksPage
        return TasksPage(ctx)
    if key == "models":
        from desktop.pages.models import ModelsPage
        return ModelsPage(ctx)
    if key == "settings":
        from desktop.pages.settings import SettingsPage
        return SettingsPage(ctx)
    raise KeyError(key)
