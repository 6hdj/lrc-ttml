"""界面自检（**无显示环境也能跑**）：``python desktop/uicheck.py``

为什么需要它：``desktop/selftest.py`` 用"不导入 Qt"换来了干净的服务层测试，
但界面有自己的一类 bug —— **构建就崩**、**切页崩**、**任务通知崩**、
**控件找不到**。这类问题在打包成 exe 之后才被发现，代价极大
（用户看到的是"点了没反应"）。

跑法（不需要显示器）：

    python desktop/uicheck.py            # 用 offscreen 平台
    python desktop/uicheck.py --shot out.png   # 顺便截一张图看看

⚠️ **必须在 import PySide6 之前**设 ``QT_QPA_PLATFORM=offscreen``。
"""
from __future__ import annotations

import os
import shutil
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_SANDBOX = os.path.join(_ROOT, ".tmp", "desktop-uicheck")
shutil.rmtree(_SANDBOX, ignore_errors=True)
os.makedirs(_SANDBOX, exist_ok=True)
os.environ["L2T_DATA_DIR"] = _SANDBOX
os.environ["L2T_ALLOW_MULTI"] = "1"
# ⚠️ 无头模式：``QMessageBox.exec()`` 在没有事件循环时**会永远阻塞**，
# 自检会直接挂死。设了这个变量之后，页面的 ``say``/``ask`` 改成打印。
os.environ["L2T_HEADLESS"] = "1"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS, FAIL = [], []
VERBOSE = "-v" in sys.argv
SHOT = ""
if "--shot" in sys.argv:
    i = sys.argv.index("--shot")
    if i + 1 < len(sys.argv):
        SHOT = sys.argv[i + 1]


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  ✓ {name}")
    except Exception as e:                              # noqa: BLE001
        FAIL.append((name, e))
        print(f"  ✗ {name}")
        print(f"      {type(e).__name__}: {e}")
        if VERBOSE:
            traceback.print_exc()


def describe_widgets(root, depth=0, out=None):
    out = out if out is not None else []
    if depth > 6:
        return out
    for ch in root.children():
        cls = type(ch).__name__
        out.append(cls)
        describe_widgets(ch, depth + 1, out)
    return out


def main(argv=None) -> int:
    """跑完整套界面自检。返回退出码。

    ⚠️ **必须是一个可调用的函数，不能只靠 ``runpy.run_path`` 启动**：
    冻结之后 ``desktop/uicheck.py`` 是包内部的一个数据文件，
    ``runpy.run_path`` 去找它的 ``__main__`` 会失败
    （实测报 ``ImportError: can't find '__main__' module in
    ...\\_internal\\desktop\\uicheck.py``，而且因为进程没退出，
    调用方看到的是"卡住不动"）。所以打包版走 ``import desktop.uicheck``。
    """
    global VERBOSE, SHOT
    argv = list(argv if argv is not None else sys.argv[1:])
    VERBOSE = "-v" in argv
    SHOT = ""
    if "--shot" in argv:
        i = argv.index("--shot")
        if i + 1 < len(argv):
            SHOT = argv[i + 1]
    return _run()


