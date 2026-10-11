"""驱动真·重启测试：起服务 → 建任务 → 杀掉 → 再起 → 用同一 id 验证。

必须真的换进程，否则测不出 sidecar 兜底（测试进程里的 TASKS 和服务器是两份）。
"""
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = 8798
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", L2T_PORT=str(PORT),
           L2T_BASE=f"http://127.0.0.1:{PORT}")


def wait_up(timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/info", timeout=3):
                return True
        except Exception:
            time.sleep(0.6)
    return False


def start():
    p = subprocess.Popen([PY, "web_server.py"], cwd=ROOT, env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_up():
        raise SystemExit("服务起不来")
    return p


def stop(p):
    """⚠️ 不要用 CTRL_BREAK_EVENT：它发给整个控制台进程组，
    会把跑测试的那个 shell 一起打断（实测把 PowerShell 打进了调试模式）。"""
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=10)
    time.sleep(1.5)          # 等端口释放


def run(args):
    r = subprocess.run([PY] + args, cwd=ROOT, env=ENV, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


ok = True
p1 = start()
try:
    rc, out = run(["tools/test_restart_audio.py", "--make"])
    print(out.strip())
    m = re.search(r"TASK_ID=(\S+)", out)
    size = re.search(r"WAV_SIZE=(\d+)", out)
    before = re.search(r"BEFORE_STATUS=(\d+)", out)
    if not m or not size:
        print("建任务阶段失败")
        ok = False
    else:
        task_id, nbytes = m.group(1), size.group(1)
        if before and before.group(1) != "200":
            print(f"⚠ 重启前就已经拿不到音频（status={before.group(1)}）")
            ok = False
finally:
    stop(p1)

if ok:
    print("\n--- 已杀掉服务，重新起一个（TASKS 清空）---")
    p2 = start()
    try:
        rc, out = run(["tools/test_restart_audio.py", "--check", task_id, nbytes])
        print(out.strip())
        ok = ok and rc == 0
    finally:
        stop(p2)

print("\n真·重启测试:", "通过 ✓" if ok else "失败 ✗")
sys.exit(0 if ok else 1)
