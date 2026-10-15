"""运行时/模型下载源的**排序策略**（不联网，只看清单）。

    python tools/test_mirror_policy.py

## 为什么要有这条

2026-10-15 用户实测：`torch` 118MB + `cuda` 529MB **下载总是非常慢、还会失败**。
实测（各源取前 1MB，绕开失效的系统代理）：

    torch-2.14.1-cp314-cp314-win_amd64.whl
        腾讯云 2701 KB/s > 南大 2023 > 阿里云 439 > 官方 PyPI 232
    nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl
        腾讯云 2258 KB/s > 南大 1668 > 阿里云 398 > 官方 PyPI 148
    cuda-runtime-win-x64-cu12.zip（github）
        **超时，完全不可用**
    清华 / 北外：连不上（10061）；中科大：超时

修好之后实测：527.5 MB **90 秒下完（5.83 MB/s）**。

这条用例守的是**策略**（顺序会决定"先撞哪个源"），不是具体速度
（速度随网络变化，不该写死在断言里）。
"""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


# 实测可用的国内镜像（按实测速度排序，前面的必须排更前）
CN_FAST = ("mirrors.cloud.tencent.com", "mirror.nju.edu.cn", "mirrors.aliyun.com")
# 官方源：能通但很慢（~0.1-0.2 MB/s），只能兜底
SLOW = ("files.pythonhosted.org", "pypi.org", "github.com")

mf = json.load(io.open(os.path.join(HERE, "desktop", "resources",
                                    "runtime_manifest.json"), encoding="utf-8"))

print("=" * 82)
print("  运行时组件（runtime_manifest.json）")
print("=" * 82)
entries = []
for rt in mf["runtimes"]:
    for key in ("wheels", "archives"):
        for w in (rt.get(key) or []):
            entries.append((rt["id"], w))

for rid, w in entries:
    urls = list(w.get("urls") or [])
    hosts = [u.split("/")[2] for u in urls]
    fn = w["filename"]
    print(f"  {rid:6s} {fn[:44]:46s} {hosts}")
    check(bool(urls), f"{fn}：至少有一个下载地址")
    check(bool(w.get("sha256")) and bool(w.get("size")),
          f"{fn}：声明了 size + sha256（换镜像不许改动它们）")
    if not urls:
        continue
    first = hosts[0]
    if fn.endswith(".whl"):
        # wheel 是 PyPI 包 → 必须走国内 PyPI 镜像
        check(first in CN_FAST,
              f"{fn}：第一优先是实测最快的国内镜像",
              f"实际 {first}")
        check(any(h in CN_FAST for h in hosts),
              f"{fn}：至少有一个国内镜像", str(hosts))
        check(len(urls) >= 3,
              f"{fn}：≥3 个备选（单源失败要能自动换）", f"{len(urls)} 个")
        # 官方源必须在所有国内源之后
        cn_idx = [i for i, h in enumerate(hosts) if h in CN_FAST]
        slow_idx = [i for i, h in enumerate(hosts) if h in SLOW]
        if cn_idx and slow_idx:
            check(max(cn_idx) < min(slow_idx),
                  f"{fn}：国内镜像全部排在官方源之前",
                  f"国内 {cn_idx} vs 官方 {slow_idx}")
    else:
        check("github.com" in hosts,
              f"{fn}：只有 github（自建产物，没有镜像）", str(hosts))

print()
print("=" * 82)
print("  模型（model_manifest.json）里的 HF 端点")
print("=" * 82)
mp = os.path.join(HERE, "desktop", "resources", "model_manifest.json")
mm = json.load(io.open(mp, encoding="utf-8"))
hf_hosts = set()
for s in mm.get("models", []):
    for u in ((s.get("source") or {}).get("urls") or []):
        hf_hosts.add(u.split("/")[2])
    for h in ((s.get("source") or {}).get("hosts") or []):
        hf_hosts.add(str(h))
print(f"  出现过的域名：{sorted(hf_hosts)}")
check(not hf_hosts or any("hf-mirror" in h or "modelscope" in h for h in hf_hosts),
      "模型源里有国内可达的（hf-mirror / modelscope）", str(sorted(hf_hosts)))

print()
print("=" * 82)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 82)
sys.exit(1 if _fail else 0)
