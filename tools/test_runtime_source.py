"""验证：CUDA 可用时（无论来自哪）运行组件必须判 ready，不再让人白下 529 MB。

    python tools/test_runtime_source.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("L2T_DATA_DIR", os.path.join(HERE, ".tmp", "rt-source"))

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


print("=" * 74)
print("  运行组件状态必须认「不需要下载的那些来源」")
print("=" * 74)

libs = gpu_probe.cuda_libs()
mgr = runtime_service.RuntimeManager()
st = mgr.check("cuda")
print(f"  cuda_libs()  → ok={libs['ok']}  {libs['detail'][:60]}")
print(f"  check(cuda)  → {st.status}  {st.detail[:60]}")

# 声明必须存在，否则上面那条修复不会生效
spec = mgr.spec("cuda")
check(spec.get("verify_dll_probe") == "cuda",
      "清单声明了 verify_dll_probe=cuda（修复才生效）")

# 核心断言：DLL 能加载 ⇒ 不能判"未安装"
if libs["ok"]:
    check(st.status == "ready",
          "CUDA 实际可用时，运行组件判为 ready（否则会引导用户白下 529 MB）",
          f"status={st.status}")
    # ⚠️ 别用 `"下载" not in detail` —— 就绪文案里本来就有
    # "不是本程序**下载**的"，子串匹配会把自己人判成失败（真踩到）。
    # 只在**开头**匹配"需要下载"这个真正表示"要下东西"的句式。
    check(not (st.detail or "").startswith("需要下载"),
          "就绪时的说明不是「需要下载…」", st.detail[:60])
else:
    print("  （本机 CUDA 不可用，跳过'必须 ready'的断言）")

# 反向：把探测打桩成"不可用"，必须回到未安装（修复不能把判据变成永真）
import desktop.services.gpu_probe as gp  # noqa: E402
_real = gp.cuda_libs
try:
    gp.cuda_libs = lambda: {"ok": False, "detail": "打桩：不可用"}
    mgr.invalidate()
    st2 = mgr.check("cuda")
    check(st2.status != "ready",
          "**阴性对照**：DLL 加载不了时不能判 ready", f"status={st2.status}")
finally:
    gp.cuda_libs = _real
    mgr.invalidate()

print("\n" + "=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
