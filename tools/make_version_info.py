"""生成 Windows 版本资源（`version_info.txt`），让资源管理器/任务管理器里
能看到正经的产品名、版本号、公司名。

    python tools/make_version_info.py          # 写出 build/version_info.txt
    python tools/make_version_info.py --check  # 校验是否与 __version__ 同步

## 为什么（实测的空白）

2026-10-13 实测 `dist/Lyric2TTML/Lyric2TTML.exe` 的版本资源**全是空的**：

    ProductName     : ''
    FileVersion     : ''
    CompanyName     : ''
    FileDescription : ''

后果：资源管理器「属性 → 详细信息」、任务管理器「详细信息」页里
**什么都不显示**，用户看到的是一个没有名字没有版本的程序。
对"要交付给别人的软件"来说这是硬伤。

## 版本号只有一处真源

`desktop/__init__.py` 的 `__version__` —— 不要在这里另抄一个。
`win_integration.app_version()` 也是从那里读的，两边必须一致。
"""
from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

OUT_DIR = os.path.join(HERE, "build")
OUT = os.path.join(OUT_DIR, "version_info.txt")

PRODUCT = "Lyric2TTML"
DESC = "普通歌词 + 音频 → Apple Music / AMLL 逐字时间轴"
COMPANY = "Lyric2TTML"
COPYRIGHT = "Copyright (C) 2026 Lyric2TTML"


def app_version() -> str:
    from desktop import __version__
    return str(__version__).strip()


def parse(v: str) -> tuple:
    """`1.1.0` → `(1, 1, 0, 0)`（必须是 4 段整数，PyInstaller 要）。"""
    parts = []
    for p in v.split(".")[:4]:
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


TEMPLATE = """# UTF-8 —— 由 `tools/make_version_info.py` 生成，**不要手改**。
# 版本号来自 desktop/__init__.py 的 __version__（唯一真源）。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [StringStruct('CompanyName', '{company}'),
         StringStruct('FileDescription', '{desc}'),
         StringStruct('FileVersion', '{ver}'),
         StringStruct('InternalName', '{product}'),
         StringStruct('LegalCopyright', '{copy}'),
         StringStruct('OriginalFilename', '{product}.exe'),
         StringStruct('ProductName', '{product}'),
         StringStruct('ProductVersion', '{ver}')])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
"""


def render() -> str:
    v = app_version()
    return TEMPLATE.format(quad=parse(v), ver=v, product=PRODUCT,
                           desc=DESC, company=COMPANY, copy=COPYRIGHT)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Windows 版本资源")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    want = render()
    v = app_version()
    if a.check:
        if not os.path.isfile(OUT):
            print(f"  ✗ 没有 {OUT}")
            return 1
        got = io.open(OUT, encoding="utf-8").read()
        if got != want:
            print(f"  ✗ {OUT} 与 desktop.__version__（{v}）不同步 —— "
                  f"重新跑一次本脚本")
            return 1
        print(f"  ✓ {OUT} 与版本号 {v} 同步")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(want)
    print(f"  ✓ 已写出 {OUT}（版本 {v}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
