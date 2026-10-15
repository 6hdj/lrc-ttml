"""回归测试：卸载/重装**不能**吃掉用户的运行组件（CUDA 529MB + PyTorch 509MB）。

    python tools/test_runtime_preserve.py

## 为什么专门守这一条

2026-10-15 用户实测的真事故：重装一次 → `runtime/` 被清空 → 软件认为
"CUDA 没装" → 在约 0.5 MB/s 的链路上重下 529 MB（界面卡在
「下载中 9.0B/528.8MB」638 秒以上）。运行组件是**用户花流量换来的**，
必须和模型缓存同等对待。

两处都守：
① `_preserve_paths()` 含 `runtime`；收尾 bat 不再整目录 `rmdir`（静态）；
② 真调 `_rmtree_except()`（就是清空安装目录的那个函数），
   断言 `runtime/**` 一个文件都没少、而普通文件被删掉（行为）。
"""
from __future__ import annotations

import io
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import uninstall_core as U  # noqa: E402

_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


print("=" * 74)
print("  ① 静态：保留名单 + 收尾脚本")
print("=" * 74)
keep = U._preserve_paths("X:/whatever")
check("runtime" in keep, "`_preserve_paths()` 含 `runtime`", str(sorted(keep)))
check(U.EXE_NAME in keep and "_internal" in keep,
      "原有的 exe / _internal 保留没被弄丢", str(sorted(keep)))

src = io.open(os.path.join(HERE, "uninstall_core.py"), encoding="utf-8").read()
seg = src[src.find("Lyric2TTML 卸载收尾"):]
seg = seg[:seg.find("try:")]
check("runtime" in seg,
      "收尾脚本里明确提到 runtime（逐项删、跳过它）",
      seg[:100].replace("\n", "⏎"))
check('rmdir /s /q "{target}"' not in seg,
      "不再有一句整目录 `rmdir /s /q \"{target}\"`（那会把 runtime 一起收掉）")

print()
print("=" * 74)
print("  ② 行为：真调一次 `_rmtree_except`（清空安装目录的那个函数）")
print("=" * 74)
SB = os.path.join(HERE, ".tmp", "rt-preserve")
shutil.rmtree(SB, ignore_errors=True)
install = os.path.join(SB, "install")


def mk(rel, blob=b"x"):
    p = os.path.join(install, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(blob)
    return p


exe = mk(U.EXE_NAME, b"MZ fake")
internal = mk("_internal/python314.dll", b"DLL")
cublas = mk("runtime/cuda/bin/cublas64_12.dll", b"CUBLAS" * 100)
zipf = mk("runtime/.wheels/cuda-runtime.zip", b"ZIP" * 200)
readme = mk("README.md", b"readme")
junk = mk("outputs/x.ttml", b"<tt/>")

U._rmtree_except(install, U._preserve_paths(install))

check(os.path.isfile(exe), "exe 还在（卸载程序还能再跑一次）")
check(os.path.isfile(internal), "_internal 还在")
check(os.path.isfile(cublas),
      "**CUDA 运行库还在**（原来会被删 → 用户被迫重下 529 MB）")
check(os.path.isfile(zipf), "**已下好的离线包还在**（重装不必再下）")
check(not os.path.isfile(readme), "普通文件被删掉（卸载确实清了东西）")
check(not os.path.isfile(junk), "用户产物目录也被清掉（行为不变）")

print()
print("=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
