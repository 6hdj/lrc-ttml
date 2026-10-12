"""「开始」页：第一次打开时的向导。

新手最容易卡在这三件事上，所以第一屏就把它们说清楚：

1. **我电脑能不能用**（N 卡 / 驱动 / CUDA）—— 一句话给结论，不要术语。
2. **还差什么**（识别模型 3GB）—— 一个按钮，进度可见，可以取消。
3. **接下来怎么用**（放哪、点什么）。

⚠️ 不要在这里做"重活"：GPU 实测和模型校验都丢后台任务，
否则第一屏会白屏几秒（用户会以为软件坏了）。第一屏先给**轻量结论**，
重活显示"检查中…"再刷新。
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtWidgets import (QGridLayout, QVBoxLayout, QWidget)

from desktop import paths
from desktop.pages.base import (C, Page, STATUS_COLOR, STATUS_ICON, button,
                                card, col, dim, h1, h2, hint, label,
                                page_header, row)
from desktop.services import model_manager


class WelcomePage(Page):
    TITLE = "欢迎使用 Lyric2TTML"
    SUBTITLE = "普通歌词（TXT/LRC）+ 音频 → Apple Music / AMLL 逐字歌词。"

    def build(self) -> QWidget:
        self.lb_env = label("正在检查环境…", wrap=True)
        self.env_rows: List[Dict[str, Any]] = []
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(6)
        self.grid.setContentsMargins(0, 0, 0, 0)

        btn_env = button("重新检查环境", on_click=lambda: self._env(True))
        btn_dl = button("下载识别模型（必需）", primary=True,
                        on_click=self._download)
        self.lb_dl = label("", wrap=True)

        self.check_box = self.card_with(
            h2("① 这台电脑能不能用"),
            self.lb_env,
            # ⚠️ 按钮要包一行并加弹簧：直接 addWidget 的话它会**横向拉满**，
            # 看起来像一条坏掉的进度条（截图里一眼能看到）。
            row(btn_env, None, stretch_index=None),
            self.grid_widget,
        )

        self.model_box = self.card_with(
            h2("② 还差一个语音识别模型"),
            # ⚠️ QLabel 默认不是 Markdown：写成 **加粗** 会把星号原样显示出来
            # （截图里就是这样）。要强调就用「」或者分开成两句，别用 Markdown 记号。
            label("模型约 3GB，不在安装包里（GitHub 单个文件上限 2GB）。"
                  "点下面的按钮下载，支持断点续传，下完会核对文件完整性。"),
            hint("模型放在程序目录旁边（或数据目录），卸载软件时不会删掉，"
                 "重装不用重下。"),
            row(btn_dl, None, stretch_index=None),
            self.lb_dl,
        )

        self.how_box = self.card_with(
            h2("③ 怎么用"),
            label("1. 到「制作」页，选音频（wav/mp3/flac…）和歌词（TXT/LRC）。\n"
                  "2. 歌名和演唱者会自动从文件名猜出来，猜错了直接改。\n"
                  "3. 点「开始制作」。一首 5 分钟的歌大约 30 秒到 2 分钟。\n"
                  "4. 生成后点「打开 TTML」或「打开编辑台」精修。"),
            hint("对唱要左右分栏的话：把「演唱者分段」设成「强制双声部」，"
                 "或者在歌词里写 [v1]/[v2] 标记。"),
        )

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.setSpacing(14)
        lay.addWidget(page_header(self))
        for w in (self.check_box, self.model_box, self.how_box):
            lay.addWidget(w)
        lay.addStretch(1)

        self._light_check()
        return self.scrolled(inner)

    # ------------------------------------------------------------ 环境
    def _light_check(self) -> None:
        """第一屏：**只跑轻量检查**（不 import torch），立刻有结论。"""
        items = self.ctx.env_check(full=False)
        self._paint(items, "（显卡详情点右边的「重新检查环境」）")

    def _env(self, full: bool) -> None:
        self.lb_env.setText("检查中…（显卡那一步要几秒）")
        tid = self.ctx.workers.start_env_check(full=full)
        self._env_tid = tid

    def on_task(self, t: Dict[str, Any]) -> None:
        if not self._built or t.get("id") != getattr(self, "_env_tid", ""):
            return
        if t.get("state") in ("done", "failed"):
            env = (t.get("result") or {}).get("env") or {}
            self._paint(env.get("items") or [], "")

    def _paint(self, items: List[Dict[str, Any]], suffix: str = "") -> None:
        # 清旧行
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        # 只显示"和能不能开始做歌词有关"的 + 显卡
        show = [i for i in items
                if i["key"] in ("gpu", "cuda", "python", "disk", "workspace")
                or i["key"].startswith("model:")]
        for r, it in enumerate(show):
            color = STATUS_COLOR.get(it["level"], C["dim"])
            icon = STATUS_ICON.get(it["level"], "·")
            # ⚠️ 宽度要够：中文名字（"Whisper medium（语音识别·中）"）比英文长，
            # 固定宽度太小会被截成 "Whisper medium（语音识别·" 这种半截话。
            name = label(f"{icon} {it['name']}", color=color)
            name.setFixedWidth(250)
            detail = label(it["detail"] or "", wrap=True, kind="dim")
            self.grid.addWidget(name, r, 0)
            self.grid.addWidget(detail, r, 1)
            if it.get("fix"):
                self.grid.addWidget(label("→ " + it["fix"], wrap=True,
                                          color=C["warn"]), r, 2)

        errs = [i for i in items if i["level"] == "error"]
        gpu = next((i for i in items if i["key"] == "gpu"), None)
        if gpu and gpu["ok"]:
            self.lb_env.setText("✓ 显卡可用，能开始做歌词。" + suffix)
            self.lb_env.setStyleSheet(f"color: {C['ok']}; font-weight: 600;")
        elif errs:
            self.lb_env.setText("⚠ 还有问题要处理（下面标红的那几项）。" + suffix)
            self.lb_env.setStyleSheet(f"color: {C['err']}; font-weight: 600;")
        else:
            self.lb_env.setText("基本环境就绪。" + suffix)
            self.lb_env.setStyleSheet(f"color: {C['warn']}; font-weight: 600;")

        self._paint_model()

    def _paint_model(self) -> None:
        mid = self.ctx.default_asr_model()
        st = self.ctx.models.check(mid)
        if st.status == model_manager.READY:
            self.lb_dl.setText(f"✓ 「{st.name}」已经装好了（{st.detail}）。"
                               f"可以直接去「制作」页。")
            self.lb_dl.setStyleSheet(f"color: {C['ok']};")
            self.model_box.setVisible(True)
            self.lb_model_state = "ready"
        else:
            self.lb_dl.setText(f"当前：{st.detail}。需要约 "
                               f"{model_manager.human(st.approx_size)}。")
            self.lb_dl.setStyleSheet(f"color: {C['text']};")

    def _download(self) -> None:
        mid = self.ctx.default_asr_model()
        tid = self.ctx.workers.start_model_download(mid)
        self._dl_tid = tid
        self.lb_dl.setText("已开始下载，到「任务」页能看到进度和日志。")
        self.lb_dl.setStyleSheet(f"color: {C['accent']};")

    def refresh(self) -> None:
        if not self._built:
            return
        self._paint_model()

    def on_show(self) -> None:
        self.refresh()

    def on_task(self, t: Dict[str, Any]) -> None:
        """环境体检 / 自己发起的模型下载完成后刷新本页。"""
        if not self._built:
            return
        if t.get("id") == getattr(self, "_env_tid", ""):
            if t.get("state") in ("done", "failed"):
                env = (t.get("result") or {}).get("env") or {}
                if env.get("items"):
                    self._paint(env["items"], "")
        if t.get("id") == getattr(self, "_dl_tid", "") and \
                t.get("state") in ("done", "failed", "cancelled"):
            self.refresh()

    def _skip(self) -> None:
        """标记"向导看过了"，下次直接进「制作」页。

        ⚠️ 只是不再**自动**停留在这里 —— 左侧「开始」一直在，用户随时能回来。
        """
        self.ctx.config.set("first_run_done", True)
        try:
            self.ctx.config.save()
        except OSError:
            pass
        win = getattr(self, "_nav_target", None)
        if win:
            win.show_page("home")
