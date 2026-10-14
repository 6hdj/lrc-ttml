"""「设置」页：工作目录、默认参数、环境体检、日志与诊断。

**设置页最容易变成垃圾桶**，所以这里只放三类：

1. **路径**（工作目录、模型缓存、日志），并显示"实际生效的位置" ——
   用户报问题时第一句该问的就是这个。
2. **默认值**（下次新建任务用什么），不在这里改"这一次"的参数。
3. **诊断**（一键体检、打开日志目录、复制诊断信息），
   让用户能把"能定位问题的东西"发给开发者，而不是"它不好使"。

⚠️ 改路径必须**当场校验可写**，不能等到转换时才发现写不进去。
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLineEdit, QMessageBox,
                               QPlainTextEdit, QVBoxLayout, QWidget)

from desktop import paths
from desktop.pages.base import (C, Page, STATUS_COLOR, STATUS_ICON, button,
                                card, col, dim, h2, hint, label, page_header,
                                row)
from desktop.services import model_manager


class SettingsPage(Page):
    TITLE = "设置"
    SUBTITLE = "目录、默认值和诊断。改完立刻生效。"

    def build(self) -> QWidget:
        # ---------- 目录 ----------
        self.ed_workspace = QLineEdit()
        btn_pick = button("选择...", on_click=self._pick_workspace)
        btn_open_out = button("打开输出目录",
                              on_click=lambda: self.ctx.open_path(
                                  self.ctx.config.outputs_dir()))
        btn_open_log = button("打开日志目录",
                              on_click=lambda: self.ctx.open_path(paths.logs_dir()))
        btn_save = button("保存", primary=True, on_click=self._save)

        self.lb_paths = label("", wrap=True, kind="faint")

        dir_box = self.card_with(
            h2("① 目录"),
            row(dim("工作目录"), self.ed_workspace, btn_pick),
            hint("转换产物写到 <工作目录>/outputs，上传的临时文件在 uploads/。"
                 "放在你找得到的地方（比如 D:\\歌词）。"),
            row(btn_save, btn_open_out, btn_open_log, None, stretch_index=None),
            self.lb_paths,
        )

        # ---------- 默认参数 ----------
        from desktop.pages.home import LANGUAGES, SINGER_MODES, TIME_FORMATS
        self.cb_lang = _combo(LANGUAGES)
        self.cb_singer = _combo(SINGER_MODES)
        self.cb_timefmt = _combo(TIME_FORMATS)
        self.chk_portable = QCheckBox("便携模式说明（把 portable.txt 放到程序目录...）")
        self.chk_portable.setEnabled(False)
        self.chk_portable.setChecked(paths.portable())

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(7)
        form.addRow(dim("默认语言"), _wrap(self.cb_lang))
        form.addRow(dim("默认分声部"), _wrap(self.cb_singer))
        form.addRow(dim("默认时间格式"), _wrap(self.cb_timefmt))

        # 主题：下拉里原来写着"浅色（还没做）"—— 现在是**真的**了。
        # ⚠️ 换主题必须**当场生效**（不用重启），所以这里接了 currentIndexChanged。
        self.cb_theme = _combo([("跟随系统", "auto"), ("深色", "dark"),
                                ("浅色", "light")])
        self.cb_theme.currentIndexChanged.connect(self._on_theme_changed)
        form.addRow(dim("主题"), _wrap(self.cb_theme))

        self.cb_port = _combo([("8765", 8765), ("8770", 8770), ("8899", 8899)])
        form.addRow(dim("编辑台端口"), _wrap(self.cb_port))
        form.addRow("", hint("编辑台（浏览器里的精修界面）用这个本地端口。"
                             "被占用的话换一个。"))

        default_box = self.card_with(h2("② 默认值"), _form(form))

        # ---------- 诊断 ----------
        self.lb_env = label("还没体检", wrap=True)
        self.env_log = QPlainTextEdit()
        self.env_log.setReadOnly(True)
        self.env_log.setObjectName("mono")
        self.env_log.setMaximumHeight(160)
        btn_env = button("重新体检（含显卡）", primary=True,
                         on_click=lambda: self._env_check(True))
        btn_env_light = button("快速体检", on_click=lambda: self._env_check(False))
        btn_copy = button("复制诊断信息", on_click=self._copy_diag)
        btn_help = button("打开「帮助 / 诊断」页",
                          on_click=lambda: self._goto_help())

        # ⚠️ 完整的体检已经搬到**「帮助 / 诊断」页**（任务书 C 要求那一页）。
        # 这里只留"改完路径当场看结论"这一小块 + 指路 ——
        # 两处各做一套完整体检必然漂移（本仓的老教训）。
        diag_box = self.card_with(
            h2("③ 诊断"),
            self.lb_env,
            row(btn_env, btn_env_light, btn_copy, None, stretch_index=None),
            self.env_log,
            hint("更完整的诊断（逐项怎么修、常见问题、一键复制环境信息）"
                 "在左边「帮助 / 诊断」页。"),
            row(btn_help, None, stretch_index=None),
        )

        # ---------- 关于 ----------
        about_box = self.card_with(
            h2("④ 关于"),
            label(f"Lyric2TTML 桌面版 {self.ctx.version}"),
            hint("普通歌词（TXT/LRC）+ 音频 → Apple Music / AMLL 逐字 TTML。"
                 "完全离线，不需要 API Key。识别用 faster-whisper，"
                 "分声部用 CAM++ 声纹，人声分离用 Demucs（可选）。"),
            hint("原版浏览器编辑台仍然可用：命令行 `python web_server.py`，"
                 "或在「制作」页做完后点「打开编辑台」。"),
        )

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.setSpacing(14)
        lay.addWidget(page_header(self))
        for w in (dir_box, default_box, diag_box, about_box):
            lay.addWidget(w)
        lay.addStretch(1)

        self._load()
        return self.scrolled(inner)

    # ------------------------------------------------------------ 读写
    def _load(self) -> None:
        c = self.ctx.config
        self.ed_workspace.setText(self.ctx.config.workspace())
        _select(self.cb_lang, c.get("language", "auto"))
        _select(self.cb_singer, str(c.get("singer_mode", "off")))
        _select(self.cb_timefmt, c.get("time_format", "clock"))
        # ⚠️⚠️ 主题下拉**必须在屏蔽信号的情况下**回填：
        # `cb_theme` 接了 `currentIndexChanged → _on_theme_changed`，
        # 而 `_select()` 内部会 `setCurrentIndex()` ⇒ **触发一次真实的主题切换**。
        # 后果（真踩到）：进程启动时已经按配置/环境变量设好主题了，
        # 一进设置页就被下拉框的"回填"覆盖回**存盘里的旧值** ——
        # 表现是"启动时选的主题被悄悄改掉"（我用 `L2T_THEME` 截图时就是这么被坑的：
        # 明明设了浅色，截出来还是深色）。
        _blocked = self.cb_theme.blockSignals(True)
        try:
            _select(self.cb_theme, c.get("theme", "light"))
        finally:
            self.cb_theme.blockSignals(_blocked)
        _select(self.cb_port, int(c.get("web_port") or 8765))
        self._paint_paths()

    def _paint_paths(self) -> None:
        d = paths.describe()
        self.lb_paths.setText(
            f"程序目录：{d['app_dir']}\n"
            f"数据目录：{d['data_dir']}"
            f"{'（便携模式）' if d['portable'] else ''}\n"
            f"配置文件：{d['config']}\n"
            f"模型缓存：{d['hf_cache']}")

    def _save(self) -> None:
        ws = self.ed_workspace.text().strip()
        if ws:
            try:
                paths.ensure_dir(ws)
                probe = os.path.join(ws, ".write_test")
                with open(probe, "w", encoding="utf-8") as fh:
                    fh.write("ok")
                os.remove(probe)
            except OSError as e:
                self.say("warn", "这个目录写不进去",
                         f"{ws}\n\n{e}\n\n换一个你有权限的目录"
                         f"（比如 D:\\歌词），不要选 C:\\Program Files 里面。")
                return
        c = self.ctx.config
        c.update({
            "workspace": ws,
            "language": self.cb_lang.currentData(),
            "singer_mode": self.cb_singer.currentData(),
            "time_format": self.cb_timefmt.currentData(),
            "theme": self.cb_theme.currentData(),
            "web_port": int(self.cb_port.currentData() or 8765),
        })
        try:
            c.save()
        except OSError as e:
            self.say("warn", "保存失败", f"写配置文件失败：{e}")
            return
        self.ctx.models = model_manager.ModelManager()   # 工作目录可能变了
        self._paint_paths()
        self.say("info", "已保存", "设置已保存，立刻生效。")

    def _pick_workspace(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择工作目录",
                                             self.ed_workspace.text() or
                                             os.path.expanduser("~"))
        if d:
            self.ed_workspace.setText(d)

    # ------------------------------------------------------------ 体检
    def _env_check(self, full: bool) -> None:
        self.lb_env.setText("检查中...（含显卡的话要几秒）")
        tid = self.ctx.workers.start_env_check(full=full)
        self._env_tid = tid

    def submit_current(self) -> bool:
        """Enter 快捷键：保存设置（本页的主操作）。

        ⚠️ 这里选「保存」而不是「重新体检」，理由：
        Enter 的语义是"确认/提交当前表单"，而保存是**幂等、廉价**的；
        体检要几秒且会把结论覆盖掉，误触代价更大。
        """
        self._save()
        return True

    def on_task(self, t: Dict[str, Any]) -> None:
        if not self._built or t.get("id") != getattr(self, "_env_tid", ""):
            return
        if t.get("state") in ("done", "failed", "cancelled"):
            env = (t.get("result") or {}).get("env") or {}
            self._render_env(env)

    def _render_env(self, env: Dict[str, Any]) -> None:
        items: List[Dict[str, Any]] = env.get("items") or []
        if not items:
            self.lb_env.setText("体检没返回结果（看日志）")
            return
        color = (C["ok"] if env.get("ready") and not env.get("warnings")
                 else C["warn"] if env.get("ready") else C["err"])
        self.lb_env.setText(env.get("headline") or "")
        self.lb_env.setStyleSheet(f"color: {color}; font-weight: 600;")
        lines = []
        for it in items:
            icon = STATUS_ICON.get(it.get("level"), "•")
            lines.append(f"[{icon}] {it['name']}：{it['detail']}")
            if it.get("fix"):
                lines.append(f"      → {it['fix']}")
        self.env_log.setPlainText("\n".join(lines))

    def refresh(self) -> None:
        if not self._built:
            return
        self._paint_paths()

    # ------------------------------------------------------------ 诊断
    def _on_theme_changed(self, *_a) -> None:
        """主题**立即生效**（不重启）。

        三步缺一不可：
          1. `apply_theme()` 原地更新 `C` / `STATUS_COLOR`（页面里直接取色的地方）；
          2. 重新生成 QSS 挂到 `QApplication` 上（按钮/卡片/输入框的样式）；
          3. **重新 polish 现有控件** —— Qt 的样式表只在控件"重新 polish"时
             才重新匹配选择器（本仓在 `models.py::_restyle` 里已经踩过这个坑，
             那里有同样的处理）。
        ⚠️ 顺序不能反：先换色再设样式表，否则新样式表用的是旧颜色。
        """
        from PySide6.QtWidgets import QApplication
        from desktop.pages.base import apply_theme, qss
        name = self.cb_theme.currentData()
        real = apply_theme(str(name or "dark"))
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss(real))
            for w in app.allWidgets():
                try:
                    w.style().unpolish(w)
                    w.style().polish(w)
                    w.update()
                except Exception:                       # noqa: BLE001
                    pass
        try:
            self.ctx.config.set("theme", str(name))
            self.ctx.config.save()
        except Exception as e:                          # noqa: BLE001
            # ⚠️ `log_service` 在本模块是**局部导入**（见 `_copy_diag` 附近），
            # 顶层没有导入它 —— 直接写 `log_service.get()` 会 NameError。
            from desktop.services import log_service as _ls
            _ls.get().warning("保存主题失败：%s", e)
        self._paint_paths()

    def _goto_help(self) -> None:
        """跳到「帮助 / 诊断」页。

        ⚠️ 用主窗口注入的 `_nav_target`（`main_window._page()` 会给每一页挂上），
        不要 `self.window()` —— 页面在 QStackedWidget 里，
        `window()` 拿到的类型不确定，调它的方法容易 AttributeError（本仓踩过）。
        """
        win = getattr(self, "_nav_target", None)
        if win and hasattr(win, "show_page"):
            win.show_page("help")
        else:
            self.say("info", "找不到主窗口",
                     "请点左侧的「帮助 / 诊断」。")

    def _copy_diag(self) -> None:
        from desktop.services import log_service
        lines = [
            f"Lyric2TTML 桌面版 {self.ctx.version}",
            f"Python {_pyver()}",
        ]
        for k, v in paths.describe().items():
            lines.append(f"{k} = {v}")
        env = self.ctx.env_summary or {}
        if env:
            lines.append("")
            lines.append("体检：" + str(env.get("headline")))
            for it in env.get("items") or []:
                lines.append(f"  [{it['level']}] {it['name']}：{it['detail']}")
        lines.append("")
        lines.append("最近日志：")
        for e in log_service.snapshot(40):
            lines.append(f"  {e['t']} {e['m']}")
        text = "\n".join(lines)
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.say("info", "已复制", "诊断信息已复制到剪贴板，粘贴给开发者即可。")
        except Exception:                                # noqa: BLE001
            self.say("info", "诊断信息", text[:4000])


def _pyver() -> str:
    import sys
    v = sys.version_info
    return f"{v.major}.{v.minor}.{v.micro}"


def _form(fl: QFormLayout) -> QWidget:
    w = QWidget()
    w.setLayout(fl)
    return w


def _wrap(w: QWidget) -> QWidget:
    h = QWidget()
    lay = QHBoxLayout(h)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(w)
    lay.addStretch(1)
    return h


def _combo(items) -> QComboBox:
    cb = QComboBox()
    for text, data in items:
        cb.addItem(text, data)
    return cb


def _select(cb: QComboBox, value) -> None:
    for i in range(cb.count()):
        if cb.itemData(i) == value:
            cb.setCurrentIndex(i)
            return
