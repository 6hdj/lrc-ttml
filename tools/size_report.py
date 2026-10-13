"""打包体积报告：把"1.82 GB 的安装包"拆成可核对的具体责任项。

    python tools/size_report.py                     # 报告 dist/Lyric2TTML
    python tools/size_report.py <onedir 目录>
    python tools/size_report.py --json out.json

为什么要这个工具（需求原文）："必须先通过体积分析找出真实的大文件，再决定
优化方案。严禁为了达成体积目标而盲目删除 DLL、Qt 插件、模型定义、CUDA 依赖
或动态导入模块。"

所以这里只做**测量**，不做判断：
* 递归统计每个一级子项的大小（`_internal/<包名>`、`_internal/nvidia/...`）；
* 按"责任归属"归类（Python 运行时 / PyTorch / CUDA / Qt / 科学计算 / 项目资源 / 模型）；
* 顺便报告**模型权重是否被打进包**（这一项最有可能是体积大头）。

⚠️ 单位统一用 MiB（1024 进制），别和 MB 混。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

MIB = 1024 * 1024
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 责任归类：名字前缀 → 归属（顺序敏感，先匹配到的算）
CATEGORIES: List[Tuple[str, str]] = [
    ("nvidia", "CUDA 运行库（cuBLAS / cuDNN / nvrtc）"),
    ("torch", "PyTorch（CAM++ 声纹用）"),
    ("PySide6", "Qt / PySide6（桌面窗口）"),
    ("shiboken6", "Qt / PySide6（桌面窗口）"),
    ("ctranslate2", "CTranslate2（Whisper 推理）"),
    ("faster_whisper", "faster-whisper"),
    ("onnxruntime", "onnxruntime"),
    ("numpy", "NumPy"),
    ("av", "PyAV（音频解码）"),
    ("tokenizers", "tokenizers"),
    ("huggingface_hub", "huggingface_hub"),
    ("hf_xet", "huggingface_hub"),
    ("models", "★ 模型权重/网络定义（不应进安装包）"),
    ("desktop", "Lyric2TTML 自身资源"),
    ("google", "protobuf"),
    ("sympy", "sympy（torch 依赖）"),
    ("functorch", "torch 附属"),
    ("torchgen", "torch 附属"),
    ("yaml", "PyYAML"),
    ("pypinyin", "pypinyin"),
    ("demucs", "Demucs（可选人声分离）"),
]


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def human(n: float) -> str:
    for u in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TiB"


def categorize(name: str) -> str:
    low = name.lower()
    for prefix, cat in CATEGORIES:
        if low.startswith(prefix.lower()):
            return cat
    return "其它"


def scan(root: str) -> Dict:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise SystemExit(f"目录不存在：{root}")

    top: List[Dict] = []
    for entry in sorted(os.listdir(root)):
        p = os.path.join(root, entry)
        if os.path.isfile(p):
            top.append({"name": entry, "size": os.path.getsize(p), "kind": "file"})
        else:
            top.append({"name": entry, "size": dir_size(p), "kind": "dir"})

    # `_internal` 下面才是真正的大头，展开它
    internal = os.path.join(root, "_internal")
    inner: List[Dict] = []
    if os.path.isdir(internal):
        for entry in sorted(os.listdir(internal)):
            p = os.path.join(internal, entry)
            if os.path.isfile(p):
                inner.append({"name": entry, "size": os.path.getsize(p),
                              "kind": "file"})
            else:
                inner.append({"name": entry, "size": dir_size(p), "kind": "dir"})

    # 归类汇总（以 _internal 为主，加程序目录里的散文件）
    by_cat: Dict[str, int] = {}
    for item in inner:
        by_cat[categorize(item["name"])] = by_cat.get(categorize(item["name"]), 0) \
            + item["size"]
    for item in top:
        if item["name"] == "_internal":
            continue
        c = categorize(item["name"])
        by_cat[c] = by_cat.get(c, 0) + item["size"]

    # Qt 细分（桌面窗口到底占多少）
    qt_detail = {}
    qdir = os.path.join(internal, "PySide6")
    if os.path.isdir(qdir):
        for sub in sorted(os.listdir(qdir)):
            p = os.path.join(qdir, sub)
            if os.path.isfile(p):
                qt_detail[f"文件:{sub}"] = os.path.getsize(p)
            else:
                qt_detail[sub] = dir_size(p)
    # CUDA 细分
    nv_detail = {}
    nvdir = os.path.join(internal, "nvidia")
    if os.path.isdir(nvdir):
        for sub in sorted(os.listdir(nvdir)):
            nv_detail[sub] = dir_size(os.path.join(nvdir, sub))

    return {
        "root": root,
        "total": dir_size(root),
        "top": sorted(top, key=lambda x: -x["size"]),
        "internal": sorted(inner, key=lambda x: -x["size"]),
        "by_category": dict(sorted(by_cat.items(), key=lambda x: -x[1])),
        "qt_detail": dict(sorted(qt_detail.items(), key=lambda x: -x[1])),
        "cuda_detail": dict(sorted(nv_detail.items(), key=lambda x: -x[1])),
    }


def print_report(rep: Dict, limit: int = 22) -> None:
    print("=" * 74)
    print(f"  打包体积报告：{rep['root']}")
    print(f"  总计：{human(rep['total'])}")
    print("=" * 74)

    print("\n【按责任归类】")
    for cat, size in rep["by_category"].items():
        pct = size * 100.0 / max(1, rep["total"])
        print(f"  {size:>12}  {pct:5.1f}%  {cat}")

    print(f"\n【_internal 里的前 {limit} 项】")
    for item in rep["internal"][:limit]:
        print(f"  {item['size']:>12}  {item['name']}"
              f"{'/' if item['kind'] == 'dir' else ''}")

    if rep["qt_detail"]:
        print("\n【Qt / PySide6 细分】")
        for k, v in list(rep["qt_detail"].items())[:14]:
            print(f"  {v:>12}  {k}")

    if rep["cuda_detail"]:
        print("\n【CUDA 运行库细分】")
        for k, v in rep["cuda_detail"].items():
            print(f"  {v:>12}  {k}")

    print("\n【程序目录一级】")
    for item in rep["top"]:
        print(f"  {item['size']:>12}  {item['name']}"
              f"{'/' if item['kind'] == 'dir' else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description="PyInstaller 产物体积报告")
    ap.add_argument("root", nargs="?", default=os.path.join(HERE, "dist",
                                                            "Lyric2TTML"))
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    ap.add_argument("--limit", type=int, default=22)
    a = ap.parse_args()
    rep = scan(a.root)
    print_report(rep, a.limit)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
        print(f"\n已写出 {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
