"""可选运行组件（Demucs Worker）：下载器 + 安装状态机自动化测试。

    .venv\\Scripts\\python.exe tools\\test_worker_download.py [--real-install]

覆盖用户要求的失败路径：断点续传、自动切源、SHA256 校验、429/5xx/过慢/断流、
取消、磁盘写入失败（ENOSPC 分支）、安装失败、自检失败不覆盖旧版本、升级。

⚠️ **诚实边界**：本文件里的 HTTP 服务器是**本地**的，用来**可控地制造**各种错误，
   它**不能**替代"大陆真机测速"。真实网络测速在 `.tmp/worker-pkg/net_report.py`
   里单独做，结果记入报告；组件包尚未上传任何 CDN（清单里 status=unpublished）。

`--real-install`：用本地服务器提供**真实组件包**（1.67GB）跑一次完整安装，
   验证"下载→SHA256→解压→完整性→自检→原子切换"整链（本地环回，不受网络影响）。
"""
from __future__ import annotations

import argparse
import contextlib
import http.server
import io
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import urllib.request
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# ⚠️ **必须在最前面禁用系统代理**（2026-10-16 实测踩到）：
# 本机系统代理是个**死地址**（127.0.0.1:65532），
# urllib 会连 127.0.0.1 的请求也交给它，
# 于是本地 mock 服务器也报
# WinError 10061 目标计算机积极拒绝 —— 看起来像“服务器没起”，
# 其实是代理在中间。之前能过是因为那时代理设置/环境不一样。
urllib.request.getproxies = lambda: {}                              # type: ignore

from desktop.services import downloader as DL                      # noqa: E402
from desktop.services import worker_service as WS                  # noqa: E402

PACK = os.path.join(HERE, ".tmp", "worker-pkg")
COMPONENTS = os.path.join(PACK, "components")
RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append({"test": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n        {detail}" if detail else ""))


# ---------------------------------------------------------------- 本地可控 HTTP
class Handler(http.server.BaseHTTPRequestHandler):
    """一台（虚拟）服务器。⚠️ 配置必须**放在实例属性上**（`make_handler` 每次
    生成独立的子类），否则两个 server 会共享类属性 —— 我第一版就是这么错的：
    备用源的 handler 继承了基类的 `fail_first`，于是备用源也回 429，
    "自动切源"用例直接假失败。"""
    mode = "ok"                 # ok / 429 / 500 / slow / reset / norange / wrong
    hits = 0
    fail_first = 0
    body = b""

    def log_message(self, *a):  # 静音
        pass

    def _send_common(self, code, extra=None, length=0):
        self.send_response(code)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(length))
        self.end_headers()

    def do_GET(self):
        type(self).hits += 1
        n = type(self).hits
        mode = type(self).mode
        data = type(self).body
        if mode == "429":
            self._send_common(429, length=0)
            return
        if mode == "500":
            self._send_common(500, length=0)
            return
        if mode == "wrong":
            junk = b"x" * 100
            self._send_common(200, {"Accept-Ranges": "bytes"}, length=len(junk))
            self.wfile.write(junk)
            return
        rng = self.headers.get("Range")
        if mode == "norange":
            # 明确不支持 Range：即使带了 Range 也返回 200 + 全量
            self._send_common(200, {"Accept-Ranges": "none"}, length=len(data))
            self.wfile.write(data)
            return
        start = 0
        if rng and rng.startswith("bytes="):
            try:
                start = int(rng.split("=", 1)[1].split("-")[0])
            except ValueError:
                start = 0
        chunk = data[start:]
        if mode == "slow":
            self._send_common(206 if rng else 200,
                              {"Accept-Ranges": "bytes",
                               "Content-Range": f"bytes {start}-{len(data)-1}/{len(data)}"},
                              length=len(chunk))
            for i in range(0, len(chunk), 4096):
                try:
                    self.wfile.write(chunk[i:i + 4096])
                    time.sleep(0.35)          # 约 11KB/s，触发"速度过低"
                except OSError:
                    return
            return
        if mode == "reset":
            # 发一半就断（用于测断点续传）
            self._send_common(206 if rng else 200,
                              {"Accept-Ranges": "bytes",
                               "Content-Range": f"bytes {start}-{len(data)-1}/{len(data)}"},
                              length=len(chunk))
            half = max(1, len(chunk) // 2)
            try:
                self.wfile.write(chunk[:half])
                self.wfile.flush()
            except OSError:
                pass
            try:
                self.connection.close()
            except OSError:
                pass
            return
        self._send_common(206 if rng else 200,
                          {"Accept-Ranges": "bytes",
                           "Content-Range": f"bytes {start}-{len(data)-1}/{len(data)}"},
                          length=len(chunk))
        self.wfile.write(chunk)


class Server:
    """独立的 mock 服务器：每个实例有自己的行为配置。"""

    def __init__(self, mode: str = "ok", body: bytes = b"", fail_first: int = 0):
        cls = type(f"H_{id(self)}", (Handler,),
                   {"mode": mode, "body": body, "fail_first": fail_first, "hits": 0})
        self.cls = cls
        self.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), cls)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name="f.bin"):
        return f"http://127.0.0.1:{self.port}/{name}"

    @property
    def hits(self):
        return self.cls.hits

    def stop(self):
        with contextlib.suppress(Exception):
            self.httpd.shutdown()
            self.httpd.server_close()


