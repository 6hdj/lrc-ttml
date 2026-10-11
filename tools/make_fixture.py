"""生成自检用的 TTML fixture（**不依赖音频、不依赖 outputs/**）。

为什么需要它：`selftest.py` 里有几组用例原本拿 ``outputs/*.ttml`` 当输入，
而那些文件是**真实歌曲的产物**，已随 `.gitignore` 排除、不会进开源仓库 ——
于是**新克隆的人跑自检会直接红**，而贡献指南恰恰让人一上手就跑自检。

这里用仓库自带的 `examples/sample.txt`（作者自撰、随项目 MIT 授权）
配一套**确定性**的合成时间轴，生成两份 fixture：

    tests/fixtures/demo.ttml        单人 + 和声（AMLL 模式）
    tests/fixtures/demo-duet.ttml   对唱 v1/v2/v3 + 和声 + 翻译（Apple 模式 + 角色）

用途：往返不变量、normalize 不该乱改、真实结构校验等。
**没有版权问题**，且生成结果可复现（同样的输入永远同样的字节）。

    python tools/make_fixture.py
"""
from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)
sys.path.insert(0, HERE)

from matcher import AlignedLine, Unit                    # noqa: E402
from preprocess import merge_background_lines, parse_lyrics  # noqa: E402
from ttml_writer import TTMLWriter                       # noqa: E402

OUT_DIR = "tests/fixtures"

# 逐字时长（秒）。固定值 → 输出确定，测试可依赖。
WORD_DUR = 0.32
GAP = 0.10          # 行内字与字之间的空隙
LINE_GAP = 1.60     # 行与行之间的间隔
START = 12.0


def build_lines(text: str, *, force_agent: str = "", translation: bool = False):
    """把示例歌词变成 AlignedLine[]，时间轴是**合成**的（与音频无关）。

    ⚠️ 和声字**不能插在主唱字中间**：那样主唱层会出现一段空洞，
    规范化会把它记成 ``[R7] 行内长停顿``（不自动修，只报告）——
    fixture 就不该带这种未解决项。真实叠唱里和声是**与主唱并行**的，
    所以这里让和声从该行中段起，用**自己的游标**走，主唱层保持连续。
    """
    src = merge_background_lines(parse_lyrics(text))
    out = []
    t = START
    for i, ln in enumerate(src):
        units = []
        main = [tk for tk in ln.tokens if not tk.is_background]
        bg = [tk for tk in ln.tokens if tk.is_background]
        step = WORD_DUR + GAP

        cur = t
        for k, tk in enumerate(main):
            units.append(Unit(core=tk.text, line=i, index=k, is_bg=False,
                              start=round(cur, 3), end=round(cur + WORD_DUR, 3),
                              prob=0.92, matched=True))
            cur += step
        main_end = cur - GAP if main else t

        # 和声：从主唱中段开始，与主唱**并行**推进（不占用主唱的时间轴）
        if bg:
            bcur = round(t + step * max(0, len(main) // 2), 3)
            for j, bt in enumerate(bg):
                units.append(Unit(core=bt.text, line=i, index=len(main) + j, is_bg=True,
                                  start=round(bcur, 3), end=round(bcur + WORD_DUR, 3),
                                  prob=0.80, matched=True))
                bcur += step
            line_end = max(main_end, round(bcur - GAP, 3))
        else:
            line_end = main_end

        if not units:                       # 理论上 merge 之后不会发生
            t = round(t + step, 3)
            continue
        out.append(AlignedLine(
            text=ln.text, agent=(force_agent or ln.agent), units=units,
            begin=round(t, 3), end=round(max(line_end, t + WORD_DUR), 3),
            matched=len(units), total=len(units), anchored=True,
            role=("x-narration" if ln.role == "x-narration" else
                  (ln.role or ("x-duet-a" if ln.agent == "v1" else "x-duet-b"))),
            translation=(f"[译文 {i + 1}]" if translation else ""),
            paragraph=ln.paragraph,
        ))
        t = round(max(line_end, t + WORD_DUR) + LINE_GAP, 3)
    return out


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    sample = io.open("examples/sample.txt", encoding="utf-8").read()

    # ① 单人 + 和声：AMLL 社区格式
    lines_a = build_lines(sample, force_agent="v1")
    w_a = TTMLWriter(mode="amll", markers="agent", line_id="both",
                     time_format="clock", translation_style="head")
    text_a = w_a.write(lines_a, title="示例歌曲 Sample Song", artists=["示例歌手"],
                       album="示例专辑 Sample Album", language="zh-Hans",
                       platform_ids={"ncmMusicId": "1000000", "qqMusicId": "2000000"},
                       author="HXJ", author_id="example",
                       duration=lines_a[-1].end + 2.0,
                       songwriters="示例作者")
    p_a = os.path.join(OUT_DIR, "demo.ttml")
    io.open(p_a, "w", encoding="utf-8", newline="\n").write(text_a)

    # ② 对唱 + 角色 + 翻译：Apple 提交格式
    lines_b = build_lines(sample, translation=True)
    w_b = TTMLWriter(mode="apple", markers="both", line_id="both",
                     time_format="clock", translation_style="head")
    text_b = w_b.write(lines_b, title="示例歌曲 Sample Song",
                       artists=["示例歌手 A", "示例歌手 B"],
                       album="示例专辑 Sample Album", language="zh-Hans",
                       platform_ids={"ncmMusicId": "1000000", "appleMusicId": "3000000",
                                     "kuwoMusicId": "4000000", "kugouMusicId": "5000000"},
                       author="HXJ", author_id="example",
                       duration=lines_b[-1].end + 2.0,
                       songwriters="示例作者")
    p_b = os.path.join(OUT_DIR, "demo-duet.ttml")
    io.open(p_b, "w", encoding="utf-8", newline="\n").write(text_b)

    for p in (p_a, p_b):
        n = len(io.open(p, encoding="utf-8").read())
        print(f"  生成 {p}  ({n} 字节)")

    # 自检：生成出来必须自己是合法的
    from ttml_check import check_ttml
    from ttml_reader import doc_to_ttml, parse_ttml
    ok = True
    for p in (p_a, p_b):
        t = io.open(p, encoding="utf-8").read()
        res = check_ttml(t, strict=True)
        rt = (doc_to_ttml(parse_ttml(t)) == t)
        agents = sorted(set(__import__("re").findall(r'ttm:agent="([^"]+)"', t)))
        print(f"  {p}: 错误 {len(res.errors)} / 往返一致 {rt} / agent {agents}")
        if res.errors:
            for e in res.errors[:3]:
                print(f"      - {e}")
        ok = ok and not res.errors and rt
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
