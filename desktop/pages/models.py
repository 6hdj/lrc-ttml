"""「模型」页：每个模型一行，状态、体积、下载、校验、清理全在这里。

这一页解决的是**用户最容易被劝退的一步**：
"软件装好了，一点转换说少个 3GB 的模型，然后让我去命令行敲 pip / 下 HF"。

设计上的四条：

1. **状态来自 ``ModelManager.check()``，不自己猜**。
   "已就绪"必须是"文件齐全 + 字节数对 + SHA256 对"，不是"文件存在"。
2. **进度未知就显示"未知"**（``QProgressBar`` 用 indeterminate 模式），
   **绝不编一个百分比** —— 编出来的进度条比没有进度条更伤信任。
3. **下载走任务中心**：可以取消、可以看日志、断了可以重来（支持断点续传）。
4. **默认只下"基础必需"的那一个**（ASR），别的按需 —— 这是需求里明说的：
   "首次启动只下载基础歌词制作必需的模型"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QVBoxLayout, QWidget)

from desktop.pages.base import (C, Page, STATUS_COLOR, button, card, col, dim,
                                h2, hint, label, page_header, row,
                                status_dot)
from desktop.services import model_manager

# 类别 → 展示名（顺序即展示顺序）
CATEGORY_ORDER = [
    ("asr", "语音识别（必需）"),
    ("diarization", "分声部 / 声纹"),
    ("separation", "人声分离（可选）"),
]


class ModelsPage(Page):
    # 标题 = 导航项名字（见 home.py 的同款说明）
    TITLE = "模型管理"
    SUBTITLE = "安装包不含模型权重：这里按需下载，下完会校验，之后完全离线。"

    def build(self) -> QWidget:
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._watch_ids: Dict[str, List[str]] = {}

        self.lb_head = h2("正在读取模型状态...")
        self.lb_cache = hint("")
        btn_recheck = button("重新检查", on_click=self._recheck)
        btn_dl_base = button("下载基础必需模型", primary=True,
                             on_click=self._download_base,
                             tip="只下语音识别模型（第一次做歌词必需）")
        btn_dl_all = button("下载全部推荐", on_click=self._download_recommended)
        btn_open_dir = button("打开模型目录", on_click=self._open_dir)

        head = self.card_with(
            self.lb_head,
            self.lb_cache,
            row(btn_dl_base, btn_dl_all, btn_recheck, btn_open_dir, None,
                stretch_index=None),
            hint("下载支持断点续传；校验用 SHA256 / 字节数，"
                 "临时文件（.part）永远不会被当成已安装。"),
        )

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(10)

        # ---------------------------------------------------------------
        # 运行组件（按需下载）：GPU(CUDA) / PyTorch
        # ---------------------------------------------------------------
        # ⚠️⚠️ 为什么必须有这块（否则方案 D 是**死路**）：
        # 第 18 轮起 CUDA 运行时从主包移出（主包 717.7 MB → 189.9 MB），
        # 但**没有任何界面能把它装回来** —— `runtime_service` 之前只有 CLI
        # 消费者（launcher / app.py），页面上一个字都没有。
        # 那样用户会遇到：装完软件 → 做歌词 → `asr.py` 报
        # 「CUDA 运行库未就位」→ **无处可点**。等于把一个必需组件做成了死路。
        self._rt_rows: Dict[str, Dict[str, Any]] = {}
        self.lb_rt = hint("正在读取运行组件状态…")
        self._rt_body = QWidget()
        self._rt_lay = QVBoxLayout(self._rt_body)
        self._rt_lay.setContentsMargins(0, 0, 0, 0)
        self._rt_lay.setSpacing(8)
        rt_card = self.card_with(
            h2("运行组件（按需下载）"),
            self.lb_rt,
            self._rt_body,
            hint("这些是「程序本身的功能件」，不是模型权重。GPU 运行时是"
                 "「语音识别」跑起来的必需品；PyTorch 只有「对唱分声部」"
                 "和「人声分离」才需要。"),
        )

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.setSpacing(14)
        lay.addWidget(page_header(self))
        lay.addWidget(head)
        lay.addWidget(rt_card)
        lay.addWidget(self.body)
        lay.addStretch(1)

        self._build_rows()
        self._build_rt_rows()
        # 第一眼就要是真实状态，不能停在 build() 里写的那句占位文字
        try:
            self.refresh()
        except Exception:                                # noqa: BLE001
            from desktop.services import log_service
            log_service.get().exception("模型页首次刷新失败")
        return self.scrolled(inner)

    # ------------------------------------------------- 运行组件（按需下载）
    def _build_rt_rows(self) -> None:
        """把 `runtime_manifest.json` 里的组件画成一行行。

        ⚠️ 清单是**唯一来源**：以后加运行时组件只要改清单，这里不用动。
        """
        while self._rt_lay.count():
            item = self._rt_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._rt_rows.clear()
        try:
            from desktop.services import runtime_service
            specs = runtime_service.load_manifest().get("runtimes", [])
        except Exception as e:                            # noqa: BLE001
            self.lb_rt.setText(f"读取运行组件清单失败：{type(e).__name__}: {e}")
            return
        for spec in specs:
            self._rt_lay.addWidget(self._make_rt_row(spec))

    def _make_rt_row(self, spec: dict) -> QWidget:
        rid = spec.get("id", "")
        name = spec.get("name", rid)
        size = int(spec.get("approx_size") or 0)
        lb_state = QLabel("…")
        lb_state.setObjectName("dim")
        lb_detail = QLabel(f"约 {size / 1048576:.0f} MB")
        lb_detail.setObjectName("faint")

        btn = button("下载并安装", on_click=lambda _=False, i=rid: self._download_rt(i))
        btn_remove = button("删除", on_click=lambda _=False, i=rid: self._remove_rt(i))
        box = QWidget()
        row_lay = QVBoxLayout(box)
        row_lay.setContentsMargins(0, 2, 0, 2)
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(lb_state)
        tl.addWidget(lb_detail)
        tl.addStretch(1)
        tl.addWidget(btn)
        tl.addWidget(btn_remove)
        row_lay.addWidget(top)
        row_lay.addWidget(hint(spec.get("purpose", "")))
        self._rt_rows[rid] = {"state": lb_state, "detail": lb_detail,
                              "btn": btn, "remove": btn_remove, "name": name}
        return box

    def _refresh_rt(self) -> None:
        try:
            from desktop.services import runtime_service
            mgr = runtime_service.RuntimeManager()
            states = mgr.check_all()
        except Exception as e:                            # noqa: BLE001
            self.lb_rt.setText(f"读取运行组件状态失败：{type(e).__name__}: {e}")
            return
        ready = 0
        for st in states:
            r = self._rt_rows.get(st.id)
            if not r:
                continue
            # 状态色统一走 STATUS_COLOR（深浅主题都能看）
            from desktop.pages.base import STATUS_COLOR
            color = STATUS_COLOR.get(st.status, C["dim"])
            label = {"ready": "已安装", "not_installed": "未安装",
                     "failed": "损坏"}.get(st.status, st.status)
            r["state"].setText(f"{st.name}：{label}")
            r["state"].setStyleSheet(f"color: {color}; font-weight: 600;")
            # ⚠️ 字段来自 `RuntimeState`：是 `download_bytes` / `installed_bytes`，
            # **没有** `approx_size`（那是 model spec 的字段名）。
            # 我第一版写成了 `st.approx_size` —— 属性不存在会在刷新里抛异常，
            # 表现为"卡片一直停在「正在读取运行组件状态…」"（真踩到）。
            _dl = int(getattr(st, "download_bytes", 0) or 0)
            _in = int(getattr(st, "installed_bytes", 0) or 0)
            size_txt = (f"需下载 {_dl / 1048576:.0f} MB"
                        + (f"　装后约 {_in / 1048576:.0f} MB" if _in else ""))
            # ⚠️ `check()` 生成的 detail 本身就是"需要下载 xxx"，
            # 再拼上去会变成"需下载 118 MB … 需要下载 118.4MB"（截图里抓到过，
            # 同一句话说了两遍）。只在 detail **不是**这类重复时才附上。
            _d = (st.detail or "").strip()
            _dup = _d.startswith("需要下载") or _d.startswith("已下载")
            r["detail"].setText(size_txt + (f"　{_d}" if _d and not _dup else ""))
            r["btn"].setEnabled(st.status != "ready")
            r["btn"].setVisible(st.status != "ready")
            r["remove"].setVisible(st.status == "ready")
            if st.status == "ready":
                ready += 1
        n = len(states)
        self.lb_rt.setText(f"{ready}/{n} 个运行组件已就绪"
                          if n else "清单里没有运行组件")

    def _build_rows(self) -> None:
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._rows.clear()

        specs_by_cat: Dict[str, List[dict]] = {}
        for s in self.ctx.models.all_specs():
            specs_by_cat.setdefault(s.get("category", "other"), []).append(s)

        for cat, title in CATEGORY_ORDER:
            specs = specs_by_cat.get(cat) or []
            if not specs:
                continue
            self.body_lay.addWidget(h2(title))
            for s in specs:
                self.body_lay.addWidget(self._make_row(s))
        # 其它类别（清单以后加了新类别也不能"看不见"）
        for cat, specs in specs_by_cat.items():
            if cat in dict(CATEGORY_ORDER):
                continue
            self.body_lay.addWidget(h2(cat))
            for s in specs:
                self.body_lay.addWidget(self._make_row(s))

    def _make_row(self, spec: dict) -> QWidget:
        mid = spec["id"]
        st = self.ctx.models.check(mid)

        name = label(st.name or mid)
        badge = label("")
        detail = label(st.detail, kind="dim", wrap=True)
        purpose = hint(st.purpose)
        size = hint(f"约 {model_manager.human(st.approx_size)}　|　"
                    f"许可：{spec.get('license') or '未标注'}")
        note = hint(spec.get("note") or "")
        note.setVisible(bool(spec.get("note")))

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setVisible(False)
        msg = hint("")
        msg.setVisible(False)

        btn_dl = button("下载", primary=(st.status != model_manager.READY),
                        on_click=lambda _=False, m=mid: self._download(m))
        btn_clean = button("清理", on_click=lambda _=False, m=mid: self._cleanup(m))
        btn_dl.setEnabled(st.status != model_manager.READY
                          and st.policy != "bundled")
        btn_clean.setEnabled(st.status == model_manager.READY
                             and st.policy != "bundled")

        left = col(name, purpose, detail, size, note, msg, spacing=4)
        f = card(flat=True)
        lay = QVBoxLayout(f)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(row(badge, left, None))
        lay.addWidget(bar)
        lay.addWidget(row(btn_dl, btn_clean, None, stretch_index=None))

        self._rows[mid] = {
            "spec": spec, "badge": badge, "detail": detail, "bar": bar,
            "msg": msg, "btn_dl": btn_dl, "btn_clean": btn_clean,
            "name": name, "widget": f,
        }
        self._paint(mid, st.public())
        return f

    def _paint(self, mid: str, st: Dict[str, Any]) -> None:
        r = self._rows.get(mid)
        if not r:
            return
        status = st.get("status", model_manager.NOT_INSTALLED)
        color = STATUS_COLOR.get(status, C["dim"])
        r["badge"].setText(f"{model_manager.STATUS_LABEL.get(status, status)}")
        r["badge"].setStyleSheet(f"color: {color}; font-weight: 600;")
        r["badge"].setFixedWidth(58)
        r["detail"].setText(st.get("detail") or "")
        ready = status == model_manager.READY
        bundled = r["spec"].get("download_policy") == "bundled"
        r["btn_dl"].setEnabled(not ready and not bundled)
        r["btn_clean"].setEnabled(ready and not bundled)
        _restyle(r["btn_dl"], primary=not ready)

    # ------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        if not self._built:
            return
        states = {s["id"]: s for s in self.ctx.model_states()}
        for mid in self._rows:
            if mid in states:
                self._paint(mid, states[mid])
        rep = self.ctx.models.report()
        self.lb_head.setText(
            f"{rep['ready']} / {rep['total']} 个模型已就绪"
            + ("" if rep["asr_ready"]
               else "　! 语音识别模型还没装，现在不能做歌词"))
        self.lb_cache.setText(f"缓存目录：{rep['hf_cache']}")
        # 运行组件也一起刷（失败不能影响模型部分的显示）
        try:
            self._refresh_rt()
        except Exception:                                 # noqa: BLE001
            from desktop.services import log_service
            log_service.get().exception("运行组件状态刷新失败")

    def _download_rt(self, rid: str) -> None:
        """下载并安装一个运行组件（走任务中心，可取消、可续传）。"""
        tid = self.ctx.workers.start_runtime_download(rid)
        r = self._rt_rows.get(rid)
        if r:
            r["detail"].setText("正在下载…（可在「任务」页看进度/取消）")
            r["btn"].setEnabled(False)
        self._watch(tid, [])

    def _remove_rt(self, rid: str) -> None:
        r = self._rt_rows.get(rid) or {}
        if not self.ask("删除运行组件",
                        f"确定删除「{r.get('name', rid)}」吗？\n"
                        f"删掉后对应功能会不可用，需要时可以重新下载。"):
            return
        try:
            from desktop.services import runtime_service
            runtime_service.RuntimeManager().remove(rid,
                                                    log=lambda m: None)
        except Exception as e:                            # noqa: BLE001
            self.say(f"删除失败：{type(e).__name__}: {e}", ok=False)
            return
        self.say(f"已删除运行组件 {rid}")
        self.refresh()

    def on_show(self) -> None:
        # ⚠️ 必须**先刷一次**：``build()`` 里已经把行建出来了（那时显示的是
        # "正在读取模型状态…"），如果只靠主窗口的定时器刷新，
        # 用户切过来的第一眼会看到那句占位文字（截图里被抓到过）。
        self.refresh()

    def _recheck(self) -> None:
        self.refresh()

    def _open_dir(self) -> None:
        from desktop import paths
        self.ctx.open_path(self.ctx.models.hf_cache or paths.models_dir())

    # ------------------------------------------------------------ 下载
    def _base_models(self) -> List[str]:
        """基础必需：满足条件的 ASR 模型（默认的那个）。"""
        return [self.ctx.default_asr_model()]

    def _recommended(self) -> List[str]:
        out = []
        for s in self.ctx.models.all_specs():
            if s.get("download_policy") == "bundled":
                continue
            st = self.ctx.models.check(s["id"])
            if st.status != model_manager.READY and (s.get("default")
                                                    or s.get("recommended")):
                out.append(s["id"])
        return out or self._base_models()

    def _download_base(self) -> None:
        self._start_download(self._base_models(), "下载基础模型")

    def _download_recommended(self) -> None:
        self._start_download(self._recommended(), "下载推荐模型")

    def _download(self, mid: str) -> None:
        self._start_download([mid], "")

    def _start_download(self, ids: List[str], title: str) -> None:
        ids = [i for i in ids if i]
        if not ids:
            self.say("info", "模型", "推荐的模型都已经装好了。")
            return
        names = []
        total = 0
        for mid in ids:
            st = self.ctx.models.check(mid)
            names.append(st.name)
            total += st.approx_size or 0
        if len(ids) > 1:
            if not self.ask("开始下载",
                            f"将下载 {len(ids)} 个模型：\n\n"
                            + "\n".join("• " + n for n in names)
                            + f"\n\n合计约 {model_manager.human(total)}，"
                              f"下载中可以随时取消，断了也能接着下。\n\n现在开始？"):
                return
        tid = (self.ctx.workers.start_model_download(ids[0])
               if len(ids) == 1
               else self.ctx.workers.start_model_download_many(ids, title))
        self._set_row_busy(ids, "排队中...")
        self._watch(tid, ids)

    def _set_row_busy(self, ids: List[str], text: str) -> None:
        for mid in ids:
            r = self._rows.get(mid)
            if not r:
                continue
            r["bar"].setVisible(True)
            r["bar"].setRange(0, 0)                      # 未知进度：转圈，不编数字
            r["msg"].setText(text)
            r["msg"].setVisible(True)
            r["btn_dl"].setEnabled(False)
            r["btn_clean"].setEnabled(False)

    def _watch(self, tid: str, ids: List[str]) -> None:
        """把任务进度映射到**它真正对应的那些行**上。

        ⚠️ 一定要按 ids 映射。图省事写成"所有行都跟着这个任务转"的话，
        下载 single 的时候其它行会一起转圈 —— 用户会以为在下三个模型。
        """
        self._watch_ids[tid] = list(ids)

    # ------------------------------------------------------------ 清理
    def _cleanup(self, mid: str) -> None:
        st = self.ctx.models.check(mid)
        if not self.ask("清理模型",
                        f"删除「{st.name}」？\n\n位置：{st.location or '（未找到）'}\n\n"
                        f"下次要用还得重新下载（约 "
                        f"{model_manager.human(st.approx_size)}）。",
                        default_yes=False):
            return
        tid = self.ctx.workers.start_model_cleanup(mid)
        r = self._rows.get(mid)
        if r:
            r["msg"].setText("清理中...")
            r["msg"].setVisible(True)
        self._watch(tid, [mid])

    # ------------------------------------------------------------ 任务回调
    def on_task(self, t: Dict[str, Any]) -> None:
        if not self._built:
            return
        tid = t.get("id")
        ids = self._watch_ids.get(tid)
        if not ids:
            return
        state = t.get("state")
        pct = t.get("progress")
        for mid in ids:
            r = self._rows.get(mid)
            if not r:
                continue
            if state in ("queued", "running", "verifying"):
                r["bar"].setVisible(True)
                if pct is None:
                    r["bar"].setRange(0, 0)              # 进度未知 → 转圈
                else:
                    r["bar"].setRange(0, 100)
                    r["bar"].setValue(int(pct))
                r["msg"].setText(str(t.get("message") or ""))
                r["msg"].setVisible(True)
            else:
                r["bar"].setVisible(False)
                r["bar"].setRange(0, 100)
                if state == "done":
                    r["msg"].setText("√ 完成")
                elif state == "cancelled":
                    r["msg"].setText("⊘ 已取消（下次会接着下）")
                else:
                    r["msg"].setText("✗ " + str(t.get("error") or "失败")[:200])
                r["msg"].setVisible(True)
        if state in ("done", "failed", "cancelled"):
            self.refresh()


def _restyle(widget, primary: bool) -> None:
    """改 ``objectName`` 之后**必须重新 polish**，否则样式不会跟着变。

    ⚠️ Qt 的样式表只在控件"重新 polish"时才重新匹配选择器。
    改 ``objectName`` 而不 unpolish/polish 的话，``#primary:disabled``
    这种按名字匹配的规则**永远不会生效** ——
    表现就是"按钮禁用了，但看起来还是启用态"（深蓝底 + 深色字，文字看不见）。
    这是本轮真踩到的坑，模型页上两个禁用按钮都中招。
    """
    from PySide6.QtWidgets import QWidget
    if not isinstance(widget, QWidget):
        return
    widget.setObjectName("primary" if primary else "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
