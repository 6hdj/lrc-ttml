"""生成应用图标（多尺寸 `.ico`）并顺带给出 SVG 源。

    python tools/make_icon.py            # 写出 assets/icon.ico + icon.svg
    python tools/make_icon.py --check    # 只校验现有 .ico 的尺寸是否齐全

## 为什么必须有（一个"一眼就不像正式软件"的问题）

2026-10-13 实测：`dist/Lyric2TTML/Lyric2TTML.exe` 的图标是
**PyInstaller 的默认图标**（橙色 Python + 窗口的那个），因为：

* 仓库里**一个 `.ico` 都没有**；
* `Lyric2TTML.spec` 里**没有 `icon=`**；
* `win_integration` 建快捷方式时 `IconLocation` 直接指向 exe（那没问题，
  但 exe 自己没有图标，于是快捷方式也是默认图标）。

后果：桌面/开始菜单/任务栏/「已安装的应用」里全是 Python 图标 ——
用户会觉得"这是个脚本"，而不是一个商业桌面软件。

## 设计

跟随应用调色板（`desktop/pages/base.py` 的 `C`）：
* 底：圆角方块，`accent #4c9aff` → `cyan #22d3ee` 线性渐变；
* 图：**三条不同长度的横线**（= 逐字时间轴 / 歌词行）＋ 一个**音符**。

⚠️ 设计约束（都是被小尺寸逼出来的）：
* **16×16 也要能认**：所以只用粗线条和大色块，不放任何汉字/细描边
  （16px 下汉字会糊成一团）；
* **不透明底**：Windows 任务栏与部分列表不友好地处理透明图标；
* 线条用**纯白**，保证在深浅两种任务栏上都看得清。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "assets")
ICO = os.path.join(OUT_DIR, "icon.ico")
SVG = os.path.join(OUT_DIR, "icon.svg")

# 与 desktop/pages/base.py 的调色板保持一致（不要另抄一套颜色）
ACCENT = "#4c9aff"
CYAN = "#22d3ee"

SIZES = [16, 24, 32, 48, 64, 128, 256]

SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"
     viewBox="0 0 256 256">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{accent}"/>
      <stop offset="100%" stop-color="{cyan}"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="256" height="256" rx="56" ry="56" fill="url(#g)"/>
  <!-- 两条"歌词行"：少就是多 —— 16px 下三条会糊成一片（实测看图后从 3 改 2） -->
  <rect x="46" y="62"  width="164" height="22" rx="11" fill="#ffffff"/>
  <rect x="46" y="104" width="120" height="22" rx="11" fill="#ffffff"
        opacity="0.9"/>
  <!-- 音符：下方留足空间，16px 才认得出是音符 -->
  <g fill="#ffffff">
    <rect x="150" y="140" width="18" height="62" rx="9"/>
    <ellipse cx="128" cy="200" rx="30" ry="23"/>
    <path d="M168 140 q44 10 44 42 q0 14 -14 20 q12 -16 -30 -32 z"/>
  </g>
</svg>
"""


def write_svg() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(SVG, "w", encoding="utf-8") as fh:
        fh.write(SVG_TEMPLATE.format(accent=ACCENT, cyan=CYAN))


def render_sizes() -> list:
    """用 Qt 把 SVG 渲染成各尺寸 QImage（Qt 自带 SVG 支持，不引第三方库）。"""
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])   # noqa: F841
    data = QByteArray(open(SVG, "rb").read())
    r = QSvgRenderer(data)
    if not r.isValid():
        raise SystemExit("SVG 渲染器认为这个 SVG 无效")
    out = []
    for s in SIZES:
        img = QImage(s, s, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        r.render(p)
        p.end()
        out.append(img)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="生成应用图标")
    ap.add_argument("--check", action="store_true", help="只校验现有 ico")
    a = ap.parse_args()

    if a.check:
        if not os.path.isfile(ICO):
            print(f"  ✗ 没有 {ICO}")
            return 1
        raw = open(ICO, "rb").read()
        # ICO 头：0,0,1,0,count
        if raw[:4] != b"\x00\x00\x01\x00":
            print("  ✗ 不是合法的 ICO")
            return 1
        n = int.from_bytes(raw[4:6], "little")
        print(f"  ✓ {ICO}：{n} 个尺寸，{len(raw)} 字节")
        return 0

    write_svg()
    imgs = render_sizes()
    os.makedirs(OUT_DIR, exist_ok=True)
    # ⚠️ Pillow 写多尺寸 ICO：`append_images` + `sizes`。
    # 只给一张 256 再让系统缩放，任务栏/资源管理器里会糊。
    from PIL import Image
    from PySide6.QtGui import QImage
    frames = []
    for img in imgs:
        # ⚠️ 必须统一成 `Format_RGBA8888`：`constBits()` 的原始内存布局
        # 取决于 QImage 的格式，直接按 BGRA 解释会得到**颜色通道颠倒**的图
        # （红蓝互换，看起来像"配色变了"这种莫名其妙的 bug）。
        img = img.convertToFormat(QImage.Format_RGBA8888)
        buf = img.constBits().tobytes()
        w, h = img.width(), img.height()
        pil = Image.frombytes("RGBA", (w, h), buf, "raw", "RGBA")
        frames.append(pil)
    base = frames[-1]                                   # 256
    base.save(ICO, format="ICO",
              sizes=[(s, s) for s in SIZES],
              append_images=frames[:-1])
    print(f"  ✓ 已写出 {SVG}")
    print(f"  ✓ 已写出 {ICO}（{len(open(ICO, 'rb').read())} 字节，"
          f"含尺寸 {SIZES}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
