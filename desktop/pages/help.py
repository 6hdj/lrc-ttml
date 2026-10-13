"""「帮助 / 诊断」页 —— 任务书 C 要求的一页，之前**根本没有**。

为什么要单独一页（而不是塞在「设置」里）：

* 用户遇到问题时的第一反应是"出事了，怎么查" —— 他需要一个**能自己找到答案**
  的地方，而不是先翻设置；
* 之前那些东西分散在「设置 → ③ 诊断」里，只覆盖了体检；
  **`--doctor` 的完整输出、日志在哪、装到哪了、版本号是多少**，
  用户都不容易拿到；
* 报问题时要能把环境信息**一条复制给别人**，这一步以前没有入口。

## 这一页放什么（都是"用户真的会问"的）

1. **一句话状态**：能不能正常用（体检结论）；
2. **完整诊断**：逐项体检结果（含显卡），每项都带"照着做就能好"的下一步；
   `doctor.py` 的规矩是"只说缺什么等于没说"，这里照做；
3. **报问题要用的信息**：版本 / 安装目录 / 数据目录 / 日志目录 + **一键复制**；
4. **常见问题速查**：把本仓实际踩过、且用户也会遇到的症状与解法列出来
   （不是编的，逐条来自 `AGENTS.md` 与文档里的真实案例）。

⚠️ 复用 `welcome.py` 那套渲染（`STATUS_ICON` / `STATUS_COLOR` / `_paint` 的结构），
**不要另写一套图标或配色** —— 本仓已经因为"两处各写一份"踩过好几次。
"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtWidgets import QGridLayout, QWidget

from desktop import __version__
# ⚠️ **必须继承 `Page`**（不是 `QWidget`）：基类 `__init__(ctx)` 负责保存
# `self.ctx`、置 `_built`、以及提供 `card_with`/`say`/`scrolled`/`refresh`。
# 直接继承 QWidget 会在构造时报
# `TypeError: QWidget.__init__ called with wrong argument types`
# （`_make_page` 是 `Page(ctx)` 这么调的）。真踩过。
from desktop.pages.base import (C, Page, STATUS_COLOR, STATUS_ICON, button, h2,
                                label, page_header, row)


# 常见问题速查：每条都是**真踩过**的症状 → 用户能自己做的动作。
# ⚠️ 写"是什么问题 + 怎么解决"，不要写"请联系开发者"。
FAQ = [
    ("提示「没有可用的显卡运行时」/ 转换特别慢",
     "点上面的「完整诊断」看 cuda 那几条；缺 CUDA 运行库时，"
     "到「模型」页点「补全」会自动装。用 CPU 跑一首歌要几分钟，属正常。"),
    ("转换报「未找到 CAM++ 声纹模型」/ 对唱没分左右栏",
     "到「模型」页下载「CAM++ 声纹」；或者在歌词里手写 [v1]/[v2] 标记。"
     "注意：全曲只有 v1 时**一定不会**左右分栏（这是 Apple Music 的规则）。"),
    ("对唱分错了某几行",
     "声纹分错的常是**连续几行**。到编辑台「声部」页：点行号选起点、"
     "Shift 点终点可批量改。比调参数快得多。"),
    ("歌词文件读进来是乱码",
     "中文 Windows 上的 .lrc 常是 GBK。本程序会自动识别 UTF-8/GB18030，"
     "但 **Big5（繁体）** 会被误判 —— 请先转成 UTF-8。"),
    ("和声（括号里的部分）没显示",
     "和声必须是某一行**内部的子内容**，不能单独成行（AMLL 规则）。"
     "把和声写在同一行的括号里，程序会自动合并。"),
    ("卸载后安装目录没删干净",
     "卸载会保留主程序与运行库，并把收尾交给一个**退出后自动运行**的清理脚本；"
     "正常几秒内就删干净了。若仍在，手动删掉那个文件夹即可。"),
    ("想换模型缓存位置 / 模型占了 C 盘",
     "到「设置 → 目录」把工作目录改到别的盘；已下的模型在「模型」页可以清理。"),
    ("双击图标没反应 / 窗口一闪而过",
     "先看日志：上面「打开日志目录」。崩溃信息会写在里面"
     "（打包版没有控制台，日志是唯一的线索）。"),
]


class HelpPage(Page):
    KEY = "help"
    TITLE = "帮助 / 诊断"
    SUBTITLE = "出问题了先看这里：环境行不行、日志在哪、怎么报给我。"

    def build(self) -> QWidget:
        self._items: List[Dict[str, Any]] = []

        # ---- ① 一句话状态 + 动作 ----
        self.lb_state = label("还没体检。点右边的「完整诊断」跑一遍。",
                              wrap=True)
        btn_full = button("完整诊断（含显卡）", primary=True,
                          on_click=lambda: self._check(True))
        btn_light = button("快速诊断", on_click=lambda: self._check(False))
        btn_copy = button("复制环境信息", on_click=self._copy_all)
        state_box = self.card_with(
            h2("① 现在能不能用"),
            self.lb_state,
            row(btn_full, btn_light, btn_copy, None, stretch_index=None),
        )

        # ---- ② 逐项结果 ----
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(6)
        self.grid.setContentsMargins(0, 0, 0, 0)
        # ⚠️ 原始清单也留着（从「设置」页合并过来的）：图形化那部分只列**不合格**的，
        # 但"全部项"在报问题时有用（对方可能想确认某个 OK 的项到底什么版本）。
        from PySide6.QtWidgets import QPlainTextEdit
        self.env_log = QPlainTextEdit()
        self.env_log.setReadOnly(True)
        self.env_log.setObjectName("mono")
        self.env_log.setMaximumHeight(300)
        # ⚠️ 200 太矮：体检有 20 项、每项还可能带一行"→ 怎么修"，
        # 截断在中间会让用户以为信息就这些（截图里确实断在"NVIDIA 声卡驱动"那行）。
        # 300 能放下大多数情况；再长它在自己的滚动条里滚。
        self.detail_box = self.card_with(
            h2("② 逐项诊断"),
            label("每一项都带「怎么修」。没列出来的项说明是好的。", kind="dim"),
            self.grid_widget,
            label("完整清单（复制给开发者时用）：", kind="dim"),
            self.env_log,
        )

        # ---- ③ 报问题要用的信息 ----
        self.lb_paths = label("", wrap=True, kind="mono")
        btn_log = button("打开日志目录", on_click=self._open_logs)
        btn_data = button("打开数据目录", on_click=self._open_data)
        btn_app = button("打开安装目录", on_click=self._open_app)
        info_box = self.card_with(
            h2("③ 报问题要用的信息"),
            label("把下面这一块复制给对方，能省很多来回。", kind="dim"),
            self.lb_paths,
            row(btn_log, btn_data, btn_app, None, stretch_index=None),
        )

        # ---- ④ 常见问题 ----
        faq_widgets = []
        for q, a in FAQ:
            faq_widgets.append(label("· " + q, color=C["warn"]))
            faq_widgets.append(label("   " + a, wrap=True, kind="dim"))
        faq_box = self.card_with(h2("④ 常见问题"), *faq_widgets)

        inner = QWidget()
        from PySide6.QtWidgets import QVBoxLayout
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.setSpacing(14)
        lay.addWidget(page_header(self))
        for w in (state_box, self.detail_box, info_box, faq_box):
            lay.addWidget(w)
        lay.addStretch(1)

        self._load_paths()
        self._check(False)                      # 进来先给个轻量结论
        return self.scrolled(inner)

    # ------------------------------------------------------------ 体检
    def _check(self, full: bool) -> None:
        self._full = full
        self.lb_state.setText("体检中…（完整诊断要跑显卡那一步，几秒）"
                              if full else "快速体检中…")
        try:
            items = self.ctx.env_check(full=full)
        except Exception as e:                          # noqa: BLE001
            self.lb_state.setText(f"体检失败：{type(e).__name__}: {e}")
            return
        self._items = items
        self._paint(items)

    def _paint(self, items: List[Dict[str, Any]]) -> None:
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

        # ⚠️ 只列**不合格**的 + 显卡（全列出来用户根本读不完，
        # 而且"哪些是好的"对他没帮助）。这一条和 welcome 页同一套判断。
        bad = [i for i in items
               if (not i["ok"]) or i["key"] == "gpu"
               or i["level"] in ("error", "warn")]
        for r, it in enumerate(bad):
            color = STATUS_COLOR.get(it["level"], C["dim"])
            icon = STATUS_ICON.get(it["level"], "•")
            name = label(f"{icon} {it['name']}", color=color)
            name.setFixedWidth(250)
            cell = QWidget()
            from PySide6.QtWidgets import QVBoxLayout
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            v.addWidget(label(it["detail"] or "", wrap=True, kind="dim"))
            if it.get("fix"):
                v.addWidget(label("→ " + it["fix"], wrap=True,
                                  color=C["warn"]))
            self.grid.addWidget(name, r, 0)
            self.grid.addWidget(cell, r, 1)

        errs = [i for i in items if i["level"] == "error"]
        # 原始清单（所有项，不只是不合格的）
        lines = []
        for it in items:
            icon = STATUS_ICON.get(it["level"], "•")
            lines.append(f"[{icon}] {it['name']}：{it['detail']}")
            if it.get("fix"):
                lines.append(f"      → {it['fix']}")
        self.env_log.setPlainText("\n".join(lines))

        if not bad:
            self.lb_state.setText("√ 全部正常，可以放心用。")
            self.lb_state.setStyleSheet(f"color: {C['ok']}; font-weight: 600;")
        elif errs:
            self.lb_state.setText(f"! 有 {len(errs)} 项必须处理才能正常用"
                                  f"（下面红色的）。")
            self.lb_state.setStyleSheet(f"color: {C['err']}; font-weight: 600;")
        else:
            self.lb_state.setText(f"基本可用；有 {len(bad)} 项提醒"
                                  f"（黄色，不影响先跑起来）。")
            self.lb_state.setStyleSheet(f"color: {C['warn']}; font-weight: 600;")

    # ------------------------------------------------------------ 路径信息
    def _load_paths(self) -> None:
        try:
            from desktop import paths
            lines = [
                f"版本      : Lyric2TTML {__version__}",
                f"安装目录  : {paths.app_dir()}",
                f"数据目录  : {paths.data_dir()}",
                f"日志目录  : {paths.log_dir() if hasattr(paths, 'log_dir') else paths.data_dir()}",
            ]
            try:
                import speaker_campplus as sp
                lines.append(f"声纹后端  : {sp.backend() or '(不可用)'}")
            except Exception:                           # noqa: BLE001
                pass
            self.lb_paths.setText("\n".join(lines))
        except Exception as e:                          # noqa: BLE001
            self.lb_paths.setText(f"（取路径失败：{type(e).__name__}: {e}）")

    def _copy_all(self) -> None:
        """把"报问题要用的东西"一次性复制到剪贴板。"""
        import datetime
        head = [f"Lyric2TTML {__version__}",
                f"时间: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
                "", self.lb_paths.text(), "", "诊断："]
        body = []
        for it in self._items:
            mark = "OK  " if it["ok"] else "FAIL"
            body.append(f"[{mark}] {it['name']}: {it['detail']}")
            if it.get("fix"):
                body.append(f"        修：{it['fix']}")
        text = "\n".join(head + body)
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.lb_state.setText("已复制环境信息到剪贴板，粘给对方即可。")
            self.lb_state.setStyleSheet(f"color: {C['ok']}; font-weight: 600;")
        except Exception as e:                          # noqa: BLE001
            self.lb_state.setText(f"复制失败：{type(e).__name__}")

    def _open_logs(self) -> None:
        try:
            from desktop import paths
            d = paths.log_dir() if hasattr(paths, "log_dir") else paths.data_dir()
            self.ctx.open_path(d)
        except Exception as e:                          # noqa: BLE001
            self.say("warn", "打不开日志目录", f"{type(e).__name__}: {e}")

    def _open_data(self) -> None:
        try:
            from desktop import paths
            self.ctx.open_path(paths.data_dir())
        except Exception as e:                          # noqa: BLE001
            self.say("warn", "打不开数据目录", f"{type(e).__name__}: {e}")

    def _open_app(self) -> None:
        try:
            from desktop import paths
            self.ctx.open_path(paths.app_dir())
        except Exception as e:                          # noqa: BLE001
            self.say("warn", "打不开安装目录", f"{type(e).__name__}: {e}")

    def refresh(self) -> None:
        if not self._built:
            return
        self._load_paths()
