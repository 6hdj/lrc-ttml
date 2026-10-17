"""长音尾部修复的 **opt-in 同歌手跨行重叠** —— 离线回归测试（合成音频，不碰真曲）。

背景
----
`tail_extend.extend_line_tails(..., allow_same_agent_overlap=True)` 是本轮新增的
实验开关：默认（False）时同歌手行尾**绝不越过下一行 begin**（TTML 同一演唱者
`<p>` 不许重叠）；打开后，**只在音高判据支持时**（同歌手 opt-in 已**禁止能量
兜底** —— 能量分不清"还在拖音"和"下一句已起唱"，L56 那种"音高已判结束、能量
仍把尾音拖进下一句"必须被挡住）允许尾字越过下一行 begin 最多
`cross_agent_max`（默认 1.5s），并在**实际越界时**给这一对"边"打上
`line.overlap_with_next = True` 标记。`overlap` 语义统一为"**实际**越过
`nxt.begin`"（同歌手/跨歌手同一规则），`summarize()` 的计数因此只反映真实越界。
下游有两个消费者必须尊重这个边级标记：

1. `pipeline._enforce_agent_monotonic`：只对 `edge_ok = (prev 行号 == i-1
   and prev 行 marker)` 的**相邻**边豁免；非相邻同 agent 行（中间隔着别的
   agent）仍必须收紧 —— 否则"边级证据"会泄漏成"整行随便重叠"。
2. `matcher._fix_cross_line_overlaps`：marker 判断必须在 `overlap_pair()`
   **之前**。因为 `overlap_pair()` 是**就地改 units** 的（不是纯查询），
   先调它再 `continue` 已经把尾字 end 压回去了，行区间看起来没动、字级证据
   却被吃掉 —— 这正是本轮修掉的真 bug。

本文件钉死以上语义。**所有断言都能被变异测试真正抓到**（见
`tools/mutation_check.py` 里 `tail-overlap-m1..m5` 五条）。

    python tools/test_tail_overlap_optin.py        # 退出码 0 = 全过

⚠️ 合成音频的设计约束（别改成随便的正弦）：
* **同歌手 opt-in 的延长必须用 `use_pitch=True` + 一段持续 220Hz 正弦**：
  收紧后同歌手只接受音高判据（`sustain_end`），纯能量（`use_pitch=False`）
  一律不延长（见 [10]）。
* 跨歌手 / 末行 / 默认路径仍可能走能量判据，所以那些用例保留 `use_pitch=False`
  （确定性、不依赖 f0 的 0.01s hop）。
* `use_pitch=False` 时 `short_thresh` 生效：尾字必须 < 0.30s 才会进入延伸逻辑；
  `use_pitch=True` 时不受这个门槛限制（音高能分清"拖音"和"起唱"）。
* `line_rms` 取 [line.begin, line.end] 的中位能量，必须 > 1e-6；
* 人声"持续音"要一直响到 `nxt.begin + 1.5s` 之后，才能验到上限截断
  （否则测出来的是"能量先掉"，不是上限）。
"""
from __future__ import annotations

import ast
import inspect
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

try:                                    # 变异检查器按 UTF-8 解子进程输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                       # noqa: BLE001 - 老解释器/被重定向时无所谓
    pass

import numpy as np                                            # noqa: E402

import tail_extend                                            # noqa: E402
from matcher import AlignedLine, LyricMatcher, Unit           # noqa: E402
from pipeline import ConvertOptions, LyricToTTML              # noqa: E402

SR = 16000
TOTAL = 0
FAILS = []


def ok(name: str, cond: bool, detail="") -> None:
    global TOTAL
    TOTAL += 1
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"  <- {detail}" if detail != "" else ""))
        FAILS.append(name)


