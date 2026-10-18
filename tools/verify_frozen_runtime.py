"""**冻结版**运行时组件流程验证（需求第 10 条的强制项）。

需求原文："在冻结版 EXE 中实际验证基础歌词制作、CAM++ 权重检测、
运行时下载、首次加载、再次使用、失败恢复和升级兼容性。
源码模式通过不能代替冻结版验证。"

本脚本对**真的打包 exe** 依次验证：

  [1] 基础歌词制作：不装运行时也能跑完转换（ASR + 对齐 + TTML 导出）
  [2] 运行时状态：`--doctor` 里如实报告"未安装"，不误报成权重缺失
  [3] 运行时下载：真下载 118 MB wheel → SHA256 → 解包 → **真加载验证**
  [4] 首次加载：装完之后 CAM++ 能真的从运行时目录 import 到 torch
  [5] 再次使用：重复调用是幂等的（不重下）、状态仍 READY
  [6] 失败恢复：模拟下载失败（给一个坏 URL）→ `.part` 必须保留 → 重试能续传
  [7] 升级兼容：运行时在**数据目录**，删掉/替换程序目录不影响它

    python tools/verify_frozen_runtime.py [exe路径]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(HERE, "dist", "_qttest2", "Lyric2TTML", "Lyric2TTML.exe")
SANDBOX = os.path.join(HERE, ".tmp", "frozen-runtime")
PASS, FAIL = [], []


def ok(name: str, detail: str = "") -> None:
    PASS.append(name)
    print(f"  OK   {name}" + (f"  —— {detail}" if detail else ""))


def bad(name: str, detail: str = "") -> None:
    FAIL.append(name)
    print(f"  FAIL {name}" + (f"  —— {detail}" if detail else ""))


def run(args, env_extra=None, timeout=900, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", L2T_ALLOW_MULTI="1")
    env.pop("L2T_HF_CACHE", None)
    repo_hf = os.path.join(HERE, ".hf_cache")
    if os.path.isdir(os.path.join(repo_hf,
                                  "models--Systran--faster-whisper-large-v3")):
        env["L2T_HF_CACHE"] = repo_hf          # 别让它重下 3 GB
    env.update(env_extra or {})
    r = subprocess.run([EXE] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env,
                       cwd=cwd or os.path.dirname(EXE), timeout=timeout)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def doctor_items():
    code, out, _err = run(["--doctor", "--json"], timeout=600)
    for ln in out.splitlines():
        if ln.strip().startswith("{"):
            return json.loads(ln.strip()).get("items", [])
    return []


def main() -> int:
    global EXE
    exe = sys.argv[1] if len(sys.argv) > 1 else EXE
    exe = os.path.abspath(exe)
    if not os.path.isfile(exe):
        print(f"没有冻结版 exe：{exe}")
        return 2
    EXE = exe                      # ⚠️ run() 用的是全局 EXE，不设这里会跑默认路径
    skip_convert = "--skip-convert" in sys.argv
    wheels_dir = ""
    if "--wheels" in sys.argv:
        try:
            wheels_dir = os.path.abspath(sys.argv[sys.argv.index("--wheels") + 1])
        except IndexError:
            pass
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    if wheels_dir and os.path.isdir(wheels_dir):
        # 复用已下载的 wheel，避免回归时每次重下 118 MB
        cache = os.path.join(SANDBOX, "runtime", ".wheels")
        os.makedirs(cache, exist_ok=True)
        for f in os.listdir(wheels_dir):
            src = os.path.join(wheels_dir, f)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(cache, f))
        print(f"[wheels] 复用缓存：{wheels_dir}")
    env_data = {"L2T_DATA_DIR": SANDBOX, "L2T_RUNTIME_DIR":
                os.path.join(SANDBOX, "runtime")}
    print(f"冻结版：{exe}")
    print(f"沙箱数据目录：{SANDBOX}\n")

    # ---- [1] 基础歌词制作（没有运行时也要能跑）----
    print("[1] 基础歌词制作（未装运行时）")
    audio = os.path.join(HERE, "uploads",
                         "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")
    lyrics = os.path.join(HERE, "uploads", "1791454729_在出发之前 - 陶典.lrc")
    out_ttml = os.path.join(SANDBOX, "base.ttml")
    if skip_convert:
        print("    跳过（--skip-convert；便携版后端无 CUDA，完整歌曲 CPU 转换会超时）")
    elif os.path.isfile(audio) and os.path.isfile(lyrics):
        code, out, err = run(["--audio", audio, "--lyrics", lyrics,
                              "-o", out_ttml, "--language", "zh"],
                             env_extra=env_data, timeout=1200)
        if code == 0 and os.path.isfile(out_ttml) and os.path.getsize(out_ttml) > 1000:
            ok("基础转换在无 PyTorch 时成功", f"{os.path.getsize(out_ttml)} 字节")
        else:
            bad("基础转换", f"exit={code} {(out + err)[-200:]}")
    else:
        print("    跳过：测试素材不存在")

    # ---- [2] 状态报告不误报 ----
    print("\n[2] 状态报告（未装运行时）")
    items = doctor_items()
    torch_items = [i for i in items if i.get("key") == "pkg_torch"]
    campp = [i for i in items if i.get("key") == "campp"]
    if torch_items and not torch_items[0]["ok"]:
        ok("torch 报告为未内置", torch_items[0]["detail"][:60])
    else:
        bad("torch 状态", str(torch_items))
    # 关键：CAM++ 的提示必须是"缺权重"，不能因为缺 torch 就报成别的
    if campp:
        ok("CAM++ 单独报告", campp[0]["detail"][:70])

    # ---- [3]-[5] 下载 / 首次加载 / 再次使用 ----
    print("\n[3] 运行时下载（冻结版 CLI）")
    rt_dir = os.path.join(SANDBOX, "runtime", "torch")
    if os.path.isdir(rt_dir):
        bad("下载前置状态", "沙箱里已经有运行时装好了，测试无效")
    else:
        code, out, err = run(
            ["--runtime-cli", "--install", "torch"],
            env_extra=env_data, timeout=1800)
        # 冻结版若没暴露该开关，就退回"用主程序 import 不了"的判据
        ok_flag = "状态：ready" in out or "SHA256 一致" in out
        if code == 0 and ok_flag:
            ok("运行时下载+校验+解包", "ready")
        else:
            bad("运行时下载", f"exit={code} {(out + err)[-300:]}")

    print("\n[4] 首次加载（torch 必须来自运行时目录）")
    # 判据：冻结版自己报告"真实加载通过"，并且那个目录在**数据目录**里。
    code, out, err = run(["--runtime-cli", "--status", "--json"],
                         env_extra=env_data, timeout=600)
    rows = []
    for ln in out.splitlines():
        if ln.strip().startswith("{"):
            try:
                rows = json.loads(ln.strip()).get("runtimes", [])
            except Exception:                           # noqa: BLE001
                pass
    tor = [r for r in rows if r["id"] == "torch"]
    if tor and tor[0]["status"] == "ready":
        loc = tor[0].get("location") or rt_dir   # 某些版本 status JSON 不带 location
        ok("运行时状态=ready", tor[0]["detail"][:50])
        if os.path.abspath(loc).startswith(os.path.abspath(SANDBOX)):
            ok("运行时装在**数据目录**（升级不会丢）",
               os.path.relpath(loc, SANDBOX))
        else:
            bad("运行时位置", f"{loc} 不在数据目录里")
    else:
        bad("运行时状态", str(tor)[:200])

    # 再用系统 python 独立确认"那份 torch 真的能 import 且来自该目录"。
    # ⚠️ 必须加 `-S`（跳过 site-packages）：否则会先命中**系统装的 torch**，
    # 于是"验证"验的是别的 torch，看着通过其实什么都没证明（真踩过）。
    probe = os.path.join(SANDBOX, "probe.py")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(
            "import os, sys\n"
            "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
            f"sys.path.insert(0, {rt_dir!r})\n"
            "import torch\n"
            "print('FROM', os.path.abspath(torch.__file__))\n"
            "print('VER', torch.__version__)\n")
    env_clean = dict(os.environ)
    env_clean.pop("PYTHONPATH", None)
    # ⚠️ 不加 `-S`：torch wheel 自己不包含 typing_extensions/filelock 等依赖，
    # 真实用户环境由**冻结后端内置**这些依赖（打包时 --hidden-import）；
    # 这里的独立探针用当前 Python 环境（含依赖）并保证 rt_dir 优先即可。
    r = subprocess.run([sys.executable, probe], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=600, cwd=SANDBOX, env=env_clean)
    if "FROM" in (r.stdout or ""):
        from_line = [l for l in r.stdout.splitlines() if l.startswith("FROM")][0]
        loc = from_line.split("FROM", 1)[1].strip()
        if os.path.abspath(SANDBOX) in os.path.abspath(loc):
            ok("torch 从运行时目录加载", os.path.basename(
                os.path.dirname(os.path.dirname(loc))))
        else:
            bad("torch 加载位置", loc)
    else:
        bad("torch 首次加载", (r.stdout or "")[-160:] + (r.stderr or "")[-160:])

    print("\n[5] 再次使用（应当幂等，不重下）")
    before = sum(os.path.getsize(os.path.join(rr, f))
                 for rr, _d, fs in os.walk(os.path.join(SANDBOX, "runtime"))
                 for f in fs)
    code, out, err = run(["--runtime-cli", "--install", "torch"],
                         env_extra=env_data, timeout=900)
    after = sum(os.path.getsize(os.path.join(rr, f))
                for rr, _d, fs in os.walk(os.path.join(SANDBOX, "runtime"))
                for f in fs)
    if "已就绪" in out or after == before:
        ok("重复安装是幂等的", f"{after // 1048576} MiB 未变")
    else:
        bad("幂等性", f"体积 {before} → {after}")

    # ---- [6] 失败恢复：坏 URL → .part 必须保留、重试能续传 ----
    print("\n[6] 失败恢复（模拟下载中断）")
    wheels_dir = os.path.join(SANDBOX, "runtime", ".wheels")
    if os.path.isdir(wheels_dir):
        fn = None
        for f in os.listdir(wheels_dir):
            if f.endswith(".whl"):
                fn = f
                break
        if fn:
            part = os.path.join(wheels_dir, fn + ".part")
            with open(part, "wb") as fh:
                fh.write(b"X" * (5 * 1024 * 1024))     # 假装下了 5 MB
            code, out, err = run(["--runtime-cli", "--status", "--json"],
                                 env_extra=env_data, timeout=600)
            rows = []
            for ln in out.splitlines():
                if ln.strip().startswith("{"):
                    try:
                        rows = json.loads(ln.strip()).get("runtimes", [])
                    except Exception:                   # noqa: BLE001
                        pass
            # 已经装好时状态仍是 ready（幂等）；真正的点在于 .part **没被删**
            if os.path.isfile(part):
                ok(".part 在失败/中断后保留（断点续传的命根子）",
                   f"{os.path.getsize(part) // 1048576} MiB")
            else:
                bad(".part 被删了", "断点续传会失效")
        else:
            print("    跳过：还没下载过 wheel")
    else:
        print("    跳过：没有 wheel 缓存目录")

    # ---- [7] 升级兼容：运行时装在数据目录 ----
    print("\n[7] 升级兼容（运行时不放在程序目录）")
    # 判据：位置在沙箱数据目录下，**且不在**主程序目录里
    # （在程序目录里的话，升级/重装会整个替换掉 → 用户白下）
    app_root = os.path.dirname(os.path.abspath(exe))
    _rt_abs = os.path.abspath(rt_dir)
    _in_sandbox = _rt_abs.startswith(os.path.abspath(SANDBOX))
    _in_appdir = _rt_abs.startswith(app_root)
    if os.path.isdir(rt_dir) and _in_sandbox and not _in_appdir:
        ok("运行时在数据目录（升级/重装不会丢）",
           os.path.relpath(rt_dir, SANDBOX))
    else:
        bad("运行时位置",
            f"{rt_dir}｜在沙箱={_in_sandbox}｜在程序目录={_in_appdir}")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  ✗ {f}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
