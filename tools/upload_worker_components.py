"""把 7 个 Worker 组件包上传到 **ModelScope 魔搭**公开仓库，并回传验证。

## 你要跑的三条命令

```powershell
# ① 自检（不联网、不上传）：确认清单/文件/token/SDK 都对
.venv\\Scripts\\python.exe tools\\upload_worker_components.py --dry-run

# ② 先传**一个**代表性组件试水（最大那片 442MB，最能暴露问题）
$env:MODELSCOPE_API_TOKEN="<在 https://modelscope.cn/my/myaccesstoken 复制>"
.venv\\Scripts\\python.exe tools\\upload_worker_components.py --only cuda-math

# ③ 回传验证（真的下载回来逐字节比对 SHA256）；通过后再传其余 6 个
.venv\\Scripts\\python.exe tools\\upload_worker_components.py --verify-only --only cuda-math
.venv\\Scripts\\python.exe tools\\upload_worker_components.py
.venv\\Scripts\\python.exe tools\\upload_worker_components.py --verify-only
```

## ⚠️ 这一版修掉了两个**会直接报错**的 API 误用（2026-10-15 用 SDK 1.40.2 核对）

| 我原来写的 | 实际签名 | 后果 |
| --- | --- | --- |
| `api.login(access_token=token)` | `login(self, token: str)` | **TypeError** |
| `api.upload_file(model_id=…, file_path=…)` | `upload_file(repo_id=…, repo_type=…, path_or_fileobj=…, path_in_repo=…)` | 参数被 `**kwargs` 吞掉 → 上传失败 |

核对方式：`inspect.signature` 读的是**装好的那版 SDK**，不是文档。写这类工具**必须先核对签名**。

## 为什么走 ModelScope

2026-10-15 本机真实大陆网络实测（`.tmp/free-host/`）：
* **ModelScope**：平台公开大文件 **5.91 MB/s**、`Accept-Ranges: bytes` ✓、匿名可读 ✓ → **唯一达标**
* GitHub Releases（我们**已上传** 7/7，SHA256 全对）：直连 **SSL 握手超时**、
  gh-proxy 0.06 MB/s、ghproxy.net 0.01 MB/s → 只能作权威存档/兜底
* 其余 11 个 GitHub 公益镜像：9 个 DNS 失败/超时，2 个证书错误
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMP = os.path.join(HERE, ".tmp", "worker-pkg", "components")
MANIFEST = os.path.join(HERE, "desktop", "resources", "worker_manifest.json")
# ⚠️ 命名空间必须是 **token 所属账号**：这个 token 属于 ModelScope 用户
#    Hxj345（不是 GitHub 的 6hdj）—— 用 6hdj/… 会报
#    [400] 创建模型失败，信息：record not found。账号名是从 /api/v1/login 的
#    Data.Username 读出来的，不是猜的。
DEFAULT_REPO = "Hxj345/lyric2ttml-demucs-worker"
REPO_TYPE = "model"

# ⚠️⚠️ **必须在 import modelscope / 构造 HubApi 之前设好**（2026-10-15 用户实跑踩到）：
# SDK 默认要在 `%USERPROFILE%\.modelscope` 建配置目录，用户那次报
# `PermissionError: [WinError 5] 拒绝访问` → `CacheError [E1022] Failed to create
# SDK directories`，**在建目录这一步就崩了，跟 token 完全无关**。
# SDK 支持 `MODELSCOPE_HOME`（配置）与 `MODELSCOPE_CACHE`（缓存）——
# 指到项目内可写目录就彻底绕开用户目录，也更干净。
_MS_HOME = os.path.join(HERE, ".tmp", "ms-home")
os.environ.setdefault("MODELSCOPE_HOME", _MS_HOME)
os.environ.setdefault("MODELSCOPE_CACHE", os.path.join(_MS_HOME, "cache"))
for _d in (os.environ["MODELSCOPE_HOME"], os.environ["MODELSCOPE_CACHE"]):
    try:
        os.makedirs(_d, exist_ok=True)
    except OSError as _e:                                    # noqa: BLE001
        print(f"  [warn] 建不了 SDK 目录 {_d}：{_e}（可用 MODELSCOPE_HOME 手动指定）")


def check_token(t: str) -> str:
    """校验 token 形态：返回空串 = OK，否则返回给人看的原因。

    ⚠️ 用户第一次跑直接把**占位符原文**赋给了环境变量
    （`$env:MODELSCOPE_API_TOKEN="<在 https://... 复制>"`）——
    工具必须当场说清楚，而不是拿它去请求、然后抛一个看不懂的鉴权错误。
    """
    if not t:
        return "没有设置 MODELSCOPE_API_TOKEN"
    if t.strip() != t:
        return "token 首尾有空格"
    if any(c in t for c in "<>"):
        return ("看起来是**占位符原文**（含 `<`/`>`）：要去 "
                "https://modelscope.cn/my/myaccesstoken 点复制，拿到真实 token")
    if re.search(r"[\u4e00-\u9fff]", t):
        return "token 里含中文（多半把说明文字一起复制进来了）"
    if " " in t:
        return "token 里有空格"
    if len(t) < 20:
        return f"token 太短（{len(t)} 字符），不像真的"
    return ""


def ms_url(repo: str, comp: str, filename: str) -> str:
    return f"https://modelscope.cn/models/{repo}/resolve/master/{comp}/{filename}"


def load_components() -> list:
    with open(MANIFEST, encoding="utf-8") as fh:
        return json.load(fh).get("components") or []


def pick(only: str) -> list:
    return [c for c in load_components() if not only or c["id"] == only]


# --------------------------------------------------------------------- 自检
def dry_run(repo: str, only: str) -> int:
    print("=" * 92)
    print("  Dry-run 自检（不联网、不上传）")
    print("=" * 92)
    problems = []
    cs = pick(only)
    if not cs:
        problems.append(f"没有匹配的组件：{only}")
    print(f"  仓库：{repo}  组件 {len(cs)} 个" + (f"（--only {only}）" if only else ""))
    tot = 0
    for c in cs:
        p = os.path.join(COMP, c["file"])
        if not os.path.isfile(p):
            problems.append(f"{c['id']}：缺文件 {p}")
            continue
        sz = os.path.getsize(p)
        tot += sz
        ok_sz = (sz == c["bytes"])
        if not ok_sz:
            problems.append(f"{c['id']}：本地 {sz} != 清单 {c['bytes']}")
        print(f"    {'✓' if ok_sz else '✗'} {c['id']:<16} {sz/1048576:8.1f} MB  "
              f"sha256 {c['sha256'][:16]}…")
    print(f"  合计 {tot/1048576:.1f} MB")
    token = os.environ.get("MODELSCOPE_API_TOKEN", "").strip()
    bad = check_token(token)
    print(f"  MODELSCOPE_API_TOKEN："
          + ("已设置（形态 OK）" if not bad else f"**有问题**：{bad}"))
    print(f"  SDK 目录（已改到项目内，避开用户目录权限问题）："
          f"{os.environ.get('MODELSCOPE_HOME')}")
    try:
        import modelscope
        from modelscope.hub.api import HubApi
        print(f"  modelscope SDK：{modelscope.__version__} ✓")
        sig_file = inspect.signature(HubApi.upload_file)
        sig_login = inspect.signature(HubApi.login)
        sig_create = inspect.signature(HubApi.create_repo)
        need = {"repo_id", "path_or_fileobj", "path_in_repo"}
        var_kw = any(p.kind == p.VAR_KEYWORD
                     for p in sig_file.parameters.values())
        miss = [k for k in need
                if k not in sig_file.parameters and not var_kw]
        if miss:
            problems.append(f"SDK 的 upload_file 缺参数 {miss}（版本不兼容）")
        print(f"    login{sig_login} ✓")
        print(f"    upload_file 必需参数 {sorted(need & set(sig_file.parameters))} ✓")
        print(f"    create_repo{sig_create} ✓")
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"modelscope SDK 不可用：{type(e).__name__}: {e}"
                        "（.venv\\Scripts\\python.exe -m pip install modelscope）")
    print()
    if problems:
        print("  ❌ 自检未通过：")
        for x in problems:
            print("     -", x)
        return 1
    print("  ✅ 自检通过。可以上传（先设 MODELSCOPE_API_TOKEN）。")
    return 0


# --------------------------------------------------------------------- 上传
def _wipe_credentials() -> int:
    """删掉 SDK 落盘的凭据（MODELSCOPE_HOME/credentials/*）。

    ⚠️ 为绕开用户目录权限问题，我们把 SDK 目录指到了 .tmp/ms-home；
    pi.login() 会顺手把 token/cookies/session **明文写盘**（实测落到
    credentials/git_token 等 4 个文件）。用完就清，避免 token 留在磁盘上。
    """
    n = 0
    d = os.path.join(os.environ.get("MODELSCOPE_HOME", ""), "credentials")
    if os.path.isdir(d):
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
                n += 1
            except OSError:
                pass
    return n


def upload(repo: str, only: str, commit: str) -> int:
    token = os.environ.get("MODELSCOPE_API_TOKEN", "").strip()
    bad = check_token(token)
    if bad:
        print(f"  ✗ token 不可用：{bad}\n"
              f"     获取：打开 https://modelscope.cn/my/myaccesstoken 登录后**点复制**，然后\n"
              f"     $env:MODELSCOPE_API_TOKEN=\"<粘贴真实 token>\"   # 注意：连尖括号一起去掉\n"
              f"     （我没有你的账号凭据，拿不到 token，这一步只能你来跑。）")
        return 2
    # ⚠️ SDK 的配置目录：默认在用户目录，用户那次报 WinError 5；
    #    上面已用 MODELSCOPE_HOME 指到 .tmp/ms-home，这里再兜一层明确报错。
    try:
        from modelscope.hub.api import HubApi
    except Exception as e:                                   # noqa: BLE001
        print(f"  ✗ 需要 modelscope 包（{type(e).__name__}）："
              ".venv\\Scripts\\python.exe -m pip install modelscope")
        return 2
    try:
        api = HubApi()
    except Exception as e:                                   # noqa: BLE001
        print(f"  ✗ 初始化 ModelScope SDK 失败：{type(e).__name__}: {str(e)[:200]}\n"
              f"     多为**配置目录不可写**。已尝试 MODELSCOPE_HOME="
              f"{os.environ.get('MODELSCOPE_HOME')}；可手动换一个可写目录再跑。")
        return 2
    try:
        api.login(token)                   # ⚠️ 参数名是 token，不是 access_token
    except Exception as e:                                   # noqa: BLE001
        msg = str(e)
        hint = ("token 无效/已过期 → 重新到 https://modelscope.cn/my/myaccesstoken 复制"
                if ("AccessToken" in msg or "登录失败" in msg or "Authentication" in msg)
                else "网络或账号问题")
        print(f"  ✗ 登录 ModelScope 失败（{hint}）\n"
              f"     {type(e).__name__}: {msg.splitlines()[0][:180] if msg else ''}")
        return 2
    try:
        # ⚠️ `exist_ok=True`：SDK 1.40.2 的 create_repo 支持它；不加的话
        #    仓库已存在时会抛错（我第一版就没加 —— 靠 --dry-run 打印真实签名才发现）。
        api.create_repo(repo_id=repo, repo_type=REPO_TYPE, visibility=5,
                        exist_ok=True, license="apache-2.0",
                        chinese_name="Lyric2TTML Demucs Worker 组件",
                        description="Lyric2TTML 的可选人声分离运行库（Demucs Worker）"
                                    "分片组件包，供程序按需下载。")
        print(f"  仓库已就绪：{repo}")
    except TypeError:
        # 老版本 SDK 没有 exist_ok 时退一步
        api.create_repo(repo_id=repo, repo_type=REPO_TYPE, visibility=5,
                        license="apache-2.0")
        print(f"  仓库已就绪：{repo}（无 exist_ok 参数）")
    except Exception as e:                                   # noqa: BLE001
        print(f"  [提示] create_repo：{type(e).__name__}: {str(e)[:130]}"
              f"（仓库已存在可忽略）")
    cs = pick(only)
    if only:
        print(f"  ⚠️ --only 模式：只传 {only}（先试水一个）")
    ver = json.load(open(MANIFEST, encoding="utf-8"))["worker_version"]
    ok_all = True
    for c in cs:
        p = os.path.join(COMP, c["file"])
        if not os.path.isfile(p) or os.path.getsize(p) != c["bytes"]:
            print(f"  ✗ {c['id']}：文件缺失或大小不符，跳过")
            ok_all = False
            continue
        t0 = time.time()
        print(f"  上传 {c['id']:<16} {c['bytes']/1048576:8.1f} MB …", flush=True)
        try:
            api.upload_file(repo_id=repo, repo_type=REPO_TYPE,
                            path_or_fileobj=p,
                            path_in_repo=f"{c['id']}/{c['file']}",
                            commit_message=commit or f"upload {c['id']} {ver}")
            print(f"    ✓ 用时 {time.time()-t0:.0f}s")
        except Exception as e:                               # noqa: BLE001
            print(f"    ✗ 失败：{type(e).__name__}: {str(e)[:220]}")
            ok_all = False
    n = _wipe_credentials()
    print()
    print(f"  [安全] 已清除 SDK 落盘的凭据文件 {n} 个")
    print("  下一步（**必须做**：只有真实下载验证通过才能标 verified）：")
    print("     .venv\\Scripts\\python.exe tools\\upload_worker_components.py "
          f"--verify-only{' --only ' + only if only else ''}")
    return 0 if ok_all else 1


# --------------------------------------------------------------------- 回传验证
def verify(repo: str, only: str) -> int:
    urllib.request.getproxies = lambda: {}                   # type: ignore
    cs = pick(only)
    print(f"  回传验证：{repo}（{len(cs)} 个组件，**真的下载回来算 SHA256**）")
    rows, ok_all = [], True
    for c in cs:
        url = ms_url(repo, c["id"], c["file"])
        h = hashlib.sha256()
        got = 0
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Lyric2TTML/desktop"})
            with urllib.request.urlopen(req, timeout=60) as r:
                read = getattr(r, "read1", None) or r.read
                while True:
                    b = read(1 << 22)
                    if not b:
                        break
                    h.update(b)
                    got += len(b)
        except Exception as e:                               # noqa: BLE001
            print(f"  ✗ {c['id']:<16} 下载失败：{type(e).__name__}: {str(e)[:110]}")
            rows.append({"id": c["id"], "ok": False, "error": str(e)[:110]})
            ok_all = False
            continue
        d = h.hexdigest()
        same = (d.lower() == c["sha256"].lower())
        ok = same and (got == c["bytes"])
        ok_all = ok_all and ok
        dt = max(1e-3, time.time() - t0)
        print(f"  {'✓' if ok else '✗'} {c['id']:<16} {got/1048576:7.1f}/"
              f"{c['bytes']/1048576:.1f} MB  {dt:5.0f}s  "
              f"{got/dt/1048576:5.2f} MB/s  sha256 {'一致' if same else '不一致'}")
        rows.append({"id": c["id"], "ok": ok, "bytes": got,
                     "seconds": round(dt, 1), "mbps": round(got / dt / 1048576, 2),
                     "sha256": d, "url": url})
    out = os.path.join(HERE, ".tmp", "free-host", "ms-verify.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"repo": repo, "date": time.strftime("%Y-%m-%d %H:%M:%S%z"),
               "all_ok": ok_all, "rows": rows},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print()
    if ok_all and rows:
        print("  ✅ 全部回传比对通过。可以把清单里 modelscope 源改成 verified —— 待填：")
        print(json.dumps([{"id": "modelscope",
                           "label": f"ModelScope 魔搭（{repo}）",
                           "status": "verified", "verified": True,
                           "note": f"真实回传验证通过 {time.strftime('%Y-%m-%d')}"}],
                         ensure_ascii=False, indent=2))
    else:
        print("  ❌ 有组件回传不一致 ⇒ **不要**标 verified，先查上传完整性。")
    print(f"  明细 → {out}")
    return 0 if (ok_all and rows) else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="上传 Lyric2TTML Worker 组件到 ModelScope 并回传验证")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--only", default="", help="只处理某个组件 id（先试水用）")
    ap.add_argument("--dry-run", action="store_true", help="只自检，不联网不上传")
    ap.add_argument("--verify-only", action="store_true",
                    help="只做回传验证（真的下载回来比对 SHA256）")
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    if a.dry_run:
        return dry_run(a.repo, a.only)
    if a.verify_only:
        return verify(a.repo, a.only)
    return upload(a.repo, a.only, a.commit)


if __name__ == "__main__":
    sys.exit(main())
