"""守「README 不能把新用户引到旧版本」。

    python tools/test_readme.py

## 为什么要这个检查（用户明确提的问题）

用户的原话是：**"避免新用户照着旧版教程操作"**。
这不是假想问题 —— v1.1.0 的安装方式和 v1.0.x **完全不同**：

| | v1.0.x | v1.1.0 |
| --- | --- | --- |
| 下载 | `part1.bin` + `part2.bin`（1.8 GB）+ 手动跑 `merge-and-extract-v3.bat` | **单个 190 MB 的 exe** |
| 权限 | 装的时候**弹 UAC** | **不需要管理员** |
| CUDA 运行时 | **安装包内置** | **按需下载（529 MB）** |
| GPU 运行时 | 内置 | 首次要联网 |

README 里任何一条停留在旧版，都会让新用户照着一个**已经不存在的流程**操作
（去下 1.8 GB 的分卷、以为要弹 UAC、以为装完就能离线用）。

判据：从 `desktop/__init__.py`（版本号唯一来源）取出当前版本，
检查 README 里**必须出现**的项和**不允许出现**的旧说法。
"""
from __future__ import annotations

import io
import os
import re
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
    from desktop import __version__ as ver
    s = io.open(os.path.join(HERE, "README.md"), encoding="utf-8").read()
    print("=" * 74)
    print(f"  README 与版本一致性（当前版本 {ver}）")
    print("=" * 74)

    # ---- 1. 必须指向当前版本 ----
    print("\n[1] 下载入口指向当前版本")
    check(f"v{ver}" in s, f"README 里出现「v{ver}」")
    link = f"releases/download/v{ver}/"
    check(link in s, f"有指向 v{ver} 的下载直链", f"找不到 {link}")
    # 资产名（ASCII，见 .tmp 记录：中文名会被 GitHub 吃掉）
    check(f"Lyric2TTML-{ver}-setup.exe" in s,
          f"写明了资产文件名 Lyric2TTML-{ver}-setup.exe")

    # ---- 2. 不许把旧版说成当前做法 ----
    print("\n[2] 不能把旧版的流程当成现行做法")
    # ⚠️ 旧版的词**可以出现**（放在"旧版是那样、别照做"的说明里），
    # 但必须明确标注是旧版。这里查的是"出现时周围有没有'旧版/不要'字样"。
    for kw, why in (("part1.bin", "旧版的分卷下载"),
                    ("merge-and-extract", "旧版的手工合并脚本")):
        if kw not in s:
            check(True, f"README 里没有出现旧版做法「{kw}」")
            continue
        # 出现时必须带上"旧版/不要再照"的提示
        idx = s.find(kw)
        ctx = s[max(0, idx - 260):idx + 120]
        warn = any(k in ctx for k in ("旧版", "不要", "已经没有", "别照"))
        check(warn, f"提到「{kw}」时明确标注了那是旧版",
              f"上下文里没有警示语：{ctx[-90:]!r}")

    # ---- 3. 权限口径必须与实现一致（不再弹 UAC）----
    print("\n[3] UAC 口径与实现一致")
    check("不需要管理员" in s or "不需要权限" in s,
          "README 写明**默认不需要管理员权限**")
    # 旧 README 曾说"会弹 UAC 请求管理员权限 —— 安装程序需要"，那是错的
    bad = re.search(r"(会弹|需要).{0,12}UAC.{0,24}(安装程序需要|需要管理员)", s)
    check(bad is None, "没有「安装程序需要 UAC」这类旧说法",
          bad.group(0) if bad else "")

    # ---- 4. 运行组件口径 ----
    print("\n[4] GPU 运行时（方案 D）口径")
    check("529" in s or "528" in s, "写明了 GPU 运行时约 529 MB")
    check("按需下载" in s or "运行组件" in s,
          "说明了 CUDA 是**按需下载的运行组件**")
    # 旧 README 说"安装包已经内置 CUDA 运行库"
    check("安装包已经内置" not in s.replace("**安装包已经内置**", ""),
          "没有「安装包已经内置 CUDA 运行库」这类过时说法")
    check("FULL_CUDA" in s, "给出了不能联网时的出路（L2T_FULL_CUDA 完整包）")

    # ---- 5. 离线承诺不能自相矛盾 ----
    print("\n[5] 离线承诺")
    check("首次使用需要联网" in s or "首次使用要联网" in s,
          "明确标注首次使用需要联网（与全程离线的说法并不矛盾）")
    check("下完之后完全离线" in s or "都下完之后" in s or "完全离线可用" in s,
          "说明两样下完之后可以完全离线")

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    if _fail:
        print("  ⚠️ README 会把新用户引到旧版做法 —— 这是用户明确要求避免的")
        return 1
    print("  ✓ README 与当前版本、权限与运行组件口径一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
