"""桌面版（Qt）高 DPI 检查：在 100% / 125% / 150% / 200% 下**真跑**并检查。

    python tools/test_hidpi.py            # 全部缩放
    python tools/test_hidpi.py --dpr 2.0  # 只跑一个

## 为什么需要它

任务书 C 明确要求"**高 DPI**"，但这条一直没有证据：
`desktop/app.py` 只声明了
`HighDpiScaleFactorRoundingPolicy.PassThrough`，
**没有任何测试在非 100% 缩放下跑过界面**。
本项目已经因为"只在默认条件验过"栽过好几次（安装器的 DPI 就是上一轮才发现的），
所以这里补上——而且不只跑"能不能构建"，还要看**渲染出来的像素**。

## 怎么在没有高分屏的机器上造出高 DPI

Qt 支持 `QT_SCALE_FACTOR` / `QT_SCREEN_SCALE_FACTORS` 强制缩放因子，
`QT_QPA_PLATFORM=offscreen` 下同样生效。
所以可以在**本机（125% 缩放）**上把 app 放到 1.0/1.25/1.5/2.0 各跑一遍。

## 判据（都要能失败才有意义）

1. `--uicheck`（六页 offscreen 构建 + 渲染断言）必须通过；
2. `QApplication.devicePixelRatio` 要真的是设定值（**证明缩放真生效了**，
   否则这测试就是在 100% 下空跑，属于"假绿"）；
3. 主窗口在缩放后的**物理尺寸**合理（不能为 0，也不能小于逻辑尺寸的 1.0 倍）；
4. **截图里必须真有内容**（颜色种类 > 阈值）——防"整体空白/全黑"；
5. 字体渲染尺寸随缩放**变大**（否则就是没吃到缩放）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ok = _fail = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


PROBE = r'''
import os, sys, json
sys.path.insert(0, r"{here}")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
app = QApplication([])
for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"):
    if os.path.isfile(p):
        QFontDatabase.addApplicationFont(p)
dpr = app.devicePixelRatio()
scr = app.primaryScreen()
# ⚠️⚠️ **字体高度不能当判据**（第一版就用错了）：
# `QFontMetrics.height()` 返回的是 **逻辑像素**，Qt 内部再做缩放，
# 所以 13pt 在 100%/125%/150%/200% 下**都报 21px** —— 拿它写
# "字体随缩放变大"的断言，其实是在**拿常量比常量**，永远不可能失败（假绿）。
# 真正能证明"缩放吃到了"的是**渲染出来的物理像素数**：
# 同一个逻辑尺寸的窗口，DPR=2 时应产生约 4 倍像素（2×2）。
# 下面直接渲染一个窗口并回报它的物理尺寸。
from desktop.context import AppContext
from desktop.main_window import MainWindow
import desktop.pages.base as b
b.apply_theme("dark")
app.setStyleSheet(b.qss("dark"))
w = MainWindow(AppContext())
w.resize(1120, 760)
pg = w.show_page("welcome")
if hasattr(pg, "ensure_built"):
    pg.ensure_built()
for _ in range(10):
    app.processEvents()
img = w.grab()
# ⚠️ `grab()` 返回的是 **QPixmap**，不是 QImage（第一版按 QImage 用，
# 报 `'QPixmap' object has no attribute 'convertToFormat'`）。先 toImage()。
img = img.toImage()
# ✅ **内容占比**：截下来的图里"非背景色像素"的比例。
# 这是能真正抓到"高 DPI 把版面搞坏"的判据：
#   版面正常 → 六页切一页，内容占比是个稳定的小百分比；
#   版面被挤坏/控件落到可视区外/整片空白 → 占比**塌到接近 0**。
# 只断言"图非空"是不够的（一张全背景色的图也"非空"）。
bg = img.pixelColor(2, 2).rgb()
diff = 0
step = 3                      # 抽样，别逐像素（2240x1520 太慢）
for y in range(0, img.height(), step):
    for x in range(0, img.width(), step):
        if img.pixelColor(x, y).rgb() != bg:
            diff += 1
sampled = len(range(0, img.height(), step)) * len(range(0, img.width(), step))
out = {{
    "dpr": dpr,
    "font_height": 0,
    "screen_dpr": scr.devicePixelRatio(),
    "img_w": img.width(),
    "img_h": img.height(),
    "logical_w": w.width(),
    "logical_h": w.height(),
    "ink_ratio": diff / max(1, sampled),
}}
print("PROBE=" + json.dumps(out))
'''


def probe(dpr: str) -> dict:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCALE_FACTOR"] = dpr
    env["L2T_DATA_DIR"] = os.path.join(HERE, ".tmp", f"hidpi-{dpr}")
    env["L2T_HEADLESS"] = "1"
    code = PROBE.format(here=HERE)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       env=env, cwd=HERE, timeout=300)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    for ln in out.split("\n"):
        if ln.startswith("PROBE="):
            import json
            return json.loads(ln[6:])
    return {"error": out[-300:]}


def uicheck(dpr: str) -> tuple:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCALE_FACTOR"] = dpr
    env["L2T_DATA_DIR"] = os.path.join(HERE, ".tmp", f"hidpi-{dpr}")
    env["L2T_HEADLESS"] = "1"
    r = subprocess.run([sys.executable, os.path.join(HERE, "desktop", "uicheck.py")],
                       capture_output=True, env=env, cwd=HERE, timeout=900)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    return r.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser(description="桌面版高 DPI 检查")
    ap.add_argument("--dpr", default="", help="只跑这个缩放（如 1.25）")
    a = ap.parse_args()
    dprs = [a.dpr] if a.dpr else ["1.0", "1.25", "1.5", "2.0"]

    print("=" * 74)
    print("  桌面版（Qt）高 DPI 检查")
    print("=" * 74)
    base = None
    base_ink = None
    for dpr in dprs:
        print(f"\n[缩放 {dpr}]")
        p = probe(dpr)
        if "error" in p:
            check(False, f"{dpr}: 探测进程能跑", p["error"][:160])
            continue
        print(f"    devicePixelRatio = {p['dpr']}  "
              f"逻辑窗口 {p['logical_w']}x{p['logical_h']}  "
              f"渲染出的物理图 {p['img_w']}x{p['img_h']}")
        # ⚠️ 最重要的一条：缩放**真的生效**了吗？
        # 没生效的话后面几条都是在 100% 下空跑 —— 典型的"假绿"。
        want = float(dpr)
        check(abs(float(p["dpr"]) - want) < 0.06,
              f"{dpr}: Qt 真的按这个缩放跑（否则测试是空跑）",
              f"实得 dpr={p['dpr']}")

        code, out = uicheck(dpr)
        check(code == 0, f"{dpr}: uicheck 通过（六页构建+渲染断言）",
              out.strip()[-200:])

        # ⚠️ **缩放吃到没吃到，看渲染出的物理像素数**
        # （同一个逻辑尺寸 1120x760，DPR 越大，物理图越大）。
        # 面积比 ≈ dpr²；给足容差（窗口有边框、取整）。
        ratio_expected = want * want
        area_ratio = (p["img_w"] * p["img_h"]) / max(1, 1120 * 760)
        if base is None:
            base = area_ratio
            check(area_ratio > 0.9,
                  f"{dpr}: 基准（物理图面积 / 逻辑面积 ≈ {area_ratio:.2f}）")
        else:
            check(area_ratio > base * 1.2,
                  f"{dpr}: 物理渲染面积明显大于 100%（{area_ratio:.2f} vs {base:.2f}）",
                  "没变大 ⇒ 缩放没吃到渲染上")
        # 图不能是空的
        check(p["img_w"] > 200 and p["img_h"] > 200,
              f"{dpr}: 渲染出的图非空（{p['img_w']}x{p['img_h']}）")
        # ✅ 内容占比：抓"版面被高 DPI 挤坏 / 大片空白"
        ink = p.get("ink_ratio", 0.0)
        if base is None or base_ink is None:
            base_ink = ink
            check(ink > 0.02,
                  f"{dpr}: 基准内容占比 {ink*100:.1f}%（>2% 才算真画了东西）",
                  f"只有 {ink*100:.2f}%")
        else:
            check(ink > base_ink * 0.5,
                  f"{dpr}: 内容占比没塌（{ink*100:.1f}% vs 基准 "
                  f"{base_ink*100:.1f}%）",
                  "占比腰斩 ⇒ 版面很可能被挤坏/大量留白")

    print("\n" + "=" * 74)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 74)
    if _fail:
        print("  高 DPI 下有问题 —— 任务书 C 的这条还没达标")
        return 1
    print("  高 DPI（100/125/150/200%）下界面均可构建且渲染通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
