"""**升级覆盖安装**的回归测试（任务书 F："旧版本升级、数据保留"）。

    python tools/test_upgrade.py

## 为什么必须单独测它（这是最容易把用户装坏的一步）

"重新解压覆盖"看着简单，但它有几个**真会毁掉用户环境**的坑，而且都只能实测：

1. **能不能覆盖一个已经装好的目录**（文件已存在、可能带只读属性）；
2. **用户数据会不会被冲掉** —— 用户的 `outputs/`、配置、以及**3GB 模型**
   如果在安装目录里，覆盖时绝不能被删；
3. **程序正开着**（文件被占用）时会不会整个安装失败；
4. 覆盖到一半**取消**，会不会留下一个"半新半旧、启动不了"的目录。

## 做法：用**合成的小载荷**，不碰真安装包

真载荷 650MB / 一万多个文件，跑一次几分钟，而且难以构造"文件被占用"这类场景。
本测试自己造一个几 KB 的 zip，直接驱动 `Installer.extract_app()` ——
被测的是**解压/覆盖/取消逻辑本身**，与载荷大小无关。
（端到端跑真载荷由 `tools/accept_frozen.py` 与 `--e2e` 负责。）

⚠️ 全部操作都在 `--dir`（默认 `.tmp/upgrade-test`）沙箱里，**不碰用户任何真实目录**。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SANDBOX = os.path.join(HERE, ".tmp", "upgrade-test")

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def make_payload(path: str, version: str, files: dict) -> str:
    """造一个"像安装载荷"的 zip：含 Lyric2TTML.exe + 若干文件。

    ⚠️ **必须照真实载荷的布局**：所有条目都套在一层顶层目录 `Lyric2TTML/` 下
    （打包时是"整个文件夹"压的，zip_dir 相对父目录存）。
    `extract_app()` 会把这层**唯一**的顶层目录剥掉；我第一版造的载荷没有这层，
    于是文件解到了 `安装目录/_internal/...`，主程序找不到、测试报"包可能损坏" ——
    **错在测试的样本，不在被测代码**。照真实布局造，才同时验到"剥顶层目录"。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    top = "Lyric2TTML/"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(top + "Lyric2TTML.exe", f"MZ fake exe {version}")
        zf.writestr(top + "version.txt", version)
        for name, content in files.items():
            zf.writestr(top + name, content)
    return path


def new_installer(dest: str, zip_path: str):
    """造一个无界面 Installer，载荷指向合成 zip。

    ⚠️ `say()` 也接管掉 —— 否则失败时**什么原因都看不见**（真踩过：
    第一版只说"第一次安装成功 FAIL"，实际 `extract_app` 返回 False 的原因
    被丢进了队列，测试里没人消费）。测试工具最忌讳"只报对错、不报原因"。

    ⚠️⚠️ **必须先把 `--no-shortcuts` 塞进 `sys.argv`**：`extract_app()` 会调
    `win_integration.integrate()`，那是**真的**去建开始菜单快捷方式、写
    HKCU 卸载注册表项。第一次跑这个测试时它就真的去写了 —— 只是在这台机器上
    恰好失败（日志里那两行"[提示] …没建成功"），也就是说**测试的安全性建立在
    "恰好失败"之上**，这不能接受：在权限正常的机器上它就会污染用户的开始菜单和
    注册表，而且测试**还会通过**。
    `installer_gui.e2e_mode()` 认这个开关，设上就彻底跳过外壳集成。
    """
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
    app.say = lambda m="": print("       | " + str(m))
    app.set_sub = lambda t, c=None: None
    app._pump_now()
    return root, app, ig


def drive(root, app) -> None:
    for _ in range(6):
        root.update()
        app._pump_now()


