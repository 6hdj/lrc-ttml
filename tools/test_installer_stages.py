"""程序化核对三个阶段的关键控件文字（不靠肉眼读图）。

    python tools/test_installer_stages.py

⚠️ 为什么要有它：第 23 轮我对着一张缩略图把 `62%` 读成了 `2%`，差点又去查
一个不存在的问题。**能从控件读出来的事实，就不要用眼睛读图。**
截图用来判断"版面/配色对不对"，控件文字用来判断"内容对不对"，两者分工不同。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["L2T_DATA_DIR"] = os.path.join(HERE, ".tmp", "stage-check")

import tkinter as tk  # noqa: E402

import installer_gui as ig  # noqa: E402

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


ig._declare_dpi_aware()
root = tk.Tk()
# ⚠️⚠️ **不要依赖 `--stage` 命令行参数**（真踩到）：
# 第一版直接构造 `Installer(root, autostart=False)` 就让 `--stage` 去设阶段，
# 但**测试进程的 `sys.argv` 里没有 `--stage`** —— 于是阶段停在 "choose"，
# 「第 3 步副标题说完成」这条断言失败。
# 更阴的是：**单独跑能过、被交付报告（子进程）跑就失败**，
# 因为我是用 `--stage done` 手动验证的。所以这里**显式调 `_preview_stage()`**，
# 不依赖任何命令行参数。
inst = ig.Installer(root, autostart=False)
inst._preview_stage("choose")
root.update()

print("=" * 74)
print("  安装器三个向导阶段：控件文字核对")
print("=" * 74)

# ---- 第 1 步：只应看到路径与勾选项 ----
inst._preview_stage("choose")
root.update()
print("\n[1] choose")
print(f"    副标题     : {inst.sub.cget('text')}")
print(f"    主按钮     : {inst.btn_go.cget('text')}")
print(f"    路径框可见 : {bool(inst.pathbar.winfo_manager())}")
print(f"    勾选区可见 : {bool(inst._opts.winfo_manager())}")
print(f"    进度条可见 : {bool(inst._pbar.winfo_manager())}")
check(bool(inst.pathbar.winfo_manager()), "第 1 步显示「安装到」路径框")
check(bool(inst._opts.winfo_manager()), "第 1 步显示快捷方式勾选项")
check(not inst._pbar.winfo_manager(), "第 1 步**收起**进度条（否则中间一大片空白）")
check(not inst.wrap.winfo_manager(), "第 1 步**收起**检查清单")

# ---- 第 2 步：进度 + 清单 + 日志 ----
inst._preview_stage("install")
root.update()
print("\n[2] install")
print(f"    副标题     : {inst.sub.cget('text')}")
print(f"    进度条值   : {inst.pb.cget('value')}")
print(f"    百分比文字 : {inst.lb_pct.cget('text')}")
print(f"    主按钮     : {inst.btn_go.cget('text')}")
check(bool(inst._pbar.winfo_manager()), "第 2 步显示进度条")
check(bool(inst.wrap.winfo_manager()), "第 2 步显示检查清单")
check(int(inst.pb.cget("value")) == 62, "进度条值 = 62（真实值，不是编的）",
      f"实得 {inst.pb.cget('value')}")
check("62%" in inst.lb_pct.cget("text"), "百分比文字含 62%",
      inst.lb_pct.cget("text"))
check("529" in inst.lb_pct.cget("text"), "百分比文字带总量（不只有百分比）",
      inst.lb_pct.cget("text"))

# ---- 第 3 步：完成态 ----
inst._preview_stage("done")
root.update()
print("\n[3] done")
print(f"    副标题     : {inst.sub.cget('text')}")
print(f"    主按钮     : {inst.btn_go.cget('text')}")
print(f"    进度条值   : {inst.pb.cget('value')}")
check("完成" in inst.sub.cget("text"), "第 3 步副标题说「完成」",
      inst.sub.cget("text"))
check("启动" in inst.btn_go.cget("text"),
      "第 3 步主按钮是「启动软件」（任务书 E：完成后启动）",
      inst.btn_go.cget("text"))
check(int(inst.pb.cget("value")) == 100, "第 3 步进度条 100")

# ---- 步骤条高亮 ----
print("\n[4] 步骤条高亮")
for stage in ("choose", "install", "done"):
    inst._preview_stage(stage)
    root.update()
    cols = [inst.steps[k].cget("fg") for k in ("choose", "install", "done")]
    print(f"    {stage:8s} 颜色 = {cols}")
    idx = ("choose", "install", "done").index(stage)
    # 走过的应是 OK 绿，当前是强调色，未到是 faint
    check(cols[idx] == ig.ACCENT, f"{stage}: 当前步骤用强调色")
    if idx > 0:
        check(cols[0] == ig.OK, f"{stage}: 走过的第 1 步用 OK 绿")
    if idx < 2:
        check(cols[-1] == ig.FAINT, f"{stage}: 未到的第 3 步用 faint")

root.destroy()
print("\n" + "=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
