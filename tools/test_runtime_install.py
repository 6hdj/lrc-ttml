"""守「运行时安装必须是原子的，且取消要真的能停」。

    python tools/test_runtime_install.py

## 为什么要测这个（第 21 轮修的两个真问题）

**① 解压直接写进 site ⇒ 可能"假就绪"。**
原来 `_safe_rmtree(site)` 之后就往 `site` 里解压。解到一半崩了/被强杀，
`site` 里就是半截文件；而 `check()` 看到 `site` 存在就会去验证 ——
万一关键文件恰好已经解出来，就判 **READY**（半截也算装好）。
现在改成解到 `site + ".extract"`，**验过之后**才换上去。

**② `extractall` 完全不理会取消。**
529 MB 的 zip 解压要几十秒，用户点取消却停不下来，装完还显示"完成"。
任务书 E 明确要求"失败/取消"要真的能取消。
现在逐条解压并检查取消，取消时**清掉临时目录**、`site` 保持"没装"。

判据（本测试守的）：**要么完整可用，要么明明白白地没有** ——
绝不能出现"看起来装了、其实半截"。
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

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def make_zip(path: str) -> str:
    """造一个"像 CUDA 包"的 zip：若干文件铺在 bin/ 下，条目要**够多**。

    ⚠️ 条目数很重要：取消用例必须确保"取消发生在**解压循环内部**"。
    第 21 轮第一版只放了 41 个条目，解压瞬间就完了，
    取消信号落到解压**前后**的窗口里 —— 于是"去掉解压时的取消检查"
    测试**照样通过**（阴性对照失败，说明测试是假的）。现在放到 400 条，
    解压足够长，取消一定落在循环里。
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("bin/cublas64_12.dll", b"A" * 1024)
        for i in range(400):
            z.writestr(f"bin/filler{i:03d}.bin", os.urandom(32 * 1024))
    return path


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def serve(path: str):
    data = open(path, "rb").read()
    digest = hashlib.sha256(data).hexdigest()

    class H(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                self.request.recv(8192)
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n"
                    % len(data) + data)
            except Exception:
                pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    srv.timeout = 1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, digest, len(data)


def main() -> int:
    from desktop.services import runtime_service as rs

    sb = os.path.join(HERE, ".tmp", "rt-install")
    shutil.rmtree(sb, ignore_errors=True)
    os.makedirs(sb, exist_ok=True)
    zpath = make_zip(os.path.join(sb, "pack.zip"))
    srv, digest, size = serve(zpath)
    url = f"http://127.0.0.1:{srv.server_address[1]}/cuda.zip"
    root = os.path.join(sb, "rt")

    manifest = {"schema_version": 1, "runtimes": [{
        "id": "cuda", "name": "假 CUDA", "purpose": "测试",
        "download_policy": "on_demand", "approx_size": size,
        "installed_size": size,
        "verify_files": ["bin/cublas64_12.dll"],
        "archives": [{"filename": "cuda.zip", "size": size,
                      "sha256": digest, "urls": [url]}],
    }]}
    mpath = os.path.join(sb, "rt.json")
    json.dump(manifest, open(mpath, "w", encoding="utf-8"), ensure_ascii=False)

    print("=" * 74)
    print("  运行时安装：原子性 + 取消")
    print("=" * 74)

    # ---- 1. 正常安装：必须先在 .extract 里，最后才出现完整的 site ----
    print("\n[1] 正常安装")
    mgr = rs.RuntimeManager(manifest=manifest)
    mgr._root_override = root
    seen = []

    def prog(pct, msg):
        seen.append((pct, msg))

    st = mgr.download("cuda", log=lambda m: None, progress=prog)
    site = mgr.site_dir("cuda")
    check(st.status == "ready", "装完状态 ready", st.detail)
    check(os.path.isfile(os.path.join(site, "bin", "cublas64_12.dll")),
          "关键文件在最终 site 目录里")
    check(not os.path.isdir(site + ".extract"),
          "**临时目录 `.extract` 已清掉**（不留残渣）")
    check(any(m and "解压" in str(m) for _p, m in seen),
          "进度里能看到「解压」阶段（不再是一路 None）")

    # ---- 2. 取消解压：必须停下，且 site 不能出现 ----
    print("\n[2] 解压途中取消")
    shutil.rmtree(root, ignore_errors=True)
    mgr2 = rs.RuntimeManager(manifest=manifest)
    mgr2._root_override = root
    # ⚠️ **用进度回调当取消时机，而不是数调用次数**（第一版就是数次数，
    # 结果取消落在解压之外的窗口，阴性对照证明测试测不出坏）。
    # 解压阶段才会报 `解压 i/n`，所以"看到解压进度就取消"**一定**落在循环里。
    state = {"started_cancel": False}

    def cancel_on_unpack() -> bool:
        return state["started_cancel"]

    def prog2(pct, msg):
        if msg and "解压" in str(msg):
            state["started_cancel"] = True

    site2 = mgr2.site_dir("cuda")
    cancelled = False
    try:
        mgr2.download("cuda", log=lambda m: None, progress=prog2,
                      cancel=cancel_on_unpack)
    except rs.DownloadCancelled:
        cancelled = True
    except Exception as e:                              # noqa: BLE001
        print(f"       （抛出 {type(e).__name__}: {str(e)[:80]}）")
    check(state["started_cancel"], "确实进入了**解压阶段**才取消（用例有效）")
    check(cancelled, "取消时抛 DownloadCancelled（不是静默装完）")
    check(not os.path.isdir(site2),
          "**取消后 site 不存在**（不会留下半截被当成已装）")
    check(not os.path.isdir(site2 + ".extract"),
          "取消后临时目录也清干净了")
    st2 = mgr2.check("cuda")
    check(st2.status != "ready", "取消后状态不是 ready", st2.status)

    srv.shutdown()
    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
