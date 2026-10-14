"""**方案 D 的端到端验证**：不含 CUDA 的包 + 真下载运行时 + 真转换。

    python .tmp/test_scheme_d_e2e.py

这是方案 D 唯一有说服力的证据链：
  1. 用一个**不含 CUDA** 的主程序（`dist/_app_nocuda`）；
  2. 用 `runtime_service` 从**真实 GitHub Release** 下 CUDA zip（528.8 MB，
     支持 Range）；
  3. 断言 `gpu_probe.cuda_dirs()` 能看到下载后的目录；
  4. 用那个"原本没有 CUDA"的程序**真做一次转换**，产物要能过严格校验。

⚠️ 会下 528 MB。跑之前确认网络。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

APP = os.path.join(HERE, "dist", "_app_nocuda", "Lyric2TTML")
SB = os.path.join(HERE, ".tmp", "scheme-d")
AUDIO = os.path.join(HERE, "uploads",
                     "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")
LRC = os.path.join(HERE, "uploads", "1791454729_在出发之前 - 陶典.lrc")

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def main() -> int:
    exe = os.path.join(APP, "Lyric2TTML.exe")
    print("=" * 74)
    print("  方案 D 端到端：不含 CUDA 的包 → 下运行时 → 真转换")
    print("=" * 74)
    if not os.path.isfile(exe):
        print(f"  ✗ 没有不含 CUDA 的产物：{exe}")
        print("    先打一个：L2T_NO_CUDA=1 pyinstaller --distpath dist/_app_nocuda …")
        return 2

    nvidia = os.path.join(APP, "_internal", "nvidia")
    check(not os.path.isdir(nvidia) or not os.listdir(nvidia),
          "主程序里**没有** CUDA 运行时（这是方案 D 的前提）")

    shutil.rmtree(SB, ignore_errors=True)
    os.makedirs(SB, exist_ok=True)
    env = dict(os.environ, L2T_DATA_DIR=SB, L2T_HF_CACHE=os.path.join(HERE, ".hf_cache"),
               TMP=SB, TEMP=SB)
    os.environ["L2T_DATA_DIR"] = SB

    # ---- 1. 真下载（走 runtime_service，与界面同一个入口）----
    print("\n[1] 从 GitHub Release 下载 CUDA 运行时（约 529 MB）")
    from desktop.services import runtime_service
    mgr = runtime_service.RuntimeManager()
    st0 = mgr.check("cuda")
    print(f"       下载前状态: {st0.status}  需下载 {st0.download_bytes/1048576:.1f} MB")
    check(st0.status != "ready", "下载前确实未就绪")
    t0 = time.time()
    last = {"p": -1}

    def prog(pct, msg):
        if pct is not None and abs(pct - last["p"]) >= 20:
            last["p"] = pct
            print(f"       {pct:5.1f}%  {msg[:60]}")

    try:
        st = mgr.download("cuda", log=lambda m: None, progress=prog)
    except Exception as e:                              # noqa: BLE001
        check(False, "下载成功", f"{type(e).__name__}: {str(e)[:200]}")
        print("\n" + "=" * 74)
        print(f"  通过 {_ok} 项，失败 {_fail} 项（下载失败，后面无法继续）")
        return 1
    check(st.status == "ready", f"下载并安装成功（{time.time()-t0:.0f}s）", st.detail)
    check(os.path.isfile(os.path.join(mgr.site_dir("cuda"), "bin",
                                      "cublas64_12.dll")),
          "落盘后有 cublas64_12.dll")

    # ---- 2. cuda_dirs 能看到 ----
    print("\n[2] 程序能定位到刚下的运行时")
    from desktop.services import gpu_probe
    dirs = [d.replace("\\", "/") for d in gpu_probe.cuda_dirs()]
    check(any(d.endswith("runtime/cuda/bin") for d in dirs),
          "cuda_dirs() 包含运行时的 bin 目录")

    # ---- 3. 用这个"原本没 CUDA"的程序真转换 ----
    print("\n[3] 真转换（这个包本身不含 CUDA）")
    out = os.path.join(SB, "o.ttml")
    e2 = dict(env, L2T_HF_CACHE=os.path.join(HERE, ".hf_cache"))
    r = subprocess.run([exe, "--audio", AUDIO, "--lyrics", LRC,
                        "--language", "zh", "-o", out],
                       capture_output=True, env=e2, cwd=SB, timeout=3600)
    o = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    check(r.returncode == 0, "转换成功", f"exit={r.returncode} …{o.strip()[-300:]}")
    check(os.path.isfile(out), "产物已生成")
    check("没有可用的显卡" not in o and "cublas" not in o.lower(),
          "没有出现缺 GPU 运行时的报错")
    if os.path.isfile(out):
        check(os.path.getsize(out) > 5000, "产物不是空壳",
              f"{os.path.getsize(out)} 字节")
        rc = subprocess.run([sys.executable, os.path.join(HERE, "main.py"),
                             "--check-only", out, "--strict-time"],
                            capture_output=True, cwd=HERE, timeout=600)
        check(rc.returncode == 0, "严格校验 0 错误",
              ((rc.stdout or b"") + (rc.stderr or b"")).decode("utf-8", "replace")[-200:])

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
