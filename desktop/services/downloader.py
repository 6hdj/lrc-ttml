"""加固的 HTTP 下载器（多源 / 断点续传 / 退避 / 分类错误 / 限速判据）。

给"可选运行组件"（Demucs Worker）用。**复用** `model_manager` 里已经验证过的能力：
`sha256_file` / `human` / `DownloadCancelled`，以及那条用 A/B 实测换来的经验 ——
**必须用 `read1()`**（`read(n)` 在连接被强制断开时一个字节都不返回，`.part` 会变 0 字节，
断点续传等于没做；见 `model_manager._http_get_to_file` 的注释与 `.tmp/ab_read.py`）。

## 与既有 `_http_get_to_file` 的区别（为什么另写一个）

| 能力 | `_http_get_to_file` | 本模块 |
| --- | --- | --- |
| 断点续传（Range） | ✅ | ✅ |
| 多下载源自动切换 | ❌（单 URL） | ✅ |
| 指数退避 + 随机抖动 | ❌（固定重试） | ✅ |
| 错误分类（429/5xx/超时/重置/过慢） | ❌ | ✅ |
| 限速判据（速度持续过低换源） | ❌ | ✅ |
| 换源时校验文件身份（ETag/大小） | ❌ | ✅ |
| 进度含"当前源/速度/重试状态" | 部分 | ✅ |

## 用户提的硬要求 → 本模块的落点

* "仅当服务器支持 Range 且文件身份和校验信息匹配时才续传" → `_probe()` 取
  `Accept-Ranges`/`Content-Length`/`ETag`/`Last-Modified`；`.part.meta.json` 记下身份，
  换源或缺身份就**从 0 重下**（宁可多下，也不能拼出畸形文件）。
* "保留下载缓存，不因单次失败删除所有已下载数据" → `.part` 只在"身份不符/不支持续传"时删。
* "不得无限重试" → 每源重试 `max_retries`，源数有限，总次数上限可算。
* "分类处理" → `DownloadError.kind` 取值见下。
"""
from __future__ import annotations

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request

try:                                            # 复用既有实现，避免重复造轮子
    from desktop.services.model_manager import (DownloadCancelled, human,
                                                sha256_file)
except Exception:                               # noqa: BLE001  允许独立运行/测试
    DownloadCancelled = type("DownloadCancelled", (Exception,), {})

    def human(n):                               # type: ignore[misc]
        n = float(n or 0)
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024 or u == "GB":
                return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
            n /= 1024

    def sha256_file(p, chunk=1 << 20, progress=None):   # type: ignore[misc]
        import hashlib
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            while True:
                b = fh.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()


UA = "Lyric2TTML/desktop (worker-component)"

# 错误分类（用户要求"对 429/5xx/超时/重置/速度过低分类处理"）
KIND_HTTP_429 = "http_429"
KIND_HTTP_5XX = "http_5xx"
KIND_HTTP_4XX = "http_4xx"
KIND_TIMEOUT = "timeout"
KIND_RESET = "reset"
KIND_SLOW = "slow"
KIND_NET = "network"
KIND_DISK_FULL = "disk_full"
KIND_SHA = "sha256_mismatch"
KIND_SIZE = "size_mismatch"

# 每类错误是否值得"换源"（而不是在本源重试）
SWITCH_ON = {KIND_HTTP_429, KIND_HTTP_5XX, KIND_HTTP_4XX, KIND_SLOW}


class DownloadError(Exception):
    """带分类的下载错误。`kind` 见模块常量；`detail` 给人看。"""

    def __init__(self, kind: str, detail: str, source: str = ""):
        super().__init__(f"[{kind}] {detail}" + (f"（源 {source}）" if source else ""))
        self.kind = kind
        self.detail = detail
        self.source = source


def _host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc
    except Exception:                            # noqa: BLE001
        return url


def _classify(exc: Exception) -> str:
    """把异常映射成分类。"""
    if isinstance(exc, urllib.error.HTTPError):
        c = int(getattr(exc, "code", 0) or 0)
        if c == 429:
            return KIND_HTTP_429
        if 500 <= c <= 599:
            return KIND_HTTP_5XX
        return KIND_HTTP_4XX
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return KIND_TIMEOUT
    if isinstance(exc, urllib.error.URLError):
        r = getattr(exc, "reason", None)
        if isinstance(r, (socket.timeout, TimeoutError)):
            return KIND_TIMEOUT
        if isinstance(r, ConnectionResetError):
            return KIND_RESET
        return KIND_NET
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        return KIND_RESET
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return KIND_DISK_FULL
    return KIND_NET


