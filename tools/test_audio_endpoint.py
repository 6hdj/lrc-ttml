"""端到端测编辑台音频端点 /api/audio（含 Range）

Range 是重点：`<audio>` 拖进度条和 `currentTime = x` 都依赖 206 分片响应，
没有它 50MB 的 wav 每次 seek 都要整份重下。
"""
import base64
import hashlib
import io
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import wave

BASE = os.environ.get("L2T_BASE", "http://127.0.0.1:8794")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

ok = True


def req(method, path, body=None, headers=None, raw=False):
    data = None if body is None else json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            content = resp.read()
            return resp.status, (content if raw else json.loads(content or b"{}")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        content = e.read()
        try:
            return e.code, json.loads(content or b"{}"), dict(e.headers)
        except ValueError:
            return e.code, content, dict(e.headers)


def check(name, cond, detail=""):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   {detail}"))
    ok = ok and cond


print("[1] 没有任务时的行为")
st, body, _ = req("GET", "/api/audio?id=__nope__")
check("404 + 结构化错误", st == 404 and "hint" in body, f"{st} {str(body)[:90]}")

print("\n[2] 造一个小音频 + 歌词，走 upload → convert 建任务")
buf = io.BytesIO()
with wave.open(buf, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"\x00\x00" * 16000 * 2)      # 2 秒静音
wav_bytes = buf.getvalue()

st, up, _ = req("POST", "/api/upload", {
    "filename": "_audio_e2e.wav", "data": base64.b64encode(wav_bytes).decode()})
check("音频上传成功", st == 200 and os.path.exists(up.get("path", "")), str(up)[:110])
audio_path = up.get("path", "")

lrc = "[00:00.50]测试一句\n[00:01.20]再来一句\n"
st, up2, _ = req("POST", "/api/upload", {
    "filename": "_audio_e2e.lrc", "data": base64.b64encode(lrc.encode("utf-8")).decode()})
lyrics_path = up2.get("path", "")

st, conv, _ = req("POST", "/api/convert", {"audio": audio_path, "lyrics": lyrics_path,
                                           "title": "音频端点测试", "separate_vocals": False})
task_id = conv.get("task_id", "")
check("转换任务已创建（不必等它跑完）", st == 200 and bool(task_id), f"{st} {conv}")

print("\n[3] 整份下载")
st, data, hdr = req("GET", f"/api/audio?id={task_id}", raw=True)
check("200", st == 200, str(st))
check("字节数与文件一致", len(data) == len(wav_bytes), f"{len(data)} vs {len(wav_bytes)}")
check("内容一致（md5）",
      hashlib.md5(data).hexdigest() == hashlib.md5(wav_bytes).hexdigest())
check("声明 Accept-Ranges: bytes", hdr.get("Accept-Ranges") == "bytes", str(hdr.get("Accept-Ranges")))
check("Content-Type 是 audio/wav", "audio/wav" in (hdr.get("Content-Type") or ""),
      str(hdr.get("Content-Type")))

print("\n[4] Range：前 1024 字节")
st, part, hdr = req("GET", f"/api/audio?id={task_id}", headers={"Range": "bytes=0-1023"}, raw=True)
check("206 Partial Content", st == 206, str(st))
check("长度 1024", len(part) == 1024, str(len(part)))
check("Content-Range 正确",
      hdr.get("Content-Range") == f"bytes 0-1023/{len(wav_bytes)}", str(hdr.get("Content-Range")))
check("内容与源文件前 1024 字节一致", part == wav_bytes[:1024])

print("\n[5] Range：中段 bytes=1000-1999")
st, part, hdr = req("GET", f"/api/audio?id={task_id}", headers={"Range": "bytes=1000-1999"}, raw=True)
check("206 且长度 1000", st == 206 and len(part) == 1000, f"{st} {len(part)}")
check("内容对位正确", part == wav_bytes[1000:2000])

print("\n[6] Range：开区间 bytes=100-（到末尾）")
st, part, hdr = req("GET", f"/api/audio?id={task_id}", headers={"Range": "bytes=100-"}, raw=True)
check("206 且长度 = 总长-100", st == 206 and len(part) == len(wav_bytes) - 100,
      f"{st} {len(part)}")
check("Content-Range 到末尾",
      hdr.get("Content-Range") == f"bytes 100-{len(wav_bytes) - 1}/{len(wav_bytes)}",
      str(hdr.get("Content-Range")))

print("\n[7] Range：后缀 bytes=-100（播放器有时这么发）")
st, part, hdr = req("GET", f"/api/audio?id={task_id}", headers={"Range": "bytes=-100"}, raw=True)
check("206 且是最后 100 字节", st == 206 and part == wav_bytes[-100:], f"{st} {len(part)}")

print("\n[8] Range：越界 → 416")
st, body, hdr = req("GET", f"/api/audio?id={task_id}",
                    headers={"Range": f"bytes={len(wav_bytes) + 10}-"}, raw=True)
check("416 Range Not Satisfiable", st == 416, str(st))
check("带 Content-Range: bytes */总长",
      (hdr.get("Content-Range") or "") == f"bytes */{len(wav_bytes)}",
      str(hdr.get("Content-Range")))

print("\n[9] sidecar 逻辑（单元层面）")
import web_server as W

sidecar = os.path.join(W.OUTPUT_DIR, f"{task_id}.src.json")
check("建任务时写了 sidecar", os.path.exists(sidecar), sidecar)
if os.path.exists(sidecar):
    got = W._audio_from_sidecar(task_id)
    check("sidecar 能取回音频路径", os.path.abspath(got) == os.path.abspath(audio_path),
          got)
    check("sidecar 名字带 ../ 时拒绝", W._audio_from_sidecar("../etc/passwd") == "")
    check("sidecar 取回的路径仍受 uploads/ 约束",
          W.safe_audio_path("C:/Windows/win.ini")[1][0] == 403)

print("\n[10] 清理测试文件")
for p in (audio_path, lyrics_path):
    try:
        os.remove(p)
    except OSError:
        pass

print("\n" + ("音频端点 ✓" if ok else "有失败项 ✗"))
sys.exit(0 if ok else 1)
