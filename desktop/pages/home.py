"""「制作」页：选文件 → 填元数据 → 开始转换 → 看结果。

这是整个软件的主页面，别的页都是为它服务的。

⚠️ 设计上的三个必须：

1. **"开始"按钮不能只在参数齐了才可点**（用户不知道怎么才能点）。
   永远是亮的；点了如果缺东西，就在**按钮下面**用红字写清楚缺什么。
2. **换歌要更新标题/艺人**（``fileguess.should_autofill`` 守着），
   但**不能擦掉用户手输的**。Web 版这里踩过两次。
3. **结果区必须在同一页**：转换完跳到"任务"页去看结果，
   距离太远 —— 用户就想要"点一下、看到匹配率、打开文件"。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar,
                               QVBoxLayout, QWidget)

from desktop import fileguess
from desktop.pages.base import (C, Page, button, card, col, dim, h1, h2, hint,
                                label, mono, page_header, row)
from desktop.services import model_manager

# 界面上的语言选项 → whisper 的 language 参数
LANGUAGES = [
    ("自动检测（推荐）", "auto"),
    ("中文", "zh"),
    ("英文", "en"),
    ("日文", "ja"),
    ("韩文", "ko"),
]

SINGER_MODES = [
    ("关闭（用歌词里的 [v1]/[v2] 标记）", "off"),
    ("强制单声部", "1"),
    ("强制双声部（对唱）", "2"),
    ("强制三声部", "3"),
]

MARKERS = [
    ("按 AMLL 规范：只写 ttm:agent（推荐）", "agent"),
    ("额外写 Apple 角色（挂在 <p> 上）", "role"),
    ("两个都写", "both"),
]

MODES = [
    ("AMLL（默认）", "amll"),
    ("Apple Music", "apple"),
]

TIME_FORMATS = [
    ("HH:MM:SS.mmm（TTML1 合法，默认）", "clock"),
    ("MM:SS.mmm（部分旧播放器）", "mmss"),
    ("SS.mmm（秒）", "seconds"),
]

EXPLICIT = [
    ("照实保留", "keep"),
    ("屏蔽成 ****", "mask"),
]

STYLES = [
    ("保持原样", "keep"),
    ("按 Apple 歌词风格规范化（标点/大小写）", "apple"),
]

# 平台 ID（键名必须与 ttml_writer.META_KEYS 一致；这里只做展示）
PLATFORM_FIELDS = [
    ("ncmMusicId", "网易云 ID"),
    ("qqMusicId", "QQ 音乐 ID"),
    ("appleMusicId", "Apple Music ID"),
    ("spotifyId", "Spotify ID"),
    ("kuwoMusicId", "酷我 ID"),
    ("kugouMusicId", "酷狗 ID"),
    ("isrc", "ISRC"),
]


class HomePage(Page):
    TITLE = "制作逐字歌词"
    SUBTITLE = "选音频和歌词 → 点开始。识别、对齐、校验全自动。"

    # ------------------------------------------------------------------ 构建
    def build(self) -> QWidget:
        cfg = self.ctx.config
        self._last_auto = {"title": "", "artists": ""}
        self._current_task: Optional[str] = None
        self._last_ttml = ""

        # ---------- 文件 ----------
        self.ed_audio = QLineEdit()
        self.ed_audio.setPlaceholderText("拖进来或点「浏览」--wav / mp3 / flac / m4a ...")
        self.btn_audio = button("浏览...", on_click=self._pick_audio)
        self.ed_lyrics = QLineEdit()
        self.ed_lyrics.setPlaceholderText("TXT 或 LRC（GBK 编码也能读）")
        self.btn_lyrics = button("浏览...", on_click=self._pick_lyrics)
        for e in (self.ed_audio, self.ed_lyrics):
            e.textChanged.connect(self._on_files_changed)

        files_box = self.card_with(
            h2("① 选文件"),
            _file_row("音频", self.ed_audio, self.btn_audio),
            _file_row("歌词", self.ed_lyrics, self.btn_lyrics),
            hint("歌词支持 GBK/UTF-8 自动识别；LRC 的行级时间戳会用来提高对齐精度。"),
        )

        # ---------- 元数据 ----------
        self.ed_title = QLineEdit()
        self.ed_artists = QLineEdit()
        self.ed_artists.setPlaceholderText("多人用「、」或「,」分隔")
        self.ed_album = QLineEdit()
        self.cb_lang = _combo(LANGUAGES)
        self.cb_model = _combo([])                       # 稍后按清单填
        self.ed_output = QLineEdit(cfg.outputs_dir())
        self.btn_output = button("...", on_click=self._pick_output)

        self.ed_title.textEdited.connect(lambda: self._mark_manual("title"))
        self.ed_artists.textEdited.connect(lambda: self._mark_manual("artists"))
        self._manual = {"title": False, "artists": False}

        meta_grid = QFormLayout()
        meta_grid.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        meta_grid.setHorizontalSpacing(12)
        meta_grid.setVerticalSpacing(7)
        meta_grid.addRow(dim("歌名"), self.ed_title)
        meta_grid.addRow(dim("演唱者"), self.ed_artists)
        meta_grid.addRow(dim("专辑"), self.ed_album)
        meta_grid.addRow(dim("语言"), _wrap(self.cb_lang))
        meta_grid.addRow(dim("识别模型"), _wrap(self.cb_model))
        meta_grid.addRow(dim("输出到"), row(self.ed_output, self.btn_output))
        meta_box = self.card_with(h2("② 填信息（歌名/演唱者会自动猜，可以改）"),
                                  _form(meta_grid))

        # ---------- 高级 ----------
        self.cb_mode = _combo(MODES)
        self.cb_markers = _combo(MARKERS)
        self.cb_singer = _combo(SINGER_MODES)
        self.cb_timefmt = _combo(TIME_FORMATS)
        self.cb_explicit = _combo(EXPLICIT)
        self.cb_style = _combo(STYLES)
        self.chk_separate = _check("先做人声分离（更准，但慢很多，需要 Demucs 模型）")
        self.chk_refine = _check("低匹配率的行自动二次精修", True)
        self.chk_credits = _check("跳过「作词/作曲/演唱：」等制作人员行", True)
        self.chk_tails = _check("补全句尾拖音（避免最后一个字被硬切）", True)
        self.chk_amll = _check("用真正的 AMLL 解析器复检一遍（推荐）", True)
        self.ed_pause = QLineEdit("0")
        self.ed_pause.setMaximumWidth(70)

        adv_grid = QGridLayout()
        adv_grid.setHorizontalSpacing(14)
        adv_grid.setVerticalSpacing(7)
        adv_grid.addWidget(dim("输出模式"), 0, 0)
        adv_grid.addWidget(self.cb_mode, 0, 1)
        adv_grid.addWidget(dim("演唱者标记"), 0, 2)
        adv_grid.addWidget(self.cb_markers, 0, 3)
        adv_grid.addWidget(dim("演唱者分段"), 1, 0)
        adv_grid.addWidget(self.cb_singer, 1, 1, 1, 3)
        adv_grid.addWidget(dim("时间格式"), 2, 0)
        adv_grid.addWidget(self.cb_timefmt, 2, 1)
        adv_grid.addWidget(dim("敏感词"), 2, 2)
        adv_grid.addWidget(self.cb_explicit, 2, 3)
        adv_grid.addWidget(dim("歌词风格"), 3, 0)
        adv_grid.addWidget(self.cb_style, 3, 1)
        adv_grid.addWidget(dim("停顿插空格(秒)"), 3, 2)
        adv_grid.addWidget(self.ed_pause, 3, 3, Qt.AlignmentFlag.AlignLeft)
        for i, c in enumerate((self.chk_refine, self.chk_credits,
                               self.chk_tails, self.chk_amll)):
            adv_grid.addWidget(c, 4 + i // 2, (i % 2) * 2, 1, 2)
        adv_grid.addWidget(self.chk_separate, 6, 0, 1, 4)

        self.adv_box = self.card_with(h2("③ 高级选项（一般不用动）"), _grid(adv_grid))
        self.adv_box.setVisible(False)
        self.btn_adv = button("显示高级选项 ▾", on_click=self._toggle_adv)

        # ---------- 开始 ----------
        self.btn_start = button("开始制作", primary=True, on_click=self._start)
        self.lb_problem = label("", wrap=True, color=C["err"])
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.lb_stage = dim("还没开始")
        self.btn_cancel = button("取消", on_click=self._cancel)
        self.btn_cancel.setEnabled(False)

        self.run_box = self.card_with(
            h2("④ 开始"),
            # stretch_index=None：按钮行谁都不拉伸。默认的"倒数第二个拉伸"
            # 会把主按钮拉成一条长条（截图里看就像个坏掉的进度条）
            row(self.btn_start, self.btn_adv, self.btn_cancel, None,
                stretch_index=None),
            self.lb_problem,
            self.bar,
            self.lb_stage,
        )

        # ---------- 结果 ----------
        self.lb_result_title = h2("还没生成文件")
        self.lb_result_body = label("", wrap=True)
        self.btn_open = button("打开 TTML", on_click=self._open_result)
        self.btn_open.setEnabled(False)
        self.btn_reveal = button("在文件夹里显示", on_click=self._reveal_result)
        self.btn_reveal.setEnabled(False)
        self.btn_editor = button("打开编辑台", on_click=self._open_editor)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(190)
        self.log_box.setObjectName("mono")
        self.res_box = self.card_with(
            h2("⑤ 结果"),
            self.lb_result_title,
            self.lb_result_body,
            row(self.btn_open, self.btn_reveal, self.btn_editor, None,
                stretch_index=None),
            self.log_box,
        )

        # ---------- 组装 ----------
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.setSpacing(14)
        self.banner = label("", wrap=True)
        lay.addWidget(page_header(self))
        lay.addWidget(self.banner)
        for w in (files_box, meta_box, self.adv_box, self.run_box, self.res_box):
            lay.addWidget(w)
        lay.addStretch(1)
        # 平台 ID 折叠在最后（多数人用不到）
        self.platform_box = self._build_platform_box()
        lay.addWidget(self.platform_box)

        self._load_config_into_ui()
        self._fill_models()
        self.refresh()
        return self.scrolled(inner)

    # ------------------------------------------------------------ 平台 ID
    def _build_platform_box(self) -> QWidget:
        self.platform_edits: Dict[str, QLineEdit] = {}
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(6)
        for i, (key, name) in enumerate(PLATFORM_FIELDS):
            ed = QLineEdit()
            ed.setPlaceholderText("可留空")
            self.platform_edits[key] = ed
            g.addWidget(dim(name), i // 2, (i % 2) * 2)
            g.addWidget(ed, i // 2, (i % 2) * 2 + 1)
        box = self.card_with(
            h2("⑥ 平台 ID（可选，会写进 TTML 的 amll:meta）"),
            _grid(g),
            hint("默认「不自动检索」：检索容易搜错歌，填错的 ID 比空着更糟。"
                 "需要的话用界面右上角的「各平台搜索」跳过去自己找。"),
        )
        box.setVisible(False)
        self.btn_platform = button("显示平台 ID ▾",
                                   on_click=lambda: self._toggle(box, self.btn_platform,
                                                                 "显示平台 ID"))
        return box

    def _toggle(self, box: QWidget, btn, name: str) -> None:
        vis = not box.isVisible()
        box.setVisible(vis)
        btn.setText(f"{'隐藏' if vis else '显示'}{name} {'▴' if vis else '▾'}")

    def _toggle_adv(self) -> None:
        self._toggle(self.adv_box, self.btn_adv, "高级选项")
        # 平台 ID 跟着高级一起出现（都是"进阶"的东西）
        if self.adv_box.isVisible() and not self.platform_box.isVisible():
            self.platform_box.setVisible(True)
            self.btn_platform.setText("隐藏平台 ID ▴")

    # ------------------------------------------------------------ 配置读写
    def _load_config_into_ui(self) -> None:
        c = self.ctx.config
        _select(self.cb_lang, c.get("language", "auto"))
        _select(self.cb_mode, c.get("mode", "amll"))
        _select(self.cb_markers, c.get("markers", "agent"))
        _select(self.cb_singer, str(c.get("singer_mode", "off")))
        _select(self.cb_timefmt, c.get("time_format", "clock"))
        _select(self.cb_explicit, c.get("explicit_policy", "keep"))
        _select(self.cb_style, c.get("style", "keep"))
        self.chk_separate.setChecked(bool(c.get("separate_vocals")))
        self.chk_refine.setChecked(bool(c.get("refine", True)))
        self.chk_credits.setChecked(bool(c.get("skip_credits", True)))
        self.chk_tails.setChecked(bool(c.get("extend_tails", True)))
        self.chk_amll.setChecked(bool(c.get("verify_amll", True)))
        self.ed_album.setText(str(c.get("album") or ""))
        self.ed_output.setText(self.ctx.config.outputs_dir())

    def _save_selection(self) -> None:
        c = self.ctx.config
        c.update({
            "language": self.cb_lang.currentData(),
            "mode": self.cb_mode.currentData(),
            "markers": self.cb_markers.currentData(),
            "singer_mode": self.cb_singer.currentData(),
            "time_format": self.cb_timefmt.currentData(),
            "explicit_policy": self.cb_explicit.currentData(),
            "style": self.cb_style.currentData(),
            "separate_vocals": self.chk_separate.isChecked(),
            "refine": self.chk_refine.isChecked(),
            "skip_credits": self.chk_credits.isChecked(),
            "extend_tails": self.chk_tails.isChecked(),
            "verify_amll": self.chk_amll.isChecked(),
            "album": self.ed_album.text().strip(),
            "asr_model": self._model_id(),
        })
        try:
            c.save()
        except OSError:
            pass

    def _fill_models(self) -> None:
        """识别模型下拉：**只列出清单里的 ASR 模型**，并标出有没有下载。"""
        self.cb_model.clear()
        want = self.ctx.default_asr_model()
        for s in self.ctx.models.all_specs():
            if s.get("category") != "asr":
                continue
            st = self.ctx.models.check(s["id"])
            suffix = "" if st.status == model_manager.READY \
                else f"　（未安装，约 {model_manager.human(st.approx_size)}）"
            self.cb_model.addItem(f"{s.get('name', s['id'])}{suffix}", s["id"])
        _select(self.cb_model, want)

    def _model_id(self) -> str:
        return str(self.cb_model.currentData() or self.ctx.default_asr_model())

    # ------------------------------------------------------------ 文件选择
    def _pick_audio(self) -> None:
        last = str(self.ctx.config.get("last_input_dir") or "")
        p, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", last,
            "音频 (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wma);;所有文件 (*)")
        if p:
            self.ed_audio.setText(p)
            self.ctx.config.set("last_input_dir", os.path.dirname(p))
            self._reset_manual()

    def _pick_lyrics(self) -> None:
        last = str(self.ctx.config.get("last_input_dir") or "")
        p, _ = QFileDialog.getOpenFileName(
            self, "选择歌词文件", last,
            "歌词 (*.lrc *.txt *.LRC *.TXT);;所有文件 (*)")
        if p:
            self.ed_lyrics.setText(p)
            self.ctx.config.set("last_input_dir", os.path.dirname(p))
            self._reset_manual()

    def _reset_manual(self) -> None:
        """**用户重新选了文件** → 强制重新自动猜歌名/艺人。

        为什么不"永远不再自动填"：用户手改过一次之后换首歌，
        歌名还停在上一首才是更常见的抱怨。

        ⚠️ 这里是**强制**填，不看 ``_on_files_changed`` 里那两层保护：
        那两层保护的是"程序自动刷新时别覆盖用户手输的"，
        而"用户主动挑了新文件"本身就意味着"这条已经翻篇了"。
        真踩过：只清 ``_manual`` 不改强行覆盖，用户手改过的歌名会把
        "换歌重新猜"一起挡住 —— 第二首歌一直挂着第一首的名字。
        """
        self._manual = {"title": False, "artists": False}
        audio, lyrics = self.ed_audio.text().strip(), self.ed_lyrics.text().strip()
        g = fileguess.suggest(audio, lyrics)
        for field, ed in (("title", self.ed_title), ("artists", self.ed_artists)):
            newv = str(g["artists_text"] if field == "artists" else g["title"] or "")
            ed.setText(newv)
            self._last_auto[field] = newv
        self.refresh()

    def _pick_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                             self.ed_output.text())
        if d:
            self.ed_output.setText(d)

    def _on_files_changed(self) -> None:
        """文件名变了就更新"自动猜"的标题/艺人（不擦用户手输的）。

        ⚠️ **两层保护，缺一不可**（Web 版在这里反复踩）：
        1. ``_manual`` 标记：用户在本字段打过字。
        2. **当前内容既非空、也不等于"上次自动填的值"** → 一定是他自己写的，
           直接不动。只靠第 1 条不够：``setText`` 也会派发 ``textChanged``，
           而"用户打字"和"程序填值"在事件排队顺序上很容易纠缠在一起，
           实测就出现过"标记说手改过、内容却被覆盖"。
        换歌一定更新靠的是"用户重新选了文件 → ``_reset_manual()``"，
        所以第二首歌不会继续挂着第一首的名字。
        """
        audio, lyrics = self.ed_audio.text().strip(), self.ed_lyrics.text().strip()
        g = fileguess.suggest(audio, lyrics)
        for field, ed in (("title", self.ed_title), ("artists", self.ed_artists)):
            newv = str(g["artists_text"] if field == "artists" else g["title"] or "")
            cur = ed.text().strip()
            last = self._last_auto[field].strip()
            if cur and cur != last:
                continue                                 # 用户自己写的，别碰
            if not fileguess.should_autofill(cur, last,
                                            keep_user_input=not self._manual[field]):
                continue
            ed.setText(newv)
            self._last_auto[field] = newv
        self.refresh()

    def _mark_manual(self, field: str) -> None:
        self._manual[field] = True

    # ------------------------------------------------------------ 参数收集
    def params(self) -> Dict[str, Any]:
        p = self.ctx.convert_params(
            audio=self.ed_audio.text().strip(),
            lyrics=self.ed_lyrics.text().strip(),
            title=self.ed_title.text().strip(),
            artists=self.ed_artists.text().strip(),
            album=self.ed_album.text().strip(),
            language=self.cb_lang.currentData(),
            model=self._model_id(),
            mode=self.cb_mode.currentData(),
            markers=self.cb_markers.currentData(),
            singer_mode=self.cb_singer.currentData(),
            time_format=self.cb_timefmt.currentData(),
            explicit_policy=self.cb_explicit.currentData(),
            style=self.cb_style.currentData(),
            separate_vocals=self.chk_separate.isChecked(),
            refine=self.chk_refine.isChecked(),
            skip_credits=self.chk_credits.isChecked(),
            extend_tails=self.chk_tails.isChecked(),
            verify_amll=self.chk_amll.isChecked(),
        )
        try:
            p["pause_space"] = float(self.ed_pause.text().strip() or 0)
        except ValueError:
            p["pause_space"] = 0.0
        for key, ed in self.platform_edits.items():
            v = ed.text().strip()
            if v:
                p[key] = v
        return p

    # ------------------------------------------------------------ 开始/取消
    def _start(self) -> None:
        from desktop.services import convert_params

        p = self.params()
        self._save_selection()
        problems = convert_params.validate(p)
        if problems:
            self.lb_problem.setText("　".join("• " + x for x in problems))
            return
        # 模型没下 → 不去崩在加载里，直接给"去模型页下载"的可操作提示
        if not self.ctx.asr_ready():
            mid = self._model_id()
            self.lb_problem.setText(
                f"• 识别模型还没下载（{mid}）。请到左侧「模型」页点「下载」，"
                f"装好之后再回来点开始。")
            return
        # GPU 运行时（CUDA）没装 → **也在点开始之前拦住**。
        # ⚠️ 为什么必须在这里拦（第 19 轮补）：
        # CUDA 运行时自第 18 轮起改为"按需下载"。如果只在 `asr.py` 里报错，
        # 用户点「开始制作」后会看到一段**技术性报错**（而且是在任务跑起来之后），
        # 而不是"去哪儿点一下就好"。`asr.py` 那条报错要保留（命令行也要用），
        # 但界面这条路要给**可操作**的说法。
        # ⚠️ 判定用 `check()` 而不是"看目录在不在"：目录在但文件不全
        # （下了一半）也算没装好 —— 与模型那边同一个口径。
        gpu_problem = self._gpu_runtime_problem()
        if gpu_problem:
            self.lb_problem.setText(gpu_problem)
            return
        self.lb_problem.setText("")
        self.log_box.clear()
        self.log_box.appendPlainText("正在准备...")
        self._current_task = self.ctx.start_convert(
            p, title=os.path.basename(p["lyrics"]))
        self.bar.setValue(0)
        self.lb_stage.setText("已提交，等模型加载...")
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)

    def _gpu_runtime_problem(self) -> str:
        """GPU 运行时没就绪时返回一句人话，否则返回空串。

        ⚠️ 三种"不能报"的情况，别误拦：
          * **机器上没有 NVIDIA 显卡** —— 那不是"少下个组件"，
            下载 CUDA 运行时也救不了（`asr.py` 明确拒绝 CPU）。
            这时给一句"本软件需要 NVIDIA 显卡"，别让用户白下 529 MB。
          * 清单里没有 cuda 组件（例如源码模式/旧清单）→ 别拦，
            让原来的链路照常跑（否则会把能用的环境拦死）。
          * 运行时检查本身出错 → **不拦**（检查失败不等于功能不可用，
            宁可让转换去试，也不要因为一个检查器的问题挡住用户）。
        """
        try:
            from desktop.services import gpu_probe, runtime_service
            # 没有 NVIDIA 显卡：下运行时也没用，直接说清
            try:
                if not gpu_probe.has_nvidia_gpu():
                    return ("• 没有检测到 NVIDIA 显卡。本软件的语音识别"
                            "必须用显卡（CPU 跑一首歌要几分钟，所以没有做"
                            "降级）。请换一台带 NVIDIA 显卡的机器。")
            except Exception:                            # noqa: BLE001
                pass                                     # 探测不了就不拦
            mgr = runtime_service.RuntimeManager()
            if "cuda" not in [s.get("id") for s in mgr.all_specs()]:
                return ""
            st = mgr.check("cuda")
            if st.status == "ready":
                return ""
            return ("• GPU 运行时还没安装。请到左侧「模型」页，"
                    "在「运行组件（按需下载）」里点 CUDA 那一行的"
                    "「下载并安装」，装好之后再回来点开始。")
        except Exception:                                # noqa: BLE001
            return ""

    def _cancel(self) -> None:
        if self._current_task:
            self.ctx.tasks.cancel(self._current_task)
            self.lb_stage.setText("取消中...（当前这一步跑完就停）")

    # ------------------------------------------------------------ 结果
    def _open_result(self) -> None:
        if self._last_ttml:
            self.ctx.open_path(self._last_ttml)

    def _reveal_result(self) -> None:
        if self._last_ttml:
            self.ctx.reveal(self._last_ttml)

    def _open_editor(self) -> None:
        """打开编辑台（浏览器里的精修界面）。

        ⚠️ **不要**用 ``self.window()`` 去调主窗口的方法：页面和主窗口
        互相调用会让"谁负责什么"彻底糊掉，测试里也起不来。
        走 ``ctx.editor_url()`` 这一条路。
        """
        if not self._last_ttml:
            return
        url = self.ctx.editor_url(self._last_ttml)
        self.ctx.open_path(url)

    # ------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        """刷新顶部横幅（模型/环境是否就绪）。任务进度由 ``on_task`` 推。"""
        if not self._built:
            return
        try:
            ready = self.ctx.asr_ready()
        except Exception:                                # noqa: BLE001
            ready = False
        if ready:
            self.banner.setText("")
            self.banner.setVisible(False)
        else:
            mid = self._model_id()
            st = self.ctx.models.check(mid)
            self.banner.setText(
                f"!　识别模型「{st.name}」还没装好（{st.detail}）。"
                f"到左侧「模型」页点「下载」即可 -- 只有第一次需要。")
            self.banner.setVisible(True)
            self.banner.setStyleSheet(f"color: {C['warn']};")

    def on_show(self) -> None:
        """切到本页时重列模型（模型页刚下完的话这里要更新）。"""
        if self._built:
            cur = self._model_id()
            self._fill_models()
            _select(self.cb_model, cur)
            self.refresh()

    # ------------------------------------------------------------ 任务回调
    def on_task(self, t: Dict[str, Any]) -> None:
        if not self._built or t.get("id") != self._current_task:
            return
        state = t.get("state")
        pct = t.get("progress")
        if pct is not None:
            self.bar.setValue(int(pct))
        self.lb_stage.setText(str(t.get("message") or ""))
        for entry in t.get("logs") or []:
            if entry["m"] not in self.log_box.toPlainText():
                self.log_box.appendPlainText(entry["m"])
        if state in ("done", "failed", "cancelled"):
            self.btn_start.setEnabled(True)
            self.btn_cancel.setEnabled(False)
        if state == "done":
            res = t.get("result") or {}
            self._last_ttml = str(res.get("output_path") or "")
            sm = res.get("summary") or {}
            ok = bool(sm.get("format_ok"))
            self.lb_result_title.setText(
                ("√ 制作完成：" if ok else "! 生成了，但格式自检有问题：")
                + os.path.basename(self._last_ttml))
            self.lb_result_body.setText(
                f"匹配率 {float(sm.get('match_rate') or 0):.1%}　|　"
                f"{sm.get('lines', 0)} 行 / {sm.get('words', 0)} 个字　|　"
                f"格式错误 {sm.get('errors', 0)} 个、警告 {sm.get('warnings', 0)} 个　|　"
                f"用时 {res.get('elapsed', sm.get('elapsed', 0))} 秒\n"
                f"文件：{self._last_ttml}")
            self.btn_open.setEnabled(bool(self._last_ttml))
            self.btn_reveal.setEnabled(bool(self._last_ttml))
        elif state == "failed":
            self.lb_result_title.setText("✗ 没做成")
            self.lb_result_body.setText(str(t.get("error") or "")[:600])
        elif state == "cancelled":
            self.lb_result_title.setText("⊘ 已取消")
            self.lb_result_body.setText("产物没有被改动。")


# ------------------------------------------------------------------ 小工具

def _file_row(title: str, edit: QLineEdit, btn) -> QWidget:
    """一行"标签 + 输入框 + 浏览按钮"。

    标签固定宽度、垂直居中 —— 用 QFormLayout 的话两行的输入框会因为
    标签宽度不同而左右错开，看着不整齐。
    """
    lb = dim(title)
    lb.setFixedWidth(44)
    lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return row(lb, edit, btn)


def _wrap(w: QWidget) -> QWidget:
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(w)
    lay.addStretch(1)
    return holder


def _form(fl: QFormLayout) -> QWidget:
    w = QWidget()
    w.setLayout(fl)
    return w


def _grid(gl: QGridLayout) -> QWidget:
    w = QWidget()
    w.setLayout(gl)
    return w


def _combo(items) -> QComboBox:
    cb = QComboBox()
    for text, data in items:
        cb.addItem(text, data)
    return cb


def _check(text: str, checked: bool = False):
    from PySide6.QtWidgets import QCheckBox
    c = QCheckBox(text)
    c.setChecked(checked)
    return c


def _select(cb: QComboBox, value) -> None:
    for i in range(cb.count()):
        if cb.itemData(i) == value:
            cb.setCurrentIndex(i)
            return
