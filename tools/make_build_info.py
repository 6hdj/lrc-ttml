"""生成 `desktop/resources/build_info.json`：**这份产物是从哪个 commit 打的**。

    python tools/make_build_info.py

## 为什么需要它（第 27 轮补的缺口）

第 26 轮发现交付物**过期两小时**（少了两个修复），而报告全绿 ——
因为所有测试跑的都是同一个旧产物。当时加了个"产物新鲜度"检查，
但它的判据里有一条**只能靠时间戳**，已知会漏。

根本问题是：**产物里没有任何信息说明它是从哪个 commit 打的。**
有了这个文件之后：
  * 用户报问题时可以让他跑 `--version`，一眼看到 build commit；
  * "产物新鲜度"可以改成**比 commit**（而不是比时间），不会再被时间骗；
  * 发布记录能写清"这一版对应哪个提交"。

⚠️ 这个文件里**不放构建机路径/用户名**之类的东西 —— 它会进安装包，
属于要发出去的内容（只放 commit / 分支 / 时间 / 是否干净）。

⚠️ 构建时必须**先跑这个**再跑 PyInstaller（`Lyric2TTML.spec` 会收它）；
`tools/build_release.py` 里已经串上了。
"""
from __future__ import annotations

import datetime
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "desktop", "resources", "build_info.json")


def git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                           timeout=60)
        return (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:                                   # noqa: BLE001
        return ""


def main() -> int:
    commit = git("rev-parse", "HEAD") or "unknown"
    short = git("rev-parse", "--short", "HEAD") or "unknown"
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    commit_time = git("log", "-1", "--format=%cI") or ""
    dirty = bool(git("status", "--porcelain", "--", "desktop", "*.py"))
    # 版本号从唯一来源取（desktop/__init__.py）
    version = ""
    try:
        sys.path.insert(0, HERE)
        from desktop import __version__ as version  # noqa: PLC0415
    except Exception:                                   # noqa: BLE001
        version = ""

    info = {
        "_comment": "自动生成，别手改；由 tools/make_build_info.py 写在打包之前",
        "version": version,
        "git_commit": commit,
        "git_short": short,
        "git_branch": branch,
        "git_commit_time": commit_time,
        "dirty": dirty,
        "built_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8", newline="").write(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n")

    print("=" * 74)
    print("  构建元信息（会打进安装包，供 --version 与新鲜度检查使用）")
    print("=" * 74)
    for k in ("version", "git_short", "git_branch", "git_commit_time",
              "dirty", "built_at"):
        print(f"  {k:16s}= {info[k]}")
    print(f"\n  已写出 {os.path.relpath(OUT, HERE)}")
    if dirty:
        # ⚠️ 如实写出来：带未提交改动的产物**无法对应到任何提交**，
        # 发出去之后没人能复现。这里只提示，不阻止（开发构建常见）。
        print("  ⚠️ 工作树有未提交改动 —— 这份产物不对应任何提交，"
              "**不要用它发版**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
