"""两个实测 bug 的回归：torch 的 DLL 被搬走、Demucs 声明指向错的来源。

    python tools/test_runtime_layout.py

## 现场（2026-10-15 用户日志）

    [14:34:04] [整理] 9 个 DLL 统一放到 bin/
    [14:34:04] [失败] 装好了但加载失败：import torch 失败：
               ImportError: DLL load failed while importing _C: 找不到指定的模块。

`_hoist_dlls()` 为了兼容 CUDA 的 `nvidia/cublas/bin/` 布局，把 site 下
**所有** .dll 都提到 `bin/` —— 对 torch 是灾难：`torch/_C.pyd` 按相对路径
去 `torch/lib/` 找 `c10.dll` / `torch_cpu.dll` / `libiomp5md.dll`，全被搬走了。

    [14:33:53] RuntimeError: 下载完成但校验没过：缓存里没有这个模型

Demucs 的 `must_have` 写的是 `955717e8-8726e21a.th`，而
`adefossez/HTDemucs`（HF）里**根本没有这个文件**（只有 safetensors 等 5 个），
且布局是 HF 的 `blobs/snapshots`，**不是** demucs 包要的
`hub/checkpoints/`。⇒ 下载成功也永远校验不过。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from desktop.services import runtime_service as rs  # noqa: E402

_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


print("=" * 78)
print("  ① _hoist_dlls 只能对【按文件布局校验】的组件用")
print("=" * 78)
SB = os.path.join(HERE, ".tmp", "rt-layout")
shutil.rmtree(SB, ignore_errors=True)

# 造一个"类 torch"的 site：依赖 DLL 在 torch/lib/ 里
site = os.path.join(SB, "site_torch")
os.makedirs(os.path.join(site, "torch", "lib"), exist_ok=True)
for n in ("c10.dll", "torch_cpu.dll", "libiomp5md.dll", "uv.dll"):
    with open(os.path.join(site, "torch", "lib", n), "wb") as fh:
        fh.write(b"dll")

src = io.open(os.path.join(HERE, "desktop", "services",
                           "runtime_service.py"), encoding="utf-8").read()
seg = src[src.find("def install_to_site") if "def install_to_site" in src else 0:]
# 判据必须写在源码里：只有声明了 verify_files 才整理
call = src[src.find("if spec.get(\"verify_files\"):"):]
check(call.startswith('if spec.get("verify_files"):'),
      "源码里 `_hoist_dlls` 由 `if spec.get(\"verify_files\")` 守着",
      src[src.find("_hoist_dlls(tmp_site"):][:80].replace("\n", "⏎"))

# 行为：真调一次 _hoist_dlls，确认它确实会搬走 —— 这正是"不能对 torch 调"的原因
rs._hoist_dlls(site, log=lambda m: None)
moved = os.path.isfile(os.path.join(site, "bin", "c10.dll"))
check(moved, "`_hoist_dlls` 确实会把 `torch/lib/*.dll` 搬走（所以对 torch 绝不能调）")

# 重新造一份，验证"不调用"时结构完好
site2 = os.path.join(SB, "site_ok")
os.makedirs(os.path.join(site2, "torch", "lib"), exist_ok=True)
for n in ("c10.dll", "torch_cpu.dll"):
    with open(os.path.join(site2, "torch", "lib", n), "wb") as fh:
        fh.write(b"dll")
check(os.path.isfile(os.path.join(site2, "torch", "lib", "c10.dll")),
      "不整理时依赖 DLL 留在 torch/lib/（torch 能 import 的前提）")

print()
print("=" * 78)
print("  ② Demucs 的声明要指向 demucs 真正会去找的位置")
print("=" * 78)
mm = json.load(io.open(os.path.join(HERE, "desktop", "resources",
                                    "model_manifest.json"), encoding="utf-8"))
dem = next((s for s in mm["models"] if s.get("id") == "demucs"), None)
check(dem is not None, "清单里有 demucs 条目")
if dem:
    src_kind = (dem.get("source") or {}).get("kind")
    urls = (dem.get("source") or {}).get("urls") or []
    ver = dem.get("verify") or {}
    tgt = str(ver.get("target") or "")
    print(f"    source.kind = {src_kind}")
    print(f"    urls        = {[u.split('/')[2] for u in urls]}")
    print(f"    verify.kind = {ver.get('kind')}")
    print(f"    target      = {tgt}")
    check(src_kind == "http",
          "来源是 http（HF 那个仓库里没有 demucs 要的 .th）", str(src_kind))
    check(any("fbaipublicfiles" in u for u in urls),
          "指向官方权重站 dl.fbaipublicfiles.com", str([u.split('/')[2] for u in urls]))
    check(tgt.replace("\\", "/").endswith(
        "models/demucs/hub/checkpoints/955717e8-8726e21a.th"),
        "落盘位置 == `separate.py` 去查的 `models/demucs/hub/checkpoints/*.th`",
        tgt)
    check(ver.get("kind") == "file_sha256" and ver.get("sha256") and ver.get("size"),
          "校验方式是 file_sha256（带 size + sha256）", json.dumps(ver)[:80])
    # 文件名里的短哈希要与 sha256 前缀一致（Demucs 的命名约定，互为印证）
    check(str(ver.get("sha256", "")).startswith("8726e21a"),
          "sha256 前缀与文件名后缀 `-8726e21a` 一致", str(ver.get("sha256"))[:16])

# separate.py 的查找路径必须与清单一致
sep = io.open(os.path.join(HERE, "separate.py"), encoding="utf-8").read()
check('"hub", "checkpoints"' in sep or "'hub', 'checkpoints'" in sep,
      "`separate.py` 确实去 `hub/checkpoints` 找权重")
check("models" in sep and "demucs" in sep,
      "`separate.py` 的根目录是 models/demucs")

print()
print("=" * 78)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 78)
sys.exit(1 if _fail else 0)
