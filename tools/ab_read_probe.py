"""A/B 实测：哪种读法能在"对方发一半就强制断开"时保住已下数据？

    python .tmp/ab_read.py

背景：`desktop/selftest.py` 第 14 组时好时坏，报 `.part` 是空的。
我不再猜，直接把三种读法在同一条件下各跑 8 次，看谁稳定。
"""
from __future__ import annotations

import http.client
import io
import os
import socketserver
import sys
import threading

BODY = b"A" * (256 * 1024)


def make_server():
    class H(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                self.request.recv(8192)
                # 发一半 + 强制断开（RST），复刻测试里的场景
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n"
                    % len(BODY) + BODY[: len(BODY) // 2])
                import struct
                self.request.setsockopt(1, 2, 1000)      # SO_REUSEADDR
                self.request.close()
            except Exception:
                pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    srv.timeout = 1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def trial(mode: str) -> int:
    """返回"拿到多少字节"。异常一律吞掉，只关心有没有数据到手。"""
    srv = make_server()
    url = f"http://127.0.0.1:{srv.server_address[1]}/x"
    import urllib.request
    got = 0
    try:
        r = urllib.request.urlopen(url, timeout=10)
        if mode == "read":
            f = r.read
        elif mode == "read1":
            f = getattr(r, "read1", None) or r.read
        elif mode == "raw":
            f = r.fp.read
        elif mode == "readinto":
            buf = bytearray(1 << 20)
            n = r.readinto(buf)
            got = n or 0
            r.close()
            srv.shutdown()
            return got
        while True:
            c = f(1 << 20)
            if not c:
                break
            got += len(c)
    except Exception:
        pass
    finally:
        try:
            r.close()
        except Exception:
            pass
        srv.shutdown()
    return got


def main() -> int:
    for mode in ("read", "read1", "raw", "readinto"):
        hits = []
        for _ in range(8):
            hits.append(trial(mode))
        nz = sum(1 for h in hits if h > 0)
        print(f"  {mode:9s} 8 次里拿到数据的: {nz}/8   各次字节: {hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
