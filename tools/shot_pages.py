"""生成**全部页面的界面快照**（两种主题），供人工视觉核对。

    python tools/shot_pages.py [输出目录]        # 默认 docs/ui-shots/

## 为什么做成正式工具

任务书要求"逐屏人工视觉核对"。我没有看图能力（只能读像素），
**但我可以把"需要人看的东西"准备好** —— 别人打开一个目录就能一次看完
六个页面 × 两种主题，而不是自己起软件、逐个点过去。

自动化的 `tools/test_design_consistency.py` 负责"是否用的同一套尺子"；
这个脚本负责"给人看"，两者互补、都不能省。

## 用法

* 输出 12 张 PNG：`<主题>-<页面>.png`（浅色 6 张 + 深色 6 张）；
* 用 `offscreen` 平台渲染，**不弹窗口**、不改用户真实数据（沙箱目录）；
* 最后打一张清单（文件名 + 尺寸），方便对照。

⚠️ 截图前会**把启动时那个后台体检任务清掉**，否则「任务中心」页
会被一条无关的"环境体检"占着，看的人会以为那是产品自带的。
"""
from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# 沙箱：绝不碰用户真实 %LOCALAPPDATA%\Lyric2TTML
SANDBOX = os.path.join(HERE, ".tmp", "shotdata")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.environ["L2T_DATA_DIR"] = SANDBOX
os.environ["L2T_ALLOW_MULTI"] = "1"
os.environ["L2T_HEADLESS"] = "1"
# ⚠️⚠️ **必须用真实平台插件（windows），不能用 offscreen**（2026-10-15 踩到）：
# offscreen 下 Qt **看不到系统字体库**（只有 2 个字族，启动时还警告
# "Qt no longer ships fonts"），于是很多符号会画成**豆腐块**：
# 我第一版截图里 `▾`（显示高级选项的三角）就是空心方框，
# 一度以为产品缺字形。用 windows 平台验证后，`▾` 在 YaHei/Segoe/SimSun
# 下都是**正常的倒三角**（首行宽 22 → 末行宽 1）—— 那是**环境的假象**。
# 结论：**看图的工具必须用和用户一样的渲染路径**，否则看到的不是产品。
if not os.environ.get("L2T_SHOT_PLATFORM"):
    if sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
# 打包版不带字体，源码模式下显式挂系统中文字体，否则截图里全是方块
for _p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"):
    if os.path.isfile(_p):
        QFontDatabase.addApplicationFont(_p)

from desktop.context import AppContext  # noqa: E402
from desktop.main_window import NAV, MainWindow  # noqa: E402
from desktop.pages import base as b  # noqa: E402

print(f"  渲染平台: {app.platformName()}   "
      f"字体库字族数: {len(QFontDatabase.families())}")
if len(QFontDatabase.families()) < 20:
    print("  ⚠️ 字体库不完整 —— 截图里的符号可能显示成豆腐块（不代表产品问题）")

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "docs", "ui-shots")
os.makedirs(OUT, exist_ok=True)

SIZE = (1180, 820)


def main() -> int:
    print("=" * 74)
    print("  生成界面快照（人工视觉核对用）")
    print("=" * 74)
    made = []
    for theme in ("light", "dark"):
        b.apply_theme(theme)
        app.setStyleSheet(b.qss(theme))
        win = MainWindow(AppContext())
        win.resize(*SIZE)
        win.show()
        for _ in range(12):
            app.processEvents()
        # 清掉启动时的后台体检任务，别让它占着「任务中心」页
        try:
            for t in win.ctx.tasks.list(with_logs=False):
                if t.get("state") in ("queued", "running"):
                    win.ctx.tasks.cancel(t.get("id"))
        except Exception:                               # noqa: BLE001
            pass
        for _ in range(8):
            app.processEvents()
        for key, title, _tip in NAV:
            page = win.show_page(key)
            for _ in range(10):
                app.processEvents()
            page.ensure_built()
            for _ in range(8):
                app.processEvents()
            fn = os.path.join(OUT, f"{theme}-{key}.png")
            img = win.grab()
            img.save(fn)
            made.append((os.path.basename(fn), img.width(), img.height()))
            print(f"  ✓ {theme:5s} {key:9s} {title:12s} "
                  f"→ {img.width()}x{img.height()}")
        win.close()
        for _ in range(4):
            app.processEvents()

    print()
    print(f"  共 {len(made)} 张 → {os.path.relpath(OUT, HERE)}")
    print("  " + "=" * 70)
    print("  看一眼就能回答的问题：")
    print("   1. 六个页面像不像**同一套软件**（间距/字号/圆角/按钮是否一致）？")
    print("   2. 浅色是默认吗？深色有没有哪一页明显没跟上？")
    print("   3. 侧边栏选中项、图标、底部版本号是否清楚？")
    print("   4. 「歌词工作台」首屏能不能看到主操作（开始制作）？")
    return 0


if __name__ == "__main__":
    sys.exit(main())
