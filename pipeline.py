"""
歌词 → Apple Music / AMLL TTML 逐字歌词 主流水线。

    audio + lyrics(LRC/纯文本)
        │
        ├─ 1. 解析歌词（LRC 行级时间戳 / 纯文本 / [v1][v2] 演唱者 / 括号背景人声）
        ├─ 2. faster-whisper 听音频，拿到**真实的**词级时间戳（AI 语音识别）
        ├─ 3. 动态规划强制对齐：把歌词每个字钉到识别结果上（同音字/漏字/多字都能容错）
        ├─ 4. 低置信度行二次精修：只转写这一行的时间窗，重新对齐
        ├─ 5. 后处理：窗口收敛、重叠消除、插值补全
        └─ 6. 生成 TTML + 确定性格式自检

全程离线，不需要任何 API Key。
"""
from __future__ import annotations

import os
import re
import sys
import time
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from asr import AsrWord, WhisperASR, audio_duration, normalize_language_code, whisper_language_code
from matcher import AlignedLine, AlignReport, LyricMatcher, Unit, scan_line
from preprocess import (LyricLine, detect_agent_switch, parse_lyrics,
                        tokenize_chinese)
import onset_align
import tail_extend
from ttml_check import CheckIssue, CheckResult, check_ttml
import snapshot
from ttml_writer import TTMLWriter

# 明显的制作人员信息行（不是歌词）
CREDIT_RE = re.compile(
    r"^\s*[\[\(（]?\s*(作词|作曲|编曲|混音师|混音|母带制作|母带|出品|制作人|制作|"
    r"录音棚|录音师|录音|乐队|吉他|贝斯|鼓手|鼓|打击乐|弦乐|大提琴|中提琴|小提琴|钢琴|"
    r"长笛|萨克斯|二胡|古筝|琵琶|笛子|笛|箫|键盘|合成器|"
    r"伴唱|和声编写|和声|哼唱|配唱|人声编辑|人声|监制|企划|统筹|宣发|"
    r"演唱|歌手|角色|配音|声优|指挥|"
    r"视觉|封面|插画|曲绘|绘制|绘|题字|设计|动画|分镜|特效|剪辑|字幕|视频|"
    r"后期|导演|摄影|特别感谢|感谢|"
    r"lyricist|composer|arranger|producer|mixing|mastering|recording|guitar|bass|drums|"
    r"cello|violin|viola|piano|flute|saxophone|keyboard|orchestra|"
    r"cast|voice\s*actor|backing\s*vocal|vocal|art\s*design|illustration|animation)",
    re.IGNORECASE,
)

# "歌名 - 歌手A&歌手B" 这种 LRC 头部的标题行（不是歌词）
TITLE_LINE_RE = re.compile(r"^\s*\S.{0,60}?\s[-–—]\s\S")
TITLE_LINE_SEPS = ("&", "/", "、", "feat", "ft.", "，")

# "录音师 Recording Engineer：某某" 这类制作信息（前 30 秒内带冒号的短标签行）
LABEL_LINE_RE = re.compile(r"^[^，。！？、；;]{1,50}?[:：]\s*\S")
# "演唱：" 这种**冒号后是空的**标签行（真踩过：制作人名字被单独换行时常见）
EMPTY_LABEL_RE = re.compile(r"^[^，。！？、；;]{1,20}?[:：]\s*$")

# "角色 —— 配音演员" 这种演员表行（真踩过：它不是歌词，但既没冒号也没常见的
# 分隔符，两条规则都漏；三行挤在 0.13/0.18/0.22 秒，逼得对齐输出重叠的坏行）
# 收紧条件：破折号两侧各有 1~24 字、整行没有句读标点。
CAST_LINE_RE = re.compile(r"^\s*[^，。！？、；;:：]{1,24}?\s*[—–]{1,2}\s*"
                          r"[^，。！？、；;:：]{1,24}\s*$")


def _is_credit_line(line: LyricLine) -> bool:
    """判断一行是不是制作人员/标题信息（不是歌词）"""
    text = line.text.strip()
    if CREDIT_RE.match(text):
        return True
    # 制作信息几乎都在开头；限制在 30 秒内，避免把歌词正文里的破折号/冒号误杀
    if line.line_start is None or line.line_start >= 30:
        return False
    if LABEL_LINE_RE.match(text) or EMPTY_LABEL_RE.match(text):
        return True
    if CAST_LINE_RE.match(text):
        return True
    if TITLE_LINE_RE.match(text):
        low = text.lower()
        if any(sep in low for sep in TITLE_LINE_SEPS):
            return True
    return False


def duet_hint(artists: Optional[Sequence[str]], n_singers: int,
              why: str = "声纹判定为单人演唱") -> str:
    """元数据有多位艺人、结果却只有一个声部时给出的可操作提示（没有这种情况返回空串）。

    为什么值得单独提示：Apple Music 的**左右分栏靠不同的 `ttm:agent` 触发**，
    全曲只有 v1 就一定不会分栏。而声纹分不开的对唱（音色接近、或第二个人只唱和声）
    是这个工具最容易让人"以为生成了对唱、其实没有"的地方。

    ⚠️ ``why`` 必须**照实说**是哪条路径得出的单声部：关闭声纹时根本没跑声纹，
    还写"声纹判定为单人演唱"就是在误导用户（真踩过）。
    """
    others = [a for a in (artists or []) if a]
    if n_singers > 1 or len(others) < 2:
        return ""
    return (f"⚠ 元数据里有 {len(others)} 位艺人（{'、'.join(others[:3])}），"
            f"但{why} → 全曲只有 v1，**Apple Music 不会出现左右分栏**。"
            f"如果确实是对唱：在歌词里给对应行加 [v1]/[v2] 标记（最可靠）、"
            f"或把「演唱者分段」设为强制双声部（CLI：--singer-mode 2）。")


def renumber_by_first_seen(labels: Sequence[int]) -> Dict[int, int]:
    """把 k-means 的**任意**簇编号重排成「首次出现顺序 → 1, 2, …」的序号（1 起）。

    为什么需要：簇编号只是聚类算法的产物，同一首歌重跑就可能把**先开口**的声部
    编成任意号。而 Apple Music 的对唱左右栏按声部首次出现的顺序排栏 —— 若 v2 唱在
    第一句，编辑台里「一声部」(v1) 的行就会显示到第二栏（用户实测：
    "选 1 声部显示成 2 声部，选 2 声部显示成 1 声部"）。

    返回 ``{旧簇号: 新序号}``；``labels`` 按**歌词行顺序**给，不能按时间排序后再传
    （行顺序才是"谁先唱"的判据）。
    """
    order: List[int] = []
    for lab in labels:
        if lab not in order:
            order.append(lab)
    return {lab: i + 1 for i, lab in enumerate(order)}


def build_overlap_evidence(ttml: str, lines, *, task_id: str = "") -> dict:
    """从**生成侧边标记**构造拖音证据（不写文件、不改任何状态）。

    数据来源：``tail_extend`` 在每行上写的 ``overlap_with_next`` / ``overlap_next_begin``
    （``pipeline`` 的 5.7 步）。只有**同 agent + 标记为真 + 实际重叠**的相邻对才入证据。

    返回 ``{"allowed_overlaps": [...], "evidence": dict | None}``：

    * ``allowed_overlaps``：给 ``check_ttml`` 的门控列表（格式自检用）
    * ``evidence``：给编辑台的 sidecar 内容（绑定**持久化后**指纹 + TTML SHA256）；
      无证据 / 行数对不上 / 无 task_id ⇒ ``None``（失败关闭）
    """
    import hashlib
    import json as _json
    from datetime import datetime, timezone

    from edit_api import edge_record_from_doc
    from ttml_model import LAYER_TRANSLATION
    from ttml_normalize import (EVIDENCE_ALGORITHM_VERSION, EVIDENCE_SCHEMA_VERSION,
                               overlap_evidence_fingerprint)
    from ttml_reader import parse_ttml

    out = {"allowed_overlaps": [], "evidence": None}
    try:
        doc = parse_ttml(ttml)
    except Exception:
        return out
    if len(doc.lines) != len(lines):
        return out                     # 行数对不上 ⇒ 不猜（失败关闭）

    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        a_doc, b_doc = doc.lines[i], doc.lines[i + 1]
        if not getattr(a, "overlap_with_next", False):
            continue
        if (a.agent or "") != (b.agent or ""):
            continue                   # R4 只对同 agent 有意义
        if a_doc.end <= b_doc.begin + 1e-6:
            continue                   # 实际没有重叠（标记陈旧）
        main_end = max((w.end for w in a_doc.words
                        if not getattr(w, "is_bg", False) and w.layer != LAYER_TRANSLATION),
                       default=None)
        if main_end is None:
            continue
        out["allowed_overlaps"].append({"next_line": i + 2, "agent": a_doc.agent or "",
                                        "prev_end": round(float(main_end), 3),
                                        "next_begin": round(float(b_doc.begin), 3)})

    if not out["allowed_overlaps"] or not task_id:
        return out
    edges = []
    for item in out["allowed_overlaps"]:
        idx = int(item["next_line"]) - 2
        try:
            edges.append(edge_record_from_doc(doc, idx))
        except Exception:
            continue
    if not edges:
        return out
    out["evidence"] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "algorithm": "tail_extend@s7b/opt-in",
        "algorithm_version": EVIDENCE_ALGORITHM_VERSION,
        "default_policy_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "doc": {"identity": {"task_id": task_id},
                "fingerprint_persisted": overlap_evidence_fingerprint(doc),
                "ttml_sha256": hashlib.sha256(ttml.encode("utf-8")).hexdigest(),
                "line_count": len(doc.lines)},
        "edges": edges,
    }
    return out


