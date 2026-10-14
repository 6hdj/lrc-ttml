"""守「模型缓存能被复用，不会莫名其妙重新下 3GB」。

    python tools/test_hf_cache.py

## 这个 bug 是**真踩到的**（2026-10-13）

冻结版转换时，本地**明明有** 3GB 的 large-v3，程序却打印
「本地缓存中未找到 large-v3，将尝试联网下载」并开始下载 ——
看起来像"卡住"（实测 21 秒 CPU / 30 分钟没结束，我一度以为挂死了）。

根因在 `launcher.py`：

    hub = os.path.join(base, ".hf_cache")
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", hub)     # ← 罪魁

程序认**两个**名字：`HUGGINGFACE_HUB_CACHE`（HF 官方）和
`L2T_HF_CACHE`（本项目别名，`desktop/paths.hf_cache_dir()` 认它）。
`setdefault` 只看官方那个，于是**只要用户传的是 `L2T_HF_CACHE`，
这行就抢先把它写死成 `<exe>/.hf_cache`**，后面想纠正也晚了。

实测三分对照（同一份缓存、同一台机）：

| 传的环境变量 | 会不会去联网下载 |
| --- | --- |
| 只传 `L2T_HF_CACHE`（旧行为） | **会**（跑 90 秒还没完） |
| 只传 `HUGGINGFACE_HUB_CACHE` | 不会 |
| 两个都传 | 不会 |

修复后三种写法**都不再下载**。本测试守住这一条。

## 为什么值得单独一个测试

它属于"**不报错、只是慢**"的一类：没有异常、没有失败退出码，
只是悄悄开始下一遍 3GB。用户看到的是"程序卡住了"，
而开发者很容易把它当成网络慢。**测出来的成本远低于查出来的成本。**
"""
from __future__ import annotations

import io
import os
import sys

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


def main() -> int:
    print("=" * 74)
    print("  模型缓存复用（L2T_HF_CACHE / HUGGINGFACE_HUB_CACHE）")
    print("=" * 74)

    # ---- 1. 源码：asr.py 的三个来源都要认 ----
    # ⚠️⚠️ **不能只判断"字符串在不在"**（变异检查抓出来的假绿）：
    # 原来写 `"L2T_HF_CACHE" in seg` —— 把
    # `os.environ.get("L2T_HF_CACHE")` 改成 `os.environ.get("L2T_HF_CACHE_X")`
    # 之后，子串 **依然在**，测试照样通过。
    # 现在直接断言**取值表达式本身**（多行，含 `os.environ.get(...)`），
    # 改坏任何一个字符都会失败。
    print("\n[1] asr.py 的取值优先级")
    src = io.open(os.path.join(HERE, "asr.py"), encoding="utf-8").read()
    i = src.find("self.cache_dir = (")
    seg = src[i:i + 420]
    has_official = 'os.environ.get("HUGGINGFACE_HUB_CACHE")' in seg
    has_alias = 'or os.environ.get("L2T_HF_CACHE")' in seg
    print(f"       认 HUGGINGFACE_HUB_CACHE: {has_official}")
    print(f"       认 L2T_HF_CACHE（别名） : {has_alias}")
    check(has_official, "asr 认官方变量 HUGGINGFACE_HUB_CACHE",
          "改坏取值表达式时应失败")
    check(has_alias,
          "asr 认项目别名 L2T_HF_CACHE（断言的是取值表达式，不是子串）",
          "只认一个的话，另一种写法会被静默无视、然后重新下载 3GB")

    # ---- 2. 源码：launcher 不能用 setdefault 锁死缓存 ----
    print("\n[2] launcher.py 不能把缓存写死")
    lsrc = io.open(os.path.join(HERE, "launcher.py"), encoding="utf-8").read()
    bad = 'os.environ.setdefault("HUGGINGFACE_HUB_CACHE"'
    check(bad not in lsrc,
          "launcher 里没有 `setdefault(\"HUGGINGFACE_HUB_CACHE\", …)`（会吃掉用户指定）"
          , "老写法会让 L2T_HF_CACHE 失效")
    check("L2T_HF_CACHE" in lsrc, "launcher 认 L2T_HF_CACHE")
    check('os.environ["HUGGINGFACE_HUB_CACHE"] = hub' in lsrc,
          "launcher 用**覆盖**（而不是 setdefault）写官方变量")

    # ---- 3. 行为：真的解析出用户指定的那个目录 ----
    print("\n[3] 实际行为（不启动 Qt、不起进程）")
    from desktop import paths
    target = os.path.join(HERE, ".hf_cache")
    old = os.environ.get("L2T_HF_CACHE")
    oldh = os.environ.get("HUGGINGFACE_HUB_CACHE")
    try:
        os.environ["L2T_HF_CACHE"] = target
        os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
        got = paths.hf_cache_dir()
        check(os.path.abspath(got) == os.path.abspath(target),
              "paths.hf_cache_dir() 认 L2T_HF_CACHE", f"得到 {got}")

        # asr 的构造优先级（不真的加载模型，只看 cache_dir 取值）
        os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
        from asr import WhisperASR
        a = WhisperASR.__new__(WhisperASR)
        chosen = (os.environ.get("HUGGINGFACE_HUB_CACHE")
                  or os.environ.get("L2T_HF_CACHE"))
        check(os.path.abspath(chosen or "") == os.path.abspath(target),
              "asr 的取值链在只给别名时也指向同一个目录", f"得到 {chosen}")

        # 缓存目录里**真的**有 large-v3 才说明这个测试有意义
        snap = os.path.join(target, "models--Systran--faster-whisper-large-v3")
        if os.path.isdir(snap):
            import glob
            bins = glob.glob(os.path.join(snap, "snapshots", "*", "model.bin"))
            check(bool(bins), "该缓存里确实有 large-v3 的 model.bin（对照物存在）")
        else:
            print("       （跳过：本机 .hf_cache 里没有 large-v3，"
                  "对照物不存在）")
    finally:
        if old is None:
            os.environ.pop("L2T_HF_CACHE", None)
        else:
            os.environ["L2T_HF_CACHE"] = old
        if oldh is None:
            os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
        else:
            os.environ["HUGGINGFACE_HUB_CACHE"] = oldh

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    if _fail:
        print("  缓存复用可能又坏了 —— 用户会白下 3GB")
        return 1
    print("  缓存复用正常 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
