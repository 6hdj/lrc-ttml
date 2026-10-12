"""后端调用服务：把"一次歌词转换"变成可以被任务中心驱动的函数。

它是**唯一的转换入口**（界面、命令行、以后可能的自动化都走这里），
所以三件事必须在这里做对：

1. **参数映射不重写** —— 一律走 ``convert_params.build_options``。
2. **失败绝不覆盖已有产物** —— 结果先写 ``<id>.ttml.tmp``，成功才
   ``os.replace`` 上位（``edit_api.atomic_write_text``）。转换崩在半路时，
   用户上一次做好的文件还在。
3. **落盘之后立刻建编辑态** —— 生成即 ``outputs/<id>.edit.json``，
   这是编辑台（``web_server`` / ``index.html``）能打开这份产物的前提。
   桌面窗口里"打开编辑台"就是把这个 id 交给本地 Web 服务。

本模块**不导入 Qt**，但会导入 ``pipeline``（连带 torch），所以
**不要**在界面启动时 import 它 —— 用函数内延迟导入（下面都是这么写的）。
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Optional

try:
    from desktop import paths
except ImportError:                                   # 单文件直接跑
    import paths                                      # type: ignore

from desktop.services import convert_params


def _ensure_sys_path() -> str:
    """保证能 import 到仓库根目录下的算法模块（pipeline / asr / edit_api …）。"""
    import sys
    base = paths.app_dir()
    if base not in sys.path:
        sys.path.insert(0, base)
    src = paths._source_dir()
    if src not in sys.path:
        sys.path.insert(0, src)
    return base


def prepare_environment() -> str:
    """**必须在 import asr / pipeline / torch 之前**调用。

    做三件事：
    1. 定好模型缓存目录（冻结版里 ``__file__`` 指向 ``_internal``，
       不重定向的话 3GB 模型会被下到包内部，升级就丢）。
    2. **把 CUDA DLL 目录注册进搜索路径**。
       ⚠️ 这一步不能省、也不能晚：cuBLAS / cuDNN 是我们自带的
       （``nvidia-*-cu12`` 包或 ``<程序目录>/cuda``），不在系统 PATH 里。
       torch / ctranslate2 一加载就去解析 ``cublas64_12.dll``，
       那时再注册已经来不及，报的是
       ``RuntimeError: Library cublas64_12.dll is not found or cannot be loaded``
       —— **看起来像没装显卡驱动，其实是 DLL 搜索路径没配对**（真踩过）。
       自检第 12 组会守住这条：``prepare_environment()`` 之后
       CUDA 目录必须在搜索路径里。
    3. 把 ``web_server`` 的资源/输出目录指到可写位置。

    返回最终使用的模型缓存目录。
    """
    _ensure_sys_path()
    hub = paths.apply_env()
    paths.ensure_dir(hub)

    # ---- 2. CUDA DLL（在别人 import torch 之前）----
    try:
        from desktop.services import gpu_probe
        gpu_probe.register_dll_dirs()
    except Exception:                                  # noqa: BLE001
        pass

    # Demucs 的权重目录（torch.hub）也重定向到程序旁边
    os.environ.setdefault("TORCH_HOME",
                          os.path.join(paths.app_dir(), "models", "demucs"))
    # 冻结版里 web_server 的 HERE / UPLOAD_DIR / OUTPUT_DIR 要指向可写目录
    try:
        import web_server
        base = paths.app_dir()
        web_server.HERE = base if paths.frozen() else os.path.dirname(
            os.path.abspath(web_server.__file__))
        web_server.UPLOAD_DIR = paths.uploads_dir()
        web_server.OUTPUT_DIR = paths.outputs_dir()
        paths.ensure_dir(web_server.UPLOAD_DIR)
        paths.ensure_dir(web_server.OUTPUT_DIR)
    except Exception:                                  # noqa: BLE001
        pass
    return hub


def estimate(lyrics_text: str, duration: Optional[float]) -> Dict[str, Any]:
    """粗略估时（只是给界面一个"大概多久"，**不承诺准确**）。

    实测基线：large-v3 + RTX 40 系，1 分钟音频约 12~20 秒；
    有 LRC 锚点时更快。这里按 0.25×音频时长 + 每行 0.35s 估。
    """
    lines = [ln for ln in (lyrics_text or "").splitlines() if ln.strip()]
    audio_part = (duration or 0) * 0.25
    line_part = len(lines) * 0.35
    total = audio_part + line_part
    return {"lines": len(lines), "seconds": round(total, 1),
            "text": f"约 {int(total // 60)} 分 {int(total % 60)} 秒" if total >= 60
                    else f"约 {int(total)} 秒"}


def convert(params: Dict[str, Any],
            log: Callable[[str], None] = print,
            progress: Optional[Callable[[Optional[float], str], None]] = None,
            cancelled: Optional[Callable[[], bool]] = None,
            output_dir: Optional[str] = None,
            task_id: str = "") -> Dict[str, Any]:
    """跑一次完整转换。**返回结果字典（不抛异常给界面看堆栈）**。

    参数：
        params     界面参数（见 ``convert_params.validate``）
        log        日志回调（任务中心把它写到任务日志里）
        progress   ``(百分比或 None, 说明文字)`` 回调
        cancelled  ``() -> bool``，在**阶段之间**查询
        output_dir  产物目录，默认 ``<workspace>/outputs``
        task_id     产物文件名前缀，默认用时间戳

    返回：``{ok, ttml, path, task_id, summary, elapsed, error}``
    """
    p = progress or (lambda pct, msg: None)
    c = cancelled or (lambda: False)

    problems = convert_params.validate(params)
    if problems:
        return {"ok": False, "error": "；".join(problems), "error_kind": "params",
                "summary": {}, "path": "", "ttml": "", "task_id": task_id}

    prepare_environment()

    out_dir = output_dir or paths.outputs_dir()
    paths.ensure_dir(out_dir)
    tid = task_id or time.strftime("%Y%m%d-%H%M%S")

    if c():
        return {"ok": False, "error": "已取消", "error_kind": "cancelled",
                "summary": {}, "path": "", "ttml": "", "task_id": tid}

    t0 = time.time()
    try:
        # ---- 1. 读歌词（带编码探测：GBK 的 .lrc 在中文 Windows 上很常见）----
        p(2, "读取歌词")
        lyrics_text, enc, dirty = convert_params.read_lyrics(params["lyrics"])
        if enc not in ("utf-8-sig", "utf-8"):
            log(f"歌词文件不是 UTF-8（识别为 {enc}），已按该编码读取")
        if dirty:
            log("⚠ 歌词编码无法可靠识别，已尽力解码，可能有乱码字符")
        if not lyrics_text.strip():
            return {"ok": False, "error": "歌词文件里没有文字", "error_kind": "lyrics",
                    "summary": {}, "path": "", "ttml": "", "task_id": tid}

        translation_text = convert_params.read_translation(params)
        options = convert_params.build_options(params)
        platform = convert_params.platform_ids(params)
        artists = convert_params.artists_list(params)
        songwriters = convert_params.songwriters_list(params)

        if c():
            raise _Cancelled()

        # ---- 2. 跑流水线 ----
        from pipeline import LyricToTTML
        log("=" * 46)
        log("开始转换")
        log(f"音频：{os.path.basename(params['audio'])}")
        log(f"歌词：{os.path.basename(params['lyrics'])}（{enc}）")
        log(f"模型：{options.model}｜语言：{options.language}｜"
            f"声部：{options.singer_mode}｜模式：{options.mode}")

        def _log(msg: str) -> None:
            log(msg)
            _pipeline_progress(msg, p)

        runner = LyricToTTML(options, log=_log)
        result = runner.convert(
            audio_path=params["audio"],
            lyrics_text=lyrics_text,
            song_title=str(params.get("title") or ""),
            artists=artists,
            album=str(params.get("album") or ""),
            author=str(params.get("author") or ""),
            author_id=str(params.get("author_id") or ""),
            platform_ids=platform,
            output_path=None,
            translation_text=translation_text,
            translation_lang=str(params.get("translation_lang") or ""),
            songwriters=songwriters,
        )
        if c():
            raise _Cancelled()

        # ---- 3. 原子落盘 ----
        p(96, "写入 TTML")
        out_path = os.path.join(out_dir, f"{tid}.ttml")
        _atomic_write(out_path, result.ttml)

        summary = _summary_of(result)
        log("")
        log(f"✓ 完成！匹配率 {result.report.match_rate:.1%}，{result.check.summary()}")
        p(100, "完成")

        return {"ok": True, "error": "", "error_kind": "",
                "ttml": result.ttml, "path": out_path, "task_id": tid,
                "summary": summary, "elapsed": round(time.time() - t0, 1),
                "encoding": enc}

    except _Cancelled:
        return {"ok": False, "error": "已取消", "error_kind": "cancelled",
                "summary": {}, "path": "", "ttml": "", "task_id": tid}
    except Exception as e:                             # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        log(f"✗ 转换失败：{type(e).__name__}: {e}")
        for ln in tb.strip().splitlines()[-6:]:
            log("   " + ln)
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "error_kind": _classify(e), "traceback": tb,
                "summary": {}, "path": "", "ttml": "", "task_id": tid,
                "elapsed": round(time.time() - t0, 1)}


class _Cancelled(Exception):
    """内部用：把"用户取消"从异常路径里区分出来。"""


def _atomic_write(path: str, text: str) -> None:
    """先临时文件再上位。**不要**直接 open(path,'w') 覆盖已有产物。"""
    try:
        import edit_api
        edit_api.atomic_write_text(path, text)
        return
    except Exception:                                  # noqa: BLE001
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _pipeline_progress(msg: str, p: Callable[[Optional[float], str], None]) -> None:
    """把 pipeline 的 ``[n/6]`` 前缀翻成进度。

    与 ``web_server.STAGE_PROGRESS`` 同一套映射；改了那边也要改这边
    （自检里有一条会比对两张表）。
    """
    m = msg.strip()
    stage = {
        "[1/6]": 4, "[2/6]": 12, "[3/6]": 62, "[4/6]": 74,
        "[5/6]": 84, "[6/6]": 94,
    }
    for prefix, pct in stage.items():
        if m.startswith(prefix):
            p(pct, m[:70])
            return
    import re
    mm = re.search(r"乐句\s+(\d+)/(\d+)", m)
    if mm:
        done, total = int(mm.group(1)), int(mm.group(2))
        p(12 + 48 * done / max(1, total), f"识别乐句 {done}/{total}")
    elif "精修" in m:
        p(78, m[:70])


def _summary_of(result) -> Dict[str, Any]:
    """把 ConvertResult 压成界面要用的几个数字。"""
    try:
        return {
            "match_rate": round(result.report.match_rate, 4),
            "format_ok": bool(result.check.ok),
            "errors": len(result.check.errors),
            "warnings": len(result.check.warnings),
            "lines": result.check.stats.get("lines", 0),
            "words": result.check.stats.get("words", 0),
            "avg_word_duration": round(result.check.stats.get("avg_word_duration", 0), 3),
            "anchored": result.report.anchored,
            "duration": result.duration,
            "elapsed": round(result.elapsed, 1),
            "roles": sorted({l.role for l in result.report.lines if l.role}),
            "issues": [e.__dict__ for e in result.check.errors[:10]],
        }
    except Exception as e:                             # noqa: BLE001
        return {"_summary_error": f"{type(e).__name__}: {e}"}


def _classify(e: Exception) -> str:
    """给失败人话分类 —— 界面照这个决定"下一步建议"。"""
    s = f"{type(e).__name__}: {e}".lower()
    # ⚠️ 顺序有讲究：显存不足的报错文本里**也**含 "cuda"，
    # 先判 gpu 的话会告诉用户"去装驱动"，而实际只要换个模型/关掉别的程序。
    if "out of memory" in s or "显存" in s or "cuda_error_out_of_memory" in s:
        return "memory"
    if "cublas" in s or "cudnn" in s or "nvcuda" in s or "cuda" in s or "gpu" in s:
        return "gpu"
    if "localentrynotfound" in s or "connection" in s or "resolve" in s \
            or "offline" in s or "download" in s or "timed out" in s:
        return "download"
    if "unicode" in s and "decode" in s:
        return "encoding"
    if "no module named" in s:
        return "dependency"
    return "unknown"


def check_ttml(path: str, strict_time: bool = False) -> Dict[str, Any]:
    """独立校验一份 TTML（不重跑转换）。

    ⚠️ ``strict_time`` 决定"``MM:SS.mmm`` 算不算错"。默认 False 是**故意的**：
    旧产物 / 别人给的 TTML 里这种写法很常见，AMLL 也认，
    打开就报一堆红会让用户以为软件坏了。要卡死格式（发布前）才传 True。
    """
    prepare_environment()
    from ttml_check import check_ttml as _check
    import textread
    text = textread.read_text(path)
    res = _check(text, strict=strict_time)
    return {
        "ok": bool(res.ok),
        "errors": [e.__dict__ for e in res.errors],
        "warnings": [w.__dict__ for w in res.warnings],
        "stats": dict(res.stats or {}),
    }


def open_editor_url(port: int = 8765) -> str:
    return f"http://127.0.0.1:{port}/"
