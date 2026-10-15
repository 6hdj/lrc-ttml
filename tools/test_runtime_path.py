"""验证 `apply_runtime_path()` 选对了 sys.path 项 —— 按**真实布局**建模。

    python .tmp/test_runtime_path.py

真实布局（用户机器上实测）：
    site  = <数据目录>/runtime/torch
    包    = <site>/torch/__init__.py        ← 包在 site 下面一层
⇒ sys.path 必须加 **site**，`import torch` 才命中。
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
SB = os.path.join(HERE, ".tmp", "rtpath")
shutil.rmtree(SB, ignore_errors=True)

_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def w(path: str, text: str = "x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run_case(name: str, build, root: str) -> None:
    shutil.rmtree(root, ignore_errors=True)
    build(root)
    sys.path[:] = [p for p in sys.path if "rtpath" not in p]
    for m in [k for k in sys.modules if k == "torch"]:
        del sys.modules[m]
    from desktop import paths
    paths.runtime_root = lambda r=root: r            # type: ignore[assignment]
    added = paths.apply_runtime_path()
    print(f"  {name}：加了 {[os.path.relpath(p, root) for p in added]}")
    try:
        mod = importlib.import_module("torch")
        check(getattr(mod, "VALUE", "") == "ok",
              f"{name}：`import torch` 成功", str(added))
    except Exception as e:                                   # noqa: BLE001
        check(False, f"{name}：`import torch` 成功", f"{type(e).__name__}: {str(e)[:90]}")
    sys.path[:] = [p for p in sys.path if "rtpath" not in p]
    for m in [k for k in sys.modules if k == "torch"]:
        del sys.modules[m]


def build_real(root: str) -> None:
    """真实布局：<root>/torch/torch/__init__.py（site 下再一层才是包）"""
    w(os.path.join(root, "torch", "torch", "__init__.py"), "VALUE = 'ok'\n")
    w(os.path.join(root, "torch", "torch", "lib", "c10.dll"), "dll")


def build_double_nested(root: str) -> None:
    """防御分支：<root>/torch/torch/torch/__init__.py"""
    w(os.path.join(root, "torch", "torch", "torch", "__init__.py"), "VALUE = 'ok'\n")


print("=" * 74)
print("  apply_runtime_path：sys.path 项必须让 `import torch` 命中")
print("=" * 74)
run_case("真实布局", build_real, os.path.join(SB, "real"))
run_case("双重嵌套", build_double_nested, os.path.join(SB, "nested"))

print()
print("=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