@dataclass
class ConvertOptions:
    mode: str = "amll"              # amll / apple
    language: str = "zh"            # zh / en / ja / ko ...
    model: str = "large-v3"
    device: str = "auto"
    # 对齐引擎：whisper=默认（faster-whisper + DP，支持纯文本）；
    # timing-tifa=实验（TIFA 歌声音素对齐，需要 models/tifa + 逐句 LRC）。
    # 失败时自动回退 whisper（见 convert() 的 1.5 步）。
    align_engine: str = "whisper"
    refine: bool = True             # 低匹配率行二次精修
    refine_threshold: float = 0.6   # 匹配率低于该值触发精修
    max_refine_lines: int = 12      # 单次转换最多精修多少行（控制耗时）
    skip_credits: bool = True       # 跳过"作词/作曲"等制作人员行
    beam_size: int = 5
    pause_space: float = 0.0        # >0 时在明显停顿处插入空格
    chunked: bool = True            # 有 LRC 锚点时按乐句切片识别（精度更高）
    chunk_max_seconds: float = 22.0
    chunk_max_lines: int = 10
    chunk_gap: float = 2.5          # 行间隔大于该值就断开成新的乐句
    chunk_max_span: float = 7.0     # 相邻两行**开始时间**相差超过该值也断开（中间多半是间奏）
    markers: str = "agent"          # agent / role / both：演唱者与 Apple 角色标记怎么写
    line_id: str = "both"           # itunes:key / xml:id / both
    translation_style: str = "head"  # head（AMLL 规范首选）/ inline / both
    time_format: str = "clock"      # clock=HH:MM:SS.mmm（W3C 合法）/ mmss=MM:SS.mmm / seconds=SS.mmm
    song_part: str = ""             # div 上的 itunes:songPart（留空则不写）
    # 演唱者分段（CAM++ 声纹）。**音频判不出到底几个人**（三种判据全部失败，见 diarize.py），
    # 所以不再假装"自动判断人数"，改成让用户明确选：
    #   off = 不跑声纹，直接用歌词里的 [v1]/[v2] 标记（没标记就全 v1）
    #   1/2/3 = **强制 N 个声部**，跑 k-means 聚成 N 簇（覆盖歌词标记）
    singer_mode: str = "off"
    # 兼容旧参数（--auto-agents / --split-singers）：singer_mode 为 off 时才生效
    auto_agents: bool = False       # 【旧】等价于 singer_mode="2"
    split_singers: int = 0          # 【旧】>=2 等价于 singer_mode=str(N)
    style: str = "keep"             # keep=一字不改；apple=按 Apple 歌词风格准则规范化
    explicit_policy: str = "keep"   # keep=照实抄录；mask=屏蔽词星号化（儿童适宜版本）
    detect_muted: bool = False      # 检测被消音的词并用 **** 替换
    agent_from: str = ""            # 参考 TTML：从里面读每行演唱者归属（人工标注复用）
    verify_amll: bool = True        # 生成后用真 AMLL 解析器验一遍"播放器里能不能显示"
    diarize_on: str = "mix"         # 声纹用哪条音轨：mix=原始混音（默认，实测准得多）/ vocals=分离轨
    separate_vocals: bool = False
    allow_same_agent_overlap: bool = False   # 实验：允许有证据的同歌手跨行重叠（默认关闭）
    # 分层歌词：允许同 agent 的 <p> 时间重叠；p 只包主唱，x-bg 可伸到 p 外。
    # 对应真实 Apple/AMLL 的“主歌词/和声/附加句同时出现”，默认关闭。
    layered_lyrics: bool = False
    extend_tails: bool = True       # 句尾拖音补全（用分离后人声轨的能量接回被切掉的尾音）
    demucs_model: str = "htdemucs"  # htdemucs / htdemucs_ft / htdemucs_6s / mdx_extra
    # ⚠️ Worker 分离的超时**不做成 ConvertOptions 字段**（2026-10-15）：
    # 它是内部实现细节，不是用户选项。做成字段就必须同步进
    # desktop/services/convert_params.build_options()，否则
    # desktop/selftest.py 第 8 组的 field_drift 会报"设了不生效" ——
    # 我第一版正是这么被守卫抓到的。这里用模块常量 SEP_TIMEOUT_S。

    # ------------------------------------------------------------------
    def forced_singers(self) -> int:
        """解析出"强制几个声部"；0 = 关闭（用歌词标记）。

        ``singer_mode`` 是新的正式选项（off/1/2/3）；为兼容旧的
        ``--auto-agents`` / ``--split-singers``，只有在 ``singer_mode`` 没给
        有效值时才回退到旧参数。
        """
        m = str(self.singer_mode or "").strip().lower()
        if m in ("off", "none", "0"):
            return 0
        if m.isdigit():
            return max(1, int(m))
        # ---- 兼容旧参数 ----
        if int(self.split_singers or 0) >= 2:
            return int(self.split_singers)
        if self.auto_agents:
            return 2
        return 0


# 独立 Worker 分离的超时（秒）：超时会**杀掉 Worker 并清理临时输出**，
# 然后如实报告「分离失败（超时）」并跳过依赖人声轨的尾音延长 —— 不伪造结果。
SEP_TIMEOUT_S = 1800.0


def vocals_cache_path(audio_path: str, model_name: str,
                      out_dir: Optional[str] = None) -> str:
    """人声分离结果的缓存路径。

    ⚠️ 文件名**必须带上 demucs 模型名**：缓存复用的判据是"文件存在且比音频新"，
    它只看得到文件名。若不同模型的产物共用 `{歌名}.vocals.wav`，那么换了
    `--demucs-model` 之后会**静默复用**上一个模型切出来的人声轨 ——
    用户以为换了模型（比如从 htdemucs 换成更准的 htdemucs_ft），
    实际听到的还是旧结果，且没有任何提示。
    """
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", stem)[:60] or "audio"
    model_safe = re.sub(r"[^\w.-]+", "_", str(model_name or "demucs"))[:24] or "demucs"
    if out_dir:
        base = out_dir
    elif getattr(sys, "frozen", False):
        # ⚠️ 冻结后 `__file__` 指向 `_internal\pipeline.py` —— 用它会算成
        # `_internal\outputs`，与用户看到的 `outputs\` 不是同一个目录，导致
        # "缓存明明在、却永远不复用"（winui 后端 / lite 包实测都踩过）。
        base = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "outputs")
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    return os.path.join(base, f"{safe}.{model_safe}.vocals.wav")


@dataclass
class ConvertResult:
    ttml: str
    check: CheckResult
    report: AlignReport
    duration: Optional[float]
    notes: List[str] = field(default_factory=list)
    elapsed: float = 0.0
    output_path: Optional[str] = None
    asr_words: List[AsrWord] = field(default_factory=list)


