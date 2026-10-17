"""入口：只起 HTTP 后端（给 WinUI 3 外壳 / Lyric2TTML.Service 用）。

WinUI 外壳（`Backend/BackendHost.cs`）会：
  * 选一个空闲端口，设 `L2T_PORT` / `L2T_HOST` / `L2T_HEADLESS=1`；
  * 启动本入口（冻结后叫 `Lyric2TTML.Service.exe`）；
  * 轮询 `/api/...` 直到健康检查通过。

⚠️ 路径约定（与 launcher.py 一致）：
  * 冻结后：以 **exe 所在目录** 为 base（uploads/ outputs/ index.html 都在这里）；
  * 源码运行：以本文件目录为 base。

本入口**不导入 Qt / desktop**，所以后端包可以比桌面包小很多。
"""
from __future__ import annotations

import os
import sys


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    base = _base_dir()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("L2T_HEADLESS", "1")
    # ⚠️⚠️ 冻结版的 stdio 编码由 bootloader 在 Python 起来前就定好了，**环境变量来不及**：
    #     PYTHONIOENCODING 在这里 setdefault 只影响子进程，对本进程的 sys.stdout 无效。
    #     实测：`--model-cli check` 打印 "✓" 时直接 UnicodeEncodeError（gbk 编不了 U+2713），
    #     界面表现为"校验失败 + 乱码"。所以必须**显式重绑 stdout/stderr**。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if base not in sys.path:
        sys.path.insert(0, base)

    # ---- 数据/缓存目录自检（必须最先做：CLI 子命令也要用到）----
    # 本机限制：非白名单程序只能写自己的目录（用户实测 LocalAppData / 外部目录均 Access denied），
    # 而 PowerShell 等系统程序可以写。探针失败就把数据目录与 HF 缓存都放到**程序目录**下。
    try:
        _la = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        _probe_dir = os.path.join(_la, "Lyric2TTML")
        os.makedirs(_probe_dir, exist_ok=True)
        for _sub in ("", os.path.join("models", "hf_cache")):
            _d = os.path.join(_probe_dir, _sub) if _sub else _probe_dir
            os.makedirs(_d, exist_ok=True)
            _probe = os.path.join(_d, ".l2t_write_probe")
            with open(_probe, "w", encoding="utf-8") as _f:
                _f.write("ok")
            os.remove(_probe)
    except Exception:
        # ⚠️ 不覆盖调用方**显式**指定的目录（测试/免安装部署会用 env 指到沙箱；
        # 之前无条件覆盖，导致测试其实写进了真实安装目录）。
        _data = os.path.join(base, "data")
        if not os.environ.get("L2T_DATA_DIR"):
            os.environ["L2T_DATA_DIR"] = _data
        if not os.environ.get("L2T_HF_CACHE"):
            os.environ["L2T_HF_CACHE"] = os.path.join(_data, "models", "hf_cache")
        try:
            os.makedirs(os.environ["L2T_HF_CACHE"], exist_ok=True)
        except Exception:
            pass
        print(f"[service] LocalAppData 不可写，数据/模型缓存改用：{_data}", flush=True)

    # ASR 优先认 HUGGINGFACE_HUB_CACHE（HF 官方名）⇒ 必须与我们的缓存目录对齐。
    # 否则：模型下到 data 缓存、ASR 去 _internal\.hf_cache 找 ⇒「下好了却说没缓存」
    # （便携包实测踩到；web_server 原来的 setdefault 冻结后指向 _internal）。
    if os.environ.get("L2T_HF_CACHE"):
        os.environ["HUGGINGFACE_HUB_CACHE"] = os.environ["L2T_HF_CACHE"]
    else:
        try:
            from desktop import paths as _paths
            os.environ["HUGGINGFACE_HUB_CACHE"] = _paths.hf_cache_dir()
        except Exception:
            os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(base, ".hf_cache")

    # ---------------- CLI JSON 子命令（WinUI 的 CliBridge 直接调本 exe）----------------
    # ⚠️ 必须**真的路由**：CliBridge 的约定是"跑后端 exe + 参数，从 stdout 取 JSON"。
    # 漏了分发的话，`--model-cli json` 会被当成"没参数" ⇒ 起 HTTP 服务 ⇒ 永远拿不到 JSON。
    argv = sys.argv[1:]
    if any(a in ("--doctor", "-doctor", "doctor") for a in argv):
        import doctor
        return int(doctor.main())
    for _flag, _module in (("--model-cli", "desktop.services.model_manager"),
                           ("--runtime-cli", "desktop.services.runtime_service"),
                           ("--worker-cli", "desktop.services.worker_service")):
        if _flag in argv:
            import importlib

            rest = [a for a in argv if a != _flag]
            mod = importlib.import_module(_module)
            return int(mod._cli(rest))

    import web_server

    # web_server 用 HERE 找 index.html；UPLOAD_DIR/OUTPUT_DIR 决定读写位置
    web_server.HERE = base
    web_server.UPLOAD_DIR = os.path.join(base, "uploads")
    web_server.OUTPUT_DIR = os.path.join(base, "outputs")
    os.makedirs(web_server.UPLOAD_DIR, exist_ok=True)
    os.makedirs(web_server.OUTPUT_DIR, exist_ok=True)

    port = os.environ.get("L2T_PORT") or "8793"
    os.environ["L2T_PORT"] = str(port)
    print(f"[service] base={base} port={port} host={os.environ.get('L2T_HOST', '127.0.0.1')}", flush=True)
    web_server.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
