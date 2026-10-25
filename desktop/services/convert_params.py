"""``params`` 字典 → ``ConvertOptions`` 的**唯一**映射。

为什么单独抽出来：Web 界面、桌面窗口、命令行三条入口都要构造同一份
``ConvertOptions``。以前这段映射写在 ``web_server._run_task`` 里，
桌面版再抄一遍的话，**加一个参数就会漏掉一条入口**（"界面填了、命令行有、
桌面版没传"这类问题最难查）。

所以：**任何入口都调 ``build_options(params)``，不要再手写字段列表。**

⚠️ ``ConvertOptions`` 的字段只增不改：这里用"白名单 + 类型转换"而不是
``ConvertOptions(**params)``，因为 params 里还有界面专用字段
（标题、艺人、输出目录……），直接展开会 ``TypeError``。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

# ---------------------------------------------------------------- 类型转换

def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("", "0", "false", "no", "off", "none"):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    return str(v)


# 字段 → (类型, 默认值)  —— 与 pipeline.ConvertOptions 一一对应
_FIELDS = [
    ("mode", _as_str, "amll"),
    ("language", _as_str, "zh"),
    ("model", _as_str, "large-v3"),
    ("device", _as_str, "auto"),
    ("align_engine", _as_str, "whisper"),
    ("refine", _as_bool, True),
    ("chunked", _as_bool, True),
    ("skip_credits", _as_bool, True),
    ("pause_space", _as_float, 0.0),
    ("markers", _as_str, "agent"),
    ("line_id", _as_str, "both"),
    ("translation_style", _as_str, "head"),
    ("time_format", _as_str, "clock"),
    ("song_part", _as_str, ""),
    ("singer_mode", _as_str, "off"),
    ("auto_agents", _as_bool, False),
    ("split_singers", _as_int, 0),
    ("style", _as_str, "keep"),
    ("explicit_policy", _as_str, "keep"),
    ("detect_muted", _as_bool, False),
    ("separate_vocals", _as_bool, False),
        ("allow_same_agent_overlap", _as_bool, False),
    ("demucs_model", _as_str, "htdemucs"),
    ("extend_tails", _as_bool, True),
    ("verify_amll", _as_bool, True),
    # 下面这些界面里没有开关，但命令行有 —— 留着是为了"CLI 能设的，程序里也能设"
    ("refine_threshold", _as_float, 0.6),
    ("max_refine_lines", _as_int, 12),
    ("beam_size", _as_int, 5),
    ("chunk_max_seconds", _as_float, 22.0),
    ("chunk_max_lines", _as_int, 10),
    ("chunk_gap", _as_float, 2.5),
    ("chunk_max_span", _as_float, 7.0),
    ("agent_from", _as_str, ""),
    ("diarize_on", _as_str, "mix"),
]

# 桌面/Web 界面共用、但要走 convert(...) 参数而不是 ConvertOptions 的字段
META_FIELDS = ["title", "artists", "album", "author", "author_id",
               "songwriters", "translation", "translation_lang"]


def build_options(params: Dict[str, Any]):
    """把界面参数变成 ``ConvertOptions``。**延迟导入 pipeline**（很重）。"""
    from pipeline import ConvertOptions

    kw: Dict[str, Any] = {}
    for name, conv, default in _FIELDS:
        kw[name] = conv(params.get(name), default)
    return ConvertOptions(**kw)


def field_drift() -> Dict[str, List[str]]:
    """自检用：本模块的字段表与 ``ConvertOptions`` 是否还对得上。

    返回 ``{"missing": [...], "unknown": [...]}``。
    * ``missing`` = ConvertOptions 有、但**这里没映射**的字段
      （= 桌面版会悄悄用默认值，用户在命令行设了却"没生效"）
    * ``unknown``  = 这里写了、ConvertOptions 已经没有的字段（改名/删字段了）
    """
    from dataclasses import fields as _fields

    from pipeline import ConvertOptions
    real = {f.name for f in _fields(ConvertOptions)}
    mine = {name for name, _c, _d in _FIELDS}
    return {"missing": sorted(real - mine), "unknown": sorted(mine - real)}


def platform_ids(params: Dict[str, Any]) -> Dict[str, str]:
    """从参数里挑出各平台 ID。

    ⚠️ 键名**从 ``ttml_writer.META_KEYS`` 取，不要另抄一份** ——
    抄一份的下场是加新平台（酷我/酷狗）时只改了写器、入口忘了加，
    界面填了 ID 却写不进文件。
    """
    from ttml_writer import META_KEYS
    out: Dict[str, str] = {}
    for key in list(META_KEYS) + ["isrc"]:
        v = params.get(key)
        if v:
            out[key] = str(v).strip()
    return out


def artists_list(params: Dict[str, Any]) -> List[str]:
    raw = _as_str(params.get("artists"))
    return [a.strip() for a in raw.replace("、", ",").replace("；", ",")
            .replace(";", ",").split(",") if a.strip()]


def songwriters_list(params: Dict[str, Any]) -> List[str]:
    raw = _as_str(params.get("songwriters"))
    return [s.strip() for s in raw.replace("、", ",").split(",") if s.strip()]


def read_lyrics(path: str):
    """读歌词文件（带编码探测）。返回 ``(text, encoding, dirty)``。

    ⚠️ 中文 Windows 上的 ``.lrc`` 大量是 GBK，写死 utf-8 会让整个转换崩在
    ``UnicodeDecodeError: 0xce``。**任何读用户文本的地方都必须走 textread**。
    """
    import textread
    return textread.read_text_best_effort(path)


def read_translation(params: Dict[str, Any]) -> str:
    path = _as_str(params.get("translation"))
    if path and os.path.exists(path):
        import textread
        return textread.read_text(path)
    return ""


def validate(params: Dict[str, Any]) -> List[str]:
    """返回"不满足就不能开始"的问题清单（空列表 = 可以开始）。"""
    problems: List[str] = []
    audio = _as_str(params.get("audio"))
    lyrics = _as_str(params.get("lyrics"))
    if not audio:
        problems.append("没有选音频文件")
    elif not os.path.isfile(audio):
        problems.append(f"音频文件不存在：{audio}")
    if not lyrics:
        problems.append("没有选歌词文件（TXT 或 LRC）")
    elif not os.path.isfile(lyrics):
        problems.append(f"歌词文件不存在：{lyrics}")
    if lyrics:
        ext = os.path.splitext(lyrics)[1].lower()
        if ext not in (".txt", ".lrc", ".ttml"):
            problems.append(f"歌词文件格式是 {ext or '（无扩展名）'}，"
                            f"只支持 .txt / .lrc")
    return problems