def case_basic_upgrade() -> None:
    """① 全新安装 → ② 覆盖升级：用户数据必须原样保留。"""
    print("\n[1] 全新安装 → 覆盖升级（用户数据保留）")
    dest = os.path.join(SANDBOX, "app")
    shutil.rmtree(dest, ignore_errors=True)
    z1 = make_payload(os.path.join(SANDBOX, "v1.zip"), "1.0.0",
                      {"_internal/base.txt": "v1"})
    root, app, ig = new_installer(dest, z1)
    try:
        ig.payload_zip = lambda: z1
        ok1 = app.extract_app()
        drive(root, app)
        check(ok1, "第一次安装成功")
        check(os.path.exists(os.path.join(dest, "Lyric2TTML.exe")),
              "主程序已就位")
        check(open(os.path.join(dest, "version.txt"), encoding="utf-8").read()
              == "1.0.0", "版本号是 1.0.0")

        # ---- 制造"用户数据"：这是他自己的东西，升级绝不能动 ----
        user_files = {
            "outputs/我的歌.ttml": "<tt>用户辛苦做的成品</tt>",
            ".hf_cache/models--x/blob": "假装是 3GB 模型",
            "config.json": '{"workspace": "E:/我的歌"}',
            "portable.txt": "",
        }
        for rel, content in user_files.items():
            p = os.path.join(dest, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(content)

        # ---- 覆盖升级 ----
        z2 = make_payload(os.path.join(SANDBOX, "v2.zip"), "1.1.0",
                          {"_internal/base.txt": "v2",
                           "_internal/new.txt": "新版本才有的文件"})
        ig.payload_zip = lambda: z2
        ok2 = app.extract_app()
        drive(root, app)
        check(ok2, "覆盖升级成功")
        check(open(os.path.join(dest, "version.txt"), encoding="utf-8").read()
              == "1.1.0", "版本号已更新到 1.1.0")
        check(os.path.exists(os.path.join(dest, "_internal", "new.txt")),
              "新版本新增的文件装进来了")
        check(open(os.path.join(dest, "_internal", "base.txt"),
                   encoding="utf-8").read() == "v2", "旧文件被正确覆盖")

        for rel, content in user_files.items():
            p = os.path.join(dest, rel.replace("/", os.sep))
            exists = os.path.exists(p)
            same = (exists and open(p, encoding="utf-8").read() == content
                    if rel != "portable.txt" else exists)
            check(same, f"用户数据保留：{rel}",
                  "被升级删掉或改写了" if not same else "")
    finally:
        root.destroy()


def case_readonly_upgrade() -> None:
    """③ 旧文件带只读属性：必须能覆盖（真踩过，卡在 CHANGELOG.md 上）。"""
    print("\n[2] 旧文件带**只读**属性时能否覆盖")
    dest = os.path.join(SANDBOX, "app-ro")
    shutil.rmtree(dest, ignore_errors=True)
    z1 = make_payload(os.path.join(SANDBOX, "ro1.zip"), "1.0.0",
                      {"CHANGELOG.md": "旧内容"})
    root, app, ig = new_installer(dest, z1)
    try:
        ig.payload_zip = lambda: z1
        app.extract_app()
        drive(root, app)
        target = os.path.join(dest, "CHANGELOG.md")
        # 加上只读属性（模拟从只读介质/版本控制里拷出来的文件）
        import stat
        os.chmod(target, stat.S_IREAD)
        z2 = make_payload(os.path.join(SANDBOX, "ro2.zip"), "1.1.0",
                          {"CHANGELOG.md": "新内容"})
        ig.payload_zip = lambda: z2
        ok = app.extract_app()
        drive(root, app)
        check(ok, "只读旧文件仍能完成覆盖")
        got = open(target, encoding="utf-8").read()
        check(got == "新内容", "只读文件内容被成功更新", f"实际={got!r}")
    finally:
        root.destroy()


def case_cancel_midway() -> None:
    """④ 解压到一半取消：必须停下、留下已完成的部分、且**不崩**。"""
    print("\n[3] 解压中途取消（安全停止）")
    dest = os.path.join(SANDBOX, "app-cancel")
    shutil.rmtree(dest, ignore_errors=True)
    # 造一个文件足够多的载荷，保证取消点落在中途
    files = {f"_internal/f{i:04d}.txt": "x" * 512 for i in range(500)}
    z1 = make_payload(os.path.join(SANDBOX, "big.zip"), "1.0.0", files)
    root, app, ig = new_installer(dest, z1)
    try:
        ig.payload_zip = lambda: z1
        app._cancelled = True                  # 一开跑就请求取消
        ok = app.extract_app()
        drive(root, app)
        check(ok is False, "取消时 extract_app 返回 False")
        exe = os.path.join(dest, "Lyric2TTML.exe")
        # 取消点在第 64 个文件之后，主程序（zip 里的第一个）应该已经解出来了
        check(os.path.exists(exe),
              "取消后已解压的文件**留在原地**（不是被清空）")
        check(app._cancelled, "取消标志保持")
    finally:
        root.destroy()


def case_locked_file() -> None:
    """⑤ 程序还开着（文件被占用）：不能整个安装失败，要报告并继续。"""
    print("\n[4] 文件被占用时不中断整个安装")
    dest = os.path.join(SANDBOX, "app-locked")
    shutil.rmtree(dest, ignore_errors=True)
    z1 = make_payload(os.path.join(SANDBOX, "lk1.zip"), "1.0.0",
                      {"locked.txt": "旧", "other.txt": "旧"})
    root, app, ig = new_installer(dest, z1)
    try:
        ig.payload_zip = lambda: z1
        app.extract_app()
        drive(root, app)
        locked_path = os.path.join(dest, "locked.txt")
        old_content = open(locked_path, encoding="utf-8").read()
        # 独占打开，模拟"程序还开着"
        fh = open(locked_path, "r+b")
        try:
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            z2 = make_payload(os.path.join(SANDBOX, "lk2.zip"), "1.1.0",
                              {"locked.txt": "新", "other.txt": "新"})
            ig.payload_zip = lambda: z2
            ok = app.extract_app()
            drive(root, app)
            check(ok, "被占用时**不报整体失败**")
            check(open(os.path.join(dest, "other.txt"),
                       encoding="utf-8").read() == "新",
                  "未占用的文件照常更新（不是一遇占用就全放弃）")
        finally:
            try:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:                       # noqa: BLE001
                pass
            fh.close()
        # ⚠️ 校验要放在**解锁之后**再读：锁还持着的时候 open() 会
        # `PermissionError: [Errno 13]`，第一版就是这么把测试自己搞崩的
        # （测试的错，不是被测代码的错）。
        after = open(locked_path, encoding="utf-8").read()
        check(after == old_content, "被占用的文件保持旧内容（没被写坏）",
              f"实际={after!r}")
    finally:
        root.destroy()


def main() -> int:
    ap = argparse.ArgumentParser(description="升级覆盖安装回归测试")
    ap.add_argument("--keep", action="store_true", help="保留沙箱（排查用）")
    a = ap.parse_args()

    os.makedirs(SANDBOX, exist_ok=True)
    print("=" * 74)
    print("  升级 / 覆盖安装 回归测试")
    print(f"  沙箱：{SANDBOX}")
    print("=" * 74)
    print(f"  载荷：合成的小 zip（不碰真安装包，被测的是解压/覆盖/取消逻辑）")

    try:
        case_basic_upgrade()
        case_readonly_upgrade()
        case_cancel_midway()
        case_locked_file()
    finally:
        if not a.keep:
            shutil.rmtree(SANDBOX, ignore_errors=True)

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    if _fail:
        print("  有失败项 —— 升级路径不安全，**不要发布**")
        return 1
    print("  升级路径安全 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
