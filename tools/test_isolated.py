"""**隔离验证**：把"开发机能提供的一切"尽量拿掉，看软件还站不站得住。

    python tools/test_isolated.py

## 它**不是**"干净机验证" —— 先把话说清楚

真正意义的"干净机可用"需要一台**没装过 Python、没装过本软件**的 Win10/11。
本脚本做不到，也**不会**声称做到了。交付报告第四节把那条列进"未验证项"。

## 它能做到什么（都是真跑）

把开发环境的痕迹尽量清掉，装一份**真安装程序**产出的软件，然后跑它自己的命令：

1. **清掉所有 `L2T_*`**（防止程序偷偷依赖我调试时设的变量）；
2. **`PATH` 只留真实 Windows 都会有的系统目录**
   （`System32`、`Wbem`、`WindowsPowerShell`）—— 不这样做会造出"不存在的系统"，
   报出来的错是我的测试错了，不是产品错了（上一轮我就在这里栽过）；
3. **全新用户目录**（`LOCALAPPDATA` / `APPDATA` / `USERPROFILE` 全指向沙箱空目录）
   ⇒ 模拟"第一次运行"；
4. **测试素材复制进沙箱**再喂给程序 —— 确保它**不需要**读本仓库里的任何文件；
5. 顺序很重要：**先跑完所有检查，最后才卸载**
   （`--e2e` 的第 [4.6] 步自己会卸载并延迟清理目录，不加 `--no-uninstall`
   会让后面几步对着一个**已被删掉的 exe** 跑，报出 `No module named '_socket'`
   这种看着像"缺运行库"的怪错 —— 上一轮真踩过）。

## 覆盖不到的（只在真干净机上才暴露）

缺 VC++ 运行库、缺显卡驱动、无音频设备、全新注册表/COM 状态、
磁盘权限策略差异、以及"从没装过本软件"的升级路径。
**本脚本跑通不能替代干净机验证；但跑不通一定是问题。**
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ok = _fail = 0
_NOTES = []


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def note(msg: str) -> None:
    _NOTES.append(msg)
    print(f"  ·    {msg}")


def isolated_env(sandbox: str) -> dict:
    """一个"干净但真实"的环境：只有真机必有的系统目录，没有开发环境痕迹。"""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    dirs = [os.path.join(sysroot, "System32"), sysroot,
            os.path.join(sysroot, "System32", "Wbem"),
            os.path.join(sysroot, "System32", "WindowsPowerShell", "v1.0")]
    # 真实 PATH 里那些"看着像系统目录"的也留着（既真实又不带开发环境）
    for p in os.environ.get("PATH", "").split(os.pathsep):
        low = (p or "").lower()
        if not p or p in dirs:
            continue
        if "windows" in low and ".venv" not in low and "opencode" not in low:
            dirs.append(p)
    return {
        "SystemRoot": sysroot,
        "SystemDrive": os.environ.get("SystemDrive", "C:"),
        "windir": sysroot,
        "ComSpec": os.path.join(sysroot, "System32", "cmd.exe"),
        "PATH": os.pathsep.join(dirs),
        "TEMP": sandbox, "TMP": sandbox,
        "LOCALAPPDATA": os.path.join(sandbox, "local"),
        "APPDATA": os.path.join(sandbox, "roaming"),
        "USERPROFILE": os.path.join(sandbox, "profile"),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
        "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
        "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "8"),
        "PYTHONIOENCODING": "utf-8",
        # ⚠️ 故意不带：所有 L2T_*、虚拟环境路径、CUDA_PATH、HF_HOME、
        # 以及任何指向本仓库的变量。
    }


def run(exe: str, args: list, env: dict, cwd: str, timeout: int = 900) -> tuple:
    r = subprocess.run([exe, *args], capture_output=True, env=env, cwd=cwd,
                       timeout=timeout)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    return r.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser(description="隔离验证（不是干净机）")
    ap.add_argument("--exe", default=os.path.join("dist", "安装程序.exe"))
    ap.add_argument("--hf-cache", default="",
                    help="把模型缓存指到已有目录（跳过 3GB 下载）；留空则不下模型")
    ap.add_argument("--convert", action="store_true",
                    help="额外做一次真转换（需要 --hf-cache 或已下好的模型）")
    a = ap.parse_args()

    exe = a.exe if os.path.isabs(a.exe) else os.path.join(HERE, a.exe)
    print("=" * 74)
    print("  隔离验证（**不是**干净机验证 —— 见脚本说明）")
    print("=" * 74)
    if not os.path.isfile(exe):
        print(f"  ✗ 找不到 {exe}")
        return 2

    sandbox = os.path.join(HERE, ".tmp", "isolated")
    shutil.rmtree(sandbox, ignore_errors=True)
    os.makedirs(sandbox, exist_ok=True)
    env = isolated_env(sandbox)
    for k in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        os.makedirs(env[k], exist_ok=True)

    print(f"  安装程序 : {os.path.relpath(exe, HERE)}")
    print(f"  沙箱     : {os.path.relpath(sandbox, HERE)}")
    print(f"  PATH     : {env['PATH']}")
    print(f"  环境变量 : {len(env)} 个（无任何 L2T_*）")
    print("=" * 74)

    dest = os.path.join(sandbox, "app")

    # ---- [1] 装（不卸载，留到最后）----
    print("\n[1] 在隔离环境里安装（--e2e --no-uninstall）")
    r = subprocess.run([exe, "--e2e", dest, "--no-models", "--no-shortcuts",
                        "--no-uninstall"], capture_output=True, env=env,
                       cwd=sandbox, timeout=3600)
    out = (r.stdout or b"").decode("utf-8", "replace")
    tail = "\n".join([ln for ln in out.strip().split("\n") if ln.strip()][-8:])
    for ln in tail.split("\n"):
        print(f"      {ln}")
    check(r.returncode == 0, "安装流程跑通", f"exit={r.returncode}")
    app_exe = os.path.join(dest, "Lyric2TTML.exe")
    check(os.path.isfile(app_exe), "主程序已就位")

    # ---- [2] 程序自己的三个自检入口 ----
    print("\n[2] 程序自检入口（都必须在隔离环境里 exit=0）")
    if os.path.isfile(app_exe):
        c, o = run(app_exe, ["--version"], env, sandbox)
        check(c == 0, "--version", f"exit={c} {o.strip()[-160:]}")
        check("1.1.0" in o or "桌面版" in o, "版本信息可读")

        c, o = run(app_exe, ["--doctor", "--json"], env, sandbox)
        check(c == 0, "--doctor --json", f"exit={c}")
        check('"frozen": true' in o, "报告自己是冻结版")
        check(len(o) > 200, "体检有实际内容", f"{len(o)} 字节")
        # 必须能报出"模型没下"，而不是崩掉
        check("未下载" in o or "not_installed" in o or "模型" in o,
              "体检覆盖到模型状态")

        c, o = run(app_exe, ["--uicheck"], env, sandbox)
        check(c == 0, "--uicheck（Qt 平台插件齐全）",
              f"exit={c} {o.strip()[-200:]}")
    else:
        check(False, "自检入口（主程序不在）")

    # ---- [3] 模型清单能在隔离环境里读出来 ----
    print("\n[3] 模型清单（随包资源，不依赖仓库）")
    if os.path.isfile(app_exe):
        c, o = run(app_exe, ["--model-cli", "list"], env, sandbox)
        check(c == 0, "--model-cli list", f"exit={c} {o.strip()[-200:]}")
        check("large-v3" in o or "whisper" in o.lower(),
              "清单里有语音识别模型")
    else:
        check(False, "模型清单（主程序不在）")

    # ---- [4] 可选：真转换（素材**复制进沙箱**，不读仓库）----
    if a.convert:
        print("\n[4] 隔离环境里真转换（素材已复制进沙箱）")
        src_audio = os.path.join(HERE, "uploads",
                                 "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")
        src_lrc = os.path.join(HERE, "uploads",
                               "1791454729_在出发之前 - 陶典.lrc")
        fix = os.path.join(sandbox, "fixtures")
        os.makedirs(fix, exist_ok=True)
        audio = os.path.join(fix, "a.wav")
        lrc = os.path.join(fix, "a.lrc")
        shutil.copy2(src_audio, audio)
        shutil.copy2(src_lrc, lrc)
        e2 = dict(env)
        if a.hf_cache:
            e2["L2T_HF_CACHE"] = os.path.abspath(a.hf_cache)
            note(f"模型缓存指向 {a.hf_cache}（唯一'借'开发环境的地方，已注明）")
        c, o = run(app_exe, ["--audio", audio, "--lyrics", lrc,
                             "--language", "zh",
                             "-o", os.path.join(sandbox, "o.ttml")],
                   e2, sandbox, timeout=3600)
        ttml = os.path.join(sandbox, "o.ttml")
        check(c == 0, "真转换成功", f"exit={c} {o.strip()[-300:]}")
        check(os.path.isfile(ttml), "产物已生成")
        if os.path.isfile(ttml):
            check(os.path.getsize(ttml) > 5000, "产物不是空壳",
                  f"{os.path.getsize(ttml)} 字节")
    else:
        note("跳过真转换（加 --convert 才跑；无模型时会下 3GB）")

    # ---- [5] 最后才卸载 ----
    print("\n[5] 隔离环境里卸载（默认保留用户数据与模型）")
    if os.path.isfile(app_exe):
        c, o = run(app_exe, ["--uninstall", dest, "--yes"], env, sandbox)
        check(c == 0, "卸载成功", f"exit={c} {o.strip()[-200:]}")
        check("Failed to import encodings" not in o,
              "卸载没有自毁运行库（老 bug 回归）")
        for _ in range(40):
            if not os.path.isdir(dest):
                break
            time.sleep(0.5)
        check(not os.path.isdir(dest), "安装目录被彻底删除（延迟清理生效）",
              f"仍在：{dest}")
    else:
        check(False, "卸载（主程序不在）")

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    for n in _NOTES:
        print(f"  · {n}")
    print("  ⚠️ 这不是干净机验证：覆盖不到 缺 VC++ 运行库 / 缺显卡驱动 /"
          " 无音频设备 /")
    print("     全新注册表与 COM 状态 / 磁盘权限策略差异。"
          "跑通**不能**替代干净机；但跑不通**一定**是问题。")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
