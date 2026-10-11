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
def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def main_exe() -> str:
    """旁边的主程序。**没有它就不许补全** —— 见 install_pkgs 的说明。"""
    p = os.path.join(BASE, "Lyric2TTML.exe")
    return p if os.path.exists(p) else ""


def install_pkgs(pkgs: list) -> bool:
    """安装 pip 依赖。

    ⚠️⚠️ **冻结（打包）之后绝对不能调 `sys.executable -m pip`**：
    PyInstaller 里 ``sys.executable`` 就是**这个安装程序自己**，
    于是 "pip install ..." 变成重新启动自己 —— 用户点一次「开始补全」
    就弹出一个新的安装程序窗口，点一次弹一次，看起来像病毒（真踩过）。

    而且打包版本来就把核心依赖（faster-whisper / ctranslate2 / numpy / av /
    tokenizers）**内置**了，根本不需要 pip；缺的只是 torch / demucs 这类可选件。

    所以：冻结时**什么都不做**，只把话说清楚。
    """
    if _frozen():
        say("  打包版已内置核心依赖，不需要 pip 安装。")
        say(f"  未内置的（{'、'.join(pkgs)}）属于可选功能，"
            f"要完整功能请用源码版：pip install -r requirements.txt")
        return True
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
    """下载 Whisper 模型。

    **交给主程序做**（`Lyric2TTML.exe --download-model`），不要在安装程序里自己下：
    主程序知道自己的缓存目录在哪（`<exe>/.hf_cache`），而且它内置了 huggingface_hub。
    安装程序是另一个进程，自己下的位置很可能跟主程序找的位置对不上。
    """
    say(f"\n  下载 Whisper 模型：{model}")
    exe = main_exe()
    if not exe:
        say("  [失败] 旁边没有找到 Lyric2TTML.exe，无法确定该下到哪里。")
        say("         请把「安装程序.exe」放进主程序所在的那个文件夹里再运行。")
        return False
    return subprocess.call([exe, "--download-model", "--model", model]) == 0


def fetch_cuda() -> bool:
    """下载 CUDA 运行库（cublas / cudnn）。

    ⚠️ **不用 pip**：直接向 PyPI 取 wheel 的下载地址、用 urllib 拉下来、
    用 zipfile 解出 DLL。原因是 pip 在冻结环境里没法安全调用
    （`sys.executable` 指向安装程序自己），而 wheel 本质就是个 zip，
    自己解反而更可控、还能显示进度。
    """
    say(f"\n  下载 CUDA 运行库（cublas + cudnn，约 2 GB）→ {CUDA_DIR}")
    say("  这一步让语音识别能跑在 GPU 上；没有它只能 CPU，慢很多。")
    import json as _json
    import urllib.request
    os.makedirs(CUDA_DIR, exist_ok=True)
    n = 0
    for pkg in CUDA_PKGS:
        try:
            say(f"    查询 {pkg} 的最新版本…")
            with urllib.request.urlopen(
                    f"https://pypi.org/pypi/{pkg}/json", timeout=30) as r:
                meta = _json.loads(r.read().decode("utf-8", "replace"))
            ver = meta["info"]["version"]
            url = ""
            for f in meta["releases"].get(ver, []):
                fn = f.get("filename", "")
                if fn.endswith(".whl") and "win_amd64" in fn:
                    url = f["url"]
                    break
            if not url:
                say(f"    [失败] {pkg} {ver} 没有 Windows 版 wheel")
                continue
            say(f"    下载 {os.path.basename(url)} …")
            tmp = os.path.join(CUDA_DIR, "_tmp.whl")
            with urllib.request.urlopen(url, timeout=120) as r, \
                    open(tmp, "wb") as out:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                step = 8 * 1024 * 1024
                while True:
                    chunk = r.read(step)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if total:
                        say(f"      {got / 1024 / 1024:.0f} / "
                            f"{total / 1024 / 1024:.0f} MB")
            with zipfile.ZipFile(tmp) as z:
                for name in z.namelist():
                    if name.lower().endswith(".dll"):
                        # wheel 内是 nvidia/<lib>/bin/xxx.dll → 摊平到 cuda/
                        dst = os.path.join(CUDA_DIR, os.path.basename(name))
                        with z.open(name) as src, open(dst, "wb") as out:
                            shutil.copyfileobj(src, out)
                        n += 1
            os.remove(tmp)
            say(f"    [OK] {pkg} 解出 DLL")
        except Exception as e:
            say(f"    [失败] {pkg}：{type(e).__name__}: {str(e)[:140]}")
    say(f"  [{'OK' if n else '失败'}] 解出 {n} 个 DLL → {CUDA_DIR}")
    if not n:
        say("  可以换镜像手动下：https://mirrors.aliyun.com/pypi/simple/ 搜 "
            + "、".join(CUDA_PKGS))
        say(f"  把 wheel 解出来的 DLL 放进 {CUDA_DIR}\\ 即可")
    return n > 0


