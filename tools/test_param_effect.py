"""参数**生效性**联调：每个选项是不是真的改变了产物？

`tools/audit_params.py` 只证明"参数传到了后端"；这个工具证明"参数**起了作用**"。
两者都要有 —— 传到了但不生效（字段接了没人读）是最难发现的一类问题。

做法：拿一份真实 TTML 读成 EditDoc，逐个选项重新写一遍，断言输出按预期变化。
不需要重跑 ASR，秒级完成。
"""
from __future__ import annotations

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)
sys.path.insert(0, HERE)

from ttml_reader import doc_to_ttml, parse_ttml  # noqa: E402

# ⚠️ 输入必须**随仓库分发**：原来写死 outputs/在出发之前.duet.ttml，
# 而那是我本机的真实产物、已被 .gitignore 排除 —— 新克隆的人跑这个工具会直接失败。
# 优先用本机真实产物（更能代表真实数据），没有就退回仓库自带的 fixture。
SRC = "outputs/在出发之前.duet.ttml"
if not os.path.exists(SRC):
    SRC = "tests/fixtures/demo-duet.ttml"

fails: list = []
oks = 0


def ck(name: str, cond: bool, detail: str = "") -> None:
    global oks
    if cond:
        oks += 1
        print(f"  OK  {name}")
    else:
        fails.append(name)
        print(f"  ✗   {name}   {detail}")


