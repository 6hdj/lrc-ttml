"""**一键产出「正式构建交付报告」**（任务书 G）。

    python tools/release_report.py                      # 跑全部检查并写报告
    python tools/release_report.py --quick              # 跳过耗时项（冻结版 E2E）
    python tools/release_report.py --out docs/交付报告.md

## 为什么要有它（不是"再写一个文档"）

任务书 G 要求交付里包含：**可重复的构建命令 / 体积报告 / 冻结版测试报告 /
安装·升级·卸载测试结果 / 已知问题与未验证项清单**。

这些东西之前**散在**：
* 体积 → `tools/size_report.py`（要自己跑）；
* 冻结版 → `tools/accept_frozen.py` / `test_peruser_e2e.py`；
* 安装/升级/卸载 → 四个 `tools/test_*.py`；
* **未验证项 → 只在对话和 `docs/` 里，没有机器可核对的版本**。

后果：**"未验证项清单"只能靠我记性好** —— 我每轮report 的"未验证"是手写的，
既可能漏、也可能随着代码变化过期（比如某轮修好了却还写着未验证）。

本工具把整件事变成**可重复跑一条命令**：
1. 依次跑所有检查，**记录每项的真实退出码与耗时**；
2. 分四档标注状态，与任务书要求一致：
   `源码可跑 / 冻结版可跑 / 安装后可用 / 干净机可用`；
3. 把"本环境**做不到**的检查"（例如干净机、提权装 Program Files）
   单独列成**未验证清单**，并写清**为什么做不到** —— 而不是含糊地说"未验证"；
4. 体积、产物大小、exe 清单/图标/版本信息一并带上；
5. 写进 `docs/交付报告.md`（带生成时间与每条命令，可复核）。

⚠️ **绝不"跑失败就跳过"**：任何一项失败都会在报告里显眼标红，
并且脚本以非 0 退出（否则报告会变成"全绿"的自我安慰）。
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
OUT_DEFAULT = os.path.join(HERE, "docs", "交付报告.md")

# 四档状态（与任务书一致）
LEVELS = ["源码可跑", "冻结版可跑", "安装后可用", "干净机可用（近似）"]

# (档次, 名称, 命令, 是否耗时, 说明)
CHECKS = [
    ("源码可跑", "离线自检", ["selftest.py"], False,
     "核心算法/格式规范/编辑台纯逻辑"),
    ("源码可跑", "服务层自检", ["desktop/selftest.py"], False,
     "路径/配置/任务/模型/参数映射（不导入 Qt）"),
    ("源码可跑", "界面自检", ["desktop/uicheck.py"], False,
     "offscreen 构建 + **渲染断言**（六页都不空白）"),
    ("源码可跑", "主题一致性", ["tools/test_theme.py"], False,
     "深浅两套键集相同 + WCAG 对比度 + QSS 可生成"),
    ("源码可跑", "界面文案检查", ["tools/scan_ui_markdown.py"], False,
     "纯文本控件里没有漏出的 Markdown 记号"),
    ("源码可跑", "卸载逻辑", ["tools/test_uninstall.py"], False,
     "默认保留模型；搬不动就什么都不删"),
    ("源码可跑", "卸载可重试性", ["tools/test_uninstall_retry.py"], False,
     "延迟清理能真正删干净、且不自毁运行库"),
    ("源码可跑", "升级覆盖安装", ["tools/test_upgrade.py"], False,
     "覆盖升级不丢用户数据；只读/占用文件处理"),
    ("源码可跑", "安装器主题一致", ["tools/test_installer_theme.py"], False,
     "安装程序与桌面版逐色一致"),
    ("源码可跑", "per-user 要求", ["tools/test_peruser.py"], False,
     "默认用户目录/不写 HKLM/名称一致"),
    ("源码可跑", "权限不足路径", ["tools/test_perm_denied.py"], False,
     "指向不可写目录时给人话 + 提管理员那条路"),
    ("源码可跑", "模型缓存复用", ["tools/test_hf_cache.py"], False,
     "L2T_HF_CACHE / HUGGINGFACE_HUB_CACHE 都能生效"
     "（只认一个的话用户会白下 3GB —— 真踩过）"),
    ("源码可跑", "GPU 运行时按需下载", ["tools/test_cuda_runtime.py"], False,
     "方案 D 的链路：清单对账 zip 的 SHA256/大小、解压布局、\n"
     "cuda_dirs() 能找到、没配 URL 时如实报错（含阴性对照）"),
    ("源码可跑", "缺 GPU 运行时的前置提示", ["tools/test_gpu_guard.py"], False,
     "点「开始制作」时的检查：就绪不拦、缺件指向模型页、\n"
     "**没 N 卡不让人白下 529 MB**、清单没组件/检查器坏都不拦"),
    ("源码可跑", "参数生效性", ["tools/test_param_effect.py"], False,
     "每个界面选项改一遍、产物真的变了"),
    ("源码可跑", "文档同步", ["tools/gen_module_docs.py", "--check"], False,
     "模块说明与源码一致"),

    ("冻结版可跑", "冻结版验收",
     ["tools/accept_frozen.py", "dist/Lyric2TTML/Lyric2TTML.exe"], True,
     "--version/--help/--doctor/--uicheck + 真转换一次\n"
     "⚠️ 必须把**主程序 exe 路径**传进去：不传的话它按默认去找，"
     "会走一条很慢的路（实测 1019 秒），而且未必测的是我们要发的那个产物。"),
    ("冻结版可跑", "应用图标", ["tools/test_icon.py"], False,
     "exe 里嵌的是**我们的**图标（不是 PyInstaller 默认的）"),

    ("安装后可用", "安装·启动·卸载闭环", ["tools/test_peruser_e2e.py"], True,
     "用正式安装程序真装一遍（普通用户），验桌面窗口与卸载"),
    ("安装后可用", "真实卸载流程", ["tools/test_uninstall_real.py"], True,
     "真载荷装完后卸载，目录**彻底删除**"),

    # ⚠️ 这一档是**近似**，不是真的干净机 —— 名字里就写明，别让读者误会。
    # 它把"开发机能提供的一切"尽量拿掉（清 `L2T_*`、PATH 只留真机必有的
    # 系统目录、全新的 LOCALAPPDATA/USERPROFILE、素材复制进沙箱），
    # 再看程序自己的 --version/--doctor/--uicheck/--model-cli 能不能跑。
    # 真干净机（无 Python 的 Win10/11）仍然**未验证**，见第四节。
    ("干净机可用（近似）", "隔离环境全流程", ["tools/test_isolated.py"], True,
     "清掉全部开发环境痕迹后：安装 → 三个自检入口 → 模型清单 → 卸载\n"
     "⚠️ 这是**近似**：覆盖不到 缺 VC++ 运行库 / 缺显卡驱动 / 无音频设备 /\n"
     "全新注册表与 COM 状态 / 磁盘权限策略差异。**不能**替代真干净机验证；\n"
     "但跑不通**一定**是问题。"),
]

# 本环境做不到的检查 —— 必须写清"为什么做不到"，不能含糊说"未验证"
CANNOT_VERIFY = [
    ("干净机可用（近似）", "**真**干净机：无 Python 的 Win10/11 上安装并运行",
     "需要一台**没装过 Python/没装过本软件**的 Win10/11 虚拟机。"
     "本脚本只能做**近似**（清环境变量/PATH/用户目录），"
     "覆盖不到：缺 VC++ 运行库、缺显卡驱动、无音频设备、"
     "全新注册表与 COM 状态、磁盘权限策略差异。"
     "**近似通过不等于真机通过**"),
    ("安装后可用", "提权后真正装进 `C:\\Program Files`",
     "本会话进程实测 `IsUserAnAdmin() == False`，**无法提权**，"
     "因此只能验证它的失败面（见「权限不足路径」）；"
     "成功面需要管理员环境或真机右键「以管理员身份运行」"),
    ("安装后可用", "普通用户双击**不弹 UAC**（真人操作）",
     "本会话无法产生真人双击；可给的替代证据是："
     "exe 嵌入清单为 `asInvoker`（OS 据此决定不弹）+ 无运行时自提权代码 + "
     "非管理员进程能完整跑完安装"),
    ("安装后可用", "`MoveFileEx` 重启删除被占用 exe",
     "需要真重启一次 Windows 才能确认那个文件消失；"
     "本会话不重启"),
    ("源码可跑", "真实 Windows 会话下 125%/150%/200% 缩放",
     "需要真人改系统缩放并肉眼看界面；"
     "offscreen 渲染只证明布局与配色，不证明 DPI 缩放后的观感"),
    ("源码可跑", "「跟随系统」主题在真切换系统主题后复测",
     "本会话无法改（也不该改）用户的系统主题设置"),
]


def run(cmd: list, timeout: int = 3600, interp: str = "") -> tuple:
    """跑一条检查，返回 (退出码, 秒数, 输出尾部)。

    ⚠️⚠️ `interp` 必须按检查项指定：**跑 UI 的检查要用 `.venv` 的解释器**，
    因为 PySide6 只装在 `.venv` 里（`.venvpack` 是打包用的、故意不带 Qt）。
    第一版图省事全用 `sys.executable`，结果 `tools/test_theme.py`
    直接 `ModuleNotFoundError: No module named 'PySide6'` ——
    **报告里的"失败"其实是报告工具自己用错了 Python**（真踩到）。
    其余不依赖 Qt 的检查继续用当前解释器（够用、启动快）。
    """
    exe = interp or sys.executable
    t0 = time.time()
    try:
        r = subprocess.run([exe, *cmd], cwd=HERE,
                           capture_output=True, timeout=timeout,
                           env=dict(os.environ, TMP=os.path.join(HERE, ".tmp", "rr"),
                                    TEMP=os.path.join(HERE, ".tmp", "rr")))
        code = r.returncode
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"超时（>{timeout}s）"
    return code, time.time() - t0, (out + ("\n" + err if err.strip() else ""))


def venv_python() -> str:
    """`.venv` 里的解释器（装了 PySide6）；找不到就退回当前解释器。"""
    p = os.path.join(HERE, ".venv", "Scripts", "python.exe")
    return p if os.path.isfile(p) else sys.executable


def artifacts() -> list:
    """交付产物：大小 + exe 的清单/图标/版本信息。"""
    rows = []
    inst = os.path.join(HERE, "dist", "安装程序.exe")
    app = os.path.join(HERE, "dist", "Lyric2TTML", "Lyric2TTML.exe")
    for label, p in (("安装程序", inst), ("主程序", app)):
        if not os.path.isfile(p):
            rows.append((label, p, None, "", "", ""))
            continue
        size = os.path.getsize(p)
        ver = ""
        try:
            import tools.test_peruser as tp  # noqa
            mf = tp.read_manifest(p)
        except Exception:                               # noqa: BLE001
            mf = "?"
        try:
            import tools.test_icon as ti
            w, r, g, b = ti.dominant_blue(p)
            ic = f"{w}px 采样 rgb({r},{g},{b})" if w else "未取到"
        except Exception:                               # noqa: BLE001
            ic = "?"
        # 版本信息用 PowerShell 取（PyInstaller 的 version 资源）
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Item '{p}').VersionInfo | "
                 f"Select-Object -Property ProductName,FileVersion | ConvertTo-Json"],
                capture_output=True, timeout=60)
            j = json.loads((r.stdout or b"{}").decode("utf-8", "replace") or "{}")
            ver = f"{j.get('ProductName','')} {j.get('FileVersion','')}".strip()
        except Exception:                               # noqa: BLE001
            ver = ""
        rows.append((label, p, size, mf, ic, ver))
    return rows


def _needs_qt(cmd: list) -> bool:
    """这条检查是不是需要 PySide6（只有 `.venv` 装了 Qt）。

    判据用**脚本路径**，不额外加字段 —— 加字段就得改 17 条元组，
    容易漏；而"哪些脚本会建界面"其实从名字就能看出来。
    ⚠️ 新增会 import PySide6 的检查时，**记得把它加进这个列表**，
    否则报告里会出现"失败"，而真相是解释器用错了。
    """
    s = " ".join(cmd)
    return any(k in s for k in ("uicheck", "test_theme", "test_param_effect",
                               "test_icon", "test_gpu_guard"))


def main() -> int:
    ap = argparse.ArgumentParser(description="产出正式构建交付报告")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--quick", action="store_true",
                    help="跳过耗时项（冻结版验收 / 安装闭环）")
    a = ap.parse_args()

    os.makedirs(os.path.join(HERE, ".tmp", "rr"), exist_ok=True)
    print("=" * 74)
    print("  正式构建交付报告")
    print("=" * 74)

    results = []
    for level, name, cmd, slow, why in CHECKS:
        if a.quick and slow:
            results.append((level, name, cmd, None, 0.0, "被 --quick 跳过", why))
            print(f"  [跳过] {name}（--quick）")
            continue
        print(f"  [跑] {name} …", end="", flush=True)
        code, dt, out = run(cmd, interp=venv_python() if _needs_qt(cmd) else "")
        state = "通过" if code == 0 else f"**失败**(exit={code})"
        tail = ""
        if code != 0:
            lines = [ln for ln in out.strip().split("\n") if ln.strip()]
            tail = "\n".join(lines[-12:])
        results.append((level, name, cmd, code, dt, state, why))
        print(f" {'✓' if code == 0 else '✗'} {dt:.1f}s")

    art = artifacts()
    failed = [r for r in results if r[3] not in (0, None)]

    # ------------------------------------------------ 写报告
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L = []
    L.append("# Lyric2TTML 正式构建交付报告\n")
    L.append(f"> 自动生成：`python tools/release_report.py`　"
             f"生成时间：**{now}**\n")
    L.append("本报告里的**每一行都是真跑出来的**（命令 + 退出码 + 耗时）。"
             "跑失败的项会**显眼标红**，脚本本身也会以非 0 退出 —— "
             "不会出现「跑挂了但报告全绿」的情况。\n")

    L.append("\n## 一、总体结论\n")
    if failed:
        L.append(f"❌ **有 {len(failed)} 项未通过**，当前提交**不可发布**：\n")
        for level, name, cmd, code, dt, state, why in failed:
            L.append(f"* `{name}` —— exit={code}")
    elif a.quick:
        L.append("⚠️ 本次是 `--quick`（跳过了耗时项），**不能据此判定可发布**。")
    else:
        L.append("✅ **本次跑到的检查全部通过。**\n")
        L.append("⚠️ 但**「没跑到的」不等于「没问题」** —— "
                 "见第四节的未验证清单。\n")

    L.append("\n## 二、四种状态分别到了哪一步\n")
    L.append("| 状态 | 含义 | 本次证据 |")
    L.append("| --- | --- | --- |")
    by_level = {}
    for level, name, cmd, code, dt, state, why in results:
        by_level.setdefault(level, []).append((name, code, state))
    meaning = {
        "源码可跑": "直接跑 Python 源码",
        "冻结版可跑": "PyInstaller 产物能启动并完成一次真转换",
        "安装后可用": "真安装程序装到磁盘后能用、能卸载",
        "干净机可用（近似）": "清掉开发环境痕迹后仍可用（**近似**，非真干净机）",
    }
    for lv in LEVELS:
        items = by_level.get(lv, [])
        if not items:
            L.append(f"| {lv} | {meaning[lv]} | **没跑**（见未验证清单） |")
            continue
        ok = sum(1 for _n, c, _s in items if c == 0)
        L.append(f"| {lv} | {meaning[lv]} | {ok}/{len(items)} 通过 |")

    L.append("\n## 三、逐项结果\n")
    for lv in LEVELS:
        items = [r for r in results if r[0] == lv]
        if not items:
            continue
        L.append(f"\n### {lv}\n")
        L.append("| 检查 | 命令 | 退出码 | 耗时 | 结论 |")
        L.append("| --- | --- | --- | --- | --- |")
        for level, name, cmd, code, dt, state, why in items:
            c = "—" if code is None else str(code)
            mark = "✅" if code == 0 else ("⏭" if code is None else "❌")
            L.append(f"| {name} | `{' '.join(cmd)}` | {c} | {dt:.1f}s | {mark} {state} |")
        L.append("")
        for level, name, cmd, code, dt, state, why in items:
            L.append(f"* **{name}**：{why}")
            if code not in (0, None):
                L.append("")
                L.append("  ```")
                for ln in (state,):
                    L.append(f"  {ln}")
                L.append("  ```")

    L.append("\n## 四、未验证项清单（本环境做不到的，逐条说明原因）\n")
    L.append("| 状态 | 未验证的事 | 为什么做不到 |")
    L.append("| --- | --- | --- |")
    for lv, what, why in CANNOT_VERIFY:
        L.append(f"| {lv} | {what} | {why} |")
    L.append("")
    L.append("⚠️ 这一节是**手写维护**的（`tools/release_report.py` 里的 "
             "`CANNOT_VERIFY`），不是跑出来的。它的价值在于："
             "**把「没验证」当成交付物的一部分明写出来**，而不是含糊地说"
             "「应该没问题」。每轮修掉一条就删一条。\n")

    L.append("\n## 五、交付产物\n")
    L.append("| 产物 | 路径 | 大小 | 嵌入清单 | 图标 | 版本信息 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for label, p, size, mf, ic, ver in art:
        rel = os.path.relpath(p, HERE)
        s = f"{size / 1048576:.1f} MB" if size else "**缺失**"
        mfs = "asInvoker（不提权）" if "asInvoker" in mf else (mf or "—")
        L.append(f"| {label} | `{rel}` | {s} | {mfs} | {ic} | {ver or '—'} |")

    L.append("\n## 六、可重复的构建命令\n")
    L.append("```powershell")
    L.append("# 1) 依赖：主程序用打包环境（.venvpack），界面需要 Qt")
    L.append("$env:L2T_WITH_QT=1")
    L.append("")
    L.append("# 2) 生成图标与版本资源（缺了会退回 PyInstaller 默认图标/空版本号）")
    L.append("python tools/make_icon.py")
    L.append("python tools/make_version_info.py")
    L.append("")
    L.append("# 3) 构建主程序（onedir）")
    L.append(".venvpack\\Scripts\\python.exe -m PyInstaller --noconfirm \\")
    L.append("    --distpath dist/_app --workpath build/_app Lyric2TTML.spec")
    L.append("")
    L.append("# 4) 组装载荷并打安装程序")
    L.append("python tools/clean_build.py --dist Lyric2TTML")
    L.append("Copy-Item -Recurse dist/_app/Lyric2TTML dist/Lyric2TTML")
    L.append("python tools/build_installer_only.py   # 需 L2T_WITH_QT=1")
    L.append("")
    L.append("# 5) 验收（本报告就是这一步的产物）")
    L.append("python tools/release_report.py")
    L.append("```")
    L.append("")
    L.append("⚠️ 第 3 步**必须重建主程序**：`uninstall_core.py` / `desktop/*` 是"
             "**编译进 `Lyric2TTML.exe`** 的，只重打安装包（第 4 步）不会带上"
             "代码改动 —— 本仓真踩过，白查两轮。")

    L.append("\n## 七、体积明细\n")
    try:
        r = subprocess.run([sys.executable, "tools/size_report.py", "--limit", "12"],
                           cwd=HERE, capture_output=True, timeout=600)
        body = (r.stdout or b"").decode("utf-8", "replace").strip()
        L.append("```")
        L.append(body[:3000])
        L.append("```")
    except Exception as e:                              # noqa: BLE001
        L.append(f"（体积报告生成失败：{type(e).__name__}: {e}）")

    io.open(a.out, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
    print(f"\n  已写出 {os.path.relpath(a.out, HERE)}")
    print(f"  通过 {len(results) - len(failed)}/{len(results)}，失败 {len(failed)}")
    if failed:
        print("  ❌ 有失败项 —— 不要发布")
        return 1
    if a.quick:
        print("  ⚠️ --quick：跳过了耗时项，不能据此判定可发布")
        return 0
    print("  ✓ 全部通过（未验证项见报告第四节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
