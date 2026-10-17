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
# ⚠️ 不能写死 HERE/.hf_cache：冻结后 HERE=_internal，而下载/体检都指向 <程序目录>\.hf_cache
# 或 L2T_HF_CACHE ⇒ ASR 会去错误目录找模型（便携包里实测"下好了却说没缓存"）。
if not os.environ.get("HUGGINGFACE_HUB_CACHE"):
    _hf_default = (os.path.dirname(os.path.abspath(sys.executable))
                   if getattr(sys, "frozen", False) else HERE)
    os.environ["HUGGINGFACE_HUB_CACHE"] = (os.environ.get("L2T_HF_CACHE")
                                            or os.path.join(_hf_default, ".hf_cache"))
sys.path.insert(0, HERE)

from pipeline import ConvertOptions, LyricToTTML  # noqa: E402
import audio_proxy  # noqa: E402
import edit_api  # noqa: E402
import edit_text  # noqa: E402
import textread  # noqa: E402

UPLOAD_DIR = os.path.join(HERE, "uploads")
OUTPUT_DIR = os.path.join(HERE, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

TASKS: dict = {}
MAX_UPLOAD_MB = 300

# 代理音频的后台生成状态（源路径 → {"state","note","ts"}）。
# 为什么不直接在请求里同步转码：用户点播放时**不能等**，先发原始音频立刻出声，
# 转码在后台做，下次打开就用上小文件了。前端可查 /api/audio_proxy 显示进度。
_PROXY_STATE: dict = {}
_PROXY_LOCK = threading.Lock()

# 阶段 → 进度百分比（与 pipeline 的日志前缀对应）
STAGE_PROGRESS = {
    "[1/6]": 4,
    "[2/6]": 12,
    "[3/6]": 62,
    "[5/6]": 84,
    "[6/6]": 94,
}


class TaskCancelled(Exception):
    """用户取消（web 端）。复用桌面版已验证的模式：在 log 回调里检查标志并抛出，
    管线的 log 调用很频繁（每个乐句/阶段都调）⇒ 取消能真正传到正在跑的任务。"""


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
        # 本任务的输出目录（用户在界面里可改）；None = 全局 OUTPUT_DIR
        self.out_dir = None
        # 编辑台（P1）
        self.doc = None                 # EditDoc（内存态）
        self.edit_path = None           # outputs/<id>.edit.json
        # 取消：默认 False；用户点取消后置位，管线下次 log 时抛出
        self.cancelled = False

    def cancel(self) -> bool:
        """请求取消。返回是否**这次调用真的改变了状态**（幂等，可重复点）。"""
        if self.status in ("done", "error", "cancelled"):
            return False
        self.cancelled = True
        self.stage = "正在取消…"
        return True

    def log(self, msg: str):
        # ⚠️ **取消检查点**：放这里而不是另开线程去杀 —— log 是管线唯一的高频回调，
        # 覆盖 ASR 每个乐句、对齐、精修、导出，取消因此能真正中断任务，
        # 而不是只关掉界面上的进度条（桌面版同一模式，已验收）。
        if self.cancelled:
            raise TaskCancelled("用户取消")
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
            # 列表页显示用（音频文件名）；纯增量字段
            "title": os.path.basename(str(self.params.get("audio") or "")) or self.id,
            # 产物路径（客户端"打开 TTML / 打开所在文件夹"要用；纯增量字段）
            "result_path": self.result_path,
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
        elif path == "/api/tasks":
            # 任务列表（**新增端点，纯增量**）：给新 UI 的任务中心用。
            # 不返回 logs（体积大）；要日志就再按 id 查 /api/task。
            # 倒序 = 最新的在前（dict 保持插入顺序）。
            items = []
            for t in reversed(list(TASKS.values())):
                d = t.public()
                d.pop("logs", None)
                items.append(d)
            self._json({"tasks": items, "count": len(items)})
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
        elif path == "/api/audio-path":
            # 给桌面版编辑台用：直接拿**本地绝对路径**（本地磁盘直读，比 HTTP 流快得多）。
            # 安全性与 /api/audio 完全一致：只放行"该任务自己记录过的音频"。
            self._audio_path(q.get("id", [""])[0])
        elif path == "/api/audio":
            self._audio(q.get("id", [""])[0])
        elif path == "/api/audio_proxy":
            # 代理缓存管理：stats=看占用，purge=清空（用户要求"缓存必须能删掉"）
            act = (q.get("act", [""])[0] or "stats").lower()
            if act == "purge":
                n = audio_proxy.purge_all()
                self._json({"ok": True, "removed": n, **_proxy_status()})
            elif act == "build":
                # 显式预热：编辑台打开时就提前转，别等用户点播放
                src, err = self._resolve_audio(q.get("id", [""])[0])
                if err:
                    self._json(err[1], err[0])
                else:
                    _kickoff_proxy_build(src)
                    self._json({"ok": True, **_proxy_status()})
            else:
                self._json({"ok": True, **_proxy_status()})
        elif path == "/api/info":
            import platform
            self._json({"python": platform.python_version(), "cwd": HERE})
        else:
            self.send_error(404)

    # ------------------------------------------------------- 音频（编辑台预览）
    AUDIO_TYPES = {".wav": "audio/wav", ".flac": "audio/flac", ".mp3": "audio/mpeg",
                   ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
                   ".opus": "audio/opus", ".wma": "audio/x-ms-wma"}

    def _resolve_audio(self, task_id: str):
        """任务 → 可播放的音频绝对路径。返回 ``(路径, None)`` 或 ``(None, (码, 错误体))``。"""
        task = TASKS.get(task_id)
        raw = (task.params.get("audio") if task else "") or ""
        trusted = _audio_from_sidecar(task_id) or ""     # 本任务自己记录的路径（可信）
        if not raw:
            # 服务重启后 TASKS 是空的，但编辑态还在盘上 → 从 sidecar 取音频路径
            raw = trusted
        if not raw:
            return None, (404, {"error": "这个任务没有音频", "where": task_id,
                                "hint": "编辑台试听只对本次转换的任务可用"})
        return safe_audio_path(raw, trusted)

    def _audio_path(self, task_id: str):
        """返回该任务音频的本地绝对路径（客户端用它做本地直读）。"""
        task = TASKS.get(task_id)
        trusted = _audio_from_sidecar(task_id) or ""
        raw = (task.params.get("audio") if task else "") or trusted
        if not raw:
            self._json({"error": "这个任务没有音频", "where": task_id}, 404)
            return
        path, err = safe_audio_path(raw, trusted)
        if err:
            self._json(err[1], err[0])
            return
        self._json({"path": path})

    def _audio(self, task_id: str):
        """把该任务的音频发给播放器，供编辑台试听/点字跳转。

        **优先发"小体积代理"**（见 ``audio_proxy`` 模块）：
        用户的音频是无损 FLAC（实测 22.7~60.5 MB），直接把原始文件喂给
        MediaPlayer 会让它为了时长/seek 索引解析整个无损流 ——
        这正是用户说的"加载好半天"。代理只有 1~2 MB，打开快得多。
        代理还没生成好时**立刻回退原始音频**（不能让编辑台没声音），
        并在后台把它转出来，下次就快了。

        仍然支持 HTTP Range：`<audio>` 拖进度条和 `currentTime = x` 都靠它。
        ⚠️ 路径必须校验（sidecar 可信 / 否则收敛在 uploads/ 内），防目录穿越。
        """
        path, err = self._resolve_audio(task_id)
        if err:
            self._json(err[1], err[0])
            return

        serve = path
        ctype = self.AUDIO_TYPES.get(os.path.splitext(path)[1].lower(),
                                     "application/octet-stream")
        try:
            pp = audio_proxy.proxy_path(path)
            if os.path.isfile(pp) and os.path.getsize(pp) > 1024:
                serve, ctype = pp, "audio/mpeg"
            else:
                # 首次：先发原始音频（马上有声），后台生成代理
                _kickoff_proxy_build(path)
        except Exception:                      # noqa: BLE001
            pass                               # 代理出任何问题都不该影响试听

        self._serve_range(serve, ctype)

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
            elif path == "/api/save":
                self._post_save()
            elif path == "/api/task/cancel":
                self._post_cancel()
            else:
                self.send_error(404)
        except edit_text.TextEditError as e:
            self._json(e.to_dict(), e.code)
        except edit_api.EditError as e:
            self._json(e.to_dict(), e.code)
        except Exception as e:  # pragma: no cover
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _post_cancel(self):
        """取消一个任务：`{"id": "<task id>"}`。

        语义（**不假装**）：
          * 任务不存在 → 404 + 明确错误；
          * 已结束（done/error/cancelled）→ 200 + `changed: false`（幂等，不报假成功）；
          * 已请求取消 → 200 + `changed: true`，真正的中断由管线在下一个 log 点完成，
            之后 `GET /api/task?id=` 会变成 `status: "cancelled"`。
        """
        body = self._read_json()
        tid = str(body.get("id") or "").strip()
        task = TASKS.get(tid)
        if task is None:
            self._json({"error": "任务不存在", "id": tid}, 404)
            return
        changed = task.cancel()
        self._json({"id": tid, "changed": changed, "status": task.status,
                    "stage": task.stage})

    # ------------------------------------------------------- 编辑台（P1）
    def _get_edit(self, task_id: str):
        task = TASKS.get(task_id)
        ttml = task.ttml if task else None
        edit_path = (task.edit_path if task and task.edit_path
                     else edit_api.edit_path_for(task_id, (task.out_dir if task else None) or OUTPUT_DIR))
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
            # 任务可能把产物写到了用户选的目录（output_dir）⇒ 两处都找
            _od = (task.out_dir if task else None) or OUTPUT_DIR
            cand = os.path.join(_od, f"{safe}.ttml")
            if not os.path.exists(cand):
                try:
                    _meta = json.loads(textread.read_text(os.path.join(_od, f"{safe}.result.json")))
                    cand = str(_meta.get("ttml") or cand)
                except Exception:
                    pass
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
                     else edit_api.edit_path_for(task_id, (task.out_dir if task else None) or OUTPUT_DIR))
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
        # ⚠️⚠️ 任务不在内存里（后端重启过）时必须**退回确定性路径**，
        # 否则 ttml_path=None ⇒ apply_doc 只写 .edit.json、**不重写 TTML**
        # ⇒ 用户看到"已保存"但打开 TTML 毫无变化（真实报告：编辑台改动没保存）。
        _persist = bool(body.get("persist", True))
        if task is not None:
            ttml_path = task.result_path if _persist else None
        else:
            _od = (task.out_dir if task else None) or OUTPUT_DIR
            _p = os.path.join(_od, f"{task_id}.ttml")
            if not os.path.exists(_p):
                try:
                    _meta = json.loads(textread.read_text(os.path.join(_od, f"{task_id}.result.json")))
                    _p = str(_meta.get("ttml") or _p)
                except Exception:
                    pass
            ttml_path = _p if os.path.exists(_p) else None
        out = edit_api.apply_doc(doc, edit_path, ttml_path,
                                 policy=str(body.get("policy") or "auto"))
        if task:
            task.doc = doc
            task.ttml = out["ttml"]
            task.summary["format_ok"] = out["check"]["ok"]
            task.summary["errors"] = len(out["check"]["errors"])
            task.summary["warnings"] = len(out["check"]["warnings"])
        self._json(out)

    @staticmethod
    def _display_stem(task) -> str:
        """保存文件名：优先「歌手 - 歌名」，其次音频文件名（与界面识别同一来源）。

        用户要求：处理完保存时不要是一串 task id，要用识别到的歌手/歌名。
        """
        pp = task.params or {}
        title = str(pp.get("title") or "").strip()
        artists = pp.get("artists") or []
        if isinstance(artists, str):
            artists = [a.strip() for a in re.split(r"[,，、/&]", artists) if a.strip()]
        artists = [str(a).strip() for a in artists if str(a).strip()]
        if title:
            base = ((" _ ".join(artists) + " - ") if artists else "") + title
        else:
            audio = str(pp.get("audio") or "")
            base = os.path.splitext(os.path.basename(audio))[0]
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
        return (base[:120] or task.id)

    @staticmethod
    def _unique_path(out_dir: str, stem: str, keep: str = "") -> str:
        """同名文件已存在时加 (2)/(3)…；keep 是本任务自己的现有路径则原样复用。"""
        cand = os.path.join(out_dir, stem + ".ttml")
        if not os.path.exists(cand) or (keep and os.path.abspath(cand) == os.path.abspath(keep)):
            return cand
        for i in range(2, 100):
            cand = os.path.join(out_dir, f"{stem} ({i}).ttml")
            if not os.path.exists(cand) or (keep and os.path.abspath(cand) == os.path.abspath(keep)):
                return cand
        return os.path.join(out_dir, f"{stem} ({uuid.uuid4().hex[:6]}).ttml")

    def _post_save(self):
        """把当前任务保存成正式 TTML（用户点「保存」时才写）。

        为什么需要它：转换完成后产物**先不落盘**（auto_save=false），用户可能想先改；
        保存时把内存里的编辑态写出去，并把 .pending.overlap.json 转成正式证据文件。
        """
        body = self._read_json()
        task_id = str(body.get("id") or "")
        if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
            self._json({"error": "任务 id 不合法"}, 400)
            return
        task = TASKS.get(task_id)
        if task is None:
            self._json({"error": "任务不在内存里（后端重启过）；请重新生成后再保存"}, 404)
            return
        out_dir = task.out_dir or OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        # 文件名 = 「歌手 - 歌名」（不是 task id）；已保存过的任务保持原路径不变
        if task.result_path and os.path.exists(task.result_path):
            final = task.result_path
        else:
            final = self._unique_path(out_dir, self._display_stem(task))
        edit_path = task.edit_path or edit_api.edit_path_for(task_id, out_dir)
        # 没编辑过（version==0）⇒ 直接把生成好的 pending 产物转正：
        # 重渲染会走一遍 normalize，可能把长尾按默认策略收回（用户抱怨过的老问题）。
        _untouched = task.doc is None or getattr(task.doc, "version", 0) == 0
        _pending = os.path.join(out_dir, f"{task_id}.pending.ttml")
        if _untouched and os.path.exists(_pending):
            edit_api.atomic_write_text(final, textread.read_text(_pending))
        elif task.doc is not None:
            out = edit_api.apply_doc(task.doc, edit_path, final,
                                     policy=str(body.get("policy") or "auto"))
            task.ttml = out["ttml"]
        else:
            pending = os.path.join(out_dir, f"{task_id}.pending.ttml")
            if not os.path.exists(pending):
                self._json({"error": "没有可保存的内容（既无编辑态也无 pending 产物）"}, 404)
                return
            edit_api.atomic_write_text(final, textread.read_text(pending))
        # 证据 sidecar：pending → 与正式 TTML 同名的 .overlap.json
        pend_ev = os.path.join(out_dir, f"{task_id}.pending.overlap.json")
        final_ev = os.path.splitext(final)[0] + ".overlap.json"
        if os.path.exists(pend_ev):
            try:
                os.replace(pend_ev, final_ev)
            except OSError:
                pass
        # 清理 pending 中间产物（保存后不再需要）
        for _p in (os.path.join(out_dir, f"{task_id}.pending.ttml"), pend_ev):
            try:
                if os.path.exists(_p):
                    os.remove(_p)
            except OSError:
                pass
        task.result_path = final
        # 记住"这个 task 的正式产物在哪"：重启后编辑台按这个找 TTML
        try:
            edit_api.atomic_write_text(
                os.path.join(out_dir, f"{task_id}.result.json"),
                json.dumps({"ttml": final}, ensure_ascii=False))
        except Exception:
            pass
        self._json({"ok": True, "path": final})

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
                     else edit_api.edit_path_for(task_id, (task.out_dir if task else None) or OUTPUT_DIR))
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
        _persist = bool(body.get("persist", True))
        out = edit_api.apply_doc(doc, edit_path,
                                 (task.result_path if task else None) if _persist else None,
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

    # ---- 输出目录：用户可选（界面「歌词保存在哪里」）----
    #
    # ⚠️ 必须**真的写进去**：之前界面没有这个参数，用户抱怨"没法选保存位置"。
    # 校验顺序：绝对路径 → 建目录 → 真写一个探针文件 → 通过才用。
    # 任何一步失败都**回退默认目录并写进任务日志**（不静默忽略用户的设置）。
    @staticmethod
    def _resolve_out_dir(raw):
        """返回 (目录, 失败原因)。原因必须能显示给用户 —— 用户选了不可写目录时必须明说。"""
        raw = str(raw or "").strip()
        if not raw:
            return OUTPUT_DIR, ""
        try:
            p = os.path.abspath(raw)
            os.makedirs(p, exist_ok=True)
            probe = os.path.join(p, ".l2t_write_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return p, ""
        except Exception as exc:
            return "", f"{type(exc).__name__}: {exc}"

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
        _wanted, _why = self._resolve_out_dir(body.get("output_dir"))
        task.out_dir = _wanted or OUTPUT_DIR
        TASKS[task.id] = task
        if str(body.get("output_dir") or "").strip() and not _wanted:
            task.log(f"[输出目录] 你选的目录不可用（{_why}），已改用默认目录：{OUTPUT_DIR}")
        elif _wanted and _wanted != OUTPUT_DIR:
            task.log(f"[输出目录] 产物将写入：{_wanted}")
        # 把音频路径落到盘上：编辑态本身能从磁盘恢复（edit_api.load_doc），
        # 但 TASKS 是进程内的，**重启后就没声音了** —— 编辑台能打开却没音频、
        # 没播放高亮。存一份 sidecar 让试听也能跨重启。
        try:
            _src_path = os.path.join(task.out_dir or OUTPUT_DIR, f"{task.id}.src.json")
            edit_api.atomic_write_text(_src_path, json.dumps(
                {"audio": os.path.abspath(audio), "lyrics": os.path.abspath(lyrics)},
                ensure_ascii=False))
        except Exception:
            pass
        threading.Thread(target=_run_task, args=(task,), daemon=True).start()
        self._json({"task_id": task.id})


def _proxy_status() -> dict:
    """代理缓存现状 + 后台生成状态（给界面显示/给自检断言）。"""
    st = audio_proxy.stats()
    with _PROXY_LOCK:
        building = sorted(k for k, v in _PROXY_STATE.items() if v.get("state") == "building")
        errors = {os.path.basename(k): v.get("note", "") for k, v in _PROXY_STATE.items()
                  if v.get("state") == "error"}
    st["building"] = building
    st["errors"] = errors
    return st


def _kickoff_proxy_build(source: str) -> bool:
    """后台把这个源文件转成小体积代理。返回是否**本次真的启动了**转码。

    幂等：同一个源已在转或已转好就不重复启动
    （播放器会并发发多个 Range 请求，不做幂等会起一堆 ffmpeg）。
    """
    src = os.path.abspath(source)
    with _PROXY_LOCK:
        cur = _PROXY_STATE.get(src) or {}
        if cur.get("state") == "building":
            return False
        _PROXY_STATE[src] = {"state": "building", "note": "", "ts": time.time()}

    def _work():
        try:
            path, note = audio_proxy.build(src)
            with _PROXY_LOCK:
                _PROXY_STATE[src] = {"state": "ready", "note": note,
                                     "path": path, "ts": time.time()}
        except Exception as e:                              # noqa: BLE001
            with _PROXY_LOCK:
                _PROXY_STATE[src] = {"state": "error",
                                     "note": f"{type(e).__name__}: {e}", "ts": time.time()}

    threading.Thread(target=_work, daemon=True).start()
    return True


def _audio_from_sidecar(task_id: str) -> str:
    """服务重启后用 ``outputs/<id>.src.json`` 找回这个任务用的音频路径。

    编辑态本来就是从磁盘恢复的（``edit_api.load_doc``），音频也应当同样能恢复，
    否则重启后会出现"编辑台打得开、但没声音、播放高亮不动"。
    ⚠️ 仍然只能返回 uploads/ 内的路径 —— sidecar 由本进程写，但校验照旧。
    """
    if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
        return ""
    task = TASKS.get(task_id)
    p = os.path.join((task.out_dir if task else None) or OUTPUT_DIR, f"{task_id}.src.json")
    try:
        with open(p, encoding="utf-8") as f:
            return str(json.load(f).get("audio") or "")
    except (OSError, ValueError):
        return ""


def safe_audio_path(raw: str, trusted: str = ""):
    """把客户端给的音频路径收敛到 uploads/ 内。

    返回 ``(绝对路径, None)`` 或 ``(None, (状态码, 错误体))``。
    单独抽出来是为了能**离线自测**这条防目录穿越逻辑（不用起 HTTP）。
    """
    if not raw:
        return None, (404, {"error": "没有音频路径"})
    path = os.path.abspath(raw)
    root = os.path.abspath(UPLOAD_DIR)
    # ⚠️⚠️ **该任务自己记录过的音频视为可信**（2026-10-16 用户实测：编辑台播放没反应）：
    # 原来只允许 uploads/ 内的音频 ⇒ 用户直接选自己电脑上的歌（如 E:\音乐\…）时
    # `/api/audio` 直接 **403** ⇒ 播放器拿不到音频、`Play()` 静默无效。
    # 网页端之所以正常，是因为它**先把文件上传进 uploads/**。
    # 这里按原理放宽：**只**信任"这个任务自己的 sidecar 里记的路径"；
    # `?id=` 仍然读不了任意文件（要读到某文件，必须先真的用它转过一次）。
    if trusted and path == os.path.abspath(trusted):
        if not os.path.isfile(path):
            return None, (404, {"error": "音频文件不存在", "where": path})
        return path, None
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
            allow_same_agent_overlap=bool(p.get("allow_same_agent_overlap", False)),
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

        _out_dir = task.out_dir or OUTPUT_DIR
        _auto_save = bool(p.get("auto_save", True))
        # auto_save=false 时先写 .pending：pipeline **只有拿到最终路径才写拖音证据**，
        # 不能传 None；用户点「保存」后再把 pending 改名成正式产物（证据一起改名）。
        _final_path = os.path.join(_out_dir, f"{task.id}.ttml")
        _gen_path = _final_path if _auto_save else os.path.join(_out_dir, f"{task.id}.pending.ttml")

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
            # ⚠️ 必须有 output_path：生成链路只有在拿到**最终产物路径**时才会
            # 写出拖音证据 sidecar（`<id>.overlap.json`）。以前这里是 None，
            # 结果 Web/后端路径**永远没有证据** ⇒ 编辑台保存时会把长尾收回。
            output_path=_gen_path,
            translation_text=translation_text,
            translation_lang=str(p.get("translation_lang", "") or ""),
            songwriters=[s.strip() for s in str(p.get("songwriters", "")).split(",") if s.strip()],
        )

        out_path = _final_path
        if _auto_save:
            # 原子写：先写同目录临时文件再 os.replace，中途崩溃不会留下半截 TTML。
            edit_api.atomic_write_text(out_path, result.ttml)
            task.result_path = out_path
        else:
            # pipeline 已把 TTML 与证据 sidecar 写到 <id>.pending.* 旁边，等用户点保存
            task.result_path = None
            task.log("（产物尚未保存：点「保存 TTML」后才写入正式文件）")

        task.ttml = result.ttml
        # 编辑台：生成即建立编辑态（内存 + outputs/<id>.edit.json 的初始快照）
        task.edit_path = edit_api.edit_path_for(task.id, task.out_dir or OUTPUT_DIR)
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
    except TaskCancelled:
        # ⚠️⚠️ **必须排在 `except Exception` 之前**（2026-10-16 真实验证抓到）：
        # 取消是靠 `Task.log()` 里抛 `TaskCancelled` 实现的，如果被通用分支接走，
        # 用户点了取消却看到 **"失败"** —— 属于误报（本项目明令禁止）。
        # 注意：这里**不能调 `task.log()`**（它会因为 cancelled 又抛一次），
        # 所以直接写环形缓冲。
        task.status = "cancelled"
        task.stage = "已取消"
        task.error = None
        with task.log_lock:
            task.logs.append({"t": time.strftime("%H:%M:%S"),
                              "m": "⊘ 已取消（用户中断；产物未写入）"})
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