# ---------------------------------------------------------------- 合成数据工具
def tone(duration: float = 12.0, spans=((1.0, 6.0),), amp: float = 0.35) -> np.ndarray:
    """静音底 + 若干段 220Hz 正弦（模拟"连着的人声"）。"""
    audio = np.zeros(int(duration * SR), dtype=np.float32)
    for a, b in spans:
        n = int((b - a) * SR)
        t = np.arange(n) / SR
        audio[int(a * SR):int(a * SR) + n] = amp * np.sin(2 * np.pi * 220.0 * t)
    return audio


def mk_line(text: str, agent: str, begin: float, end: float, words) -> AlignedLine:
    units = [Unit(core=ch, index=i, start=s, end=e, matched=True)
             for i, (ch, s, e) in enumerate(words)]
    return AlignedLine(text=text, agent=agent, units=units, begin=begin, end=end)


def mk_pair(next_begin: float = 2.0, next_end: float = 3.0,
            agent2: str = "v1"):
    """L0：begin=1.0、尾字 1.9~2.0（0.1s，短到会进延伸逻辑）；L1：从 next_begin 开始。"""
    l1 = mk_line("拖长音", "v1", 1.0, 2.0,
                 [("拖", 1.2, 1.5), ("长", 1.5, 1.9), ("音", 1.9, 2.0)])
    l2 = mk_line("下一句", agent2, next_begin, next_end,
                 [("下", next_begin, next_begin + 0.2),
                  ("一", next_begin + 0.2, next_begin + 0.4),
                  ("句", next_begin + 0.4, next_begin + 0.5)])
    return l1, l2


def fix_of_line(fixes, idx: int):
    return [f for f in fixes if f.get("line") == idx]


MUTED_LOG = lambda *_a, **_k: None      # noqa: E731 - 测试里不需要日志

# ============================================================================
print("[1] 同歌手且 next.begin == 尾字 end：默认不延长/opt-in 延长且打 marker")
audio = tone()
l1, l2 = mk_pair()
fixes = tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=True)
f0 = fix_of_line(fixes, 0)
ok("默认：不产生 L0 尾音改动", not f0, f0)
ok("默认：L0 行尾保持 2.0", abs(l1.end - 2.0) < 1e-9, l1.end)
ok("默认：不设 overlap_with_next", getattr(l1, "overlap_with_next", None) is False)
ok("默认：不写 overlap_next_begin", getattr(l1, "overlap_next_begin", None) is None)

l1, l2 = mk_pair()
fixes = tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=True,
                                      allow_same_agent_overlap=True)
f0 = fix_of_line(fixes, 0)
ok("opt-in：产生 L0 尾音改动", bool(f0), f0)
ok("opt-in：L0 行尾越过 next.begin", bool(f0) and l1.end > 2.0, l1.end)
ok("opt-in：L0 行尾 = next.begin + 1.5 上限内（3.5）",
   abs(l1.end - 3.5) < 0.02, l1.end)
ok("opt-in：fix 记录 overlap=True", bool(f0) and f0[0].get("overlap") is True, f0)
ok("opt-in：overlap_with_next 为真", getattr(l1, "overlap_with_next", False) is True)
ok("opt-in：overlap_next_begin 记录为 2.0",
   abs(getattr(l1, "overlap_next_begin", -1.0) - 2.0) < 1e-9,
   getattr(l1, "overlap_next_begin", "MISSING"))
ok("opt-in：尾字 end 与行尾一致",
   abs(l1.units[-1].end - l1.end) < 1e-9, (l1.units[-1].end, l1.end))

# ============================================================================
print("\n[2] 下一行很远：opt-in 仍延长，但 marker 必须为 False（仅实际越界才置真）")
l1, l2 = mk_pair(next_begin=10.0, next_end=11.0)
fixes = tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=True,
                                      allow_same_agent_overlap=True)
f0 = fix_of_line(fixes, 0)
ok("远距离：opt-in 仍然延长", bool(f0) and l1.end > 2.0, l1.end)
ok("远距离：没有越过 next.begin", l1.end < 10.0, l1.end)
ok("远距离：overlap_with_next 必须为 False",
   getattr(l1, "overlap_with_next", None) is False,
   getattr(l1, "overlap_with_next", "MISSING"))
