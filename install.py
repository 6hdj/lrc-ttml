"""一键安装 / 补全：先体检，缺什么补什么。

    python install.py              # 交互式：先列清单，问过再装
    python install.py --yes        # 全自动（双击 安装.bat 走的就是这个）
    python install.py --check-only # 只看缺什么，不动任何东西
    python install.py --skip-cuda  # 跳过 2GB 的 CUDA 运行库

它会依次处理：

    1. pip 依赖        faster-whisper / numpy / pypinyin / torch …
    2. Whisper 模型    large-v3，约 3 GB（可 --model small 换小的）
    3. CUDA 运行库     cublas + cudnn，约 2 GB —— **GPU 加速必需**
    4. CAM++ 声纹权重  约 300 MB —— **对唱分声部必需**
    5. ffmpeg          可选；缺了只是个别格式解不开

--------------------------------------------------------------------------
两个设计决定，都是有原因的
--------------------------------------------------------------------------
* **CUDA 运行库不在发布包里**：cublas+cudnn 合计 2 GB，而 GitHub Release
  单文件上限就是 2 GB —— 塞不进去。所以改成**安装时下载**，解包到程序目录的
  `cuda/` 下；`asr._register_cuda_dlls()` 会把这个目录加进 DLL 搜索路径。
  好处是不用动系统、装 CUDA Toolkit，删掉 `cuda/` 就等于卸载。

* **CAM++ 权重同样不在包里**（授权 + 体积），而它缺失时 `diarize.py` 会
  **静默按单人处理** —— 表现就是"开了分声部却没分"。所以必须在这里补齐，
  并且补不上时要**大声说出来**，不能又变成静默降级。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

FROZEN = getattr(sys, "frozen", False)


def base_dir() -> str:
    return os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE


BASE = base_dir()
CUDA_DIR = os.path.join(BASE, "cuda")
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(BASE, ".hf_cache"))

# CUDA 运行库：PyPI 上的 wheel 里就是现成的 DLL，直接下 wheel 再解包，
# 比装 CUDA Toolkit 轻得多，也不用管理员权限。
CUDA_PKGS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]

CAMPP_REPO = "iic/speech_campplus_sv_zh-cn_16k-common"
CAMPP_DIR = os.path.join(BASE, "models", "campplus")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str = "") -> None:
    say("=" * 72)
    if title:
        say(f"  {title}")
        say("=" * 72)


def ask(prompt: str, default: bool = True) -> bool:
    if AUTOOYES:
        return True
    try:
        a = input(f"{prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    except EOFError:
        return default
    if not a:
        return default
    return a in ("y", "yes", "是", "好")


# =============================================================== 各项检查
def need_python() -> tuple:
    v = sys.version_info
    return ((v.major, v.minor) >= (3, 10),
            f"{v.major}.{v.minor}.{v.micro}",
            "到 python.org 装 3.10+，安装时勾选 Add Python to PATH")


REQ_PKGS = ["faster-whisper", "ctranslate2", "numpy", "huggingface-hub", "av",
            "tokenizers", "pypinyin", "torch"]


def missing_pkgs() -> list:
    import importlib
    mods = {"faster-whisper": "faster_whisper", "ctranslate2": "ctranslate2",
            "numpy": "numpy", "huggingface-hub": "huggingface_hub", "av": "av",
            "tokenizers": "tokenizers", "pypinyin": "pypinyin", "torch": "torch"}
    out = []
    for pkg in REQ_PKGS:
        try:
            importlib.import_module(mods[pkg])
        except Exception:
            out.append(pkg)
    return out


def whisper_models() -> dict:
    try:
        from asr import _REPO_MAP, DEFAULT_CACHE_DIR
    except Exception:
        return {}
    cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or DEFAULT_CACHE_DIR
    found = {}
    for name, repo in _REPO_MAP.items():
        d = os.path.join(cache, "models--" + repo.replace("/", "--"), "snapshots")
        size = 0
        if os.path.isdir(d):
            for r, _, fs in os.walk(d):
                for f in fs:
                    try:
                        size += os.path.getsize(os.path.join(r, f))
                    except OSError:
                        pass
        if size > 50 * 1024 * 1024:
            found[name] = size
    return found


def cuda_dlls_present() -> bool:
    """有没有可用的 cublas/cudnn。

    ⚠️ 两个地方都要看，只看一处会**误报"缺"**：
      * `<程序目录>/cuda/`——打包版让用户/安装脚本放 DLL 的地方；
      * `site-packages/nvidia/*/bin`——`pip install nvidia-cublas-cu12` 装的位置
        （源码运行时最常见的形态，本机就是这种）。
    """
    if os.path.isdir(CUDA_DIR):
        if any(f.lower().startswith("cublas") for f in os.listdir(CUDA_DIR)):
            return True
    for base in sys.path:
        nv = os.path.join(base or "", "nvidia")
        if not os.path.isdir(nv):
            continue
        for r, _, fs in os.walk(nv):
            if any(f.lower().startswith("cublas") and f.lower().endswith(".dll")
                   for f in fs):
                return True
    return False


def campp_present() -> bool:
    if not os.path.isdir(CAMPP_DIR):
        return False
    return any(f.endswith((".onnx", ".pt", ".bin", ".pth"))
               for r, _, fs in os.walk(CAMPP_DIR) for f in fs)


def ffmpeg_present() -> str:
    return shutil.which("ffmpeg") or ""


# =============================================================== 补全动作
def install_pkgs(pkgs: list) -> bool:
    say(f"\n  安装依赖：{' '.join(pkgs)}")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + pkgs
    say("  $ " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        say("  [失败] pip 装不上。常见原因：网络不通 / 需要代理。")
        say("         可以手动重试：python -m pip install " + " ".join(pkgs))
        return False
    return True


def fetch_whisper(model: str) -> bool:
    say(f"\n  下载 Whisper 模型：{model}")
    cmd = [sys.executable, os.path.join(BASE, "download_model.py"), "--model", model]
    return subprocess.call(cmd) == 0


def fetch_cuda() -> bool:
    """从 PyPI 下 cublas/cudnn 的 wheel，把 DLL 解到 <程序目录>/cuda/。

    不解 CUDA Toolkit、不改系统 PATH、不要管理员权限。
    """
    say(f"\n  下载 CUDA 运行库（cublas + cudnn，约 2 GB）→ {CUDA_DIR}")
    say("  这一步让语音识别能跑在显卡上；没有它只能 CPU，慢很多。")
    tmp = tempfile.mkdtemp(prefix="l2t_cuda_")
    try:
        cmd = [sys.executable, "-m", "pip", "download", "--only-binary=:all:",
               "--no-deps", "-d", tmp] + CUDA_PKGS
        say("  $ " + " ".join(cmd))
        if subprocess.call(cmd) != 0:
            say("  [失败] 拉不到 wheel。可以换镜像重试：")
            say("         python -m pip download --only-binary=:all: --no-deps "
                "-d .\\_cuda -i https://pypi.tuna.tsinghua.edu.cn/simple "
                + " ".join(CUDA_PKGS))
            return False
        os.makedirs(CUDA_DIR, exist_ok=True)
        n = 0
        for f in os.listdir(tmp):
            if not f.endswith(".whl"):
                continue
            with zipfile.ZipFile(os.path.join(tmp, f)) as z:
                for name in z.namelist():
                    if name.lower().endswith(".dll"):
                        # wheel 里是 nvidia/<lib>/bin/xxx.dll → 摊平到 cuda/
                        dst = os.path.join(CUDA_DIR, os.path.basename(name))
                        with z.open(name) as src, open(dst, "wb") as out:
                            shutil.copyfileobj(src, out)
                        n += 1
        say(f"  [OK] 解出 {n} 个 DLL → {CUDA_DIR}")
        return n > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch_campp() -> bool:
    """下载 CAM++ 声纹权重（对唱分声部必需）。"""
    say(f"\n  下载 CAM++ 声纹权重（约 300 MB）→ {CAMPP_DIR}")
    os.makedirs(CAMPP_DIR, exist_ok=True)
    # 首选 ModelScope（国内快）
    try:
        import importlib
        importlib.import_module("modelscope")
        from modelscope.hub.snapshot_download import snapshot_download
        p = snapshot_download(CAMPP_REPO, cache_dir=os.path.join(BASE, ".ms_cache"))
        copied = 0
        for r, _, fs in os.walk(p):
            for f in fs:
                if f.endswith((".onnx", ".pt", ".bin", ".pth")):
                    shutil.copy2(os.path.join(r, f), os.path.join(CAMPP_DIR, f))
                    copied += 1
        say(f"  [OK] 复制 {copied} 个权重文件")
        return copied > 0
    except ImportError:
        pass
    except Exception as e:
        say(f"  ModelScope 下载失败：{type(e).__name__}: {str(e)[:120]}")

    say("  [需要手动] 没装 modelscope，无法自动下载。两种办法：")
    say("       ① 装一下再重跑：pip install modelscope")
    say(f"       ② 手动从 ModelScope 下载 {CAMPP_REPO}")
    say(f"          把权重文件放进 {CAMPP_DIR}\\")
    say("      ⚠️ 缺了它，「对唱分声部」会退化成'按单人处理' —— 看起来像没生效。")
    return False


# =============================================================== 主流程
AUTOOYES = False


def main() -> int:
    global AUTOOYES
    ap = argparse.ArgumentParser(description="检查并补全 Lyric2TTML 需要的所有东西")
    ap.add_argument("--yes", action="store_true", help="全自动，不再逐项询问")
    ap.add_argument("--check-only", action="store_true", help="只检查，不安装")
    ap.add_argument("--skip-cuda", action="store_true", help="跳过 2GB 的 CUDA 运行库")
    ap.add_argument("--model", default="large-v3", help="Whisper 模型（默认 large-v3）")
    args = ap.parse_args()
    AUTOOYES = args.yes

    rule("Lyric2TTML 安装 / 补全")
    say(f"  程序目录：{BASE}")
    say(f"  运行方式：{'打包版（自带 Python）' if FROZEN else '源码 / 便携版'}")
    say()

    # ---------------- 1. 体检并列出缺口 ----------------
    say("  正在检查……\n")
    problems = []          # (名称, 现状, 怎么补, 补全函数或 None)

    ok, ver, fix = need_python()
    if FROZEN:
        say(f"  [OK]  Python（打包版内置 {ver}）")
    elif ok:
        say(f"  [OK]  Python {ver}")
    else:
        say(f"  [缺]  Python {ver}（需要 3.10+）")
        problems.append(("Python 3.10+", ver, fix, None))

    miss = [] if FROZEN else missing_pkgs()
    if miss:
        say(f"  [缺]  Python 依赖：{'、'.join(miss)}")
        problems.append(("Python 依赖", "、".join(miss), "用 pip 安装", install_pkgs))
    else:
        say("  [OK]  Python 依赖（faster-whisper / ctranslate2 / numpy / torch …）")

    wm = whisper_models()
    if args.model in wm:
        say(f"  [OK]  Whisper 模型 {args.model}"
            f"（{wm[args.model] / 1024 / 1024:.0f} MB）")
    else:
        have = "、".join(wm) if wm else "一个都没有"
        say(f"  [缺]  Whisper 模型 {args.model}（现有：{have}）")
        problems.append((f"Whisper 模型 {args.model}", have,
                         "联网下载一次，之后完全离线", fetch_whisper))

    if cuda_dlls_present():
        n = len([f for f in os.listdir(CUDA_DIR)
                 if f.lower().endswith(".dll")]) if os.path.isdir(CUDA_DIR) else 0
        where = CUDA_DIR if n else "site-packages/nvidia（pip 装的）"
        say(f"  [OK]  CUDA 运行库：{where}" + (f"（{n} 个 DLL）" if n else ""))
    elif args.skip_cuda:
        say("  [跳过] CUDA 运行库（--skip-cuda）")
    else:
        say("  [缺]  CUDA 运行库（cublas/cudnn，约 2 GB）—— GPU 加速必需")
        problems.append(("CUDA 运行库", "无 cublas/cudnn",
                         "从 PyPI 拉 wheel 解出 DLL（不动系统）", fetch_cuda))

    if campp_present():
        say(f"  [OK]  CAM++ 声纹权重（对唱分声部）")
    else:
        say("  [缺]  CAM++ 声纹权重（对唱分声部）—— 缺了会退化成按单人处理")
        problems.append(("CAM++ 声纹权重", "未找到",
                         "从 ModelScope 下载（需 pip install modelscope）", fetch_campp))

    ff = ffmpeg_present()
    say(f"  [{'OK' if ff else '--'}]  ffmpeg：" +
        (ff if ff else "未找到（可选；个别格式解不开时再装）"))

    if not problems:
        say()
        rule("  全部就绪，不需要补什么")
        return 0

    # ---------------- 2. 列出要补的东西 ----------------
    say()
    rule(f"  发现 {len(problems)} 项缺失")
    for i, (name, cur, how, fn) in enumerate(problems, 1):
        say(f"  {i}. {name}")
        say(f"       现状：{cur}")
        say(f"       怎么补：{how}")
        if fn is None:
            say("       ⚠️ 这一项**需要你手动处理**（程序没法代劳）")
    say()

    if args.check_only:
        say("  （--check-only：只检查，未安装任何东西）")
        return 1

    # ---------------- 3. 逐项补全 ----------------
    if not ask("开始补全？"):
        say("  已取消。")
        return 1

    done, failed = [], []
    for name, cur, how, fn in problems:
        if fn is None:
            failed.append((name, "需要手动处理"))
            continue
        okk = fn(args.model) if fn is fetch_whisper else fn()
        (done if okk else failed).append(
            (name, "" if okk else "安装失败，见上面的提示"))

    # ---------------- 4. 复检 ----------------
    say()
    rule("  结果")
    for name, _ in done:
        say(f"  [补好] {name}")
    for name, why in failed:
        say(f"  [未完成] {name}  {why}")
    say()
    say("  建议再跑一次体检确认：")
    say(f"      python {os.path.join('tools', '..', 'doctor.py')}")
    say("   或双击「检查环境.bat」")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
