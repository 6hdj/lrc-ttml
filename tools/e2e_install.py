"""端到端：模拟"小白用户"把软件装到 E 盘，全程自动，不需要人点。

跑法：
    .venvpack\\Scripts\\python.exe tools\\e2e_install.py [安装目录]

它做的事和人点界面完全一样：
    解压 → 写快捷方式/卸载程序 → 检查环境 → 补全缺的东西 → 复查
唯一不同的是把 GUI 的消息泵换成直接调用（`_pump_now`），所以不需要真的开窗口。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)


def drain(app, root, rounds=40):
    for _ in range(rounds):
        root.update()
        app._pump_now()


def main() -> int:
    dest = sys.argv[1] if len(sys.argv) > 1 else r"E:\Lyric2TTML"
    print("=" * 74)
    print(f"  端到端安装测试 → {dest}")
    print("=" * 74)

    # 干净起步：先删掉（模拟"新电脑上第一次装"）
    if os.path.isdir(dest):
        print("  [准备] 删除已有目录…")
        try:
            for r, ds, fs in os.walk(dest, topdown=False):
                for f in fs:
                    try:
                        os.chmod(os.path.join(r, f), 0o666)
                    except Exception:
                        pass
            shutil.rmtree(dest, ignore_errors=True)
        except Exception as e:
            print(f"  [警告] 删不干净：{e}")

    root = tk.Tk()
    root.withdraw()
    import installer_gui as G

    app = G.Installer(root)
    app.install_dir = dest
    app.path_var.set(dest)
    print(f"  payload_zip() = {G.payload_zip() or '(源码模式，无载荷)'}")

    # ---- 1. 解压安装 ----
    print("\n[1] 解压安装…")
    ok = app.extract_app()
    drain(app, root)
    print(f"    结果：{'成功' if ok else '失败'}")
    exe = os.path.join(dest, "Lyric2TTML.exe")
    print(f"    主程序在：{os.path.exists(exe)}")
    if not ok or not os.path.exists(exe):
        print("\n✗ 解压这一步就没过，后面的不用测了")
        root.destroy()
        return 1
    n_files = sum(len(fs) for _, _, fs in os.walk(dest))
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(dest) for f in fs)
    print(f"    装了 {n_files} 个文件，{size / 1024 / 1024:.0f} MB")

    # ---- 2. 快捷方式 / 卸载程序 ----
    print("\n[2] 快捷方式与卸载程序…")
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    print(f"    桌面快捷方式: {os.path.exists(os.path.join(desk, 'Lyric2TTML.lnk'))}")
    print(f"    卸载程序.bat: {os.path.exists(os.path.join(dest, '卸载程序.bat'))}")
    print(f"    桌面卸载入口: "
          f"{os.path.exists(os.path.join(desk, '卸载 Lyric2TTML.lnk'))}")

    # ---- 3. 环境检查（问主程序要报告）----
    print("\n[3] 环境检查…")
    rep = G.ask_app_report(dest)
    if not rep or not rep.get("items"):
        print("    ✗ 拿不到主程序的报告")
        root.destroy()
        return 1
    items = rep["items"]
    bad = [i for i in items if not i["ok"] and i["level"] == "crit"]
    print(f"    共 {len(items)} 项，缺 {len(bad)} 项必需的：")
    for i in bad:
        print(f"      - {i['name']}：{i['detail']}")

    # ---- 4. 补全 ----
    if bad:
        print("\n[4] 补全缺的项…（可能要下几 GB，耐心）")
        for i in bad:
            key = i["key"]
            fn = None
            if key == "whisper":
                fn = lambda: G.core.fetch_whisper("large-v3")
            elif key == "campp":
                fn = G.core.fetch_campp
            elif key.startswith("cuda_"):
                fn = G.core.fetch_cuda
            if fn is None:
                print(f"    [跳过] {i['name']}（没有自动补全的方法）")
                continue
            print(f"\n    ── 补 {i['name']} ──")
            try:
                r = bool(fn())
            except Exception as e:
                print(f"    [异常] {type(e).__name__}: {e}")
                r = False
            print(f"    {'[成功]' if r else '[失败]'} {i['name']}")
            if key.startswith("cuda_"):
                break        # 三个 DLL 一次装完
    else:
        print("\n[4] 什么都不缺，跳过补全")

    # ---- 5. 复查 ----
    print("\n[5] 复查…")
    rep2 = G.ask_app_report(dest)
    bad2 = [i for i in rep2.get("items", [])
            if not i["ok"] and i["level"] == "crit"]
    print(f"    仍未通过 {len(bad2)} 项必需的：")
    for i in bad2:
        print(f"      ✗ {i['name']}：{i['detail']}")

    root.destroy()
    print()
    print("=" * 74)
    if bad2:
        print(f"  ✗ 还有 {len(bad2)} 项没配好")
        return 1
    print("  ✓ 全部通过 —— 环境完整，可以启动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