ok("远距离：不写 overlap_next_begin", getattr(l1, "overlap_next_begin", None) is None,
   getattr(l1, "overlap_next_begin", "MISSING"))
ok("远距离：fix 记录 overlap=False", bool(f0) and f0[0].get("overlap") is False, f0)

# ============================================================================
print("\n[3] 上限：new_end 不得超过 next.begin + cross_agent_max")
long_audio = tone(spans=((1.0, 11.0),))        # 能量足够长，测到的只能是上限
l1, l2 = mk_pair(next_begin=2.0, next_end=3.0)
tail_extend.extend_line_tails([l1, l2], long_audio, sr=SR, use_pitch=True,
                              allow_same_agent_overlap=True)
ok("默认上限 1.5：new_end <= 3.5", l1.end <= 3.5 + 1e-9, l1.end)
ok("默认上限 1.5：恰好被截到 3.5（说明是上限在起作用，不是能量先掉）",
   abs(l1.end - 3.5) < 0.02, l1.end)

l1, l2 = mk_pair(next_begin=2.0, next_end=3.0)
tail_extend.extend_line_tails([l1, l2], long_audio, sr=SR, use_pitch=True,
                              allow_same_agent_overlap=True, cross_agent_max=0.4)
ok("cross_agent_max=0.4：new_end <= 2.4", l1.end <= 2.4 + 1e-9, l1.end)
ok("cross_agent_max=0.4：恰好 2.4", abs(l1.end - 2.4) < 0.02, l1.end)

# ============================================================================
print("\n[4] 不同 agent：行为不变（跨歌手叠唱保持旧语义，marker 仍为真）")
results = {}
for flag in (False, True):
    l1, l2 = mk_pair(next_begin=2.0, next_end=2.5, agent2="v2")
    fixes = tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=False,
                                          allow_same_agent_overlap=flag)
    f0 = fix_of_line(fixes, 0)
    results[flag] = l1.end
    ok(f"跨歌手 flag={flag}：延长到 next.end=2.5", abs(l1.end - 2.5) < 1e-9, l1.end)
    ok(f"跨歌手 flag={flag}：overlap_with_next 为真",
       getattr(l1, "overlap_with_next", False) is True)
    ok(f"跨歌手 flag={flag}：fix 记录 overlap=True",
       bool(f0) and f0[0].get("overlap") is True, f0)
ok("跨歌手：开关不改变结果（行为不变）", results[False] == results[True], results)

# 4b. overlap 语义已统一为"**实际**越过 nxt.begin"（同歌手/跨歌手同一规则）：
# 旧实现里跨歌手分支无条件 overlap=True，即使延伸停在下一行之前也会被计数。
l1, l2 = mk_pair(next_begin=10.0, next_end=11.0, agent2="v2")
fixes = tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=False,
                                      allow_same_agent_overlap=True)
f0 = fix_of_line(fixes, 0)
ok("跨歌手远距离：确实延长", bool(f0) and l1.end > 2.0, l1.end)
ok("跨歌手远距离：没有越过 next.begin", l1.end < 10.0, l1.end)
ok("跨歌手远距离：fix 记录 overlap=False（不再无条件 True）",
   bool(f0) and f0[0].get("overlap") is False, f0)
ok("跨歌手远距离：overlap_with_next 为 False",
   getattr(l1, "overlap_with_next", None) is False,
   getattr(l1, "overlap_with_next", "MISSING"))

# ============================================================================
print("\n[5] 重入/陈旧 marker：调用前手动置 True + 全静音 ⇒ 必须复位为 False")
silent = np.zeros(int(6 * SR), dtype=np.float32)

