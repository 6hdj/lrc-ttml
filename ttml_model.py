"""
可编辑的 TTML 文档模型（编辑态）。

为什么单独一层
--------------
流水线里的 ``matcher.Unit`` / ``matcher.AlignedLine`` 是**对齐过程的中间产物**：
字段围着算法转（``leading``/``trailing``/``core``/``anchored``…），没有版本号、
没有脏标记、也不适合直接序列化给前端。

``EditDoc`` 是**给人改的**模型：一份 TTML 读进来就是一个 EditDoc，
改完再写回 TTML。它必须满足：

* **可序列化**（JSON 落盘 / 发给前端）——见 ``to_dict`` / ``from_dict``；
* **可往返**：``parse_ttml(write(doc)) == doc``（测试里锁死）；
* **字段名与 ``matcher`` 对齐**，这样 §5.2 的时间轴规范化纯函数
  （``ttml_normalize``）**同一份**既能服务生成链路、也能服务编辑链路。

时间字段用 ``begin`` / ``end``（与 TTML 属性同名）；同时提供 ``start`` 别名，
让共用算法可以同时吃 ``Unit`` 和 ``EditWord``。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# 层名：main=主唱 / bg=背景和声（x-bg）/ translation=翻译（x-translation）
LAYER_MAIN = "main"
LAYER_BG = "bg"
LAYER_TRANSLATION = "translation"


@dataclass
class EditWord:
    """一个可编辑的字/词（对应 TTML 里一个 ``<span>``）"""

    text: str = ""
    begin: float = 0.0
    end: float = 0.0
    is_bg: bool = False
    layer: str = LAYER_MAIN
    prob: float = 0.0          # 来自 Unit.prob：这字可信度低，UI 可标黄
    matched: bool = True       # 来自 Unit.matched：False = 插值补的，UI 可标灰
    muted: bool = False        # 被消音屏蔽（输出 ****）
    index: int = 0             # 所属层内的文本顺序（规范化算法依赖它，勿按时间排）

    # --- 与 matcher.Unit 的字段名对齐，便于共用同一套纯函数 ---
    @property
    def start(self) -> float:
        return self.begin

    @start.setter
    def start(self, v: float) -> None:
        self.begin = v

    @property
    def duration(self) -> float:
        return self.end - self.begin


@dataclass
class EditLine:
    """一行歌词（对应 TTML 里一个 ``<p>``）"""

    key: str = ""              # itunes:key，如 "L1"；check 要求 L1..Ln 连续
    xml_id: str = ""
    text: str = ""
    agent: str = "v1"
    role: str = ""             # x-duet-a / x-duet-b / x-narration（行级 role）
    translation: str = ""      # 头部 iTunesMetadata 里的翻译文本
    paragraph: int = 0         # 段落号（源歌词空行），用于分 <div>
    begin: float = 0.0
    end: float = 0.0
    words: List[EditWord] = field(default_factory=list)
    locked: bool = False       # 用户锁定：规范化不主动改这一行（只报告）

    # ------------------------------------------------------------------
    def __post_init__(self):
        self.reindex()

    def reindex(self) -> None:
        """按当前顺序重新编号每层内的 index"""
        for layer in (LAYER_MAIN, LAYER_BG, LAYER_TRANSLATION):
            for i, w in enumerate(w for w in self.words if w.layer == layer):
                w.index = i

    @property
    def main_words(self) -> List[EditWord]:
        return [w for w in self.words if w.layer == LAYER_MAIN and not w.is_bg]

    @property
    def bg_words(self) -> List[EditWord]:
        return [w for w in self.words if w.is_bg or w.layer == LAYER_BG]

    @property
    def translation_words(self) -> List[EditWord]:
        return [w for w in self.words if w.layer == LAYER_TRANSLATION]

    @property
    def all_text(self) -> str:
        """主唱 + 和声的可见文本（和声带括号，与写出的 TTML 一致）"""
        out = []
        for w in self.words:
            out.append(w.text)
        return "".join(out)

    def main_text(self) -> str:
        return "".join(w.text for w in self.main_words)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "key": self.key, "xml_id": self.xml_id, "text": self.text,
            "agent": self.agent, "role": self.role, "translation": self.translation,
            "paragraph": self.paragraph, "begin": self.begin, "end": self.end,
            "locked": self.locked,
            "words": [asdict(w) for w in self.words],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EditLine":
        words = [EditWord(**{k: v for k, v in w.items()
                             if k in EditWord.__dataclass_fields__})
                 for w in d.get("words", [])]
        return cls(
            key=d.get("key", ""), xml_id=d.get("xml_id", ""), text=d.get("text", ""),
            agent=d.get("agent", "v1"), role=d.get("role", ""),
            translation=d.get("translation", ""), paragraph=d.get("paragraph", 0),
            begin=d.get("begin", 0.0), end=d.get("end", 0.0),
            words=words, locked=d.get("locked", False),
        )


@dataclass
class EditDoc:
    """整份可编辑文档"""

    lines: List[EditLine] = field(default_factory=list)
    meta: Dict[str, object] = field(default_factory=dict)  # musicName/album/ncmMusicId…；artists 是列表
    agents: Dict[str, str] = field(default_factory=dict)   # agent_id -> 显示名
    agent_types: Dict[str, str] = field(default_factory=dict)  # agent_id -> person/group/other…
    declared_agents: List[str] = field(default_factory=list)   # head 里**显式声明**过的 agent id
    # （区别于 agents：后者连"正文用到但没声明"的兜底 id 也会包含）
    timing: str = "Word"
    lang: str = "zh-Hans"
    version: int = 0       # 乐观锁版本号（POST /api/edit 用）
    duration: Optional[float] = None   # body/@dur，回写时保持与源文件一致
    # parse_ttml 推断出的"源文件当初怎么写的"（mode/markers/line_id/time_format/
    # translation_style/song_part/label_sections）。回写时照原样来，
    # 否则编辑台保存会把用户没动过的写法改掉（例如删掉 x-duet-a/b 角色）。
    write_options: Dict[str, object] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def line_by_key(self, key: str) -> Optional[EditLine]:
        for line in self.lines:
            if line.key == key:
                return line
        return None

    def iter_words(self):
        """遍历所有 (行, 字)"""
        for line in self.lines:
            for w in line.words:
                yield line, w

    def stats(self) -> dict:
        words = [w for _, w in self.iter_words()]
        return {
            "lines": len(self.lines),
            "words": len(words),
            "bg_words": sum(1 for w in words if w.is_bg or w.layer == LAYER_BG),
            "unmatched": sum(1 for w in words if not w.matched),
        }

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": self.version, "lang": self.lang, "timing": self.timing,
            "duration": self.duration,
            "write_options": dict(self.write_options),
            "meta": dict(self.meta), "agents": dict(self.agents),
            "agent_types": dict(self.agent_types),
            "lines": [l.to_dict() for l in self.lines],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EditDoc":
        return cls(
            lines=[EditLine.from_dict(x) for x in d.get("lines", [])],
            meta=dict(d.get("meta", {})), agents=dict(d.get("agents", {})),
            agent_types=dict(d.get("agent_types", {})),
            timing=d.get("timing", "Word"), lang=d.get("lang", "zh-Hans"),
            version=int(d.get("version", 0)),
            duration=d.get("duration"),
            write_options=dict(d.get("write_options", {})),
        )

    def bump(self) -> int:
        self.version += 1
        return self.version
