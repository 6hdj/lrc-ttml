"""验证「点开始制作时拦住缺 GPU 运行时」的逻辑（含**两种不能拦错**的情况）。

    python .tmp/test_gpu_guard.py

要验三件事：
  1. 有 N 卡 + CUDA 运行时就绪 → **不拦**（不能把能用的环境挡住）；
  2. 有 N 卡 + 运行时未装       → **拦**，且文案指向"模型页 → 运行组件"；
  3. 没有 N 卡                  → **拦**，但文案是"需要 N 卡"，
                                  **不是**让人去下 529 MB（白下）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["L2T_HEADLESS"] = "1"
os.environ.setdefault("L2T_DATA_DIR", os.path.join(HERE, ".tmp", "gpuguard"))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication([])

from desktop.context import AppContext  # noqa: E402
from desktop.pages.home import HomePage  # noqa: E402
from desktop.services import gpu_probe, runtime_service  # noqa: E402

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


page = HomePage(AppContext())
page.ensure_built()

real_has = gpu_probe.has_nvidia_gpu
real_check = runtime_service.RuntimeManager.check

print("=" * 72)
print("  「开始制作」的 GPU 运行时前置检查")
print("=" * 72)

# ---- 情况 1：有卡 + 运行时 ready → 不拦 ----
gpu_probe.has_nvidia_gpu = lambda: True
runtime_service.RuntimeManager.check = lambda self, rid, use_cache=True: \
    runtime_service.RuntimeState(id=rid, name="CUDA", status="ready")
msg = page._gpu_runtime_problem()
check(msg == "", "有卡 + 运行时已就绪 → 不拦", f"却报了：{msg}")

# ---- 情况 2：有卡 + 未安装 → 拦，且指向模型页 ----
runtime_service.RuntimeManager.check = lambda self, rid, use_cache=True: \
    runtime_service.RuntimeState(id=rid, name="CUDA", status="not_installed",
                                 detail="需要下载 528.8MB")
msg = page._gpu_runtime_problem()
check(bool(msg), "有卡 + 运行时未装 → 拦")
check("模型" in msg and "运行组件" in msg, "文案指向「模型」页的「运行组件」", msg)
check("CUDA" in msg, "文案点名 CUDA")
print(f"       文案：{msg}")

# ---- 情况 3：没有 N 卡 → 拦，但不说"去下载" ----
gpu_probe.has_nvidia_gpu = lambda: False
msg = page._gpu_runtime_problem()
check(bool(msg), "没有 N 卡 → 拦")
check("NVIDIA 显卡" in msg, "文案说明需要 NVIDIA 显卡", msg)
check("运行组件" not in msg and "下载并安装" not in msg,
      "**不**让没卡的用户去下运行时（白下 529 MB）", msg)
print(f"       文案：{msg}")

# ---- 情况 4：清单里没有 cuda 组件 → 不拦 ----
gpu_probe.has_nvidia_gpu = lambda: True
_real_all = runtime_service.RuntimeManager.all_specs
runtime_service.RuntimeManager.all_specs = lambda self: [{"id": "torch"}]
msg = page._gpu_runtime_problem()
check(msg == "", "清单里没有 cuda 组件 → 不拦（不把能用的环境挡死）", msg)
runtime_service.RuntimeManager.all_specs = _real_all

# ---- 情况 5：检查本身抛异常 → 不拦 ----
def _boom(self, rid, use_cache=True):
    raise RuntimeError("模拟检查器坏掉")
runtime_service.RuntimeManager.check = _boom
msg = page._gpu_runtime_problem()
check(msg == "", "检查器抛异常 → 不拦（宁可让转换去试）", msg)

gpu_probe.has_nvidia_gpu = real_has
runtime_service.RuntimeManager.check = real_check

print("\n" + "=" * 72)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 72)
sys.exit(1 if _fail else 0)
