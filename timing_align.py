#!/usr/bin/env python3
"""实验性对齐引擎：TIFA（歌声专用音素对齐）。

来源与边界
----------
算法/适配思路来自 MIT 项目 Timing（https://github.com/Zennmn/Timing），
TIFA 运行时与模型来自 openvpi/TIFA（源码 MIT；模型包按发布包条款使用）。
这里**不复制** Timing 的完整代码，只实现我们项目需要的最小链路：

    我们的 LRC/歌词行（逐句锚点）
      → 按行切 clip（±0.2s 上下文）
      → TIFA infer.py（本地 GPU）
      → 解析 TextGrid 的 texts tier（中文逐字边界）
      → 映射回我们的 Unit / AlignedLine
      → 复用现有 TTMLWriter / ttml_check 输出并自检

实验性限制
----------
- 需要逐句 LRC（TIFA 在这条链路里按行锚点切窗；纯文本请用默认 whisper 引擎）；
- 当前按中文（普通话）配置跑；英文/日文混唱需要扩展 G2P；
- 模型不随包分发，放在 ``models/tifa``（model.pt + config.yaml + vocabulary.json +
  runtime/），由用户自行准备；
- 任何一行映射失败都有明确占位/回退，不伪造逐字时间。

用法
----
    python main.py --audio song.wav --lyrics song.lrc --align-engine timing-tifa
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from matcher import AlignedLine, AlignReport, scan_line
from ttml_check import CheckResult, check_ttml
from ttml_writer import TTMLWriter

HERE = Path(__file__).resolve().parent


def _tifa_roots() -> List[Path]:
    """TIFA 可能的位置：环境变量 > 程序目录 > 包内 > 当前目录。"""
    roots: List[Path] = []
    env = os.environ.get("L2T_TIFA_DIR", "").strip()
    if env:
        roots.append(Path(env))
    if getattr(sys, "frozen", False):
        app = Path(sys.executable).resolve().parent
        roots += [app / "models" / "tifa", app.parent / "models" / "tifa",
                  app.parent.parent / "models" / "tifa"]
    roots += [HERE / "models" / "tifa", Path.cwd() / "models" / "tifa"]
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def tifa_root() -> Path:
    for r in _tifa_roots():
        if (r / "model.pt").is_file() and (r / "runtime" / "infer.py").is_file():
            return r
    return _tifa_roots()[0]


def _python_exe() -> Optional[str]:
    """跑 TIFA infer.py 的 Python。

    冻结后端（WinUI 便携/安装包）自己没有解释器，必须由用户通过
    ``L2T_TIFA_PYTHON`` 指向一个装了 TIFA 依赖的 python.exe。
    """
    env = os.environ.get("L2T_TIFA_PYTHON", "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return str(p)
        # 允许传安装目录
        for cand in (p / "python.exe", p / "Scripts" / "python.exe"):
            if cand.is_file():
                return str(cand)
    if not getattr(sys, "frozen", False):
        return sys.executable
    return None


def available() -> tuple[bool, str]:
    """返回 (可用, 原因)。不加载模型，只查文件。"""
    root = tifa_root()
    if not (root / "model.pt").is_file():
        return False, f"缺少 TIFA 模型：{root / 'model.pt'}"
    if not (root / "config.yaml").is_file():
        return False, f"缺少 TIFA 配置：{root / 'config.yaml'}"
    if not (root / "runtime" / "infer.py").is_file():
        return False, f"缺少 TIFA 运行时：{root / 'runtime' / 'infer.py'}"
    if getattr(sys, "frozen", False) and not _python_exe():
        return False, ("冻结版运行 TIFA 需要设置环境变量 L2T_TIFA_PYTHON，"
                       "指向已安装 TIFA 依赖的 python.exe")
    return True, "ok"


def _kept(ch: str) -> bool:
    """模型实际要发音的字符（与 preprocess/matcher 的口径保持一致）。"""
    import unicodedata
    return unicodedata.category(ch)[0] in "LN"


def _read_wav(path: str):
    """用标准库 wave 读 WAV 为 mono float32，避免冻结后端依赖 soundfile。"""
    import numpy as np
    import wave
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ival = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int32) << 16))
        ival = np.where(ival >= 1 << 23, ival - (1 << 24), ival)
        data = ival.astype(np.float32) / float(1 << 23)
    elif sw == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    else:
        raise RuntimeError(f"不支持的 WAV 位深：{sw * 8} bit")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, int(sr)


def _write_wav(path: Path, samples, sr: int) -> None:
    """写 16bit mono WAV（TIFA 的 ffmpeg/librosa 都能读）。"""
    import numpy as np
    import wave
    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)


def _external_decode(path: str) -> tuple:
    """冻结后端没有 ffmpeg 时，借外部 TIFA Python（soundfile/librosa）解码。"""
    python = _python_exe()
    if not python:
        raise RuntimeError("读取音频失败：既没有 ffmpeg，也没有 L2T_TIFA_PYTHON")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = tmp.name
    code = (
        "import sys, soundfile as sf, numpy as np\n"
        "a, sr = sf.read(sys.argv[1], dtype='float32', always_2d=True)\n"
        "a = a.mean(axis=1)\n"
        "sf.write(sys.argv[2], a, sr, subtype='PCM_16')\n"
    )
    try:
        proc = subprocess.run([python, "-X", "utf8", "-c", code, str(path), wav],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=1800)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "")[-500:])
        return _read_wav(wav)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass


def _decode_audio(path: str) -> tuple:
    """读音频为 mono float32：wav 走标准库；其它格式用 ffmpeg / 外部 Python。"""
    p = str(path)
    if p.lower().endswith(".wav"):
        try:
            return _read_wav(p)
        except Exception:
            pass
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return _external_decode(p)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = tmp.name
    try:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-i", p, "-vn", "-ac", "1", "-ar", "48000",
                        "-c:a", "pcm_s16le", wav], check=True)
        return _read_wav(wav)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass


@dataclass
class _Clip:
    line_index: int
    path: Path
    start: float
    end: float
    text: str
    lang: str = "zh"


def _parse_textgrid(path: Path) -> dict:
    """极简 TextGrid 读取：返回 {tier_name: [(xmin, xmax, text), ...]}。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    tiers: dict = {}
    current = None
    cur_xmin = cur_xmax = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'name = "([^"]+)"', line)
        if m:
            current = m.group(1)
            tiers.setdefault(current, [])
            continue
        m = re.match(r"xmin = ([-\d.]+)", line) if current else None
        if m:
            cur_xmin = float(m.group(1))
            continue
        m = re.match(r"xmax = ([-\d.]+)", line) if current else None
        if m:
            cur_xmax = float(m.group(1))
            continue
        m = re.match(r'text = "(.*)"', line) if current else None
        if m and cur_xmin is not None and cur_xmax is not None and cur_xmax > cur_xmin:
            tiers[current].append((cur_xmin, cur_xmax, m.group(1)))
            cur_xmin = cur_xmax = None
    return tiers


