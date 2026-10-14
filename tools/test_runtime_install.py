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
    """造一个"像 CUDA 包"的 zip：条目够多、**每个够大**，确保解压能持续一会儿。

    ⚠️⚠️ 为什么必须"够大"（反复踩出来的）：取消用例要让取消**落在解压循环内部**。
    第一版 41 个条目 → 解压瞬间完成，取消落到循环外；
    第二版 400 个条目但每个 32 KB → 解压仍是毫秒级，跟取消时机赛跑，
    于是**时灵时不灵**（变异检查三轮里中一轮）。
    现在每个条目 256 KB × 400 = 100 MB，解压要**几秒**，
    取消必然落在循环里 —— 测试不再看运气。
    代价：造包 + 下载 + 解压约十几秒，换来的是判定可靠。
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr("bin/cublas64_12.dll", b"A" * 1024)
        filler = os.urandom(256 * 1024)
        for i in range(400):
            z.writestr(f"bin/filler{i:03d}.bin", filler)
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
    # ⚠️⚠️ **必须连着下载缓存一起清掉**（真踩到，而且是"时灵时不灵"的根源）：
    # `<root>/.wheels/cuda.zip` 若已存在，`download()` 会**跳过下载**直接解压 ——
    # 于是下面用来判断"下载已结束"的那个进度标记（`已下载 …100%`）**永远不会出现**，
    # 取消predicate 永远返回 False，测试就变成"取消被无视但下载装完了"，exit=0。
    # 批量跑时前面的用例已经把 zip 下好，所以时灵时不灵；单独跑（干净沙箱）就通过。
    shutil.rmtree(os.path.join(root, ".wheels"), ignore_errors=True)
    mgr2 = rs.RuntimeManager(manifest=manifest)
    mgr2._root_override = root
    # ⚠️⚠️ **取消时机要结构性确定，不能跟解压/下载速度赛跑**（反复踩三次）。
    # 走过的错路：
    #   ① 数 `stop()` 调用次数 → 落点取决于下载耗时，不稳；
    #   ② 等"看到解压进度"→ 第一个进度在**循环最后一条**才报，太晚；
    #   ③ 等"已下载 100%" → 若缓存命中会跳下载，这个标记**永不出现**，
    #      predicate 永远 False（而且批量跑时缓存已被前面的用例填上）。
    # 现在：**一进入解压阶段（第一次收到 `解压 i/n`）就开始数 `stop()`**，
    # 数到第 20 次就取消。解压 401 个条目会调 `stop()` 约 200 次，
    # 第 20 次**必然**在循环内部 —— 与下载速度、缓存命中、机器负载都无关。
    state = {"unpack_stops": 0, "in_unpack": False}

    def cancel_on_unpack() -> bool:
        if state["in_unpack"]:
            state["unpack_stops"] += 1
        return state["unpack_stops"] >= 20

    def prog2(pct, msg):
        # 解压阶段才会报 `解压 i/n` —— 这就是"已经进解压"的确凿信号
        if msg and "解压" in str(msg):
            state["in_unpack"] = True

    # ⚠️⚠️ **不要把"进入解压阶段"押在进度回调上**。
    # 这是我在这里栽的第四次：进度回调 `p(i*100/total, …)` 在一个大包里
    # 可能**一次都不报**（或报得很晚），于是"开始计数"的标志永远不亮，
    # 取消 predicate 恒为 False —— 用例变成"取消了但装完"，测试静默通过。
    # 改成**直接包住 `_unpack`**：它一被调用就说明下载已经结束、解压即将开始，
    # 这个事实与进度回调、缓存命中、机器快慢全都无关。
    _orig_unpack = rs.RuntimeManager._unpack

    def _hooked_unpack(self, spec, files, tmp_site, log=print, p=None, stop=None):
        state["in_unpack"] = True
        return _orig_unpack(self, spec, files, tmp_site, log=log, p=p, stop=stop)

    rs.RuntimeManager._unpack = _hooked_unpack

    site2 = mgr2.site_dir("cuda")
    cancelled = False
    try:
        mgr2.download("cuda", log=lambda m: None, progress=prog2,
                      cancel=cancel_on_unpack)
    except rs.DownloadCancelled:
        cancelled = True
    except Exception as e:                              # noqa: BLE001
        print(f"       （抛出 {type(e).__name__}: {str(e)[:80]}）")
    finally:
        rs.RuntimeManager._unpack = _orig_unpack
    check(state["unpack_stops"] >= 20,
          "取消发生在**解压阶段**（用例有效）",
          f"解压阶段只数到 {state['unpack_stops']} 次 stop")
    check(cancelled, "取消时抛 DownloadCancelled（不是静默装完）")

    # ---- 2b. **结构性**断言：解压循环里必须逐条检查取消 ----
    # ⚠️⚠️ 为什么除了行为用例还要有这一条（老实说明）：
    # 上面那个取消用例依赖"取消信号能在解压循环跑完之前被送达"。
    # 我为此改了四轮（数 stop 次数 → 等解压进度 → 等下载标记 → 挂钩 `_unpack`），
    # 在**满负载**（整套变异检查连跑）下仍然会偶发地让解压先跑完 ——
    # 那时用例静默通过，而"去掉循环里的取消检查"这个变异就抓不到。
    # 也就是说：**行为用例本身有竞态，不能单独作为保证**。
    # 这条断言不看竞态，直接检查源码结构：
    # 解压循环体内、`z.extract(...)` **之前**必须有一次"检查取消"的调用。
    # 它比行为用例弱（管不住"调用了但不生效"），但**确定性**地守住
    # "循环里必须检查"这件事；两条一起用，既确定又有行为覆盖。
    import inspect
    src_lines = inspect.getsource(rs.RuntimeManager._unpack).split("\n")
    # 找 `for i, m in enumerate(...)`，在**同一个缩进块内**按顺序找
    # "检查取消" 与 `z.extract(`
    loop_ok = False
    indent = ""
    seen_stop = False
    for ln in src_lines:
        st = ln.strip()
        if st.startswith("for i, m in enumerate("):
            indent = ln[:len(ln) - len(ln.lstrip())]
            seen_stop = False
            continue
        if not indent:
            continue
        if ln.strip() and not ln.startswith(indent + " "):
            continue                                    # 循环体结束
        if st.startswith("stop()") or st.startswith("_stop2()"):
            seen_stop = True
        elif seen_stop and st.startswith("z.extract("):
            loop_ok = True
            break
    check(loop_ok,
          "解压循环内**每一条**之前都检查取消（结构性断言，不看竞态）",
          "循环里没找到『先检查取消、再解压』的顺序")
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
