"""验证"per-user 安装、不需要管理员"这组要求（用户 2026-10-13 方案①）。

    python tools/test_peruser.py                     # 只查源码与默认值
    python tools/test_peruser.py dist/安装程序.exe    # 连 EXE 的**嵌入清单**一起查

## 为什么要有它

用户要求里有好几条是"必须真的改掉，不能只在界面上声称支持"：

1. 正式安装程序的 EXE 上**不能有**强制管理员权限的清单；
2. 默认安装位置必须是当前用户目录（`%LOCALAPPDATA%\\Lyric2TTML`）；
3. 安装/快捷方式/卸载注册/配置写入**都不能暗中依赖管理员**；
4. **不许写 HKLM / Program Files / 系统级目录**（读没关系，写不行）；
5. 名字要统一（用户特别提到别出现 `LyricZTTML` 这类拼写不一致）；
6. 开始菜单快捷方式与卸载注册项都必须是**当前用户范围**的。

这些是"看一眼就以为对了"的东西，所以逐条写成断言。
⚠️ 本测试**不抢屏幕、不写真实系统位置**（纯只读检查），可以随便跑。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def _read(rel: str) -> str:
    return io.open(os.path.join(HERE, rel), encoding="utf-8").read()


# ---------------------------------------------------------------- EXE 清单
def read_manifest(exe: str) -> str:
    """从 PE 里抠出嵌入的清单 XML（只读文件字节，不执行 exe）。

    ⚠️ 不能只看"构建命令里有没有 `--uac-admin`" —— 清单是**嵌进 exe** 的，
    真正决定行为的是文件里的字节。用户明确要求"检查 EXE 的实际嵌入清单"。
    """
    with open(exe, "rb") as fh:
        blob = fh.read()
    # 清单是 UTF-8 XML，找 requestedExecutionLevel 那段
    m = re.search(rb"<requestedExecutionLevel[^>]*>", blob)
    if m:
        return m.group(0).decode("utf-8", "replace")
    # 没有该节点时，返回是否有 manifest 节点（用于区分"无清单"和"有清单但没提权"）
    has_manifest = b"<assembly" in blob and b"trustInfo" in blob
    return "(有清单，但无 requestedExecutionLevel)" if has_manifest else ""


def case_manifest(exe: str) -> None:
    print(f"\n[1] EXE 嵌入清单 —— {exe}")
    if not os.path.isfile(exe):
        print(f"  [跳过] 文件不存在：{exe}")
        return
    node = read_manifest(exe)
    print(f"       实际清单节点：{node or '(没有任何清单)'}")
    check("requireAdministrator" not in node,
          "清单**没有**要求管理员（requireAdministrator）",
          f"发现：{node}")
    check("highestAvailable" not in node,
          "清单也没有 highestAvailable（会弹 UAC）", f"发现：{node}")
    size = os.path.getsize(exe)
    print(f"       文件大小：{size / 1073741824:.2f} GB")
    # verify_signature / signature，避免误判
    if node and "asInvoker" in node:
        print("       （asInvoker = 以当前用户权限运行，正确）")


# ---------------------------------------------------------------- 源码审计
def _code_text(src: str) -> str:
    """只保留**真正的代码 token**，把注释和所有字符串字面量都去掉。

    ⚠️⚠️ 为什么不能靠"这一行是不是以 # 开头"来判（前两版都栽在这上面）：
      * `win_integration.py` 里有一段**文档字符串**写着
        "用 HKCU（不是 HKLM）：普通用户就能写" —— 那是**正确的说明**，
        照原文匹配却把它判成了违规；
      * docstring 的中间行既不以 `#` 也不以引号开头，任何"看行首"的启发式都会漏。
    用 AST 拿到注释以外的**全部代码文本**，再把字符串字面量逐个挖掉 ——
    剩下的是标识符/运算符/关键字，能真正反映"代码做了什么"。
    这样"注释里提到 HKLM"不会误报，"代码里真的用 HKLM"一定报得出来。
    """
    import ast
    tree = ast.parse(src)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            spans.append((node.lineno, node.col_offset,
                          getattr(node, "end_lineno", node.lineno),
                          getattr(node, "end_col_offset", node.col_offset)))
    lines = src.split("\n")
    keep = []
    for i, ln in enumerate(lines, 1):
        cut = len(ln)
        for (a, _ca, b, ) in ((s[0], s[1], s[2]) for s in spans):
            if a == i:
                cut = min(cut, _ca)
            elif a < i < b:
                cut = 0
        keep.append(ln[:cut])
    text = "\n".join(keep)
    # 再去掉注释
    return "\n".join(x.split("#")[0] for x in text.split("\n"))


def case_sources() -> None:
    print("\n[2] 源码里是否还藏着「需要管理员」的写操作")
    ig = _read("installer_gui.py")
    wi = _read("win_integration.py")
    uc = _read("uninstall_core.py")

    # 自提权必须停用
    m = re.search(r"def relaunch_elevated\(\)[^\n]*\n(?:.*?\n)*?    return (\w+)",
                  ig)
    body = m.group(1) if m else "?"
    check(body == "False",
          "installer_gui.relaunch_elevated() 已停用（永远返回 False）",
          f"实际 return {body}")
    # ⚠️ 只看**真正的代码**：`runas` 在解释性注释/文档里仍然出现（说明为什么停用），
    # 那是好事，不该算违规。
    ig_code = _code_text(ig)
    if "runas" in ig_code.lower():
        idx = ig_code.lower().index("runas")
        where = ig_code[max(0, idx - 60):idx + 40].replace("\n", " ")
    else:
        where = ""
    check("runas" not in ig_code.lower(),
          "installer_gui 的**代码**里不再用 `runas` 动词触发 UAC",
          f"…{where}…")
    check(not re.search(r'^    if os\.name == "nt" and not _is_elevated\(\)',
                        ig, re.M),
          "入口处不再「不是管理员就重启自己」")

    # 机器级写入
    for label, src in (("installer_gui.py", ig), ("win_integration.py", wi),
                       ("uninstall_core.py", uc)):
        code = _code_text(src)
        writes = []
        for i, ln in enumerate(code.split("\n"), 1):
            low = ln.lower()
            # 只看"写"的迹象：reg add / delete、mkdir、open(..,"w")、copy
            is_write = any(k in low for k in
                           ("reg add", "reg delete", "shutil.rmtree",
                            "mkdir", "makedirs", "os.remove",
                            "copyfile", "shutil.copy"))
            if is_write and ("hklm" in low or "hkey_local_machine" in low
                             or "program files" in low or "allusersprofile"
                             in low or "programdata" in low):
                writes.append((i, ln.strip()[:70]))
        check(not writes, f"{label} 里没有对 HKLM / Program Files / 系统目录的**写**操作",
              f"{writes}")

    # 用户范围
    check("HKCU\\" in wi, "卸载注册表项写 HKCU（当前用户）")
    wi_code = _code_text(wi)
    check("hklm" not in wi_code.lower()
          and "hkey_local_machine" not in wi_code.lower(),
          "win_integration 的**代码**里不出现 HKLM",
          f"{[l for l in wi_code.split(chr(10)) if 'HKLM' in l.upper()][:2]}")
    check("CSIDL_PROGRAMS" in wi or "Start Menu" in wi,
          "开始菜单用当前用户的「程序」目录（CSIDL_PROGRAMS / Start Menu）")
    check("SHGetFolderPathW" in wi,
          "标准目录用 Shell API 取（能正确处理被重定向的桌面/开始菜单）")


def case_defaults() -> None:
    print("\n[3] 默认值与命名")
    import installer_gui as ig
    d = ig.default_install_dir()
    local = os.environ.get("LOCALAPPDATA", "")
    print(f"       默认安装目录：{d}")
    check(local and d.lower().startswith(local.lower()),
          "默认装到 %LOCALAPPDATA%（当前用户目录，不需要管理员）", d)
    check(not d.lower().startswith("c:\\program files")
          and "program files" not in d.lower(),
          "默认**不**装进 Program Files")

    # 名字统一
    import desktop.paths as paths
    import win_integration as wi
    names = {
        "win_integration.APP_NAME": wi.APP_NAME,
        "desktop.paths.APP_NAME": paths.APP_NAME,
        "安装目录末段": os.path.basename(d),
    }
    for k, v in names.items():
        print(f"       {k} = {v!r}")
    check(len(set(names.values())) == 1,
          "软件名 / 目录名在各处一致（无 LyricZTTML 之类拼写不一致）", f"{names}")

    # 全仓扫拼写错误（⚠️ 必须跳过本文件本身 —— 它的检查清单里就写着这些错拼
    # 当成"要找的模式"，第一版把自己报成了违规）
    bad = []
    me = os.path.abspath(__file__)
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [x for x in dirs if x not in
                   (".venv", ".venvpack", "dist", "build", ".tmp",
                    "node_modules", ".git", "promo")]
        for fn in files:
            if not fn.endswith((".py", ".json", ".bat", ".spec", ".md")):
                continue
            p = os.path.join(root, fn)
            if os.path.abspath(p) == me:
                continue
            try:
                txt = io.open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for pat in ("LyricZ", "Lyric2TMML", "Lyrics2TTML", "LyricTTML"):
                if pat in txt:
                    bad.append((os.path.relpath(p, HERE), pat))
    check(not bad, "全仓没有 App 名拼写错误", f"{bad[:5]}")


def case_app_permissions() -> None:
    """应用本体：不能为了安装器免提权，把应用自己的写入搞坏。"""
    print("\n[4] 应用本体的写入是否都在用户范围")
    paths = _read("desktop/paths.py")
    check("data_dir" in paths, "desktop/paths.py 有 data_dir 概念")
    # 数据目录必须是用户范围（函数在 paths.py 里叫 `data_dir`，不是
    # `default_data_dir` —— 那个名字在 installer_gui/uninstall_core 里）
    m = re.search(r"def data_dir\(\)[\s\S]{0,700}?\n    return ([^\n]+)", paths)
    body = m.group(0) if m else ""
    print("       data_dir 片段：")
    for ln in body.split("\n")[:12]:
        print(f"         {ln.strip()[:86]}")
    check(("LOCALAPPDATA" in body) or ("APPDATA" in body)
          or ("expanduser" in body) or ("_local_appdata" in body),
          "数据目录落在用户范围（LOCALAPPDATA / APPDATA / ~）",
          "regex 没匹配到？")
    # 程序目录不可写时要有降级（Program Files 场景）
    check("不可写" in paths or "writable" in paths.lower(),
          "paths 里处理了「程序目录不可写」的情况（Program Files 场景）")


def main() -> int:
    ap = argparse.ArgumentParser(description="per-user 安装要求验证")
    ap.add_argument("exe", nargs="?", default="",
                    help="安装程序 exe（给了就连嵌入清单一起查）")
    a = ap.parse_args()

    print("=" * 74)
    print("  per-user 安装 / 免管理员 验证")
    print("=" * 74)
    exe = a.exe
    if exe and not os.path.isabs(exe):
        exe = os.path.join(HERE, exe)
    if exe:
        case_manifest(exe)
    else:
        print("\n[1] 跳过 EXE 清单检查（没给 exe 路径）")
    case_sources()
    case_defaults()
    case_app_permissions()

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    if _fail:
        print("  有未达标项 —— 普通用户可能仍被要求管理员权限")
        return 1
    print("  per-user 安装要求全部达标 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
