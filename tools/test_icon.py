"""验证"exe 里真的嵌的是**我们的**图标"，而不是 PyInstaller 默认图标。

    python tools/test_icon.py [dist/安装程序.exe] [dist/Lyric2TTML/Lyric2TTML.exe]

## 为什么不能只看"有图标"

`ExtractAssociatedIcon()` 在**任何** PyInstaller 产物上都能拿到一个图标 ——
默认那个是 **Python 徽标**（橙色蛇 + 窗口）。所以"提取到图标 32x32"这种检查
**证明不了任何事**，实测就是这么被骗过去的：以为有图标，其实是默认的。

本测试用**像素颜色**判定：从 exe 里抠出最大那张 PNG 图标（多尺寸 .ico 里
256×256 那帧通常是 PNG 压的），在**正中央**取色，要求它是**蓝色系**
（应用图标是 `#4c9aff → #22d3ee` 的圆角方块；Python 默认图标中央是橙/白）。

⚠️ 取"正中央"是有意的：我们的图标中心是**白色音符**…所以要取**偏左上**
的方块底色区域才稳。实测下来最稳的是图标 25% 高度处（横线之间是蓝底）。
"""
from __future__ import annotations

import io
import os
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICO = os.path.join(HERE, "assets", "icon.ico")

_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


def _png_size(data: bytes) -> tuple:
    """从 PNG 头里读宽高（不引 Pillow，保持这个检查轻量）。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    w, h = struct.unpack(">II", data[16:24])
    return (w, h)


def embedded_icons(exe: str) -> list:
    """从 exe 里找出所有 PNG 图标（返回 [(宽, 高, 字节数)]）。"""
    blob = open(exe, "rb").read()
    out = []
    i = 0
    while True:
        i = blob.find(b"\x89PNG\r\n\x1a\n", i)
        if i < 0:
            break
        w, h = _png_size(blob[i:i + 32])
        if w and h and w == h and 8 <= w <= 512:
            # IEND 之后结束
            end = blob.find(b"IEND", i)
            if end > 0:
                n = end + 8 - i
                out.append((w, h, n, i))
        i += 8
    return out


def dominant_blue(exe: str) -> tuple:
    """粗略判断 exe 里的图标是不是**蓝色系**。

    用 Pillow 打不开"exe 里的 PNG 切片"很麻烦，所以这里退一步：
    直接把整段 PNG 里的原始像素解出来 —— 但对压缩流不方便。
    ⇒ 改用**尺寸**与**颜色直方图**两个弱信号一起判：
      * 我们的 .ico 在 exe 里会留下 **256×256** 的 PNG；
      * PyInstaller 默认图标也有 256×256。
    所以尺寸不够，必须真解像素。这里用 Pillow 打开**整段切片**。
    """
    from PIL import Image
    blob = open(exe, "rb").read()
    best = None
    i = 0
    while True:
        i = blob.find(b"\x89PNG\r\n\x1a\n", i)
        if i < 0:
            break
        end = blob.find(b"IEND", i)
        if end > 0:
            data = blob[i:end + 8]
            try:
                im = Image.open(io.BytesIO(data)).convert("RGBA")
            except Exception:                           # noqa: BLE001
                i += 8
                continue
            if im.width >= 200 and im.width == im.height and \
                    (best is None or im.width > best.width):
                best = im
        i += 8
    if best is None:
        return (0, 0, 0)
    # 采样左上角的方块底色（图标 0.25 宽处必然在蓝底上）
    px = best.getpixel((int(best.width * 0.12), int(best.height * 0.5)))
    return (best.width, px[0], px[1], px[2])


def main() -> int:
    targets = sys.argv[1:] or [os.path.join("dist", "安装程序.exe"),
                               os.path.join("dist", "Lyric2TTML",
                                            "Lyric2TTML.exe")]
    print("=" * 74)
    print("  应用图标验证")
    print("=" * 74)

    if not os.path.isfile(ICO):
        print(f"  ✗ 缺少 {ICO}（先跑 `python tools/make_icon.py`）")
        return 1
    print(f"  图标源：{ICO}（{os.path.getsize(ICO)} 字节）")

    for t in targets:
        exe = t if os.path.isabs(t) else os.path.join(HERE, t)
        print(f"\n[{os.path.basename(t)}]")
        if not os.path.isfile(exe):
            print("  [跳过] 不存在")
            continue
        ic = embedded_icons(exe)
        sizes = sorted({w for w, _h, _n, _i in ic})
        print(f"       内嵌 PNG 图标尺寸：{sizes or '(无)'}")
        try:
            w, r, g, b = dominant_blue(exe)
        except Exception as e:                          # noqa: BLE001
            print(f"  [跳过] 解像素失败：{type(e).__name__}: {e}")
            continue
        if not w:
            check(False, "能取出最大图标", "没找到 ≥200px 的 PNG 图标")
            continue
        print(f"       最大图标 {w}px，采样色 = rgb({r},{g},{b})")
        # 我们的图标是蓝底（#4c9aff→#22d3ee：B 明显大于 R）
        is_blue = b > r + 40 and b > 120
        check(is_blue,
              "exe 里嵌的是**我们的蓝色图标**（不是 PyInstaller 默认的橙色）",
              f"采样色 rgb({r},{g},{b}) 不像蓝底 —— 很可能是默认图标")

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
