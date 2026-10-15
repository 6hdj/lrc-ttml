"""**交付产物是不是过期了**：源码改过、却忘了重新打包。

    python tools/check_artifact_freshness.py            # 人看
    python tools/check_artifact_freshness.py --json     # 给交付报告用

## 为什么必须有这个检查（本项目的真实事故）

`uninstall_core.py` / `desktop/*` / `installer_gui.py` 这些是**编译进
`Lyric2TTML.exe`** 的。改完之后**只重打安装包**、不重建主程序，
产物里就还是旧代码 —— 而所有测试都会"通过"，因为它们跑的是同一个旧产物。

本仓真踩过两次：
* 改了卸载逻辑只重打安装包 → 白查两轮；
* 第 26 轮：`welcome.py` / `runtime_service.py` / `installer_gui.py`
  改了十几处，`dist/安装程序.exe` 却是两小时前的 —— **交付物缺了
  「首次启动引导」和「运行组件原子性/取消」两个修复**，而报告全绿。

可怕之处在于**没有任何测试会失败**：测试验的是"产物能用"，
而产物是旧的、其中一部分确实还能用。唯一能发现它的是**比较状态**。

## 判据（踩过一次坑后改成"看 git"）

第一版用**时间戳**（源码 mtime 必须早于产物）。它**误报**了：
`desktop/pages/home.py` 被"碰过但内容没改"（跑测试时被 py_compile 之类
重写过），mtime 变成当前时间 ⇒ 报"产物过期"，而该文件相对 git 毫无改动。
**误报比不检查更糟**：会让人去重建一个根本不需要重建的产物。

现在改成看 **git 状态**，它不受"碰一下"影响：

1. 产物存在；
2. **打进产物的源码没有未提交改动**（`git status --porcelain` 对这些路径为空）
   —— 有未提交改动，说明产物**不可能**包含它们，必须重新打包；
3. 产物 mtime 不早于 **HEAD 提交时间** —— 防止"改了、提交了、忘了打包"。

⚠️ 已知边界：产物**没有**记录"它是从哪个 commit 打的"，所以
"提交 A 打完包 → 又提交 B（未打包）"这种情况，判据 3 能抓（HEAD 时间更新了）；
但如果 B 的提交时间早于产物（回退提交等极端情况），可能漏。
真要根治得把 commit 号写进产物（`--version-file`/资源里），本轮未做。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCE_DIRS = ["desktop"]
ROOT_PY = [
    "main.py", "asr.py", "launcher.py", "pipeline.py", "ttml_writer.py",
    "ttml_check.py", "installer_gui.py", "uninstall_core.py", "doctor.py",
    "envcheck.py", "web_server.py", "edit_api.py", "edit_text.py",
    "preprocess.py", "matcher.py", "diarize.py", "tail_extend.py",
    "speaker_campplus.py", "separate.py", "textread.py", "install.py",
    "win_integration.py", "ttml_reader.py", "ttml_model.py",
    "ttml_normalize.py", "selftest.py", "Lyric2TTML.spec",
]
ARTIFACTS = [
    ("主程序", os.path.join(HERE, "dist", "Lyric2TTML", "Lyric2TTML.exe")),
    ("安装程序", os.path.join(HERE, "dist", "安装程序.exe")),
]


def git(*args) -> str:
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True,
                       timeout=120)
    return (r.stdout or b"").decode("utf-8", "replace").strip()


def head_time() -> float:
    ts = git("log", "-1", "--format=%ct")
    try:
        return float(ts)
    except ValueError:
        return 0.0


def head_time_packaged() -> float:
    """**最近一次改动"打进产物的文件"**的提交时间。

    ⚠️⚠️ 为什么不能用 HEAD 的时间（2026-10-15 修，假阳性）：
    判据 3 原来是"产物 mtime 不早于 **HEAD** 提交时间"。可 HEAD 经常只改了
    **不进包**的东西 —— `tools/*.py`（测试脚本）、`docs/*.md`、`AGENTS.md`。
    那些提交一动，守卫就报"产物比 HEAD 更早 → 忘了打包"，于是：
      * 我每次提交测试/文档都要白重建一遍（一次 10 分钟）；
      * 更糟的是**"狼来了"**：报得多了就会开始无视它，
        而真正"改了 `uninstall_core.py` 却没重新打包"那次就会被放过去
        —— 这个守卫的全部价值就没了。
    所以：只拿**进包的文件**的最后一次提交时间来比。

    `--` 后面那串路径 = 真正会被打进产物/安装包的东西
    （与 `dirty_sources()` 用的是同一份清单）。
    """
    out = git("log", "-1", "--format=%ct", "--",
              *ROOT_PY, *SOURCE_DIRS)
    try:
        return float(out)
    except (ValueError, TypeError):
        return head_time()          # 取不到就退回旧口径（宁可严一点）


def dirty_sources() -> list:
    """打进产物的源码里，有哪些**未提交改动**（= 产物不可能包含它们）。"""
    paths = list(ROOT_PY) + SOURCE_DIRS
    out = git("status", "--porcelain", "--", *paths)
    rows = []
    for ln in out.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        # 形如 " M desktop/pages/welcome.py" / "?? tools/x.py"
        parts = ln.split(None, 1)
        if len(parts) == 2:
            rows.append(parts[1].strip())
    return rows


def embedded_build_info() -> dict:
    """读**打进产物里的**构建元信息（它比"产物文件的时间戳"可信得多）。

    ⚠️ 为什么这是最强判据（第 27 轮补）：
    时间戳会被"碰一下"骗（上一次就误报过），git 状态也只说明"当前工作树干净"、
    不说明"产物是从这个 commit 打的"。而 `build_info.json` 是**打包前写进包里的**，
    它记的 commit 就是这份产物真正的来源 —— 直接比它和 HEAD 即可。
    先看主程序目录（真产物），再退回源码目录（开发态）。
    """
    cands = [
        os.path.join(HERE, "dist", "Lyric2TTML", "_internal", "desktop",
                     "resources", "build_info.json"),
        os.path.join(HERE, "desktop", "resources", "build_info.json"),
    ]
    for p in cands:
        if os.path.isfile(p):
            try:
                import json as _json
                d = _json.load(open(p, encoding="utf-8"))
                d["_from"] = os.path.relpath(p, HERE)
                return d
            except Exception:                           # noqa: BLE001
                pass
    return {}


def git_ok(*args) -> bool:
    """跑一条 git 命令，只看退出码（0 = 成立）。"""
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)
    return r.returncode == 0


def artifact_covers(pkg_head: str, emb: str) -> bool:
    """产物（构建于 `emb`）是否**包含了进包文件的最后一次改动**（`pkg_head`）。

    ⚠️⚠️ 这里**不能写成 `emb == pkg_head`**（2026-10-15 修，方向搞反过）：
    打包之后往往还会为**不进包**的东西再提交几次（测试、文档），
    这时 `emb` 是 `pkg_head` 的**后代** —— 产物明明包含了全部进包改动，
    却因为 `emb != pkg_head` 被判"没重建"（假阳性）。

    正确判据是**祖先关系**：`pkg_head` 必须是 `emb` 的祖先（或相等），
    即"最后一次进包改动已经在这个产物里"。
    """
    if not pkg_head or not emb:
        return False
    if emb == pkg_head:
        return True
    return git_ok("merge-base", "--is-ancestor", pkg_head, emb)


def main() -> int:
    ap = argparse.ArgumentParser(description="交付产物新鲜度")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    dirty = dirty_sources()
    # ⚠️ 时间判据用"**进包文件**的最后一次提交"，不是 HEAD（见 head_time_packaged）
    ht = head_time_packaged()
    head = git("rev-parse", "--short", "HEAD")
    # ⚠️⚠️ 判据 1（最强判据）也要比"进包文件的最后提交"，不是 HEAD。
    # 原因同 `head_time_packaged()`：只改了 `tools/test_*.py` / `docs/` 的提交
    # **不进包**，拿它跟产物里记的 commit 比必然不等 → 每次都假报"没重建"。
    pkg_head = git("log", "-1", "--format=%h", "--", *ROOT_PY, *SOURCE_DIRS).strip()
    bi = embedded_build_info()
    emb = bi.get("git_short", "")
    rows = []
    for label, p in ARTIFACTS:
        if not os.path.isfile(p):
            rows.append({"name": label, "ok": False,
                         "why": "产物不存在（还没打包？）"})
            continue
        m = os.path.getmtime(p)
        # 判据按可信度排序：先比产物里记的 commit，再退回 git 状态/时间
        if label == "主程序" and emb and pkg_head:
            if not artifact_covers(pkg_head, emb):
                ok, why = False, (f"产物里记的 commit 是 {emb}，"
                                  f"**最近一次改动进包文件**的提交是 {pkg_head}"
                                  f"（HEAD {head}） —— 改了代码没重建主程序")
            else:
                bits = []
                if dirty:
                    bits.append(f"工作树有 {len(dirty)} 个未提交改动")
                ok = not dirty
                why = ("；".join(bits) + "，产物不可能包含它们") if dirty else ""
        elif dirty:
            ok, why = False, (f"有 {len(dirty)} 个未提交改动，产物不可能包含："
                              f"{', '.join(dirty[:3])}")
        elif ht and m < ht:
            ok, why = False, ("产物比**最近一次改动进包文件**的提交更早"
                              "（改了并提交，但忘了重新打包）")
        else:
            ok, why = True, ""
        rows.append({"name": label, "path": os.path.relpath(p, HERE), "ok": ok,
                     "why": why, "exe_mtime": m, "head_time": ht,
                     "embedded_commit": emb if label == "主程序" else ""})

    if a.json:
        print(json.dumps({"dirty": dirty, "head": head,
                          "embedded": bi, "rows": rows}, ensure_ascii=False))
        return 0 if all(r["ok"] for r in rows) else 1

    fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")
    print("=" * 74)
    print("  交付产物新鲜度（源码改过就必须重新打包）")
    print("=" * 74)
    print(f"  当前 HEAD：{head or '?'}   未提交源码改动：{len(dirty)} 个"
          + (f"  {dirty[:4]}" if dirty else ""))
    if bi:
        print(f"  产物内记录的构建来源：{bi.get('_from')} → "
              f"commit {emb or '?'}（{bi.get('git_branch', '?')}）"
              f"  构建于 {bi.get('built_at', '?')}"
              + ("　⚠️ 含未提交改动" if bi.get("dirty") else ""))
    else:
        print("  产物内**没有**构建元信息（先跑 tools/make_build_info.py 再打包）")
    bad = 0
    for r in rows:
        mark = "OK  " if r["ok"] else "FAIL"
        extra = (f"产物 {fmt(r['exe_mtime'])}  HEAD {fmt(r['head_time'])}"
                 if r.get("exe_mtime") else "")
        print(f"  {mark} {r['name']:6s} {extra}  {r['why']}")
        if not r["ok"]:
            bad += 1
    print()
    if bad:
        print(f"  ❌ {bad} 个产物与源码不一致 —— 重建步骤：")
        print("     1) python tools/make_build_info.py")
        print("     2) .venvpack\\Scripts\\python.exe -m PyInstaller --noconfirm \\")
        print("          --distpath dist/_app --workpath build/_app Lyric2TTML.spec")
        print("     3) Copy-Item -Recurse dist/_app/Lyric2TTML dist/Lyric2TTML")
        print("     4) python tools/build_installer_only.py   （需 L2T_WITH_QT=1）")
        return 1
    print("  ✓ 产物与源码一致（没有忘打包）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
