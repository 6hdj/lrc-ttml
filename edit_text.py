"""编辑台的**文本编辑**后端（纯逻辑，不碰 HTTP）。

需求来源：原始导入的歌词常**缺行、错字**，需要在人工精修里补。要支持：

* **行级**：在两行之间插入一行、删除一行、整行改文本；
* **字级**：改一个字、插一个字、删一个字；
* **和声**：给某行加/改/删背景人声（``x-bg``）文本。

## 三条不变量（每个操作都必须守住）

1. **层内不重叠、时间单调**：同一层（主唱 / 和声）的字按文本顺序时间递增，
   ``begin < end``。插入新字时**从相邻字里切一半**，绝不用"随便给个时间"——
   那会立刻产出 `字时间重叠` 的坏文件。
2. **`span ⊆ p`**：新字的区间必须落在行区间内；不够就把**行区间撑开**（铁律 #13：
   宁可让行尾长出去，也不把字压进一个装不下的窗口）。
3. **key 连续**：``itunes:key`` 由写入器按 ``doc.lines`` 顺序自动编号（L1..Ln），
   所以插行/删行**只要改列表顺序**就够了 —— 这里额外刷新 ``key``/``xml_id``
   只是为了让编辑台界面显示正确。

## 为什么不复用 normalize_timeline 来分配时间

``ttml_normalize`` 是**修坏时间**的（把重叠、越界拉回合法），它不做"新增内容的
时间该放哪"这种**创造性**决定。这里是新内容，必须先给出一个合理的初始时间。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ttml_model import (LAYER_BG, LAYER_MAIN, LAYER_TRANSLATION, EditDoc,
                        EditLine, EditWord)

# 新行在"下一行离得太近"时给的最小占位时长（秒）
MIN_LINE_SPAN = 0.50
# 新字在"相邻字短得没法切"时给的最小占位时长
MIN_WORD_SPAN = 0.05
# 插入行时，若后面没有下一行，默认往后留多长
TAIL_LINE_SPAN = 2.00


@dataclass
class TextEditError(Exception):
    """文本编辑的结构化错误（与 edit_api.EditError 同形，便于直接返回给前端）"""

    error: str
    where: str = ""
    detail: str = ""
    hint: str = ""
    code: int = 400

    def to_dict(self) -> dict:
        return {"error": self.error, "where": self.where,
                "detail": self.detail, "hint": self.hint, "code": self.code}


# ---------------------------------------------------------------- 基础工具
def layer_words(line: EditLine, layer: str) -> List[EditWord]:
    """该层的字，**按数组顺序**（= 文本顺序，不是时间顺序）"""
    if layer == LAYER_BG:
        return [w for w in line.words if w.layer == LAYER_BG or w.is_bg]
    if layer == LAYER_TRANSLATION:
        return [w for w in line.words if w.layer == LAYER_TRANSLATION]
    return [w for w in line.words if w.layer == LAYER_MAIN and not w.is_bg]


def line_by_key(doc: EditDoc, key: str) -> EditLine:
    line = doc.line_by_key(key)
    if line is None:
        raise TextEditError("找不到这一行", where=key,
                            hint="行可能已被删除或改过编号，刷新编辑台重试")
    return line


def rekey(doc: EditDoc) -> None:
    """刷新每行的 key/xml_id 为 L1..Ln（写入器本来就会按顺序编号，这里只为界面显示）"""
    for i, line in enumerate(doc.lines, 1):
        line.key = f"L{i}"
        if line.xml_id:
            line.xml_id = f"L{i}"


def _renumber(doc: EditDoc) -> None:
    rekey(doc)
    for line in doc.lines:
        line.reindex()


def _layer_span(line: EditLine, layer: str) -> Optional[Tuple[float, float]]:
    ws = layer_words(line, layer)
    if not ws:
        return None
    return (min(w.begin for w in ws), max(w.end for w in ws))


def _alloc_in_line(line: EditLine, index: int, layer: str,
                   count: int = 1) -> List[Tuple[float, float]]:
    """给"要插进 index 位置的 count 个字"分配时间槽。

    规则（保证不重叠、不越行）：
    1. 有**后一个字** → 从它前半段切（新字占前半，它顺延到后半）；
    2. 否则有**前一个字** → 从它后半段切；
    3. 都没有 → 从行首起给 ``MIN_WORD_SPAN``，不够就撑开行区间。
    """
    ws = layer_words(line, layer)
    nxt = ws[index] if 0 <= index < len(ws) else None
    prv = ws[index - 1] if index > 0 else None

    if nxt is not None and (nxt.end - nxt.begin) > 2 * MIN_WORD_SPAN:
        begin, end = nxt.begin, (nxt.begin + nxt.end) / 2.0
        nxt.begin = end
    elif prv is not None and (prv.end - prv.begin) > 2 * MIN_WORD_SPAN:
        begin, end = (prv.begin + prv.end) / 2.0, prv.end
        prv.end = begin
    else:
        begin = line.begin
        end = begin + MIN_WORD_SPAN * max(1, count)

    # 每个字等分这个槽
    step = (end - begin) / max(1, count)
    out = []
    for i in range(count):
        out.append((begin + i * step, begin + (i + 1) * step))
    if out and out[-1][1] > line.end:
        line.end = out[-1][1]              # 撑开行尾（铁律 #13）
    return out


def _make_words(text: str, slots: Sequence[Tuple[float, float]],
                layer: str) -> List[EditWord]:
    """把 text 拆成逐字 word，按 slots 给时间。字数多于槽位时按最后一个槽延续。"""
    chars = list(text)
    out: List[EditWord] = []
    last_end = slots[-1][1] if slots else 0.0
    for i, ch in enumerate(chars):
        if i < len(slots):
            b, e = slots[i]
        else:
            b, e = last_end, last_end + MIN_WORD_SPAN
            last_end = e
        out.append(EditWord(text=ch, begin=b, end=e,
                            is_bg=(layer == LAYER_BG), layer=layer,
                            matched=False,          # 人工新加的字没有 ASR 证据
                            prob=0.0))
    return out


# ---------------------------------------------------------------- 字级操作
def set_word_text(doc: EditDoc, key: str, layer: str, index: int,
                  text: str) -> EditLine:
    """改一个字的文本。**字数变了就在原区间内重新等分**（保持这一层的总时长不变）。"""
    line = line_by_key(doc, key)
    ws = layer_words(line, layer)
    if not (0 <= index < len(ws)):
        raise TextEditError("找不到这个字", where=f"{key} {layer}[{index}]",
                            hint="刷新编辑台再试")
    if not text:
        return delete_word(doc, key, layer, index)
    old = ws[index]
    span = (old.begin, old.end)
    pos = line.words.index(old)
    line.words.pop(pos)
    count = len(text)
    step = (span[1] - span[0]) / count
    fresh = []
    for i, ch in enumerate(text):
        fresh.append(EditWord(text=ch, begin=span[0] + i * step,
                              end=span[0] + (i + 1) * step,
                              is_bg=(layer == LAYER_BG), layer=layer,
                              matched=False))
    line.words[pos:pos] = fresh
    _renumber(doc)
    return line


def insert_word(doc: EditDoc, key: str, layer: str, index: int,
                text: str) -> EditLine:
    """在 ``index`` **之前**插入字（index == 层内字数则追加到末尾）"""
    line = line_by_key(doc, key)
    if not text:
        return line
    ws = layer_words(line, layer)
    index = max(0, min(index, len(ws)))
    slots = _alloc_in_line(line, index, layer, count=len(text))
    fresh = _make_words(text, slots, layer)
    anchor = ws[index] if index < len(ws) else (ws[-1] if ws else None)
    if anchor is not None:
        pos = line.words.index(anchor)
        line.words[pos:pos] = fresh
    else:
        line.words.extend(fresh)
    line.text = line.main_text()
    _renumber(doc)
    return line


def delete_word(doc: EditDoc, key: str, layer: str, index: int) -> EditLine:
    """删一个字。**腾出的时间给相邻字**（同层顺延，不留下空洞导致时间跳跃）"""
    line = line_by_key(doc, key)
    ws = layer_words(line, layer)
    if not (0 <= index < len(ws)):
        raise TextEditError("找不到这个字", where=f"{key} {layer}[{index}]")
    if len(ws) == 1 and layer == LAYER_MAIN:
        raise TextEditError("不能删掉整行的最后一个字",
                            where=key,
                            hint="整行不要了就删这一行（行右侧的 ✕）")
    if len(ws) == 1:
        remove_bg(doc, key)
        return line
    victim = ws[index]
    nxt = ws[index + 1] if index + 1 < len(ws) else None
    prv = ws[index - 1] if index > 0 else None
    if nxt is not None:
        nxt.begin = victim.begin          # 后一个字往前顶
    elif prv is not None:
        prv.end = victim.end              # 最后一个字：前一个字往后延
    line.words.remove(victim)
    line.text = line.main_text()
    _renumber(doc)
    return line


# ---------------------------------------------------------------- 行级操作
def set_line_text(doc: EditDoc, key: str, text: str, *,
                  layer: str = LAYER_MAIN) -> EditLine:
    """整行改文本：按该层**原有的时间跨度**重新等分。

    这是"错字整行重打"和"从别处粘一句进来"最常用的操作。
    """
    line = line_by_key(doc, key)
    if not text:
        raise TextEditError("整行文本不能为空", where=key,
                            hint="整行不要了就删这一行")
    span = _layer_span(line, layer)
    if span is None or span[1] - span[0] <= 0:
        begin = line.begin
        span = (begin, begin + MIN_WORD_SPAN * len(text))
    step = (span[1] - span[0]) / len(text)
    slots = [(span[0] + i * step, span[0] + (i + 1) * step) for i in range(len(text))]
    fresh = _make_words(text, slots, layer)
    keep = [w for w in line.words if w not in layer_words(line, layer)]
    line.words = (fresh + keep) if layer == LAYER_MAIN else (keep + fresh)
    if layer == LAYER_MAIN:
        line.text = "".join(w.text for w in fresh)
        if span[1] > line.end:
            line.end = span[1]
    _renumber(doc)
    return line


def insert_line(doc: EditDoc, after_key: str, text: str, *,
                layer: str = LAYER_MAIN, agent: Optional[str] = None) -> EditLine:
    """在 ``after_key`` 之后插入一行。

    ``after_key`` 传空串表示插到最前面。

    时间：优先用**上一行结束 → 下一行开始**之间的空隙；空隙不够就往后占
    ``MIN_LINE_SPAN``，**但绝不越过下一行的开始**（不制造重叠）。
    """
    if not text:
        raise TextEditError("新行文本不能为空", where=after_key)
    if after_key:
        prev = line_by_key(doc, after_key)
        idx = doc.lines.index(prev)
    else:
        idx = -1
        prev = None
    nxt = doc.lines[idx + 1] if idx + 1 < len(doc.lines) else None

    begin = prev.end if prev is not None else (nxt.begin if nxt is not None else 0.0)
    if nxt is not None:
        end = max(begin + MIN_WORD_SPAN, min(nxt.begin, begin + 60.0))
        if end <= begin:
            end = begin + MIN_LINE_SPAN       # 空隙为负（源文件本来就有问题）时的兜底
    else:
        end = begin + TAIL_LINE_SPAN

    new_agent = agent or (prev.agent if prev is not None else
                          (nxt.agent if nxt is not None else "v1"))
    line = EditLine(key="", text=text, agent=new_agent, begin=begin, end=end,
                    paragraph=prev.paragraph if prev is not None else 0)
    slots = []
    if text:
        step = (end - begin) / len(text)
        slots = [(begin + i * step, begin + (i + 1) * step) for i in range(len(text))]
    line.words = _make_words(text, slots, layer)
    doc.lines.insert(idx + 1, line)
    _renumber(doc)
    return line


def delete_line(doc: EditDoc, key: str) -> None:
    """删掉一行（key 会自动重编号）"""
    line = line_by_key(doc, key)
    if len(doc.lines) <= 1:
        raise TextEditError("至少要留一行", where=key,
                            hint="整首歌都空了就没法导出 TTML 了")
    doc.lines.remove(line)
    _renumber(doc)


# ---------------------------------------------------------------- 和声
def set_bg_text(doc: EditDoc, key: str, text: str) -> EditLine:
    """设置这一行的**背景人声**（``x-bg``）文本；传空串 = 删掉和声。

    时间：沿用原和声的跨度；原来没有和声时，接在主唱之后（不够就撑开行尾）。
    """
    line = line_by_key(doc, key)
    if not text:
        remove_bg(doc, key)
        return line
    span = _layer_span(line, LAYER_BG)
    if span is None or span[1] - span[0] <= 0:
        main = _layer_span(line, LAYER_MAIN)
        begin = main[1] if main else line.begin
        span = (begin, begin + MIN_WORD_SPAN * len(text))
    step = (span[1] - span[0]) / len(text)
    slots = [(span[0] + i * step, span[0] + (i + 1) * step) for i in range(len(text))]
    fresh = _make_words(text, slots, LAYER_BG)
    for w in fresh:
        w.is_bg = True
        w.layer = LAYER_BG
    keep = [w for w in line.words if not (w.layer == LAYER_BG or w.is_bg)]
    line.words = fresh + keep          # 写入器会按时间决定 x-bg 放前还是放后
    if span[1] > line.end:
        line.end = span[1]
    _renumber(doc)
    return line


def remove_bg(doc: EditDoc, key: str) -> EditLine:
    """删掉这一行的和声层"""
    line = line_by_key(doc, key)
    line.words = [w for w in line.words if not (w.layer == LAYER_BG or w.is_bg)]
    main = _layer_span(line, LAYER_MAIN)
    if main and line.end > main[1]:
        line.end = main[1]             # 和声撑开的行尾收回来
    _renumber(doc)
    return line


# ---------------------------------------------------------------- 统一入口
def apply_op(doc: EditDoc, op: str, payload: dict) -> dict:
    """给 HTTP 层用的统一入口：``op`` 名字 + 参数 → 改 doc。

    返回 ``{"doc": ..., "touched": {...}}``；错误抛 ``TextEditError``。
    """
    p = payload or {}
    key = str(p.get("key") or "")
    layer = str(p.get("layer") or LAYER_MAIN)
    if layer not in (LAYER_MAIN, LAYER_BG, LAYER_TRANSLATION):
        raise TextEditError("layer 不合法", detail=layer)

    if op == "set_word":
        set_word_text(doc, key, layer, int(p.get("index", 0)), str(p.get("text", "")))
    elif op == "insert_word":
        insert_word(doc, key, layer, int(p.get("index", 0)), str(p.get("text", "")))
    elif op == "delete_word":
        delete_word(doc, key, layer, int(p.get("index", 0)))
    elif op == "set_line_text":
        set_line_text(doc, key, str(p.get("text", "")), layer=layer)
    elif op == "insert_line":
        insert_line(doc, key, str(p.get("text", "")), layer=layer,
                    agent=(str(p["agent"]) if p.get("agent") else None))
    elif op == "delete_line":
        delete_line(doc, key)
    elif op == "set_bg":
        set_bg_text(doc, key, str(p.get("text", "")))
    elif op == "remove_bg":
        remove_bg(doc, key)
    else:
        raise TextEditError("不认识的操作", detail=op,
                            hint="支持 set_word/insert_word/delete_word/"
                                 "set_line_text/insert_line/delete_line/set_bg/remove_bg")
    return {"doc": doc.to_dict(), "stats": doc.stats()}