# 5a. opt-in 真的置真之后，再静音重跑：必须清掉陈旧的 True
l1, l2 = mk_pair()
tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=True,
                              allow_same_agent_overlap=True)
ok("重入：opt-in 之后 marker=True（前置条件成立）",
   getattr(l1, "overlap_with_next", False) is True)
fixes = tail_extend.extend_line_tails([l1, l2], silent, sr=SR, use_pitch=True,
                                      allow_same_agent_overlap=True)
ok("重入：静音重跑后 marker=False（陈旧标记被复位）",
   getattr(l1, "overlap_with_next", None) is False,
   getattr(l1, "overlap_with_next", "MISSING"))
ok("重入：静音重跑不产生改动", not fixes, fixes)

# 5b. 首跑就手工塞一个 True（模拟上一轮/外部写入的陈旧值）
l1, l2 = mk_pair()
l1.overlap_with_next = True
l1.overlap_next_begin = 1.0
tail_extend.extend_line_tails([l1, l2], silent, sr=SR, use_pitch=False)
ok("手工预置 True：静音调用后仍复位为 False",
   getattr(l1, "overlap_with_next", None) is False,
   getattr(l1, "overlap_with_next", "MISSING"))

# ============================================================================
print("\n[6] 消费者豁免只在相邻边生效（_enforce_agent_monotonic）")


class _Recorder:
    def __init__(self):
        self.lines = []

    def log(self, msg):
        self.lines.append(msg)


def enforce(lines):
    notes = []
    n = LyricToTTML._enforce_agent_monotonic(_Recorder(), lines, notes)
    return n, notes


def unit_line(agent, begin, end, units, marker=None):
    line = mk_line("句", agent, begin, end, units)
    if marker is not None:
        line.overlap_with_next = marker
    return line


# 6a. 相邻 + marker=True ⇒ 豁免（不收紧）
a = unit_line("v1", 4.0, 5.0, [("发", 4.0, 4.2), ("么", 4.8, 5.0)], marker=True)
b = unit_line("v1", 4.5, 4.9, [("句", 4.5, 4.9)])
fixed, _notes = enforce([a, b])
ok("相邻边 + marker：不收紧（fixed=0）", fixed == 0, fixed)
ok("相邻边 + marker：前一行行尾保持 5.0", abs(a.end - 5.0) < 1e-9, a.end)

# 6b. 相邻但没有 marker ⇒ 必须收紧（marker 是必要条件，不是"相邻就豁免"）
a = unit_line("v1", 4.0, 5.0, [("发", 4.0, 4.2), ("么", 4.8, 5.0)], marker=False)
b = unit_line("v1", 4.5, 4.9, [("句", 4.5, 4.9)])
fixed, _notes = enforce([a, b])
ok("相邻边无 marker：收紧（fixed=1）", fixed == 1, fixed)
ok("相邻边无 marker：前一行行尾压到 4.5", abs(a.end - 4.5) < 1e-9, a.end)

# 6c. 非相邻（中间隔着 v2）+ marker=True ⇒ 仍必须收紧（边级证据不许泄漏）
a = unit_line("v1", 4.0, 5.0, [("发", 4.0, 4.2), ("么", 4.8, 5.0)], marker=True)
mid = unit_line("v2", 4.5, 4.9, [("间", 4.5, 4.9)])
c = unit_line("v1", 4.6, 5.2, [("后", 4.6, 5.0)])
fixed, _notes = enforce([a, mid, c])
ok("非相邻同 agent + marker：仍收紧（fixed=1）", fixed == 1, fixed)
ok("非相邻同 agent + marker：前一行行尾压到 4.6", abs(a.end - 4.6) < 1e-9, a.end)
ok("非相邻同 agent + marker：后一行 begin 不动", abs(c.begin - 4.6) < 1e-9, c.begin)

