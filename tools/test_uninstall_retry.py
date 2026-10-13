"""卸载**能否真正删干净**的回归测试（含"第二次运行"那条死路）。

    python tools/test_uninstall_retry.py

## 为什么要有它（真踩到的 bug）

老逻辑是：`rmtree(ignore_errors=True)` 能删多少删多少，删不掉就告诉用户
"**再运行一次这个卸载程序**就干净了"。

实测这条路**走不通**，而且原因很反直觉：

* Windows 只锁**正在运行的那个 exe**，**不锁** `_internal/`（Python 运行库
  已经加载进内存，磁盘文件是可删的）；
* 于是 rmtree 把 `_internal/` 删了个精光：**384 个文件 → 剩 32 个**，
  连 `encodings` 模块都没了；
* 而冻结版 exe **必须靠 `_internal/` 才能启动** —— 用户按提示再运行一次，
  拿到的是 `Failed to start embedded python interpreter:
  Failed to import encodings module`，**永远清不干净**。
* 更糟的是日志还在说"再运行一次就没了"，等于把用户引进死路。

现在改成：把剩下的目录交给一个**退出后才跑**的清理助手（`cmd` 批处理），
既不自残、也能真正删干净。本测试就验这两点。
"""
from __future__ import annotations

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


def fake_install(dest: str) -> None:
    """造一个"像被占用的安装目录"：有一个 exe + 一堆可删的文件。"""
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.join(dest, "_internal", "encodings"), exist_ok=True)
    for i in range(20):
        with open(os.path.join(dest, "_internal", f"f{i}.pyd"), "w") as fh:
            fh.write("x")
    with open(os.path.join(dest, "_internal", "encodings", "__init__.py"),
              "w") as fh:
        fh.write("x")
    with open(os.path.join(dest, "Lyric2TTML.exe"), "w") as fh:
        fh.write("MZ fake")


def main() -> int:
    print("=" * 74)
    print("  卸载清理 回归测试")
    print("=" * 74)

    tmp = os.path.join(HERE, ".tmp", "uninst-retry")
    dest = os.path.join(tmp, "app")
    os.makedirs(tmp, exist_ok=True)
    fake_install(dest)

    print(f"\n[1] 在子进程里调用 uninstall（模拟 exe 自己卸载自己）")
    # 用子进程：uninstall 会 os.chdir，而且我们要它真正退出
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import uninstall_core as g;"
        "sys.exit(g.uninstall(r'%s', keep_models=True, assume_yes=True))"
        % (HERE, dest)
    )
    env = dict(os.environ)
    env["TEMP"] = tmp                      # 清理脚本写到沙箱，别散在系统 TEMP
    env["TMP"] = tmp
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       env=env, cwd=tmp, timeout=300)
    out = (r.stdout or b"").decode("utf-8", "replace")
    print("--- 卸载输出 ---")
    for ln in out.strip().split("\n"):
        print(f"    {ln}")
    print("--- 结束 ---")

    check(r.returncode == 0, "卸载返回 0（成功）", f"实际 {r.returncode}")
    check("再运行一次" not in out,
          "**不再**骗用户「再运行一次就干净了」（那条路走不通）",
          "日志里还在说再运行一次")

    print(f"\n[2] 等清理助手把目录删干净（最多 20 秒）")
    gone = False
    for _ in range(40):
        if not os.path.isdir(dest):
            gone = True
            break
        time.sleep(0.5)
    check(gone, "安装目录已被清理助手**真正删除**",
          f"仍在：{dest}")

    # 关键：如果没删干净，`_internal` 必须仍然完好（否则用户没法重试）
    if not gone and os.path.isdir(dest):
        enc = os.path.join(dest, "_internal", "encodings", "__init__.py")
        check(os.path.exists(enc),
              "没删干净时 `_internal/encodings` **仍然完好**（用户还能重试）",
              "运行库被删 → 用户按提示重试会 'Failed to import encodings module'")

    print(f"\n[3] 清理脚本是否自删（不留垃圾）")
    leftovers = [f for f in os.listdir(tmp) if f.startswith("l2t-cleanup-")]
    logs = [f for f in leftovers if f.endswith(".log")]
    if logs:
        try:
            content = open(os.path.join(tmp, logs[0]), encoding="gbk",
                           errors="replace").read().strip()
            print(f"    清理脚本日志：{content}")
        except OSError:
            pass
    time.sleep(1.5)
    leftovers = [f for f in os.listdir(tmp) if f.startswith("l2t-cleanup-")]
    check(not [f for f in leftovers if f.endswith(".bat")],
          "清理用 .bat 已自删", f"残留：{leftovers}")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
