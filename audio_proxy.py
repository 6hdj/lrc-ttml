"""编辑台试听用的**小体积代理音频**。

为什么需要
----------
2026-10-16 用户实测：**"音乐加载非常慢，每次都要加载好半天；编辑完一个字之后又要重新加载"**。

根因实测（不是猜）：
* 用户的音频是 **无损 FLAC**，实测样本 **22.7 MB / 246 秒**（另一首 60.5 MB）；
* 编辑台把**原始文件**直接喂给播放器（`/api/audio` → MediaPlayer）；
* 播放器为了知道时长和建立 seek 索引，必须解析整个流 ——
  无损格式尤其贵；再叠加"每次编辑都会重新打开一次音频"（那条已单独修掉），
  体验就是"卡半天"。

做法
----
生成一个**小体积、可 seek 的代理文件**（单声道 MP3），只用于试听：

    ffmpeg -i 源 -vn -ac 1 -ar 44100 -b:a 64k -f mp3 代理

实测：22.7 MB FLAC → **1.88 MB / 转码 1.5 秒**（32 kbps 版 0.94 MB / 0.9 秒）。
体积掉一个数量级，打开与 seek 都快得多。

⚠️ **代理只用于试听**，绝不参与打轴：ASR、声纹、拖音分析一律仍用原始音频
（无损质量对识别更有利）。这是刻意的边界。

⚠️ **缓存必须可删**：用户明确要求"缓存不能一直留着"。
所以这里有 `purge_all()` / `evict_lru()` / `stats()`，
`/api/audio_proxy/stats|purge` 两个端点把它暴露给界面，
并且**每次新建代理前先按 LRU 裁剪**，不会无限膨胀。

⚠️ 源文件坏了（FLAC 有坏帧）不能让预览完全不可用：
ffmpeg 对坏帧会尽量跳过继续解码，所以只要产出文件够大就采信，
只是把 stderr 记进 `note` 供排查。真正失败（没产出/太小）才报错。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Dict, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))

#: 默认缓存上限。一首 4 分钟歌的代理约 1.9 MB ⇒ 512 MB 能装 ~250 首。
DEFAULT_MAX_BYTES = 512 * 1024 * 1024

#: 备选码率（从高到低）。第一条失败就换下一条 —— 极端环境下某些编码器不可用。
_BITRATES = ("64k", "48k", "32k")

#: 产出文件小于这个大小就认为转码失败（正常的歌不会小于 8 KB）。
_MIN_OUTPUT_BYTES = 8 * 1024

#: 单次转码超时（秒）。给足余量：60 MB 的 FLAC 在慢机器上也就几秒。
_TIMEOUT_SEC = 180

#: 每个源文件一把锁：并发请求（播放器会发多个 Range 请求）不能同时转码同一个文件，
#: 否则会重复跑 ffmpeg、互相覆盖同一个 tmp。
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


# ---------------------------------------------------------------- 目录

def _writable(d: str) -> bool:
    """这个目录能不能真的写？（不存在就试着建）"""
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return False
    probe = os.path.join(d, ".w_probe")
    try:
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return True
    except OSError:
        return False


def cache_dir() -> str:
    """代理缓存目录。

    顺序：``L2T_AUDIO_CACHE``（显式/测试）→ ``<data_dir>/audio_cache``
    → 项目内 ``.cache/audio``。

    ⚠️ **必须逐个验证可写**：数据目录默认在 `%LOCALAPPDATA%`，
    在受限环境（沙箱、只读部署）里可能直接 `PermissionError`。
    代理缓存**只是加速用的**，绝不能因为写不进去就让编辑台播不出声 ——
    所以这里挑"第一个真能写的"目录，全都不行就退回项目内目录（大概率可写）。
    """
    explicit = os.environ.get("L2T_AUDIO_CACHE")
    cands = []
    if explicit:
        cands.append(explicit)
    else:
        try:
            from desktop import paths as _p
            cands.append(os.path.join(_p.data_dir(), "audio_cache"))
        except Exception:  # noqa: BLE001
            pass
        cands.append(os.path.join(_HERE, ".cache", "audio"))
    for d in cands:
        if _writable(d):
            return d
    return cands[-1]


# ---------------------------------------------------------------- ffmpeg

def find_ffmpeg() -> Optional[str]:
    """找 ffmpeg 可执行文件。

    顺序：环境变量 → 打包目录 → PATH。
    ⚠️ 冻结版把 ffmpeg 放在 `_internal/` 里（PyInstaller datas），
    所以不仅要看 `PATH`，还要看**程序目录**与 `_MEIPASS`。
    """
    env = os.environ.get("L2T_FFMPEG")
    if env and os.path.isfile(env):
        return env
    cands = []
    meipass = getattr(__import__("sys"), "_MEIPASS", "")
    if meipass:
        cands.append(os.path.join(meipass, "ffmpeg.exe"))
    cands.append(os.path.join(_HERE, "ffmpeg.exe"))
    try:
        from desktop import paths as _p
        cands.append(os.path.join(_p.app_dir(), "ffmpeg.exe"))
        for root in _p.resource_roots():
            cands.append(os.path.join(root, "ffmpeg.exe"))
    except Exception:  # noqa: BLE001
        pass
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return shutil.which("ffmpeg")


# ---------------------------------------------------------------- 缓存键

def source_fingerprint(path: str) -> str:
    """源文件的指纹：路径 + 大小 + mtime。

    用 (大小, mtime) 而不是只按路径：**同一个文件被替换/重新转出后必须失效**，
    否则播放器会拿到上一版音频（用户会说"声音和歌词对不上"）。
    """
    p = os.path.abspath(path)
    try:
        st = os.stat(p)
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        stamp = "missing"
    h = hashlib.sha1(f"{p}|{stamp}".encode("utf-8", "replace")).hexdigest()
    return h[:16]


def proxy_path(source: str) -> str:
    """该源文件对应的代理路径（不保证已存在）。"""
    return os.path.join(cache_dir(), source_fingerprint(source) + ".mp3")


# ---------------------------------------------------------------- LRU

def _entries() -> list:
    """[(path, size, mtime)]，按 mtime 从新到旧。"""
    d = cache_dir()
    out = []
    try:
        with os.scandir(d) as it:
            for e in it:
                if not e.is_file() or not e.name.endswith(".mp3"):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                out.append((e.path, st.st_size, st.st_mtime))
    except OSError:
        return []
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def stats() -> Dict[str, object]:
    """缓存现状：文件数 / 总字节 / 上限。给界面显示与自检用。"""
    items = _entries()
    total = sum(s for _, s, _ in items)
    return {
        "dir": cache_dir(),
        "files": len(items),
        "bytes": total,
        "mb": round(total / 1048576, 2),
        "max_bytes": DEFAULT_MAX_BYTES,
    }


def purge_all() -> int:
    """**删除全部代理缓存**，返回删掉的文件数。

    用户要求"缓存不能一直留着"：界面上一键清空就走这里。
    删不掉的文件（被播放器占用）**跳过而不是报错** —— 上层调用时
    播放器可能正拿着它，此时删失败是正常的。
    """
    n = 0
    for path, _, _ in _entries():
        try:
            os.remove(path)
            n += 1
        except OSError:
            pass
    # 顺手清理可能残留的临时文件
    try:
        with os.scandir(cache_dir()) as it:
            for e in it:
                if e.is_file() and e.name.startswith(".tmp_"):
                    try:
                        os.remove(e.path)
                    except OSError:
                        pass
    except OSError:
        pass
    return n


def purge_for_source(source: str) -> bool:
    """删掉某个源文件对应的代理（源换了/删了时调用）。"""
    p = proxy_path(source)
    try:
        os.remove(p)
        return True
    except OSError:
        return False


def evict_lru(max_bytes: int = DEFAULT_MAX_BYTES) -> int:
    """按最近使用时间裁剪，直到总量不超过上限。返回删除数。"""
    items = _entries()
    total = sum(s for _, s, _ in items)
    n = 0
    for path, size, _ in reversed(items):          # 最旧的先删
        if total <= max_bytes:
            break
        try:
            os.remove(path)
            total -= size
            n += 1
        except OSError:
            pass
    return n


# ---------------------------------------------------------------- 生成

def build(source: str, bitrate: str = _BITRATES[0],
          force: bool = False) -> Tuple[str, str]:
    """生成（或复用）代理文件。返回 ``(代理路径, 说明)``。

    说明用于日志：``cached`` / ``built(1.5s,64k)``。

    抛 ``RuntimeError`` 的情况：ffmpeg 不存在、转码失败、产出过小。
    上层（`/api/audio`）应当**回退到原始音频**而不是让编辑台没声音。

    ⚠️ 同一个源文件**串行化**：播放器会并发发多个 Range 请求，
    不串行化就会重复跑 ffmpeg、互相覆盖同一个临时文件。
    """
    key = os.path.abspath(source)
    with _lock_for(key):
        return _build_locked(source, bitrate=bitrate, force=force)


def _build_locked(source: str, bitrate: str = _BITRATES[0],
                  force: bool = False) -> Tuple[str, str]:
    src = os.path.abspath(source)
    if not os.path.isfile(src):
        raise RuntimeError(f"源音频不存在：{src}")

    dst = proxy_path(src)
    if not force and os.path.isfile(dst) and os.path.getsize(dst) > _MIN_OUTPUT_BYTES:
        # 摸一下 mtime，让 LRU 认为它是"最近用过"
        try:
            os.utime(dst, None)
        except OSError:
            pass
        return dst, "cached"

    exe = find_ffmpeg()
    if not exe:
        raise RuntimeError("找不到 ffmpeg（设 L2T_FFMPEG 或把 ffmpeg.exe 放进程序目录）")

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    except OSError:
        pass

    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".mp3", dir=os.path.dirname(dst))
    os.close(fd)
    last_err = ""
    try:
        for br in (bitrate,) + tuple(b for b in _BITRATES if b != bitrate):
            t0 = time.time()
            cmd = [exe, "-y", "-v", "error", "-i", src,
                   "-vn",                # 丢掉封面图（FLAC 里常有 mjpeg，白解码）
                   "-ac", "1",           # 单声道足够试听
                   "-ar", "44100",
                   "-b:a", br,
                   "-f", "mp3", tmp]
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                last_err = f"ffmpeg 超时（>{_TIMEOUT_SEC}s，码率 {br}）"
                continue
            except OSError as e:
                raise RuntimeError(f"ffmpeg 无法执行：{e}") from e

            size = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
            if proc.returncode == 0 and size > _MIN_OUTPUT_BYTES:
                os.replace(tmp, dst)          # 原子上位：不会留下半截代理
                dt = time.time() - t0
                try:
                    evict_lru()               # 别无限膨胀
                except Exception:             # noqa: BLE001
                    pass
                warn = proc.stderr.decode("utf-8", "replace").strip()
                note = f"built({dt:.1f}s,{br})" + ("-with-warnings" if warn else "")
                return dst, note

            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-2:]
            last_err = f"ffmpeg 退出码 {proc.returncode}，产出 {size} 字节：" + " / ".join(tail)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    raise RuntimeError(f"代理音频生成失败：{last_err}")


def ensure(source: str, force: bool = False) -> Tuple[str, str]:
    """`build()` 的别名，语义上强调"确保可用"。"""
    return build(source, force=force)