class LyricToTTML:
    def __init__(self, options: Optional[ConvertOptions] = None, log: Callable[[str], None] = print):
        self.opt = options or ConvertOptions()
        self.log = log
        self._asr: Optional[WhisperASR] = None
        self._ref_agent_types: Dict[str, str] = {}

    # ------------------------------------------------------------------ ASR
    @property
    def asr(self) -> WhisperASR:
        if self._asr is None:
            self._asr = WhisperASR(
                model=self.opt.model,
                device=self.opt.device,
                log=self.log,
            )
        return self._asr

    # -------------------------------------------------------------- 主流程
    def convert(
        self,
        audio_path: str,
        lyrics_text: str,
        song_title: str = "",
        artists: Optional[Sequence[str]] = None,
        album: str = "",
        author: str = "",
        author_id: str = "",
        platform_ids: Optional[Dict[str, str]] = None,
        output_path: Optional[str] = None,
        translation_text: str = "",
        translation_lang: str = "",
        songwriters: Optional[Sequence[str]] = None,
    ) -> ConvertResult:
        t_start = time.time()
        snapshot.init()   # 只读诊断开关：未设 L2T_SNAPSHOT_DIR 时立即返回 False，零行为差异
        notes: List[str] = []
        artists = list(artists or [])

        if not audio_path or not os.path.exists(audio_path):
            raise FileNotFoundError(f"找不到音频文件: {audio_path}")

        # 1. 解析歌词 ------------------------------------------------------
        self.log("[1/6] 解析歌词")
        lines = parse_lyrics(lyrics_text)
        lines = detect_agent_switch(lines)
        if self.opt.skip_credits:
            before = len(lines)
            lines = [l for l in lines if not _is_credit_line(l)]
            if len(lines) != before:
                self.log(f"      跳过 {before - len(lines)} 行制作人员/标题信息")
        if not lines:
            raise ValueError("歌词解析失败：没有有效歌词行")

        # 1.5 Apple 歌词风格准则规范化（可选，全部改动都会列出来）----------
        style_report = self._apply_style(lines)
        if style_report.changes:
            self.log(f"      风格规范化：{style_report.summary()}")
            for d in style_report.detail(6):
                self.log(f"        {d}")
            notes.append(style_report.summary())
        for n in style_report.notes:
            notes.append(n)

        anchored = any(l.line_start is not None for l in lines)
        self.log(f"      {len(lines)} 行歌词（{'含 LRC 行级时间戳' if anchored else '纯文本'}）")
        if not anchored:
            notes.append("输入为纯文本歌词：所有行时间由音频推断，建议提供 LRC 以获得更高精度。")

        duration = audio_duration(audio_path)

        # 1.5 实验引擎：TIFA 歌声音素对齐（需要逐句 LRC + models/tifa）----------
        # 失败/缺模型/纯文本 → 记一条 note，回退到下面的 whisper 主流程。
        if str(self.opt.align_engine or "whisper").strip().lower() in (
                "timing-tifa", "timing", "tifa"):
            self.log("[1.5/6] 尝试 TIFA 实验引擎（歌声音素对齐）")
            try:
                import timing_align

                tifa_audio = audio_path
                sep_path = None
                if self.opt.separate_vocals:
                    sep_path = self._separate_vocals(audio_path, song_title, notes)
                    if sep_path:
                        tifa_audio = sep_path
                        self.log("[1.5/6] TIFA 使用分离后的人声轨")

                # 和声（x-bg）：TIFA 是单序列强制对齐，叠唱时会把和声字
                # 混进主唱轨迹；Whisper 的 ASR/DP 对叠唱更稳。检测到歌词里有
                # 背景层时，先跑一遍 Whisper，仅取其 bg 单元的时间。
                hybrid_bg = None
                auto_asr_words = []
                has_bg = any(getattr(t, "is_background", False)
                             for l in lines for t in (getattr(l, "tokens", None) or []))
                auto_harmony = (os.environ.get("L2T_AUTO_HARMONY", "1").strip().lower()
                                not in ("0", "false", "no", "off"))
                if has_bg or auto_harmony:
                    self.log("[1.5/6] 和声层：跑 Whisper 识别"
                             + ("（已有和声标记，只取 x-bg 时间）" if has_bg
                                else "（自动转录和声文本，实验）"))
                    try:
                        from dataclasses import replace
                        import tempfile, shutil as _sh
                        _tmpdir = tempfile.mkdtemp(
                            prefix="l2t-hybrid-",
                            dir=os.path.dirname(os.path.abspath(output_path or audio_path)))
                        try:
                            _wopt = replace(
                                self.opt, align_engine="whisper",
                                # 自动转录和声时**不能用歌词提示的逐句切片**：
                                # prompt 会把 ASR 拉回主唱歌词，叠唱部分直接消失。
                                # 也不要用 Demucs 单轨：原混音里的叠唱更完整。
                                chunked=(self.opt.chunked if has_bg else False),
                                refine=(self.opt.refine if has_bg else False),
                                separate_vocals=self.opt.separate_vocals)
                            _wr = LyricToTTML(_wopt, log=lambda m: None)
                            _res = _wr.convert(
                                audio_path=audio_path, lyrics_text=lyrics_text,
                                song_title=song_title, artists=artists, album=album,
                                author=author, author_id=author_id,
                                platform_ids=platform_ids,
                                output_path=os.path.join(_tmpdir, "whisper-bg.ttml"),
                                translation_text=translation_text,
                                translation_lang=translation_lang,
                                songwriters=songwriters)
                            hybrid_bg = _res.report
                            auto_asr_words = list(_res.asr_words or [])
                            self.log(f"      Whisper 原始词 {len(auto_asr_words)} 个"
                                     f"（chunked={getattr(_wopt, 'chunked', None)}）")
                            notes.append("和声层：TIFA 主唱 + Whisper 叠唱识别")
                        finally:
                            _sh.rmtree(_tmpdir, ignore_errors=True)
                    except Exception as e:                  # noqa: BLE001
                        self.log(f"      Whisper 和声对齐失败（{type(e).__name__}: {e}），"
                                 f"和声仍用 TIFA")
                        notes.append(f"和声层 Whisper 对齐失败：{e}")

                # TIFA 结果之后再跑声纹/歌词标记（写 TTML 之前注入，保证 agent 进文件）
                def _tifa_post(report: AlignReport) -> None:
                    forced = self.opt.forced_singers()
                    if forced:
                        self._split_singers(lines, report, audio_path, notes,
                                            artists, forced)
                    else:
                        self._use_lyric_markers(lines, report, notes, artists)
                    if self.opt.extend_tails and sep_path:
                        # TIFA 自家音素边界已经是真实发音终点；Whisper 时代
                        # 的“能量/基频续尾”启发式会把准确尾音再拉长。
                        # 用户实测 TIFA 更准 ⇒ TIFA 分支默认不做尾音补全。
                        notes.append("TIFA 自带音素级边界，已跳过 Whisper 的尾音补全启发式")

                r = timing_align.align_lines(
                    lyrics_text, tifa_audio, output_path or "",
                    title=song_title, artists=artists, album=album,
                    author=author, author_id=author_id,
                    platform_ids=platform_ids,
                    translation_text=translation_text,
                    translation_lang=translation_lang,
                    songwriters=songwriters,
                    mode=self.opt.mode, markers=self.opt.markers,
                    line_id=self.opt.line_id,
                    translation_style=self.opt.translation_style,
                    time_format=self.opt.time_format,
                    song_part=self.opt.song_part,
                    keep_credits=not self.opt.skip_credits,
                    language=self.opt.language,
                    crop_vocals=bool(sep_path),
                    layered=self.opt.layered_lyrics,
                    bg_report=hybrid_bg,
                    asr_words=auto_asr_words,
                    postprocess=_tifa_post,
                    log=self.log)
                notes.extend(r.get("notes") or [])
                if translation_text:
                    notes.append("TIFA 实验引擎暂不写翻译层（已忽略 --translation）")
                self.log(f"[1.5/6] TIFA 完成：{r['elapsed']:.1f}s，"
                         f"字级映射 {r['report'].match_rate:.1%}")
                return ConvertResult(
                    ttml=r["ttml"], check=r["check"], report=r["report"],
                    duration=r.get("duration") or duration, notes=notes,
                    elapsed=time.time() - t_start,
                    output_path=output_path)
            except Exception as e:                      # noqa: BLE001
                msg = f"TIFA 引擎不可用（{type(e).__name__}: {e}），已自动回退 whisper"
                self.log("      " + msg)
                notes.append(msg)
        if duration:
            self.log(f"      音频时长 {duration:.1f}s")

        # 1.8 人声分离（可选）：后续识别/声纹都用干净的人声轨 ----------------
        work_audio = audio_path
        # ⚠️ "为什么没有干净人声轨"——**照实记下来**，留给 5.7 的提示用。
        self._sep_reason = "没开「先做人声分离」"
        if self.opt.separate_vocals:
            vocals = self._separate_vocals(audio_path, song_title, notes)
            if vocals:
                work_audio = vocals
                self._sep_reason = ""

        # 2. 语音识别 ------------------------------------------------------
        lang = whisper_language_code(self.opt.language)
        if anchored and self.opt.chunked:
            self.log("[2/6] 语音识别（按乐句切片，逐句用歌词本身做提示词）")
            t0 = time.time()
            words, chunks = self._transcribe_chunked(lines, work_audio)
            self.log(f"      识别到 {len(words)} 个词/字，{chunks} 个乐句，用时 {time.time() - t0:.1f}s")
        else:
            self.log("[2/6] 语音识别（faster-whisper，整曲词级时间戳）")
            prompt = self._build_prompt(song_title, artists, lines)
            t0 = time.time()
            words = self.asr.transcribe(
                work_audio,
                language=lang,
                initial_prompt=prompt,
                beam_size=self.opt.beam_size,
                vad_filter=False,
            )
            self.log(f"      识别到 {len(words)} 个词/字，用时 {time.time() - t0:.1f}s")
        snapshot.dump_words("asr_raw", words, source="pipeline.py",
                            function="AsrEngine.transcribe", note="ASR 原始输出（未加工）")
        if not words:
            raise RuntimeError("语音识别没有得到任何结果，请检查音频是否为纯音乐/无人声")

        # 3. 强制对齐 ------------------------------------------------------
        self.log("[3/6] 动态规划强制对齐")
        matcher = LyricMatcher(log=self.log)
        report = matcher.align(lines, words, duration)
        snapshot.dump_lines("align_dp", report.lines, source="pipeline.py",
                            function="LyricMatcher.align", note="DP 强制对齐 + 行内后处理后的结果")

        # 4. 低匹配率行精修 -------------------------------------------------
        # 有第二条音轨时（分离人声）把阈值抬高：只要不是近乎满分，都让另一条轨复核一遍，
        # 因为实测两条轨各有胜负（有的行混音更准、有的行人声轨更准）
        if self.opt.refine and anchored:
            alt = audio_path if work_audio != audio_path else None
            thr = max(self.opt.refine_threshold, 0.95) if alt else self.opt.refine_threshold
            report = self._refine_lines(matcher, lines, report, work_audio,
                                        alt_audio=alt, threshold=thr)
        else:
            matcher._fix_cross_line_overlaps(report.lines)
            matcher._clamp_units_into_lines(report.lines, layered=self.opt.layered_lyrics)

        # 4.5 丢掉开头那些"一个字都没对上"的行（多半是漏网的制作人员行）------
        if self.opt.skip_credits:
            dropped = self._drop_leading_unmatched(report)
            if dropped:
                self.log(f"      再跳过 {dropped} 行开头没唱出来的信息行")
        for w in report.warnings[:10]:
            notes.append(w)

        # 5. 文本后处理（停顿空格）------------------------------------------
        if self.opt.pause_space > 0:
            self._apply_pause_spaces(report.lines)

        # 5.5 演唱者归属 -----------------------------------------------------
        # 优先用参考 TTML 里的人工标注（最可靠）；否则按用户选的模式：
        #   关闭（0）→ 用歌词里的 [v1]/[v2] 标记（preprocess 已经写进 lines）
        #   强制 N   → CAM++ 声纹按 N 簇划分
        forced_n = self.opt.forced_singers()
        if self.opt.agent_from:
            self.log(f"[分段] 套用参考文件的演唱者归属：{os.path.basename(self.opt.agent_from)}")
            self._apply_agents_from_reference(report, self.opt.agent_from, notes)
            if self._ref_agent_types:
                self.log("      同时继承参考文件的 agent 类型："
                         + "，".join(f"{k}={v}" for k, v in self._ref_agent_types.items()))
        elif forced_n:
            # ⚠️ 声纹必须用**原始混音**，不能用 Demucs 分离出的人声轨：
            # 实测《在出发之前》拿人工真值当基准，原始混音 93.9%、Demucs 人声轨 54.5%
            # （=瞎猜）。Demucs 为了分离人声会把共振峰细节平滑掉，而那是身份线索。
            diar_audio = audio_path if self.opt.diarize_on == "mix" else work_audio
            if diar_audio != work_audio:
                self.log("[分段] 声纹用原始混音（分离轨会破坏音色线索）")
            self._split_singers(lines, report, diar_audio, notes, artists, forced_n)
        else:
            self._use_lyric_markers(lines, report, notes, artists)

        # 5.6 消音词检测（Apple：被屏蔽的词用星号替换）----------------------
        if self.opt.detect_muted:
            self._detect_muted(report, work_audio, notes)

        # 5.7 句尾拖音补全 ---------------------------------------------------
        # 分句时每行的结束被无条件卡在下一行开始，歌手拖长音时尾字被硬切。
        # 实测 4 首歌 218 行里 51 处（24%）卡在下一行开始，其中 73% 的边界上
        # 人声能量根本没下降 —— 声音是连着的，被切了。这里用能量把它接回去。
        # ⚠️ 必须在**人声轨**上量：原始混音里间奏照样有能量，会把尾音延到间奏结束。
        if self.opt.extend_tails:
            if work_audio != audio_path:
                tail_audio = self._load_audio_for_tail(work_audio)
                # 起音对齐（管行首）放在尾音补全（管行尾）**之前**：
                # 先把行首拉准，再按拉准后的位置量尾音。
                snaps = onset_align.snap_line_starts(report.lines, tail_audio,
                                                     log=self.log)
                snapshot.dump_lines("onset_align", report.lines, source="pipeline.py",
                                    function="onset_align.snap_line_starts", note="首字起音对齐之后")
                if snaps:
                    notes.append("首字起音对齐：" + onset_align.summarize(snaps))
                fixes = tail_extend.extend_line_tails(report.lines, tail_audio,
                                                      allow_same_agent_overlap=self.opt.allow_same_agent_overlap,
                                                      log=self.log)
                snapshot.dump_lines("tail_extend", report.lines, source="pipeline.py",
                                    function="tail_extend.extend_line_tails", note="句尾拖音补全之后")
                if fixes:
                    notes.append("句尾拖音补全：" + tail_extend.summarize(fixes))
                else:
                    self.log("[尾音] 没有检测到需要补的拖音")
            else:
                # ⚠️ **原因必须照实说**（用户 2026-10-15 实测踩到）：
                # 原来一律写"没开 --separate-vocals"，可用户**明明勾了** ——
                # 真实原因是分离没跑起来（打包版没内置 demucs / 分离失败）。
                # 误导性提示比没有提示更糟：用户会反复去点那个已经勾上的开关。
                why = getattr(self, "_sep_reason", "") or "人声分离没有生效"
                self.log(f"[尾音/起音] 跳过：{why}，"
                         f"没有干净人声轨就没法可靠区分人声与伴奏")
                notes.append(f"句尾拖音补全与首字起音对齐已跳过（{why}）")
                snapshot.dump_lines("onset_align", report.lines, source="pipeline.py",
                                    function="onset_align.snap_line_starts", executed=False,
                                    skip_reason=why, note="未执行（无干净人声轨）")
                snapshot.dump_lines("tail_extend", report.lines, source="pipeline.py",
                                    function="tail_extend.extend_line_tails", executed=False,
                                    skip_reason=why, note="未执行（无干净人声轨）")

        # 5.8 同歌手行不许重叠（铁律 4）--------------------------------------
        # 放在尾音补全**之后**：补全本身已按"同歌手不越过下一行"设了上限，
        # 但对齐原本就可能产出重叠（实测《让风告诉你》L35/L36），这里是兜底。
        # 分层歌词（layered_lyrics）允许同时出现，不做这一步压缩。
        if not self.opt.layered_lyrics:
            self._enforce_agent_monotonic(report.lines, notes)
            snapshot.dump_lines("agent_monotonic", report.lines, source="pipeline.py",
                                function="Pipeline._enforce_agent_monotonic", note="同歌手去重叠之后")

        # 6. 输出 + 自检 ---------------------------------------------------
        if translation_text:
            n = self._attach_translations(report.lines, translation_text)
            where = {"head": "头部 iTunesMetadata", "inline": "行内 x-translation",
                     "both": "头部 + 行内"}[self.opt.translation_style]
            notes.append(f"已附加 {n} 行翻译（{where}）")

        roles = sorted({l.role for l in report.lines if l.role})
        if roles:
            notes.append("Apple 角色标记：" + ", ".join(roles))

        self.log("[5/6] 生成 TTML")
        writer = TTMLWriter(
            mode=self.opt.mode,
            markers=self.opt.markers,
            line_id=self.opt.line_id,
            translation_style=self.opt.translation_style,
            time_format=self.opt.time_format,
            song_part=self.opt.song_part,
            inline_translation_lang=translation_lang or self._ttml_lang(),
            agent_types=getattr(self, "_ref_agent_types", None),
            layered=self.opt.layered_lyrics,
        )
        snapshot.dump_lines("final_write", report.lines, source="pipeline.py",
                            function="TTMLWriter.write", note="写入 TTML 前的最终值")
        ttml = writer.write(
            report.lines,
            title=song_title,
            artists=artists,
            album=album,
            language=self._ttml_lang(),
            platform_ids=platform_ids,
            author=author,
            author_id=author_id,
            duration=duration,
            translation_lang=translation_lang or self._ttml_lang(),
            songwriters=songwriters,
        )

        # 生成侧拖音证据：从 5.7 步的边标记构造（无标记 ⇒ 空，行为完全不变）
        _tid = ""
        if output_path:
            _tid = "".join(c for c in os.path.splitext(os.path.basename(output_path))[0]
                           if c.isalnum() or c in "-_")
        overlap_bundle = build_overlap_evidence(ttml, report.lines, task_id=_tid)
        if overlap_bundle["allowed_overlaps"]:
            self.log(f"      拖音证据：{len(overlap_bundle['allowed_overlaps'])} 条同歌手跨行重叠边"
                     f"（格式自检按证据允许，仍以 warning 可见）")

        self.log("[6/6] 格式与时间轴自检")
        # 只有 clock(HH:MM:SS.mmm) 是 W3C TTML1 合法写法；mmss/seconds 是 AMLL 宽松写法，
        # 用户显式选择时按宽松模式校验（降级为警告），避免自检把故意的格式判成错误
        check = check_ttml(ttml, strict=((self.opt.time_format == "clock")
                                         and self.opt.mode != "apple-internal"),
                           allowed_overlaps=overlap_bundle["allowed_overlaps"],
                           layered=bool(self.opt.layered_lyrics
                                        or self.opt.mode == "apple-internal"),
                           apple_internal=(self.opt.mode == "apple-internal"))
        self.log("      " + check.summary())
        if not check.ok:
            for e in check.errors[:5]:
                self.log(f"      ✗ [{e.where}] {e.issue}")

        # 6.5 用**真正的 AMLL 解析器**再验一遍"播放器里能不能显示" --------------
        # 我们自己的校验器只查规范；"整行被当成一个词、逐字全丢、播放器空白"
        # 这类语义问题它查不出来（真实踩过）。解析器不可用时静默跳过。
        if self.opt.verify_amll:
            try:
                import sys as _sys
                _tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
                if _tools not in _sys.path:
                    _sys.path.insert(0, _tools)
                from verify_amll_parse import parse_check
                r = parse_check(ttml)
                if r is None:
                    # ⚠️ 说清**到底缺什么**：打包版以前一律说"未准备 AMLL 解析器"，
                    # 而真实原因可能是机器上没 node。解析器本体自 2026-10-15 起
                    # 随包分发，所以"没准备好"基本只剩"没有 node"这一种。
                    import shutil as _sh
                    why = ("机器上没有 node（装 Node.js 后即可自动做这个检查）"
                           if not _sh.which("node")
                           else "解析器没跑起来（可跑 tools/verify_amll_parse.py --setup）")
                    notes.append(f"（跳过「播放器能不能显示」检查：{why}）")
                elif r.get("ok"):
                    self.log(f"      AMLL 解析器：行 {r['lines']} / 字 {r['words']}"
                             f"（平均 {r['perLine']:.1f} 字每行），逐字完整 ✓")
                    notes.append(f"AMLL 解析器验证通过：{r['lines']} 行 "
                                 f"{r['words']} 字，agent 分布 {r.get('byAgent')}")
                else:
                    msg = (f"⚠ AMLL 解析器判定**播放器里会显示异常**："
                           f"行 {r.get('lines')} / 字 {r.get('words')}"
                           f"（平均 {r.get('perLine', 0):.1f} 字每行）"
                           f"，没有 words 的行 {r.get('emptyLines')}"
                           f"{'，逐字时间轴疑似丢失' if r.get('suspicious') else ''}")
                    self.log("      " + msg)
                    notes.append(msg)
                    check.warnings.append(CheckIssue("warning", "AMLL 解析器", msg))
            except Exception as e:                      # 绝不能因为附加检查把主流程搞挂
                self.log(f"      （AMLL 解析器检查跳过：{type(e).__name__}）")

        if output_path:
            with open(output_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(ttml)
            self.log(f"      已保存: {output_path}")
            if overlap_bundle.get("evidence"):
                from edit_api import atomic_write_text
                side = os.path.splitext(output_path)[0] + ".overlap.json"
                atomic_write_text(side, json.dumps(overlap_bundle["evidence"],
                                                   ensure_ascii=False, indent=1))
                notes.append(f"拖音证据已写入 {os.path.basename(side)}"
                             f"（{len(overlap_bundle['evidence']['edges'])} 条边；"
                             f"编辑台保存时会据此保住长尾，证据不符则自动回退）")

        notes.append(f"字级匹配率 {report.match_rate:.1%}（匹配 {report.matched_units}/{report.total_units}）")
        if report.match_rate < 0.75:
            notes.append("匹配率偏低：请确认歌词文本与音频版本一致；使用带行级时间戳的 LRC 可显著提升精度。")

        return ConvertResult(
            ttml=ttml,
            check=check,
            report=report,
            duration=duration,
            notes=notes,
            elapsed=time.time() - t_start,
            output_path=output_path,
            asr_words=words,
        )

    def _enforce_agent_monotonic(self, lines: Sequence[AlignedLine],
                                 notes: List[str]) -> int:
        """保证**同一演唱者的相邻行不重叠**（TTML 铁律 4）。

        为什么要这一步：对齐是按行独立做的，某行的 ASR 字时间可能越过下一行的
        开始时间。实测《让风告诉你》出现过 L35(v1) 结束 114.570 而 L36(v1)
        开始 114.300 的重叠 —— 这是**格式错误**（`ttml_check` 会报）。
        不同演唱者之间的重叠是允许的（双人叠唱），这里只压同 agent 的。

        ⚠️ **必须按 agent 追踪"上一个同 agent 的行"，不能只比相邻条目**：
        叠唱时时间线上常常是 v1/v2 交错（L34 v2 → L35 v1 → L36 v1 → L37 v2），
        两个 v2 中间隔着别的行，只比相邻就漏掉（真踩过：L34 v2 拖到 124.650，
        L37 v2 从 114.530 开始，报"同一演唱者 v2 行时间重叠"）。
        """
        fixed = 0
        last_of: Dict[str, Tuple[int, float]] = {}      # agent -> (行号, 结束时间)
        for i, line in enumerate(lines):
            ag = line.agent or ""
            prev = last_of.get(ag)
            edge_ok = (prev is not None and prev[0] == i - 1
                       and getattr(lines[prev[0]], "overlap_with_next", False))
            if prev is not None and prev[1] > line.begin and not edge_ok:
                j, prev_end = prev
                a, b = lines[j], line
                limit = float(b.begin)
                span_old, span_new = a.end - a.begin, limit - a.begin
                if span_old > 0 and span_new > 0:
                    # **整行按比例缩放**到 [begin, limit]。
                    # ⚠️ 不能逐字 `end = min(end, limit)`：多个越界的字会全落到同一个
                    # limit，而它们的 start 还保持原顺序 → 立刻"字时间重叠"（真踩过：
                    # 压完报 4 个"字时间重叠: 发 与 么 / 么 与 了 …"）。
                    scale = span_new / span_old
                    for u in (a.units or []):
                        if u.start is not None:
                            u.start = a.begin + (u.start - a.begin) * scale
                        if u.end is not None:
                            u.end = a.begin + (u.end - a.begin) * scale
                    a.end = limit
                    fixed += 1
                    self.log(f"　　[重叠] L{j + 1}「{(a.text or '')[-6:]}」行尾压到 "
                             f"{limit:.3f}（同歌手 L{i + 1} 从这里开始）")
            last_of[ag] = (i, line.end)
        if fixed:
            notes.append(f"修正同歌手行重叠 {fixed} 处（TTML 不允许同 agent 的 <p> 重叠）")
        return fixed

    # ------------------------------------------------------------ 内部实现
    _tail_audio_cache: dict = {}

    def _load_audio_for_tail(self, path: str):
        """读人声轨（16k 单声道），只解一次。"""
        key = os.path.abspath(path)
        if key not in self._tail_audio_cache:
            from faster_whisper.audio import decode_audio
            self._tail_audio_cache[key] = decode_audio(path, sampling_rate=16000)
        return self._tail_audio_cache[key]

    def _line_span(self, lines: Sequence[LyricLine], idx: int) -> tuple:
        """返回第 idx 行的 (开始, 合理演唱结束时间)。

        LRC 的行结束时间经常等于下一行的开始时间（中间可能是一大段间奏），
        这里按字数估一个上限，避免把整段间奏送进识别。
        """
        line = lines[idx]
        snapshot.record(stage="line_span_clip", line_id="L" + str(idx + 1), span_id=None,
                        text=line.text, executed=True,
                        note="行尾约束输入（_line_span 入口；返回值见 align_dp 阶段）",
                        source_file="pipeline.py", function="Pipeline._line_span")
        lo = float(line.line_start)
        nxt = lines[idx + 1].line_start if idx + 1 < len(lines) else None
        n_chars = sum(1 for ch in line.text if not ch.isspace())
        est = max(1.4, 0.75 * n_chars + 1.0)

        # 合并过背景和声的行**不能**按"下一行开始"截断：
        # LRC 里和声与下一句常常是交错标注的（和声 [03:32.87] 与下一句 [03:33.09]
        # 只差 0.2 秒），真人和声会一直唱到下一句开始之后。按下一行截断的话，
        # 那几个和声字会被硬挤进 0.2 秒里，最后变成时间戳重叠的坏文件。
        if any(getattr(t, "is_background", False) for t in line.tokens):
            hard_end = line.line_end if (line.line_end and line.line_end > lo) else lo + est
            snapshot.record(stage="line_span_clip_out", line_id="L" + str(idx + 1), span_id=None,
                            text=line.text, begin_after=lo, end_after=max(hard_end, lo + est),
                            duration_after=round(max(hard_end, lo + est) - lo, 3),
                            reason="背景/和声行分支：不被下一行截断",
                            source_file="pipeline.py", function="Pipeline._line_span")
            return lo, max(hard_end, lo + est)

        hard_end = line.line_end if line.line_end and line.line_end > lo else None
        if hard_end is None:
            hard_end = nxt if nxt and nxt > lo else lo + 8.0
        if nxt and nxt > lo:
            hard_end = min(hard_end, nxt)
        snapshot.record(stage="line_span_clip_out", line_id="L" + str(idx + 1), span_id=None,
                        text=line.text, begin_after=lo, end_after=min(hard_end, lo + est),
                        duration_after=round(min(hard_end, lo + est) - lo, 3),
                        reason="普通行：按下一行开始/字数估上限截断",
                        source_file="pipeline.py", function="Pipeline._line_span")
        return lo, min(hard_end, lo + est)

    def _transcribe_chunked(self, lines: Sequence[LyricLine], audio_path: str):
        """把连续的几行合成一个乐句送进 whisper，显著提高"提示词约束"的强度。

        实测：整曲一次识别会把短句（尤其背景和声）整段漏掉；
        而按乐句切片 + 用该句歌词做 initial_prompt，字级时间轴几乎完全正确。
        """
        audio = self.asr.load_audio(audio_path)
        lang = whisper_language_code(self.opt.language)
        anchors = [self._line_span(lines, i) for i in range(len(lines))]

        chunks: List[List[int]] = []
        current: List[int] = []
        for i, (lo, hi) in enumerate(anchors):
            if current:
                prev_hi = anchors[current[-1]][1]
                too_far = lo - prev_hi > self.opt.chunk_gap
                # 两行开始时间隔得太远 → 中间多半是纯间奏，切开识别更稳
                too_sparse = lo - anchors[current[-1]][0] > self.opt.chunk_max_span
                too_long = hi - anchors[current[0]][0] > self.opt.chunk_max_seconds
                too_many = len(current) >= self.opt.chunk_max_lines
                if too_far or too_sparse or too_long or too_many:
                    chunks.append(current)
                    current = []
            current.append(i)
        if current:
            chunks.append(current)

        all_words: List[AsrWord] = []
        for ci, chunk in enumerate(chunks, 1):
            lo = max(0.0, anchors[chunk[0]][0] - 0.3)
            hi = anchors[chunk[-1]][1] + 0.3
            if hi - lo < 0.4:
                hi = lo + 0.4
            # 提示词只放歌词本身。注意不要在开头加「。」之类的分隔符：
            # 实测（large-v3, CPU）提示词以标点开头时，whisper 会把整段歌词
            # 合并成几个长达 9 秒的"词"，字级时间轴直接失效。
            prompt = "\n".join(lines[k].text for k in chunk)[:500]
            expect_chars = sum(1 for k in chunk for ch in lines[k].text if not ch.isspace())
            self.log(f"      乐句 {ci}/{len(chunks)}：{lo:6.2f}s-{hi:6.2f}s  {lines[chunk[0]].text[:18]}…")
            got = self._transcribe_slice_safe(audio, lo, hi - lo, prompt, expect_chars, lang)
            all_words.extend(got)
        all_words.sort(key=lambda w: (w.start, w.end))
        return all_words, len(chunks)

    @staticmethod
    def _looks_degenerate(words: Sequence[AsrWord], expect_chars: int) -> bool:
        """识别结果是否明显崩了（词数太少 / 单个"词"长到不合理）"""
        if not words:
            return True
        if len(words) < max(3, int(0.35 * expect_chars)):
            return True
        dur = sum(w.end - w.start for w in words)
        return (dur / len(words)) > 1.2 or max(w.end - w.start for w in words) > 4.0

    def _transcribe_slice_safe(self, audio, lo: float, duration: float, prompt: str,
                               expect_chars: int, lang: str) -> List[AsrWord]:
        """切片识别 + 退化兜底：提示词失效时退回不带提示词的识别"""
        words = self.asr.transcribe_slice(audio, lo, duration, language=lang,
                                          initial_prompt=prompt, beam_size=self.opt.beam_size)
        if not self._looks_degenerate(words, expect_chars):
            return words
        alt = self.asr.transcribe_slice(audio, lo, duration, language=lang,
                                        initial_prompt=None, beam_size=self.opt.beam_size)
        if alt and (not self._looks_degenerate(alt, expect_chars) or len(alt) > len(words)):
            self.log("      ⚠ 该段识别结果异常，已改用无提示词重新识别")
            return alt
        return words


    def _build_prompt(self, title: str, artists: Sequence[str], lines: Sequence[LyricLine]) -> str:
        """用**很短**的上下文当 initial_prompt：把输出拉向简体中文、并给出专有名词。

        ⚠️⚠️ **绝不能把整份歌词（甚至前 6 行）灌进 prompt** —— 这是实测过的严重 bug：

        Whisper 的 ``initial_prompt`` 语义是"**已经转写过的上文**"，
        模型会**接着它往下写**。所以提示词里出现过的歌词，模型就认为已经转写完了、
        直接跳过。实测（26.6s 纯文本歌词，8 行）：

        | prompt | 识别结果 |
        | --- | --- |
        | 不带 prompt | **59 个单元**（全对） |
        | 前 6 行当 prompt（**旧实现**） | **16 个单元** —— 前 6 行整段丢失，只识别出最后 2 行 |
        | 只给第一句 | 59 个单元 ✓ |

        后果：字级匹配率掉到 27.4%，前几行还会被当成"没唱出来的信息行"丢掉。
        **纯文本歌词（没有 LRC）走的就是这条路径**，也就是最常见的用法。

        所以这里只保留 **标题 + 歌手 + 第一句**，并再兜一道长度上限。
        """
        parts = []
        if title:
            parts.append(title)
        if artists:
            parts.append("/".join(artists))
        first = next((l.text.strip() for l in lines if l.text.strip()), "")
        if first:
            parts.append(first)
        return " ".join(parts)[:80]

    def _refine_lines(self, matcher: LyricMatcher, lines: Sequence[LyricLine],
                      report: AlignReport, audio_path: str,
                      alt_audio: Optional[str] = None,
                      threshold: Optional[float] = None) -> AlignReport:
        """对匹配率低的行，单独重转写该行时间窗并重新对齐。

        ``alt_audio``：另一条音轨（分离人声时就是原始混音）。同一条弱行两条轨都会试，
        取匹配率更高的那个结果 —— 实测分离人声整体更好，但个别行反而是原轨更准。
        """
        audio = self.asr.load_audio(audio_path)
        alt = None
        if alt_audio and os.path.abspath(alt_audio) != os.path.abspath(audio_path):
            try:
                alt = self.asr.load_audio(alt_audio)
            except Exception:
                alt = None
        lang = whisper_language_code(self.opt.language)
        n = len(lines)
        refined = 0
        attempted = 0
        alt_wins = 0

        for idx, line in enumerate(lines):
            if attempted >= self.opt.max_refine_lines:
                self.log(f"      已达到单次精修上限（{self.opt.max_refine_lines} 行），其余行跳过")
                break
            al = report.lines[idx]
            if al.total == 0:
                continue
            if al.match_rate >= (threshold if threshold is not None else self.opt.refine_threshold):
                continue
            lo = line.line_start
            if lo is None:
                continue
            attempted += 1
            _lo, hi = self._line_span(lines, idx)
            win_lo = max(0.0, lo - 0.2)
            win_hi = hi + 0.2
            if win_hi - win_lo < 0.4:
                win_hi = win_lo + 0.4

            ctx = lines[idx - 1].text if idx > 0 else ""
            prompt = (ctx + "\n" + line.text).strip()
            n_chars = sum(1 for ch in line.text if not ch.isspace())
            best = al
            words2 = self._transcribe_slice_safe(audio, win_lo, win_hi - win_lo, prompt,
                                                 max(4, n_chars * 2), lang)
            if words2:
                cand = matcher.align_one_line(line, idx, words2, lo, hi, anchored=True)
                if cand.match_rate > best.match_rate:
                    best = cand
            if alt is not None:
                words3 = self._transcribe_slice_safe(alt, win_lo, win_hi - win_lo, prompt,
                                                     max(4, n_chars * 2), lang)
                if words3:
                    cand2 = matcher.align_one_line(line, idx, words3, lo, hi, anchored=True)
                    if cand2.match_rate > best.match_rate:
                        best = cand2
                        alt_wins += 1
            report.lines[idx] = best
            if best.match_rate > al.match_rate:
                refined += 1

        if refined:
            msg = f"      精修 {refined} 行成功（共尝试 {attempted} 行）"
            if alt_wins:
                msg += f"，其中 {alt_wins} 行改用原始混音更准"
            self.log(msg)
        matcher._fix_cross_line_overlaps(report.lines)
        matcher._clamp_units_into_lines(report.lines, layered=self.opt.layered_lyrics)
        report.matched_units = sum(l.matched for l in report.lines)
        report.total_units = sum(l.total for l in report.lines)
        # 精修可能改变了匹配率，重新生成提示
        report.warnings = [
            f"第 {i} 行匹配率偏低（{l.match_rate:.0%}）：{l.text[:20]}"
            for i, l in enumerate(report.lines, 1)
            if l.total and l.match_rate < 0.5
        ]
        return report

    def _attach_translations(self, lines: List[AlignedLine], text: str) -> int:
        """把翻译/音译文本按行号挂到对应行（输出为 ttm:role="x-translation"）"""
        trans = [t.strip() for t in text.replace("\r\n", "\n").split("\n")]
        trans = [t for t in trans if t]
        if not trans:
            return 0
        if len(trans) != len(lines):
            self.log(f"      ⚠ 翻译共 {len(trans)} 行，歌词 {len(lines)} 行，按顺序尽量对应")
        n = 0
        for line, tr in zip(lines, trans):
            line.translation = tr
            n += 1
        return n

    def _drop_leading_unmatched(self, report: AlignReport) -> int:
        """丢掉开头连续"完全没对上"的行。

        LRC 头部常有制作人员信息（``大提琴录制 xxx`` / ``绘 xxx``），关键词表不可能穷举完。
        但有个可靠的结构特征：**这些行根本没被唱出来**，所以字级匹配率是 0，
        而且都排在第一个正常行之前。
        """
        lines = report.lines
        first_ok = next((i for i, l in enumerate(lines) if l.total and l.match_rate >= 0.5), None)
        if first_ok is None or first_ok == 0:
            return 0
        keep = [l for i, l in enumerate(lines)
                if i >= first_ok or (l.total and l.match_rate > 0)]
        dropped = len(lines) - len(keep)
        if dropped:
            report.lines = keep
            report.matched_units = sum(l.matched for l in keep)
            report.total_units = sum(l.total for l in keep)
        return dropped

    # ------------------------------------------------------------ 人声分离
    def _worker_vocals(self, audio_path: str, out_path: str,
                       notes: List[str]) -> Optional[str]:
        """用**独立 Demucs Worker** 分离（打包版的主路径）。

        ⚠️ 主程序**绝不导入** Worker 里的 torch/demucs —— 只跑子进程 + 读 JSON。
        ⚠️⚠️ 失败时**不要在这里断言"改用原始音频"**（2026-10-15 实测踩到）：
        源码环境里 Worker 失败后还会走进程内 demucs，那条路**可能成功**；
        在这里就写"改用原始音频"，会出现"日志说的和实际做的不一样"。
        失败原因记进 `self._worker_error`，最终措辞由 `_separate_vocals`
        按**两条路的真实结果**决定。
        """
        try:
            from desktop.services import worker_service as ws
        except Exception as e:                               # noqa: BLE001
            self._worker_error = f"服务模块不可用（{type(e).__name__}）"
            self.log(f"[分离] Worker 服务不可用：{type(e).__name__}: {e}")
            return None
        exe = ws.discover()
        if not exe:
            self._worker_error = ""
            return None                                       # 没装，不算错误
        # 复用产品已有的 Demucs 权重目录（separate.CACHE_DIR）
        torch_home = ""
        try:
            from separate import CACHE_DIR as _cd
            torch_home = _cd
        except Exception:                                    # noqa: BLE001
            pass
        t0 = time.time()
        self.log(f"[分离] 使用独立 Worker：{os.path.basename(exe)}"
                 f"（模型 {self.opt.demucs_model}）")
        try:
            meta = ws.separate(audio_path, out_path, exe=exe,
                               torch_home=torch_home,
                               timeout=SEP_TIMEOUT_S,
                               stop=self._sep_cancelled, echo=lambda m: None)
        except Exception as e:                               # noqa: BLE001
            kind = getattr(e, "kind", type(e).__name__)
            detail = getattr(e, "detail", str(e))
            self._worker_error = f"{kind}：{detail}"
            self.log(f"[分离] ✗ Worker 失败（{kind}）：{detail}")
            return None
        dt = time.time() - t0
        self.log(f"[分离] 完成（Worker {meta.get('used_device', '?')} "
                 f"{meta.get('device_name', '')}），用时 {dt:.1f}s")
        notes.append(f"人声分离（Worker/Demucs {self.opt.demucs_model}，"
                     f"{meta.get('used_device')}）：用时 {dt:.1f}s")
        self._worker_error = ""
        return out_path

    def _sep_cancelled(self) -> bool:
        """是否被取消（供 Worker 轮询）。默认不取消，便于测试打桩。"""
        try:
            return bool(getattr(self, "_cancel_flag", None)
                        and self._cancel_flag())
        except Exception:                                    # noqa: BLE001
            return False

    def _separate_vocals(self, audio_path: str, song_title: str,
                         notes: List[str]) -> Optional[str]:
        """用 Demucs 把人声分离出来，返回人声轨路径（失败返回 None，流程照常走原音轨）。

        分离出的人声轨与原始音频**时间轴完全一致**，所以后面所有时间戳照旧可用；
        它只是让 whisper 和 CAM++ 不再被鼓/贝斯/弦乐干扰。

        路径优先级（第三/四阶段）：
        ① **独立 Demucs Worker**（可选运行组件，打包版主路径；主程序不导入 torch）
        ② 进程内 demucs（源码/开发环境，老路径，行为不变）
        ③ 都不可用 → 如实报原因，**跳过**依赖人声轨的句尾拖音
        """
        out_path = vocals_cache_path(audio_path, self.opt.demucs_model)

        # 同一首歌重复跑时直接复用（分离很费时间）—— **复用检查要放在最前**，
        # 这样不论走 Worker 还是进程内 demucs，都不会重算。
        if (os.path.exists(out_path)
                and os.path.getmtime(out_path) >= os.path.getmtime(audio_path)
                and os.path.getsize(out_path) > 1024):
            self.log(f"[分离] 复用已有人声轨：{os.path.basename(out_path)}")
            notes.append("人声分离：复用上次结果（想重新分离就删掉 outputs/*.vocals.wav）")
            return out_path

        # ---- ① 独立 Worker（首选）----
        r = self._worker_vocals(audio_path, out_path, notes)
        if r:
            return r
        _werr = getattr(self, "_worker_error", "")

        # ---- ② 进程内 demucs（源码环境/老用户）----
        def _both_failed(reason2: str) -> None:
            """两条路都失败时，才说"改用原始音频"，并把**两个原因**都说出来。"""
            both = "；".join(x for x in (f"Worker: {_werr}" if _werr else "",
                                        f"进程内 demucs: {reason2}" if reason2 else "")
                            if x)
            self.log(f"[分离] ✗ 人声分离两条路都不可用：{both} —— 已改用原始音频；"
                     f"句尾拖音补全依赖人声轨，本次会跳过")
            notes.append(f"⚠ 人声分离不可用（{both}），跳过")
            self._sep_reason = f"人声分离不可用（{both}）"

        try:
            from separate import VocalSeparator, available
        except Exception as e:
            self.log(f"[分离] ✗ 人声分离模块不可用：{type(e).__name__}: {e}")
            _both_failed(f"模块不可用（{type(e).__name__}）")
            return None
        if not available():
            # ⚠️⚠️ **必须说出来，而且要说准**（2026-10-15 用户实测）：
            # 打包版以前**没有内置 demucs**，`available()` 恒为 False；
            # 这里以前只往 notes 里塞一句（要翻到最后才看得到），
            # 日志里一个 `[分离]` 都没有 —— 结果拖音那步把原因说成
            # "**没开 --separate-vocals**"，而用户明明勾了。
            # 现在：用 `why_unavailable()` 报**具体**原因
            # （缺 torch？缺 einops？），并同时进日志与 notes。
            try:
                from separate import why_unavailable as _why
                reason = _why() or "demucs 不可用"
            except Exception:                                # noqa: BLE001
                reason = "demucs 不可用"
            self.log(f"[分离] ✗ 进程内 demucs 用不了：{reason}"
                     + (f"（Worker 也已失败：{_werr}）" if _werr else ""))
            _both_failed(reason)
            return None

        try:
            t0 = time.time()
            sep = VocalSeparator(model=self.opt.demucs_model, device=self.opt.device,
                                 log=self.log)
            sep.separate_to_file(audio_path, out_path)
            self.log(f"[分离] 完成，用时 {time.time() - t0:.1f}s"
                     + (f"（Worker 曾失败：{_werr}，已回落到进程内 demucs）"
                        if _werr else ""))
            notes.append(f"人声分离（进程内 Demucs {self.opt.demucs_model}）："
                         f"识别与声纹都基于纯人声轨，用时 {time.time() - t0:.1f}s"
                         + (f"；⚠ Worker 曾失败（{_werr}），本次走的是进程内路径"
                            if _werr else ""))
            self._sep_reason = ""
            return out_path
        except Exception as e:
            self.log(f"[分离] ✗ 进程内 demucs 失败：{type(e).__name__}: {str(e)[:150]}")
            _both_failed(f"{type(e).__name__}: {str(e)[:80]}")
            return None

    # ------------------------------------------------------------ 风格准则
    def _apply_style(self, lines: List[LyricLine]) -> "StyleReport":
        """按 Apple《风格标准和准则》做可程序化的规范化，并如实列出改动"""
        from lyric_style import StyleReport, normalize_line, style_preset

        rep = StyleReport()
        preset = style_preset(self.opt.style)
        mask = self.opt.explicit_policy == "mask"
        if self.opt.style == "keep" and not mask:
            rep.notes.append("未做文本规范化（--style keep）：歌词一字未改")
            return rep

        for i, line in enumerate(lines, 1):
            new = normalize_line(line.text, i,
                                 strip_punct=preset["strip_punct"],
                                 capitalize=preset["capitalize"],
                                 mask=mask, report=rep)
            if new != line.text:
                line.text = new
                line.tokens = tokenize_chinese(new)
        if mask:
            rep.notes.append("已按儿童适宜版本要求屏蔽脏话（如版本非儿童适宜，请用 --explicit-policy keep）")
        return rep

    def _detect_muted(self, report: AlignReport, audio_path: str, notes: List[str]):
        """检测被消音屏蔽的词：该字时间窗里既没能量也没基频，而同一行其它字都有。

        Apple 准则：被消音屏蔽的词语必须整个用星号替换。
        """
        audio = self.asr.load_audio(audio_path)
        sr = 16000
        masked = 0
        for al in report.lines:
            loud = [u for u in al.units if u.start is not None and u.end is not None]
            if len(loud) < 3:
                continue
            rms = {}
            for u in loud:
                seg = audio[int(u.start * sr):int(u.end * sr)]
                rms[id(u)] = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
            med = float(np.median(list(rms.values()))) if rms else 0.0
            if med <= 1e-4:
                continue
            for u in loud:
                if len(u.core) < 1 or rms[id(u)] > med * 0.15:
                    continue
                if u.duration < 0.12:
                    continue
                u.muted = True
                masked += 1
        if masked:
            notes.append(f"检测到 {masked} 个被消音屏蔽的字/词，已用 {MASK} 替换")
            self.log(f"      消音检测：{masked} 处已替换为 {MASK}")
        return masked

    def _apply_agents_from_reference(self, report: AlignReport, path: str,
                                     notes: List[str]) -> int:
        """从一份**已有 TTML**（例如 AMLL TTML DB 里人工标注过的版本）读出每行的演唱者，
        套到本次对齐出来的行上。

        为什么需要它：声纹对**音色接近的对唱**实测分不开
        （《在出发之前》对人工真值只有 51.5% = 瞎猜，最好的音色特征无监督也只有 66.7%），
        所以当社区已经有了人工标注，复用它是唯一可靠的自动化路径。
        匹配顺序：先按**文本相似度**，对不上再按**时间重叠**。
        """
        from difflib import SequenceMatcher

        from ttml_reader import parse_ttml_file

        doc = parse_ttml_file(path)
        # 参考文献里连 <ttm:agent> 声明都没有 → 说明它没做演唱者标注。
        # （parse_ttml 会把缺失的 agent 兜底成 v1，直接套会静默把所有行变成 v1）
        if not doc.declared_agents:
            notes.append(f"⚠ 参考文件 {os.path.basename(path)} 里没有 ttm:agent，未套用")
            return 0
        refs = [(re.sub(r"[\s()（）\[\]]", "", l.text), l.begin, l.end, l.agent)
                for l in doc.lines if l.agent]
        if not refs:
            notes.append(f"⚠ 参考文件 {os.path.basename(path)} 里没有可用的歌词行，未套用")
            return 0
        # 连参考文件的 agent 类型一起继承（真实 AMLL DB 文件里 v2 常写成 type="other"）
        self._ref_agent_types = dict(getattr(doc, "agent_types", {}) or {})

        role_of = {"v1": "x-duet-a", "v2": "x-duet-b"}
        applied = by_text = by_time = 0
        for al in report.lines:
            key = re.sub(r"[\s()（）\[\]]", "", al.text)
            dur = max(0.2, al.end - al.begin)
            best, best_score, best_ratio = None, -1.0, 0.0
            for r in refs:
                ratio = SequenceMatcher(None, key, r[0]).ratio()
                # 副歌会重复出现同一句，光看文本会挑错那一次 —— 用时间重叠破平局
                overlap = max(0.0, min(al.end, r[2]) - max(al.begin, r[1]))
                score = ratio + 0.35 * min(1.0, overlap / dur)
                if score > best_score:
                    best, best_score, best_ratio = r, score, ratio
            if best is None or best_ratio < 0.6:
                # 文本对不上 → 直接按时间重叠找
                cand = [(min(al.end, r[2]) - max(al.begin, r[1]), r) for r in refs]
                cand = [c for c in cand if c[0] > 0]
                if not cand:
                    continue
                best = max(cand, key=lambda c: c[0])[1]
                by_time += 1
            else:
                by_text += 1
            al.agent = best[3]
            al.role = role_of.get(best[3], "")
            applied += 1

        counts = {}
        for al in report.lines:
            counts[al.agent] = counts.get(al.agent, 0) + 1
        self.log(f"      套用参考文件的演唱者：{applied}/{len(report.lines)} 行"
                 f"（文本匹配 {by_text}，时间匹配 {by_time}）→ "
                 + "，".join(f"{k} {v} 行" for k, v in sorted(counts.items())))
        notes.append(f"演唱者归属来自参考文件 {os.path.basename(path)}"
                     f"（{applied} 行，文本匹配 {by_text} / 时间匹配 {by_time}）")
        return applied

    def _use_lyric_markers(self, lines: Sequence[LyricLine], report: AlignReport,
                           notes: List[str],
                           artists: Optional[Sequence[str]] = None) -> None:
        """**关闭声纹**：演唱者完全由歌词里的 ``[v1]``/``[v2]`` 标记决定。

        ``preprocess.detect_agent_switch`` 已经把标记写进 ``lines``/``report``
        （角色 ``x-duet-a/b`` 也跟着走），这里只负责：
        1. 报告实际用到哪些声部；
        2. 一个字标记都没有、而元数据写了 ≥2 位艺人时**提醒一句** ——
           这正是"以为分了对唱其实全是 v1"最容易发生的地方（铁律 #14）。
        """
        used: List[str] = []
        for al in report.lines:
            a = (al.agent or "v1")
            if a not in used:
                used.append(a)
        marked = sum(1 for l in lines if getattr(l, "manual_agent", False))
        if marked:
            self.log(f"[分段] 关闭声纹：用歌词里的演唱者标记（{marked} 行有标记，"
                     f"实际用到 {len(used)} 个声部：{'、'.join(used)}）")
            notes.append(f"演唱者分段：关闭（用歌词标记），实际用到 {len(used)} 个声部"
                         f"：{'、'.join(used)}")
            if len(used) <= 1:
                notes.append("⚠ 标了演唱者但全曲只落到一个声部，检查标记写法"
                             "（[v1]/[v2]/[男声]/[女声]/[合唱]）")
        else:
            self.log("[分段] 关闭声纹：歌词里没有演唱者标记 → 全曲 ttm:agent=\"v1\"")
            notes.append("演唱者分段：关闭（歌词里没有 [v1]/[v2] 标记），全曲 v1")
            hint = duet_hint(artists, 1, "歌词里没有演唱者标记、且你选的是「关闭」")
            if hint:
                notes.append(hint)
                self.log("      " + hint)

    def _ttml_lang(self) -> str:
        """TTML 的 ``xml:lang``。

        ⚠️ 选了「自动检测」时必须用 **whisper 实际检测到的语言**：
        写死 zh-Hans 会让英文歌/日文歌的 ``xml:lang`` 是错的（而 TTML 要求它是
        BCP-47 真语言标签，``auto`` 这种值直接非法）。检测不到才退回 zh-Hans。
        """
        from asr import is_auto_language
        if is_auto_language(self.opt.language):
            det = str(getattr(self.asr, "last_language", "") or "")
            if det:
                return normalize_language_code(det)
        return normalize_language_code(self.opt.language)

    def _split_singers(self, lines: Sequence[LyricLine], report: AlignReport,
                       audio_path: str, notes: List[str],
                       artists: Optional[Sequence[str]] = None,
                       forced_n: int = 0):
        """**强制 N 个声部**：用 CAM++ 声纹把每行聚成 N 簇，写回 ttm:agent / 角色标记。

        为什么不再"自动判断人数"：音频里判不出到底几个人（三种判据全部失败，
        见 ``diarize.py`` 与 AGENTS.md）。人数**由用户明确给**，这里只负责
        "给定 N 之后每行归哪一簇"—— 实测与 AMLL 官方人工标注约 90% 一致。
        """
        from diarize import available, diarize

        n = int(forced_n or 0) or 2
        # 强制 N 是用户的明确选择，**优先于歌词标记**（想用标记就选"关闭"）；
        # 但必须说出来，否则用户会以为标记丢了。
        if any(getattr(l, "manual_agent", False) for l in lines):
            self.log(f"      歌词里有 [v1]/[v2] 标记，但已按你选的「强制 {n} 声部」覆盖"
                     "（想按标记分就把演唱者分段设为「关闭」）")
            notes.append(f"⚠ 歌词里的演唱者标记被「强制 {n} 声部」覆盖"
                         "（要按标记分请选「关闭」）")

        self.log(f"[分段] CAM++ 声纹聚类（强制 {n} 个声部）")
        if not available():
            # ⚠️⚠️ **这里必须打日志。**以前只往 notes 里塞一句就 return ——
            # notes 只进报告 JSON，用户在日志里看不到任何东西，于是"开了强制双声部
            # 却一行都没分"变成了完全静默的失败（真踩过，排查了很久）。
            # 凡是"没做成分声部"的路径，都要在日志里明确说出来。
            msg = ("⚠ 未找到 CAM++ 声纹模型（models/campplus/），"
                   "强制分声部不可用；已退回歌词标记")
            self.log("      " + msg)
            notes.append(msg)
            return

        audio = self.asr.load_audio(audio_path)
        spans = [(al.begin, al.end) for al in report.lines]
        res = diarize(audio, spans, n_singers=n)
        self.log("      " + res.reason)
        notes.append("声纹分段：" + res.reason)

        if n == 1 or res.n_singers <= 1:
            # 强制单声部：把声纹分出来的（或歌词标记带来的）agent 一律收成 v1
            for al in report.lines:
                al.agent = "v1"
                al.role = ""
            notes.append("强制单声部：全曲 ttm:agent=\"v1\"")
            hint = duet_hint(artists, 1, "你选的是「强制单声部」")
            if hint:
                notes.append(hint)
                self.log("      " + hint)
            return

        # 簇编号（``res.labels`` 里的 0/1/…）是 k-means 输出，**与"谁先唱"无关** ——
        # 同一份音频重跑就可能把先开口的人编成 1 号。而 Apple Music 的对唱左右栏按
        # 声部**首次出现的顺序**排栏 ⇒ 第一个开口的声部必须是 v1；否则编辑台里选
        # 「一声部」（v1）的行会在 Apple Music 显示到第二栏（用户实测 bug：
        # "选 1 声部显示成 2 声部，选 2 声部显示成 1 声部"）。
        remap = renumber_by_first_seen(res.labels)       # {旧簇号: 新 v 序号(1 起)}
        role_of = {"v1": "x-duet-a", "v2": "x-duet-b"}
        for al, lab in zip(report.lines, res.labels):
            agent = f"v{remap[lab]}"
            al.agent = agent
            al.role = role_of.get(agent, "")
        counts = {f"v{remap[lab]}": res.labels.count(lab) for lab in remap}
        notes.append("分声部结果：" + "，".join(f"{k} {v} 行" for k, v in counts.items())
                     + "（如与听感不符可手动加 [v1]/[v2] 标记覆盖）")
        notes.append("两个人同时张嘴的齐唱/和声仍无法拆分（一行里混了两个声纹）")
        # 哪几行最该人工看一眼：到两个簇心的余量最小 = 两个人都像。
        # ⚠️ 这只是**弱提示**：实测（本曲 32 行、3 个错行）按余量排序，
        # 要抓全 3 个错行得列 24 行（75% 误报），所以只列最小的 3 行，
        # 并明确告诉用户"错的往往是连续一段"，别让人以为没列出来就没问题。
        if res.uncertain:
            rows = "、".join(f"L{i + 1}" for i in res.uncertain[:3])
            self.log(f"      声纹最没把握的 3 行（仅按余量排序）：{rows}")
            notes.append(f"声纹最没把握的 3 行：{rows}（弱提示，参考价值有限）")
        notes.append("自动分声部与 AMLL 官方人工标注实测约 90% 一致；"
                     "错的一般是**连续一段**，建议按段落扫一眼再定，改行去编辑台最快")

    def _apply_pause_spaces(self, lines: List[AlignedLine]):
        """按 AMLL 习惯，在明显停顿处给前一个字补一个空格"""
        for line in lines:
            units = [u for u in line.units if not u.is_bg]
            for a, b in zip(units, units[1:]):
                if a.end is not None and b.start is not None and (b.start - a.end) >= self.opt.pause_space:
                    if not a.trailing.endswith(" "):
                        a.trailing += " "
