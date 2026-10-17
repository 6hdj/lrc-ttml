"""只读阶段快照（诊断基础设施）。**默认关闭**；仅 `L2T_SNAPSHOT_DIR` 显式启用。

设计约束（不可违背）：
  1. 只记录、**不参与**任何时间计算 / 分支判定 / 算法决策；
  2. 未启用时 `record()` 立即返回 —— 零开销、零行为差异；
  3. 任何写入异常一律吞掉，**绝不**中断或影响转换结果；
  4. 只写不读：生产链路没有任何模块读取快照文件。
时间单位统一为**秒（float）**，相对音频起点，未做任何平移。
"""
from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.Lock()
_ROOT = None
_RUN_ID = None
_PATH = None


def enabled() -> bool:
    """是否已启用快照（仅此判定，不产生副作用）。"""
    return _PATH is not None


def init(directory=None) -> bool:
    """按显式配置初始化；失败一律返回 False，不抛异常。"""
    global _ROOT, _RUN_ID, _PATH
    d = directory or os.environ.get("L2T_SNAPSHOT_DIR", "")
    if not d:
        _ROOT = _RUN_ID = _PATH = None
        return False
    try:
        run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + str(os.getpid())
        run_dir = os.path.join(str(d), run_id)
        os.makedirs(run_dir, exist_ok=True)
        _ROOT, _RUN_ID = str(d), run_id
        _PATH = os.path.join(run_dir, "stage_snapshots.jsonl")
        return True
    except Exception:
        _ROOT = _RUN_ID = _PATH = None
        return False


def _b(u):
    v = getattr(u, "begin", None)
    return getattr(u, "start", None) if v is None else v


def record(**fields):
    """写一行 JSONL。未启用时零开销；任何异常都吞掉。"""
    if _PATH is None:
        return
    fields.setdefault("run_id", _RUN_ID)
    fields.setdefault("unit", "秒(float)，相对音频起点，未做任何平移")
    try:
        line = json.dumps(fields, ensure_ascii=False, default=str)
        with _LOCK:
            with open(_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def dump_lines(stage, lines, *, note="", source="", function="", executed=True, skip_reason=""):
    """逐行逐字记录 begin/end（只读遍历，不修改任何字段）。"""
    if _PATH is None:
        return
    seq = 0
    for li, ln in enumerate(lines or [], 1):
        units = getattr(ln, "units", None) or getattr(ln, "words", None) or []
        for si, u in enumerate(units):
            b, e = _b(u), getattr(u, "end", None)
            record(case_id=None, stage=stage, line_id="L" + str(li), span_id=si,
                   text=getattr(u, "text", ""), seq_in_line=si, seq_global=(seq),
                   begin_after=(None if b is None else round(float(b), 3)),
                   end_after=(None if e is None else round(float(e), 3)),
                   duration_after=(None if (b is None or e is None) else round(float(e) - float(b), 3)),
                   line_begin_after=getattr(ln, "begin", None), line_end_after=getattr(ln, "end", None),
                   executed=executed, skipped_reason=skip_reason, reason=note,
                   source_file=source, function=function)
            seq += 1


def dump_words(stage, words, *, note="", source="", function="", executed=True, skip_reason=""):
    """逐词记录 ASR 原始时间（只读）。"""
    if _PATH is None:
        return
    for i, w in enumerate(words or []):
        b = getattr(w, "start", None)
        e = getattr(w, "end", None)
        record(case_id=None, stage=stage, line_id=None, span_id=i, text=getattr(w, "word", getattr(w, "text", "")),
               seq_in_line=i, seq_global=i,
               begin_after=(None if b is None else round(float(b), 3)),
               end_after=(None if e is None else round(float(e), 3)),
               duration_after=(None if (b is None or e is None) else round(float(e) - float(b), 3)),
               executed=executed, skipped_reason=skip_reason, reason=note,
               source_file=source, function=function)


def dump_units(stage, units, *, note="", source="", function="", executed=True, skip_reason=""):
    """逐单元记录 begin/end（只读遍历；不改动任何字段、不改对象身份）。"""
    if _PATH is None:
        return
    for si, u in enumerate(units or []):
        b, e = _b(u), getattr(u, "end", None)
        record(case_id=None, stage=stage, line_id=None, span_id=si,
               text=getattr(u, "core", getattr(u, "text", "")), seq_in_line=si, seq_global=si,
               begin_after=(None if b is None else round(float(b), 3)),
               end_after=(None if e is None else round(float(e), 3)),
               duration_after=(None if (b is None or e is None) else round(float(e) - float(b), 3)),
               matched=getattr(u, "matched", None), executed=executed,
               skipped_reason=skip_reason, reason=note, source_file=source, function=function)
