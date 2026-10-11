"""编辑台后端逻辑（P1）。

设计原则：**这里全是纯函数**，不碰 HTTP，方便离线自测；
`web_server.py` 只做"取参 → 调这里 → 返回 JSON"。

分工（见 docs/竞品调研与优化方案.md §5.5）：
* ``load_doc``     读取编辑态：内存 → ``outputs/<id>.edit.json`` → 由 TTML 重建
* ``validate``     只算不写（供前端实时预览，前端节流 ≥300ms）
* ``apply_doc``    规范化 + 校验 + 原子落盘，并把最新 TTML 回给调用方

⚠️ **微调阶段绝不重跑 ASR**：这里全部是毫秒级操作。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any, Dict, Optional, Tuple

from ttml_check import check_ttml
from ttml_model import LAYER_BG, LAYER_MAIN, EditDoc
from ttml_normalize import normalize_timeline
from ttml_reader import TTMLParseError, doc_to_aligned, doc_to_ttml, parse_ttml

# 结构化错误契约（前端按这四个字段定位问题）
ERROR_FIELDS = ("error", "where", "detail", "hint")


class EditError(Exception):
    """带定位信息的错误：{error, where, detail, hint}"""

    def __init__(self, error: str, where: str = "", detail: str = "",
                 hint: str = "", code: int = 400):
        super().__init__(error)
        self.error = error
        self.where = where
        self.detail = detail
        self.hint = hint
        self.code = code

    def to_dict(self) -> dict:
        return {"error": self.error, "where": self.where,
                "detail": self.detail, "hint": self.hint, "code": self.code}


# --------------------------------------------------------------------- 读写
def atomic_write_text(path: str, text: str) -> None:
    """先写同目录临时文件再 os.replace —— 中途崩溃不会留下半截文件"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def doc_from_ttml(text: str) -> EditDoc:
    try:
        return parse_ttml(text)
    except TTMLParseError as e:
        raise EditError("TTML 解析失败", detail=str(e),
                        hint="确认文件是 UTF-8 的 TTML；也可以先跑 "
                             "python main.py --check-only <文件> 看结构问题") from e


def backfill_align_hints(doc: EditDoc, aligned) -> int:
    """把对齐报告里的 ``matched`` / ``prob`` / ``muted`` 补回编辑态。

    为什么需要
    ----------
    编辑台的颜色提示（红=插值补的字、黄=ASR 可信度低）读的是
    ``EditWord.matched`` / ``EditWord.prob``。但这两个字段**不存在于 TTML 文本里**，
    而 ``parse_ttml`` 只能写死 ``matched=True``、``prob=0``（ttml_reader.py 的
    ``_fill_words`` / ``_walk_collect``）。于是"从 TTML 文本重建编辑态"这条路
    必然把它们丢光 —— 界面上的红/黄**永远不会出现**，而"哪个字不可信"恰恰是
    微调阶段最该看到的信息。

    修法：TTML 文本仍然用来建文档骨架（它带着元数据、翻译、源文件写法），
    但**对齐信息从内存里的 ``result.report.lines`` 按层回填**，不再走文本往返。

    匹配依据（已在 16 份真实产物上验证 0 失配）：
    ``doc_to_aligned`` 会跳过翻译层、其余字**按文档顺序**逐一映射，
    且写器不会重排字序，所以"同层的第 i 个字"在两边一一对应。
    计数不一致时**保守跳过该行**（宁可不标颜色，也不能把错位的 prob 贴到别的字上）。

    返回成功回填的行数。
    """
    if not doc.lines or not aligned:
        return 0

    def layer_of_doc_word(w) -> str:
        return LAYER_BG if (w.layer == LAYER_BG or w.is_bg) else LAYER_MAIN

    def layer_of_unit(u) -> str:
        return LAYER_BG if u.is_bg else LAYER_MAIN

    filled = 0
    for line, al in zip(doc.lines, aligned):
        for want in (LAYER_MAIN, LAYER_BG):
            rw = [w for w in line.words if layer_of_doc_word(w) == want]
            lw = [u for u in al.units if layer_of_unit(u) == want]
            if len(rw) != len(lw):
                continue
            for dst, src in zip(rw, lw):
                dst.matched = bool(getattr(src, "matched", True))
                dst.prob = float(getattr(src, "prob", 0.0) or 0.0)
                dst.muted = bool(getattr(src, "muted", False))
        filled += 1
    return filled


def doc_from_dict(data: Any) -> EditDoc:
    if not isinstance(data, dict) or "lines" not in data:
        raise EditError("doc 结构不对", detail=f"收到 {type(data).__name__}",
                        hint="doc 必须是 EditDoc.to_dict() 的产物（含 lines 数组）")
    try:
        return EditDoc.from_dict(data)
    except Exception as e:
        raise EditError("doc 反序列化失败", detail=f"{type(e).__name__}: {e}",
                        hint="检查每个 word 的 start/end 是否为数字") from e


def load_doc(ttml_text: Optional[str], edit_path: Optional[str]
             ) -> Tuple[EditDoc, str]:
    """返回 (doc, source)；source ∈ memory/disk/draft。

    * disk：``outputs/<id>.edit.json`` 存在且能读 → 说明用户改过，优先用它的进度；
    * draft：文件损坏或不存在 → 由 TTML 重建（**不能 500**）；
    * memory：调用方已持有 doc（本函数只做磁盘与重建）。
    """
    if edit_path and os.path.exists(edit_path):
        try:
            with open(edit_path, encoding="utf-8") as f:
                data = json.load(f)
            return doc_from_dict(data.get("doc", data)), "disk"
        except EditError:
            pass                      # 损坏 → 降级重建，不抛
        except (OSError, ValueError):
            pass
    if not ttml_text:
        raise EditError("没有可编辑的内容", code=404,
                        hint="先生成一次 TTML，再进入编辑台")
    return doc_from_ttml(ttml_text), "draft"


