"""守「首次启动引导」：必须覆盖**真正阻塞**的东西，且第一眼就有内容。

    python tools/test_first_run.py

## 两个真问题（第 25 轮抓到）

**① 引导没提 GPU 运行时 ⇒ 用户按引导走完仍然做不了歌词。**
主包自第 19 轮起不含 CUDA 运行时（方案 D：717.7 MB → 189.9 MB），
而语音识别**没有它就跑不了**（`asr.py` 明确拒绝 CPU）。
但欢迎页原来只有「① 环境 ② 模型 ③ 怎么用」——一个字都没提它。
实测（把 CUDA 打桩成未安装）：按钮只有 `['重新检查环境', '重新下载']`。

**② 运行组件那张卡在首次进入时是**空的**。**
`build()` 只调 `_light_check()`，而 `refresh()` 有
`if not self._built: return` —— `_built` 是外层在 `build()` 返回后才置位，
所以构建期的 `refresh()` 被**静默跳过**：标题和按钮在，中间那行状态**没有字**。
（截图里就是一个明显的空缺。）

⚠️ 本测试**必须把 CUDA 打桩成未安装**，否则开发机上 venv/系统里有 CUDA，
`check('cuda')` 报 ready，"该不该提 GPU 运行时"这条根本走不到 —— **空过**。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["L2T_HEADLESS"] = "1"
os.environ["L2T_DATA_DIR"] = os.path.join(HERE, ".tmp", "first-run-test")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from desktop.context import AppContext  # noqa: E402
from desktop.pages.welcome import WelcomePage  # noqa: E402
from desktop.services import runtime_service  # noqa: E402

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


# —— 打桩：CUDA 未安装（真实用户第一次的样子）——
_real_check = runtime_service.RuntimeManager.check
runtime_service.RuntimeManager.check = (
    lambda self, rid, use_cache=True: runtime_service.RuntimeState(
        id=rid, name="CUDA 运行时（显卡加速）", status="not_installed",
        detail="需要下载 528.8MB", download_bytes=554472678,
        installed_bytes=771556864)
    if rid == "cuda" else _real_check(self, rid, use_cache=use_cache))

try:
    print("=" * 74)
    print("  首次启动引导（CUDA 打桩为「未安装」）")
    print("=" * 74)
    page = WelcomePage(AppContext())
    page.ensure_built()
    for _ in range(6):
        app.processEvents()

    # ① 引导必须提到 GPU 运行时
    print("\n[1] 引导是否覆盖 GPU 运行时")
    from PySide6.QtWidgets import QPushButton
    btns = [b.text().strip() for b in page._root.findChildren(QPushButton)
            if b.text().strip()] if hasattr(page, "_root") else []
    print(f"       按钮：{btns}")
    check(any("GPU" in t or "运行时" in t for t in btns),
          "有「下载 GPU 运行时」这类按钮", f"{btns}")
    check("GPU" in page.head_rt.text() or "运行时" in page.head_rt.text(),
          "卡片标题提到 GPU 运行时", page.head_rt.text())

    # ② 第一眼就要有内容（不是空白）
    print("\n[2] 首次进入时卡片已有内容（不是空白）")
    print(f"       标题  : {page.head_rt.text()}")
    print(f"       状态  : {page.lb_rt.text()}")
    print(f"       说明  : {page.lb_rt_note.text()[:46]}…")
    check(bool(page.lb_rt.text().strip()),
          "运行组件卡的状态行**非空**（空 = 第一眼一片空缺）",
          repr(page.lb_rt.text()))
    check(bool(page.lb_rt_note.text().strip()),
          "运行组件卡的说明行非空", repr(page.lb_rt_note.text()))
    check("529" in page.lb_rt.text() or "528" in page.lb_rt.text(),
          "状态行写出了要下载多少 MB", page.lb_rt.text())

    # ③ 编号连续（① ② ③ ④，不能因为插了一张卡就重复/跳号）
    print("\n[3] 卡片编号")
    nums = []
    for t in (page.head_model.text(), page.head_rt.text()):
        nums.append(t[:1])
    print(f"       模型卡标题 = {page.head_model.text()[:22]}")
    print(f"       运行时标题 = {page.head_rt.text()[:22]}")
    check(page.head_rt.text().startswith("③"), "运行组件卡是 ③", page.head_rt.text())
    check(page.head_model.text().startswith("②"), "模型卡是 ②", page.head_model.text())

    # ④ 装好之后不能还说"还差一个"（与模型卡同一套规矩）
    print("\n[4] 就绪后的文案（不能自相矛盾）")
    runtime_service.RuntimeManager.check = (
        lambda self, rid, use_cache=True: runtime_service.RuntimeState(
            id=rid, name="CUDA 运行时（显卡加速）", status="ready",
            detail="2 个文件齐全")
        if rid == "cuda" else _real_check(self, rid, use_cache=use_cache))
    page._paint_runtime()
    for _ in range(3):
        app.processEvents()
    print(f"       标题  : {page.head_rt.text()}")
    print(f"       按钮  : {page.btn_rt.text()}")
    check("就绪" in page.head_rt.text(),
          "就绪后标题变成「已就绪」（不再说「还差一个」）", page.head_rt.text())
    check("必需" not in page.btn_rt.text(),
          "就绪后按钮不再是「必需」", page.btn_rt.text())
finally:
    runtime_service.RuntimeManager.check = _real_check

print("\n" + "=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
