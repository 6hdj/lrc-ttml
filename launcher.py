"""打包版（PyInstaller 冻结）的统一入口。

双击 exe = 起 Web 界面；带参数 = 走命令行。两条路都复用原有模块，
不复制任何逻辑。

为什么要单独一个入口，而不是直接打包 ``main.py``：
``web_server`` 用 ``HERE = dirname(abspath(__file__))`` 定位 ``index.html``
和 ``uploads/``、``outputs/``。**冻结之后 `__file__` 指向包内部**
（PyInstaller 的 ``_internal``），往那儿写转换结果是错的 ——
临时目录会被清理、Program Files 下还可能是只读的。
所以这里把几个模块级路径**重定向到 exe 所在目录**（那里才是用户的数据目录）。

    Lyric2TTML.exe                    → 起 Web 界面（默认 8765，浏览器自动打开）
    Lyric2TTML.exe --web --port 9000  → 指定端口
    Lyric2TTML.exe --audio a.wav --lyrics b.lrc -o out.ttml   → 命令行转换
    Lyric2TTML.exe --help             → 命令行全部参数
"""
from __future__ import annotations

import os
import sys
import webbrowser


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def _base_dir() -> str:
    """用户数据目录：冻结时 = exe 所在目录；未冻结时 = 本文件所在目录。"""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _rehome() -> str:
    """把"数据/资源目录"从包内部改到 exe 旁边。返回该目录。"""
    base = _base_dir()
    os.chdir(base)
    if not _frozen():
        return base

    # ⚠️ **必须在 import 其它模块之前**设好模型缓存目录。
    # main.py / web_server.py / asr.py 里都是 `os.environ.setdefault(
    # "HUGGINGFACE_HUB_CACHE", os.path.join(HERE, ".hf_cache"))`，而冻结后
    # HERE 指向 _internal —— 于是 3GB 的模型会被下到**包内部**：
    # 升级/重装就丢，用户也根本找不到。放在 exe 旁边才是对的。
    hub = os.path.join(base, ".hf_cache")
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", hub)
    os.makedirs(hub, exist_ok=True)

    # web_server：HERE 决定 index.html 去哪找，UPLOAD_DIR/OUTPUT_DIR 决定写到哪
    import web_server
    web_server.HERE = base
    web_server.UPLOAD_DIR = os.path.join(base, "uploads")
    web_server.OUTPUT_DIR = os.path.join(base, "outputs")
    os.makedirs(web_server.UPLOAD_DIR, exist_ok=True)
    os.makedirs(web_server.OUTPUT_DIR, exist_ok=True)

    # 声纹/分离的权重目录（models/）也放在 exe 旁边，方便用户自己放模型
    for modname in ("speaker_campplus", "separate", "diarize", "asr"):
        mod = sys.modules.get(modname)
        if mod is None:
            continue
        for attr in ("_HERE", "HERE", "BASE_DIR", "_BASE_DIR", "DEFAULT_CACHE_DIR"):
            if hasattr(mod, attr):
                setattr(mod, attr, base if attr != "DEFAULT_CACHE_DIR" else hub)
    return base


def _banner(base: str) -> None:
    print("=" * 62)
    print("  Lyric2TTML — 歌词 + 音频 → Apple Music / AMLL 逐字 TTML")
    print("=" * 62)
    print(f"  工作目录：{base}")
    print("  uploads/ 与 outputs/ 都在这个目录下（转换结果就在 outputs/）")


def main() -> int:
    argv = sys.argv[1:]
    base = _rehome()

    # ⚠️ 先进 UTF-8 控制台模式再说话：冻结进程的 stdout 默认走控制台代码页
    # （简体中文 = GBK），而我们输出里有 ⚠️ 这类不在 GBK 里的字符，
    # 不处理就是乱码甚至直接 UnicodeEncodeError。--doctor 这种纯文本输出尤其明显。
    try:
        import main as _m
        _m.ensure_utf8_console()
    except Exception:
        pass

    # 环境体检 / 安装补全 / 下载模型：这些是"安装期"命令，直接转发，不走 argparse
    if any(a in ("--doctor", "-doctor", "doctor") for a in argv):
        import doctor
        return doctor.main()
    if any(a in ("--install", "-install", "install") for a in argv):
        import install
        rest = [a for a in argv if a not in ("--install", "-install", "install")]
        sys.argv = ["install.py"] + rest
        return install.main()
    if any(a in ("--download-model", "download-model") for a in argv):
        import download_model
        # 复用它的参数（--model small 之类）
        rest = [a for a in argv if a not in ("--download-model", "download-model")]
        sys.argv = ["download_model.py"] + rest
        return download_model.main()

    # 走命令行：有参数、且不是 --web/--port 这类界面参数
    cli_like = bool(argv) and not any(
        a in ("--web", "--gui", "--server") for a in argv)
    if cli_like:
        import main as cli
        return cli.main(argv)

    # 起 Web 界面
    import web_server
    port = int(os.environ.get("L2T_PORT", "8765"))
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            os.environ["L2T_PORT"] = str(port)
    _banner(base)
    url = f"http://127.0.0.1:{port}"
    print(f"  界面地址：{url}")
    print("  关掉这个窗口即可退出服务")
    print("=" * 62)

    # 等服务起来再开浏览器（省得用户看到"无法访问"）
    try:
        import threading
        import time
        import urllib.request

        def _open():
            for _ in range(60):
                try:
                    urllib.request.urlopen(url, timeout=1).read(64)
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(0.5)
        threading.Thread(target=_open, daemon=True).start()
    except Exception:
        pass

    web_server.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
