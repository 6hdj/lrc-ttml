#!/usr/bin/env python3
"""
Lyric2TTML — 普通歌词 → Apple Music / AMLL TTML 逐字歌词

    python main.py --audio 歌曲.mp3 --lyrics 歌词.lrc --output 歌曲.ttml

时间轴来自真实音频（faster-whisper 语音识别 + 强制对齐），
全程离线，不需要 API Key。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 国内 HuggingFace 镜像 + 本地模型缓存目录
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(HERE, ".hf_cache"))

from pipeline import ConvertOptions, LyricToTTML  # noqa: E402
import textread  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lyric2ttml",
        description="把普通歌词（TXT/LRC）+ 歌曲音频转换成 Apple Music / AMLL 逐字 TTML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python main.py --audio song.flac --lyrics song.lrc --title \"歌名\" --artist \"歌手\"\n"
               "  python main.py --audio song.mp3 --lyrics lyrics.txt --mode apple -o apple.ttml\n"
               "  python main.py --check-only out.ttml\n",
    )
    p.add_argument("--audio", "-a", help="音频文件（mp3/wav/flac/m4a…）")
    p.add_argument("--lyrics", "-l", help="歌词文件（.lrc 或 .txt，UTF-8）")
    p.add_argument("--output", "-o", default=None, help="输出 TTML 路径（默认与歌词同名 .ttml）")
    p.add_argument("--mode", "-m", choices=["amll", "apple"], default="amll",
                   help="amll=AMLL 社区格式（默认，播放器直接用）；apple=Apple 官方提交格式")
    p.add_argument("--language", default="zh",
                   help="识别语言：auto=让 whisper 自己检测（中英混唱用它）/ zh / en / ja / ko。"
                        "⚠️ 界面默认是 auto；**命令行默认仍是 zh**，"
                        "为的是让评估脚本可复现（改默认值会动到 85.0%% 那条基线）")
    p.add_argument("--model", default="large-v3", help="whisper 模型（默认 large-v3，small 更快）")
    p.add_argument("--device", default="cuda", choices=["cuda", "auto"],
                   help="计算设备：cuda（默认，本项目强制用 GPU）/ auto（等价于 cuda）。"
                        "⚠️ 故意不提供 CPU —— 用 CPU 处理一首歌要几分钟，"
                        "静默降级只会让人觉得程序很慢，所以没有可用的显卡时直接明确报错")
    p.add_argument("--align-engine", choices=["whisper", "timing-tifa"], default="whisper",
                   help="对齐引擎：whisper=默认（faster-whisper + DP，支持纯文本）；"
                        "timing-tifa=实验（TIFA 歌声音素对齐，需要逐句 LRC + models/tifa 模型，"
                        "当前按中文普通话配置）")
    p.add_argument("--title", default="", help="歌曲名")
    p.add_argument("--artist", action="append", default=[], help="歌手（可重复）")
    p.add_argument("--album", default="", help="专辑名")
    p.add_argument("--author", default="", help="TTML 制作者（GitHub ID）")
    p.add_argument("--ncm-id", default="", help="网易云音乐 ID")
    p.add_argument("--qq-id", default="", help="QQ 音乐 ID")
    p.add_argument("--apple-id", default="", help="Apple Music ID")
    p.add_argument("--spotify-id", default="", help="Spotify ID")
    p.add_argument("--isrc", default="", help="ISRC")
    p.add_argument("--pause-space", type=float, default=0.0,
                   help="停顿超过该秒数时在字后插入空格（AMLL 用它表示停顿，默认关闭）")
    p.add_argument("--no-refine", action="store_true", help="关闭低匹配率行的二次精修")
    p.add_argument("--no-chunked", action="store_true",
                   help="整曲一次识别（默认按乐句切片，更准但稍慢）")
    p.add_argument("--keep-credits", action="store_true", help="保留「作词/作曲」等制作人员行")
    p.add_argument("--translation", metavar="TXT",
                   help="翻译/音译文件（每行对应一句歌词）")
    p.add_argument("--translation-lang", default="", help="翻译语言代码（BCP-47，如 zh-Hans / en）")
    p.add_argument("--translation-style", choices=["head", "inline", "both"], default="head",
                   help="翻译写法：head=Apple Music 样式的 <iTunesMetadata>（AMLL 规范首选，默认）；"
                        "inline=行内 <span ttm:role=\"x-translation\">")
    p.add_argument("--songwriter", action="append", default=[], help="词曲作者（可重复，写入 <songwriters>）")
    p.add_argument("--markers", choices=["auto", "agent", "role", "both"], default="auto",
                   help="演唱者标记：auto=按 mode 决定（amll→agent，apple→both，默认）；"
                        "agent=只用 ttm:agent（AMLL 规范内）；"
                        "role/both=再加 Apple 内部扩展 ttm:role=\"x-duet-a/b\" / x-narration。"
                        "⚠️ 提交 Apple Music 的对唱要写 x-duet-a/b，所以 apple 模式默认 both")
    p.add_argument("--line-id", choices=["itunes:key", "xml:id", "both"], default="both",
                   help="行标识写法（默认 itunes:key 与 xml:id 都写）")
    p.add_argument("--agent-from", default="", metavar="REF.ttml",
                   help="从一份已有 TTML（如 AMLL TTML DB 里人工标注过的版本）复用每行的"
                        "演唱者归属。声纹对音色接近的对唱实测分不开，社区已有标注时用这个最可靠")
    p.add_argument("--time-format", choices=["clock", "mmss", "seconds", "auto"], default="clock",
                   help="时间戳格式：clock=HH:MM:SS.mmm（默认，W3C TTML1 §10.3.1 合法）；"
                        "mmss=MM:SS.mmm、seconds=SS.mmm（AMLL 宽松，非合法 TTML1）；"
                        "auto=Apple/AMLL 混合写法（<1min 裸秒、<1h M:SS、≥1h H:MM:SS）")
    p.add_argument("--song-part", default="", help="在 <div> 上写 itunes:songPart（如 Verse / Chorus）")
    p.add_argument("--author-id", default="", help="TTML 制作者的 GitHub 数字 ID（amll:meta ttmlAuthorGithub）")
    p.add_argument("--singer-mode", choices=["off", "1", "2", "3"], default="off",
                   help="演唱者分段：off=关闭，直接用歌词里的 [v1]/[v2] 标记；"
                        "1/2/3=强制 N 个声部（CAM++ 声纹聚成 N 簇，覆盖歌词标记）。"
                        "⚠ 音频判不出到底几个人，所以人数必须你来定")
    p.add_argument("--auto-agents", action="store_true",
                   help="【旧参数】等价于 --singer-mode 2")
    p.add_argument("--split-singers", type=int, default=0, metavar="N",
                   help="【旧参数】等价于 --singer-mode N（N>=2）")
    p.add_argument("--style", choices=["keep", "apple"], default="keep",
                   help="歌词风格规范化：keep=一字不改（默认）；apple=按 Apple 歌词准则"
                        "去掉行尾句号/逗号、英文行首字母大写（会逐条列出改动）")
    p.add_argument("--explicit-policy", choices=["keep", "mask"], default="keep",
                   help="脏话处理：keep=照实抄录（默认，儿童不宜版本）；"
                        "mask=星号屏蔽（儿童适宜版本）")
    p.add_argument("--detect-muted", action="store_true",
                   help="检测被消音屏蔽的词，并按 Apple 准则用 **** 替换")
    p.add_argument("--allow-same-agent-overlap", dest="allow_same_agent_overlap",
                   action="store_true", default=False,
                   help="【实验/诊断】允许有证据的同歌手跨行叠唱并越过下一行 begin（上限 nxt.begin+1.5s）。⚠️ 产出 TTML 会触发『同一演唱者行时间重叠』校验错误，不要当成品发布；默认关闭")
    p.add_argument("--separate-vocals", action="store_true",
                   help="先用 Demucs 分离出人声轨，再用它做识别与声纹（更准，但要多花时间）")
    p.add_argument("--extend-tails", dest="extend_tails", action="store_true",
                   default=True,
                   help="句尾拖音补全（默认开）：用分离后人声轨的能量，把被"
                        "「下一行开始」硬切掉的尾音接回去。**需要 --separate-vocals**，"
                        "否则自动跳过（原始混音分不清人声与伴奏）")
    p.add_argument("--no-extend-tails", dest="extend_tails", action="store_false",
                   help="关掉句尾拖音补全")
    p.add_argument("--demucs-model", default="htdemucs",
                   choices=["htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra"],
                   help="Demucs 模型：htdemucs=默认；htdemucs_ft=更准但慢 4 倍；"
                        "htdemucs_6s=六分轨；mdx_extra=另一种")
    p.add_argument("--check-only", metavar="TTML", help="只校验一个已有的 TTML 文件")
    p.add_argument("--strict-time", action="store_true",
                   help="配合 --check-only：把 AMLL 宽松时间写法（MM:SS.mmm / 裸秒）也判为错误")
    p.add_argument("--report", metavar="JSON", help="把对齐与校验报告写到 JSON")
    return p


def cmd_check_only(path: str, strict: bool = False) -> int:
    from ttml_check import check_ttml
    text = open(path, encoding="utf-8").read()
    res = check_ttml(text, strict=strict)
    print(res.summary())
    for e in res.errors:
        print(f"  ✗ [{e.where}] {e.issue}")
    for w in res.warnings[:20]:
        print(f"  ! [{w.where}] {w.issue}")
    if len(res.warnings) > 20:
        print(f"  … 另有 {len(res.warnings) - 20} 条警告")
    return 0 if res.ok else 1


def ensure_utf8_console() -> None:
    """让控制台输出不会因为"字符不在本机代码页里"而崩。

    ⚠️ 真踩过：``--help`` 文本里有一个 ``⚠️``（U+26A0）。**简体中文 Windows 的
    控制台代码页是 GBK，里面没有这个字符** —— 于是 ``argparse.print_help()``
    在写这一行时直接抛 ``UnicodeEncodeError: 'gbk' codec can't encode character
    '\\u26a0'``，**整个 ``--help`` 崩掉、退出码 1**。
    打包成 exe 后必现（冻结进程的 stdout 走控制台代码页），源码运行时用
    ``chcp 65001`` 或 IDE 控制台则不一定复现 —— 属于"只在发布物上炸"的那类 bug。

    处理：
    1. 先把控制台输出代码页切到 UTF-8（Windows）；
    2. 再给 stdout/stderr 加 ``errors="replace"`` 兜底 ——
       即使切不过去（重定向到文件、老系统），最差也只是显示成 ``?``，**不会崩**。
    """
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv=None) -> int:
    ensure_utf8_console()
    args = build_parser().parse_args(argv)

    if args.check_only:
        return cmd_check_only(args.check_only, strict=args.strict_time)

    if not args.audio or not args.lyrics:
        print("错误：需要同时提供 --audio 和 --lyrics（或使用 --check-only）\n")
        build_parser().print_help()
        return 2
    for path, name in ((args.audio, "音频"), (args.lyrics, "歌词")):
        if not os.path.exists(path):
            print(f"错误：找不到{name}文件 {path}")
            return 2

    # 编码探测：中文 Windows 上的 .lrc 大量是 GBK，写死 utf-8 会直接失败
    lyrics_text, lyrics_enc, lyrics_dirty = textread.read_text_best_effort(args.lyrics)
    if lyrics_enc not in ("utf-8-sig", "utf-8"):
        print(f"提示：歌词文件不是 UTF-8（识别为 {lyrics_enc}），已按该编码读取")
    if lyrics_dirty:
        print("警告：歌词编码无法可靠识别，已尽力解码，可能有乱码字符")
    output = args.output or os.path.splitext(args.lyrics)[0] + ".ttml"

    platform_ids = {}
    for key, val in (("ncmMusicId", args.ncm_id), ("qqMusicId", args.qq_id),
                     ("appleMusicId", args.apple_id), ("spotifyId", args.spotify_id),
                     ("isrc", args.isrc)):
        if val:
            platform_ids[key] = val

    options = ConvertOptions(
        mode=args.mode,
        language=args.language,
        model=args.model,
        device=args.device,
        align_engine=args.align_engine,
        refine=not args.no_refine,
        chunked=not args.no_chunked,
        skip_credits=not args.keep_credits,
        pause_space=args.pause_space,
        markers=args.markers,
        line_id=args.line_id,
        agent_from=args.agent_from,
        translation_style=args.translation_style,
        time_format=args.time_format,
        song_part=args.song_part,
        singer_mode=args.singer_mode,
        auto_agents=args.auto_agents,
        split_singers=args.split_singers,
        style=args.style,
        explicit_policy=args.explicit_policy,
        detect_muted=args.detect_muted,
        separate_vocals=args.separate_vocals,
        allow_same_agent_overlap=args.allow_same_agent_overlap,
        extend_tails=args.extend_tails,
        demucs_model=args.demucs_model,
    )

    translation_text = ""
    if args.translation:
        if not os.path.exists(args.translation):
            print(f"错误：找不到翻译文件 {args.translation}")
            return 2
        translation_text = textread.read_text(args.translation)


    print("=" * 64)
    print(" Lyric2TTML · 普通歌词 → Apple Music 逐字 TTML")
    print("=" * 64)
    runner = LyricToTTML(options, log=lambda m: print(m, flush=True))
    t0 = time.time()
    try:
        result = runner.convert(
            audio_path=args.audio,
            lyrics_text=lyrics_text,
            song_title=args.title,
            artists=args.artist,
            album=args.album,
            author=args.author,
            author_id=args.author_id,
            platform_ids=platform_ids,
            output_path=output,
            translation_text=translation_text,
            translation_lang=args.translation_lang,
            songwriters=args.songwriter,
        )
    except Exception as e:
        print(f"\n✗ 转换失败：{e}")
        import traceback
        traceback.print_exc()
        return 1

    print("-" * 64)
    print(f"完成，用时 {time.time() - t0:.1f}s")
    print(f"输出：{output}")
    print(f"字级匹配率：{result.report.match_rate:.1%}")
    print(result.check.summary())
    for note in result.notes:
        print(f"  · {note}")

    if args.report:
        data = {
            "output": output,
            "match_rate": result.report.match_rate,
            "anchored": result.report.anchored,
            "duration": result.duration,
            "elapsed": result.elapsed,
            "notes": result.notes,
            "check": result.check.to_dict(),
            "lines": [
                {
                    "text": l.text,
                    "begin": l.begin,
                    "end": l.end,
                    "match_rate": l.match_rate,
                    "units": [
                        {"text": u.text, "start": u.start, "end": u.end,
                         "matched": u.matched, "bg": u.is_bg}
                        for u in l.units
                    ],
                }
                for l in result.report.lines
            ],
        }
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"报告：{args.report}")

    return 0 if result.check.ok else 1


if __name__ == "__main__":
    sys.exit(main())
