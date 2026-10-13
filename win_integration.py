"""Windows 外壳集成：开始菜单 / 桌面快捷方式、卸载注册表项。

    python -m win_integration --probe                       # 看会写哪些位置
    python -m win_integration --install <安装目录> [--desktop] [--start-menu]
    python -m win_integration --remove  <安装目录> [--desktop] [--start-menu]

## 为什么单独一个模块

安装器和**卸载**必须用**同一份**逻辑，否则必然漂移：安装时写进去的注册表项
和快捷方式，卸载时漏删就成了"系统设置里留一个删不掉的条目"（用户会觉得
软件没卸干净）。以前这两段分别写在 `installer_gui.py` 与 `uninstall_core.py` 里，
各写一遍，很容易只改一边。

## 三个位置

1. **开始菜单** `<APPDATA>\\Microsoft\\Windows\\Start Menu\\Programs\\Lyric2TTML.lnk`
   —— 指向 **`Lyric2TTML.exe` 本体**（不是 `启动.bat`）。
   ⚠️ 别指 .bat：那会带出一个黑色控制台窗口，而且用户看到的是"批处理"。
2. **桌面**（可选，默认**不建**，安装界面里有勾选框）——
   同一份逻辑，`desktop=True` 才建。
3. **卸载入口**：写 HKCU 的
   `Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Lyric2TTML`
   下的 `DisplayName` / `DisplayVersion` / `Publisher` / `InstallLocation` /
   `UninstallString` / `DisplayIcon` / `EstimatedSize` / `NoModify` / `NoRepair`。
   * 用 **HKCU**（不是 HKLM）：普通用户就能写，**不需要管理员**。
     HKLM 要提权，而本项目的原则是"能不提权就不提权"。
   * 写这些键之后，**Windows 设置 → 应用 → 已安装的应用**里就会显示它，
     卸载按钮直接调用我们的卸载程序。

## 快捷方式怎么建（不引第三方库）

用 PowerShell 的 `WScript.Shell`（系统自带）。`pythoncom`/`pywin32` 不引入 ——
安装器是冻结的 onefile，多一个依赖就多一类打包问题。
"""
from __future__ import annotations

import os
import subprocess
import sys

APP_NAME = "Lyric2TTML"
UNINSTALL_KEY = ("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
                 + APP_NAME)
EXE_NAME = "Lyric2TTML.exe"


def _ps_escape(s: str) -> str:
    """PowerShell 单引号字符串转义（单引号本身要写两遍）。"""
    return s.replace("'", "''")