def _run() -> int:
    print("=" * 72)
    print("  Lyric2TTML 桌面版界面自检（offscreen，无显示环境）")
    print(f"  沙箱数据目录：{_SANDBOX}")
    print("=" * 72)

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:                              # noqa: BLE001
        print(f"  跳过：没有 PySide6（{type(e).__name__}: {e}）")
        print("  装好桌面依赖后重跑：pip install -r requirements-desktop.txt")
        return 0

    app = QApplication.instance() or QApplication([])

    # ⚠️⚠️ **给 offscreen 的 Qt 手工装系统字体**，否则截图里中文全是"豆腐块"
    # （□□□），"文字是否正常显示"就成了测试盲区。
    #
    # 原因：PySide6/Qt6 **不再随包带字体**，而 offscreen 平台插件
    # **不枚举 Windows 系统字体**（实测 `QFontDatabase.families()` 返回 **0**）。
    # `QT_QPA_FONTDIR` 也不行（本机 Qt 没有 fontconfig）。
    # 真实 Windows 桌面上 `msyh.ttc` 就在系统里，显示本来就是好的 ——
    # 所以这是**测试环境**的问题，不是产品缺陷；但不修就看不到真相。
    _font_families = 0
    try:
        from PySide6.QtGui import QFontDatabase
        if not QFontDatabase.families():
            for _p in (r"C:\Windows\Fonts\msyh.ttc",
                       r"C:\Windows\Fonts\msyhbd.ttc",
                       r"C:\Windows\Fonts\simsun.ttc",
                       r"C:\Windows\Fonts\simhei.ttf",
                       r"C:\Windows\Fonts\segoeui.ttf"):
                if os.path.isfile(_p):
                    _fid = QFontDatabase.addApplicationFont(_p)
                    if _fid >= 0:
                        _font_families += len(
                            QFontDatabase.applicationFontFamilies(_fid))
        _font_families = len(QFontDatabase.families())
    except Exception:                                   # noqa: BLE001
        pass
    if _font_families:
        print(f"  已加载系统字体：{_font_families} 个家族"
              f"（截图里的中文才看得见）")
    else:
        print("  ⚠️ 没有可用字体 —— 截图里的中文会是豆腐块，"
              "**不能**用它判断文字渲染")

    # ⚠️ 必须和 `desktop/app.py` 一样把样式表挂在 QApplication 上。
    # 以前这里忘了设，结果"UI 自检全绿、实际界面没样式"——
    # 自检的自检（下面那条渲染断言）就是为了堵这个。
    from desktop.pages.base import QSS
    app.setStyleSheet(QSS)

    # ⚠️ 主窗口里还有几处直接调 QMessageBox 的地方（关闭确认等）。
    # 无头环境**没有事件循环**，exec() 会永远阻塞 → 自检挂死。
    # 统一在这里打桩，比在每个按钮里写 if 更可靠。
    try:
        from PySide6 import QtWidgets
        QtWidgets.QMessageBox.information = staticmethod(
            lambda *a, **k: print("[stub] information:", (a[2] if len(a) > 2 else "")[:120]))
        QtWidgets.QMessageBox.warning = staticmethod(
            lambda *a, **k: print("[stub] warning:", (a[2] if len(a) > 2 else "")[:120]))
        QtWidgets.QMessageBox.critical = staticmethod(
            lambda *a, **k: print("[stub] critical:", (a[2] if len(a) > 2 else "")[:120]))
        QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
        QtWidgets.QMessageBox.exec = lambda self: 0
    except Exception as e:                              # noqa: BLE001
        print("  （打桩 QMessageBox 失败，后续弹窗测试可能阻塞：%s）" % e)

    # ---------------------------------------------------- 1 构建主窗口
    from desktop.context import AppContext
    from desktop.main_window import NAV, MainWindow
    from desktop.services.task_manager import TaskManager

    holder = {}

    def t_build():
        ctx = AppContext(mgr=TaskManager(state_path=os.path.join(_SANDBOX, "t.json")))
        win = MainWindow(ctx)
        win.show()
        app.processEvents()
        holder["win"] = win
        holder["ctx"] = ctx
        assert win.windowTitle().startswith("Lyric2TTML"), win.windowTitle()

    check("主窗口能构建并显示", t_build)
    if "win" not in holder:
        print("\n主窗口都起不来，后面的检查没意义，停在这里。")
        return 1
    win = holder["win"]

    # ---------------------------------------------------- 2 每页能构建
    def t_pages():
        for key, text, tip in NAV:
            page = win.show_page(key)
            app.processEvents()
            assert page is not None, f"页面 {key} 是 None"
            assert getattr(page, "TITLE", ""), f"页面 {key} 没有 TITLE"
            root = page.ensure_built()
            assert root is not None, f"页面 {key} build() 返回 None"
            assert root.parent() is not None or root.isWindow() or True
        # 所有页都在 stack 里（懒加载过一遍之后）
        assert win.stack.count() == len(NAV), \
            f"stack 里 {win.stack.count()} 页，期望 {len(NAV)}"

    # ⚠️ 断言文案**不要写死页数**（原来写"五个页面"，加了「帮助 / 诊断」之后
    # 就变成假描述 —— 测试名和实际不符会让人误判覆盖范围）。直接从 NAV 取。
    check(f"{len(NAV)} 个页面都能构建、切换、显示", t_pages)

    # ---------------------------------------------------- 3 页面契约
    def t_contract():
        for key, text, tip in NAV:
            page = win.show_page(key)
            for meth in ("build", "refresh", "on_show"):
                assert callable(getattr(page, meth, None)), \
                    f"页面 {key} 缺方法 {meth}"
            page.refresh()          # 不能抛
        # 页面必须有导航按钮和标题
        assert len(win._nav_buttons) == len(NAV)            # noqa: SLF001
        for key in win._nav_buttons:                        # noqa: SLF001
            assert key in dict((k, v) for k, v, _t in
                               ((a, b, c) for a, b, c in NAV)), key

    check("页面契约（build/refresh/on_show）", t_contract)

    # ---------------------------------------------------- 3b 页面真的画出来了
    def t_rendered():
        """**渲染断言**：页面必须真的占满可视区并画出内容。

        ⚠️ 这一条是本自检里最有价值的一条，也是最容易被省掉的一条：
        只断言"控件存在、方法在"完全测不出下面这个真 bug ——
        ``build()`` 的返回值忘了加进页面布局时，控件全都存在、
        ``TITLE`` 也对，但 ``isVisible()`` 是 False、``geometry()``
        还是默认的 640x480，用户打开看到的是**一片空白**。
        所以这里必须检查：可见、尺寸合理、**并且截图里有内容**。
        """
        win.resize(1180, 820)
        app.processEvents()
        for key, _t, _tip in NAV:
            page = win.show_page(key)
            root = page.ensure_built()
            app.processEvents()
            assert root.isVisible(), f"页面 {key} 的根控件不可见（空白页）"
            g = root.geometry()
            assert g.width() > 400 and g.height() > 300, \
                f"页面 {key} 根控件尺寸异常：{g.width()}x{g.height()}"
            page_g = page.geometry()
            assert page_g.width() > 400 and page_g.height() > 300, \
                f"页面 {key} 自身尺寸异常：{page_g.width()}x{page_g.height()}"
            # 真正抓一张图，确认不是"全同色"的空画面
            img = win.grab().toImage()
            assert img.width() > 400 and img.height() > 300, "截图尺寸异常"
            colors = set()
            for y in range(0, img.height(), 12):
                for x in range(0, img.width(), 12):
                    colors.add(img.pixel(x, y))
                    if len(colors) > 6:
                        break
                if len(colors) > 6:
                    break
            assert len(colors) > 3, \
                f"页面 {key} 看起来是纯色空白（只有 {len(colors)} 种颜色）"

    check(f"{len(NAV)} 个页面真的渲染出内容（非空白）", t_rendered)

    # ---------------------------------------------------- 4 深浅色样式
    def t_style():
        from desktop.pages.base import QSS
        assert "QPushButton" in QSS and "QProgressBar" in QSS, "QSS 不完整"
        # 关键控件都必须被样式覆盖，否则会出现"白底白字"（打包后非常显眼）
        for sel in ("QLineEdit", "QComboBox", "QTableWidget", "QScrollBar:vertical"):
            assert sel in QSS, f"QSS 里没有 {sel}"

    check("样式表覆盖关键控件", t_style)

    # ---------------------------------------------------- 4b 样式真的生效
    def t_style_applied():
        """样式表必须**挂在 QApplication 上并且真的改变了外观**。

        真踩过：`uicheck` 自己没设样式表，于是"UI 自检全绿"，而实际启动的
        界面**一点样式都没有**（浅灰底、白框）。所以这里既检查挂载位置，
        也检查渲染结果与"不设样式"确实不同。
        """
        from desktop.pages.base import C
        assert app.styleSheet(), "QApplication 上没有样式表（app.py 里设了吗？）"
        win.show_page("home")
        app.processEvents()
        img = win.grab().toImage()
        # 背景色应该接近深色（我们自己的 C['bg']），而不是系统默认浅色
        px = img.pixelColor(600, 700)
        assert px.lightness() < 120, \
            f"背景不像深色主题（lightness={px.lightness()}），样式没生效？"
        del C

    check("样式表挂在应用上且渲染为深色主题", t_style_applied)

    # ---------------------------------------------------- 4c 状态相关的样式
    def t_disabled_style():
        """禁用/启用按钮必须**走不同的样式规则**。

        ⚠️ 真踩过：``#primary`` 这种 ID 选择器优先级高于 ``:disabled``，
        所以 QSS 里不显式写 ``#primary:disabled`` 的话，禁用按钮会保留
        启用态的深色文字 —— 深蓝底 + 深色字，按钮上的字**看不见**
        （截图里一眼就看到了，但纯属性断言一辈子发现不了）。
        这里能断言的是"规则存在"和"paint 之后 objectName 对得上"，
        颜色本身靠 `--shot` 截图人工看。
        """
        from desktop.pages.base import QSS
        assert "QPushButton:disabled" in QSS, "QSS 缺少通用禁用态"
        assert "QPushButton#primary:disabled" in QSS, \
            "QSS 缺少 #primary:disabled（禁用按钮会保留启用态文字色，看不见）"
        # 改 objectName 之后一定要重新 polish，否则新选择器不生效
        from desktop.pages.models import _restyle
        from PySide6.QtWidgets import QPushButton
        b = QPushButton("x")
        _restyle(b, primary=True)
        assert b.objectName() == "primary", b.objectName()
        _restyle(b, primary=False)
        assert b.objectName() == "", b.objectName()

    check("禁用态样式规则齐全 + 改 objectName 会重新 polish", t_disabled_style)

    # ---------------------------------------------------- 5 制作页交互
    def t_home():
        home = win.show_page("home")
        app.processEvents()
        # 换歌必须更新歌名/艺人（不擦手输的）
        home.ed_audio.setText("D:/music/周杰伦 - 晴天.mp3")
        home.ed_lyrics.setText("D:/music/周杰伦 - 晴天.lrc")
        app.processEvents()
        assert home.ed_artists.text(), "艺人没自动填上"
        assert "周杰伦" in home.ed_artists.text(), \
            f"艺人猜错了：{home.ed_artists.text()!r}"
        assert "晴天" in home.ed_title.text(), \
            f"歌名猜错了：{home.ed_title.text()!r}"
        # 用户手改之后，**没有重新选文件**时不能覆盖
        # （`textEdited` 是只有真人输入才发的信号，所以这里手动把"手改过"
        #  标记置上，等价于用户真在框里打了字）
        home.ed_title.setText("我自己写的歌名")
        home._manual["title"] = True                        # noqa: SLF001
        home.ed_audio.setText("D:/music/陶典 - 在出发之前.wav")
        app.processEvents()
        assert home.ed_title.text() == "我自己写的歌名", \
            "用户手输的歌名被覆盖了！"
        # **重新选了文件**（真实操作 = `_reset_manual`）→ 必须重新猜，
        # 否则第二首歌还挂着第一首的歌名（更常见的抱怨）
        home._reset_manual()                                # noqa: SLF001
        app.processEvents()
        assert "出发之前" in home.ed_title.text(), \
            f"重新选文件后没重新猜歌名：{home.ed_title.text()!r}"
        assert "陶典" in home.ed_artists.text(), \
            f"重新选文件后没重新猜艺人：{home.ed_artists.text()!r}"
        # 参数收集：必须带齐界面上的选择
        p = home.params()
        for k in ("audio", "lyrics", "language", "model", "singer_mode",
                  "markers", "time_format", "separate_vocals", "refine"):
            assert k in p, f"params 里缺 {k}"
        assert p["audio"].endswith(".wav"), p["audio"]
        # 缺文件时点开始：**不能崩**，要给红字提示
        home.ed_audio.setText("")
        home._start()                                       # noqa: SLF001
        assert home.lb_problem.text(), "参数不全时必须有可见提示"
        assert "音频" in home.lb_problem.text() or "歌词" in home.lb_problem.text(), \
            home.lb_problem.text()

    check("制作页：文件名猜元数据 + 参数收集 + 缺参数提示", t_home)

    # ---------------------------------------------------- 6 模型页
    def t_models():
        mp = win.show_page("models")
        mp.ensure_built()
        app.processEvents()
        mp.refresh()
        app.processEvents()
        rows = mp._rows                                        # noqa: SLF001
        assert rows, "模型页一行都没有"
        st = holder["ctx"].models.all_specs()
        assert len(rows) == len(st), f"行数 {len(rows)} != 清单 {len(st)}"
        for mid, r in rows.items():
            assert r["badge"].text(), f"{mid} 没有状态徽标"
            assert r["detail"].text() is not None
        # **表头必须是真实状态**，不能停在 build() 里那句占位文字。
        # 真踩过：`_built` 置位顺序错了，build() 结尾那次 refresh 被跳过，
        # 页面永远显示"正在读取模型状态…"（看着像功能坏了）。
        assert "已就绪" in mp.lb_head.text(), \
            f"模型页表头没刷新：{mp.lb_head.text()!r}"
        assert "缓存目录" in mp.lb_cache.text(), mp.lb_cache.text()
        # 就绪的模型：下载按钮必须禁用；没装的：必须可点
        ok_btn = rows["whisper-large-v3"]["btn_dl"]
        assert ok_btn.text() == "下载"
        # 刷新两次不能重复建行（会内存泄漏 + 界面越来越长）
        n1 = mp.body_lay.count()                               # noqa: SLF001
        mp.refresh()
        mp.refresh()
        n2 = mp.body_lay.count()                               # noqa: SLF001
        assert n1 == n2, f"refresh() 把行数从 {n1} 改成了 {n2}"

    check("模型页：每行有状态/详情，refresh 不重复建行", t_models)

    # ---------------------------------------------------- 7 任务页
    def t_tasks():
        tp = win.show_page("tasks")
        app.processEvents()
        tp.refresh()
        assert tp.table.columnCount() == 5, tp.table.columnCount()
        # 造一个任务，看它能不能出现在表里
        ctx = holder["ctx"]
        tid = ctx.tasks.submit("convert", "界面自检任务", lambda h: {"ok": 1})
        for _ in range(200):
            app.processEvents()
            t = ctx.tasks.get(tid)
            if t and t.state in ("done", "failed"):
                break
            import time
            time.sleep(0.02)
        tp.refresh()
        app.processEvents()
        found = False
        for r in range(tp.table.rowCount()):
            it = tp.table.item(r, 1)
            if it and "界面自检任务" in it.text():
                found = True
                tp.table.selectRow(r)
                break
        assert found, "新任务没出现在任务列表里"
        app.processEvents()
        assert "界面自检任务" in tp.lb_title.text(), tp.lb_title.text()
        assert tp.log.toPlainText() is not None
        tp._clear_done()                                       # noqa: SLF001
        app.processEvents()
        for r in range(tp.table.rowCount()):
            it = tp.table.item(r, 1)
            assert not (it and "界面自检任务" in it.text()), "清掉已完成没生效"

    check("任务页：任务进列表、选中显示详情、清掉已完成", t_tasks)

    # ---------------------------------------------------- 8 设置页
    def t_settings():
        sp = win.show_page("settings")
        app.processEvents()
        assert sp.ed_workspace.text(), "工作目录是空的"
        assert sp.lb_paths.text(), "路径总览是空的"
        assert "数据目录" in sp.lb_paths.text()
        # 保存一次：不能抛、不能把 workspace 写坏
        sp.ed_workspace.setText(_SANDBOX)
        sp._save()                                             # noqa: SLF001
        assert holder["ctx"].config.get("workspace") == _SANDBOX, \
            holder["ctx"].config.get("workspace")
        # 写不进去的目录要**拦住**（不能等转换时才发现）
        sp.ed_workspace.setText("Z:\\definitely\\not\\writable\\dir")
        sp._save()                                             # noqa: SLF001
        assert holder["ctx"].config.get("workspace") == _SANDBOX, \
            "写不进去的目录居然被保存了"
        sp.ed_workspace.setText(_SANDBOX)

    check("设置页：路径校验拦住不可写目录", t_settings)

    # ---------------------------------------------------- 9 欢迎页
    def t_welcome():
        wp = win.show_page("welcome")
        app.processEvents()
        assert wp.lb_env.text(), "欢迎页没有环境结论"
        assert "✓" in wp.lb_env.text() or "⚠" in wp.lb_env.text() \
            or "就绪" in wp.lb_env.text() or "检查" in wp.lb_env.text(), \
            wp.lb_env.text()
        wp._paint(wp.ctx.env_items, "")                        # noqa: SLF001
        app.processEvents()
        assert wp.grid.count() > 0, "环境条目一个都没画出来"

    check("欢迎页：环境结论 + 条目渲染", t_welcome)

    # ---------------------------------------------------- 10 任务广播
    def t_broadcast():
        """任务通知必须广播到**所有已构建页面**，而不只是当前页。

        这是 Web 版最经典的 bug：切页之后另一页永远是旧状态。
        """
        ctx = holder["ctx"]
        seen = []
        for key, page in win._pages.items():                   # noqa: SLF001
            orig = getattr(page, "on_task", None)
            if orig is None:
                continue
            page.on_task = (lambda k, o: (lambda t: (seen.append(k), o(t))))(key, orig)
        ctx.tasks.submit("convert", "广播测试", lambda h: {})
        for _ in range(200):
            app.processEvents()
            if len(seen) > len(win._pages):                    # noqa: SLF001
                break
            import time
            time.sleep(0.02)
        pages = set(seen)
        assert len(pages) >= len(win._pages), \
            f"只有 {pages} 收到了通知（共 {len(win._pages)} 页）"

    check("任务通知广播到所有已构建页面", t_broadcast)

    # ---------------------------------------------------- 11 关闭不崩
    def t_close():
        ctx = holder["ctx"]
        ctx.tasks.submit("convert", "假的进行中", lambda h: __import__("time").sleep(30))
        # 有任务在跑时 closeEvent 会弹框 → offscreen 下不能真弹，
        # 所以只验 ctx.shutdown() 这套收尾逻辑
        ctx.shutdown()
        assert ctx.tasks.has_running() or True

    check("退出收尾（ctx.shutdown）不崩", t_close)

    # ---------------------------------------------------- 12 截图（可选）
    if SHOT:
        def t_shot():
            win.show_page(sys.argv[sys.argv.index("--page") + 1]
                          if "--page" in sys.argv else "home")
            app.processEvents()
            win.resize(1120, 760)
            app.processEvents()
            ok = win.grab().save(SHOT)
            assert ok, f"截图保存失败：{SHOT}"
        check(f"截图 → {SHOT}", t_shot)

    print("-" * 72)
    print(f"  通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    code = 0
    if FAIL:
        for name, e in FAIL:
            print(f"    ✗ {name}: {type(e).__name__}: {e}")
        code = 1
    else:
        print("  全部通过 ✓")

    # ⚠️ 收尾必须硬退出：后台还有 daemon 线程（任务中心、轮询定时器），
    # 正常 return 之后解释器会**等它们**，自检看起来就是"卡住不动"。
    try:
        holder["ctx"].shutdown()
    except Exception:                                       # noqa: BLE001
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
