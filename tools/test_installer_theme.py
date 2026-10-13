"""守"安装程序与桌面版是同一套视觉"这条约定。

    python tools/test_installer_theme.py

## 为什么需要（真踩过）

`installer_gui.py` 原来自己抄了一份颜色常量，和 `desktop/pages/base.py` 的 `C`
**几乎每个色都不一样**：

    BG   #0f1117 vs #14161a      DIM  #8b95ad vs #9aa3b0
    OK   #3ddc84 vs #3ecf8e      BAD  #ff6b6b vs #ef5350
    WARN #ffc857 vs #e2b53e      ACCENT #4aa8ff vs #4c9aff

后果：**安装程序和应用看起来是两个不相干的产品** —— 用户装完打开应用会怀疑
"这不是我装的那个东西"。截图并排一看非常明显。

安装器是**独立打包**的（还要在没装 Qt 的环境里跑），所以不能 import
`desktop/pages/base.py` 拿颜色，只能复制常量。**复制就必须有测试守着**，
否则下次改一边忘了另一边，又漂回去。

本测试逐一比对两边**同名语义**的颜色，不一致就报错；同时扫一遍
`installer_gui.py` 里有没有绕过常量、直接写死十六进制色的地方。
"""
from __future__ import annotations

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _desktop_palette() -> dict:
    """从 `desktop/pages/base.py` 里取出**深色主题**的调色板（不导入 Qt）。

    ⚠️ 2026-10-13 起调色板改名了：原来是 `C = {...}`，现在是
    `DARK = {...}` / `LIGHT = {...}`，`C` 变成"当前生效的那一份"（由
    `apply_theme()` 原地更新）。安装器的配色是照**深色**做的，
    所以这里要取 `DARK`。直接找 `C = {` 会报"找不到 C = {...}"（真踩到）。
    """
    src = io.open(os.path.join(HERE, "desktop", "pages", "base.py"),
                  encoding="utf-8").read()
    m = re.search(r"^DARK = \{(.*?)^\}", src, re.S | re.M)
    if not m:
        raise SystemExit("在 desktop/pages/base.py 里找不到 DARK = {...}")
    out = {}
    for line in m.group(1).split("\n"):
        mm = re.match(r'\s*"([a-z_0-9]+)"\s*:\s*"(#[0-9a-fA-F]{6})"', line)
        if mm:
            out[mm.group(1)] = mm.group(2).lower()
    return out


def _installer_palette() -> dict:
    src = io.open(os.path.join(HERE, "installer_gui.py"),
                  encoding="utf-8").read()
    out = {}
    for name in ("BG", "PANEL", "PANEL2", "LINE", "FG", "DIM", "OK", "BAD",
                 "WARN", "ACCENT", "ACCENT_DIM", "CYAN", "FAINT"):
        m = re.search(rf"^{name} = \"(#[0-9a-fA-F]{{6}})\"", src, re.M)
        if m:
            out[name] = m.group(1).lower()
    return out, src


# installer 常量名 → desktop 调色板键名（语义一一对应）
PAIRS = [
    ("BG", "bg"), ("PANEL", "panel"), ("PANEL2", "panel2"), ("LINE", "line"),
    ("FG", "text"), ("DIM", "dim"), ("OK", "ok"), ("BAD", "err"),
    ("WARN", "warn"), ("ACCENT", "accent"), ("ACCENT_DIM", "accent_dim"),
    ("CYAN", "cyan"), ("FAINT", "faint"),
]


def main() -> int:
    dp = _desktop_palette()
    ip, src = _installer_palette()
    print("=" * 74)
    print("  安装程序 vs 桌面版：调色板一致性")
    print("=" * 74)

    bad = []
    for iname, dname in PAIRS:
        if iname not in ip:
            print(f"  [跳过] installer 里没有 {iname}（可选）")
            continue
        if dname not in dp:
            print(f"  [跳过] desktop 里没有 C['{dname}']")
            continue
        same = ip[iname] == dp[dname]
        mark = "✓" if same else "✗"
        print(f"  {mark} {iname:11s} {ip[iname]}  vs  C['{dname}'] {dp[dname]}")
        if not same:
            bad.append((iname, ip[iname], dname, dp[dname]))

    # 绕过常量、直接写死十六进制色的地方（允许极少数例外，列出来人工确认）
    print("\n" + "=" * 74)
    print("  安装器里是否还有绕过常量写死的颜色")
    print("=" * 74)
    const_vals = set(ip.values())
    hard = []
    for i, line in enumerate(src.split("\n"), 1):
        if line.strip().startswith("#"):
            continue
        for h in re.findall(r"#(?:[0-9a-fA-F]{6})", line):
            if h.lower() not in const_vals:
                hard.append((i, h, line.strip()[:80]))
    if hard:
        for i, h, ln in hard:
            print(f"  L{i}: {h}   {ln}")
    else:
        print("  ✓ 没有（全部走常量）")

    print("\n" + "=" * 74)
    ok = not bad
    if ok:
        print("  ✓ 调色板一致（安装程序与桌面版看起来是同一个产品）")
    else:
        print(f"  ✗ {len(bad)} 项颜色不一致 —— 改一处忘了另一处")
        for iname, iv, dname, dv in bad:
            print(f"      installer.{iname}={iv}  应为 C['{dname}']={dv}")
    print("=" * 74)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
