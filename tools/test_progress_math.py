"""验证下载进度的**字节换算**（用户实测"下载中 9.0B/528.8MB 看着像卡死"）。

    python tools/test_progress_math.py

根因：`_http_get_to_file` 的回调传的是**百分比（0-100）**，而
`runtime_service.download` 把它当成**字节数**用 —— 于是
9% 被显示成 "9.0B"，进度条按 9×100/528.8M 算 ≈ 0.000002% 永远 0%。

这条用例起一个**真**的本地 HTTP 服务，下完一个已知大小的载荷，
断言进度回调给出来的字节数**和载荷大小同量级**（而不是 0~100）。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import socketserver
import sys
import threading
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import http.server  # noqa: E402

from test_runtime_install import _start_local  # noqa: E402

_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


SB = os.path.join(HERE, ".tmp", "progress-math")
shutil.rmtree(SB, ignore_errors=True)
os.makedirs(SB, exist_ok=True)

# 造一个 ~2MB 的 zip（够大，进度会回调很多次；又够小，跑得快）
payload = os.urandom(2 * 1024 * 1024)
zp = os.path.join(SB, "pack.zip")
with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("bin/cublas64_12.dll", payload)
data = io.open(zp, "rb").read()
digest = hashlib.sha256(data).hexdigest()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self._send_slow(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self._send_slow(data)

    def _send_slow(self, blob: bytes):
        """分块慢慢发 —— 让下载持续 >1.2 秒。

        ⚠️ 不限速的话 localhost 上 2MB 不到 1 秒就下完，而
        `_http_get_to_file` **每 0.4 秒才回调一次** → 只回调两三次、
        字节数还没涨到接近总量，这条用例就**测不到**它要测的东西
        （我第一版就是这么写的，结果误报 FAIL）。
        """
        import time
        step = max(1, len(blob) // 8)
        for i in range(0, len(blob), step):
            self.wfile.write(blob[i:i + step])
            self.wfile.flush()
            time.sleep(0.2)


import urllib.request as _ur  # noqa: E402

_ur.getproxies = lambda: {}          # 本机代理会拦本地请求（见 test_runtime_install）

srv = _start_local(socketserver.TCPServer, H)
url = f"http://127.0.0.1:{srv.server_address[1]}/cuda.zip"

from desktop.services import runtime_service as rs  # noqa: E402

manifest = {"schema_version": 1, "runtimes": [{
    "id": "cuda", "name": "假 CUDA", "purpose": "进度换算",
    "download_policy": "on_demand", "approx_size": len(data),
    "installed_size": len(data), "verify_files": ["bin/cublas64_12.dll"],
    "archives": [{"filename": "cuda.zip", "size": len(data),
                  "sha256": digest, "urls": [url]}],
}]}
mgr = rs.RuntimeManager(manifest=manifest, root=os.path.join(SB, "rt"))

prog = []


def _p(pct, msg):
    prog.append((pct, msg))


print("=" * 74)
print("  下载进度的字节换算")
print("=" * 74)
okdl = mgr.download("cuda", log=lambda m: None, progress=_p)
check(okdl, "真下载成功（本地 HTTP）")
print(f"  回调 {len(prog)} 次；载荷 {len(data) / 1048576:.1f} MB")
print(f"  最后一条: pct={prog[-1][0]}  msg={prog[-1][1] if prog else ''}")

check(bool(prog), "下载过程中有进度回调")
pcts = [x[0] for x in prog if x[0] is not None]
check(bool(pcts) and max(pcts) > 90,
      "进度百分比最终涨到 90% 以上（不是卡在 0%）",
      f"最大 {max(pcts) if pcts else None}")

# ⚠️ 核心判据：消息里的"已下字节"必须和载荷同量级
# （旧 bug 会把 0~100 的百分比当字节显示出来）
import re  # noqa: E402
sizes = []
for _pct, msg in prog:
    m = re.search(r"下载中 ([\d.]+)([KMG]?B)/", msg or "")
    if m:
        v = float(m.group(1))
        mult = {"B": 1, "KB": 1024, "MB": 1048576, "GB": 1073741824}[m.group(2)]
        sizes.append(v * mult)
biggest = max(sizes) if sizes else 0
print(f"  消息里出现过的最大已下字节 = {biggest / 1048576:.2f} MB")
check(biggest > len(data) * 0.5,
      "**消息里的字节数接近载荷大小**（旧 bug 只会显示 0~100 字节）",
      f"最大 {biggest:.0f} 字节 vs 载荷 {len(data)} 字节")

print()
print("=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
srv.shutdown()
sys.exit(1 if _fail else 0)
