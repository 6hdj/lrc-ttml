"""一键下载语音识别模型（首次安装时跑一次，之后完全离线）。

    python download_model.py                 # 默认 large-v3（约 3GB，最准）
    python download_model.py --model small   # 0.5GB，先跑通
    python download_model.py --endpoint huggingface.co   # 不用镜像

为什么单独一个脚本：模型不在发布包里（3GB、GitHub 单文件上限 2GB），
而"第一次转换才发现要下载、下载还失败了"是最容易把新用户劝退的一步。
安装时就把这件事做完，之后断网也能用。

⚠️ 镜像：国内直连 huggingface.co 经常失败，默认先用 hf-mirror.com。
   两个都试过还是不行的话，脚本会告诉你怎么**手动放置**已有模型。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

# 必须在 import huggingface_hub 之前设好
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(HERE, ".hf_cache"))

ENDPOINTS = ["https://hf-mirror.com", "https://huggingface.co"]


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def cached_size(repo: str) -> int:
    from asr import DEFAULT_CACHE_DIR
    cache = os.environ.get("HUGGINGFACE_HUB_CACHE") or DEFAULT_CACHE_DIR
    d = os.path.join(cache, "models--" + repo.replace("/", "--"), "snapshots")
    total = 0
    if os.path.isdir(d):
        for r, _, fs in os.walk(d):
            for f in fs:
                try:
                    total += os.path.getsize(os.path.join(r, f))
                except OSError:
                    pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="下载语音识别模型")
    ap.add_argument("--model", default="large-v3",
                    help="large-v3(默认) / medium / small / base / tiny")
    ap.add_argument("--endpoint", default="", help="指定 HuggingFace 端点")
    args = ap.parse_args()

    from asr import _REPO_MAP
    repo = _REPO_MAP.get(args.model, args.model)

    print("=" * 70)
    print(f"  下载语音识别模型：{args.model}")
    print(f"  仓库：{repo}")
    print(f"  缓存：{os.environ['HUGGINGFACE_HUB_CACHE']}")
    print("=" * 70)

    have = cached_size(repo)
    if have > 50 * 1024 * 1024:
        print(f"\n  [OK] 已经下载过（{human(have)}），跳过。")
        return 0

    from huggingface_hub import snapshot_download
    endpoints = [args.endpoint] if args.endpoint else ENDPOINTS
    last_err = None
    for ep in endpoints:
        os.environ["HF_ENDPOINT"] = ep
        print(f"\n  尝试端点：{ep}")
        t0 = time.time()
        try:
            path = snapshot_download(repo_id=repo, resume_download=True)
            size = cached_size(repo)
            print(f"\n  [OK] 下载完成：{path}")
            print(f"       体积 {human(size)}，用时 {time.time() - t0:.0f} 秒")
            print("\n  现在开始可以**完全离线**使用了。")
            return 0
        except KeyboardInterrupt:
            print("\n  已中断。重跑本脚本会**断点续传**，不用从头下。")
            return 1
        except Exception as e:
            last_err = e
            print(f"  [失败] {type(e).__name__}: {str(e)[:160]}")

    print("\n" + "=" * 70)
    print("  两个端点都失败了。三种办法：")
    print("=" * 70)
    print(f"""
  ① 换个网络/挂代理后重跑本脚本（支持断点续传）：
       python download_model.py --model {args.model}

  ② 手动放置已有模型（最快，不用重新下载）：
     把别处的这个目录
         .hf_cache\\models--{repo.replace('/', '--')}
     整个复制到
         {os.environ['HUGGINGFACE_HUB_CACHE']}\\
     Windows 上也可以用目录联接，秒完成、不占额外磁盘：
         mklink /J "{os.environ['HUGGINGFACE_HUB_CACHE']}\\models--{repo.replace('/', '--')}" "旧目录\\.hf_cache\\models--{repo.replace('/', '--')}"

  ③ 先用小模型跑通（体积小得多，下载快）：
       python download_model.py --model small      （约 0.5GB）
     然后在界面里把「Whisper 模型」也选成 small。

  最后一条错误：{type(last_err).__name__}: {str(last_err)[:200]}
""")
    return 1


if __name__ == "__main__":
    sys.exit(main())