# 6d. 不同 agent 重叠本来合法 ⇒ 不收紧
a = unit_line("v1", 4.0, 5.0, [("发", 4.0, 4.2), ("么", 4.8, 5.0)])
b = unit_line("v2", 4.5, 4.9, [("句", 4.5, 4.9)])
fixed, _notes = enforce([a, b])
ok("跨歌手重叠：不当作要修的重叠（fixed=0）", fixed == 0, fixed)
ok("跨歌手重叠：前一行行尾不动", abs(a.end - 5.0) < 1e-9, a.end)

# ============================================================================
print("\n[7] matcher._fix_cross_line_overlaps：marker 判断必须在 overlap_pair 之前")


def overlap_pair_fixture(marker):
    a = mk_line("发么", "v1", 4.0, 6.0, [("发", 4.0, 4.2), ("么", 5.8, 6.0)])
    b = mk_line("下一", "v1", 5.0, 5.5, [("下", 5.0, 5.2), ("一", 5.2, 5.4)])
    if marker is not None:
        a.overlap_with_next = marker
    return a, b


matcher = LyricMatcher(log=MUTED_LOG)

a, b = overlap_pair_fixture(True)
matcher._fix_cross_line_overlaps([a, b])
ok("marker=True：行尾不被压回（a.end 保持 6.0）", abs(a.end - 6.0) < 1e-9, a.end)
ok("marker=True：最后一个主唱字 end 不被 overlap_pair 就地压回",
   abs(a.units[-1].end - 6.0) < 1e-9, a.units[-1].end)

a, b = overlap_pair_fixture(False)
matcher._fix_cross_line_overlaps([a, b])
ok("marker=False：对照用例确实删减了行尾（a.end < 5.0）", a.end < 5.0, a.end)
ok("marker=False：对照用例最后一字在原地被压回（< 6.0）",
   a.units[-1].end < 6.0, a.units[-1].end)

# ============================================================================
print("\n[8] 默认零回归：默认值、参数映射、CLI、pipeline 传参链路")
from desktop.services import convert_params                     # noqa: E402

ok("ConvertOptions() 默认 allow_same_agent_overlap=False",
   ConvertOptions().allow_same_agent_overlap is False,
   ConvertOptions().allow_same_agent_overlap)
ok("build_options({}) 默认 False",
   convert_params.build_options({}).allow_same_agent_overlap is False)
ok("build_options 能把开关打开",
   convert_params.build_options(
       {"allow_same_agent_overlap": "1"}).allow_same_agent_overlap is True)
ok('field_drift() == {"missing": [], "unknown": []}',
   convert_params.field_drift() == {"missing": [], "unknown": []},
   convert_params.field_drift())

sig = inspect.signature(tail_extend.extend_line_tails).parameters
ok("extend_line_tails 有 allow_same_agent_overlap 形参",
   "allow_same_agent_overlap" in sig, list(sig))
ok("extend_line_tails 形参默认 False",
   sig.get("allow_same_agent_overlap") is not None
   and sig["allow_same_agent_overlap"].default is False,
   None if "allow_same_agent_overlap" not in sig else sig["allow_same_agent_overlap"].default)

# CLI 开关真实存在（argparse 的 % 格式化炸掉会在这里露出来）
proc = subprocess.run([sys.executable, "main.py", "--help"],
                      cwd=ROOT, capture_output=True, timeout=300)
help_text = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")
ok("main.py --help 退出码 0", proc.returncode == 0, proc.returncode)
ok("main.py --help 含 --allow-same-agent-overlap",
   "--allow-same-agent-overlap" in help_text)

# pipeline 必须把 self.opt.allow_same_agent_overlap 传给 extend_line_tails
# （结构性断言：这是"界面/CLI 能设但流水线永远传 False"这类死字段的守卫）
py_src = io.open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
tree = ast.parse(py_src)
calls = [n for n in ast.walk(tree)
         if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)
         and n.func.attr == "extend_line_tails"
         and isinstance(n.func.value, ast.Name)
         and n.func.value.id == "tail_extend"]
