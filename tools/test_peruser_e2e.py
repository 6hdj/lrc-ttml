"""证明"普通用户能装"——用**真实发行 exe** 在**非管理员**进程里跑完整安装。

    python tools/test_peruser_e2e.py [dist/安装程序.exe]

## 为什么这条证据是决定性的

用户要求"普通用户双击安装不触发 UAC"。要证明它，需要同时满足：

1. **发起进程**不是管理员（`IsUserAnAdmin() == False`）——本会话实测就是；
2. **被启动的 exe** 没有要求提权 —— 如果清单要 `requireAdministrator`，
   从非管理员进程启动它会**直接失败**（Windows 拒绝创建进程，报
   "请求的操作需要提升"），根本进不到我们的代码；
3. 安装**真的跑完**（解压出主程序、能启动桌面窗口、能卸载）。

三条同时成立，「不弹 UAC」就不是"我声称"，而是**进程根本没走提权那条路**。
（UAC 弹窗是"提权请求"；非管理员进程成功启动 + 清单是 asInvoker ⇒ 没有提权请求。）

⚠️ 本测试会把软件装到 `--dest`（默认 `.tmp/peruser-e2e`），**不碰用户真实目录**，
也不建真实快捷方式（传 `--no-shortcuts`）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                   # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="非管理员下的完整安装验收")
    ap.add_argument("exe", nargs="?",
                    default=os.path.join("dist", "安装程序.exe"))
    ap.add_argument("--dest", default=os.path.join(HERE, ".tmp", "peruser-e2e"))
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    exe = a.exe if os.path.isabs(a.exe) else os.path.join(HERE, a.exe)
    print("=" * 74)
    print("  普通用户（非管理员）完整安装验收")
    print("=" * 74)
    print(f"  exe            : {exe}")
    print(f"  发起进程管理员 : {is_admin()}   ← 必须为 False 才有说服力")
    if is_admin():
        print("  ⚠ 本进程是管理员 —— 这条测试**失去意义**（无法证明普通用户可装）。")
        print("     如实标记为未验证。")
        return 0
    if not os.path.isfile(exe):
        print(f"  ✗ 找不到 {exe}")
        return 2

    dest = os.path.abspath(a.dest)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)

    # 指向工作区内的临时目录（PyInstaller onefile 解包要用）
    tmp = os.path.join(HERE, ".tmp", "pyi-e2e")
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, TMP=tmp, TEMP=tmp)

    print(f"  安装目标       : {dest}")
    print("=" * 74)
    r = subprocess.run([exe, "--e2e", dest, "--no-models", "--no-shortcuts"],
                       capture_output=True, env=env, timeout=3600,
                       cwd=HERE)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    print(out[-2600:])
    if err.strip():
        print("--- stderr ---")
        print(err[-800:])

    print("=" * 74)
    ok = r.returncode == 0
    # ⚠️⚠️ **不能断言"装完 exe 还在"**（2026-10-15 修：这是个"假绿"）。
    #
    # `--e2e` 干的是**完整闭环**：装 → 启动桌面窗口 → **卸载**（步骤 [4.6]），
    # 所以它正常返回时安装目录**本来就应该没了**。
    # 原来这里写 `exe_installed = os.path.exists(dest/Lyric2TTML.exe)` 并要求它为真
    # —— 于是这条测试**只有在"卸载没删干净"时才通过**，等于拿 bug 当成功。
    # 我修好卸载的"清理助手排不上"之后，卸载真的删干净了，
    # 这条断言立刻翻车（exit=1），暴露了它一直在测反的东西。
    #
    # 现在断言**真实结果**：装完 → 启动过 → 卸载后目录必须干净。
    # "装完能用"由 e2e 内部步骤自己证明（它会真启动桌面窗口并检查退出码）。
    #
    # ⚠️ 而且要**等**：卸载是"延迟清理"（退出后才删 —— Windows 不允许删
    # 正在运行的程序），立刻检查必然还在。最多等 25 秒。
    deadline = time.time() + 25.0
    app_gone = False
    while time.time() < deadline:
        if not os.path.exists(os.path.join(dest, "Lyric2TTML.exe")):
            app_gone = True
            break
        time.sleep(0.5)
    if app_gone:
        time.sleep(2.0)          # 让清理 bat 走完自删，别影响下一条用例
    print(f"  退出码                 : {r.returncode}")
    print(f"  卸载后安装目录已清空   : {app_gone}")
    print(f"  全程管理员权限         : {is_admin()}（False = 没有提权）")
    print("=" * 74)
    if ok and app_gone:
        print("  ✓ 非管理员进程完成「装 → 启动 → 卸载」闭环 —— 没有走提权那条路")
        print("    （若 exe 要求 requireAdministrator，这个 subprocess 会直接")
        print("      失败并报「请求的操作需要提升」，根本跑不到这里）")
    elif ok and not app_gone:
        print("  ✗ 卸载没删干净：装完的目录还在（清理助手没生效或没等到）")
    else:
        print("  ✗ 安装未完成")

    if not a.keep:
        shutil.rmtree(dest, ignore_errors=True)
    return 0 if (ok and app_gone) else 1


if __name__ == "__main__":
    sys.exit(main())
