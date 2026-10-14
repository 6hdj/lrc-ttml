"""守「GPU 运行时按需下载」（方案 D）这条链路。

    python tools/test_cuda_runtime.py

## 背景

第 17 轮实测：含 CUDA 的安装包 717.7 MB，不含 189.9 MB —— 但**不含就跑不了**
（`asr.py` 明确拒绝 CPU）。用户选了 **方案 D**：主包不带 CUDA，改成按需下载。
本测试守三件事，都是**真跑**（不下载 3GB，用本地文件冒充远端）：

1. **清单里确实有 cuda 组件**，且 SHA256/大小与 `tools/make_cuda_pack.py`
   产出的 zip **逐字节对得上**（对不上 = 用户会下到一个校验失败的包）；
2. **解压后的目录布局正确**：`<runtime>/cuda/bin/*.dll` —— 正是
   `gpu_probe.cuda_dirs()` 会去找的位置（源码与打包版同一口径）；
3. **没配下载地址时如实报错**，不能假装能下（本项目无服务器）。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import zipfile

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


def sha256_file(p: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    print("=" * 74)
    print("  GPU 运行时按需下载（方案 D）")
    print("=" * 74)

    man = os.path.join(HERE, "desktop", "resources", "runtime_manifest.json")
    d = json.load(io.open(man, encoding="utf-8"))
    ids = [r["id"] for r in d["runtimes"]]
    print(f"\n[1] 清单")
    print(f"       组件: {ids}")
    check("cuda" in ids, "清单里有 cuda 运行时组件")
    spec = next((r for r in d["runtimes"] if r["id"] == "cuda"), None)
    if not spec:
        print("\n  ✗ 没有 cuda 组件，后面没法测")
        return 1
    check("archives" in spec or spec.get("wheels"),
          "cuda 组件声明了分发形态（archives）")
    check(bool(spec.get("verify_files")),
          "声明了 verify_files（CUDA 不是 python 包，不能靠 import 验）")

    # ---- 2. 与真实 zip 对账 ----
    print("\n[2] 与 tools/make_cuda_pack.py 的产物对账")
    z = os.path.join(HERE, "dist", "cuda-runtime-win-x64-cu12.zip")
    if not os.path.isfile(z):
        print("       （跳过：先跑 python tools/make_cuda_pack.py 生成 zip）")
    else:
        a = (spec.get("archives") or [{}])[0]
        real_size = os.path.getsize(z)
        real_sha = sha256_file(z)
        check(int(a.get("size") or 0) == real_size,
              f"清单里的 size 与真实 zip 一致（{real_size} 字节）",
              f"清单 {a.get('size')}")
        check(str(a.get("sha256", "")).lower() == real_sha,
              "清单里的 sha256 与真实 zip 一致",
              f"清单 {str(a.get('sha256'))[:16]}… 实际 {real_sha[:16]}…")
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
        check(all(n.replace("\\", "/").startswith("bin/") for n in names),
              "zip 内部结构是 `bin/*.dll`",
              f"{names[:3]}")

    # ---- 3. 解压到运行时目录后，cuda_dirs() 必须能找到 ----
    print("\n[3] 安装布局（用本地 zip 冒充下载结果）")
    sandbox = os.path.join(HERE, ".tmp", "cudart-test")
    shutil.rmtree(sandbox, ignore_errors=True)
    os.makedirs(sandbox, exist_ok=True)
    site = os.path.join(sandbox, "runtime", "cuda")
    os.makedirs(site, exist_ok=True)
    if os.path.isfile(z):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(site)
        check(os.path.isfile(os.path.join(site, "bin", "cublas64_12.dll")),
              "解压后有 bin/cublas64_12.dll")

    # 用 runtime_service 的**文件验证**函数真跑一次
    from desktop.services import runtime_service as rs
    ok2, why = rs._verify_files_present(spec, site)
    check(ok2, f"_verify_files_present 判为合格（{why}）")
    # 负面对照：删掉一个必需文件必须判不合格
    victim = os.path.join(site, "bin", "cublasLt64_12.dll")
    keep = None
    if os.path.isfile(victim):
        keep = victim + ".bak"
        os.replace(victim, keep)
        ok3, why3 = rs._verify_files_present(spec, site)
        check(not ok3, "**阴性对照**：删掉 cublasLt 必须判不合格", why3)
        os.replace(keep, victim)
    else:
        print("       （跳过阴性对照：zip 不在）")

    # 让 cuda_dirs() 看到这个沙箱
    print("\n[4] gpu_probe.cuda_dirs() 能找到运行时目录")
    from desktop import paths as _paths
    old = os.environ.get("L2T_DATA_DIR")
    os.environ["L2T_DATA_DIR"] = sandbox
    try:
        from desktop.services import gpu_probe
        dirs = [d.replace("\\", "/").lower() for d in gpu_probe.cuda_dirs()]
        hit = any(d.endswith("runtime/cuda/bin") for d in dirs)
        check(hit, "cuda_dirs() 包含 <data_dir>/runtime/cuda/bin",
              f"现有: {[d.split('/')[-3:] for d in dirs][:4]}")
    finally:
        if old is None:
            os.environ.pop("L2T_DATA_DIR", None)
        else:
            os.environ["L2T_DATA_DIR"] = old

    # ---- 5. 没配下载地址时必须如实报错 ----
    print("\n[5] 没配下载地址时的行为（不能假装能下）")
    urls = (spec.get("archives") or [{}])[0].get("urls") or []
    if urls:
        note = f"清单里已配置 {len(urls)} 个下载地址"
        check(True, note)
    else:
        # 真跑一次 download，必须抛"没有下载地址"而不是别的
        mgr = rs.RuntimeManager(manifest=d)
        mgr._root_override = os.path.join(sandbox, "rt")
        try:
            mgr.download("cuda", log=lambda m: None)
            check(False, "没配 URL 时下载必须失败（而不是假装成功）")
        except Exception as e:                          # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            check("没有下载地址" in msg or "urls" in msg,
                  "没配 URL 时明确报「没有下载地址」", msg[:150])
            print(f"       报错原文: {msg[:130]}")

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    if not (spec.get("archives") or [{}])[0].get("urls"):
        print("  ⚠️ CUDA zip **还没有托管地址**：")
        print("     把 dist/cuda-runtime-win-x64-cu12.zip 挂到 GitHub Release，")
        print("     再把资源 URL 填进清单的 urls —— 否则用户下不了。")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