ok("pipeline 里恰好一处 tail_extend.extend_line_tails 调用", len(calls) == 1, len(calls))


def _is_self_opt_allow(node) -> bool:
    return (isinstance(node, ast.Attribute)
            and node.attr == "allow_same_agent_overlap"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "opt"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self")


if calls:
    kwargs = {k.arg: k.value for k in calls[0].keywords}
    ok("pipeline 把 allow_same_agent_overlap 作为关键字实参传入",
       "allow_same_agent_overlap" in kwargs, list(kwargs))
    ok("pipeline 传的是 self.opt.allow_same_agent_overlap（不是常量 False）",
       "allow_same_agent_overlap" in kwargs
       and _is_self_opt_allow(kwargs["allow_same_agent_overlap"]),
       ast.dump(kwargs["allow_same_agent_overlap"]) if "allow_same_agent_overlap" in kwargs else "MISSING")
else:
    ok("pipeline 把 allow_same_agent_overlap 作为关键字实参传入", False, "没找到调用")
    ok("pipeline 传的是 self.opt.allow_same_agent_overlap（不是常量 False）", False,
       "没找到调用")

# ============================================================================
print("\n[9] 集成：opt-in 实际补出的相邻重叠边，_enforce_agent_monotonic 必须不收紧")
# 这条专门守住"补了又被流水线自己压回去"——只断 line.end 不够（overlap_pair /
# 收紧都是就地改 units），必须同时断言 unit.end 逐个不变。
int_audio = tone(spans=((1.0, 6.0),))
l1, l2 = mk_pair()
fixes = tail_extend.extend_line_tails([l1, l2], int_audio, sr=SR, use_pitch=True,
                                      allow_same_agent_overlap=True)
ok("集成前置：L0 已被延长并实际越界（marker 为真）",
   l1.end > 2.0 and getattr(l1, "overlap_with_next", False) is True, l1.end)
unit_ends_before = [u.end for u in l1.units]
fixed, _notes = enforce([l1, l2])
ok("集成：被标记的相邻边不被收紧（fixed==0）", fixed == 0, fixed)
ok("集成：行尾保持延长值", abs(l1.end - unit_ends_before[-1]) < 1e-9, l1.end)
ok("集成：每个 unit.end 都不被压回（不是只保住 line.end）",
   [u.end for u in l1.units] == unit_ends_before,
   [(round(a, 3), round(b, 3)) for a, b in zip([u.end for u in l1.units], unit_ends_before)])

# 阴性对照：同一行去掉 marker 后，_enforce_agent_monotonic 必须收紧这条边，
# 并且**真的把 unit.end 改了** —— 证明上面的"不变"不是空断言。
l1_nomark, l2_nomark = mk_pair()
tail_extend.extend_line_tails([l1_nomark, l2_nomark], int_audio, sr=SR, use_pitch=True,
                              allow_same_agent_overlap=True)
l1_nomark.overlap_with_next = False
ends_before2 = [u.end for u in l1_nomark.units]
fixed2, _ = enforce([l1_nomark, l2_nomark])
ok("对照：marker=False 时同一对边被收紧（fixed==1）", fixed2 == 1, fixed2)
ok("对照：收紧了行尾（压到下一行 begin）", abs(l1_nomark.end - l2_nomark.begin) < 1e-9,
   l1_nomark.end)
ok("对照：unit.end 确实被改（证明断言能感知收紧）",
   [u.end for u in l1_nomark.units] != ends_before2,
   [(round(a, 3), round(b, 3)) for a, b in zip([u.end for u in l1_nomark.units], ends_before2)])

# ============================================================================
print("\n[10] 收紧：opt-in 同歌手只有能量证据 ⇒ 不延长（禁能量兜底）")
# long_audio 的能量一直持续到 11s，纯能量判据一定会想延伸；收紧后同歌手必须拒绝。
l1, l2 = mk_pair()
fixes = tail_extend.extend_line_tails([l1, l2], long_audio, sr=SR, use_pitch=False,
                                      allow_same_agent_overlap=True)
ok("同歌手 opt-in + 只有能量证据：L0 不延长", not fix_of_line(fixes, 0), fixes)
ok("同歌手 opt-in + 只有能量证据：L0 行尾不动", abs(l1.end - 2.0) < 1e-9, l1.end)
ok("同歌手 opt-in + 只有能量证据：不设 marker",
   getattr(l1, "overlap_with_next", None) is False,
   getattr(l1, "overlap_with_next", "MISSING"))
ok("同歌手 opt-in + 只有能量证据：不写 overlap_next_begin",
   getattr(l1, "overlap_next_begin", None) is None,
   getattr(l1, "overlap_next_begin", "MISSING"))

# 对照 1：默认路径（flag=False）同样只有能量证据 → 旧行为不变（本来就不越界）
l1, l2 = mk_pair()
fixes = tail_extend.extend_line_tails([l1, l2], long_audio, sr=SR, use_pitch=False)
ok("默认路径 + 只有能量证据：仍不越过下一行（旧行为逐位不变）",
   not fix_of_line(fixes, 0) and l1.end <= 2.0 + 1e-9, l1.end)

# 对照 2：跨歌手 + 只有能量证据 → 仍允许延伸（证明收紧**只限同歌手**）
l1, l2 = mk_pair(next_begin=2.0, next_end=3.0, agent2="v2")
fixes = tail_extend.extend_line_tails([l1, l2], long_audio, sr=SR, use_pitch=False,
                                      allow_same_agent_overlap=True)
ok("跨歌手 + 只有能量证据：仍延长（收紧仅限同歌手）",
   bool(fix_of_line(fixes, 0)) and l1.end > 2.0, l1.end)

# ============================================================================
print("\n[11] 收紧：音高已判结束、能量仍在 ⇒ opt-in 同歌手不延长（L56 场景）")
# 真实 L56 的判据组合：ext_p=0（音高在 +0.10s 已换音）+ ext_e 很大（下一句能量高）。
# 用打桩固定"证据"形态（音高=0 / 能量=2.0），断言分支选择本身。
_orig_sustain = tail_extend.sustain_end
_orig_energy = tail_extend.find_tail_end
try:
    tail_extend.sustain_end = lambda *a, **k: 0.0
    tail_extend.find_tail_end = lambda *a, **k: 2.0
    l1, l2 = mk_pair()
    fixes = tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=True,
                                          allow_same_agent_overlap=True)
    ok("L56 场景：音高判结束 + 能量仍在 ⇒ opt-in 同歌手不延长",
       not fix_of_line(fixes, 0) and abs(l1.end - 2.0) < 1e-9, l1.end)
    ok("L56 场景：不设 marker",
       getattr(l1, "overlap_with_next", None) is False,
       getattr(l1, "overlap_with_next", "MISSING"))

    l1, l2 = mk_pair(next_begin=2.0, next_end=3.0, agent2="v2")
    fixes = tail_extend.extend_line_tails([l1, l2], audio, sr=SR, use_pitch=True,
                                          allow_same_agent_overlap=True)
    ok("同样证据模式下跨歌手仍走能量（收紧仅限同歌手）",
       bool(fix_of_line(fixes, 0)) and l1.end > 2.0, l1.end)
finally:
    tail_extend.sustain_end = _orig_sustain
    tail_extend.find_tail_end = _orig_energy

# ============================================================================
print("\n" + "=" * 66)
if FAILS:
    print(f"通过 {TOTAL - len(FAILS)} / {TOTAL} 项")
    print(f"FAIL: {len(FAILS)} 项")
    for name in FAILS:
        print(f"  - {name}")
    print("=" * 66)
    sys.exit(1)
print(f"通过 {TOTAL} / {TOTAL} 项")
print("=" * 66)
sys.exit(0)
