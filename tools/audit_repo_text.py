"""仓库发布前核查：不宜开源的内容 + 文档与代码的一致性。

    python tools/audit_repo_text.py

两类问题分开报：
  A. **不宜开源**：个人路径、凭据、真实商业歌曲名/歌词、本地盘符目录、临时脚本
  B. **文案过时**：文档里还在说代码里已经不存在的东西（比"写得不好"严重得多 ——
     照着文档操作会失败）
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)

# ---- A 类：不宜开源 ----
A_PATTERNS = [
    ("个人绝对路径", re.compile(r"[A-Za-z]:\\+Users\\+[^\\\s\"']+")),
    ("个人绝对路径", re.compile(r"E:\\+HXJ")),
    ("个人绝对路径", re.compile(r"E:\\+songs")),
    ("凭据", re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,})")),
    ("凭据字段", re.compile(r"(?i)\b(api[_-]?key|passwd|password|access[_-]?token)"
                            r"\s*[:=]\s*[\"'][^\"']{8,}")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
]
# 允许出现的（审计脚本自己的正则、示例邮箱、伪造测试路径）
A_ALLOW_FILES = ("tools/audit_public_ready.py", "tools/audit_repo_text.py",
                 "AGENTS.md", "CHANGELOG.md")
A_ALLOW_TEXT = ("D:\\music\\demo", "C:\\Users\\xxx", "E:\\xxx",
                "D:\\models", "C:\\path\\to")

# 真实商业歌曲名：出现在**代码/文档**里（不算示例）就值得核对版权
SONGS = ["在出发之前", "只要平凡", "让风告诉你", "我不曾忘记", "失眠飞行",
         "它像一颗", "空心", "体面", "晴天"]
# 这些文件里出现歌名是**故意的**（记录真实测试/评估），保留但要知道
SONG_OK_FILES = ("AGENTS.md", "CHANGELOG.md", "docs/声纹评估协议.md",
                 "docs/竞品调研与优化方案.md", "docs/项目总结.md",
                 "tools/eval_diarization.py", "tools/eval_word_timing.py",
                 "selftest.py")

# ---- B 类：代码里已经没有了、文档还在说的东西 ----
STALE = [
    ("--device cpu / CPU 选项",
     re.compile(r"--device\s+cpu|value=\"cpu\"|回退 CPU|回退到 CPU|自动回退 CPU"),
     "设备选项已改成 {cuda,auto}，且取消 CPU 降级"),
    ("「自动」作为默认设备",
     re.compile(r"device.{0,6}默认.{0,6}auto(?!.{0,10}cuda)|默认.{0,4}auto.{0,6}设备"),
     "默认已是 cuda"),
    ("ffmpeg 必需",
     re.compile(r"需要\s*`?ffmpeg`?\s*在\s*PATH|ffmpeg.*必需|必须安装 ffmpeg"),
     "PyAV 自带 FFmpeg 库，ffmpeg 只是可选兜底"),
    ("--auto-agents 当新参数介绍",
     re.compile(r"自动分声部：CAM\+\+ 声纹模型（`--auto-agents`）"),
     "已改成 --singer-mode"),
    ("portable 作为主推下载",
     re.compile(r"portable\.zip.*推荐|推荐.*portable"),
     "Release 已只留 lite + installer"),
]


def tracked() -> list:
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                       encoding="utf-8")
    return [f.strip() for f in (r.stdout or "").splitlines() if f.strip()]


def read(p: str) -> str:
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def main() -> int:
    files = tracked()
    md = [f for f in files if f.endswith((".md", ".txt"))]
    code = [f for f in files if f.endswith((".py", ".js", ".html", ".bat"))]
    print("=" * 74)
    print(f"  跟踪文件 {len(files)} 个（文档 {len(md)} / 代码 {len(code)}）")
    print("=" * 74)

    # ---------------- A. 不宜开源 ----------------
    print("\n【A】不宜开源的内容")
    a_hits = 0
    for f in files:
        if f in A_ALLOW_FILES or not f.endswith(
                (".py", ".js", ".md", ".txt", ".html", ".bat", ".yml", ".json")):
            continue
        t = read(f)
        for label, pat in A_PATTERNS:
            for m in pat.finditer(t):
                s = m.group(0)
                if any(x in s for x in A_ALLOW_TEXT):
                    continue
                line = t[t.rfind("\n", 0, m.start()) + 1:t.find("\n", m.start())]
                print(f"  ✗ [{label}] {f}: {line.strip()[:96]}")
                a_hits += 1
    if not a_hits:
        print("  ✓ 没有发现个人路径 / 凭据 / 手机号")

    print("\n【A2】真实商业歌曲名的出现位置（确认是否为'记录真实测试'）")
    for f in files:
        if not f.endswith((".py", ".js", ".md", ".txt", ".html")):
            continue
        t = read(f)
        found = [s for s in SONGS if s in t]
        if not found:
            continue
        flag = "（记录真实测试，保留）" if f in SONG_OK_FILES else "⚠ 需确认"
        print(f"  {f}: {'、'.join(found)} {flag}")

    # ---------------- B. 文案过时 ----------------
    print("\n【B】文档里与代码不一致的说法")
    b_hits = 0
    for f in md + ["index.html", "main.py"]:
        t = read(f)
        for name, pat, why in STALE:
            for m in pat.finditer(t):
                ln = t.count("\n", 0, m.start()) + 1
                line = t[t.rfind("\n", 0, m.start()) + 1:t.find("\n", m.start())]
                print(f"  ✗ {f}:{ln}  「{name}」")
                print(f"      {line.strip()[:96]}")
                print(f"      → {why}")
                b_hits += 1
    if not b_hits:
        print("  ✓ 没发现过时说法")

    print()
    print("=" * 74)
    print(f"  A 类 {a_hits} 处 · B 类 {b_hits} 处")
    print("=" * 74)
    return 0 if (a_hits == 0 and b_hits == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
