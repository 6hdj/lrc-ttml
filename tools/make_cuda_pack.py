"""把 CUDA 运行时打包成**可分发的一个 zip**（供按需下载）。

    python tools/make_cuda_pack.py                    # 打包并打印 SHA256/大小
    python tools/make_cuda_pack.py --out dist/cuda-runtime-win-x64-cu12.zip

## 为什么需要它

第 17 轮实测（`docs/商业化重构审计与体积报告.md` §3.6）：

| | 含 CUDA | 不含 CUDA |
| --- | --- | --- |
| 主程序目录 | 1078.2 MB | **342.4 MB** |
| 单文件安装程序 | 717.7 MB | **189.9 MB** |

但**不含 CUDA 就根本跑不了**（`asr.py` 明确拒绝 CPU，不是"慢跑"）。
用户已选**方案 D：GPU 运行时按需下载** —— 主包降到 ~190 MB，
首次使用（或首次转换）时把 CUDA 运行时下回来。

## 这个脚本产出什么

把 `.venvpack` 里 `nvidia/cublas/bin` 的三个 DLL 打成一个 zip（`deflate`），
并打印 **SHA256 + 字节数** —— 这两个值要填进
`desktop/resources/runtime_manifest.json` 的 `cuda` 组件里，
下载后按它们校验（复用 `model_manager` 同一套校验逻辑）。

## ⚠️ 必须知道的两件事

1. **zip 里的目录结构是 `bin/xxx.dll`**，解压到 `<data_dir>/runtime/cuda/`
   之后就是 `<data_dir>/runtime/cuda/bin/*.dll` —— 正是
   `gpu_probe.cuda_dirs()` 会去找的位置（源码与打包版同一口径）。
2. **这个 zip 需要有人托管**（本项目无服务器）。最省事的做法是挂到
   GitHub Release 上，把 release 资源 URL 填进清单的 `urls`。
   **没有 URL 时程序不能假装能下载** —— 体检要如实说
   "CUDA 运行时未安装，且当前没有配置下载地址"。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_cublas_bin() -> str:
    """定位打包环境里的 `nvidia/cublas/bin`。"""
    for base in (os.path.join(HERE, ".venvpack", "Lib", "site-packages"),
                 os.path.join(HERE, ".venv", "Lib", "site-packages")):
        p = os.path.join(base, "nvidia", "cublas", "bin")
        if os.path.isdir(p):
            return p
    raise SystemExit("找不到 nvidia/cublas/bin（先确认 .venvpack 里装了 nvidia-cublas-cu12）")


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 CUDA 运行时 zip")
    ap.add_argument("--out", default=os.path.join(HERE, "dist",
                                                  "cuda-runtime-win-x64-cu12.zip"))
    a = ap.parse_args()

    src = find_cublas_bin()
    dlls = sorted(f for f in os.listdir(src) if f.lower().endswith(".dll"))
    print("=" * 72)
    print("  CUDA 运行时打包（供按需下载）")
    print("=" * 72)
    print(f"  来源: {os.path.relpath(src, HERE)}")
    total = sum(os.path.getsize(os.path.join(src, f)) for f in dlls)
    for f in dlls:
        print(f"    {os.path.getsize(os.path.join(src, f)) / 1048576:8.1f} MB  {f}")
    print(f"  未压缩合计: {total / 1048576:.1f} MB")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    # ⚠️ 目录结构固定为 `bin/<dll>` —— 解压到 <data_dir>/runtime/cuda/ 即可
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in dlls:
            z.write(os.path.join(src, f), arcname=os.path.join("bin", f))
    size = os.path.getsize(a.out)
    digest = sha256_file(a.out)

    print()
    print(f"  产物  : {os.path.relpath(a.out, HERE)}")
    print(f"  大小  : {size} 字节（{size / 1048576:.1f} MB）"
          f"  压缩比 {size / total * 100:.1f}%")
    print(f"  SHA256: {digest}")
    print()
    print("  把下面两项填进 desktop/resources/runtime_manifest.json 的 cuda 组件：")
    print(f'      "size": {size},')
    print(f'      "sha256": "{digest}"')
    print()
    print("  ⚠️ 还需要一个真实的下载 URL（本项目无服务器）：")
    print("     最省事是挂到 GitHub Release，把资源 URL 填进 \"urls\"。")
    print("     没有 URL 时**不要**编一个 —— 程序会如实报「未配置下载地址」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
