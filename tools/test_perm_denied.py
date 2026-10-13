"""**权限不足路径**的真实测试（用户要求："不能只在界面上声称支持"）。

    python tools/test_perm_denied.py

## 背景（一个必须先说清楚的事实）

本会话进程实测 `IsUserAnAdmin() == False`，即**普通用户**；
`C:\\Program Files` 在本会话**不可写**。这意味着：

* **能验证**：普通用户装到用户目录（已由 `--e2e` 跑通，见 `tools/test_peruser.py`）；
* **能验证**：安装器被指向**写不进去的目录**时的行为 —— 这恰好就是
  "用户选了 Program Files 但没提权"那条路径的**失败面**；
* **不能验证**：真正**提权后装进 Program Files** 的全流程（本会话无法提权）。

用户明确要求"必须验证安装器在这种启动方式下确实能正确处理目标路径和权限”。
提权成功的那一半我做不到；但**失败的一半能真做**，而且它才是用户最常遇到的
（用户不会一开始就右键提权，而是被拒绝后才知道）。所以本测试聚焦：

1. 指向不可写目录时**不能崩**（不能甩 traceback）；
2. 必须给出**人话**的失败原因；
3. 必须告诉用户**两条可执行的路**：换个位置，或**以管理员身份运行**；
4. 不能留下半成品或假成功。
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
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


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                   # noqa: BLE001
        return False


def writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".l2t-probe")
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except Exception:                                   # noqa: BLE001
        return False


def make_payload(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    top = "Lyric2TTML/"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(top + "Lyric2TTML.exe", "MZ fake")
        zf.writestr(top + "a.txt", "x")
    return path


def run_installer_at(dest: str, zip_path: str):
    """在 `dest` 上跑一次 extract_app，返回 (ok, 日志行列表)。"""
    import tkinter as tk
    import installer_gui as ig

    if "--no-shortcuts" not in sys.argv:
        sys.argv.append("--no-shortcuts")
    root = tk.Tk()
    root.withdraw()
    ig.payload_zip = lambda: zip_path
    app = ig.Installer(root, autostart=False)
    app.install_dir = dest
    app._app_ready = False
    lines = []
    app.say = lambda m="": lines.append(str(m))
    app.set_sub = lambda t, c=None: None
    app._pump_now()
    try:
        ok = app.extract_app()
    finally:
        root.destroy()
    return ok, lines


def main() -> int:
    admin = is_admin()
    print("=" * 74)
    print("  权限不足路径 真实测试")
    print(f"  当前进程是否管理员：{admin}")
    print("=" * 74)

    tmp = os.path.join(HERE, ".tmp", "perm-test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    z = make_payload(os.path.join(tmp, "p.zip"))

    # ---- 找一个**当前用户确实写不进去**的目录 ----
    print("\n[0] 找一个不可写的目标目录")
    print(f"       （本会话管理员={admin}；普通用户下 C:\\Program Files 应不可写）")
    candidates = [r"C:\Program Files\Lyric2TTML-test",
                  r"C:\Program Files (x86)\Lyric2TTML-test",
                  r"C:\Windows\System32\Lyric2TTML-test"]
    dest = ""
    for c in candidates:
        if not writable(c):
            dest = c
            print(f"       不可写：{c}")
            break
        print(f"       竟然可写（跳过）：{c}")

    if not dest:
        print("\n  ⚠ 找不到不可写目录（当前进程权限太高）—— "
              "本机无法验证这条路径，如实标记为未验证。")
        print("\n" + "=" * 74)
        print("  通过 0 项，未验证 1 项（环境限制，非产品问题）")
        print("=" * 74)
        return 0

    # ---- 指向不可写目录安装 ----
    print(f"\n[1] 把安装目标指向不可写目录")
    ok, lines = run_installer_at(dest, z)
    for ln in lines:
        print(f"       | {ln}")
    check(ok is False, "安装**没有假成功**（返回 False）")
    joined = "\n".join(lines)
    check("Traceback" not in joined and "Traceback" not in joined,
          "没有把 traceback 甩给用户")
    check(any(k in joined for k in ("装不了", "无法创建", "拒绝访问",
                                    "PermissionError", "不能写")),
          "给出了人话的失败原因")
    # 用户要求的两条可执行路径
    has_alt = ("浏览" in joined or "换个" in joined or "其它" in joined
               or "手动新建" in joined or "①" in joined)
    has_admin_hint = ("管理员" in joined)
    check(has_alt, "给出了「换个位置」这类可执行建议")
    check(has_admin_hint,
          "**告诉用户可以「以管理员身份运行」**（用户明确要求不能只在界面声称）",
          "日志里没有提到管理员")

    # ---- 确认确实什么都没装进去 ----
    print(f"\n[2] 确认没有留下半成品")
    exe = os.path.join(dest, "Lyric2TTML.exe")
    check(not os.path.exists(exe), "目标目录里没有留下主程序")

    # ---- 正常位置仍然能装（对照）----
    print(f"\n[3] 对照：可写位置仍然能正常安装")
    good = os.path.join(tmp, "good")
    ok2, lines2 = run_installer_at(good, z)
    check(ok2 is True, "可写位置安装成功")
    check(os.path.exists(os.path.join(good, "Lyric2TTML.exe")),
          "可写位置主程序就位")

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    if not admin:
        print("  ⚠ 未验证：**提权后真正写入 Program Files** 的全流程"
              "（本会话无法提权）")
    if _fail:
        return 1
    print("  权限不足时的行为达标 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
