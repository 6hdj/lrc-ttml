"""**打包版验收**：对冻结好的 exe 跑一遍"用户会走的路"。

    python tools/accept_frozen.py dist\\Lyric2TTML\\Lyric2TTML.exe
    python tools/accept_frozen.py <exe> --audio 歌.wav --lyrics 歌.lrc   # 换素材
    python tools/accept_frozen.py <exe> --no-convert                     # 只跑快速项

为什么必须有这个脚本：**源码模式全绿，不代表打包版能用**。
本轮就是靠它抓到六个只在冻结后才出现的问题（每个都写到 AGENTS.md 里了）：

1. `runpy.run_path` 在包内部找不到 `__main__` → `--uicheck` 卡死不动；
2. 桌面版的开关（`--uicheck` / `--selftest` / `--version`）被转发给
   `main.py` 的 argparse → `unrecognized arguments`（退出码 2）；
3. 包内自带的 CAM++ 权重在 `_internal/models/` 里，
   而模型管理器和 `doctor` 都只找 exe 旁边 → 误报"模型缺失"；
4. `collect_all('PySide6')` 把整个 Addons 拖进来（多约 90MB DLL）；
5. 冻结后控制台编码不对会让 `--help` 里的 `⚠️` 直接炸；
6. `--out-dir` 这种"看起来该有"的参数其实不存在（CLI 是 `-o/--output`）。

检查项（每项都打印实际输出，失败时打印尾部而不是只说"失败"）：
  1. 包结构（_internal / models / index.html / desktop 资源在不在）
  2. `--version`
  3. `--help`（argparse + 控制台编码）
  4. `--doctor --json`（依赖 / CUDA / 模型，机器可读）
  5. `--uicheck`（**Qt 平台插件在不在** —— 最常见的打包事故）
  6. 真转换一次（可选，默认开；走的是用户会走的那条路）
  7. 用打包版自己 `--check-only --strict-time` 严格校验产物

⚠️ 音频/歌词必须是**绝对路径**：脚本用 `cwd=exe 目录` 跑子进程，
相对路径会被 exe 当成"相对自己"，报"找不到音频文件"
（第一次跑就踩了，看着像打包版坏了）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_AUDIO = os.path.join(
    HERE, "uploads", "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")
DEFAULT_LYRICS = os.path.join(HERE, "uploads", "1791454729_在出发之前 - 陶典.lrc")

FAIL: list = []


def _run(exe: str, base: str, label: str, args, timeout: float = 900,
         env_extra=None, expect=0):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    t0 = time.time()
    try:
        p = subprocess.run([exe] + list(args), capture_output=True,
                           timeout=timeout, cwd=base, env=env)
    except subprocess.TimeoutExpired:
        print(f"  FAIL {label}：超时（>{timeout:.0f}s）—— 卡住不退出也算失败")
        FAIL.append(label)
        return None
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    good = (expect is None) or (p.returncode == expect)
    print(f"  {'OK  ' if good else 'FAIL'} {label}  exit={p.returncode}  "
          f"{time.time() - t0:.1f}s")
    if not good:
        FAIL.append(label)
        for name, body in (("stdout", out), ("stderr", err)):
            tail = body.strip().splitlines()[-10:]
            if tail:
                print(f"    --- {name} ---")
                for ln in tail:
                    print("     ", ln[:160])
    return out, err, p.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包版（冻结 exe）验收")
    ap.add_argument("exe", nargs="?", default=os.path.join(
        HERE, "dist", "Lyric2TTML", "Lyric2TTML.exe"))
    ap.add_argument("--audio", default=DEFAULT_AUDIO)
    ap.add_argument("--lyrics", default=DEFAULT_LYRICS)
    ap.add_argument("--no-convert", action="store_true",
                    help="跳过真转换（只跑快速项）")
    ap.add_argument("--timeout", type=float, default=1800,
                    help="单次转换的超时秒数（默认 1800）")
    args = ap.parse_args(argv)

    exe = os.path.abspath(args.exe)
    if not os.path.isfile(exe):
        print(f"找不到 exe：{exe}")
        return 2
    base = os.path.dirname(exe)
    print("exe:", exe)
    print("包结构：")
    for name in ("_internal", os.path.join("_internal", "models"),
                 os.path.join("_internal", "index.html"),
                 os.path.join("_internal", "desktop")):
        p = os.path.join(base, name)
        print(f"  {'OK  ' if os.path.exists(p) else 'MISS'} {name}")
        if not os.path.exists(p):
            FAIL.append(f"missing:{name}")
    if os.path.isdir(os.path.join(base, "_internal")):
        total = sum(os.path.getsize(os.path.join(r, f))
                    for r, _d, fs in os.walk(base) for f in fs
                    if os.path.isfile(os.path.join(r, f)))
        print(f"  体积：{total / 1024 ** 3:.2f} GB")

    print("\n=== 1. --version ===")
    _run(exe, base, "--version", ["--version"])

    print("=== 2. --help（控制台编码 + argparse）===")
    _run(exe, base, "--help", ["--help"])

    print("=== 3. --doctor --json ===")
    res = _run(exe, base, "--doctor --json", ["--doctor", "--json"], timeout=900)
    if res and res[0]:
        try:
            rep = json.loads(res[0][res[0].index("{"):])
            items = rep.get("items") or []
            bad = [i for i in items if not i.get("ok")]
            print(f"    体检 {len(items)} 项，未通过 {len(bad)} 项")
            for i in bad:
                print(f"      ! {i.get('name')}: {str(i.get('detail'))[:100]}")
        except Exception as e:                          # noqa: BLE001
            print(f"    JSON 解析失败：{e}")
            FAIL.append("doctor-json")

    print("=== 4. --uicheck（Qt 平台插件）===")
    # ⚠️⚠️ **必须把模型缓存指到已有的 `.hf_cache`**，否则这一轮会去
    # **重新下载 3 GB 的 whisper 模型**：`L2T_DATA_DIR` 一改，模型缓存
    # 目录也跟着变（默认在数据目录下），本地那 3 GB 就"看不见"了。
    #
    # 这个坑很隐蔽：表现是"真转换要 573 秒"，看起来像 **CPU 静默慢跑**，
    # 于是会去查 CUDA —— 其实是在下载模型（真踩过，白查了一轮）。
    # 所以：有现成的 `.hf_cache` 就显式传 `L2T_HF_CACHE`。
    env = {"QT_QPA_PLATFORM": "offscreen", "L2T_ALLOW_MULTI": "1",
           "L2T_HEADLESS": "1",
           "L2T_DATA_DIR": os.path.join(base, ".accept-data")}
    _repo_hf = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".hf_cache")
    if os.path.isdir(os.path.join(
            _repo_hf, "models--Systran--faster-whisper-large-v3")):
        env["L2T_HF_CACHE"] = _repo_hf
        print(f"    （复用已有模型缓存：{_repo_hf}）")
    else:
        print("    ⚠️ 仓库里没有 .hf_cache —— 这一轮会**真的下载 3 GB 模型**，"
              "因此耗时里包含下载时间（不代表转换慢）")

    # ⚠️⚠️ 第 19 轮起**默认安装包不含 CUDA 运行时**（方案 D：717.7 MB → 189.9 MB），
    # 而语音识别**没有它就跑不了**（`asr.py` 明确拒绝 CPU）。
    # 所以验收脚本要在这里**先把它装上**，否则第 5 步真转换必然失败，
    # 而失败原因看起来像"打包版坏了"（其实是"还没下运行时"）。
    # 这也正是真实用户第一次跑会遇到的情形 —— 顺带把这条链路一起验了。
    print("\n=== 4.5 确保 GPU 运行时可用（按需下载，约 529 MB 一次性）===")
    _rt_bin = os.path.join(base, ".accept-data", "runtime", "cuda", "bin")
    if os.path.isfile(os.path.join(_rt_bin, "cublas64_12.dll")):
        print(f"    已就绪：{_rt_bin}")
    else:
        print("    未安装 —— 通过程序自己的 runtime-cli 下载（首次约 1-2 分钟）")
        _run(exe, base, "runtime install",
             ["--runtime-cli", "--install", "cuda"], timeout=3600,
             env_extra=env, expect=None)
        ok_rt = os.path.isfile(os.path.join(_rt_bin, "cublas64_12.dll"))
        _run(exe, base, "runtime install",
             ["--runtime-cli", "--install", "cuda"], timeout=3600,
             env_extra=env, expect=0 if ok_rt else None)
        print(f"    cublas 就位={ok_rt}")
        if not ok_rt:
            print("    ⚠️ 运行时没装上 —— 第 5 步真转换会失败，且**不是**打包版的问题")

    res = _run(exe, base, "--uicheck", ["--uicheck"], timeout=600,
               env_extra=env, expect=None)
    if res:
        if res[2] != 0 or "全部通过" not in res[0]:
            FAIL.append("uicheck")

    if args.no_convert:
        print("\n（按要求跳过真转换）")
    else:
        print("=== 5. 真转换（端到端）===")
        audio = os.path.abspath(args.audio)
        lyrics = os.path.abspath(args.lyrics)
        if not (os.path.isfile(audio) and os.path.isfile(lyrics)):
            print(f"    跳过：素材不存在\n      {audio}\n      {lyrics}")
        else:
            outdir = os.path.join(base, ".accept-out")
            os.makedirs(outdir, exist_ok=True)
            # ⚠️ 必须**绝对路径**（见模块 docstring）
            ttml = os.path.join(outdir, "accept.ttml")
            # ⚠️⚠️ **必须带上完整的 `env`，不能只带 `L2T_HF_CACHE`**（真踩到）。
            # 原来这里只传 `env_extra={"L2T_HF_CACHE": …}`，于是
            # `L2T_DATA_DIR` **丢了** —— 而第 4.5 步刚把 CUDA 运行时装到了
            # `<L2T_DATA_DIR>/runtime/cuda`。数据目录一变，运行时"就不见了"，
            # 第 5 步必然报 `Library cublas64_12.dll is not found`，
            # 看起来像"打包版坏了"，其实是**测试脚本没把环境传全**。
            # 现在把 `env` 整个并进去（含 `L2T_DATA_DIR` / `QT_QPA_PLATFORM` 等）。
            _conv_env = dict(env)
            res = _run(exe, base, "convert",
                       ["--audio", audio, "--lyrics", lyrics,
                        "--language", "auto", "-o", ttml],
                       timeout=args.timeout, expect=None,
                       env_extra=_conv_env)
            if res and res[2] == 0 and os.path.isfile(ttml):
                print(f"    产物：{os.path.getsize(ttml)} 字节")
                print("=== 6. 严格校验产物（打包版 + 仓库校验器各一遍）===")
                _run(exe, base, "check-only",
                     ["--check-only", ttml, "--strict-time"], timeout=300,
                     expect=None)
                _check_only(ttml)
            else:
                FAIL.append("convert")

    print()
    if FAIL:
        print(f"未通过 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过 ✓")
    return 0


def _check_only(ttml: str) -> None:
    """独立确认产物在**仓库的**校验器下也是 0 错误（不只是打包版自己说 OK）。"""
    sys.path.insert(0, HERE)
    try:
        import textread
        from ttml_check import check_ttml
        res = check_ttml(textread.read_text(ttml), strict=True)
        if res.ok:
            print(f"  OK   仓库校验器：0 个错误（{res.summary()}）")
        else:
            print(f"  FAIL 仓库校验器：{len(res.errors)} 个错误")
            for e in res.errors[:8]:
                print("      ", e.__dict__)
            FAIL.append("repo-check")
    except Exception as e:                              # noqa: BLE001
        print(f"  （跳过仓库校验器：{type(e).__name__}: {e}）")


if __name__ == "__main__":
    sys.exit(main())