def save_doc(doc: EditDoc, edit_path: str, ttml_path: Optional[str] = None) -> str:
    """原子落盘 ``.edit.json``；给了 ttml_path 就同步更新 TTML"""
    payload = {"doc": doc.to_dict(), "version": doc.version}
    atomic_write_text(edit_path, json.dumps(payload, ensure_ascii=False, indent=1))
    if ttml_path:
        atomic_write_text(ttml_path, _safe_ttml(doc))
    return edit_path


# ------------------------------------------------------------------- 规范化
def _safe_ttml(doc: EditDoc) -> str:
    """doc → TTML；空文档等异常必须变成结构化 400，不能漏成 500"""
    try:
        return doc_to_ttml(doc)
    except ValueError as e:
        raise EditError(
            "这份文档写不出 TTML", detail=str(e),
            hint="确认至少有一行带时间戳的歌词；空文件或只有 head 的文件无法导出"
        ) from e


def _report_dict(rep) -> dict:
    return {
        "fixes": [f.to_dict() for f in rep.fixes],
        "unresolved": [u.to_dict() for u in getattr(rep, "unresolved", [])],
        "summary": rep.summary(),
    }


def _check_dict(res) -> dict:
    return {
        "ok": res.ok,
        "errors": [e.__dict__ for e in res.errors],
        "warnings": [w.__dict__ for w in res.warnings],
        "summary": res.summary(),
    }


def validate(doc: EditDoc, policy: str = "auto") -> dict:
    """只算不写。**深拷贝**后规范化，绝不动调用方的 doc。

    返回值里额外带上**修复后的 doc**：前端拖完一个字要立刻显示"已自动修复"后的
    时间，否则界面还停在坏时间上（设计文档 §5.2 明确要求"提示已自动修复 1 处"
    而不是把红色错误丢给用户）。version 不变，因为它没落盘。
    """
    work = copy.deepcopy(doc)
    rep = normalize_timeline(work, policy=policy)
    res = check_ttml(_safe_ttml(work), strict=True)
    return {"normalize": _report_dict(rep), "check": _check_dict(res),
            "doc": work.to_dict(), "version": work.version, "stats": work.stats()}


def apply_doc(doc: EditDoc, edit_path: str, ttml_path: Optional[str] = None,
              policy: str = "auto") -> dict:
    """保存路径：**就地**规范化（自动修好时间轴）+ 校验 + 落盘。

    返回 {doc, version, normalize, check}；``doc`` 是修好之后的版本，
    前端要用它刷新界面（否则界面还显示着未修复的时间）。
    """
    rep = normalize_timeline(doc, policy=policy)
    ttml = _safe_ttml(doc)
    res = check_ttml(ttml, strict=True)
    doc.version = int(getattr(doc, "version", 0) or 0) + 1
    ttml = _safe_ttml(doc)           # version 变化不影响正文，但保持同源
    save_doc(doc, edit_path, ttml_path)
    return {"doc": doc.to_dict(), "version": doc.version, "ttml": ttml,
            "normalize": _report_dict(rep), "check": _check_dict(res),
            "stats": doc.stats()}


def check_version(current: Any, expected: Any) -> None:
    """乐观锁：拿**存储里的** version 和前端带来的比，不一致 → 409。

    ⚠️ 必须是"存储版本 vs 提交版本"，**不能**拿提交体里的 doc.version 自比
    （那样永远相等，锁形同虚设）。
    """
    if expected is None:
        return
    try:
        want = int(expected)
    except (TypeError, ValueError):
        raise EditError("version 不是整数", detail=repr(expected), code=400)
    cur = int(current or 0)
    if want != cur:
        raise EditError(
            "版本冲突：这份文档已被改动", where=f"version={want}",
            detail=f"当前 version={cur}",
            hint="重新加载一次再改（GET /api/edit）", code=409)


def stored_version(task_doc: Optional[EditDoc], edit_path: Optional[str]) -> int:
    """当前权威版本号：内存优先，其次磁盘 .edit.json，最后 0"""
    if task_doc is not None:
        return int(getattr(task_doc, "version", 0) or 0)
    if edit_path and os.path.exists(edit_path):
        try:
            with open(edit_path, encoding="utf-8") as f:
                return int(json.load(f).get("version", 0) or 0)
        except (OSError, ValueError):
            return 0
    return 0


# --------------------------------------------------------------------- 便捷
def validate_payload(body: dict) -> dict:
    """POST /api/validate 的入参：{doc} 或 {ttml}"""
    if body.get("doc"):
        doc = doc_from_dict(body["doc"])
    elif body.get("ttml"):
        doc = doc_from_ttml(str(body["ttml"]))
    else:
        raise EditError("既没给 doc 也没给 ttml",
                        hint="传 {doc: <EditDoc>} 或 {ttml: '<tt>…'} 之一")
    return validate(doc, policy=str(body.get("policy") or "auto"))


def edit_path_for(task_id: str, output_dir: str) -> str:
    safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_") or "unknown"
    return os.path.join(output_dir, f"{safe}.edit.json")
