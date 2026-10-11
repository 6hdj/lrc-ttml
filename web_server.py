#!/usr/bin/env python3
"""
Lyric2TTML Web 服务：浏览器界面版（不需要 tkinter）。

    python web_server.py           # 然后打开 http://127.0.0.1:8765
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(HERE, ".hf_cache"))
sys.path.insert(0, HERE)

from pipeline import ConvertOptions, LyricToTTML  # noqa: E402
import edit_api  # noqa: E402
import edit_text  # noqa: E402
import textread  # noqa: E402

UPLOAD_DIR = os.path.join(HERE, "uploads")
OUTPUT_DIR = os.path.join(HERE, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

TASKS: dict = {}
MAX_UPLOAD_MB = 300

# 阶段 → 进度百分比（与 pipeline 的日志前缀对应）
STAGE_PROGRESS = {
    "[1/6]": 4,
    "[2/6]": 12,
    "[3/6]": 62,
    "[5/6]": 84,
    "[6/6]": 94,
}


class Task:
    def __init__(self, params: dict):
        self.id = uuid.uuid4().hex[:10]
        self.params = params
        self.status = "pending"     # pending / running / done / error
        self.progress = 0
        self.stage = "排队中"
        self.logs = []
        self.log_lock = threading.Lock()
        self.error = None
        self.result_path = None
        self.ttml = None
        self.summary = {}
        # 编辑台（P1）
        self.doc = None                 # EditDoc（内存态）
        self.edit_path = None           # outputs/<id>.edit.json

    def log(self, msg: str):
        with self.log_lock:
            self.logs.append({"t": time.strftime("%H:%M:%S"), "m": str(msg)})
            if len(self.logs) > 800:
                self.logs = self.logs[-800:]
        self._update_progress(str(msg))

    def _update_progress(self, msg: str):
        for prefix, pct in STAGE_PROGRESS.items():
            if msg.strip().startswith(prefix):
                self.progress = max(self.progress, pct)
                self.stage = msg.strip()
        m = re.search(r"乐句\s+(\d+)/(\d+)", msg)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            self.progress = max(self.progress, 12 + int(48 * done / max(1, total)))
            self.stage = f"识别乐句 {done}/{total}"
        if "精修" in msg:
            self.progress = max(self.progress, 78)

    def public(self, with_ttml: bool = False):
        with self.log_lock:
            logs = list(self.logs)
        data = {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "logs": logs,
            "error": self.error,
            "summary": self.summary,
            "has_result": bool(self.ttml),
        }
        if with_ttml:
            data["ttml"] = self.ttml
        return data


class Handler(BaseHTTPRequestHandler):
    server_version = "Lyric2TTML"

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------------ 工具
    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 本地工具，页面随时在改：禁止缓存，免得刷新了还是旧界面
        # （曾出现过"改了 index.html 但看起来没生效"）
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------- GET
    def do_GET(self):
        # 和 do_POST 一样兜底：EditError 要变成结构化 JSON，而不是断连接
        try:
            self._get()
        except edit_api.EditError as e:
            self._json(e.to_dict(), e.code)
        except Exception as e:  # pragma: no cover
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _get(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._file(os.path.join(HERE, "index.html"), "text/html; charset=utf-8")
        elif path == "/api/lookup":
            self._lookup(q)
        elif path == "/api/task":
            task = TASKS.get(q.get("id", [""])[0])
            self._json(task.public() if task else {"error": "任务不存在"}, 200 if task else 404)
        elif path == "/api/ttml":
            task = TASKS.get(q.get("id", [""])[0])
            if not task or task.ttml is None:
                self._json({"error": "结果不存在"}, 404)
            else:
                self._json({"ttml": task.ttml, "summary": task.summary})
        elif path == "/api/download":
            task = TASKS.get(q.get("id", [""])[0])
            if task and task.result_path and os.path.exists(task.result_path):
                name = os.path.basename(task.result_path)
                self.send_response(200)
                body = open(task.result_path, "rb").read()
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)
        elif path == "/api/edit":
            self._get_edit(q.get("id", [""])[0])
        elif path == "/api/audio":
            self._audio(q.get("id", [""])[0])
        elif path == "/api/info":
            import platform
            self._json({"python": platform.python_version(), "cwd": HERE})
        else:
            self.send_error(404)

    # ------------------------------------------------------- 音频（编辑台预览）
    AUDIO_TYPES = {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg",
                   ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
                   ".opus": "audio/opus", ".wma": "audio/x-ms-wma"}

    def _audio(self, task_id: str):
        """把该任务的**原始音频**发给浏览器，供编辑台试听/点字跳转。

        必须支持 HTTP Range：`<audio>` 拖动进度条和 `currentTime = x` 都靠它，
        否则 50MB 的 wav 每次 seek 都要重新下载。
        ⚠️ 路径必须校验落在 uploads/ 内，防止 `?id=` 被拿来做目录穿越。
        """
        task = TASKS.get(task_id)
        raw = (task.params.get("audio") if task else "") or ""
        if not raw:
            # 服务重启后 TASKS 是空的，但编辑态还在盘上 → 从 sidecar 取音频路径
            raw = _audio_from_sidecar(task_id)
        if not raw:
            self._json({"error": "这个任务没有音频", "where": task_id,
                        "hint": "编辑台试听只对本次转换的任务可用"}, 404)
            return
        path, err = safe_audio_path(raw)
        if err:
            self._json(err[1], err[0])
            return

        ctype = self.AUDIO_TYPES.get(os.path.splitext(path)[1].lower(),
                                     "application/octet-stream")
        self._serve_range(path, ctype)

    def _serve_range(self, path: str, ctype: str):
        """带 Range 的静态文件响应（支持 `bytes=a-b` / `bytes=a-` / `bytes=-n`）"""
        size = os.path.getsize(path)
        rng = self.headers.get("Range") or ""
        start, end = 0, size - 1
        partial = False

        m = re.match(r"bytes=(\d*)-(\d*)\s*$", rng.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:                                   # bytes=-n：最后 n 个字节
                n = int(m.group(2))
                start = max(0, size - n)
                end = size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        remaining = length
        with open(path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return                          # 播放器提前断开是正常的
                remaining -= len(chunk)

    # ------------------------------------------------------------------ POST
    # ------------------------------------------------- 多平台 ID 检索（可选）
    def _lookup(self, q: dict):
        """按歌名+歌手到各平台检索歌曲 ID。

        ⚠️ **默认关闭**：只有设了环境变量 ``L2T_METING_API``（指向你自己部署的
        Meting / Meting-Agent 实例，统一 ``search`` 接口）才启用。
        本项目的卖点是**离线运行、无需 API Key** —— 不能偷偷联网；
        没配就明确回一句"未配置"，**不要假装查过了**。

        ⚠️ 另一个更重要的理由：**检索会搜错歌**。同名/翻唱/现场/remix 遍地都是，
        **填错的 ID 比空着更糟**（听众点过去是另一首歌）。所以：
        * 这是**用户显式点击**才发生的动作；
        * 前端**只填空字段，绝不覆盖已填的值**，并提示逐个核对。
        """
        import urllib.request
        base = (os.environ.get("L2T_METING_API") or "").strip().rstrip("/")
        if not base:
            self._json({
                "error": "未配置多平台检索服务",
                "hint": "本项目默认离线运行，不内置第三方检索。"
                        "要自动回填请自建 Meting 实例并设环境变量 L2T_METING_API；"
                        "否则可用界面上的「🔍 各平台搜索」按钮手动查。",
                "configured": False,
            })
            return
        title = (q.get("title", [""])[0] or "").strip()
        artists = (q.get("artists", [""])[0] or "").strip()
        if not title:
            self._json({"error": "缺少 title"}, 400)
            return
        first = re.split(r"[,，、/&]", artists)[0].strip() if artists else ""
        kw = f"{title} {first}".strip()
        plat_map = {"netease": "ncmId", "tencent": "qqId",
                    "kuwo": "kuwoId", "kugou": "kugouId"}
        ids, errors = {}, {}
        for plat in plat_map:
            try:
                url = f"{base}/?server={plat}&type=search&id={urllib.parse.quote(kw)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Lyric2TTML"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                item = data[0] if isinstance(data, list) and data else None
                if item and item.get("id"):
                    ids[plat] = str(item["id"])
            except Exception as e:                 # 单个平台失败不能拖垮整次检索
                errors[plat] = f"{type(e).__name__}"
        self._json({"ids": ids, "errors": errors, "keyword": kw,
                    "warning": "自动检索可能搜到同名/翻唱，请逐个核对是不是同一首歌"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/upload":
                self._upload()
            elif path == "/api/convert":
                self._convert()
            elif path == "/api/edit":
                self._post_edit()
            elif path == "/api/validate":
                self._post_validate()
            elif path == "/api/text":
                self._post_text()
            else:
                self.send_error(404)
        except edit_text.TextEditError as e:
            self._json(e.to_dict(), e.code)
        except edit_api.EditError as e:
            self._json(e.to_dict(), e.code)
        except Exception as e:  # pragma: no cover
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ------------------------------------------------------- 编辑台（P1）
    def _get_edit(self, task_id: str):
        task = TASKS.get(task_id)
        ttml = task.ttml if task else None
        edit_path = (task.edit_path if task and task.edit_path
                     else edit_api.edit_path_for(task_id, OUTPUT_DIR))
        if task and task.doc is not None:
            self._json({"doc": task.doc.to_dict(), "version": task.doc.version,
                        "source": "memory", "stats": task.doc.stats()})
            return
        # ⚠️ 服务重启后 TASKS 是空的，磁盘上只有 outputs/<id>.ttml（转换产物）
        # 和可选的 <id>.edit.json（用户保存过的进度）。
        # 以前这里只认 .edit.json，于是"**转换完但还没点保存**"的任务，
        # 重启后编辑台会报"没有可编辑的内容" —— 明明 .ttml 就在旁边。
        # 现在退回读产物 TTML（load_doc 会走 draft 分支重建）。
        if not ttml and not os.path.exists(edit_path):
            safe = "".join(c for c in task_id if c.isalnum() or c in "-_")
            cand = os.path.join(OUTPUT_DIR, f"{safe}.ttml")
            if safe and os.path.exists(cand):
                ttml = textread.read_text(cand)
        doc, source = edit_api.load_doc(ttml, edit_path)   # 损坏会降级为 draft
        if task:
            task.doc = doc
        self._json({"doc": doc.to_dict(), "version": doc.version,
                    "source": source, "stats": doc.stats()})

    def _post_edit(self):
        body = self._read_json()
        task_id = str(body.get("id") or "")
        task = TASKS.get(task_id)
        edit_path = (task.edit_path if task and task.edit_path
                     else edit_api.edit_path_for(task_id, OUTPUT_DIR))
        # 乐观锁拿"存储里的版本"比，不能拿提交体自比
        edit_api.check_version(
            edit_api.stored_version(task.doc if task else None, edit_path),
            body.get("version"))
        if body.get("doc"):
            doc = edit_api.doc_from_dict(body["doc"])       # 支持导入外部 TTML 编辑
        elif task and task.doc is not None:
            doc = task.doc
        else:
            doc, _ = edit_api.load_doc(task.ttml if task else None, edit_path)
        ttml_path = task.result_path if task else None
        out = edit_api.apply_doc(doc, edit_path, ttml_path,
                                 policy=str(body.get("policy") or "auto"))
        if task:
            task.doc = doc
            task.ttml = out["ttml"]
            task.summary["format_ok"] = out["check"]["ok"]
            task.summary["errors"] = len(out["check"]["errors"])
            task.summary["warnings"] = len(out["check"]["warnings"])
        self._json(out)

    def _post_validate(self):
        self._json(edit_api.validate_payload(self._read_json()))

    def _post_text(self):
        """**文本编辑**（插行/删行/改字/和声）→ 落盘。

        与 ``_post_edit`` 同一套乐观锁 + 结构化错误；区别是它先跑
        ``edit_text.apply_op`` 改模型，再走同一条 ``apply_doc``（规范化 + 校验 + 落盘）。
        """
        body = self._read_json()
        task_id = str(body.get("id") or "")
        task = TASKS.get(task_id)
        edit_path = (task.edit_path if task and task.edit_path
                     else edit_api.edit_path_for(task_id, OUTPUT_DIR))
        edit_api.check_version(
            edit_api.stored_version(task.doc if task else None, edit_path),
            body.get("version"))
        if body.get("doc"):
            doc = edit_api.doc_from_dict(body["doc"])
        elif task and task.doc is not None:
            doc = task.doc
        else:
            doc, _ = edit_api.load_doc(task.ttml if task else None, edit_path)
        result = edit_text.apply_op(doc, str(body.get("op") or ""),
                                   body.get("payload") or {})
        out = edit_api.apply_doc(doc, edit_path,
                                 task.result_path if task else None,
                                 policy=str(body.get("policy") or "auto"))
        if task:
            task.doc = doc
            task.ttml = out["ttml"]
            task.summary["format_ok"] = out["check"]["ok"]
            task.summary["errors"] = len(out["check"]["errors"])
            task.summary["warnings"] = len(out["check"]["warnings"])
        out["touched"] = result.get("stats") or {}
        self._json(out)

    def _upload(self):
        body = self._read_json()
        filename = os.path.basename(body.get("filename") or "upload.bin")
        data = body.get("data") or ""
        try:
            raw = base64.b64decode(data)
        except Exception:
            self._json({"error": "文件内容解码失败"}, 400)
            return
        if not raw:
            self._json({"error": "文件为空"}, 400)
            return
        if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
            self._json({"error": f"文件超过 {MAX_UPLOAD_MB}MB 限制"}, 413)
            return
        safe = f"{int(time.time())}_{filename}"
        target = os.path.join(UPLOAD_DIR, safe)
        with open(target, "wb") as f:
            f.write(raw)
        self._json({"path": target, "filename": filename, "size": len(raw)})

    def _convert(self):
        body = self._read_json()
        audio = body.get("audio") or ""
        lyrics = body.get("lyrics") or ""
        if not audio or not os.path.exists(audio):
            self._json({"error": "音频文件不存在，请重新上传"}, 400)
            return
        if not lyrics or not os.path.exists(lyrics):
            self._json({"error": "歌词文件不存在，请重新上传"}, 400)
            return

        task = Task(body)
        TASKS[task.id] = task
        # 把音频路径落到盘上：编辑态本身能从磁盘恢复（edit_api.load_doc），
        # 但 TASKS 是进程内的，**重启后就没声音了** —— 编辑台能打开却没音频、
        # 没播放高亮。存一份 sidecar 让试听也能跨重启。
        try:
            _src_path = os.path.join(OUTPUT_DIR, f"{task.id}.src.json")
            edit_api.atomic_write_text(_src_path, json.dumps(
                {"audio": os.path.abspath(audio), "lyrics": os.path.abspath(lyrics)},
                ensure_ascii=False))
        except Exception:
            pass
        threading.Thread(target=_run_task, args=(task,), daemon=True).start()
        self._json({"task_id": task.id})


def _audio_from_sidecar(task_id: str) -> str:
    """服务重启后用 ``outputs/<id>.src.json`` 找回这个任务用的音频路径。

    编辑态本来就是从磁盘恢复的（``edit_api.load_doc``），音频也应当同样能恢复，
    否则重启后会出现"编辑台打得开、但没声音、播放高亮不动"。
    ⚠️ 仍然只能返回 uploads/ 内的路径 —— sidecar 由本进程写，但校验照旧。
    """
    if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
        return ""
    p = os.path.join(OUTPUT_DIR, f"{task_id}.src.json")
    try:
        with open(p, encoding="utf-8") as f:
            return str(json.load(f).get("audio") or "")
    except (OSError, ValueError):
        return ""


def safe_audio_path(raw: str):
    """把客户端给的音频路径收敛到 uploads/ 内。

    返回 ``(绝对路径, None)`` 或 ``(None, (状态码, 错误体))``。
    单独抽出来是为了能**离线自测**这条防目录穿越逻辑（不用起 HTTP）。
    """
    if not raw:
        return None, (404, {"error": "没有音频路径"})
    path = os.path.abspath(raw)
    root = os.path.abspath(UPLOAD_DIR)
    if not (path == root or path.startswith(root + os.sep)):
        return None, (403, {"error": "音频路径不在 uploads/ 内", "where": path,
                            "hint": "只允许试听本工具上传的音频"})
    if not os.path.isfile(path):
        return None, (404, {"error": "音频文件不存在", "where": path})
    return path, None


def _run_task(task: Task):
    p = task.params
    task.status = "running"
    try:
        # 编码探测：中文 Windows 上的 .lrc 大量是 GBK，写死 utf-8 会直接失败
        lyrics_text, lyrics_enc, lyrics_dirty = textread.read_text_best_effort(p["lyrics"])
        if lyrics_enc not in ("utf-8-sig", "utf-8"):
            task.log(f"歌词文件不是 UTF-8（识别为 {lyrics_enc}），已按该编码读取")
        if lyrics_dirty:
            task.log("⚠ 歌词编码无法可靠识别，已尽力解码，可能有乱码字符")
            p.setdefault("_encoding_warning", True)

        platform_ids = {}
        # 从 ttml_writer.META_KEYS 取，**不要另抄一份**：
        # 抄一份的下场是新加平台（酷我/酷狗）时只改了写器、这里忘了加，
        # 界面上填了 ID 却写不进文件（"填了没用"这类问题最难查）。
        from ttml_writer import META_KEYS as _META_KEYS
        for key in list(_META_KEYS) + ["isrc"]:
            if p.get(key):
                platform_ids[key] = str(p[key]).strip()

        artists = [a.strip() for a in str(p.get("artists", "")).replace("、", ",").split(",") if a.strip()]

        options = ConvertOptions(            mode=p.get("mode", "amll"),
            language=p.get("language", "zh"),
            model=p.get("model", "large-v3"),
            device=p.get("device", "auto"),
            refine=bool(p.get("refine", True)),
            chunked=bool(p.get("chunked", True)),
            skip_credits=bool(p.get("skip_credits", True)),
            pause_space=float(p.get("pause_space", 0) or 0),
            markers=p.get("markers", "agent"),
            line_id=p.get("line_id", "both"),
            translation_style=p.get("translation_style", "head"),
            time_format=p.get("time_format", "clock"),
            song_part=p.get("song_part", ""),
            singer_mode=str(p.get("singer_mode") or "off"),
            auto_agents=bool(p.get("auto_agents", False)),
            split_singers=int(p.get("split_singers", 0) or 0),
            style=p.get("style", "keep"),
            explicit_policy=p.get("explicit_policy", "keep"),
            detect_muted=bool(p.get("detect_muted", False)),
            separate_vocals=bool(p.get("separate_vocals", False)),
            demucs_model=p.get("demucs_model", "htdemucs"),
        )

        translation_text = ""
        tr_path = p.get("translation") or ""
        if tr_path and os.path.exists(tr_path):
            translation_text = textread.read_text(tr_path)

        task.log("=" * 46)
        task.log("开始转换")
        task.log(f"音频：{os.path.basename(p['audio'])}")
        task.log(f"歌词：{os.path.basename(p['lyrics'])}")
        task.stage = "加载模型 / 解析歌词"
        task.progress = max(task.progress, 2)

        runner = LyricToTTML(options, log=task.log)
        result = runner.convert(
            audio_path=p["audio"],
            lyrics_text=lyrics_text,
            song_title=p.get("title", ""),
            artists=artists,
            album=p.get("album", ""),
            author=p.get("author", ""),
            author_id=str(p.get("author_id", "") or ""),
            platform_ids=platform_ids,
            output_path=None,
            translation_text=translation_text,
            translation_lang=str(p.get("translation_lang", "") or ""),
            songwriters=[s.strip() for s in str(p.get("songwriters", "")).split(",") if s.strip()],
        )

        out_path = os.path.join(OUTPUT_DIR, f"{task.id}.ttml")
        # 原子写：先写同目录临时文件再 os.replace，中途崩溃不会留下半截 TTML。
        # （编辑台保存路径本来就用同一个 helper，这里保持一致；原来是普通 open().write）
        edit_api.atomic_write_text(out_path, result.ttml)

        task.ttml = result.ttml
        task.result_path = out_path
        # 编辑台：生成即建立编辑态（内存 + outputs/<id>.edit.json 的初始快照）
        task.edit_path = edit_api.edit_path_for(task.id, OUTPUT_DIR)
        try:
            task.doc = edit_api.doc_from_ttml(result.ttml)
            # TTML 文本里没有 prob/matched（parse_ttml 只能写死默认值），
            # 但从内存里的对齐报告能拿到 —— 不回填的话编辑台的"红/黄"提示永远不会出现
            edit_api.backfill_align_hints(task.doc, result.report.lines)
            edit_api.save_doc(task.doc, task.edit_path)
            n_low = task.doc.stats().get("unmatched", 0)
            if n_low:
                task.log(f"（编辑台已标出 {n_low} 个插值补的字，可在里面重点核对）")
        except edit_api.EditError as e:
            task.log(f"（编辑态初始化失败，仍可下载 TTML：{e.error}）")
        task.summary = {
            "match_rate": round(result.report.match_rate, 4),
            "format_ok": result.check.ok,
            "errors": len(result.check.errors),
            "warnings": len(result.check.warnings),
            "lines": result.check.stats.get("lines", 0),
            "words": result.check.stats.get("words", 0),
            "avg_word_duration": round(result.check.stats.get("avg_word_duration", 0), 3),
            "anchored": result.report.anchored,
            "duration": result.duration,
            "elapsed": round(result.elapsed, 1),
            "notes": result.notes,
            "roles": sorted({l.role for l in result.report.lines if l.role}),
            "issues": [e.__dict__ for e in result.check.errors[:10]],
        }
        task.progress = 100
        task.stage = "完成"
        task.status = "done"
        task.log("")
        task.log(f"✓ 完成！匹配率 {result.report.match_rate:.1%}，{result.check.summary()}")
    except Exception as e:
        task.status = "error"
        task.error = f"{type(e).__name__}: {e}"
        task.stage = "失败"
        task.log(f"✗ 转换失败：{task.error}")
        import traceback
        for line in traceback.format_exc().splitlines()[-8:]:
            task.log(line)


def main():
    port = int(os.environ.get("L2T_PORT", "8765"))
    host = os.environ.get("L2T_HOST", "127.0.0.1")
    httpd = ThreadingHTTPServer((host, port), Handler)
    print("=" * 52)
    print(" Lyric2TTML Web 服务已启动")
    print(f" 请在浏览器打开: http://{host}:{port}")
    print(" 按 Ctrl+C 停止")
    print("=" * 52)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