def _line_language(text: str, fallback: str = "zh") -> str:
    """逐行语言：假名→ja，纯拉丁→en，汉字+拉丁→zh-en，其余按整首设置。"""
    has_kana = re.search(r"[\u3040-\u30ff]", text)
    has_han = re.search(r"[\u3400-\u9fff]", text)
    has_latin = re.search(r"[A-Za-z]", text)
    if has_kana:
        return "ja-en" if has_latin else "ja"
    if has_han and has_latin:
        return "zh-en"
    if has_latin and not has_han:
        return "en"
    if has_han:
        return "zh"
    f = (fallback or "zh").strip().lower()
    return f if f in ("zh", "ja", "en", "zh-en", "ja-en") else "zh"


def _lang_args(lang: str) -> List[str]:
    if lang == "zh-en":
        return ["-l", "zh", "-L", "en"]
    if lang == "ja-en":
        return ["-l", "ja", "-L", "en"]
    return ["-l", lang or "zh"]


def _prepare_g2p(work: Path, model_root: Path) -> Optional[Path]:
    """生成一份 G2P 配置：把 @ 资源改成绝对路径，并给日语 MeCab 指定
    unidic-lite 词典，避免 g2pflow 在首次转换时联网下载 UniDic。

    返回 g2p 配置路径；生成失败返回 None（退回 TIFA 内置配置）。
    """
    try:
        import yaml
        cfg_all = yaml.safe_load((model_root / "config.yaml").read_text(encoding="utf-8"))
        g2p = cfg_all["inference"]["g2p"]

        def resolve(obj):
            if isinstance(obj, dict):
                return {k: resolve(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [resolve(v) for v in obj]
            if isinstance(obj, str) and obj.startswith("@"):
                return str((model_root / obj[1:]).resolve())
            return obj

        g2p = resolve(g2p)
        try:
            import unidic_lite
            dicdir = unidic_lite.DICDIR
        except Exception:
            dicdir = None
        if dicdir:
            for conv in g2p.get("converters", []):
                if conv.get("id") == "japanese-mecab":
                    conv.setdefault("kwargs", {})["unidic_dir"] = dicdir
        path = work / "g2p.yaml"
        path.write_text(yaml.safe_dump(g2p, allow_unicode=True), encoding="utf-8")
        return path
    except Exception:
        return None


def _run_tifa(clips: List[_Clip], work: Path, log: Callable[[str], None],
              language: str = "zh") -> dict:
    """按行语言分组跑 TIFA infer.py，返回 {line_index: TextGrid path}。"""
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    root = tifa_root()
    runtime = root / "runtime"
    model = root / "model.pt"
    python = _python_exe()
    if not python:
        raise RuntimeError("冻结版运行 TIFA 需要 L2T_TIFA_PYTHON")
    groups: dict = {}
    for clip in clips:
        groups.setdefault(clip.lang, []).append(clip)
    log("      TIFA 逐行语言：" + "、".join(
        f"{k}×{len(v)}" for k, v in sorted(groups.items())))

    result = {}
    env = dict(os.environ)
    env["PYTHONPATH"] = str(runtime) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env.setdefault("TMP", str(work))
    env.setdefault("TEMP", str(work))
    g2p_path = _prepare_g2p(work, root)
    for lang, group in sorted(groups.items()):
        sub = work / f"lang_{re.sub(r'[^a-z-]', '_', lang)}"
        clips_dir = sub / "clips"
        out_dir = sub / "tifa-out"
        if sub.exists():
            shutil.rmtree(sub, ignore_errors=True)
        clips_dir.mkdir(parents=True)
        for clip in group:
            shutil.copy2(clip.path, clips_dir / f"line_{clip.line_index:04d}.wav")
            (clips_dir / f"line_{clip.line_index:04d}.txt").write_text(
                clip.text, encoding="utf-8", newline="\n")
        cmd = [python, "-X", "utf8", str(runtime / "infer.py"), str(clips_dir),
               "-m", str(model), "-o", str(out_dir),
               "--batch-size", "8", "--num-workers", "0", "--precision", "32-true"]
        if g2p_path is not None:
            cmd += ["--g2p", str(g2p_path)]
        cmd += _lang_args(lang)
        log(f"      TIFA [{lang}]：{len(group)} 个句段（GPU）…")
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=str(runtime), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            tail = (proc.stdout or "")[-1200:] + (proc.stderr or "")[-1600:]
            raise RuntimeError(f"TIFA[{lang}] 推理失败（exit={proc.returncode}）：\n{tail}")
        log(f"      TIFA [{lang}] 完成：{time.time() - t0:.1f}s")
        for grid in sorted(out_dir.rglob("*.TextGrid")):
            m = re.search(r"line_(\d+)", grid.stem)
            if m:
                result[int(m.group(1))] = grid
    return result


def _norm_char(ch: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", ch).casefold()


def _map_tifa_line(text: str, intervals: Sequence[tuple], offset: float,
                   anchors: tuple, log: Callable[[str], None]):
    """把 TIFA texts tier 映射成「原文字符 → (start,end)」。

    返回 (char_times, matched)；char_times 是与 text 等长的列表，
    标点/空格为 None；映射不上返回 (None, False)，由调用方占位。
    英文按大小写无关比较（TIFA 可能返回 hello/Hello），日文按 NFKC 比较。
    """
    n = len(text)
    char_times: List[Optional[tuple]] = [None] * n
    model_chars = []
    for a, b, t in intervals:
        for ch in t:
            if _kept(ch):
                model_chars.append((_norm_char(ch), a + offset, b + offset))
    targets = [i for i, ch in enumerate(text) if _kept(ch)]
    target_seq = "".join(_norm_char(text[i]) for i in targets)
    model_seq = "".join(c for c, _, _ in model_chars)
    if model_seq != target_seq:
        # TIFA 偶尔把标点/撇号/大小写归一化得和原文不同：先按 NFKC+casefold
        # 比，仍不匹配才回退（不硬造时间）。
        return None, False
    if not targets:
        return None, False
    # 每个 model 字一个边界；多字 word 在 word 内部按字数均分，保证同层字不重叠
    pos = 0
    for _ch, a, b in model_chars:
        # 找到该 model 字所属的 interval 内位置：按连续相同 (a,b) 分组
        # （model_chars 是逐字展开的，同一 TIFA word 的边界相同）
        group = []
        j = pos
        while j < len(model_chars) and model_chars[j][1:] == (a, b):
            group.append(model_chars[j])
            j += 1
        span = (b - a) / max(1, len(group))
        for k in range(len(group)):
            idx = targets[pos + k]
            char_times[idx] = (a + span * k, a + span * (k + 1))
        pos += len(group)
    # 标点/空格：用前一个有效字的结束时间做一个 1ms 的占位（只影响 span 归属，
    # 不伪造发音时长；writer 会把标点并进相邻字的 span）
    last = None
    for i in range(n):
        if char_times[i] is not None:
            last = char_times[i]
        elif last is not None:
            char_times[i] = (last[1], last[1] + 0.001)
    return char_times, True


def _fallback_times(text: str, begin: float, end: float) -> List[Optional[tuple]]:
    """逐字均匀分配（明确标记 matched=False，只作为可编辑占位）。"""
    n = len(text)
    if n <= 0 or end <= begin:
        return [None] * n
    step = (end - begin) / n
    return [(begin + i * step, begin + (i + 1) * step) for i in range(n)]


def _build_line(text: str, agent: str, role: str, paragraph: int,
                char_times: List[Optional[tuple]], matched: bool,
                anchors: tuple) -> AlignedLine:
    units = scan_line(text)
    cursor = 0
    n = len(text)
    prev_end = anchors[0]
    for u in units:
        # scan_line 的 unit.text 可能带 leading 空格（英文 " a"），也可能折叠掉；
        # 先原样匹配，失败再跳过空白/向后找，不能无条件先跳过空白 ——
        # 否则 " a" 的 space 被跳过，"a" 会被匹配到后面的同名片段（真踩过）。
        seg = u.text
        pos = cursor
        if not text.startswith(seg, pos):
            # 有些 unit 的 leading 里带空格，有些（scan_line 折叠掉空格后）不带；
            # 先原样匹配，再尝试跳过空白，最后才向后查找（避免把后面的同名
            # 片段当成当前 unit —— 英文 " a" 真踩过：a 被映射到下一个 a 上）。
            skipped = pos
            while skipped < n and text[skipped].isspace():
                skipped += 1
            if text.startswith(seg, skipped):
                pos = skipped
            else:
                found = text.find(seg, pos)
                if found != -1 and found - pos <= 8:
                    pos = found
        lead = len(u.leading)
        core_start = pos + lead
        core_end = core_start + len(u.core)
        spans = [char_times[i] for i in range(core_start, min(core_end, len(char_times)))
                 if char_times[i] is not None]
        if spans:
            u.start = min(a for a, _ in spans)
            u.end = max(b for _, b in spans)
        else:
            # 纯标点 unit：贴在前一个结束点上，给 1ms，保证 begin<end
            u.start = prev_end
            u.end = prev_end + 0.001
        prev_end = u.end
        cursor = pos + len(seg)
        u.matched = matched
        u.prob = 1.0 if matched else 0.0
    begin = min((u.start for u in units), default=anchors[0])
    end = max((u.end for u in units), default=anchors[1])
    if end <= begin:
        begin, end = anchors
    return AlignedLine(text=text, agent=agent, units=units, begin=begin, end=end,
                       matched=len(units) if matched else 0, total=len(units),
                       anchored=True, role=role, paragraph=paragraph)


def _enforce_agent_monotonic(lines: List[AlignedLine], notes: List[str],
                             log: Callable[[str], None]) -> int:
    """同一 agent 相邻行不重叠（TTML 铁律 4）。

    TIFA 是逐行独立对齐的，clip 又带了 ±0.2s 上下文，行尾/行首可能越过
    相邻行。这里只压同 agent 的行（不同歌手的叠唱允许重叠），整行按比例
    缩放，避免多个越界字全落到同一个 limit 上造成字间重叠。
    """
    fixed = 0
    last: dict = {}
    for i, line in enumerate(lines):
        ag = line.agent or ""
        prev = last.get(ag)
        if prev is not None and prev[1] > line.begin:
            j, prev_end = prev
            a, b = lines[j], line
            limit = float(b.begin)
            span_old = a.end - a.begin
            span_new = limit - a.begin
            if span_old > 0 and span_new > 0:
                scale = span_new / span_old
                for u in a.units:
                    if u.start is not None:
                        u.start = a.begin + (u.start - a.begin) * scale
                    if u.end is not None:
                        u.end = a.begin + (u.end - a.begin) * scale
                a.end = limit
                fixed += 1
                log(f"      [重叠] L{j + 1} 行尾压到 {limit:.3f}（同歌手 L{i + 1} 开始）")
        last[ag] = (i, line.end)
    if fixed:
        notes.append(f"修正同歌手行重叠 {fixed} 处")
    return fixed


def align_lines(lyrics_text: str, audio_path: str, output_path: str,
                *, title: str = "", artists: Optional[Sequence[str]] = None,
                album: str = "", author: str = "", author_id: str = "",
                platform_ids: Optional[dict] = None,
                translation_text: str = "", translation_lang: str = "",
                songwriters: Optional[Sequence[str]] = None,
                mode: str = "amll", markers: str = "auto", line_id: str = "both",
                translation_style: str = "head", time_format: str = "clock",
                song_part: str = "", keep_credits: bool = False,
                language: str = "zh",
                postprocess: Optional[Callable[[AlignReport], None]] = None,
                log: Callable[[str], None] = print) -> dict:
    """TIFA 对齐并写出 TTML；返回 {ttml, check, report, notes, elapsed, duration}。"""
    from preprocess import detect_agent_switch, parse_lyrics
    from pipeline import _is_credit_line

    t0 = time.time()
    notes: List[str] = []
    ok, why = available()
    if not ok:
        raise RuntimeError(
            f"TIFA 实验引擎不可用：{why}\n"
            "  请把 TIFA-1.0-ST 解压到 models/tifa（model.pt/config.yaml/vocabulary.json），\n"
            "  并把 openvpi/TIFA 源码解压到 models/tifa/runtime。")

    lines = parse_lyrics(lyrics_text)
    lines = detect_agent_switch(lines)
    if not keep_credits:
        before = len(lines)
        lines = [l for l in lines if not _is_credit_line(l)]
        if len(lines) != before:
            log(f"      跳过 {before - len(lines)} 行制作人员/标题信息")
    if not lines:
        raise ValueError("歌词解析失败：没有有效歌词行")
    if not all(l.line_start is not None for l in lines):
        raise ValueError(
            "TIFA 引擎需要逐句时间锚点（LRC / 带时间的 JSON）；\n"
            "  纯文本歌词请用默认 whisper 引擎（去掉 --align-engine timing-tifa）。")

    audio, sr = _decode_audio(audio_path)
    duration = len(audio) / sr
    # LRC 最后一行通常没有下一行可推出 end：用音频总长兜底；
    # 中间行缺失 end 时用下一行 start（与 preprocess 的语义一致）。
    # ⚠️ 最后一行不能直接拿整首剩余时长当 clip：那会把 8 个字对到 280 秒上。
    known = [l.line_end - l.line_start for l in lines
             if l.line_end is not None and l.line_end > l.line_start]
    typical = sorted(known)[len(known) // 2] if known else 6.0
    for i, line in enumerate(lines):
        if line.line_end is None:
            nxt = next((l.line_start for l in lines[i + 1:] if l.line_start is not None), None)
            if nxt is not None and nxt > line.line_start:
                line.line_end = nxt
            else:
                line.line_end = min(duration, line.line_start + max(2.0, min(20.0, typical * 1.2)))
        if line.line_end <= line.line_start:
            line.line_end = min(duration, line.line_start + 0.5)
    log(f"      音频 {duration:.1f}s / {sr}Hz；逐句锚点 {len(lines)} 行")

    work = HERE / ".tmp" / "timing-tifa" / (
        re.sub(r"[^\w-]+", "_", Path(output_path).stem)[:40] or "out")
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    clips: List[_Clip] = []
    clip_info = {}
    for i, line in enumerate(lines):
        start = max(0.0, float(line.line_start) - 0.2)
        end = min(duration, float(line.line_end) + 0.2)
        if end - start < 0.2:
            end = min(duration, start + 0.2)
        a, b = int(start * sr), int(end * sr)
        if b - a < int(0.1 * sr):
            raise RuntimeError(f"第 {i + 1} 行音频过短，无法对齐")
        clip_path = work / f"clip_{i:04d}.wav"
        _write_wav(clip_path, audio[a:b], sr)
        lang = _line_language(line.text, language)
        clips.append(_Clip(i, clip_path, start, end, line.text, lang))
        clip_info[i] = (start, end, line)

    grids = _run_tifa(clips, work, log, language=language)

    report_lines: List[AlignedLine] = []
    unmatched: List[int] = []
    for i, clip in enumerate(clips):
        line = clip_info[i][2]
        grid = grids.get(i)
        if grid is None:
            unmatched.append(i)
            char_times, matched = None, False
        else:
            tiers = _parse_textgrid(grid)
            intervals = tiers.get("texts", [])
            char_times, matched = _map_tifa_line(
                line.text, intervals, clip.start, (clip.start, clip.end), log)
            if not matched:
                unmatched.append(i)
        if not matched or char_times is None:
            char_times = _fallback_times(line.text, float(line.line_start), float(line.line_end))
            notes.append(f"第 {i + 1} 行 TIFA 映射失败，保留 LRC 整句时间作占位（需人工补轴）")
        report_lines.append(_build_line(
            line.text, line.agent, line.role, line.paragraph, char_times,
            matched, (float(line.line_start), float(line.line_end))))

    total = sum(l.total for l in report_lines)
    matched_units = sum(l.matched for l in report_lines)
    report = AlignReport(lines=report_lines, total_units=total,
                         matched_units=matched_units, anchored=True,
                         warnings=[])
    _enforce_agent_monotonic(report.lines, notes, log)

    # TIFA 之后的可选后处理：声纹分声部 / 歌词标记（由 pipeline 注入）。
    # 放在写 TTML 之前，保证 agent 真正进文件。
    if postprocess is not None:
        try:
            postprocess(report)
        except Exception as e:                          # noqa: BLE001
            notes.append(f"TIFA 后处理失败（{type(e).__name__}: {e}），保留原声部")
            log(f"      TIFA 后处理失败：{e}")

    log("      写 TTML + 自检…")
    writer = TTMLWriter(mode=mode, markers=markers, line_id=line_id,
                        translation_style=translation_style,
                        time_format=time_format, song_part=song_part,
                        inline_translation_lang=translation_lang or "zh-Hans")
    ttml = writer.write(
        report.lines, title=title, artists=list(artists or []), album=album,
        language="zh-Hans", platform_ids=platform_ids or {}, author=author,
        author_id=author_id, duration=duration,
        translation_lang=translation_lang or "zh-Hans",
        songwriters=list(songwriters or []))
    check = check_ttml(ttml, strict=(time_format == "clock"))
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(ttml, encoding="utf-8", newline="\n")
        log(f"      已保存: {output_path}")
    if unmatched:
        notes.append(f"{len(unmatched)} 行未完成逐字映射（保留 LRC 整句占位）")
    notes.append(f"TIFA 字级映射率 {report.match_rate:.1%}"
                 f"（匹配 {matched_units}/{total}）")
    return {"ttml": ttml, "check": check, "report": report, "notes": notes,
            "elapsed": time.time() - t0, "duration": duration}
