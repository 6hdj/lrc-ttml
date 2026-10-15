"""界面参数**真的传下去了**吗 —— 专抓"纯装饰控件"。

    python tools/test_home_params.py

## 为什么单开一条

2026-10-15 用户实测两条：

* **「修改输出地址失效」** —— 欢迎页有「输出到」目录框 + 「…」按钮，
  但 `home.params()` **从来没把它传下去**，后端又写死 `output_path=None`。
  控件长得像能用、点了没反应。
* **「补全句尾拖音失效」** —— 它依赖人声轨，没开「人声分离」时只是静默跳过。

`tools/audit_params.py` 只审 `ConvertOptions` 字段（"接了没人读的死字段"），
**审不到"界面控件压根没进 params"** —— 所以这里从**页面对象**出发查。

⚠️ 不需要显示设备（offscreen）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("L2T_HEADLESS", "1")
    import tempfile

    sandbox = tempfile.mkdtemp(prefix="l2t-homeparams-")
    os.environ["L2T_APP_DIR"] = sandbox
    os.environ["L2T_DATA_DIR"] = sandbox

    _ok = _fail = 0

    def check(cond, label, extra=""):
        nonlocal _ok, _fail
        if cond:
            _ok += 1
            print(f"  OK   {label}")
        else:
            _fail += 1
            print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from desktop.context import AppContext
    from desktop.pages.home import HomePage

    ctx = AppContext()
    page = HomePage(ctx)
    page.ensure_built()
    page.show()

    print("=" * 76)
    print("  ① 「输出到」必须真的进 params")
    print("=" * 76)
    p = page.params()
    check("output_dir" in p,
          "`params()` 里有 output_dir（以前根本没有这个键）", str(sorted(p))[:100])
    if "output_dir" in p:
        check(str(p["output_dir"]) == page.ed_output.text().strip(),
              "取的就是输入框里的值",
              f"{p['output_dir']!r} vs {page.ed_output.text()!r}")
    # 改一个自定义目录，params 必须跟着变
    import os as _os
    custom = _os.path.join(sandbox, "我的输出")
    page.ed_output.setText(custom)
    check(page.params().get("output_dir") == custom,
          "改了输入框 → params 跟着变（不是写死的默认值）",
          str(page.params().get("output_dir")))

    print()
    print("=" * 76)
    print("  ② 勾了「补全句尾拖音」但没勾「人声分离」→ 必须自动打开")
    print("=" * 76)
    page.chk_tails.setChecked(True)
    page.chk_separate.setChecked(False)
    page._start()          # 只走到"提交转换"之前；参数已经确定
    check(page.chk_separate.isChecked(),
          "自动勾上「人声分离」（拖音需要人声轨）",
          f"tails={page.chk_tails.isChecked()} separate={page.chk_separate.isChecked()}")
    check("人声分离" in page.log_box.toPlainText(),
          "日志里说明了为什么自动打开", page.log_box.toPlainText()[:60])

    print()
    print("=" * 76)
    print(f"  通过 {_ok} 项，失败 {_fail} 项")
    print("=" * 76)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