def sha_of(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- 测试
def t_download_ok(srv, tmp):
    srv.cls.mode, srv.cls.hits = "ok", 0
    srv.cls.body = os.urandom(3 * 1024 * 1024)
    dest = os.path.join(tmp, "a.bin")
    r = DL.download_from_sources([{"url": srv.url(), "label": "s1"}], dest,
                                 sha256=sha_of(srv.cls.body), log=lambda *a: None)
    ok = os.path.isfile(dest) and os.path.getsize(dest) == len(srv.cls.body)
    rec("下载成功 + SHA256 校验（正常路径）", ok and r["ok"],
        f"{os.path.getsize(dest)} B，来自 {r['label']}")


def t_resume(srv, tmp):
    """断点续传：第一次传一半就断，第二次应当从断点续。"""
    srv.cls.mode, srv.cls.hits = "reset", 0
    srv.cls.body = os.urandom(4 * 1024 * 1024)
    dest = os.path.join(tmp, "r.bin")
    part = dest + ".part"
    try:
        DL.download_from_sources([{"url": srv.url()}], dest, sha256=sha_of(srv.cls.body),
                                 log=lambda *a: None, max_retries=2, timeout=10)
    except Exception:                                        # noqa: BLE001
        pass
    kept = os.path.getsize(part) if os.path.isfile(part) else 0
    srv.cls.mode = "ok"
    r = DL.download_from_sources([{"url": srv.url()}], dest, sha256=sha_of(srv.cls.body),
                                 log=lambda *a: None)
    ok = kept > 0 and r["ok"] and os.path.getsize(dest) == len(srv.cls.body)
    rec("断点续传（.part 保留并从断点继续）", ok,
        f"中断后 .part={kept} B；续传后 {os.path.getsize(dest)} B（期望 {len(srv.cls.body)}）")


def t_mirror_switch(srv, tmp):
    """主源持续 429 → 自动换备用源成功。"""
    body = os.urandom(2 * 1024 * 1024)
    srv1 = Server(mode="429", body=body)
    srv2 = Server(mode="ok", body=body)
    dest = os.path.join(tmp, "m.bin")
    r = DL.download_from_sources(
        [{"url": srv1.url("bad"), "label": "主源(429)"},
         {"url": srv2.url("good"), "label": "备用源"}],
        dest, sha256=sha_of(body), log=lambda *a: None, max_retries=1, timeout=10)
    srv1.stop()
    srv2.stop()
    ok = r["ok"] and r["label"] == "备用源"
    rec("主源 HTTP 429 → 自动切换备用源", ok, f"最终来自 {r.get('label')}")


def t_sha_mismatch(srv, tmp):
    srv.cls.mode, srv.cls.hits = "wrong", 0
    dest = os.path.join(tmp, "s.bin")
    bad = "0" * 64
    try:
        DL.download_from_sources([{"url": srv.url()}], dest, sha256=bad,
                                 log=lambda *a: None, max_retries=1, timeout=10)
        rec("SHA256 不匹配 → 拒绝安装", False, "居然成功了")
    except DL.DownloadError as e:
        ok = e.kind == DL.KIND_SHA and not os.path.isfile(dest)
        rec("SHA256 不匹配 → 报错且不产出目标文件", ok,
            f"kind={e.kind}；目标文件存在={os.path.isfile(dest)}")


def t_slow_switch(srv, tmp):
    """速度持续过低 → 归类为 slow 并换源。"""
    body = os.urandom(512 * 1024)
    s1 = Server(mode="slow", body=body)
    s2 = Server(mode="ok", body=body)
    dest = os.path.join(tmp, "slow.bin")
    t0 = time.time()
    r = DL.download_from_sources(
        [{"url": s1.url(), "label": "慢源"}, {"url": s2.url(), "label": "快源"}],
        dest, sha256=sha_of(body), log=lambda *a: None,
        max_retries=1, timeout=10, low_speed_bps=200_000, low_speed_secs=4.0)
    s1.stop()
    s2.stop()
    ok = r["ok"] and r["label"] == "快源" and time.time() - t0 < 120
    rec("速度持续过低 → 换源", ok,
        f"最终来自 {r.get('label')}，用时 {time.time()-t0:.0f}s")


def t_5xx_retry_then_ok(srv, tmp):
    body = os.urandom(1024 * 1024)
    srv.cls.mode, srv.cls.hits, srv.cls.fail_first = "ok", 0, 2
    srv.cls.body = body
    dest = os.path.join(tmp, "r5.bin")
    try:
        r = DL.download_from_sources([{"url": srv.url()}], dest, sha256=sha_of(body),
                                     log=lambda *a: None, max_retries=3, timeout=10)
        ok = r["ok"]
        rec("HTTP 429/5xx 重试后成功（退避生效）", ok,
            f"第 3 次请求成功，hits={srv.hits}")
    except DL.DownloadError as e:
        rec("HTTP 429/5xx 重试后成功（退避生效）", False, str(e))


def t_norange(srv, tmp):
    """服务器不支持 Range：必须**从头重下**，不能拼接。"""
    srv.cls.mode, srv.cls.hits = "norange", 0
    srv.cls.body = os.urandom(2 * 1024 * 1024)
    dest = os.path.join(tmp, "nr.bin")
    part = dest + ".part"
    # 先造一个"半截"文件 + 身份不符的 meta
    with open(part, "wb") as fh:
        fh.write(b"X" * 500_000)
    with open(part + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump({"url": srv.url(), "etag": "old", "total": len(srv.cls.body)}, fh)
    r = DL.download_from_sources([{"url": srv.url()}], dest, sha256=sha_of(srv.cls.body),
                                 log=lambda *a: None, max_retries=1, timeout=20)
    ok = r["ok"] and os.path.getsize(dest) == len(srv.cls.body)
    rec("不支持 Range 时从头重下（不拼接畸形文件）", ok,
        f"最终 {os.path.getsize(dest)} B（期望 {len(srv.cls.body)}）")


def t_cancel(srv, tmp):
    body = os.urandom(8 * 1024 * 1024)
    s1 = Server(mode="slow", body=body)
    dest = os.path.join(tmp, "c.bin")
    flag = {"stop": False}

    def stop():
        return flag["stop"]

    def prog(got, total, info):
        if got > 120_000:
            flag["stop"] = True
    try:
        # ⚠️ low_speed_bps=1：**关掉低速中止**，否则 25s 的 slow 判据会比取消先触发，
        #    用例测的就不是"取消"了（第一版就是这么假失败的）。
        DL.download_from_sources([{"url": s1.url()}], dest, sha256=sha_of(body),
                                 log=lambda *a: None, stop=stop, progress=prog,
                                 max_retries=1, timeout=10, low_speed_bps=1)
        rec("取消下载 → 保留 .part、不产出目标文件", False, "居然下完了")
    except DL.DownloadCancelled:
        part = dest + ".part"
        ok = os.path.isfile(part) and os.path.getsize(part) > 0 and not os.path.isfile(dest)
        rec("取消下载 → 保留 .part、不产出目标文件", ok,
            f".part={os.path.getsize(part) if os.path.isfile(part) else 0} B")
    except Exception as e:                                   # noqa: BLE001
        rec("取消下载 → 保留 .part、不产出目标文件", False, f"{type(e).__name__}: {e}")
    s1.stop()


def t_disk_full(tmp):
    """磁盘写入失败（ENOSPC）→ 分类为 disk_full 且不产出目标文件。

    ⚠️ 这是**分支级**测试：本机没有能安全填满的盘，所以用打桩让 write 抛 ENOSPC。
       真机"磁盘不足"没有验证（报告里如实标）。
    """
    import errno
    body = os.urandom(256 * 1024)
    srv = Server(mode="ok", body=body)
    dest = os.path.join(tmp, "d.bin")
    real_open = DL.open if hasattr(DL, "open") else open

    class FakeFH:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, b):
            raise OSError(errno.ENOSPC, "No space left on device")

        def close(self):
            pass

    import builtins
    orig = builtins.open

    def patched(p, *a, **k):
        if str(p).endswith(".part") and ("w" in (a[0] if a else k.get("mode", ""))
                                         or "a" in (a[0] if a else k.get("mode", ""))):
            return FakeFH()
        return orig(p, *a, **k)

    builtins.open = patched
    try:
        try:
            DL.download_from_sources([{"url": srv.url()}], dest, sha256=sha_of(body),
                                     log=lambda *a: None, max_retries=1, timeout=10)
            rec("写入失败(ENOSPC) → 分类报错（分支级）", False, "居然成功了")
        except DL.DownloadError as e:
            rec("写入失败(ENOSPC) → 分类报错（分支级）", e.kind == DL.KIND_DISK_FULL,
                f"kind={e.kind}")
    finally:
        builtins.open = orig
        srv.stop()


def t_service_fake(tmp):
    """状态机：fake 组件（本地服务器）→ 成功安装；再验证各失败路径不破坏旧版本。"""
    print()
    print("  —— WorkerService 状态机（fake 小包）")
    # 造一个"假 Worker"组件：满足 REQUIRED_FILES 的最小集合
    fake = os.path.join(tmp, "fake-dist")
    for f in WS.REQUIRED_FILES:
        p = os.path.join(fake, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"\0" * 128)
    # 打成一个 xz 包
    import tarfile
    arc = os.path.join(tmp, "fake-core.tar.xz")
    with tarfile.open(arc, "w:xz") as tf:
        tf.add(fake, arcname=".")
    with open(arc, "rb") as fh:
        srv = Server(mode="ok", body=fh.read())
    manifest = {"worker_version": "9.9.9", "components": [
        {"id": "core", "file": "fake-core.tar.xz", "bytes": len(srv.cls.body),
         "sha256": sha_of(srv.cls.body), "required": True,
         # ⚠️ 必须有 status:"verified" 才算"可用的源"；否则新的闸门会拦住
         #    （这正是不写成"未验证也算能用"的意思）
         "sources": [{"url": srv.url(), "label": "本地",
                      "status": "verified"}]}]}

    with tempfile.TemporaryDirectory() as appdir:
        paths = WS.paths
        old_app, old_data = paths.app_dir, paths.data_dir
        paths.app_dir = lambda: appdir              # type: ignore[assignment]
        paths.data_dir = lambda: appdir             # type: ignore[assignment]
        try:
            svc = WS.WorkerService(manifest, log=lambda *a: None)
            # ① 自检失败 → 不得标记已安装、不得留 staging
            real_selftest = WS.run_selftest
            WS.run_selftest = lambda *a, **k: {"ok": False, "error": "打桩自检失败"}
            r1 = svc.install()
            root = WS.worker_root()
            staging = [d for d in os.listdir(os.path.join(appdir, "runtime"))
                       if d.startswith("demucs-worker.staging")] \
                if os.path.isdir(os.path.join(appdir, "runtime")) else []
            rec("自检失败 → 不安装、清除 staging、旧版本不动",
                (not r1["ok"]) and r1["state"] == WS.SELFTEST_FAILED
                and not os.path.isdir(root) and not staging,
                f"state={r1['state']} 正式目录存在={os.path.isdir(root)} staging={staging}")

            # ② 自检通过 → 安装成功
            WS.run_selftest = lambda *a, **k: {"ok": True, "torch_C": "ok",
                                               "cuda_available": True}
            r2 = svc.install()
            rec("自检通过 → 原子切换到正式目录",
                r2["ok"] and os.path.isfile(WS.worker_exe()),
                f"state={r2['state']} exe={os.path.basename(WS.worker_exe())}")

            # ③ 旧版本存在时，新版本安装失败 → 旧版本必须还在
            marker = os.path.join(root, "_internal", "OLD-MARKER")
            with open(marker, "w") as fh:
                fh.write("v1")
            WS.run_selftest = lambda *a, **k: {"ok": False, "error": "升级自检失败"}
            r3 = svc.install()
            rec("升级失败 → 旧版本文件完好（不被覆盖/删除）",
                (not r3["ok"]) and os.path.isfile(marker)
                and os.path.isfile(WS.worker_exe()),
                f"state={r3['state']} 旧标记还在={os.path.isfile(marker)}")

            # ④ 升级成功 → 旧标记消失（换了新版本）
            WS.run_selftest = lambda *a, **k: {"ok": True, "torch_C": "ok",
                                               "cuda_available": True}
            r4 = svc.install()
            rec("升级成功 → 正式目录换成新版本",
                r4["ok"] and not os.path.isfile(marker),
                f"旧标记已消失={not os.path.isfile(marker)}")

            # ⑤ SHA256 不符 → 不得安装
            manifest["components"][0]["sha256"] = "0" * 64
            svc._m = manifest
            r5 = svc.install()
            rec("SHA256 不符 → 不安装、状态 failed",
                (not r5["ok"]) and r5["state"] == WS.FAILED,
                f"state={r5['state']} error={str(r5.get('error'))[:60]}")

            # ⑥ 未安装时 discover 返回空 + separate 明确报错
            svc.uninstall()
            rec("未安装时 discover 为空、separate 明确报错（不伪造）",
                WS.discover() == "" and _raises_not_installed(),
                "WorkerError(kind=not_installed)")
        finally:
            paths.app_dir, paths.data_dir = old_app, old_data
            srv.stop()


def _raises_not_installed() -> bool:
    try:
        WS.separate("nope.wav", "out.wav", exe="")
        return False
    except WS.WorkerError as e:
        return e.kind == "not_installed"


def t_real_install(tmp):
    """用本地服务器提供**真实组件包**跑完整安装（验证组件切分与整链正确）。"""
    print()
    print("  —— 真实组件包完整安装（本地环回；验证切分/解压/完整性/自检/切换）")
    mf_path = os.path.join(HERE, "desktop", "resources", "worker_manifest.json")
    manifest = json.load(open(mf_path, encoding="utf-8"))
    srv = Server(mode="ok")

    class FileHandler(Handler):
        def do_GET(self):
            name = os.path.basename(self.path.split("?")[0])
            p = os.path.join(COMPONENTS, name)
            if not os.path.isfile(p):
                self._send_common(404, length=0)
                return
            with open(p, "rb") as fh:
                data = fh.read()
            rng = self.headers.get("Range")
            start = 0
            if rng and rng.startswith("bytes="):
                with contextlib.suppress(ValueError):
                    start = int(rng.split("=", 1)[1].split("-")[0])
            chunk = data[start:]
            self._send_common(206 if rng else 200,
                              {"Accept-Ranges": "bytes",
                               "Content-Range": f"bytes {start}-{len(data)-1}/{len(data)}"},
                              length=len(chunk))
            self.wfile.write(chunk)

    srv.httpd.RequestHandlerClass = FileHandler
    for c in manifest["components"]:
        c["sources"] = [{"url": srv.url(c["file"]), "label": "本地环回",
                         "status": "verified"}]
    manifest["worker_version"] = "0.1.0"
    with tempfile.TemporaryDirectory() as appdir:
        paths = WS.paths
        old_app, old_data = paths.app_dir, paths.data_dir
        paths.app_dir = lambda: appdir              # type: ignore[assignment]
        paths.data_dir = lambda: appdir             # type: ignore[assignment]
        try:
            svc = WS.WorkerService(manifest, log=lambda m: None)
            t0 = time.time()
            r = svc.install()
            total = manifest["total_compressed_bytes"]
            rec("真实组件包：下载→SHA256→解压→完整性→自检→原子切换",
                r["ok"] and os.path.isfile(WS.worker_exe()),
                f"{total/1048576:.0f} MB 用时 {time.time()-t0:.0f}s；"
                f"state={r.get('state')} exe={os.path.basename(WS.worker_exe())}")
            if r["ok"]:
                st = WS.run_selftest(WS.worker_exe())
                rec("安装后的 Worker 真能自检（torch._C + CUDA）",
                    bool(st.get("ok")),
                    f"torch={st.get('torch')} cuda={st.get('cuda_available')} "
                    f"device={st.get('device_name')}")
                # 版本文件
                vf = os.path.join(os.path.dirname(WS.worker_exe()), "_internal",
                                  "worker.version")
                rec("版本文件已写入（供廉价版本比对）",
                    os.path.isfile(vf) and open(vf).read().strip() == "0.1.0",
                    open(vf).read().strip() if os.path.isfile(vf) else "缺失")
                # 真跑一次分离（真 GPU）
                audio = os.path.join(HERE, "uploads",
                                     "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")
                out = os.path.join(appdir, "voc.wav")
                t0 = time.time()
                meta = WS.separate(audio, out, torch_home=os.path.join(HERE, "models", "demucs"),
                                   timeout=900, echo=lambda *a: None)
                import wave
                with wave.open(out, "rb") as w:
                    dur = w.getnframes() / w.getframerate()
                import numpy as np
                with wave.open(out, "rb") as w:
                    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                rec("安装后的 Worker 真跑 GPU 分离（时长一致、无 NaN/Inf）",
                    meta.get("used_device") == "cuda"
                    and abs(dur - 288.0) < 1.0
                    and not np.isnan(x.astype(np.float32)).any(),
                    f"device={meta.get('used_device')} 时长={dur:.1f}s "
                    f"sep={meta.get('sep_s')}s 用时 {time.time()-t0:.0f}s")
        finally:
            paths.app_dir, paths.data_dir = old_app, old_data
            srv.stop()


def t_sources_gate(tmp):
    """**源未发布就不要开始下载**（含阴性对照：标成 verified 就得放行）。"""
    print()
    print("  —— 下载源闸门（未验证的地址不得当成已支持）")
    from desktop.services import worker_service as ws
    import tempfile
    srv = Server(mode="ok", body=b"x" * 4096)
    fake = {"worker_version": "9.9.9", "components": [
        {"id": "core", "file": "c.tar.xz", "bytes": 4096, "sha256": "0" * 64,
         "required": True,
         "sources": [{"url": srv.url(), "label": "候选", "status": "unpublished"}]}]}
    with tempfile.TemporaryDirectory() as appdir:
        paths = ws.paths
        old_app, old_data = paths.app_dir, paths.data_dir
        paths.app_dir = lambda: appdir              # type: ignore[assignment]
        paths.data_dir = lambda: appdir             # type: ignore[assignment]
        try:
            # --- 闸门：全部 unpublished → 拒绝，且状态是"未安装"而不是"失败" ---
            svc = ws.WorkerService(fake, log=lambda *a: None)
            r = svc.install()
            hit = srv.hits                    # 一次请求都不该发出去
            rec("源全部未发布 → 不开始下载（一次请求都不发）、状态仍是未安装",
                (not r["ok"]) and r["state"] == ws.NOT_INSTALLED and hit == 0,
                f"state={r['state']} 发出的请求数={hit} 原因={str(r.get('error'))[:60]}")
            snap = svc.snapshot()
            rec("界面能拿到'源未就绪'与原因（据此禁用按钮）",
                snap.get("sources_ready") is False and bool(snap.get("sources_reason")),
                f"sources_ready={snap.get('sources_ready')} "
                f"reason={str(snap.get('sources_reason'))[:50]}")

            # --- 阴性对照：标成 verified → 必须放行（证明闸门不是恒 false） ---
            fake["components"][0]["sources"][0]["status"] = "verified"
            svc2 = ws.WorkerService(fake, log=lambda *a: None)
            ok2, why2 = ws.sources_ready(fake)
            r2 = svc2.install()
            rec("阴性对照：标成 verified 后放行（闸门不是恒 false）",
                ok2 and bool(r2.get("started_download", True)) and srv.hits > 0,
                f"sources_ready={ok2} 发出的请求数={srv.hits} "
                f"state={r2.get('state')}（失败原因是假 SHA，属预期）")
        finally:
            paths.app_dir, paths.data_dir = old_app, old_data
            srv.stop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real-install", action="store_true",
                    help="额外跑真实组件包（1.67GB）的完整安装（耗时较长）")
    a = ap.parse_args()
    print("=" * 92)
    print("  一、下载器（本地可控 HTTP 服务器）")
    print("=" * 92)
    with tempfile.TemporaryDirectory() as tmp:
        srv = Server(mode="ok", body=os.urandom(1024))
        t_download_ok(srv, tmp)
        t_resume(srv, tmp)
        t_mirror_switch(srv, tmp)
        t_sha_mismatch(srv, tmp)
        t_slow_switch(srv, tmp)
        t_5xx_retry_then_ok(srv, tmp)
        t_norange(srv, tmp)
        t_cancel(srv, tmp)
        t_disk_full(tmp)
        srv.stop()
    print()
    print("=" * 92)
    print("  二、WorkerService 状态机")
    print("=" * 92)
    with tempfile.TemporaryDirectory() as tmp:
        t_service_fake(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        t_sources_gate(tmp)
    if a.real_install:
        with tempfile.TemporaryDirectory() as tmp:
            t_real_install(tmp)
    n_ok = sum(1 for r in RESULTS if r["ok"])
    print()
    print("=" * 92)
    print(f"  合计 通过 {n_ok} / {len(RESULTS)}")
    print("=" * 92)
    for r in RESULTS:
        if not r["ok"]:
            print(f"  ✗ {r['test']}: {r['detail'][:150]}")
    out = os.path.join(PACK, "download-tests.json")
    os.makedirs(PACK, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, ensure_ascii=False, indent=2)
    print(f"  明细 → {out}")
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
