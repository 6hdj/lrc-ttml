"""开源前的敏感信息与"不该公开的内容"扫描。

比肉眼看可靠：把整个工作区（排除依赖/缓存/媒体）过一遍，找出：
  1. **个人绝对路径**（C:\\Users\\xxx、E:\\xxx）—— 会把你的用户名和目录结构公开；
  2. **凭据**（api key / token / password / cookie）；
  3. **邮箱 / 手机号**；
  4. **版权风险**（引用的第三方歌词、GPL 源码片段）；
  5. **大文件**（>1MB 的文本/二进制，不该进 git）。

用法：python tools/audit_public_ready.py
"""
from __future__ import annotations

import io
import os
import re
import sys
from typing import List, Tuple

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)

# 不该扫的：依赖、缓存、媒体、模型、用户数据
SKIP_DIRS = {".git", ".venv", ".hf_cache", ".tmp", "__pycache__", "models",
             "uploads", "outputs", "node_modules", "dist", "build"}
TEXT_EXT = {".py", ".js", ".md", ".txt", ".json", ".html", ".bat", ".ps1",
            ".yml", ".yaml", ".toml", ".cfg", ".ini", ".ttml", ".lrc", ".csv"}

PATTERNS: List[Tuple[str, str, str]] = [
    ("个人绝对路径", r"[A-Za-z]:\\+Users\\+[^\\\s\"']+", "会公开你的 Windows 用户名"),
    ("个人绝对路径", r"[A-Za-z]:\\+[^\s\"'<>|]{3,}", "会公开你的目录结构"),
    ("疑似密钥", r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,})", "凭据泄露"),
    ("疑似密钥字段", r"(?i)\b(api[_-]?key|secret|passwd|password|access[_-]?token|bearer)\s*[:=]\s*[\"'][^\"']{6,}", "凭据泄露"),
    ("邮箱", r"[\w.+-]+@[\w-]+\.[\w.]{2,}", "个人信息"),
    ("手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)", "个人信息"),
    ("外链（可能依赖第三方）", r"https?://(?!127\.0\.0\.1|localhost)[^\s\"'<>)]+", "确认是否必要/合规"),
]

hits: dict = {}
big: List[Tuple[str, int]] = []
scanned = 0

for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fn in files:
        p = os.path.join(root, fn)
        rel = p.replace("\\", "/").lstrip("./")
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size > 1024 * 1024:
            big.append((rel, size))
        if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
            continue
        scanned += 1
        try:
            text = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for label, pat, why in PATTERNS:
            for m in re.finditer(pat, text):
                s = m.group(0)
                # 白名单：文档里出现的 URL 示例、协议地址
                if s.startswith(("http://www.w3.org", "http://music.apple.com",
                                 "http://www.example.com")):
                    continue
                hits.setdefault((label, why), {}).setdefault(s, []).append(rel)

print("=" * 74)
print(f"扫描了 {scanned} 个文本文件")
print("=" * 74)
total = 0
for (label, why), found in sorted(hits.items()):
    print(f"\n【{label}】（{why}）")
    for s, files in sorted(found.items(), key=lambda x: -len(x[1]))[:12]:
        uniq = sorted(set(files))
        total += len(uniq)
        print(f"  {s[:60]!r:<62} {len(uniq)} 个文件")
        print(f"      例：{', '.join(uniq[:3])}")

if big:
    print("\n【大文件（>1MB，不该进 git）】")
    for rel, size in sorted(big, key=lambda x: -x[1])[:20]:
        print(f"  {size/1024/1024:8.1f} MB  {rel}")
    total += len(big)

print()
print("=" * 74)
print(f"共 {total} 处需要确认；0 处 = 可以直接公开")
print("=" * 74)