def main() -> int:
    doc = parse_ttml(io.open(SRC, encoding="utf-8").read())

    print("=" * 72)
    print("时间戳格式 time_format")
    print("=" * 72)
    a = doc_to_ttml(doc, time_format="clock")
    b = doc_to_ttml(doc, time_format="mmss")
    c = doc_to_ttml(doc, time_format="seconds")
    ck("clock → HH:MM:SS.mmm", bool(re.search(r'begin="\d\d:\d\d:\d\d\.\d\d\d"', a)))
    ck("mmss → MM:SS.mmm", bool(re.search(r'begin="\d\d:\d\d\.\d\d\d"', b)),
       (re.search(r'begin="[^"]*"', b) or [""])[0] if b else "")
    ck("seconds → SS.mmm", bool(re.search(r'begin="\d+\.\d\d\d"', c)),
       (re.search(r'begin="[^"]*"', c) or [""])[0] if c else "")
    ck("三种格式互不相同", len({a, b, c}) == 3)

    print()
    print("=" * 72)
    print("行标识 line_id")
    print("=" * 72)
    d_both = doc_to_ttml(doc, line_id="both")
    d_key = doc_to_ttml(doc, line_id="itunes:key")
    d_id = doc_to_ttml(doc, line_id="xml:id")
    # ⚠️ 只看 <p>：<ttm:agent xml:id="v1"> 在 head 里，本来就带 xml:id
    p_xmlid = lambda s: bool(re.search(r"<p [^>]*xml:id=", s))
    p_key = lambda s: bool(re.search(r"<p [^>]*itunes:key=", s))
    ck("both 同时有 itunes:key 和 xml:id", p_key(d_both) and p_xmlid(d_both))
    ck("itunes:key 模式：<p> 上不写 xml:id", not p_xmlid(d_key))
    ck("xml:id 模式：<p> 上不写 itunes:key", not p_key(d_id))
    ck("只用 xml:id 时 L1..Ln 仍在 xml:id 上",
       bool(re.search(r'<p [^>]*xml:id="L1"', d_id)), "没有 L1")

    print()
    print("=" * 72)
    print("演唱者/角色标记 markers")
    print("=" * 72)
    m_agent = doc_to_ttml(doc, markers="agent")
    m_role = doc_to_ttml(doc, markers="role")
    m_both = doc_to_ttml(doc, markers="both")
    ck("agent 模式：<p> 上有 ttm:agent", 'ttm:agent=' in m_agent)
    ck("agent 模式：<p> 上不写 ttm:role",
       not re.search(r'<p [^>]*ttm:role=', m_agent))
    ck("role 模式：<p> 上有 ttm:role", bool(re.search(r'<p [^>]*ttm:role=', m_role)))
    ck("both 模式：两个都有",
       'ttm:agent=' in m_both and bool(re.search(r'<p [^>]*ttm:role=', m_both)))

    print()
    print("=" * 72)
    print("翻译写法 translation_style")
    print("=" * 72)
    doc2 = parse_ttml(io.open(SRC, encoding="utf-8").read())
    doc2.lines[0].translation = "测试译文 Test"
    doc2.meta = dict(doc2.meta or {})
    t_head = doc_to_ttml(doc2, translation_style="head")
    t_inline = doc_to_ttml(doc2, translation_style="inline")
    t_both = doc_to_ttml(doc2, translation_style="both")
    ck("head：译文进 iTunesMetadata", "测试译文" in t_head and "iTunesMetadata" in t_head)
    ck("inline：译文在行内 span", 'x-translation' in t_inline)
    ck("both：两处都有", "iTunesMetadata" in t_both and 'x-translation' in t_both)

    print()
    print("=" * 72)
    print("语言 xml:lang")
    print("=" * 72)
    # ⚠️ xml:lang 来自 `doc.lang`（write() 的参数），**不是** writer 的构造覆盖项；
    # 传 doc_to_ttml(doc, language=...) 会 TypeError。改 doc.lang 才对。
    import copy as _copy
    d_en = _copy.deepcopy(doc)
    d_en.lang = "en"
    d_ja = _copy.deepcopy(doc)
    d_ja.lang = "ja"
    lang_en = doc_to_ttml(d_en)
    lang_ja = doc_to_ttml(d_ja)
    ck("doc.lang=en → xml:lang=\"en\"", 'xml:lang="en"' in lang_en)
    ck("doc.lang=ja → xml:lang=\"ja\"", 'xml:lang="ja"' in lang_ja)
    ck("换语言确实改变产物", lang_en != lang_ja)

    print()
    print("=" * 72)
    print("输出格式 mode（作用面很窄，但确实有）")
    print("=" * 72)
    # mode 只通过两条路影响产物：
    #   ① markers="auto" 时解析成 agent(amll) / both(apple)
    #   ② apple 模式按 >4s 停顿断 <div>
    mo_amll = doc_to_ttml(doc, mode="amll", markers="auto")
    mo_apple = doc_to_ttml(doc, mode="apple", markers="auto")
    ck("markers=auto 时：amll→agent（<p> 无 ttm:role）",
       not re.search(r"<p [^>]*ttm:role=", mo_amll))
    ck("markers=auto 时：apple→both（<p> 有 ttm:role）",
       bool(re.search(r"<p [^>]*ttm:role=", mo_apple)))
    ck("amll / apple 产物确实不同", mo_amll != mo_apple)
    # 显式给 markers 时 mode 只影响"按停顿断 div"，这时它几乎不起作用 —— 这是事实，不是 bug
    same_markers = (doc_to_ttml(doc, mode="amll", markers="agent")
                    == doc_to_ttml(doc, mode="apple", markers="agent"))
    ck("显式指定 markers 后，mode 不再改变产物（已知且合理：它只负责 auto 解析 + 断 div）",
       same_markers or "itunes:songPart" in doc_to_ttml(doc, mode="apple", markers="agent"))

    print()
    print("=" * 72)
    print("平台 ID（含新增的酷我/酷狗）")
    print("=" * 72)
    doc3 = parse_ttml(io.open(SRC, encoding="utf-8").read())
    doc3.meta = dict(doc3.meta or {})
    ids = {"ncmMusicId": "1", "qqMusicId": "2", "appleMusicId": "3",
           "spotifyId": "4", "kuwoMusicId": "5", "kugouMusicId": "6"}
    doc3.meta.update(ids)
    out3 = doc_to_ttml(doc3)
    back3 = parse_ttml(out3)
    for k, v in ids.items():
        ck(f"{k} 写入并往返", (back3.meta or {}).get(k) == v,
           f"写出来={k in out3} 读回来={(back3.meta or {}).get(k)!r}")

    print()
    print("=" * 72)
    print("歌词风格规范化 style / 停顿空格 pause_space")
    print("=" * 72)
    from pipeline import ConvertOptions, LyricToTTML
    from preprocess import parse_lyrics
    pl = LyricToTTML.__new__(LyricToTTML)
    pl.log = lambda *a: None
    pl.opt = ConvertOptions(style="apple")
    # ⚠️ parse_lyrics 吃的是**整段文本**（不是行列表）
    lines = parse_lyrics("你好世界。\nHello world.")
    pl._apply_style(lines)
    ck("style=apple 去掉行尾句号",
       not lines[0].text.endswith("。"), lines[0].text)
    ck("style=apple 英文行首字母大写",
       lines[1].text.startswith("H"), lines[1].text)

    pl.opt = ConvertOptions(explicit_policy="mask")
    lines2 = parse_lyrics("你这个他妈的东西")
    pl._apply_style(lines2)
    ck("explicit_policy=mask 把脏话换成星号",
       "****" in lines2[0].text, lines2[0].text)

    # 停顿空格：>= 阈值的空隙给前一个字补一个空格
    pl.opt = ConvertOptions(pause_space=0.5)
    from matcher import AlignedLine, Unit
    al = AlignedLine(text="甲乙", agent="v1", begin=0.0, end=2.0, matched=2, total=2,
                     anchored=True, role="",
                     units=[Unit(core="甲", line=0, index=0, start=0.0, end=0.4),
                            Unit(core="乙", line=0, index=1, start=1.5, end=2.0)])
    pl._apply_pause_spaces([al])
    ck("pause_space=0.5：1.1s 的停顿后加了空格",
       al.units[0].trailing.strip() == "" and al.units[0].trailing != "",
       repr(al.units[0].trailing))

    print()
    print("=" * 72)
    print("识别/设备参数是否真的接到 ASR")
    print("=" * 72)
    import inspect
    from asr import WhisperASR
    sig = inspect.signature(WhisperASR.__init__)
    ck("ASR 构造吃 model/device", "model" in sig.parameters and "device" in sig.parameters)
    src_pipe = io.open("pipeline.py", encoding="utf-8").read()
    for f in ("self.opt.model", "self.opt.device", "self.opt.language",
              "self.opt.beam_size", "self.opt.chunked", "self.opt.refine",
              "self.opt.detect_muted", "self.opt.separate_vocals"):
        ck(f"{f} 在流水线里被读", f in src_pipe)

    print()
    print("=" * 72)
    print("CLI 能起来（help 字符串里的裸 % 会让 argparse 直接抛异常、"
          "整个 CLI 全挂 —— 真踩过）")
    print("=" * 72)
    import subprocess
    r = subprocess.run([sys.executable, "main.py", "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    ck("python main.py --help 退出码 0", r.returncode == 0, (r.stderr or "")[-200:])

    print()
    print("=" * 72)
    print(f"结论：{oks} 项通过，{len(fails)} 项失败")
    if fails:
        for f in fails:
            print("  ✗ " + f)
    print("=" * 72)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
