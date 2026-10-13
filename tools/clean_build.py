"""可靠地清掉构建产物 / 测试残留（Windows 上删不掉的目录用 robocopy 镜像清空）。

    python tools/clean_build.py --probe
    python tools/clean_build.py                 # 清 build/ 与 dist/_*（保留 dist/ 里的交付物）
    python tools/clean_build.py --dist _qttest2 # 只清某一个
    python tools/clean_build.py --all-dist      # 连 dist/ 一起清（谨慎）

## 为什么需要它（真踩过，而且**卡住了整条构建流水线**）

PyInstaller 开始构建前会 `shutil.rmtree(distpath)`。而上一次跑测试时，
主程序会把它自己的模型缓存写到 **`<dist>/Lyric2TTML/.hf_cache/`** 里；
中断的下载会留下 `tmp*` 目录和**只读属性**文件，于是：

    PermissionError: [WinError 5] 拒绝访问。
      ... \\dist\\_qttest2\\Lyric2TTML\\.hf_cache\\...\\tmp02f43kpq
    → PyInstaller 直接失败

`shutil.rmtree(ignore_errors=True)` 也救不了（属性是只读、且有句柄）。
可用的办法是两步：
  ① 递归清掉只读/隐藏/系统属性；
  ② 用 `robocopy <空目录> <目标> /MIR` 把目标**镜像成空**（比 rmtree 强得多），
     再删掉空壳。
这个脚本就是干这个的，`--probe` 只报告不删。
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def human(n: float) -> str:
    for u in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TiB"


def dir_size(p: str) -> int:
    total = 0
    for root, _d, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clear_attributes(path: str) -> int:
    """递归清掉只读/隐藏/系统属性（删不掉的首要原因）。"""
    n = 0
    for root, dirs, files in os.walk(path):
        for name in files + dirs:
            p = os.path.join(root, name)
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                n += 1
            except OSError:
                pass
    return n


def mirror_delete(path: str, log=print) -> bool:
    """用 `robocopy /MIR` 把 `path` 清空，然后删掉空壳。返回是否删掉。"""
    if not os.path.exists(path):
        return True
    empty = tempfile.mkdtemp(prefix="l2t-empty-")
    try:
        try:
            subprocess.run(["robocopy", empty, path, "/MIR", "/R:1", "/W:1",
                            "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                           capture_output=True, timeout=600)
        except Exception as e:                          # noqa: BLE001
            log(f"    robocopy 失败：{type(e).__name__}: {e}")
        # 镜像之后目录空了，但空目录壳还在；逐个删
        for root, dirs, _files in os.walk(path, topdown=False):
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(path)
        except OSError:
            pass
        if not os.path.exists(path):
            return True
        # 还不行就用 shutil 再补一刀（这时属性已经清过了）
        shutil.rmtree(path, ignore_errors=True)
        return not os.path.exists(path)
    finally:
        shutil.rmtree(empty, ignore_errors=True)


def clean_one(path: str, log=print, dry: bool = False) -> bool:
    if not os.path.exists(path):
        return True
    size = dir_size(path) if os.path.isdir(path) else 0
    log(f"  清理 {os.path.relpath(path, HERE)}  ({human(size)})")
    if dry:
        return False
    clear_attributes(path) if os.path.isdir(path) else None
    ok = mirror_delete(path, log=log)
    if not ok:
        log(f"    ⚠️ 没删干净，可能被进程占用：{path}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="清理构建产物与测试残留")
    ap.add_argument("--probe", action="store_true", help="只报告，不删")
    ap.add_argument("--dist", default="", help="只清 dist 下的某个子目录名")
    ap.add_argument("--all-dist", action="store_true",
                    help="连 dist/ 整个清掉（会删掉交付物，谨慎）")
    a = ap.parse_args()

    targets: list = []
    build = os.path.join(HERE, "build")
    dist = os.path.join(HERE, "dist")

    if a.dist:
        targets.append(os.path.join(dist, a.dist))
    elif a.all_dist:
        targets += [build, dist]
    else:
        targets.append(build)
        # dist 下的**中间产物**（`_` 开头）+ 已放置的主程序目录里的运行时残留
        if os.path.isdir(dist):
            for name in sorted(os.listdir(dist)):
                if name.startswith("_"):
                    targets.append(os.path.join(dist, name))
        # ⚠️ 交付物目录里的测试残留：这些是"不该进安装包"的东西，
        # 而且会**直接卡住下一次构建**（PyInstaller 删不掉它们）。
        for app in ("Lyric2TTML",):
            for residue in (".hf_cache", "outputs", "uploads", ".accept-data",
                            ".accept-out", "logs"):
                p = os.path.join(dist, app, residue)
                if os.path.exists(p):
                    targets.append(p)

    print("=" * 66)
    print("  清理构建产物与测试残留" + ("（--probe 只报告）" if a.probe else ""))
    print("=" * 66)
    if not targets:
        print("  没有需要清理的")
        return 0

    failed = []
    for t in targets:
        if not clean_one(t, dry=a.probe):
            if not a.probe:
                failed.append(t)
    print()
    if a.probe:
        print("（--probe：什么都没删）")
        return 0
    if failed:
        print(f"⚠️ {len(failed)} 个没删干净：")
        for f in failed:
            print(f"   {f}")
        print("  多半是被占用（关掉正在跑的程序/资源管理器窗口再试）")
        return 1
    print("清理完成 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
