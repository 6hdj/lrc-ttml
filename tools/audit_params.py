"""参数联调审计：界面 → /api/convert → ConvertOptions → 真正被用到？

三类问题都能查出来：
  1. 界面有控件、但 `startConversion` 没发给后端（纯前端 UI）；
  2. 前端发了、但 `web_server._convert` 没接（参数在半路丢了）；
  3. `ConvertOptions` 有字段、但**全仓库没人读**（接了不用，等于没接）。

用法：python tools/audit_params.py
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys
from typing import Dict, List, Set

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)
sys.path.insert(0, HERE)


def read(p: str) -> str:
    return io.open(p, encoding="utf-8", errors="replace").read()


def ui_param_keys(html: str) -> Dict[str, str]:
    """从 index.html 的 `const params = {...}` 里取 key → 值的源码"""
    m = re.search(r"const params = \{(.*?)\n  \};", html, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        line = line.split("//")[0].strip().rstrip(",")
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def web_kwargs(src: str) -> Set[str]:
    """web_server._convert 里 ConvertOptions(...) 的关键字"""
    i = src.find("ConvertOptions(")
    if i < 0:
        return set()
    j = src.find("\n        )", i)
    body = src[i:j if j > 0 else i + 4000]
    return set(re.findall(r"(\w+)\s*=", body))


def option_fields(src: str) -> Dict[str, str]:
    """ConvertOptions 的字段名 → 默认值源码"""
    m = re.search(r"class ConvertOptions:(.*?)\n\n\n", src, re.S)
    if not m:
        m = re.search(r"class ConvertOptions:(.*?)\ndef ", src, re.S)
    body = m.group(1) if m else ""
    out = {}
    for line in body.splitlines():
        mm = re.match(r"\s{4}(\w+)\s*:\s*[^=]+=\s*(.*)$", line)
        if mm:
            out[mm.group(1)] = mm.group(2).split("#")[0].strip()
    return out


def readers_of(field: str, files: List[str], skip: List[str]) -> List[str]:
    """谁读过这个字段（`opt.<field>` / `self.opt.<field>` / `.field`）"""
    pat = re.compile(r"\bopt\.%s\b|\bself\.opt\.%s\b|\.%s\b" % (field, field, field))
    hits = []
    for f in files:
        if f in skip:
            continue
        if pat.search(read(f)):
            hits.append(f)
    return hits


def meta_keys(writer_src: str) -> Set[str]:
    """ttml_writer.META_KEYS 里的键。

    ⚠️ 这些键在 web_server 里是**循环处理**的（`for key in META_KEYS`），
    不会以字面量出现。只看字面量会把它们全误报成"没接"——
    误报的审计比没有审计更糟（会让人去"修"根本没坏的东西）。
    """
    m = re.search(r"META_KEYS\s*=\s*\[(.*?)\]", writer_src, re.S)
    return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()


def main() -> int:
    html = read("index.html")
    web = read("web_server.py")
    pipe = read("pipeline.py")
    main_py = read("main.py")
    files = [f for f in os.listdir(".") if f.endswith(".py")]
    files = [f for f in files if not f.startswith("_")]

    ui = ui_param_keys(html)
    kw = web_kwargs(web)
    fields = option_fields(pipe)

    print("=" * 74)
    print("① 界面发出的参数 → 后端有没有接（p.get / ConvertOptions 关键字）")
    print("=" * 74)
    # ⚠️ 不能只查 ConvertOptions 的关键字：title/artists/album/translation 这些
    # 是**绕过 ConvertOptions 单独传给流水线**的（走 meta / 构造参数），
    # 只比关键字会把它们误报成"没接"。真正的判据是后端有没有读过这个 key。
    missing_web = []
    mkeys = meta_keys(read("ttml_writer.py"))
    for k in sorted(ui):
        # 后端"接过"的判据（三条通道任一条即可）：
        #   ① 名字作为带引号的字符串出现（p.get("x") / 循环里的 "x"）
        #   ② 是 ConvertOptions 的关键字
        #   ③ 在 ttml_writer.META_KEYS 里（web_server 直接循环它，见 meta_keys 注释）
        ok = (f'"{k}"' in web) or (f"'{k}'" in web) or (k in kw) or (k in mkeys)
        if not ok:
            missing_web.append(k)
        print(f"  {'OK ' if ok else '✗  '} {k}")
    print(f"\n  web_server 接了 {len(kw)} 个 ConvertOptions 关键字；界面发了 {len(ui)} 个")

    print()
    print("=" * 74)
    print("② ConvertOptions 字段 → 全仓库有没有人读")
    print("=" * 74)
    dead = []
    for f in sorted(fields):
        hits = readers_of(f, files, skip=["pipeline.py"])
        in_pipe = bool(re.search(r"opt\.%s\b" % f, pipe))
        used = bool(hits) or in_pipe
        if not used:
            dead.append(f)
        print(f"  {'OK ' if used else '✗ 死字段'} {f:<22}"
              f"{'pipeline ' if in_pipe else '        '}"
              f"{','.join(h.split('.')[0] for h in hits[:3])}")

    print()
    print("=" * 74)
    print("③ 界面控件 → startConversion 有没有把它发出去（纯 UI 控件检测）")
    print("=" * 74)
    m = re.search(r"const params = \{(.*?)\n  \};", html, re.S)
    block = m.group(1) if m else ""
    ids = re.findall(r'<select[^>]*id="([^"]+)"', html)
    # 文件选择框不是配置项，本来就不进 params
    skip_ids = {"audioFile", "lyricsFile", "transFile"}
    pure_ui = []
    for cid in ids:
        if cid in skip_ids:
            continue
        ok = f"'{cid}'" in block
        if not ok:
            pure_ui.append(cid)
        print(f"  {'OK ' if ok else '✗ 纯UI'} <select id=\"{cid}\">")
    other = [i for i in re.findall(r'<input[^>]*id="([^"]+)"', html) if i not in skip_ids]
    for cid in other:
        ok = f"'{cid}'" in block
        if not ok:
            pure_ui.append(cid)
        print(f"  {'OK ' if ok else '✗ 纯UI'} <input  id=\"{cid}\">")

    print()
    print("=" * 74)
    print("结论")
    print("=" * 74)
    print(f"  ① 界面发了但后端完全没这个 key：{missing_web or '无'}")
    print(f"  ② 接了但没人读的死字段：{dead or '无'}")
    print(f"  ③ 只在界面上、没进 params 的控件：{pure_ui or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
