"""真·重启测试：服务重启（TASKS 清空）后还能不能试听。

上一版是假通过的：测试进程里 `W.TASKS.pop()` 只改了**测试进程自己的副本**，
服务器进程里任务还在，HTTP 照样 200 —— 等于什么都没验证。
这里必须**真的把服务杀掉再起一个**，再用同一个 task id 请求。

用法（由 PowerShell 驱动，见文件末尾注释）：
    python tools/test_restart_audio.py --make     # 建任务，打印 TASK_ID 和它在的端口
    python tools/test_restart_audio.py --check <task_id> <总字节数>
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave

BASE = os.environ.get("L2T_BASE", "http://127.0.0.1:8797")


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


def make():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 2)
    wav = buf.getvalue()
    st, up, _ = req("POST", "/api/upload", {
        "filename": "_restart_e2e.wav",
        "data": base64.b64encode(wav).decode()})
    if st != 200:
        print("上传失败", st, up)
        return 1
    st, up2, _ = req("POST", "/api/upload", {
        "filename": "_restart_e2e.lrc",
        "data": base64.b64encode(b"[00:00.50]test\n").decode()})
    st, conv, _ = req("POST", "/api/convert", {
        "audio": up["path"], "lyrics": up2["path"], "title": "restart-test",
        "separate_vocals": False})
    if st != 200 or not conv.get("task_id"):
        print("建任务失败", st, conv)
        return 1
    # 等转换真的跑完再交回：否则服务被杀时 TTML 还没生成，
    # "编辑态能不能跨重启恢复"就没得测（上一版因此假失败）
    deadline = time.time() + 180
    status = {}
    while time.time() < deadline:
        st, status, _ = req("GET", f"/api/task?id={conv['task_id']}")
        if st == 200 and (status.get("done") or status.get("progress", 0) >= 100):
            break
        time.sleep(2)
    print(f"TASK_ID={conv['task_id']}")
    print(f"WAITED_STATUS={status.get('state') or status.get('stage') or status}")
    # 建完立刻验证一次（此时任务在内存里）
    st, data, _ = req("GET", f"/api/audio?id={conv['task_id']}", raw=True)
    print(f"BEFORE_STATUS={st}")
    print(f"BEFORE_BYTES={len(data) if isinstance(data, bytes) else 0}")
    print(f"WAV_SIZE={len(wav)}")
    return 0


def check(task_id, expect_bytes):
    """重启后调用：TASKS 必是空的，能拿到音频就说明 sidecar 生效"""
    ok = True

    def c(name, cond, detail=""):
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   {detail}"))
        ok = ok and cond

    st, data, hdr = req("GET", f"/api/audio?id={task_id}", raw=True)
    c("重启后整份下载 200", st == 200, str(st))
    c("字节数正确", isinstance(data, bytes) and len(data) == int(expect_bytes),
      f"{len(data) if isinstance(data, bytes) else '?'} vs {expect_bytes}")
    c("仍是 audio/wav", "audio/wav" in (hdr.get("Content-Type") or ""),
      str(hdr.get("Content-Type")))
    st, part, hdr = req("GET", f"/api/audio?id={task_id}",
                        headers={"Range": "bytes=0-99"}, raw=True)
    c("重启后 Range 仍 206", st == 206 and len(part) == 100, f"{st} {len(part)}")

    # 编辑态也应能从磁盘恢复（同一套跨重启语义）
    st, ed, _ = req("GET", f"/api/edit?id={task_id}")
    c("编辑态也能取到", st == 200 and "doc" in ed, f"{st} {str(ed)[:70]}")

    # 不存在的 id 仍要 404（别把 sidecar 兜底做成万能放行）
    st, body, _ = req("GET", "/api/audio?id=__definitely_not_a_task__")
    c("不存在的 id 仍 404", st == 404, str(st))

    print("重启后试听 ✓" if ok else "有失败项 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--make" in sys.argv:
        sys.exit(make())
    if "--check" in sys.argv:
        i = sys.argv.index("--check")
        sys.exit(check(sys.argv[i + 1], sys.argv[i + 2]))
    print(__doc__)
    sys.exit(2)
