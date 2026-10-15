"""「开始」页：第一次打开时的向导。

新手最容易卡在这三件事上，所以第一屏就把它们说清楚：

1. **我电脑能不能用**（N 卡 / 驱动 / CUDA）—— 一句话给结论，不要术语。
2. **还差什么**（识别模型 3GB）—— 一个按钮，进度可见，可以取消。
3. **接下来怎么用**（放哪、点什么）。

⚠️ 不要在这里做"重活"：GPU 实测和模型校验都丢后台任务，
否则第一屏会白屏几秒（用户会以为软件坏了）。第一屏先给**轻量结论**，
重活显示"检查中..."再刷新。
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
    # ⚠️ 这一页的标题**故意不等于导航名**（导航是「开始」）：
    # 它是首次使用时的欢迎页，标题写成问候语更自然；导航名是"动作"。
    # 这是**唯一一个例外**，`tools/test_design_consistency.py` 里显式豁免，
    # 其它页面必须严格"标题 = 导航名"。
    TITLE = "欢迎使用 Lyric2TTML"
    SUBTITLE = "普通歌词（TXT/LRC）+ 音频 → Apple Music / AMLL 逐字歌词。"

    def build(self) -> QWidget:
        self.lb_env = label("正在检查环境...", wrap=True)
        self.env_rows: List[Dict[str, Any]] = []
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(6)
        self.grid.setContentsMargins(0, 0, 0, 0)

        btn_env = button("重新检查环境", on_click=lambda: self._env(True))
        self.btn_dl = button("下载识别模型（必需）", primary=True,
                             on_click=self._download)
        self.lb_dl = label("", wrap=True)
        # 说明文字要能跟着状态变（装好之后不该再说"点按钮下载"），所以留着引用
        self.lb_model_note = label("", wrap=True)

        self.check_box = self.card_with(
            h2("① 这台电脑能不能用"),
            self.lb_env,
            # ⚠️ 按钮要包一行并加弹簧：直接 addWidget 的话它会**横向拉满**，
            # 看起来像一条坏掉的进度条（截图里一眼能看到）。
            row(btn_env, None, stretch_index=None),
            self.grid_widget,
        )

        # ② 语音识别模型
        self.head_model = h2("② 还差一个语音识别模型")
        self.model_box = self.card_with(
            self.head_model,
            # ⚠️ QLabel 默认不是 Markdown：写成 **加粗** 会把星号原样显示出来
            # （截图里就是这样）。要强调就用「」或者分开成两句，别用 Markdown 记号。
            self.lb_model_note,
            hint("模型放在数据目录里，卸载软件时不会删掉，重装也不用重下。"),
            row(self.btn_dl, None, stretch_index=None),
            self.lb_dl,
        )

        # ---------------------------------------------------------------
        # ③ 运行组件（首次启动引导**必须**包含它）
        # ---------------------------------------------------------------
        # ⚠️⚠️ 为什么必须有这一块（第 25 轮补，是个真的"走进死路"问题）：
        # 主包自第 19 轮起**不含 CUDA 运行时**（方案 D：717.7 MB → 189.9 MB），
        # 而语音识别**没有它就跑不了**（`asr.py` 明确拒绝 CPU）。
        # 但原来的首次启动引导只有"① 环境 ② 模型 ③ 怎么用"——
        # **一个字都没提 GPU 运行时**。
        # 后果：用户老老实实按引导走完（环境✓、模型✓），
        # 点「开始制作」才发现还缺一个 529 MB 的东西，而且不知道该去哪装。
        # 实测（`.tmp/first_run_probe.py`，把 CUDA 打桩成未安装后）：
        #   按钮 = ['重新检查环境', '重新下载']，**完全没提 GPU 运行时**。
        self.lb_rt = label("", wrap=True)
        self.btn_rt = button("下载 GPU 运行时（必需）", primary=True,
                             on_click=self._download_runtime)
        self.lb_rt_note = label("", wrap=True)
        # ⚠️ 标题留引用：装好后要从"还差一个…"变成"已就绪"（与模型卡同理）
        self.head_rt = h2("③ 还差一个 GPU 运行时")
        self.rt_box = self.card_with(
            self.head_rt,
            self.lb_rt_note,
            row(self.btn_rt, None, stretch_index=None),
            self.lb_rt,
        )

        self.how_box = self.card_with(
            h2("④ 怎么用"),
            label("1. 到「制作」页，选音频（wav/mp3/flac...）和歌词（TXT/LRC）。\n"
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
        for w in (self.check_box, self.model_box, self.rt_box, self.how_box):
            lay.addWidget(w)
        lay.addStretch(1)

        self._light_check()
        # ⚠️⚠️ **必须在这里把两张卡都刷一遍**（真踩到，第 25 轮）：
        # `build()` 原来只调 `_light_check()`，而 `refresh()` 有
        # `if not self._built: return` 的保护 —— `_built` 是**外层**在 `build()`
        # 返回之后才置位的，所以构建期的任何 `refresh()` 都被**静默跳过**。
        # 后果：刚进「开始」页时，运行组件那张卡是**空的**
        # （`lb_rt` / `lb_rt_note` 都是空串，`rt_box` 甚至 `isVisible()=False`），
        # 标题和按钮在、中间一行字没有 —— 截图里就是一个明显的空缺。
        # 而"模型"卡看着正常，只是因为模型状态在更早的 `_light_check()` 里刷到过。
        # 现在直接调 `_paint_*`（不经过 `refresh()` 的守卫），保证第一眼就是真实状态。
        self._paint_model()
        self._paint_runtime()
        return self.scrolled(inner)

    # ------------------------------------------------------------ 环境
    def _light_check(self) -> None:
        """第一屏：**只跑轻量检查**（不 import torch），立刻有结论。"""
        items = self.ctx.env_check(full=False)
        self._paint(items, "（显卡详情点右边的「重新检查环境」）")

    def _env(self, full: bool) -> None:
        self.lb_env.setText("检查中...（显卡那一步要几秒）")
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
            icon = STATUS_ICON.get(it["level"], "•")
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
            self.lb_env.setText("√ 显卡可用，能开始做歌词。" + suffix)
            self.lb_env.setStyleSheet(f"color: {C['ok']}; font-weight: 600;")
        elif errs:
            self.lb_env.setText("! 还有问题要处理（下面标红的那几项）。" + suffix)
            self.lb_env.setStyleSheet(f"color: {C['err']}; font-weight: 600;")
        else:
            self.lb_env.setText("基本环境就绪。" + suffix)
            self.lb_env.setStyleSheet(f"color: {C['warn']}; font-weight: 600;")

        self._paint_model()

    def _paint_model(self) -> None:
        """② 卡片：**标题、按钮、说明**都要跟着状态走。

        ⚠️⚠️ 这里踩过一个很显眼的 bug（截图里一眼看到）：`h2("② 还差一个语音
        识别模型")` 与按钮文字 `"下载识别模型（必需）"` 都是**静态字面量**，
        而 `_paint_model()` 只改下面那行状态文字。于是模型**已经装好**时，
        界面同时显示：

            ② 还差一个语音识别模型          ← 标题在骗人
            [下载识别模型（必需）]           ← 让人以为必须再下
            ✓ 「Whisper large-v3」已经装好了 ← 又说装好了

        三条自相矛盾。用户按标题和按钮去做，只会白点一次下载。
        现在：标题、按钮、说明**都按状态刷新**；装好后按钮变「重新下载」
        （不再是"必需"），并把"不需要再下"说清楚。
        """
        mid = self.ctx.default_asr_model()
        st = self.ctx.models.check(mid)
        ready = st.status == model_manager.READY
        if ready:
            self.head_model.setText("② 语音识别模型已就绪")
            self.btn_dl.setText("重新下载")
            self.lb_dl.setText(f"√ 「{st.name}」已经装好了（{st.detail}）。"
                               f"可以直接去「制作」页；这一项不用再管。")
            self.lb_dl.setStyleSheet(f"color: {C['ok']};")
            self.model_box.setVisible(True)
            self.lb_model_state = "ready"
            self._set_model_note(True)
        else:
            self.head_model.setText("② 还差一个语音识别模型")
            self.btn_dl.setText("下载识别模型（必需）")
            self.lb_dl.setText(f"当前：{st.detail}。需要约 "
                               f"{model_manager.human(st.approx_size)}。")
            self.lb_dl.setStyleSheet(f"color: {C['text']};")
            self._set_model_note(False)

    def _set_model_note(self, ready: bool) -> None:
        """说明文字也分两种：装好后不该再说"点按钮下载"。

        ⚠️ 注意**别和下面的 `hint(...)` 重复**：第一版把"模型放在数据目录里..."
        同时写进了这里和 hint，截图上同一个句子里出现了两遍（很显眼）。
        这里只管"要不要下载"，"放哪儿"交给 hint。
        """
        if ready:
            self.lb_model_note.setText(
                "不用再做任何操作；换模型或想重装一遍时，才需要点下面的按钮。")
        else:
            self.lb_model_note.setText(
                "模型约 3GB，不在安装包里（GitHub 单个文件上限 2GB）。"
                "点下面的按钮下载，支持断点续传，下完会核对文件完整性。")

    def _download(self) -> None:
        mid = self.ctx.default_asr_model()
        tid = self.ctx.workers.start_model_download(mid)
        self._dl_tid = tid
        self.lb_dl.setText("已开始下载，到「任务」页能看到进度和日志。")
        self.lb_dl.setStyleSheet(f"color: {C['accent']};")

    # ------------------------------------------------------- ③ 运行组件
    def _paint_runtime(self) -> None:
        """运行组件卡的标题/按钮/说明都按状态走（与模型卡同一套规矩）。

        ⚠️ 与模型卡一样的坑：**标题和按钮不能写成静态字面量** ——
        装好之后还写"还差一个 GPU 运行时 / 下载（必需）"就是自相矛盾。
        """
        try:
            from desktop.services import runtime_service
            st = runtime_service.RuntimeManager().check("cuda")
        except Exception as e:                          # noqa: BLE001
            self.lb_rt.setText(f"读取运行组件状态失败：{type(e).__name__}: {e}")
            self.rt_box.setVisible(True)
            return
        ready = st.status == "ready"
        if ready:
            self.head_rt.setText("③ GPU 运行时已就绪")
            self.btn_rt.setText("重新下载")
            self.lb_rt.setText(f"√ 已经装好了（{st.detail}）。这一项不用再管。")
            self.lb_rt.setStyleSheet(f"color: {C['ok']};")
            self.lb_rt_note.setText("语音识别可以直接跑。")
        else:
            self.head_rt.setText("③ 还差一个 GPU 运行时")
            self.btn_rt.setText("下载 GPU 运行时（必需）")
            self.lb_rt.setText(f"当前：{st.detail}。需要约 "
                               f"{st.download_bytes / 1048576:.0f} MB。")
            self.lb_rt.setStyleSheet(f"color: {C['text']};")
            self.lb_rt_note.setText(
                "语音识别必须用显卡跑（CPU 跑一首歌要几分钟，所以没有做降级），"
                "而显卡加速要的 CUDA 运行库不在安装包里。")

    def _download_runtime(self) -> None:
        """下载 GPU 运行时（走任务中心，可取消、可续传）。"""
        try:
            tid = self.ctx.workers.start_runtime_download("cuda")
        except Exception as e:                          # noqa: BLE001
            self.lb_rt.setText(f"启动下载失败：{type(e).__name__}: {e}")
            self.lb_rt.setStyleSheet(f"color: {C['err']};")
            return
        self._rt_tid = tid
        self.lb_rt.setText("已开始下载（约 529 MB），到「任务」页能看到进度和日志。")
        self.lb_rt.setStyleSheet(f"color: {C['accent']};")

    def refresh(self) -> None:
        if not self._built:
            return
        self._paint_model()
        self._paint_runtime()

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