def _probe(url: str, timeout: float) -> dict:
    """探测一个源：是否可达、是否支持 Range、文件身份。

    用 `GET Range: bytes=0-0` 而不是 HEAD：很多 CDN 的 HEAD 不返回 Content-Length，
    而 Range 探测能同时拿到 206/200、Content-Range、ETag、Last-Modified。
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Range": "bytes=0-0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        code = getattr(r, "status", 200)
        h = r.headers
        cr = h.get("Content-Range") or ""
        total = 0
        if "/" in cr:
            try:
                total = int(cr.rsplit("/", 1)[1])
            except ValueError:
                total = 0
        if not total:
            total = int(h.get("Content-Length") or 0)
        return {
            "url": url, "host": _host(url), "status": code,
            "accept_ranges": (h.get("Accept-Ranges", "").lower() == "bytes"
                              or code == 206),
            "total": total,
            "etag": (h.get("ETag") or "").strip('"'),
            "last_modified": h.get("Last-Modified") or "",
        }


def _read_meta(p: str) -> dict:
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                            # noqa: BLE001
        return {}


def _write_meta(p: str, d: dict) -> None:
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _backoff(attempt: int, base: float = 1.5, cap: float = 30.0) -> float:
    """指数退避 + 随机抖动（抖动避免多客户端同时重试打垮镜像）。"""
    return min(cap, base * (2 ** max(0, attempt - 1))) * (0.6 + 0.8 * random.random())


def download_from_sources(sources: list, dest: str, *, sha256: str = "",
                          expected_size: int = 0, log=print,
                          progress=None, stop=None, timeout: float = 30.0,
                          max_retries: int = 3, low_speed_bps: int = 15_000,
                          low_speed_secs: float = 25.0,
                          verify_hash: bool = True) -> dict:
    """从一组源下载到 `dest`（**不改名、不安装**，只保证文件到位且校验通过）。

    `sources`：`[{"url":..., "label":...}, ...]`，按优先级排列（第一个是主源）。
    返回 `{"ok":True,"path":dest,"bytes":n,"source":url,"sha256":...}`；
    失败抛 `DownloadError`（`.part` 与 `.meta` **保留**，便于重试/续传）。
    """
    progress = progress or (lambda *a, **k: None)
    stop = stop or (lambda: False)

    def _stopped() -> None:
        """统一的"该不该停"判据。

        ⚠️⚠️ **这里踩过一个真 bug**（被"取消下载"用例抓到）：
        `model_manager._http_get_to_file` 的约定是"`stop()` 自己抛
        `DownloadCancelled`"，于是我只写了 `stop()` 而**没看返回值**；
        可实际调用方（`worker_service`、测试）传的是
        `lambda: bool` —— 结果是**取消永远不生效**，用户点"取消"后
        下载会一直跑下去。现在两种约定都支持：
        `stop()` 自己抛 → 直接冒泡；返回真值 → 这里抛。
        """
        if stop():
            raise DownloadCancelled()
    part = dest + ".part"
    meta_p = part + ".meta.json"

    if os.path.isfile(dest):
        os.remove(dest)
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)

    have = os.path.getsize(part) if os.path.isfile(part) else 0
    meta = _read_meta(meta_p)
    last_err: Exception = DownloadError(KIND_NET, "没有可用下载源")

    for si, src in enumerate(sources):
        url = src["url"] if isinstance(src, dict) else str(src)
        label = (src.get("label") if isinstance(src, dict) else "") or _host(url)
        # ---- 探测 ----
        try:
            info = _probe(url, timeout)
        except Exception as e:                                   # noqa: BLE001
            k = _classify(e)
            log(f"[探测失败·{k}] {label}：{e}")
            last_err = DownloadError(k, str(e), label)
            continue
        if expected_size and info["total"] and info["total"] != expected_size:
            log(f"[跳过] {label}：文件大小不符（{info['total']} != {expected_size}）")
            last_err = DownloadError(KIND_SIZE, f"大小不符 {info['total']}",
                                     label)
            continue

        # ---- 续传判定：必须"同源同身份" ----
        resume = 0
        if have and have < (info["total"] or 1 << 62):
            same_src = meta.get("url") == url
            same_id = (meta.get("etag") == info["etag"]
                       and meta.get("total") == info["total"])
            if info["accept_ranges"] and same_src and same_id:
                resume = have
                log(f"[续传] {label}：从 {human(have)} 继续")
            else:
                why = ("该源不支持 Range" if not info["accept_ranges"]
                       else "换源后身份不一致" if not same_src
                       else "文件身份(ETag/大小)与已下部分不符")
                log(f"[重下] {label}：{why}，已下的 {human(have)} 作废")
                try:
                    os.remove(part)
                except OSError:
                    pass
                have = resume = 0

        # ---- 逐次尝试 ----
        for attempt in range(1, max_retries + 1):
            _stopped()
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Accept": "*/*"})
                if resume:
                    req.add_header("Range", f"bytes={resume}-")
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    code = getattr(r, "status", 200)
                    if resume and code != 206:
                        log(f"[重下] {label}：服务器忽略 Range（HTTP {code}）")
                        try:
                            os.remove(part)
                        except OSError:
                            pass
                        resume = 0
                    clen = int(r.headers.get("Content-Length") or 0)
                    total = (resume + clen) if clen else (info["total"] or 0)
                    _write_meta(meta_p, {"url": url, "etag": info["etag"],
                                         "total": total,
                                         "last_modified": info["last_modified"]})
                    read = getattr(r, "read1", None) or r.read   # ⚠️ read1！
                    got = resume
                    t0 = time.time()
                    win_t, win_b = t0, got
                    mode = "ab" if resume else "wb"
                    with open(part, mode) as fh:
                        while True:
                            _stopped()
                            b = read(1 << 18)
                            if not b:
                                break
                            try:
                                fh.write(b)
                            except OSError as e:
                                if getattr(e, "errno", None) == 28:
                                    raise DownloadError(KIND_DISK_FULL,
                                                        "磁盘空间不足") from e
                                raise
                            got += len(b)
                            now = time.time()
                            dt = max(1e-3, now - t0)
                            speed = (got - resume) / dt
                            progress(got, total, {
                                "source": label, "url": url,
                                "speed_bps": speed,
                                "attempt": attempt, "resumed": bool(resume),
                            })
                            if now - win_t >= low_speed_secs:
                                avg = (got - win_b) / (now - win_t)
                                if avg < low_speed_bps and (not total or got < total):
                                    raise DownloadError(
                                        KIND_SLOW,
                                        f"速度持续过低（{human(avg)}/s 持续 "
                                        f"{low_speed_secs:.0f}s）", label)
                                win_t, win_b = now, got
                    if total and got < total:
                        raise DownloadError(
                            KIND_NET, f"连接提前结束（{got}/{total}）", label)
                    have, resume = got, got
                    # ---- 大小 + SHA256 ----
                    if expected_size and got != expected_size:
                        raise DownloadError(KIND_SIZE,
                                            f"大小不符（{got} != {expected_size}）",
                                            label)
                    if verify_hash and sha256:
                        progress(got, total, {"source": label, "verifying": True})
                        log(f"[校验] 计算 SHA256（{human(got)}）…")
                        d = sha256_file(part)
                        if d.lower() != sha256.lower():
                            raise DownloadError(
                                KIND_SHA,
                                f"SHA256 不匹配（得到 {d[:16]}…，期望 {sha256[:16]}…）",
                                label)
                        log("[校验] SHA256 一致 ✓")
                    os.replace(part, dest)
                    try:
                        os.remove(meta_p)
                    except OSError:
                        pass
                    return {"ok": True, "path": dest, "bytes": got,
                            "source": url, "label": label, "sha256": sha256}
            except DownloadCancelled:
                raise
            except DownloadError as e:
                last_err = e
                if e.kind == KIND_DISK_FULL:
                    raise
                if e.kind in SWITCH_ON:
                    log(f"[{e.kind}] {label}：{e.detail} → 换下一个源")
                    break                      # 立即换源
                log(f"[{e.kind}] {label} 第 {attempt}/{max_retries} 次：{e.detail}")
            except Exception as e:                                   # noqa: BLE001
                k = _classify(e)
                last_err = DownloadError(k, str(e), label)
                if k == KIND_DISK_FULL:
                    raise last_err
                log(f"[{k}] {label} 第 {attempt}/{max_retries} 次：{e}")
            if attempt < max_retries:
                d = _backoff(attempt)
                log(f"[退避] {d:.1f}s 后重试（{label}）")
                time.sleep(d)
        # 本源用尽，尝试下一个源（保留 .part，下次可续）
        if si < len(sources) - 1:
            log(f"[换源] {label} 不可用，改用下一个源")

    raise DownloadError(getattr(last_err, "kind", KIND_NET),
                        f"所有下载源都失败：{getattr(last_err, 'detail', last_err)}",
                        getattr(last_err, "source", ""))
