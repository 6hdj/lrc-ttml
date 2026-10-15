"""网络失败时的**可照做的**提示（尤其是"配了代理但代理没开"这种情况）。

## 为什么单独做这个（2026-10-15 用户实测）

用户的下载**全部**失败，日志长这样：

    [失败] URLError: <urlopen error [WinError 10061] 由于目标计算机积极拒绝…>
    [跳过] … 的所有来源都失败，换下一个来源
    RuntimeError: cuda: 所有下载来源都失败了（试过 3 个：github.com,
                  mirrors.aliyun.com, mirrors.cloud.tencent.com）

五个不同的站点（github / 阿里云 / 腾讯云 / hf-mirror / huggingface）**同时**
报"目标计算机积极拒绝" —— 这**不可能是镜像的问题**，但软件就是这么呈现的，
用户只会以为"你们的下载源又挂了"，然后来问开发者。

真实原因：Windows 里配了系统代理 `http://127.0.0.1:65532`（`ProxyEnable=1`），
而**那个代理进程没在跑** —— 于是 urllib 把请求发给本机 65532，
系统立刻回"连接被拒绝"。请求**根本没出过这台机器**。

所以这里做两件事：
1. **判断**：失败是不是"配了代理、但代理连不上"造成的；
2. **说清楚**：给一句能照做的提示（关掉代理 / 启动代理软件 / 用离线导入），
   而不是让用户对着"连接被拒绝"猜。
"""
from __future__ import annotations

import os
import socket
import urllib.parse
import urllib.request

# 出现这些字样，基本可以断定是"连不上"而不是"服务端返回错误"
_REFUSED = ("10061", "积极拒绝", "actively refused", "connection refused",
           "拒绝连接")
_TIMEOUT = ("timed out", "超时", "10060")


def configured_proxies() -> dict:
    """当前进程认为要走哪些代理（注册表或环境变量，urllib 说了算）。"""
    try:
        return dict(urllib.request.getproxies() or {})
    except Exception:                                   # noqa: BLE001
        return {}


def proxy_alive(proxy_url: str, timeout: float = 0.8) -> bool:
    """代理地址上的端口**现在**能不能连上（不能连 = 代理没开）。"""
    try:
        u = urllib.parse.urlsplit(proxy_url if "://" in proxy_url
                                  else "http://" + proxy_url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
    except Exception:                                   # noqa: BLE001
        return False


def proxy_hint(err: BaseException | str | None = None) -> str:
    """如果是"代理连不上"导致的失败，返回一段**可照做**的提示；否则返回空串。

    ⚠️ 只在**确实能证明**是代理问题时才提示（避免误导）：
    必须同时满足 ①有代理配置 ②代理端口连不上 ③错误里有"拒绝/超时"字样。
    """
    text = str(err or "")
    low = text.lower()
    is_conn = any(k in text or k in low for k in _REFUSED + _TIMEOUT)
    if not is_conn:
        return ""
    proxies = configured_proxies()
    if not proxies:
        return ""
    # 找一个连不上的代理（http/https 通常指向同一个）
    dead = [u for u in {v for v in proxies.values() if v} if not proxy_alive(u)]
    if not dead:
        return ""
    url = dead[0]
    return (
        f"⚠️ 本机的**系统代理** {url} 连不上（端口没在监听）。\n"
        f"   现在的请求都发给了这个代理、被本机直接拒绝，所以**每个下载源都会失败** "
        f"—— 不是镜像挂了。\n"
        f"   三选一：① 关掉系统代理（设置 → 网络和 Internet → 代理 → "
        f"「使用代理服务器」关）；② 启动你的代理软件；\n"
        f"   ③ 不联网也能用：「模型管理」页支持离线导入 —— "
        f"把已下好的模型目录放进模型缓存、把 CUDA 包放进 runtime\\.wheels 再点安装。"
    )


def describe_net_env() -> str:
    """给日志/诊断用的一行网络环境摘要。"""
    proxies = configured_proxies()
    if not proxies:
        return "代理：无（直连）"
    parts = []
    for k, v in sorted(proxies.items()):
        parts.append(f"{k}={v}（{'可用' if proxy_alive(v) else '连不上'}）")
    return "代理：" + "，".join(parts)