def _http_get(url: str, dst: str, timeout: int = 120) -> bool:
    """带进度、带**字节校验**的下载。

    ⚠️ 以前是"读到空就算下载完" —— 连接中途断掉会被当成成功，
    然后拿一个残缺文件去解压，报 `BadZipFile: File is not a zip file`。
    真踩过：cublas 528MB 只下了 489MB、cudnn 709MB 只下了 478MB。
    现在必须 **实收字节数 == Content-Length**，否则算失败。
    """
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Lyric2TTML"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dst, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(4 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                if total:
                    say(f"      {got / 1024 / 1024:.0f} / {total / 1024 / 1024:.0f} MB")
        if total and got != total:
            say(f"    [失败] 只下到 {got} 字节，应为 {total}（连接被截断）")
            return False
        return got > 0
    except Exception as e:
        say(f"    [失败] {type(e).__name__}: {str(e)[:120]}")
        return False


# CAM++ 权重的候选下载地址：**一个不通就换下一个**，不能只有一条路。
CAMPP_SOURCES = [
    # ModelScope 的 HTTP 直取接口（不需要装 modelscope 包）
    ("ModelScope 官方",
     "https://www.modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh-cn_16k-common"
     "/repo?Revision=master&FilePath={f}"),
    ("ModelScope 镜像",
     "https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh-cn_16k-common"
     "/repo?Revision=master&FilePath={f}"),
]
CAMPP_FILES = ["campplus_cn_common.bin", "configuration.json", "config.yaml"]


def fetch_campp() -> bool:
    """下载 CAM++ 声纹权重（对唱分声部必需）。

    **多路尝试**：ModelScope 官方接口 → ModelScope 镜像 → 已安装的 modelscope 包。
    任何一条路成功即可。全失败时把每条路的结果都打出来，用户知道该找谁。
    """
    say(f"\n  下载 CAM++ 声纹权重（约 300 MB）→ {CAMPP_DIR}")
    os.makedirs(CAMPP_DIR, exist_ok=True)

    # ① 直连 HTTP（不需要额外装任何包）
    for label, tpl in CAMPP_SOURCES:
        say(f"    尝试 {label} …")
        ok_all = True
        for f in CAMPP_FILES:
            dst = os.path.join(CAMPP_DIR, f)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                continue
            if not _http_get(tpl.format(f=f), dst):
                ok_all = False
                break
        if ok_all and any(
                fn.endswith(".bin") for fn in os.listdir(CAMPP_DIR)):
            say(f"    [OK] {label} 下载成功")
            return True
        say(f"    [{label} 这条路不通]")

    # ② 退回 modelscope 包（源码模式下才有意义）
    try:
        import importlib
        importlib.import_module("modelscope")
        from modelscope.hub.snapshot_download import snapshot_download
        say("    尝试 modelscope 包 …")
        p = snapshot_download(CAMPP_REPO,
                              cache_dir=os.path.join(BASE, ".ms_cache"))
        n = 0
        for r, _, fs in os.walk(p):
            for f in fs:
                if f.endswith((".onnx", ".pt", ".bin", ".pth", ".json", ".yaml")):
                    shutil.copy2(os.path.join(r, f), os.path.join(CAMPP_DIR, f))
                    n += 1
        if n:
            say(f"    [OK] 复制 {n} 个文件")
            return True
    except ImportError:
        say("    （没装 modelscope 包，跳过这条路）")
    except Exception as e:
        say(f"    [失败] modelscope 包：{type(e).__name__}: {str(e)[:100]}")

    say("  [失败] 三条路都没走通。")
    say(f"    请手动把 campplus_cn_common.bin 放到：{CAMPP_DIR}\\")
    say("    来源：https://www.modelscope.cn/models/"
        "iic/speech_campplus_sv_zh-cn_16k-common/files")
    say("    ⚠️ 缺了它，「对唱分声部」会退化成按单人处理 —— 看起来像没生效")
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
