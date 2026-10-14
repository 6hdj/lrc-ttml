"""守「安装程序只要求"装完能打开"，大件留到软件里下」。

    python tools/test_install_scope.py

## 用户的原始诉求（2026-10-14）

> 安装程序需要检查的只是**会影响软件打开**的东西。
> 处理歌词时才需要下载的，可以在软件里的**模型管理页**下载 ——
> 不需要让用户还没打开软件就去下这些。

## 改之前是什么样

`envcheck` 的 `level=CRIT` 把两件事混成一个等级：

* `pkg_faster_whisper` / 写出权限 / 磁盘空间 → 缺了**核心真跑不动**；
* `whisper`（语音模型 **3 GB**）、`cuda_*.dll`（**527 MB**）
  → 缺了**软件照样打开**，只是点「开始制作」做不了。

而安装程序按 `level == CRIT` 判"必需"，于是**软件还没打开就要求下 3.5 GB**。

现在按 `scope` 分层：`launch` / `convert` / `extra`。

## 本测试守什么

1. 两个大件**必须**是 `convert`，绝不能落进 `launch`；
2. 核心依赖（`pkg_*` 关键项、目录可写、磁盘）**必须**是 `launch`；
3. 未登记的键**保守**归到 `convert`（宁可安装程序少拦，也不多拦）；
4. 反向：真的缺一个 launch 项时，`launch_blockers` **必须**报出来
   （否则这个分层就是个摆设）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["L2T_HF_CACHE"] = os.path.join(HERE, ".hf_cache")

import envcheck  # noqa: E402

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def item(key: str, ok: bool = True, level: str = envcheck.CRIT):
    return envcheck.Item(key, key, "why", level, ok)


print("=" * 74)
print("  安装作用域：只拦「影响打开」的，不要求在安装阶段下大件")
print("=" * 74)

# ---- 1. 两个大件必须是 convert ----
print("\n[1] 大件不能在安装阶段被要求下载")
for k in ("whisper", "cuda_cublas64_12.dll", "cuda_cublasLt64_12.dll", "gpu"):
    check(envcheck.scope_of(item(k)) == envcheck.CONVERT,
          f"{k} 属于 convert（处理歌词才要）",
          envcheck.scope_of(item(k)))
big = [item("whisper", ok=False), item("cuda_cublasLt64_12.dll", ok=False)]
check(envcheck.launch_blockers(big) == [],
      "这两个大件**都不在** install 阶段的拦截里",
      str([i.key for i in envcheck.launch_blockers(big)]))

# ---- 2. 核心依赖必须是 launch ----
print("\n[2] 真正影响打开的背后必须是 launch")
for k in ("python", "pkg_faster_whisper", "pkg_ctranslate2", "pkg_numpy",
          "pkg_huggingface_hub", "pkg_av", "pkg_tokenizers", "disk",
          "writable"):
    check(envcheck.scope_of(item(k)) == envcheck.LAUNCH,
          f"{k} 属于 launch（影响打开）", envcheck.scope_of(item(k)))

# ---- 3. 未登记的键保守归 convert ----
print("\n[3] 未登记的键：保守归 convert")
check(envcheck.scope_of(item("some_new_key")) == envcheck.CONVERT,
      "未登记的新键不会意外拦安装（保守）",
      envcheck.scope_of(item("some_new_key")))

# ---- 4. 反向：真缺 launch 项时必须报出来 ----
print("\n[4] 反向验证（不然分层就是摆设）")
lb = envcheck.launch_blockers([item("pkg_numpy", ok=False)])
check([i.key for i in lb] == ["pkg_numpy"],
      "缺 pkg_numpy 时 launch_blockers 报出来", str([i.key for i in lb]))
cb = envcheck.convert_blockers([item("whisper", ok=False)])
check([i.key for i in cb] == ["whisper"],
      "缺 whisper 时 convert_blockers 报出来", str([i.key for i in cb]))
check(envcheck.launch_blockers([item("pkg_numpy", ok=True)]) == [],
      "不缺时不报")

# ---- 5. 真实环境跑一遍，确认结论符合预期 ----
print("\n[5] 真实环境（当前机器）")
base = os.environ.get("LOCALAPPDATA", HERE) + r"\Lyric2TTML"
items = envcheck.check_all(base=base, cache_dir=os.environ["L2T_HF_CACHE"],
                           frozen=True)
lb = envcheck.launch_blockers(items)
cb = envcheck.convert_blockers(items)
print(f"       共 {len(items)} 项；launch 缺 {len(lb)} 项；"
      f"convert 缺 {len(cb)} 项")
print(f"       launch 缺：{[i.key for i in lb] or '（无）'}")
print(f"       convert 缺：{[i.key for i in cb] or '（无）'}")
check(all(envcheck.scope_of(i) != envcheck.LAUNCH for i in items
          if i.key in ("whisper",) and not i.ok),
      "语音模型即使缺失也不进 launch 拦截")

print("\n" + "=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
if _fail:
    print("  ⚠️ 安装程序可能会在打开软件前就要求下载大件")
    sys.exit(1)
print("  ✓ 安装阶段只保证「装完能打开」；大件到软件里下")