def _run_ps(script: str, timeout: int = 60) -> tuple:
    """跑一段 PowerShell。返回 (成功, 输出)。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                              # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def start_menu_dir(programs: str = "") -> str:
    """开始菜单"程序"目录。

    默认从 `%APPDATA%` 推，**不调 shell API**（避免依赖 + 便于测试注入）。
    """
    if programs:
        return programs
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs")


def desktop_dir() -> str:
    """桌面目录（用户自己的，不是 Public）。"""
    return os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"),
                        "Desktop")


def make_shortcut(lnk: str, target: str, workdir: str = "",
                  icon: str = "", args: str = "",
                  description: str = "") -> bool:
    """建一个 .lnk。返回是否**确认建成功**（真的去看文件在不在）。"""
    try:
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
    except OSError:
        pass
    icon = icon or target
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        f"'{_ps_escape(lnk)}');"
        f"$s.TargetPath='{_ps_escape(target)}';"
        f"$s.WorkingDirectory='{_ps_escape(workdir or os.path.dirname(target))}';"
        f"$s.IconLocation='{_ps_escape(icon)}';"
        f"$s.Description='{_ps_escape(description or APP_NAME)}';"
    )
    if args:
        script += f"$s.Arguments='{_ps_escape(args)}';"
    script += "$s.Save()"
    ok, _out = _run_ps(script)
    return ok and os.path.isfile(lnk)


def remove_file(path: str) -> bool:
    """删文件；返回 True 表示"现在确实不在了"。"""
    try:
        if os.path.isfile(path):
            os.remove(path)
        return not os.path.exists(path)
    except OSError:
        return not os.path.exists(path)


# --------------------------------------------------------------- 注册表
def _reg(args: list) -> tuple:
    """调 reg.exe（系统自带，不需要 winreg 的权限差异）。"""
    try:
        r = subprocess.run(["reg"] + args, capture_output=True, text=True,
                           encoding="gbk", errors="replace", timeout=60)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                              # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def register_uninstall(install_dir: str, version: str, publisher: str = "",
                       size_kb: int = 0, display_icon: str = "",
                       uninstall_string: str = "") -> bool:
    """写"已安装的应用"条目（HKCU，不需要管理员）。

    ⚠️ 用 `reg add /f` 是**幂等**的：重复安装不会叠加条目，
    也不会留下"同名多个条目"。
    """
    exe = os.path.join(install_dir, EXE_NAME)
    items = [
        ("DisplayName", f"{APP_NAME}（歌词转 TTML）"),
        ("DisplayVersion", version),
        ("InstallLocation", install_dir),
        ("DisplayIcon", display_icon or exe),
        ("UninstallString", uninstall_string or
         f'"{exe}" --uninstall "{install_dir}"'),
        ("QuietUninstallString", f'"{exe}" --uninstall "{install_dir}" --yes'),
        # 不显示"修改/修复"：我们没有这两条路径，显示了只会误导
        ("NoModify", "1", "REG_DWORD"),
        ("NoRepair", "1", "REG_DWORD"),
    ]
    if publisher:
        items.append(("Publisher", publisher))
    if size_kb > 0:
        items.append(("EstimatedSize", str(size_kb), "REG_DWORD"))

    all_ok = True
    for it in items:
        name, value = it[0], it[1]
        kind = it[2] if len(it) > 2 else "REG_SZ"
        ok, _out = _reg(["add", f"HKCU\\{UNINSTALL_KEY}", "/v", name,
                         "/t", kind, "/d", value, "/f"])
        all_ok = all_ok and ok
    return all_ok


def unregister_uninstall() -> bool:
    """删掉整个卸载注册表项（**幂等**：不存在也算成功）。"""
    ok, _out = _reg(["delete", f"HKCU\\{UNINSTALL_KEY}", "/f"])
    # reg delete 在"项不存在"时返回非 0 —— 那也算成功（目标状态已达成）
    exists, _ = read_uninstall()
    return ok or not exists


def read_uninstall() -> tuple:
    """读回卸载注册表项（给测试/诊断用）。返回 (是否存在, 解析出的字典)。"""
    ok, out = _reg(["query", f"HKCU\\{UNINSTALL_KEY}"])
    if not ok:
        return False, {}
    data = {}
    for ln in out.splitlines():
        parts = ln.split("    ")
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 3 and parts[0].startswith(("Display", "Install",
                                                    "Uninstall", "Publisher",
                                                    "NoModify", "NoRepair",
                                                    "Estimated", "Quiet")):
            data[parts[0]] = parts[-1]
    return True, data


# ------------------------------------------------------------ 一键安装/卸载
def integrate(install_dir: str, version: str, *, desktop: bool = False,
              start_menu: bool = True, programs: str = "",
              desk: str = "", size_kb: int = 0, publisher: str = "",
              log=print) -> dict:
    """安装完成后建立外壳集成。返回明细（给界面显示、给测试断言）。"""
    result = {"start_menu": "", "desktop": "", "registry": False,
              "errors": []}
    exe = os.path.join(install_dir, EXE_NAME)

    if start_menu:
        lnk = os.path.join(start_menu_dir(programs), f"{APP_NAME}.lnk")
        if make_shortcut(lnk, exe, install_dir, exe,
                         description="歌词 + 音频 → 逐字 TTML"):
            result["start_menu"] = lnk
            log(f"  [完成] 开始菜单快捷方式：{lnk}")
        else:
            result["errors"].append("开始菜单快捷方式没建成功")
            log("  [提示] 开始菜单快捷方式没建成功")

    if desktop:
        lnk = os.path.join(desk or desktop_dir(), f"{APP_NAME}.lnk")
        if make_shortcut(lnk, exe, install_dir, exe,
                         description="歌词 + 音频 → 逐字 TTML"):
            result["desktop"] = lnk
            log(f"  [完成] 桌面快捷方式：{lnk}")
        else:
            result["errors"].append("桌面快捷方式没建成功")
            log("  [提示] 桌面快捷方式没建成功")

    if register_uninstall(install_dir, version, publisher=publisher,
                          size_kb=size_kb):
        result["registry"] = True
        log("  [完成] 已注册到「设置 → 应用 → 已安装的应用」")
    else:
        result["errors"].append("卸载注册表项没写成功")
        log("  [提示] 卸载注册表项没写成功（不影响使用，但设置里看不到它）")
    return result


def integrate_remove(install_dir: str, *, desktop: bool = True,
                     start_menu: bool = True, programs: str = "",
                     desk: str = "", log=print) -> dict:
    """卸载时**对称**地清掉集成项（幂等）。"""
    result = {"removed": [], "registry": False, "errors": []}
    if start_menu:
        lnk = os.path.join(start_menu_dir(programs), f"{APP_NAME}.lnk")
        if remove_file(lnk):
            result["removed"].append(lnk)
    if desktop:
        for name in (f"{APP_NAME}.lnk", f"卸载 {APP_NAME}.lnk"):
            lnk = os.path.join(desk or desktop_dir(), name)
            if remove_file(lnk):
                result["removed"].append(lnk)
    if unregister_uninstall():
        result["registry"] = True
    else:
        result["errors"].append("卸载注册表项没删掉")
    for p in result["removed"]:
        log(f"  已删除：{p}")
    if result["registry"]:
        log("  已从「已安装的应用」里移除")
    return result


def _cli(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="win_integration",
                                 description="Windows 外壳集成")
    ap.add_argument("--probe", action="store_true", help="只显示会写哪些位置")
    ap.add_argument("--install", default="", help="安装目录")
    ap.add_argument("--remove", default="", help="卸载（传安装目录）")
    ap.add_argument("--desktop", action="store_true", help="同时建桌面快捷方式")
    ap.add_argument("--version", default="", help="版本号")
    ap.add_argument("--programs", default="", help="覆盖开始菜单目录（测试用）")
    ap.add_argument("--desk", default="", help="覆盖桌面目录（测试用）")
    a = ap.parse_args(argv)

    if a.probe:
        print(f"应用名      : {APP_NAME}")
        print(f"开始菜单目录: {start_menu_dir(a.programs)}")
        print(f"桌面目录    : {a.desk or desktop_dir()}")
        print(f"卸载注册表项: HKCU\\{UNINSTALL_KEY}")
        exists, data = read_uninstall()
        print(f"当前是否已注册: {exists}")
        for k, v in data.items():
            print(f"    {k} = {v}")
        return 0
    if a.install:
        r = integrate(a.install, a.version or "0.0.0", desktop=a.desktop,
                      programs=a.programs, desk=a.desk)
        return 0 if not r["errors"] else 1
    if a.remove:
        r = integrate_remove(a.remove, desktop=a.desktop,
                             programs=a.programs, desk=a.desk)
        return 0 if not r["errors"] else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
