"""验证 gpu_probe 的两个修复（打桩模拟"打包版没有 torch"）。

    python .tmp/test_gpu_probe_fix.py
"""
import builtins
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from desktop.services import gpu_probe as g  # noqa: E402

_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


print("=" * 74)
print("  gpu_probe：打包版没有 torch 时，不能把显卡判成不可用")
print("=" * 74)

# ---- 打桩：让 import torch 失败（模拟打包版）----
_real_import = builtins.__import__


def _no_torch(name, *a, **kw):
    if name == "torch" or name.startswith("torch."):
        raise ModuleNotFoundError("No module named 'torch._C'")
    return _real_import(name, *a, **kw)


builtins.__import__ = _no_torch
# 清掉可能已缓存的 torch
for k in [k for k in sys.modules if k == "torch" or k.startswith("torch.")]:
    del sys.modules[k]
try:
    info = g.probe()
finally:
    builtins.__import__ = _real_import

print(f"  ok={info.get('ok')}  reason={info.get('reason')!r}  "
      f"gpu={info.get('gpu_name')!r}  driver={info.get('driver')!r}")
check(info.get("ok") is True,
      "没有 torch 时，**显卡仍然判为可用**（原来判 ❌）",
      f"ok={info.get('ok')} error={info.get('error')!r}")
check(info.get("reason") == "no_torch", "理由记为 no_torch（供上层区分）")
check(not info.get("error"),
      "**不把它当错误**（原来 error='torch 无法导入…' 会被渲染成红字）",
      str(info.get("error")))
check(bool(info.get("note")), "给出一条说明（PyTorch 是可选的）",
      str(info.get("note")))
check("RTX" in (info.get("gpu_name") or "").upper()
      or bool(info.get("gpu_name")),
      "显卡型号改从 nvidia-smi 拿到（不再依赖 torch）",
      str(info.get("gpu_name")))
check(bool(info.get("driver")), "驱动版本也拿到了", str(info.get("driver")))

print()
print("  --- cuDNN 可选：缺了也不能判失败 ---")
_real_find = g._find_dll


def _no_cudnn(names):
    if any("cudnn" in n for n in names):
        return None
    return _real_find(names)


g._find_dll = _no_cudnn
try:
    libs = g.cuda_libs()
finally:
    g._find_dll = _real_find
print(f"  ok={libs['ok']}  detail={libs['detail']}")
check(libs["ok"] is True, "缺 cuDNN 时 ok 仍为 True（原来判 False）",
      str(libs))
check("可选" in libs["detail"], "说明里写清 cuDNN 是「可选」", libs["detail"])
check("重新跑安装程序" not in (libs.get("fix") or ""),
      "不再让用户「重跑安装程序补全」（安装程序早就不下 cuDNN）",
      str(libs.get("fix")))


def _no_cublas(names):
    if any("cublas" in n for n in names):
        return None
    return _real_find(names)


g._find_dll = _no_cublas
try:
    bad = g.cuda_libs()
finally:
    g._find_dll = _real_find
print(f"  缺 cuBLAS → ok={bad['ok']}  detail={bad['detail']}")
check(bad["ok"] is False, "**缺 cuBLAS 仍然要判失败**（它是硬要求）",
      str(bad))

print()
print("=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
