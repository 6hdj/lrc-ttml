"""带编码探测的文本读取。

为什么需要：歌词/LRC 文件在中文 Windows 上大量是 **GBK/GB18030** 编码，
写死 ``encoding="utf-8-sig"`` 会直接抛
``UnicodeDecodeError: 'utf-8' codec can't decode byte 0xce ...``，
用户上传一个 GBK 的 .lrc 就整个转换失败——这是纯工程问题，不该让用户去转码。

策略（顺序很重要）：

1. **先看 BOM**：``utf-8-sig`` / ``utf-16`` / ``utf-32`` 有 BOM 就是铁证，直接采信。
   （无 BOM 的 utf-16 一律不猜——它几乎能把任意字节解成"看起来正常"的乱码。）
2. 再试 **严格 UTF-8**：能解开就是它（严格 UTF-8 通过是很强的证据）。
3. 再试 **gb18030**（GBK/GB2312 的超集），覆盖简体老文件。
4. 都不行 → ``utf-8 + errors="replace"`` 兜底，并把结果标成"可能损坏"。

⚠️ 已知边界：**Big5（繁体）会被 gb18030 静默误解成乱码**——gb18030 几乎不会抛异常，
所以"试到不抛就成了"这条路判不出它。大陆用户场景里 Big5 极少，
这里选择接受（真要支持就得上 charset-normalizer 之类的统计检测库）。
为了不让它无声无息，解出来之后会做一次**合理性检查**（见 ``_looks_broken``），
发现明显异常就把 ``dirty`` 标上，让调用方提示用户。
"""
from __future__ import annotations

import codecs
import os
from typing import Tuple

BOMS = [
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
]

# 合理字符集：ASCII 可打印 + 中文/日文/韩文 + 常见全角标点 + 空白
_REPLACEMENT = "\ufffd"


def _looks_broken(text: str) -> bool:
    """解出来的文本像不像乱码。

    判据（任一命中即认为可疑）：
    * 出现 U+FFFD（解码替换符）；
    * 出现除 \\n \\r \\t 之外的控制字符；
    * 可打印字符里"期望字符"（ASCII / CJK / 全角标点 / 常见符号）占比过低。
    """
    if not text:
        return False
    if _REPLACEMENT in text:
        return True
    bad_ctrl = sum(1 for ch in text
                   if ord(ch) < 32 and ch not in "\n\r\t")
    if bad_ctrl:
        return True
    printable = [ch for ch in text if not ch.isspace()]
    if not printable:
        return False
    good = 0
    for ch in printable:
        o = ord(ch)
        if 32 <= o < 127:                       # ASCII 可打印
            good += 1
        elif 0x2000 <= o <= 0x206F:             # 常用标点
            good += 1
        elif 0x3000 <= o <= 0x303F:             # CJK 标点
            good += 1
        elif 0x3040 <= o <= 0x30FF:             # 日文假名
            good += 1
        elif 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF:   # CJK 汉字
            good += 1
        elif 0xAC00 <= o <= 0xD7AF:             # 韩文
            good += 1
        elif 0xFF00 <= o <= 0xFFEF:             # 全角
            good += 1
        elif 0x2010 <= o <= 0x22FF or 0x2460 <= o <= 0x24FF:
            good += 1
    return (good / len(printable)) < 0.80


def read_text_with_encoding(path: str) -> Tuple[str, str]:
    """返回 (文本, 实际用的编码)。**不抛 UnicodeDecodeError**。"""
    raw = open(path, "rb").read()

    for bom, enc in BOMS:                       # 1) BOM 是铁证
        if raw.startswith(bom):
            try:
                return raw.decode(enc), enc
            except UnicodeDecodeError:
                break

    for enc in ("utf-8", "gb18030"):            # 2) 严格 UTF-8 → 3) 简体老编码
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def read_text(path: str) -> str:
    return read_text_with_encoding(path)[0]


def read_text_best_effort(path: str) -> Tuple[str, str, bool]:
    """返回 (文本, 编码, 是否可疑)。

    第三个值为真 = 内容可能已经损坏（解码替换符/控制字符/合理性过低），
    调用方应该提示用户，而不是闷着。
    """
    text, enc = read_text_with_encoding(path)
    dirty = enc.endswith("(replace)") or _looks_broken(text)
    return text, enc, dirty


def describe(path: str) -> str:
    """给日志用：``歌.lrc（GBK/GB18030）``"""
    try:
        _, enc = read_text_with_encoding(path)
    except OSError:
        return os.path.basename(path)
    nice = {"utf-8-sig": "UTF-8", "utf-8": "UTF-8", "gb18030": "GBK/GB18030",
            "utf-16": "UTF-16", "utf-32": "UTF-32"}.get(enc, enc)
    return f"{os.path.basename(path)}（{nice}）"
