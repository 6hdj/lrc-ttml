"""**真实卸载流程**的可重试性测试（用真安装目录 + 真 exe）。

    python tools/test_uninstall_real.py [--exe dist/安装程序.exe]

## 要回答的问题

`--e2e` 的卸载检查一直说"残留 32 个文件，按提示再运行一次即可"。
**这句话从来没被验证过。** 本测试真去做一次"再运行一次"，看会发生什么。

## 为什么它可能不成立（真踩到的机理）

卸载进程就是 `<安装目录>\\Lyric2TTML.exe` 起的一个子进程，于是：

* Windows **只锁正在运行的那个 exe**，不锁 `_internal/`（Python 运行库已加载进
  内存，磁盘文件是可删的）；
* 老的 `uninstall` 把 `_internal/` 删了个精光（实测 **384 → 32 个文件**，
  `encodings` 都没了）；
* 而冻结版 exe **必须靠 `_internal/` 才能启动** ⇒ 用户"再运行一次"拿到的是
  `Failed to import encodings module`。**老提示是错的**。

现在 `uninstall_core` 保留 exe 与 `_internal/`、其余照删，并把最终收尾交给
"退出后运行"的清理助手。本测试验证：① 运行库仍在（还能重试）；
② 第二次运行不再崩；③ 目录最终消失。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

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


def n_files(root: str) -> int:
    if not os.path.isdir(root):
        return 0
    return sum(len(fs) for _r, _d, fs in os.walk(root))


def runtime_intact(dest: str) -> bool:
    """冻结版能不能再启动：看运行库在不在。

    ⚠️ `encodings` 可能被打包进 exe 而不是放 `_internal`（取决于 PyInstaller
    版本与 `--onefile`），所以判据不能只认 `encodings` 目录 ——
    改成"`_internal` 里还有没有 Python 运行库文件"。
    """
    internal = os.path.join(dest, "_internal")
    if not os.path.isdir(internal):
        return False
    if os.path.isdir(os.path.join(internal, "encodings")):
        return True
    # 退一步：还有 .pyd / python*.dll 就说明运行库大体还在
    for _r, _d, fs in os.walk(internal):
        for f in fs:
            low = f.lower()
            if low.endswith(".pyd") or low.startswith("python"):
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="真实卸载可重试性测试")
    ap.add_argument("--exe", default=os.path.join("dist", "安装程序.exe"))
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    exe = a.exe if os.path.isabs(a.exe) else os.path.join(HERE, a.exe)
    if not os.path.isfile(exe):
        print(f"找不到 {exe}")
        return 2

    print("=" * 74)
    print("  真实卸载流程：可重试性")
    print("=" * 74)

    tmp = os.path.join(HERE, ".tmp", "uninst-real")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    dest = os.path.join(tmp, "app")
    # ⚠️ 把数据目录也指到沙箱：否则卸载时"搬模型"会去写用户真实的
    # `%LOCALAPPDATA%\\Lyric2TTML`，在受限环境里直接 `WinError 5`，
    # 卸载会**在动任何文件之前就中止**（那样测的就不是卸载逻辑了）。
    env = dict(os.environ, TMP=tmp, TEMP=tmp,
               LOCALAPPDATA=os.path.join(tmp, "local"),
               PYTHONIOENCODING="utf-8")

    print("\n[1] 用真安装程序装一份（--no-uninstall：只装不卸）")
    r = subprocess.run([exe, "--e2e", dest, "--no-models", "--no-shortcuts",
                        "--no-uninstall"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=HERE, timeout=3600)
    n0 = n_files(dest)
    print(f"    装好：{n0} 个文件")
    check(n0 > 300, "安装成功（文件数 > 300，是真载荷）", f"实际 {n0}")
    check(runtime_intact(dest), "安装后运行库完好")

    exe_in = os.path.join(dest, "Lyric2TTML.exe")
    print("\n[2] 第一次卸载（模拟用户双击卸载程序）")
    r1 = subprocess.run([exe_in, "--uninstall", dest, "--yes"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=env, cwd=tmp, timeout=900)
    out1 = (r1.stdout or "") + (r1.stderr or "")
    print(f"    退出码 {r1.returncode}")
    for ln in out1.strip().split("\n")[-8:]:
        print(f"      {ln}")
    check("再运行一次" not in out1 or "清理助手" in out1,
          "不再出现「再运行一次这个卸载程序」这种错误引导")

    for _ in range(60):
        if not os.path.isdir(dest):
            break
        time.sleep(0.5)
    gone = not os.path.isdir(dest)
    print(f"    清理后：{'目录已删除' if gone else f'仍在（{n_files(dest)} 个文件）'}")

    if gone:
        check(True, "清理助手把安装目录**彻底删除**")
    else:
        # 没删干净 → 用户必须还能重试
        check(runtime_intact(dest),
              "没删干净时**运行库仍然完好**（exe 还能启动、能重试）",
              "运行库被删 ⇒ 用户重试会 'Failed to import encodings module'")
        if os.path.isfile(exe_in):
            r2 = subprocess.run([exe_in, "--uninstall", dest, "--yes"],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                env=env, cwd=tmp, timeout=900)
            both = (r2.stdout or "") + (r2.stderr or "")
            check("Failed to import encodings" not in both,
                  "第二次运行**没有**因运行库缺失而崩",
                  both[:200])
            for _ in range(60):
                if not os.path.isdir(dest):
                    break
                time.sleep(0.5)
            check(not os.path.isdir(dest), "第二次运行后目录消失")

    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
