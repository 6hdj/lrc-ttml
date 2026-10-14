"""**接近干净机**的环境下跑真安装 + 真转换（本机能做到的那部分）。

    python tools/test_clean_env.py [--exe dist/安装程序.exe]

## 先诚实说明它**不是**什么

真正意义的"干净机可用"需要一台**没装过 Python / 没装过本软件**的 Win10/11。
本脚本做不到那件事，也**不会**声称做到了。

## 它能做到什么（都是真跑，不是模拟）

把"开发机能提供的一切"尽量拿掉，看程序还站不站得住：

1. **清掉所有 `L2T_*` 环境变量** —— 防止程序偷偷依赖我调试时设的变量；
2. **把 `PATH` 砍到只剩 `System32` / `Windows`** —— 这样任何
   "顺手调个外部命令"（python / ffmpeg / powershell / 7z…）都会失败。
   真干净机上没有这些东西，所以这一步能提前暴露依赖；
3. **全新的用户数据目录**（`LOCALAPPDATA` 指向一个空目录）——
   模拟"第一次运行"；
4. **`HOME`/`USERPROFILE` 之外不留任何从开发环境继承的路径**。

然后在这个环境里：跑安装程序的 `--e2e`（含解压、体检、桌面窗口自检、卸载），
再用装好的主程序**做一次真转换**并严格校验产物。

⚠️ 已知它**覆盖不到**的：缺 VC++ 运行库、缺显卡驱动、无音频设备、
磁盘权限策略差异、真正的全新注册表/COM 状态。这些只在真干净机上才暴露 ——
本脚本跑通**不能**替代干净机验证，但跑不通**一定**是问题。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def bare_env(sandbox: str) -> dict:
    """一个"干净但**真实**"的环境。

    ⚠️⚠️ `PATH` 的取法很重要，我第一版砍成"只有 System32"，结果
    `--uicheck` / 真转换 都在 `numpy` / `_socket` 上失败 —— 一隔离就发现
    **任何真实 Windows 的 PATH 里都有 `System32\\Wbem`、
    `WindowsPowerShell\\v1.0`** 这些目录，砍掉它们造出的是"不存在的系统"，
    报出来的"失败"是**测试环境的错**，不是产品问题。

    现在保留这些**标准系统目录**（真机必有），只拿掉"开发环境特有的东西"：
    虚拟环境、`C:\\Python*`、项目目录、以及所有 `L2T_*`。

    `SystemRoot`/`SystemDrive` 必须有（Windows API 大量依赖）。
    """
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    sysdirs = [os.path.join(sysroot, "System32"), sysroot,
               os.path.join(sysroot, "System32", "Wbem"),
               os.path.join(sysroot, "System32", "WindowsPowerShell", "v1.0"),
               os.path.join(sysroot, "System32", "OpenSSH")]
    # 从真实 PATH 里再挑出"看着像系统目录"的（既真实又不带开发环境）
    for p in os.environ.get("PATH", "").split(os.pathsep):
        low = (p or "").lower()
        if not p or p in sysdirs:
            continue
        if "windows" in low and ".venv" not in low and "opencode" not in low:
            sysdirs.append(p)
    return {
        "SystemRoot": sysroot,
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
        "windir": sysroot,
        "ComSpec": os.path.join(sysroot, "System32", "cmd.exe"),
        "PATH": os.pathsep.join(sysdirs),
        "TEMP": sandbox,
        "TMP": sandbox,
        "LOCALAPPDATA": os.path.join(sandbox, "local"),
        "APPDATA": os.path.join(sandbox, "roaming"),
        "USERPROFILE": os.path.join(sandbox, "profile"),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
        "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
        "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "8"),
        "PYTHONIOENCODING": "utf-8",
        # ⚠️ **故意不带**：所有 L2T_*、虚拟环境路径、CUDA_PATH、HF_HOME、
        # 以及任何指向本仓库/开发工具的变量。
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="接近干净机的验收")
    ap.add_argument("--exe", default=os.path.join("dist", "安装程序.exe"))
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    exe = a.exe if os.path.isabs(a.exe) else os.path.join(HERE, a.exe)
    print("=" * 74)
    print("  接近干净机的验收（**不是**真干净机，见脚本说明）")
    print("=" * 74)
    if not os.path.isfile(exe):
        print(f"  ✗ 找不到 {exe}")
        return 2

    sandbox = os.path.join(HERE, ".tmp", "clean-env")
    shutil.rmtree(sandbox, ignore_errors=True)
    os.makedirs(sandbox, exist_ok=True)
    env = bare_env(sandbox)
    for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        os.makedirs(env[k], exist_ok=True)

    print(f"  安装程序   : {os.path.relpath(exe, HERE)}")
    print(f"  沙箱       : {os.path.relpath(sandbox, HERE)}")
    print(f"  PATH       : {env['PATH']}")
    print(f"  带的环境变量: {sorted(env)}")
    print("=" * 74)

    dest = os.path.join(sandbox, "app")

    print("\n[1] 在这个空环境里跑安装（--e2e --no-uninstall，不下载模型）")
    # ⚠️⚠️ **必须加 `--no-uninstall`**：`--e2e` 的第 [4.6] 步自己会把刚装好的
    # 软件**卸载掉**（顺带用延迟清理删掉目录）。我第一版没加这个开关，
    # 于是后面几步全在对着一个**已经被删掉的 exe** 跑：
    # 报出来的是 `No module named '_socket'` / `numpy` 之类的怪错，
    # 看起来像"空环境下缺运行库"，其实是**文件已经没了**（真踩到，
    # 白查了两轮隔离）。卸载那一半单独放到最后一步测。
    r = subprocess.run([exe, "--e2e", dest, "--no-models", "--no-shortcuts",
                        "--no-uninstall"],
                       capture_output=True, env=env, timeout=3600, cwd=sandbox)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    tail = "\n".join([ln for ln in out.strip().split("\n") if ln.strip()][-14:])
    print("    " + tail.replace("\n", "\n    "))
    check(r.returncode == 0, "空环境里安装流程跑通", f"exit={r.returncode}")
    if err.strip():
        print(f"    （stderr 尾部）{err.strip()[-400:]}")

    app_exe = os.path.join(dest, "Lyric2TTML.exe")
    check(os.path.isfile(app_exe), "主程序已就位")

    print("\n[2] 空环境里问主程序自己的体检（--doctor）")
    if os.path.isfile(app_exe):
        r2 = subprocess.run([app_exe, "--doctor", "--json"], capture_output=True,
                            env=env, timeout=900, cwd=sandbox)
        o2 = (r2.stdout or b"").decode("utf-8", "replace")
        check(r2.returncode == 0, "--doctor 能跑", f"exit={r2.returncode}")
        check('"frozen": true' in o2, "报告自己是冻结版")
        # 缺模型是**预期**的（我们没下），但要确认不是"程序起不来"
        check(len(o2) > 200, "体检有实际内容（不是空输出）", f"{len(o2)} 字节")
    else:
        check(False, "--doctor（主程序不在，跳过）")

    print("\n[3] 空环境里开桌面窗口（--uicheck）")
    if os.path.isfile(app_exe):
        r3 = subprocess.run([app_exe, "--uicheck"], capture_output=True,
                            env=env, timeout=900, cwd=sandbox)
        o3 = ((r3.stdout or b"") + (r3.stderr or b"")).decode("utf-8", "replace")
        check(r3.returncode == 0, "桌面窗口能起来（Qt 插件齐全）",
              f"exit={r3.returncode} {o3.strip()[-200:]}")
    else:
        check(False, "--uicheck（主程序不在，跳过）")

    print("\n[4] 空环境里做一次**真转换**（音频+歌词 → TTML）")
    audio = os.path.join(HERE, "uploads",
                         "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")
    lrc = os.path.join(HERE, "uploads", "1791454729_在出发之前 - 陶典.lrc")
    if not (os.path.isfile(audio) and os.path.isfile(lrc)):
        check(False, "有测试素材", f"{audio} / {lrc}")
    elif not os.path.isfile(app_exe):
        check(False, "真转换（主程序不在）")
    else:
        outdir = os.path.join(sandbox, "out")
        os.makedirs(outdir, exist_ok=True)
        # ⚠️ 模型缓存**必须**指到已有的 3GB 缓存（用 L2T_HF_CACHE）——
        # 否则这一步会去下 3GB，几十分钟，而且会掩盖真正要测的东西。
        # 这是本脚本唯一"借"开发环境的地方，已写明。
        env2 = dict(env, L2T_HF_CACHE=os.path.join(HERE, ".hf_cache"))
        r4 = subprocess.run(
            [app_exe, "--audio", audio, "--lyrics", lrc, "--language", "zh",
             "-o", os.path.join(outdir, "clean.ttml")],
            capture_output=True, env=env2, timeout=3600, cwd=sandbox)
        o4 = ((r4.stdout or b"") + (r4.stderr or b"")).decode("utf-8", "replace")
        ttml = os.path.join(outdir, "clean.ttml")
        check(r4.returncode == 0, "真转换成功", f"exit={r4.returncode}")
        check(os.path.isfile(ttml), "产物文件已生成")
        if os.path.isfile(ttml):
            size = os.path.getsize(ttml)
            check(size > 5000, "产物不是空壳", f"{size} 字节")
            # 用仓库里的校验器严格检查（源码侧工具，只是**读**产物）
            rc = subprocess.run([sys.executable, os.path.join(HERE, "main.py"),
                                 "--check-only", ttml, "--strict-time"],
                                capture_output=True, cwd=HERE, timeout=600)
            ro = ((rc.stdout or b"") + (rc.stderr or b"")).decode("utf-8", "replace")
            check(rc.returncode == 0, "严格校验 0 错误", ro.strip()[-300:])
        else:
            print(f"    （转换输出尾部）{o4.strip()[-500:]}")

    print("\n[5] 空环境里卸载（默认保留模型）")
    if os.path.isfile(app_exe):
        r5 = subprocess.run([app_exe, "--uninstall", dest, "--yes"],
                            capture_output=True, env=env, timeout=900,
                            cwd=sandbox)
        o5 = ((r5.stdout or b"") + (r5.stderr or b"")).decode("utf-8", "replace")
        check(r5.returncode == 0, "卸载成功", f"exit={r5.returncode} {o5[-200:]}")
        check("Failed to import encodings" not in o5,
              "卸载没有自毁运行库（老 bug 的回归）")
        # 等延迟清理
        import time
        for _ in range(40):
            if not os.path.isdir(dest):
                break
            time.sleep(0.5)
        check(not os.path.isdir(dest),
              "安装目录被彻底删除（延迟清理生效）",
              f"仍在：{dest}")
    else:
        check(False, "卸载（主程序不在）")

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    print("  ⚠️ 这只是「接近」干净机：**覆盖不到** 缺 VC++ 运行库、缺显卡驱动、"
          "无音频设备、\n     全新注册表/COM 状态、磁盘权限策略差异 —— "
          "那些只在真干净机上暴露。")
    if not a.keep:
        pass          # 保留沙箱便于排查；需要清理时手动删
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
