"""
Apple Music 歌词风格准则的规范化与检查。

对应《风格标准和准则 · 歌词准则》里**可以程序化**的部分：

1. 行尾不得使用句号或逗号（``! ? "`` 可以保留）
2. 英文每行第一个单词首字母大写；括号中第一个单词首字母大写
3. 消音/屏蔽的词语必须用星号替换整个词
4. 唱段之间分段（由 ``preprocess`` 记录段落 + 生成器分 ``<div>`` 实现）
5. 背景人声作为独立层（`ttm:role="x-bg"`），由 ``preprocess.merge_background_lines``
   并入相邻主唱行 —— 注意**不能**让整行只有和声（AMLL 解析后是空行、看不见）

无法程序化的部分（专有名词、神/宗教相关大写、品牌名、作品名、地理名、
口述内容、scatting 衬词、印度语系转写）不做改动，只在检查里提示。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Apple 示例里被消音的词统一用 4 个星号
MASK = "****"

# 行尾必须去掉的标点（句号/逗号，中英文）
TRAILING_PUNCT = ".,。，"
# 允许保留在行尾的标点
ALLOWED_TRAILING = "!?！？”\"'…~—"

_OPENERS = "([{（【《\"“‘"

# 常见脏话（英文按词匹配，中文按子串匹配）。只用于 --explicit-policy mask
PROFANITY_EN = {
    "fuck", "fucks", "fucked", "fucking", "fucker", "fuckers", "motherfucker",
    "motherfuckers", "motherfucking", "shit", "shits", "shitty", "bullshit",
    "bitch", "bitches", "ass", "asses", "asshole", "assholes", "damn", "goddamn",
    "dick", "dicks", "cock", "cocks", "pussy", "pussies", "cunt", "cunts",
    "bastard", "bastards", "whore", "whores", "slut", "sluts", "nigga", "niggas",
    "nigger", "niggers", "piss", "pissed", "crap",
}
PROFANITY_ZH = [
    "妈的", "妈逼", "妈的逼", "傻逼", "傻B", "煞笔", "操你", "草你", "日你",
    "干你", "滚蛋", "婊子", "贱人", "王八蛋", "狗娘养", "混账",
]


@dataclass
class StyleChange:
    line_no: int
    before: str
    after: str
    kind: str            # trailing-punct / capitalize / mask / muted


@dataclass
class StyleReport:
    changes: List[StyleChange] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.changes)

    def summary(self) -> str:
        if not self.changes:
            return "风格规范化：无需改动"
        by: Dict[str, int] = {}
        for c in self.changes:
            by[c.kind] = by.get(c.kind, 0) + 1
        label = {"trailing-punct": "行尾标点", "capitalize": "英文首字母大写",
                 "mask": "屏蔽词星号化", "muted": "消音词星号化"}
        parts = [f"{label.get(k, k)} {v} 处" for k, v in by.items()]
        return "风格规范化：" + "，".join(parts)

    def detail(self, limit: int = 12) -> List[str]:
        out = []
        for c in self.changes[:limit]:
            out.append(f"L{c.line_no} [{c.kind}] {c.before} → {c.after}")
        if len(self.changes) > limit:
            out.append(f"… 另有 {len(self.changes) - limit} 处")
        return out


# --------------------------------------------------------------------------
def strip_trailing_punct(text: str) -> str:
    """去掉行尾的句号/逗号（Apple：行尾不得使用句号或逗号）"""
    t = text.rstrip()
    while t and t[-1] in TRAILING_PUNCT:
        t = t[:-1].rstrip()
    return t


def capitalize_english(text: str) -> str:
    """英文每行第一个单词首字母大写；括号中第一个单词首字母大写。

    只做大写、不做小写，避免破坏 NASA / iPhone 这类写法。
    """
    chars = list(text)
    need_cap = True
    for i, ch in enumerate(chars):
        if need_cap and ("a" <= ch <= "z"):
            chars[i] = ch.upper()
            need_cap = False
            continue
        if need_cap and ("A" <= ch <= "Z"):
            need_cap = False
            continue
        if ch in _OPENERS:
            need_cap = True
            continue
        if ch.isspace() or ch in "-–—'’\"“”":
            # 空格/连字符/撇号不改状态：保证第一个真正的字母被大写
            continue
        need_cap = False
    return "".join(chars)


def has_english_letter(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text))


def mask_profanity(text: str) -> Tuple[str, int]:
    """把脏话替换成 ``****``（用于儿童适宜版本）"""
    count = 0

    def repl_en(m):
        nonlocal count
        count += 1
        return MASK

    out = re.sub(r"\b(" + "|".join(sorted(PROFANITY_EN, key=len, reverse=True)) + r")\b",
                 repl_en, text, flags=re.IGNORECASE)
    for w in PROFANITY_ZH:
        if w in out:
            count += out.count(w)
            out = out.replace(w, MASK)
    return out, count


def normalize_line(text: str, line_no: int, strip_punct: bool = True,
                   capitalize: bool = True, mask: bool = False,
                   report: Optional[StyleReport] = None) -> str:
    """对单行做风格规范化，并把改动记录到 report"""
    original = text
    cur = text
    changes: List[StyleChange] = []

    if strip_punct:
        new = strip_trailing_punct(cur)
        if new != cur:
            changes.append(StyleChange(line_no, cur, new, "trailing-punct"))
            cur = new

    if capitalize and has_english_letter(cur):
        new = capitalize_english(cur)
        if new != cur:
            changes.append(StyleChange(line_no, cur, new, "capitalize"))
            cur = new

    if mask:
        new, n = mask_profanity(cur)
        if n:
            changes.append(StyleChange(line_no, cur, new, "mask"))
            cur = new

    if report is not None:
        report.changes.extend(changes)
    return cur if cur else original


# --------------------------------------------------------------------------
# 检查（不改动，只报告哪些地方不符合准则）
# --------------------------------------------------------------------------
def lint_text(text: str) -> List[str]:
    """检查一句歌词文本是否符合 Apple 风格准则，返回问题列表"""
    issues: List[str] = []
    t = text.strip()
    if not t:
        return issues
    if t[-1] in TRAILING_PUNCT:
        issues.append(f"行尾不应使用句号/逗号：「{t[-6:]}」")
    if has_english_letter(t):
        first = next((c for c in t if c.isalpha()), "")
        if first and first.islower():
            issues.append(f"英文行首字母应大写：「{t[:12]}」")
        for m in re.finditer(r"[\(\[\{（【]([^\)\]\}）】]{0,20})", t):
            seg = m.group(1)
            f = next((c for c in seg if c.isalpha()), "")
            if f and f.islower():
                issues.append(f"括号内首词应大写：「{m.group(0)[:14]}」")
    if re.search(r"\b(" + "|".join(sorted(PROFANITY_EN, key=len, reverse=True)) + r")\b",
                 t, flags=re.IGNORECASE) or any(w in t for w in PROFANITY_ZH):
        issues.append("含屏蔽词：儿童适宜版本需以星号替换整个词")
    return issues


def style_preset(name: str) -> Dict[str, bool]:
    """风格预设：keep = 一字不改；apple = 应用可程序化的准则"""
    if name == "keep":
        return {"strip_punct": False, "capitalize": False, "mask": False}
    if name == "apple":
        return {"strip_punct": True, "capitalize": True, "mask": False}
    raise ValueError("style 必须是 'keep' 或 'apple'")
