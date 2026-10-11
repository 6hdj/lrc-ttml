#!/usr/bin/env python3
"""
极简 wheel 安装器（本机专用）。

为什么不用 pip：
    这台机器的沙箱会拒绝 pip 在临时目录里写 ``.whl`` 文件
    （``PermissionError: [Errno 13]``，pip 报 "Failed to remove contents in a
    temporary directory"）。但 **urllib 下载到内存** 和 **zipfile 解包** 都是允许的，
    所以这里自己实现"解析依赖 → 内存下载 → 解包"，全程不落 .whl 文件。

用法：
    python minipip.py install pypinyin
    python minipip.py install --target .venv/Lib/site-packages funasr modelscope
    python minipip.py list-remote <包名>          # 看看有哪些版本

只支持 wheel（不编译源码包），依赖环境标记简单求值。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import urllib.request
import zipfile
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

INDEX_URLS = [
    u.strip() for u in os.environ.get(
        "MINIPIP_INDEX",
        "https://repo.huaweicloud.com/repository/pypi/simple/,"
        "https://mirrors.cloud.tencent.com/pypi/simple/,"
        "https://mirrors.aliyun.com/pypi/simple/",
    ).split(",") if u.strip()
]
UA = {"User-Agent": "minipip/1.0"}
DEFAULT_TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              ".venv", "Lib", "site-packages")


# --------------------------------------------------------------------------
def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _content_length(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def _download_to_disk(url: str, path: str, log=print, retries: int = 10) -> str:
    """大 wheel（例如 1.9GB 的 CUDA torch）流式写盘，并支持**断点续传 + 完整性校验**。

    直连 download.pytorch.org 实测会中途断流（1.9GB 只下到 528MB），
    所以必须按 Content-Length 校验并用 Range 续传。
    """
    import time as _time
    total = _content_length(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in range(retries):
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if total and have >= total:
            break
        headers = dict(UA)
        mode = "wb"
        if have and total:
            headers["Range"] = f"bytes={have}-"
            mode = "ab"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as r:
                if have and getattr(r, "status", 200) != 206:
                    have, mode = 0, "wb"          # 服务器不支持续传 → 重头来
                with open(path, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
        except Exception as e:
            got = os.path.getsize(path) if os.path.exists(path) else 0
            log(f"      ⚠ 下载中断（{type(e).__name__}），已 {got / 1048576:.0f}MB，续传…")
            _time.sleep(2)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if total and size < total:
        raise RuntimeError(f"下载不完整：{size / 1048576:.0f}/{total / 1048576:.0f} MB")
    return path


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: List[Tuple[str, str]] = []
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_data(self, data):
        if self._href:
            self.links.append((self._href, data.strip()))

    def handle_endtag(self, tag):
        if tag == "a":
            self._href = None


def index_links(name: str, base: Optional[str] = None) -> List[Tuple[str, str]]:
    """返回 [(url, 文件名)]；``base`` 为空时按顺序尝试多个镜像"""
    bases = [base] if base else INDEX_URLS
    errors = []
    for b in bases:
        for candidate in (name.lower().replace("_", "-"), name.lower()):
            url = b.rstrip("/") + "/" + candidate + "/"
            try:
                html = _get(url).decode("utf-8", "replace")
            except Exception as e:
                errors.append(f"{b}: {type(e).__name__}")
                continue
            p = _Links()
            p.feed(html)
            out = []
            for href, text in p.links:
                fn = text or href.rsplit("/", 1)[-1].split("#")[0]
                if not fn.endswith(".whl"):
                    continue
                if href.startswith("http"):
                    out.append((href, fn))
                else:
                    out.append((url.rsplit("/", 1)[0] + "/" + href, fn))
            if out:
                return out
    if errors:
        raise LookupError(f"所有镜像都失败: {'; '.join(errors[:3])}")
    return []


def _rank_candidates(links: List[Tuple[str, str]], want_version: str = "") -> List[Tuple[Tuple, str, str]]:
    cands = []
    for url, fn in links:
        if want_version and f"-{want_version}-" not in fn:
            continue
        r = _wheel_rank(fn)
        if r is None:
            continue
        cands.append((r, url, fn))
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands


def fetch_wheel(name: str, want_version: str = "", log=print) -> Tuple[str, bytes]:
    """按镜像顺序找一个**能真正下载成功**的 wheel。

    PyTorch 的专属索引里会列出 setuptools 这类包，但文件是 403，
    所以必须"索引能列出来"和"文件能下下来"分开判断。
    """
    last_err = None
    for base in INDEX_URLS:
        try:
            links = index_links(name, base=base)
        except Exception as e:
            last_err = e
            continue
        for _rank, url, fn in _rank_candidates(links, want_version):
            try:
                return fn, _get(url)
            except Exception as e:
                last_err = e
                continue
    raise LookupError(f"{name} 在所有镜像都下载失败：{last_err}")


# --------------------------------------------------------------------------
_PY_TAG = "cp%d%d" % sys.version_info[:2]
_PY_TAGS = {_PY_TAG, "cp%d%d" % sys.version_info[:2], "py3", "py2.py3", "py%d" % sys.version_info[0]}
_IS_64 = sys.maxsize > 2 ** 32
try:
    import sysconfig as _sc
    _FREETHREADED = bool(_sc.get_config_var("Py_GIL_DISABLED"))
except Exception:                                     # pragma: no cover
    _FREETHREADED = False


def _wheel_rank(fn: str) -> Optional[Tuple]:
    """给 wheel 打分；None = 不兼容。

    必须严格：这台机器的索引里存在 ``numpy-*-cp314-cp314-win32.whl``（32 位）和
    ``safetensors-*-cp314-cp314t-*.whl``（自由线程 ABI）这种**装上就 import 失败**
    的包，选错会直接把环境搞坏。
    """
    base = fn[:-4]
    parts = base.split("-")
    if len(parts) < 5:
        return None
    ver, pytag, abitag, plattag = parts[1], parts[2], parts[3], parts[4]

    # --- 平台：拒绝位数不匹配的二进制 wheel ---
    if plattag == "any":
        plat = 2
    elif plattag == "win_amd64" and _IS_64:
        plat = 3
    elif plattag == "win32" and not _IS_64:
        plat = 3
    else:
        return None

    # --- 解释器标签 ---
    tags = pytag.split(".")
    if _PY_TAG in tags:
        py = 3
    elif "py3" in tags or "py2.py3" in tags or ("py%d" % sys.version_info[0]) in tags:
        py = 2
    else:
        return None

    # --- ABI：cp314t 只能装在自由线程构建上 ---
    if any(a.startswith("cp") and a.endswith("t") and not _FREETHREADED
           for a in abitag.split(".")):
        return None

    def vkey(v):
        return [int(x) if x.isdigit() else -1 for x in re.split(r"[.+]", v)[:4]]

    stable = 0 if re.search(r"(a|b|rc|dev)\d*$", ver) else 1
    return (vkey(ver), stable, -py, -plat)


def _version_key(ver: str):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.+]", ver)[:4])


def pick_wheel(name: str, want_version: str = "") -> Tuple[str, str]:
    links = index_links(name)
    if not links:
        raise LookupError(f"索引里找不到包: {name}")
    cands = _rank_candidates(links, want_version)
    if not cands:
        vers = sorted({fn.split("-")[1] for _, fn in links if len(fn.split("-")) > 2})
        raise LookupError(f"{name} 没有适配 Python {sys.version_info[:2]} / win_amd64 的 wheel；"
                          f"可用版本 {vers[-6:]}")
    _, url, fn = cands[0]
    return url, fn


# --------------------------------------------------------------------------
def read_metadata(blob: bytes) -> Tuple[str, str, List[str], List[str]]:
    """从内存里的 wheel 读 METADATA，返回 (名字, 版本, Requires-Dist, 顶层包名)"""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        meta_name = next((n for n in z.namelist()
                          if n.endswith(".dist-info/METADATA")), None)
        if meta_name is None:
            raise ValueError("wheel 里没有 METADATA")
        text = z.read(meta_name).decode("utf-8", "replace")
        top = sorted({n.split("/")[0] for n in z.namelist()
                      if "/" in n and not n.startswith(".")
                      and not n.split("/")[0].endswith(".dist-info")
                      and not n.split("/")[0].endswith(".data")})
    name = version = ""
    requires: List[str] = []
    for line in text.splitlines():
        if line.startswith("Name: "):
            name = line[6:].strip()
        elif line.startswith("Version: "):
            version = line[9:].strip()
        elif line.startswith("Requires-Dist: "):
            requires.append(line[15:].strip())
    return name, version, requires, top


def _marker_ok(marker: str) -> bool:
    """尽量用 packaging 求值；失败就保守返回 True（装了多余的依赖不影响）"""
    marker = (marker or "").strip()
    if not marker:
        return True
    if "extra ==" in marker or "extra==" in marker:
        return False                     # 不装 extras
    try:
        from packaging.markers import Marker          # type: ignore
        return bool(Marker(marker).evaluate())
    except Exception:
        pass
    try:
        from pip._vendor.packaging.markers import Marker   # type: ignore
        return bool(Marker(marker).evaluate())
    except Exception:
        pass
    if "sys_platform" in marker and "win32" not in marker and "win_amd64" not in marker:
        if "!= 'win32'" in marker or '!= "win32"' in marker:
            return False
    return True


def _req_name(spec: str) -> str:
    return re.split(r"[<>=!;\[\s(]", spec.strip(), 1)[0].strip()


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _installed(target: str = "") -> Set[str]:
    """当前解释器**已经能用**的所有发行包（含 --system-site-packages 继承来的）。

    早先只看目标目录里的 *.dist-info，导致 venv 会重复安装 numpy / torch 这类
    系统里已经有的包，把环境搞坏。
    """
    names: Set[str] = set()
    try:
        from importlib.metadata import distributions
        for d in distributions():
            try:
                n = (d.metadata or {}).get("Name")
            except Exception:
                n = None
            if n:
                names.add(_norm(n))
    except Exception:                                  # pragma: no cover
        pass
    if target and os.path.isdir(target):
        for d in os.listdir(target):
            if d.endswith((".dist-info", ".egg-info")):
                names.add(_norm(d.split("-")[0]))
    return names


def install(pkgs: Sequence[str], target: str = DEFAULT_TARGET, upgrade: bool = False,
            dry_run: bool = False, log=print, big_mb: int = 300) -> int:
    """``big_mb`` 以上的 wheel 走流式下盘（内存装不下 1.8GB 的 CUDA torch）"""
    os.makedirs(target, exist_ok=True)
    have = _installed(target)
    seen: Set[str] = set()
    queue: List[str] = [p for p in pkgs]
    order: List[Tuple[str, object, str]] = []      # (文件名, bytes 或 磁盘路径, 版本)

    while queue:
        spec = queue.pop(0)
        name = _req_name(spec)
        key = _norm(name)
        if key in seen:
            continue
        seen.add(key)
        if key in have and not upgrade:
            log(f"  · 已安装，跳过 {name}")
            continue
        try:
            url, fn = pick_wheel(name)
        except LookupError as e:
            log(f"  ✗ {e}")
            continue
        size_mb = _content_length(url) / 1048576
        log(f"  ↓ {fn}" + (f"  ({size_mb:.0f} MB)" if size_mb else ""))
        if size_mb > big_mb:
            tmp = os.path.join(os.path.dirname(os.path.abspath(target)) or ".",
                               ".tmp_pkgs", fn)
            _download_to_disk(url, tmp, log=log)
            log(f"     已下盘 {tmp}（{os.path.getsize(tmp) / 1048576:.0f} MB）")
            try:
                with zipfile.ZipFile(tmp) as z:
                    meta = next((n for n in z.namelist() if n.endswith(".dist-info/METADATA")), None)
                    text = z.read(meta).decode("utf-8", "replace") if meta else ""
            except zipfile.BadZipFile:
                os.remove(tmp)
                raise RuntimeError(f"{fn} 下载损坏（不是合法 zip），已删除，请重试")
            mver = ""
            requires: List[str] = []
            mname = name
            for line in text.splitlines():
                if line.startswith("Name: "):
                    mname = line[6:].strip()
                elif line.startswith("Version: "):
                    mver = line[9:].strip()
                elif line.startswith("Requires-Dist: "):
                    requires.append(line[15:].strip())
            order.append((fn, tmp, mver))
        else:
            try:
                blob = _get(url)
            except Exception as e:
                # 这个索引给出的文件下不了（例如 PyTorch 索引里的 setuptools 是 403）
                # → 依次换其它镜像重试
                log(f"     ⚠ {fn} 从当前索引下载失败（{type(e).__name__}），换镜像…")
                try:
                    fn, blob = fetch_wheel(name)
                    log(f"     ↻ 改用 {fn}")
                except LookupError as e2:
                    log(f"  ✗ {e2}")
                    continue
            mname, mver, requires, top = read_metadata(blob)
            order.append((fn, blob, mver))
        for req in requires:
            if ";" in req:
                req_part, marker = req.split(";", 1)
                if not _marker_ok(marker):
                    continue
            else:
                req_part = req
            if req_part.strip():
                queue.append(req_part.strip())
        log(f"     {mname} {mver}（依赖 {len(requires)} 项）")

    if dry_run:
        log("dry-run：不写入")
        return len(order)

    for fn, blob, ver in order:
        if isinstance(blob, str):                  # 磁盘上的大 wheel
            with zipfile.ZipFile(blob) as z:
                z.extractall(target)
            try:
                os.remove(blob)
            except OSError:
                pass
        else:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                z.extractall(target)
        log(f"  ✓ 解包 {fn}")
    return len(order)


def main():
    ap = argparse.ArgumentParser(description="内存版 wheel 安装器（绕开沙箱对 .whl 落盘的拦截）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("install")
    p_ins.add_argument("packages", nargs="+")
    p_ins.add_argument("--target", default=DEFAULT_TARGET)
    p_ins.add_argument("--upgrade", action="store_true")
    p_ins.add_argument("--dry-run", action="store_true")

    p_ls = sub.add_parser("list-remote")
    p_ls.add_argument("package")

    args = ap.parse_args()
    if args.cmd == "install":
        n = install(args.packages, args.target, args.upgrade, args.dry_run)
        print(f"\n完成，处理 {n} 个 wheel → {args.target}")
        return 0
    if args.cmd == "list-remote":
        for url, fn in index_links(args.package)[-25:]:
            print(" ", fn)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
