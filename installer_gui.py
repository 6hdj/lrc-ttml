"""图形化安装程序（像普通 Windows 软件那样：检查 → 提示缺什么 → 自动补全）。

打包成 `安装程序.exe` 后，用户双击即可，**不需要命令行、不需要 Python**。

    python installer_gui.py            # 直接运行（开发时）
    pyinstaller ... installer_gui.py   # 见 tools/build_release.py --installer
    Lyric2TTML.exe --installer         # 打包版里的入口

界面流程（四步，一个窗口）：
    ① 欢迎：说明这个程序要干什么
    ② 检查：逐项列出需要的东西，缺的标红
    ③ 补全：自动下载/安装，实时日志
    ④ 完成：告诉用户可以启动了

⚠️ 所有耗时操作（下载、pip）都放在**后台线程**里，主线程只负责刷新界面 ——
   tkinter 不允许在非主线程碰控件，直接用会随机崩。
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

FROZEN = getattr(sys, "frozen", False)
BASE = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE

# 复用 install.py 的补全动作 + envcheck.py 的**穷尽式**检查
import envcheck  # noqa: E402
import install as core  # noqa: E402

# ==========================================================================
#  视觉：与桌面版**同一套调色板**（这是本轮改的重点）
# ==========================================================================
# ⚠️⚠️ 以前这里是另抄的一份颜色，和 `desktop/pages/base.py` 的 `C` **几乎每个色
# 都不一样**（BG #0f1117 vs #14161a、DIM #8b95ad vs #9aa3b0、
# OK #3ddc84 vs #3ecf8e、BAD #ff6b6b vs #ef5350、WARN #ffc857 vs #e2b53e…）。
# 后果：**安装程序和应用看起来是两个不相干的产品** —— 用户装完打开应用会觉得
# "这不是我刚装的那个东西"。截图一比就看出来了（`.tmp/inst_before.png`）。
#
# 现在**直接对齐** base.py 的取值（不再各写一份）。理想做法是 import 它，
# 但安装器是**独立打包**的、且要在没装 Qt 的环境里跑，所以只能复制常量；
# 复制就**必须有测试守着**（`tools/test_installer_theme.py` 会比对两边是否一致），
# 否则迟早又漂移。
BG = "#14161a"          # 与 desktop/pages/base.py C['bg'] 一致
PANEL = "#1b1e24"       # 卡片/面板底色
PANEL2 = "#22262e"      # 输入框/更深一层的底色
LINE = "#2c323c"        # 描边、分隔线
FG = "#e8eaee"          # 正文
DIM = "#9aa3b0"         # 次要文字
FAINT = "#6b7480"       # 最弱提示
OK = "#3ecf8e"
BAD = "#ef5350"
WARN = "#e2b53e"
ACCENT = "#4785d6"      # 与 desktop/pages/base.py 的 DARK["accent"] 一致
                        # （`tools/test_installer_theme.py` 会守着这一条；
                        #  改一边忘了另一边，安装程序和应用就不是一个产品了）
ACCENT_DIM = "#2b5f9e"
CYAN = "#22d3ee"        # 强调/进度

# 与桌面版一致的字体栈（Qt 的 QSS 和 tkinter 都要写同一串）
FONT = "Microsoft YaHei UI"


def _style_progressbar(root: tk.Tk) -> None:
    """给 ttk 进度条套上我们的调色板（默认样式是浅灰的，在深色界面上很突兀）。"""
    try:
        from tkinter import ttk
        st = ttk.Style(root)
        # clam 主题才吃自定义颜色（Windows 原生主题会忽略背景色）
        try:
            st.theme_use("clam")
        except Exception:                                   # noqa: BLE001
            pass
        st.configure("L2T.Horizontal.TProgressbar",
                     troughcolor=PANEL2, background=CYAN,
                     bordercolor=PANEL2, lightcolor=CYAN, darkcolor=CYAN,
                     thickness=14)
        # ⚠️ `thickness` 太小（试过 8）时，进度条看起来就是一条**分隔线**，
        # 用户根本认不出那是进度条（截图里一眼可见）。14 才像进度条。
        st.configure("L2T.Horizontal.TProgressbar", borderwidth=0,
                     relief="flat")
    except Exception:                                       # noqa: BLE001
        pass


def _font(size: int, bold: bool = False):
    """统一字体：字号与桌面版对齐，中文用同一族。"""
    return (FONT, size, "bold") if bold else (FONT, size)


def ask_app_report(base: str) -> dict:
    """问**主程序**要环境报告。

    ⚠️⚠️ 这是这个安装程序最要紧的一处设计。**不能让安装程序检查自己**：
    它是独立的 PyInstaller onefile，进程里的 Python 是打包时的临时环境，
    为了减小体积又特意排除了 numpy / ctranslate2 / onnxruntime ——
    于是 `importlib.import_module("numpy")` 必然抛 ModuleNotFoundError，
    界面就会谎报"numpy 未安装 / faster-whisper 未安装"（**真踩过**，
    用户看到一排红叉，其实主程序跑得好好的）。

    正确分工：**主程序报自己的环境，安装程序只负责读结果 + 补全**。
    找不到主程序（比如源码模式）才退回进程内自检。
    """
    import json
    exe = os.path.join(base, "Lyric2TTML.exe")
    if not os.path.exists(exe):
        return {}
    try:
        # ⚠️⚠️ **优先用 `--launch-check`**（2026-10-14 加）。
        # 它比 `--doctor --json` 多两样这次需要的东西：
        #   ① 每项的 **`scope`**（launch / convert / extra）——
        #      安装程序据此只拦"影响打开"的，而不是把 3 GB 模型也算必需；
        #   ② `can_open` / `launch_blockers` / `convert_pending` 的**结论**。
        # `--doctor --json` 保留为退路（老版本主程序没有 --launch-check）。
        r = subprocess.run([exe, "--launch-check"], cwd=base,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        line = ""
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("{"):
                line = ln
                break
        if line:
            d = json.loads(line)
            if d.get("items"):
                print("[检查] 用主程序 --launch-check（带 scope）", flush=True)
                return d
        # 退路：老主程序只有 --doctor
        r = subprocess.run([exe, "--doctor", "--json"], cwd=base,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        line = ""
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("{"):
                line = ln
                break
        if line:
            print("[检查] 退路：用主程序 --doctor --json（无 scope）", flush=True)
        return json.loads(line) if line else {}
    except Exception:
        return {}


def e2e_mode() -> bool:
    """是不是在跑无人值守自测（`--e2e` / `--no-shortcuts`）。

    自测**绝不能**往用户真实的桌面/开始菜单/注册表写东西 —— 会留垃圾，
    而且清理起来很麻烦（真踩过：测试在真实开始菜单里留了 `Lyric2TTML.lnk`）。
    """
    return any(a in ("--e2e", "--no-shortcuts") for a in sys.argv)


def payload_zip() -> str:
    """安装包里**自带的主程序载荷**（一个 zip）。

    正常的软件安装程序就应该是**一个文件**：双击 → 解压安装 → 完事。
    以前做成"lite.zip + 安装程序.exe 两个文件、用户先解压再点安装程序"，
    用户原话："那不脱裤子放屁吗？" —— 所以主程序本体被塞进了这个 exe。
    """
    base = getattr(sys, "_MEIPASS", "")
    if not base:
        return ""
    p = os.path.join(base, "payload", "_app_payload.zip")
    return p if os.path.exists(p) else ""


# ----------------------------------------------------- 卸载（转发到 uninstall_core）
# ⚠️ 卸载逻辑**不能放在这个文件里**：本模块开头 `import tkinter`，而主程序的
# 构建特意排除了 tkinter（GUI 用不上）。`Lyric2TTML.exe --uninstall` 一旦
# 走到这里就会 `ModuleNotFoundError: No module named 'tkinter'`（E2E 抓到的）。
# 所以真正的实现在 `uninstall_core.py`（只依赖标准库），这里只做转发，
# 让 `installer_gui.uninstall(...)` 这个老入口继续可用。
from uninstall_core import (  # noqa: E402  （故意放这里：见上面的说明）
    default_data_dir as _default_data_dir,
    dir_size as _dir_size,
    human as _human,
    move_tree as _move_tree,
    uninstall as _uninstall_impl,
    uninstall_cli,
)


def default_data_dir() -> str:
    """桌面版放配置/模型/日志的地方（转发，保持旧入口可用）。"""
    return _default_data_dir()


def uninstall(install_dir: str, keep_models: bool = True,
              assume_yes: bool = False, desk: str = "") -> int:
    """卸载（**默认保留模型**）。真正实现在 `uninstall_core.uninstall`。"""
    return _uninstall_impl(install_dir, keep_models=keep_models,
                           assume_yes=assume_yes, desk=desk)



def default_install_dir() -> str:
    """默认装到哪。

    ⚠️ 不装进 `Program Files`：那里需要管理员权限，而模型/权重都要写在程序旁边，
    没权限会直接失败。装到用户目录最省事，也符合"解压即用"的定位。
    """
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "Lyric2TTML")


def app_here() -> str:
    """安装程序旁边有没有主程序（"绿色版"用法：已经解压好了）"""
    for c in (os.path.join(BASE, "Lyric2TTML.exe"),
              os.path.join(os.path.dirname(BASE), "Lyric2TTML.exe")):
        if os.path.exists(c):
            return os.path.dirname(c)
    return ""


def ensure_dir(path: str) -> bool:
    """建目录，尽量成功。

    ⚠️⚠️ **Python 的 `os.makedirs` 在盘根目录下会 `WinError 5 拒绝访问`，
    而 PowerShell 的 `New-Item` 却可以** —— 实测（同一台机器、同一个用户）：

        PS> New-Item -ItemType Directory -Path E:\\_probe      → ✓ 成功
        py> os.makedirs("E:/_probe")                           → ✗ WinError 5

    用户"浏览…"选 `E:\\` 时我们拼出 `E:\\Lyric2TTML`，正好踩中这一条：
    安装程序直接抛 PermissionError，界面还只能甩一坨 traceback。
    所以这里先用 Python 试，失败就**回退到 PowerShell / cmd**。
    """
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        pass
    for cmd in (["powershell", "-NoProfile", "-Command",
                 f"New-Item -ItemType Directory -Force -Path '{path}' "
                 f"-ErrorAction SilentlyContinue | Out-Null"],
                ["cmd", "/c", "mkdir", path]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except Exception:
            pass
        if os.path.isdir(path):
            return True
    return False


def shot_mode() -> bool:
    """`--shot <png>`：只画界面、**不干活**，截图后自动退出。

    为什么需要它：安装器是本项目唯一"改界面必须看真图"又**最难看**的地方 ——
    正常双击会先 `relaunch_elevated()` 弹 UAC，自动化测不了；
    `--e2e` 又是无界面模式。所以专门开一个：开真实窗口、画完整界面、
    **不起任何后台作业**（不下载、不解压、不写注册表），存图即退。

    ⚠️⚠️ **这是"抢屏幕"的操作，只能由人主动调用，绝不能进自动化测试。**
    实测副作用（真发生过，得如实记下来）：`-topmost` + `focus_force()` 会把
    前台焦点从用户正在用的窗口抢走 —— 有一次截图恰好把**用户的开始菜单**
    顶了出来（抓到的图里是开始菜单和桌面图标）。在开发者自己机器上跑一次
    无所谓，但**别放进 CI、别在别人用电脑时跑**。
    自动化要验安装器请用 `--e2e`（无界面）或 `tools/test_installer_theme.py`
    （只读源码比对颜色），那两个都不会抢焦点。
    """
    return "--shot" in sys.argv


def view_mode() -> bool:
    """`--view`：只开界面看一眼（不截图、不干活、不提权），手动关掉即退。"""
    return "--view" in sys.argv


def _shot_path() -> str:
    i = sys.argv.index("--shot")
    return sys.argv[i + 1] if len(sys.argv) > i + 1 else os.path.join(
        BASE, "installer_shot.png")


def _stage_arg() -> str:
    """`--stage install|done|choose`：预览/截图时直接跳到某个向导阶段。

    ⚠️ 只在 `--shot` / `--view` 下有意义 —— 真安装流程的阶段由流程本身推进，
    不会被这个参数影响（`_preview_stage` 只在 `not autostart` 时调用）。
    """
    if "--stage" not in sys.argv:
        return ""
    i = sys.argv.index("--stage")
    val = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
    return val if val in ("choose", "install", "done") else ""


class Installer:
    def __init__(self, root: tk.Tk, autostart: bool = True):
        """`autostart=False`：**只画界面**，不自动开始检查/解压（截图与预览用）。"""
        self.root = root
        self.autostart = autostart
        self.q: queue.Queue = queue.Queue()
        self.items: list = []          # [(键, 名称, 补全函数)]
        self.busy = False
        self._cancelled = False        # 用户在安装过程中点了「退出」
        self.model = "large-v3"        # 补全时要下的 Whisper 模型（跟 exe 默认一致）
        # 安装在哪儿：旁边已有主程序就用那个目录（绿色版），
        # 否则用"安装包自带载荷解压"的方式装到用户目录。
        self._beside = app_here()
        self.install_dir = self._beside or default_install_dir()
        self._app_ready = bool(self._beside)

        root.title("Lyric2TTML 安装程序")
        root.configure(bg=BG)
        root.geometry("860x700")
        root.minsize(760, 620)
        try:
            root.iconbitmap(default="")
        except Exception:
            pass

        self._build()
        root.after(120, self._pump)
        _style_progressbar(root)
        # 起始阶段：路径 + 勾选项（第 1 步）。
        # ⚠️ 必须在 `_build()` 之后调 —— 它要 pack/forget 控件。
        self.set_stage("choose")
        # ⚠️ `--stage install|done`：**只为截图/预览**设一个阶段，并填上假数据。
        # 为什么需要（第 23 轮补）：向导做了三个步骤，
        # 但之前只截得到第 1 步 —— 第 2/3 步「做过但从来没人看过」。
        # 界面代码"存在且测试通过"不等于"画出来是对的"（本项目已多次靠看图
        # 才发现"字看不见""区块没收起来"）。所以给预览模式一个能直达任意阶段的口子。
        _stage = _stage_arg()
        if _stage and not autostart:
            self._preview_stage(_stage)
            return
        if not autostart:
            # 预览/截图：停在"准备就绪"这一屏，不碰任何作业
            self.set_sub("准备就绪，点「开始安装」", ACCENT)
            self.btn_go.configure(text="开始安装",
                                  command=self.start_install_app,
                                  state="normal")
            self.log_line("安装位置：" + self.install_dir)
            return
        if self._app_ready:
            # 绿色版：旁边就有主程序，直接开始检查
            self.btn_go.configure(command=self.start_check)
            self.root.after(300, self.start_check)
        else:
            # 真安装包：先解压主程序，再检查
            self.set_sub("准备就绪，点「开始安装」", ACCENT)
            self.btn_go.configure(text="开始安装", command=self.start_install_app,
                                  state="normal")
            self.log_line("安装位置：" + self.install_dir)
            self.log_line("点「开始安装」，程序会自动解压到上面这个文件夹，")
            # ⚠️ 文案要跟**实际行为**一致（2026-10-14 改）：安装阶段只保证
            # "装完能打开"，不再在这里要求下语音模型 / GPU 运行库。
            self.log_line("然后只检查「能不能打开」；语音模型和 GPU 运行库")
            self.log_line("到软件里的「模型」页下载就行，不用在这儿等。")

    # ------------------------------------------------------------ 预览
    def _preview_stage(self, stage: str) -> None:
        """把界面切到指定阶段并填**假数据**（只在 `--shot`/`--view` 预览时用）。

        假数据刻意做得像真的：进度条 62% 且带字节数、日志有几行、
        清单里既有已装项也有缺失项 —— 这样截图才能看出
        "进度条会不会太窄""日志区会不会被挤没""按钮在不在视野里"。
        """
        from desktop.pages.base import (DARK, LIGHT)  # noqa: F401  (仅为对称)
        self.set_stage(stage)
        # ⚠️⚠️ **预览里必须直接改控件，不能用 `self.set_sub(...)`**（真踩到）：
        # `set_sub()` 只是往队列里塞 `("sub", …)`，要等 `_pump()`（主循环定时器）
        # 取出来才真的生效。预览模式下没人跑泵 ⇒ **副标题一直停在旧文字**。
        # 表现很隐蔽：截图上看不出（图里是"准备就绪"），
        # 但断言副标题会失败；而且"手动跑 `--stage done` 能过、测试里过不了" ——
        # 因为手动跑时主循环转起来了。这里直接 `configure`。
        def _sub(text: str, color: str = ACCENT) -> None:
            self.sub.configure(text=text, fg=color)

        if stage == "install":
            _sub("正在安装…（首次可能要一两分钟，请勿关闭）")
            self.log_line("安装位置：" + self.install_dir)
            self.log_line("[1/4] 解压主程序…")
            self.log_line("      已解压 387 个文件（1078 MB）")
            # ⚠️ 预览样例要跟新口径一致：安装阶段只检查"能不能打开"，
            # 语音模型 / GPU 运行库显示为**待下载**（不是"缺失"）。
            self.log_line("[2/4] 检查能不能打开…")
            self.log_line("      ✓ 核心依赖齐全   ✓ 目录可写")
            self.log_line("      ↓ 语音识别模型：待下载（打开后到「模型」页）")
            self.log_line("      ↓ GPU 运行时：待下载（打开后到「模型」页）")
            # ⚠️ 预览的进度样例值也要**跟真实流程同口径**：
            # 安装阶段真实做的是"解压 1078 MB 的载荷"，不是"下载 529 MB 运行时"。
            # 原来这里放的是 `62% 328/529MB` + "正在下载 GPU 运行时…" ——
            # 那是第 19 轮的旧口径（安装时下载运行时），会让截图与行为不符。
            self.pb.configure(value=100)
            self._pb_mode = "bytes"
            self._pb_total = 1130164224          # 载荷解压后的字节数
            self._pb_done = 1130164224
            self.lb_pct.configure(text="100%  1078/1078MB")
            self.hint.configure(text="已解压完成")
            self.btn_go.configure(text="▶ 启动软件", state="normal",
                                  command=self.launch_app)
            self.btn_quit.configure(state="normal")
        elif stage == "done":
            _sub("安装完成，可以打开了", OK)
            self.log_line("")
            self.log_line("安装位置：" + self.install_dir)
            self.log_line("已创建开始菜单快捷方式")
            self.log_line("")
            self.log_line("✅ 影响打开的一项都不缺。")
            self.log_line("ℹ️ 语音识别模型、GPU 运行时是「处理歌词时」才需要的，")
            self.log_line("   打开软件后到「模型」页下载（可续传、可取消）。")
            self.log_line("")
            self.log_line("点上面的「▶ 启动软件」就能打开了。")
            self.pb.configure(value=100)
            self.lb_pct.configure(text="100%  完成")
            self.hint.configure(text="")
            self.btn_go.configure(text="▶ 启动软件（完成）", state="normal",
                                  command=self.launch_app)
            self.btn_quit.configure(state="normal")
        else:
            _sub("准备就绪，点「开始安装」", ACCENT)
            self.btn_go.configure(text="开始安装",
                                  command=self.start_install_app,
                                  state="normal")

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=28, pady=(22, 6))
        tk.Label(head, text="Lyric2TTML", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        tk.Label(head, text="普通歌词 + 歌曲音频 → Apple Music / AMLL 逐字时间轴",
                 bg=BG, fg=DIM, font=("Microsoft YaHei UI", 11)).pack(anchor="w")

        self.sub = tk.Label(self.root, text="正在准备…", bg=BG, fg=ACCENT,
                            font=("Microsoft YaHei UI", 11, "bold"))
        self.sub.pack(anchor="w", padx=28, pady=(14, 4))

        # ---------------------------------------------------------------
        # 步骤条：让用户知道"现在在哪一步、还要几步"（任务书 E：现代化向导）
        # ---------------------------------------------------------------
        # ⚠️ 之前是一个**单屏表单**：路径、勾选项、清单、日志、按钮全堆在一起，
        # 用户看不出"现在该做什么、后面还有几步"。加一条步骤条是最小改动，
        # 又确实解决"不知道进行到哪"的问题。
        self.steps = {}
        stepbar = tk.Frame(self.root, bg=BG)
        stepbar.pack(anchor="w", padx=28, pady=(6, 2))
        for _k, _label in (("choose", "1 选择位置"), ("install", "2 安装"),
                           ("done", "3 完成")):
            _lb = tk.Label(stepbar, text=_label, bg=BG, fg=FAINT,
                           font=("Microsoft YaHei UI", 10, "bold"))
            _lb.pack(side="left", padx=(0, 18))
            self.steps[_k] = _lb

        # 安装位置：**必须让用户能改**。默认装到用户目录（不需要管理员权限，
        # 也不会有 Program Files 的写权限问题），但想装哪儿应该由用户说了算。
        self.pathbar = tk.Frame(self.root, bg=BG)
        self.pathbar.pack(fill="x", padx=28, pady=(2, 6))
        tk.Label(self.pathbar, text="安装到：", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        self.path_var = tk.StringVar(value=self.install_dir)
        self.path_entry = tk.Entry(
            self.pathbar, textvariable=self.path_var, bg=PANEL2, fg=FG,
            insertbackground=FG, relief="flat", font=("Microsoft YaHei UI", 10))
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(2, 8),
                             ipady=5)
        self.btn_browse = tk.Button(
            self.pathbar, text="浏览…", command=self.choose_dir, relief="flat",
            bg=PANEL2, fg=FG, activebackground=LINE,
            activeforeground=FG, font=("Microsoft YaHei UI", 9),
            padx=12, pady=4, cursor="hand2", bd=0)
        self.btn_browse.pack(side="left")

        # 清单：**必须能滚动**。
        # ⚠️ 检查项有 20+ 行，直接 pack 进窗口会把下面的日志和按钮挤出可视区
        # （真踩过：用户截图里清单只显示到一半，按钮根本看不见）。
        # 所以套一层 Canvas + Scrollbar，并给它一个固定高度上限；
        # 日志区用 expand=True 吃掉剩余空间，窗口拉大时也自适应。
        self.wrap = tk.Frame(self.root, bg=BG)
        self.wrap.pack(fill="x", padx=28, pady=(4, 10))
        self.canvas = tk.Canvas(self.wrap, bg=PANEL, highlightthickness=1,
                                highlightbackground=PANEL2, height=250, bd=0)
        self.canvas.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(self.wrap, orient="vertical", command=self.canvas.yview)
        sb.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=sb.set)

        self.rows_box = tk.Frame(self.canvas, bg=PANEL)
        self._rows_win = self.canvas.create_window((0, 0), window=self.rows_box,
                                                   anchor="nw")
        # 内容变高 → 更新可滚动范围；Canvas 变宽 → 内层跟着变宽
        self.rows_box.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._rows_win, width=e.width))
        # 滚轮：只在指针位于清单上时生效，别抢走日志框的滚动
        # 滚轮：清单里的行是 canvas 的子控件，事件不会冒泡到 canvas，
        # 所以直接在整个窗口上接，再判断指针是不是落在清单区域。
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.rows: dict = {}

        # ---------------------------------------------------------------
        # 安装选项：桌面快捷方式**必须由用户勾**（任务书 E："快捷方式选择"）
        # ---------------------------------------------------------------
        # ⚠️ 以前 `win_integration.integrate()` **已经支持** desktop=True/False，
        # 但界面上**没有任何地方设置 `want_desktop`** ⇒ 用户永远拿不到桌面图标，
        # 而代码里那句 `getattr(self, "want_desktop", False)` 看着像"已实现"。
        # 这种"后端能、界面没接"的缺口属性断言查不出来，只有看界面才发现。
        self._opts = tk.Frame(self.root, bg=BG)
        self._opts.pack(fill="x", padx=28, pady=(2, 6))
        self.var_desktop = tk.BooleanVar(value=False)
        self.var_sm = tk.BooleanVar(value=True)
        for var, text in ((self.var_sm, "在「开始菜单」里创建快捷方式（推荐）"),
                          (self.var_desktop, "同时创建桌面快捷方式")):
            tk.Checkbutton(
                self._opts, text=text, variable=var, bg=BG, fg=DIM,
                activebackground=BG, activeforeground=FG, selectcolor=PANEL2,
                font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=0,
                anchor="w").pack(side="left", padx=(0, 18))
        # 桌面默认不勾：任务书要求"避免未经同意创建桌面图标"。
        self.want_desktop = False
        self.var_desktop.trace_add("write", self._on_desktop_toggle)

        # 日志
        self.log = tk.Text(self.root, height=6, bg=PANEL2, fg=DIM,
                           insertbackground=FG, relief="flat", wrap="word",
                           font=("Consolas", 9), padx=10, pady=8)
        self.log.pack(fill="both", expand=True, padx=28)
        self.log.configure(state="disabled")

        # ---------------------------------------------------------------
        # 进度：**真实进度，不伪造百分比**（任务书 E 明确要求）
        # ---------------------------------------------------------------
        # 口径：主程序解压（按字节，PyInstaller 的 onefile 有真实回调）+
        #      环境检查/补全（按"清单条目完成数"计数）。
        # 两者都不是"转圈动画"，每一项都能对上日志里的一行。
        self._pbar = tk.Frame(self.root, bg=BG)
        self._pbar.pack(fill="x", padx=28, pady=(10, 0))
        self.pb = ttk.Progressbar(self._pbar, mode="determinate", maximum=100,
                                  style="L2T.Horizontal.TProgressbar")
        self.pb.pack(fill="x", side="left", expand=True)
        self.lb_pct = tk.Label(self._pbar, text="", bg=BG, fg=DIM, width=12,
                               anchor="e", font=("Microsoft YaHei UI", 9))
        self.lb_pct.pack(side="right", padx=(10, 0))
        self._pb_total = 0        # 本轮共多少项（0 = 未知，不显示百分比）
        self._pb_done = 0
        self._pb_mode = ""

        # 按钮区
        self._bar = tk.Frame(self.root, bg=BG)
        self._bar.pack(fill="x", padx=28, pady=16)
        self.hint = tk.Label(self._bar, text="", bg=BG, fg=DIM,
                             font=("Microsoft YaHei UI", 9))
        self.hint.pack(side="left")
        self.btn_quit = self._btn(self._bar, "退出", self._quit, side="right")
        self.btn_go = self._btn(self._bar, "开始补全", self.start_install,
                                side="right", primary=True)
        self.btn_go.configure(state="disabled")

    def _on_desktop_toggle(self, *_a) -> None:
        """勾选桌面快捷方式时，把选择同步给 `win_integration`。"""
        self.want_desktop = bool(self.var_desktop.get())

    # ------------------------------------------------------------ 真实进度
    def pb_set(self, done: int, total: int, label: str = "") -> None:
        """按"完成项 / 总项"更新进度条（**不做假动画**）。

        `total <= 0` 表示"还不知道总量" —— 这时**只显示文字、不显示百分比**，
        绝不猜一个数字出来（任务书要求真实进度）。
        """
        self._pb_done, self._pb_total = done, total
        if total > 0:
            pct = max(0, min(100, int(done * 100 / total)))
            self.pb.configure(mode="determinate", value=pct)
            self.lb_pct.configure(
                text=f"{label} {done}/{total}" if label else f"{done}/{total}  {pct}%")
        else:
            self.pb.configure(mode="indeterminate", value=0)
            self.lb_pct.configure(text=label)
        try:
            self.root.update_idletasks()
        except Exception:                                   # noqa: BLE001
            pass

    def pb_bytes(self, done: int, total: int) -> None:
        """解压阶段：按**字节**报进度（onefile 解包能拿到真实字节数）。"""
        if total <= 0:
            self.pb_set(0, 0, "正在解压…")
            return
        pct = max(0, min(100, int(done * 100 / total)))
        self.pb.configure(mode="determinate", value=pct)
        self.lb_pct.configure(
            text=f"解压 {done / 1048576:.0f}/{total / 1048576:.0f} MB")
        try:
            self.root.update_idletasks()
        except Exception:                                   # noqa: BLE001
            pass

    def pb_reset(self) -> None:
        self._pb_done = self._pb_total = 0
        self.pb.configure(mode="determinate", value=0)
        self.lb_pct.configure(text="")

    def _quit(self) -> None:
        """「退出」按钮。

        ⚠️ 改前是直接 `self.root.destroy()` —— 安装正在解压 650MB 时点退出，
        进程会**在写文件的中途消失**，留下半个目录，用户下次启动就是"程序坏了"。
        现在：忙的时候只**置取消标志**并给出反馈，让解压循环走到检查点后自己停，
        停在哪儿、留下了什么、怎么补救，都写在日志里（见 `_extract`）。
        """
        if self.busy:
            self._cancelled = True
            self.btn_quit.configure(state="disabled", text="正在停止…")
            self.say("")
            self.say("[取消] 收到退出请求，正在安全停止（不会写坏已装好的文件）…")
            self.set_sub("正在停止…", WARN)
            return
        self.root.destroy()

    def _on_wheel(self, event) -> None:
        """鼠标滚轮滚动清单。

        ⚠️ 行是 canvas 的子控件，滚轮事件不会冒泡到 canvas，所以在窗口上统一接；
        但要**判断指针是否落在清单区域**，否则会抢走日志框的滚动。
        """
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
            p = w
            for _ in range(6):
                if p is None:
                    break
                if p in (self.canvas, self.rows_box):
                    self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                    return
                p = getattr(p, "master", None)
        except Exception:
            pass

    def _scroll_top(self) -> None:
        """每次重新检查时把清单滚回顶部，否则会停在上一轮的滚动位置"""
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass

    def _btn(self, parent, text, cmd, side="right", primary=False):
        b = tk.Button(parent, text=text, command=cmd, relief="flat",
                      bg=ACCENT if primary else PANEL2,
                      fg=PANEL if primary else FG,
                      activebackground=CYAN if primary else LINE,
                      activeforeground=PANEL if primary else FG,
                      font=("Microsoft YaHei UI", 10, "bold" if primary else "normal"),
                      padx=20, pady=8, cursor="hand2", bd=0)
        b.pack(side=side, padx=(10, 0))
        return b

    # ------------------------------------------------------------ 工具
    def say(self, msg: str) -> None:
        self.q.put(("log", msg))

    def set_row(self, key: str, state: str, detail: str = "") -> None:
        self.q.put(("row", key, state, detail))

    def set_sub(self, text: str, color: str = ACCENT) -> None:
        self.q.put(("sub", text, color))

    def set_stage(self, stage: str) -> None:
        """切换向导阶段：`choose` / `install` / `done`。

        做两件事：
          1. **高亮步骤条**上对应的那一步（已完成的用 OK 色，当前用强调色）；
          2. **按阶段显示/隐藏区块** —— 这样用户一眼能看出"现在该做什么"，
             而不是面对一屏同时出现的路径框、清单、日志、按钮发懵。

        阶段与可见性（都是本类自己的控件，不涉及别处）：
          * `choose`  ：只看路径 + 勾选项（清单/日志先收起来）
          * `install` ：看清单 + 进度 + 日志
          * `done`    ：看结果（清单/日志保留，失败时要能翻）
        """
        self._stage = stage
        order = ["choose", "install", "done"]
        try:
            idx = order.index(stage)
        except ValueError:
            idx = 0
        for i, k in enumerate(order):
            lb = self.steps.get(k)
            if lb is None:
                continue
            if i < idx:
                lb.configure(fg=OK)                  # 已经走过的
            elif i == idx:
                lb.configure(fg=ACCENT)              # 当前
            else:
                lb.configure(fg=FAINT)               # 还没到
        # 按阶段收起/展开区块
        show_opts = (stage == "choose")
        try:
            if show_opts:
                # 第 1 步只留"路径 + 勾选项"，其余全收起来 ——
                # 否则中间会留一大片空白（截图里能看到），看着像没画完。
                self._pbar.pack_forget()
                self.pathbar.pack(fill="x", padx=28, pady=(10, 6))
                self._opts.pack(fill="x", padx=28, pady=(2, 6))
                self.wrap.pack_forget()
                self.log.pack_forget()
            else:
                self.pathbar.pack_forget()
                self._opts.pack_forget()
                # 进度条重新出现（pack 到按钮条之前，也就是它原来在的位置）
                self._pbar.pack(fill="x", padx=28, pady=(10, 0),
                                before=self._bar)
                self.wrap.pack(fill="both", expand=True, padx=28, pady=(4, 0))
                self.log.pack(fill="both", expand=True, padx=28)
        except Exception as _e:                          # noqa: BLE001
            # 阶段切换只是视觉优化，**绝不能因为它弄崩安装流程**。
            # ⚠️ 但也不能**静默**吞掉：第一版就是 `pass`，结果"区块没收起来"
            # 完全看不出来（截图里清单和日志还在），查了半天才发现是这里抛了异常。
            # 留一行 stderr，出问题时一眼能看到。
            import sys as _sys
            print(f"[wizard] 切换阶段 {stage} 时出错（不影响安装）："
                  f"{type(_e).__name__}: {_e}", file=_sys.stderr)

    def _mark_done(self, ok: bool, msg: str) -> None:
        """完成页：一句话结果 + 主按钮变成「启动软件」。"""
        self.set_stage("done")
        if ok:
            self.set_sub("安装完成，可以使用了", OK)
            self.say("")
            self.say("=" * 46)
            self.say("  ✅ 安装完成！点下面的「▶ 启动软件」开始使用。")
        else:
            self.set_sub("安装没完成，可以重试", BAD)

    def log_line(self, msg: str) -> None:
        """直接往日志框写一行（主线程用；后台线程请用 self.say）"""
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _pump_now(self) -> None:
        """同步把队列里积压的消息处理一轮（给自动化测试用）。

        `_pump` 是靠 `after(120ms)` 自循环的，测试里没有 mainloop 就永远不跑；
        这个入口让测试能直接推进一次，不必起窗口。
        """
        self._drain()

    def _pump(self) -> None:
        """主线程里消费后台线程发来的消息（tkinter 只能主线程碰控件）"""
        self._drain()
        self.root.after(120, self._pump)

    def _drain(self) -> None:
        try:
            while True:
                m = self.q.get_nowait()
                kind = m[0]
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", m[1] + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "addrow":
                    # 行是检查完才建的 —— 检查项有 20+ 个，固定几行装不下，
                    # 而且"要检查什么"应该由 envcheck 说了算，不该在界面里硬编码。
                    _, key, name, why, level = m
                    r = tk.Frame(self.rows_box, bg=PANEL)
                    r.pack(fill="x", padx=14, pady=4)
                    icon = tk.Label(r, text="•", bg=PANEL, fg=DIM, width=3,
                                    font=("Segoe UI Symbol", 13))
                    icon.pack(side="left")
                    tag = {"crit": "必需", "opt": "可选", "info": ""}[level]
                    tk.Label(r, text=name, bg=PANEL, fg=FG, width=22,
                             anchor="w", font=("Microsoft YaHei UI", 10)
                             ).pack(side="left")
                    tk.Label(r, text=(f"[{tag}]" if tag else ""), bg=PANEL,
                             fg=WARN, width=6, anchor="w",
                             font=("Microsoft YaHei UI", 8)).pack(side="left")
                    det = tk.Label(r, text=why, bg=PANEL, fg=DIM, anchor="w",
                                   font=("Microsoft YaHei UI", 9))
                    det.pack(side="left", fill="x", expand=True)
                    self.rows[key] = (icon, det)
                    # 显式刷新可滚动范围：只靠 <Configure> 有时会晚一拍，
                    # 表现是"滚轮/滚动条一开始滚不动"。
                    try:
                        self.canvas.configure(
                            scrollregion=self.canvas.bbox("all"))
                    except Exception:
                        pass
                elif kind == "row":
                    _, key, state, detail = m
                    if key not in self.rows:
                        continue
                    icon, det = self.rows[key]
                    # ⚠️ `todo` = **处理歌词时才要、安装阶段不用下**的那类
                    # （语音模型 / CUDA 运行库）。用"↓ 待下载"和强调色，
                    # 与"✕ 缺失"区分开 —— 否则用户会以为装坏了。
                    icon.configure(text={"ok": "✓", "bad": "✕", "skip": "–",
                                         "wait": "•", "work": "…",
                                         "todo": "↓"}.get(state, "•"),
                                   fg={"ok": OK, "bad": BAD, "skip": DIM,
                                       "wait": DIM, "work": WARN,
                                       "todo": ACCENT}.get(state, DIM))
                    if detail:
                        det.configure(
                            text=detail,
                            fg={"bad": BAD, "todo": ACCENT}.get(state, DIM))
                elif kind == "setbtn":
                    _, text, cmd, state = m
                    self.btn_go.configure(text=text, command=cmd, state=state)
                elif kind == "run":
                    m[1]()
                elif kind == "sub":
                    self.sub.configure(text=m[1], fg=m[2])
                elif kind == "progress":
                    # worker 线程报进度 → **主线程**更新控件（见 `_extract` 的说明）
                    self.pb_set(m[1], m[2], "解压")
                elif kind == "done":
                    self.busy = False
                    # 复位「退出」按钮（`_quit` 忙时把它改成了"正在停止…"）
                    self.btn_quit.configure(state="normal", text="退出")
                    self.btn_go.configure(state=m[1], text=m[2])
                    # 全部就绪时，主按钮变成「启动软件」——用户就不用再去找 启动.bat
                    if len(m) > 3 and m[3] == "launch":
                        # 走到「启动软件」= 向导的第 3 步（完成）。
                        # 在**这里**统一切阶段，而不是去改三处 `q.put(...)` ——
                        # 那三处分别在"全部就绪/核心可用/缺可选"分支里，
                        # 改漏一个就会出现"按钮变了但步骤条还停在第 2 步"。
                        self.set_stage("done")
                        self.btn_go.configure(command=self.launch_app,
                                              text="▶ 启动软件（完成）")
                    elif len(m) > 3 and m[3] == "install_app":
                        self.btn_go.configure(command=self.start_install_app)
                    elif len(m) > 3 and m[3] == "install":
                        self.btn_go.configure(command=self.start_install)
                    else:
                        self.btn_go.configure(command=self.start_install)
        except queue.Empty:
            pass
        # ⚠️ 这里**不要**再 after 一次：定时器由 _pump 统一续，
        # 两处都调会让每一轮多排一个定时器（指数增长，很快就卡死）。

    # ------------------------------------------------------------ 安装本体
    def _rebind(self, new_dir: str) -> None:
        """把"程序目录"切到解压出来的位置。

        `install` / `envcheck` 里的 BASE、CUDA_DIR、CAMPP_DIR 都是 import 时算好的，
        解压完必须同步改掉，否则补全会往安装程序自己的临时目录里写。
        """
        global BASE
        BASE = new_dir
        try:
            import install as _core
            _core.BASE = new_dir
            _core.CUDA_DIR = os.path.join(new_dir, "cuda")
            _core.CAMPP_DIR = os.path.join(new_dir, "models", "campplus")
        except Exception:
            pass
        os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(new_dir, ".hf_cache")
        self.install_dir = new_dir

    def extract_app(self) -> bool:
        """把 exe 里自带的主程序解压到 `install_dir`。"""
        z = payload_zip()
        if not z:
            self.say("[错误] 安装包里没有找到主程序载荷（可能是用源码方式跑的）。")
            return False
        dest = self.install_dir
        # ⚠️ **先探测能不能写，再解压。** 往**盘根目录**（`E:\`）建文件夹在 Windows 上
        # 需要管理员权限，普通用户会 `WinError 5 拒绝访问` —— 真踩过：用户在
        # 「浏览…」里选了 `E:\`，`os.makedirs` 抛异常，而且异常在**后台线程**里，
        # 界面只能把一坨 traceback 甩给用户。这里先试写，失败就给人话 + 推荐位置。
        try:
            # ⚠️ 目录**已经存在且可写**时，根本不用（也不该）调 makedirs：
            # `os.makedirs(exist_ok=True)` 在有些盘上即使目录存在也会报
            # `WinError 5`（盘根目录的 ACL 特殊）。先测可写性，最稳。
            if not os.path.isdir(dest):
                if not ensure_dir(dest):
                    raise PermissionError(f"无法创建目录 {dest}")
            probe = os.path.join(dest, ".l2t_write_test")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
        except Exception as e:
            self.say("")
            self.say(f"✗ 这个位置装不了：{dest}")
            self.say(f"  原因：{type(e).__name__}: {e}")
            self.say("")
            exists = os.path.isdir(dest)
            if exists:
                # ⚠️ 最阴的一种：文件夹**存在**（所以看着没问题），但当前用户写不进去。
                # 常见原因是这个文件夹当初是**以管理员权限**创建的（比如用管理员命令行
                # mkdir、或管理员权限的程序建的）—— 属主是 Administrators，
                # 普通用户只有读权限。真踩过（我自己用管理员权限建过一个，害用户连试好几次）。
                self.say("  这个文件夹已经存在，但当前用户写不进去。")
                self.say("  最常见的原因：它是**以管理员权限**创建或解压出来的，")
                self.say("  属主变成了 Administrators，普通用户只有读权限。")
                self.say("")
                self.say("  解决办法（任选）：")
                self.say("    ① 把它整删掉，然后在资源管理器里手动新建一个，再选它")
                self.say(f"       （要删的：{dest}）")
                self.say("    ② 直接换一个位置，比如下面这个必然能写：")
                self.say(f"       {default_install_dir()}")
                self.say("    ③ 确实想装到这个位置：**关掉本窗口**，在安装程序上右键 →")
                self.say("       「以管理员身份运行」，再选这个文件夹")
            else:
                self.say("  这个位置建不出文件夹。")
                self.say("")
                # ⚠️⚠️ 必须**明确告诉用户「以管理员身份运行」**这条路（用户 2026-10-13
                # 明确要求："不能只在界面上声称支持"）。最常见的场景就是用户在
                # 「浏览…」里选了 `C:\\Program Files\\...` —— 那是机器级目录，
                # 普通用户建不了。只给"换个位置"是不够的：用户想要的就是装到那。
                # 真踩过（`tools/test_perm_denied.py` 抓到的）：这里原来只说了
                # ①默认位置 ②手动建文件夹，**完全没提管理员** —— 想装 Program Files
                # 的用户看完会以为"这软件不支持装到那里"。
                self.say("  解决办法（任选）：")
                self.say(f"    ① 用默认位置（不需要管理员）：{default_install_dir()}")
                self.say("    ② 确实想装到这个位置：**关掉本窗口**，在安装程序上右键 →")
                self.say("       「以管理员身份运行」，再选这个文件夹")
                self.say("       （Program Files 这类机器级目录必须提权才能写）")
            self.set_sub("安装位置不可写，请换一个或改用管理员运行", BAD)
            return False
        locked: list = []
        self.say(f"[安装] 正在解压到 {dest}")
        try:
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
                # ⚠️ 载荷是"整个文件夹"打的包（zip_dir 相对父目录存），所以每条路径
                # 都带一层顶层目录名（`Lyric2TTML/...`）。**不剥掉这层就会解成
                # `安装目录/Lyric2TTML/Lyric2TTML.exe`** —— 用户看到的"安装完却找不到
                # 程序"就是这么来的。这里检测唯一的顶层目录并剥掉。
                tops = {n.split("/")[0] for n in names if "/" in n}
                prefix = (next(iter(tops)) + "/") if len(tops) == 1 else ""
                total = len(names)
                for i, n in enumerate(names, 1):
                    if prefix:
                        if not n.startswith(prefix):
                            continue
                        rel = n[len(prefix):]
                    else:
                        rel = n
                    if not rel:
                        continue
                    target = os.path.join(dest, rel.replace("/", os.sep))
                    if n.endswith("/"):
                        os.makedirs(target, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        # ⚠️ 覆盖安装最常见的两个坑，都必须处理，否则整个安装会中断：
                        #  ① 旧文件带**只读**属性 → open(.., "wb") 直接 PermissionError
                        #  ② 程序**还开着**，文件被占用 → 同样 PermissionError
                        # 真踩过：卡在 CHANGELOG.md 上，整个解压报错、装了 7 次都不成功。
                        if os.path.exists(target):
                            try:
                                import stat as _stat
                                os.chmod(target, _stat.S_IWRITE)
                            except Exception:
                                pass
                        # ⚠️⚠️ **必须先写临时文件，再原子替换**（真踩到的数据损坏 bug）：
                        # 原实现是 `open(target, "wb")` 直写 —— 而 `"wb"` 会**先把
                        # 目标截断成 0 字节**，然后才去拷内容。文件被占用时拷贝失败，
                        # 于是旧文件**已经被清空了**：用户升级一次，原本好好的文件
                        # 变成空文件（实测 `locked.txt` 内容从 '旧' 变成 ''）。
                        # 这是"升级把用户装坏"里最恶劣的一种 —— 静默丢数据。
                        # 现在：写 `<name>.l2t-new` → 成功才 `os.replace()` 上位；
                        # 失败就把临时文件删掉，**原文件一个字节都不动**。
                        # （`os.replace` 在 Windows 上是原子的。）
                        tmp = target + ".l2t-new"
                        last = None
                        for attempt in range(3):
                            try:
                                with zf.open(n) as src, open(tmp, "wb") as out:
                                    shutil.copyfileobj(src, out)
                                os.replace(tmp, target)
                                last = None
                                break
                            except (PermissionError, OSError) as e:
                                last = e
                                try:
                                    if os.path.exists(tmp):
                                        os.remove(tmp)
                                except OSError:
                                    pass
                                time.sleep(0.4)
                        if last is not None:
                            # 单个文件写不进去 → 记下来继续，**不要**让整次安装失败
                            locked.append(rel)
                    if total and (i % 200 == 0 or i == total):
                        self.set_sub(f"正在安装… {i * 100 // total}%")
                    # ⚠️ 取消支持：解压 650MB / 上万个文件要几十秒，
                    # 这中间用户点「退出」不该毫无反应（任务书 E 要求"取消处理"）。
                    # 每 64 个文件让出一次事件循环并检查取消标志 ——
                    # 既能刷新界面，又不会因为太频繁而拖慢解压。
                    if self._cancelled:
                        self.say("")
                        self.say(f"[取消] 已在第 {i}/{total} 个文件处停下。")
                        self.say("       已经解压的文件留在目标目录里，程序可能不完整；")
                        self.say("       想装完就再运行一次安装程序（会接着覆盖补齐）。")
                        return False
                    # ⚠️⚠️ **绝对不能在 worker 线程里调 `root.update()`** ——
                    # 它不是"只刷新画面"，而是会**派发输入事件**：主线程如果正在
                    # 一个回调里，重入会造成 `QObject`/Tk 那种"跨线程碰 UI"级别的
                    # 崩溃。`_extract` 跑在 `threading.Thread` 里（见
                    # `start_install_app`），所以刷新一律**走队列交给主线程**：
                    # `_pump`（主线程 `after` 循环）会消费这些消息。
                    if i % 64 == 0:
                        self.q.put(("progress", i, total))
            self.say(f"[完成] 已解压 {len(names)} 个文件")
            if locked:
                self.say("")
                self.say(f"⚠ 有 {len(locked)} 个文件被占用/只读，没能覆盖（其余都装好了）：")
                for x in locked[:6]:
                    self.say(f"    {x}")
                self.say("  多半是**程序还开着**。关掉程序（黑框窗口）和浏览器页面后，")
                self.say("  重新运行本安装程序即可补上；这些文件不影响核心功能。")
        except Exception as e:
            self.say(f"[错误] 解压失败：{type(e).__name__}: {e}")
            return False
        if not os.path.exists(os.path.join(dest, "Lyric2TTML.exe")):
            self.say("[错误] 解压完没看到 Lyric2TTML.exe，包可能损坏。")
            return False
        self._rebind(dest)
        self._app_ready = True
        # 装完顺手做两件用户会期待的事：桌面快捷方式 + 卸载程序
        self.set_sub("正在创建快捷方式…")
        # ⚠️ `--e2e` / `--no-shortcuts` 时**不建真实快捷方式**：
        # 端到端自测跑在开发机上，往**真实**桌面/开始菜单写东西会留垃圾，
        # 而且很难清理（真踩过：测试在真实开始菜单里留了 Lyric2TTML.lnk）。
        if e2e_mode():
            self.say("[跳过] 自测模式：不创建快捷方式与注册表项。")
        else:
            self.make_shortcut()
        self.write_uninstaller()
        return True

    def choose_dir(self) -> None:
        """让用户选安装目录 —— **就装在他选的那个文件夹里**。

        ⚠️ 以前是 `os.path.join(d, "Lyric2TTML")` 无条件再追一层，于是用户
        明明选中了 `E:\\Lyric2TTML`，路径变成 `E:\\Lyric2TTML\\Lyric2TTML`
        （而且这个子目录还建不出来）—— 真踩过，用户连点几次都是这个错。
        「选择安装位置」当然应该装在他选的位置上。
        **只有选到盘根时才补一层名字**（不能直接往盘根里铺一堆文件）。
        """
        from tkinter import filedialog
        d = filedialog.askdirectory(title="选择安装位置",
                                    initialdir=os.path.dirname(self.install_dir))
        if not d:
            return
        d = os.path.normpath(d)
        if len(os.path.splitdrive(d)[1].strip("\\/")) == 0:
            # 盘根：补一层，别把程序散在根目录
            d = os.path.join(d, "Lyric2TTML")
        self.install_dir = d
        self.path_var.set(d)

    # ------------------------------------------------- 快捷方式 / 卸载程序
    def make_shortcut(self, desktop: bool = False) -> None:
        """建立外壳集成：开始菜单（**总是**）＋ 桌面（**用户勾了才建**）＋ 卸载注册表项。

        ⚠️ 改造前的三个问题（都是"不像正式软件"的直接原因）：
        1. 快捷方式指向 **`启动.bat`** —— 点开先弹黑窗，用户看到的还是"批处理"。
           现在指向 **`Lyric2TTML.exe` 本体**（spec 里已改 `hide_console`，无黑窗）。
        2. **没有开始菜单项** —— 装完只能在桌面找，卸载后更找不到。
           现在按 Windows 规范建 `开始菜单\\程序\\Lyric2TTML.lnk`。
        3. **没写卸载注册表项** —— Windows 设置里看不到这个软件。
           现在写 HKCU（普通用户可写、**不需要管理员**）。

        ⚠️ 逻辑都在 `win_integration` 里，**卸载走同一个模块** ——
        分成两份写必然漂移，最后变成"设置里留一个删不掉的条目"。
        """
        try:
            import win_integration
        except ImportError:
            self.say("[提示] 找不到 win_integration，跳过外壳集成。")
            return
        exe = os.path.join(self.install_dir, "Lyric2TTML.exe")
        size_kb = 0
        try:
            total = 0
            for root, _d, files in os.walk(self.install_dir):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            size_kb = total // 1024
        except OSError:
            pass
        # ⚠️ 版本号**只有一处真源**：`win_integration.app_version()` →
        # `desktop/__init__.py` 的 `__version__`。
        # 这里原来写成"取不到就用 `"1.1.0"`" —— 那是**把版本号抄了一遍**，
        # 下次升版本必然漂移：注册表里显示旧版本，用户以为没升级成功。
        version = win_integration.app_version()
        res = win_integration.integrate(
            self.install_dir, version,
            desktop=desktop or getattr(self, "want_desktop", False),
            start_menu=True, size_kb=size_kb,
            log=lambda m: self.say(m))
        if res.get("errors"):
            for e in res["errors"]:
                self.say(f"  [提示] {e}")

    def write_uninstaller(self) -> None:
        """写一个能"一键卸载"的 `卸载程序.bat`。

        要删的东西：安装目录 + 桌面快捷方式。**默认保留已下载的模型**。

        ⚠️ 用 `%~dp0` 定位自己，绝不写死路径 —— 用户可能装到任何地方。
        ⚠️ 删自己所在目录时，bat 必须**先 cd 到别处**，否则 Windows 会因为
        "文件正在使用"删不掉。

        ------------------------------------------------------------------
        "保留模型"这条为什么要写得这么绕（血泪）
        ------------------------------------------------------------------
        模型（约 3GB）可能在两个地方：
          * **用户数据目录** `<LOCALAPPDATA>\\Lyric2TTML\\.hf_cache` —— 桌面版
            默认位置，删安装目录动不到它，什么都不用做；
          * **安装目录** `<安装目录>\\.hf_cache` —— 旧版 / 就地模式。

        麻烦的是后者：`rmdir /s /q 安装目录` 会**连同模型一起删掉**。
        所以必须先把它搬出去。而"搬"这一步**不能用会失败还报成功的写法**：

        * `xcopy "<中文长路径>" "<还不存在的目标子目录>\\\\"` 实测会打印
          `1 File(s) copied` 并返回 0，**但目标里什么都没有**；
        * `robocopy /MOVE` 在普通路径上是好的（实测 src 消失、dst 出现），
          但在带中文的路径上出现过"报告搬了 1 个文件、目标却是空的"。

        搬 3GB 的文件**不能赌**。所以这里的原则是：
        **先确认目标真的存在，才允许删源。** 确认不了就**不搬也不删**，
        直接告诉用户"模型还在安装目录里，手工移走再删目录"。
        宁可留一个不干净的卸载，也不能让用户白下 3GB。
        """
        bat = os.path.join(self.install_dir, "卸载程序.bat")
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
        # ⚠️⚠️ 这个 .bat **只负责把卸载逻辑叫起来**，真正的删除/搬移在
        # `uninstall()`（Python）里做。原因：`.bat` 里的 xcopy / robocopy
        # 在"含中文的路径 + 目标子目录还不存在"时会**报成功却没搬**，
        # 而它要搬的是 3GB 的模型 —— 不能赌（详见 `_move_tree` 的说明）。
        # Python 侧还能真正验证"源没了、目标有东西"，搬不动就什么都不删。
        #
        # 用哪个 Python 来跑？
        #   ① 安装目录里的主程序（自带运行时，永远可用）——
        #      它是 GUI 程序，但带 --uninstall 参数时走命令行分支；
        #   ② 万一主程序被删了，退回系统 python（开发机上就是这样）。
        content = (
            "@echo off\r\n"
            "title 卸载 Lyric2TTML\r\n"
            "setlocal\r\n"
            "set \"DIR=%~dp0\"\r\n"
            "if \"%DIR:~-1%\"==\"\\\" set \"DIR=%DIR:~0,-1%\"\r\n"
            "set \"PYTHONIOENCODING=utf-8\"\r\n"
            "echo.\r\n"
            "echo   将要卸载 Lyric2TTML\r\n"
            "echo   位置：%DIR%\r\n"
            "echo.\r\n"
            "echo   语音识别模型（约 3GB）默认保留，这样以后重装不用重下。\r\n"
            "echo.\r\n"
            "choice /c YN /n /m \"   确定卸载吗？[Y=是  N=取消] \"\r\n"
            "if errorlevel 2 goto cancelled\r\n"
            "echo.\r\n"
            "if exist \"%DIR%\\Lyric2TTML.exe\" goto useexe\r\n"
            "goto usepython\r\n"
            # ---- 用主程序（打包版，自带 Python）----
            ":useexe\r\n"
            "\"%DIR%\\Lyric2TTML.exe\" --uninstall \"%DIR%\" --keep-models\r\n"
            "goto after\r\n"
            # ---- 退回系统 python（开发/便携版）----
            ":usepython\r\n"
            "where python >nul 2>nul\r\n"
            "if errorlevel 1 goto nopython\r\n"
            "python -c \"import sys;sys.path.insert(0,r'%DIR%');import installer_gui as g;sys.exit(g.uninstall(r'%DIR%', keep_models=True, assume_yes=True))\"\r\n"
            "goto after\r\n"
            ":nopython\r\n"
            "echo   [错误] 找不到卸载所需的程序。\r\n"
            "echo          请手工删除这个目录：%DIR%\r\n"
            "echo          （模型在 %%LOCALAPPDATA%%\\Lyric2TTML，默认保留）\r\n"
            ":after\r\n"
            "echo.\r\n"
            "pause\r\n"
            "goto :eof\r\n"
            ":cancelled\r\n"
            "echo.\r\n"
            "echo   已取消。\r\n"
            "pause\r\n"
        )
        try:
            import io as _io
            with _io.open(bat, "w", encoding="gbk", newline="") as f:
                f.write(content)
            self.say(f"[完成] 卸载程序：{bat}")
            # 桌面也放一个卸载快捷方式，用户不用进目录找
            lnk = os.path.join(desk, "卸载 Lyric2TTML.lnk")
            ps = (
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
                "$s.TargetPath='%s';$s.WorkingDirectory='%s';"
                "$s.Description='卸载 Lyric2TTML';$s.Save()"
                % (lnk, bat, self.install_dir)
            )
            if os.path.isdir(desk):
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               timeout=60, capture_output=True)
        except Exception as e:
            self.say(f"[提示] 写卸载脚本失败：{type(e).__name__}: {e}")

    def quick_recheck(self, keys: list) -> list:
        """补全后的**轻量复查**：只看刚装的那几项文件在不在，不再跑一遍 `--doctor`。

        ⚠️⚠️ 为什么必须轻量：`Lyric2TTML.exe --doctor` 要
        **import torch（445MB）+ ctypes 加载 cublas/cudnn（2GB）**，
        在打包环境里跑一次要**好几分钟**。补全后如果又调一次完整 doctor，
        界面就会长时间"没反应"—— 用户实测**卡了 15~20 分钟**，
        手动杀掉重开才发现其实早就装好了（真踩过）。

        补全只是"下文件"，所以复查也只需要"看文件在不在"。
        """
        still = []
        for k in keys:
            if k == "whisper":
                ok = bool(core.whisper_models())
            elif k == "campp":
                ok = core.campp_present()
            elif k.startswith("cuda_"):
                ok = core.cuda_dlls_present()
            else:
                ok = True
            if not ok:
                still.append(k)
        return still

    def start_install_app(self) -> None:
        """「开始安装」：解压主程序 → 接着做环境检查。"""
        if self.busy:
            return
        self.busy = True
        self.set_stage("install")          # 步骤条：进入第 2 步
        self.btn_go.configure(state="disabled")

        def work():
            # ⚠️ 兜底：后台线程里抛出的异常不会有人接，只能在控制台留一坨 traceback，
            # 用户看到的就是"安装程序闪一下就没了"。所以这里一律转成人话。
            try:
                ok = self.extract_app()
            except Exception as e:
                import traceback
                self.say(f"\n[错误] 安装时出问题：{type(e).__name__}: {e}")
                self.say(traceback.format_exc()[-400:])
                ok = False
            self.busy = False
            if ok:
                self.q.put(("setbtn", "开始检查", self.start_check, "normal"))
                self.set_sub("安装完成，正在检查运行环境…")
                self.q.put(("run", self.start_check))
            else:
                self.q.put(("setbtn", "重试", self.start_install_app, "normal"))
                # ⚠️ 失败时也要把步骤条退回第 2 步（而不是停在"完成"），
                # 否则用户看到"3 完成"却是红的，会以为装好了但其实没有。
                self.q.put(("run", lambda: self.set_stage("install")))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ 检查
    def start_check(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.btn_go.configure(state="disabled")
        self._scroll_top()          # 重新检查时清单滚回顶部，别停在上一轮的位置
        self.set_sub("正在检查你的电脑…（首次可能要一两分钟，请勿关闭）")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        """跑**穷尽式**检查（envcheck），需要的行动态建、缺的项自动排好补全动作。

        ⚠️ 检查项有 20 多个（每个依赖包、每个 CUDA DLL、模型文件的完整性…），
        不能在界面里硬编码几行就算数 —— 那正是上一版"只检查一小部分"的原因。
        """
        try:
            self.items = []
            cache = os.environ.get("HUGGINGFACE_HUB_CACHE") \
                or os.path.join(BASE, ".hf_cache")
            print(f"[检查] 程序目录 {BASE}", flush=True)

            # ① 优先让**主程序**报自己的环境（见 ask_app_report 的说明）
            rep = ask_app_report(self.install_dir)
            if rep and rep.get("items"):
                import types
                rows = [types.SimpleNamespace(**d) for d in rep["items"]]
                print("[检查] 用主程序 Lyric2TTML.exe --doctor --json 的报告",
                      flush=True)
            elif FROZEN:
                # ⚠️⚠️ **打包版找不到主程序时必须停下，不能退回自检。**
                # 因为安装程序自己的临时 Python 环境里没有 numpy/ctranslate2
                # （打包时特意排除了），自检必然谎报"未安装" —— 用户看到一排
                # 假红叉，接着点补全又会往那个临时环境里装，装了也没用。
                # 真踩过。这里明确告诉用户怎么放，并禁用补全按钮。
                print("[检查] 没找到主程序，中止", flush=True)
                self.set_sub("还没安装主程序", BAD)
                self.say("⚠️ 这个位置还没有主程序，先点「开始安装」把它解压出来。")
                self.q.put(("done", "normal", "开始安装", "install_app"))
                return
            else:
                # ② 源码模式才在进程内自检
                rows = envcheck.check_all(BASE, cache, frozen=FROZEN)
                print("[检查] 源码模式：进程内自检", flush=True)

            # ⚠️⚠️ **安装程序只检查"能不能打开"这一层**（2026-10-14 用户要求）。
            #
            # 原来这里是 `envcheck.summarize(rows)` —— 按 `level == CRIT` 判"必需"。
            # 而 CRIT 里混着**两件完全不同的事**：
            #   * `pkg_faster_whisper` / 写出权限 / 磁盘空间 → 缺了**真跑不动**；
            #   * `whisper`（语音模型 **3 GB**）、`cuda_*.dll`（**527 MB**）
            #     → 缺了**软件照样打开**，只是点「开始制作」时做不了。
            # 后果：**软件还没打开，就先要求下 3.5 GB**。用户明确说不要这样：
            # 「安装的时候只检查会影响软件打开的，处理歌词要用的东西到软件里的
            #   模型页去下」。
            #
            # 现在用 `envcheck.launch_blockers()`（按 scope 分层）：
            #   * launch 缺 → 拦住并给补全按钮（这些是打包/权限问题，安装阶段修得动）
            #   * convert/extra 缺 → **不拦**，到软件里下（模型页 / 运行组件卡）
            lb = envcheck.launch_blockers(rows)
            cb = envcheck.convert_blockers(rows)
            # ⚠️ 主程序报回来的项里**已经带了 `scope`**（`--launch-check`），
            # 优先用它；退回 `--doctor` 时没有 scope，就本地按 key 映射算。
            scope = {}
            for it in rows:
                sc = getattr(it, "scope", None)
                scope[it.key] = sc or envcheck.scope_of(it)

            for it in rows:
                self.q.put(("addrow", it.key, it.name, it.why, it.level))

            # ⚠️ 补全动作**只对 launch 缺项建**。原来把所有 CRIT 缺项
            # 都塞进 `self.items`，于是「开始补全」会去下 3 GB 模型。
            actions = {
                "campp": core.fetch_campp,
            }
            # 任何一个 CUDA DLL 缺 → 一次把三个都装齐（同一个 wheel 里）
            cuda_missing = [it for it in lb
                            if it.key.startswith("cuda_") and not it.ok]
            if cuda_missing:
                actions["cuda_gether"] = core.fetch_cuda
            # 缺的包合并成一次 pip（**只看 launch 层**）
            pkg_missing = [it for it in lb if it.key.startswith("pkg_") and not it.ok]

            for it in rows:
                sc = scope.get(it.key, envcheck.CONVERT)
                if it.ok:
                    self.set_row(it.key, "ok", it.detail)
                    self.say(f"[OK] {it.name}"
                             + (f"：{it.detail}" if it.detail else ""))
                elif sc == envcheck.LAUNCH:
                    self.set_row(it.key, "bad",
                                 it.detail + ("" if not it.fix else
                                              ("　" + it.fix.splitlines()[0])))
                    self.say(f"[缺·影响打开] {it.name}"
                             + (f"：{it.detail}" if it.detail else ""))
                    self.items.append(
                        (it.key, it.name,
                         actions.get(it.key, getattr(it, "action", None))))
                else:
                    # ⚠️ 这两类**不在安装阶段补**。列表里如实显示"待下载"，
                    # 并明确写清"打开软件后到哪下" —— 不能让用户以为装坏了。
                    where = ("「模型」页" if sc == envcheck.CONVERT else "可选功能")
                    self.set_row(it.key, "todo", f"待下载（{where}）")
                    self.say(f"[待下载] {it.name}：{it.detail} "
                             f"→ 打开软件后到{where}下载（安装阶段不需要）")

            if pkg_missing:
                names = [it.name for it in pkg_missing]
                self.say(f"\n→ 一次性安装缺的依赖：{'、'.join(names)}")
                self.items.insert(0, ("pkg_all", "Python 依赖库",
                                      lambda: core.install_pkgs(names)))
            if "cuda_gether" in actions:
                # 三个 CUDA DLL 合成一项，避免重复下载同一个 wheel
                for k in ("cuda_cublas64_12.dll", "cuda_cublasLt64_12.dll",
                          "cuda_cudnn64_9.dll"):
                    self.items = [t for t in self.items if t[0] != k]
                self.items.append(("cuda_gether", "CUDA 运行库（3 个 DLL）",
                                   actions["cuda_gether"]))

            if lb:
                self.set_sub(f"发现 {len(lb)} 项影响打开的问题，可以自动补全", BAD)
                self.say(f"\n检查了 {len(rows)} 项，其中 {len(lb)} 项**会影响打开**。"
                         f"点「开始补全」自动修。")
                self.q.put(("done", "normal", f"开始补全（{len(lb)} 项）"))
            else:
                # ✅ **装完就能打开** —— 这是安装程序的目标。
                self.set_sub("安装完成，可以打开了", OK)
                self.say(f"\n✅ 检查了 {len(rows)} 项：**影响打开的一项都不缺。**")
                if cb:
                    names = "、".join(it.name for it in cb)
                    self.say(f"\nℹ️ 另有 {len(cb)} 项是**处理歌词时**才需要的"
                             f"（{names}）——")
                    self.say("   安装阶段**不用下**，打开软件后到「模型」页"
                             "（含「运行组件」）点一下就会下，可续传可取消。")
                self.say("\n点下面的「▶ 启动软件」就能打开了。")
                self.q.put(("done", "normal", "▶ 启动软件", "launch"))
        except Exception as e:
            import traceback
            self.say(f"[错误] 检查时出问题：{type(e).__name__}: {e}")
            self.say(traceback.format_exc()[-600:])
            self.set_sub("检查失败", BAD)
            self.q.put(("done", "normal", "重试"))

    # ------------------------------------------------------------ 启动
    def launch_app(self) -> None:
        """启动主程序。

        找不到主程序就明确说 —— 安装程序是"把环境补齐"，
        程序本体（Lyric2TTML.exe / 启动.bat）得在同一个文件夹里。
        """
        base = BASE
        exe = os.path.join(base, "Lyric2TTML.exe")
        bat = os.path.join(base, "启动.bat")
        srv = os.path.join(base, "web_server.py")
        try:
            if os.path.exists(exe):
                subprocess.Popen([exe], cwd=base, close_fds=True)
                self.say("[启动] Lyric2TTML.exe")
            elif os.path.exists(bat):
                subprocess.Popen(["cmd", "/c", bat], cwd=base, close_fds=True)
                self.say("[启动] 启动.bat")
            elif os.path.exists(srv):
                # ⚠️ 冻结时 sys.executable 是**这个安装程序自己**，用它启动
                # web_server.py 等于重开安装程序（点一次弹一次）。源码模式才安全。
                if FROZEN:
                    self.say("[提示] 只找到 web_server.py，但打包版不能这样启动。")
                    self.say("        请解压完整的 lite 包后运行 启动.bat。")
                    return
                subprocess.Popen([sys.executable, srv], cwd=base, close_fds=True)
                self.say("[启动] web_server.py")
            else:
                self.say("[提示] 这个文件夹里没有找到主程序。")
                self.say("        请把「安装程序.exe」放到主程序（Lyric2TTML.exe 或 启动.bat）")
                self.say("        所在的那个文件夹里，再运行一次。")
                return
            self.set_sub("已启动，浏览器会自动打开界面", OK)
        except Exception as e:
            self.say(f"[错误] 启动失败：{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 补全
    def start_install(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.btn_go.configure(state="disabled")
        self.btn_quit.configure(state="disabled")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self) -> None:
        failed = []
        for key, name, fn in self.items:
            if fn is None:
                self.say(f"\n⚠️ {name} 需要你手动处理（程序没法代劳）")
                failed.append(name)
                continue
            self.set_row(key, "work", "正在安装…")
            self.set_sub(f"正在安装：{name}")
            self.say(f"\n── 安装 {name} ──")
            try:
                ok = bool(fn())
            except Exception as e:
                self.say(f"[错误] {type(e).__name__}: {e}")
                ok = False
            if ok:
                self.set_row(key, "ok", "已安装")
                self.say(f"[完成] {name}")
            else:
                self.set_row(key, "bad", "安装失败")
                self.say(f"[失败] {name}")
                failed.append(name)

        if failed:
            self.set_sub(f"{len(failed)} 项没装成，见日志", BAD)
            self.say("\n" + "=" * 46)
            for f in failed:
                self.say(f"  未完成：{f}")
            self.say("可以关掉重试，或把日志发给开发者。")
            self.q.put(("done", "normal", "重试"))
        else:
            # ⚠️ **不能装完再跑一次完整 doctor** —— 那个要 import torch + 加载 2GB
            # cudnn，界面会长时间无响应（实测卡 15~20 分钟）。
            # 补全只是下文件，所以这里只做**轻量复查**（看文件在不在），秒出结果。
            keys = [k for k, _n, _f in self.items]
            self.say("\n" + "=" * 46)
            self.say("  正在复查（只检查刚装的文件，很快）…")
            self.set_sub("正在复查…", ACCENT)
            try:
                still = self.quick_recheck(keys)
            except Exception as e:
                self.say(f"  [警告] 复查出错（不影响使用）：{type(e).__name__}: {e}")
                still = []
            if still:
                self.say(f"  ⚠ 还有 {len(still)} 项没就位：{'、'.join(still)}")
                self.say("     可以点「开始补全」再试一次（支持断点续传）。")
                self.set_sub(f"还有 {len(still)} 项没装好", BAD)
                self.q.put(("done", "normal", "继续补全", "install"))
            else:
                self.say("  ✓ 补全的文件都已就位。")
                self.say("\n" + "=" * 46)
                self.say("  ✅ 安装完成！点下面的「▶ 启动软件」开始使用。")
                self.set_sub("安装完成，可以使用了", OK)
                self.q.put(("done", "normal", "▶ 启动软件", "launch"))
            return
        self.q.put(("quitbtn", None))
        self.root.after(0, lambda: self.btn_quit.configure(state="normal"))


def e2e(dest: str) -> int:
    """无界面地把"安装 → 检查 → 补全 → 复查"跑一遍，并打印每一步结果。

    这是给**自动化**用的入口（`安装程序.exe --e2e E:\\Lyric2TTML`），
    这样我能像小白一样真的把软件装到 E 盘，而不是只测源码。
    """
    print("=" * 70)
    print(f"  端到端安装测试 -> {dest}")
    print("=" * 70)
    # ⚠️ Windows 控制台默认 GBK，日志里的 ✓/✗/⚠ 会直接把脚本崩掉
    # （UnicodeEncodeError，真踩过）。先切 UTF-8，切不动就退化成 ASCII。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"  payload_zip() = {payload_zip() or '(取不到内置载荷！)'}")
    if not payload_zip():
        print("  ✗ 没有载荷 —— 这不是打包版，测试无意义")
        return 1

    root = tk.Tk()
    root.withdraw()
    app = Installer(root)
    app.install_dir = dest
    app._app_ready = False
    # 无界面模式下，界面上的日志/状态只能丢掉 —— 直接转打到标准输出，
    # 否则失败原因看不见（第一次跑就是这么白跑一轮的）。
    app.say = lambda m="": print("    " + str(m), flush=True)
    app.set_sub = lambda t, c=None: None
    print("[debug] extract_app 即将开始")

    print("\n[1] 解压安装…")
    ok = app.extract_app()
    for _ in range(30):
        root.update()
        app._pump_now()
    exe = os.path.join(dest, "Lyric2TTML.exe")
    print(f"    解压 {'成功' if ok else '失败'}；主程序存在：{os.path.exists(exe)}")
    if not ok:
        root.destroy()
        return 1
    n = sum(len(fs) for _, _, fs in os.walk(dest))
    mb = sum(os.path.getsize(os.path.join(r, f))
             for r, _, fs in os.walk(dest) for f in fs) / 1024 / 1024
    print(f"    装了 {n} 个文件，{mb:.0f} MB")

    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    print("\n[2] 快捷方式 / 卸载程序")
    print(f"    桌面快捷方式：{os.path.exists(os.path.join(desk, 'Lyric2TTML.lnk'))}")
    print(f"    卸载程序.bat：{os.path.exists(os.path.join(dest, '卸载程序.bat'))}")

    print("\n[3] 环境检查（问主程序）")
    rep = ask_app_report(dest)
    items = (rep or {}).get("items") or []
    if not items:
        print("    ✗ 拿不到主程序的报告")
        root.destroy()
        return 1
    # ⚠️⚠️ **这里必须和 GUI 路径同口径**（2026-10-14 修，用户验收时发现）。
    # 原来自测用 `level == "crit"` 判"必需" —— 那是**旧口径**，
    # 会把语音模型（3 GB）和 CUDA 运行库（527 MB）也算进去，
    # 于是 `--e2e` **真的去下载 4 GB**：
    # 实测安装目录从 342 MB 涨到 **4025.9 MB**，而 GUI 路径已经不这么做了。
    # 两条路径口径不一致是**测试在骗人**：自测说"安装阶段要下大件"，
    # 而用户实际不会 —— 反过来也会掩盖问题。现在统一用 scope。
    import types

    def _scope_of(d: dict) -> str:
        """主程序报了 scope 就用它；退回老 `--doctor` 时按 key 映射算。"""
        sc = d.get("scope")
        if sc in ("launch", "convert", "extra"):
            return sc
        ns = types.SimpleNamespace(key=d.get("key", ""))
        return envcheck.scope_of(ns)

    launch_bad = [i for i in items if not i["ok"] and _scope_of(i) == "launch"]
    convert_pending = [i for i in items
                       if not i["ok"] and _scope_of(i) == "convert"]
    print(f"    共 {len(items)} 项；影响打开 {len(launch_bad)} 项；"
          f"处理歌词才要 {len(convert_pending)} 项")
    for i in launch_bad:
        print(f"      [影响打开] {i['name']}：{i['detail']}")
    for i in convert_pending:
        print(f"      [待下载·不拦打开] {i['name']}：{i['detail']}")

    print("\n[4] 补全（**只补影响打开的**）…")
    print("    ⚠️ 语音模型 / CUDA 运行库属于「处理歌词才要」，"
          "安装阶段**不下载** —— 到软件里的「模型」页下。")
    done_cuda = False
    skip_models = "--no-models" in sys.argv
    if skip_models:
        print("    （--no-models：跳过下载，只验安装/桌面窗口/卸载）")
    # ⚠️ 只遍历 `launch_bad`。`convert_pending` 一律跳过（用户明确要求）。
    for i in ([] if skip_models else launch_bad):
        k = i["key"]
        fn = None
        if k == "campp":
            fn = core.fetch_campp
        elif k.startswith("cuda_"):
            # 理论上不会进来（CUDA 现在是 convert），留着防御：
            # 真进来的话说明 scope 表被改坏了，明确说出来。
            print(f"    ⚠️ {k} 被判成 launch 了 —— scope 表可能有问题，跳过不下载")
            continue
        if fn is None:
            continue
        print(f"    ── {i['name']} ──")
        try:
            r = bool(fn())
        except Exception as e:
            print(f"    [异常] {type(e).__name__}: {e}")
            r = False
        print(f"    {'[成功]' if r else '[失败]'} {i['name']}")

    # ---- [4.5] 桌面窗口能不能真的建出来（本次改造的核心验收点）----
    # ⚠️ 必须用 offscreen：`--e2e` 是无界面自动化，真开窗口会挂住。
    # 这一条是**唯一**能证明"装完之后双击真的出桌面窗口"的检查：
    # `--doctor`/`--help` 都不会去加载 Qt 平台插件，缺插件时它们照样通过。
    print("\n[4.5] 桌面窗口自检（Qt 平台插件）")
    exe = os.path.join(dest, "Lyric2TTML.exe")
    try:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                   L2T_ALLOW_MULTI="1", L2T_HEADLESS="1",
                   L2T_DATA_DIR=os.path.join(dest, ".e2e-data"),
                   PYTHONIOENCODING="utf-8")
        r = subprocess.run([exe, "--uicheck"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env,
                           cwd=dest, timeout=900)
        ui_ok = r.returncode == 0 and "全部通过" in (r.stdout or "")
        print(f"    {'✓' if ui_ok else '✗'} --uicheck 退出码 {r.returncode}")
        if not ui_ok:
            print((r.stdout or "")[-700:])
            print((r.stderr or "")[-700:])
    except Exception as e:
        print(f"    [异常] {type(e).__name__}: {e}")
        ui_ok = False

    # ---- [4.6] 卸载：默认必须**保留模型** ----
    # ⚠️ `--no-uninstall`：只安装、不自动卸载。
    # 用来单独测"卸载/重试"这条路（否则 [4.6] 会在同进程里先卸一遍，
    # 安装目录已经被动过，没法再验卸载行为）。
    # `un_skipped` 要在这里先定义（`--no-uninstall` 分支之外也会用到）
    un_skipped = False
    if "--no-uninstall" in sys.argv:
        print("\n[4.6] 跳过卸载检查（--no-uninstall）")
        # ⚠️⚠️ **跳过必须当成"通过"，不能留 False**（真踩到）。
        # `un_ok` 默认是 False，而下面 `if not un_ok: return 1` ——
        # 于是"每一项都通过了、只是没测卸载"的 `--e2e --no-uninstall`
        # **退出码是 1**，看起来像安装失败。
        # 危害是实打实的：`tools/test_isolated.py` 就把它判成
        # "安装流程跑通 ✗ exit=1"，而日志里每一步都是 ✓ ——
        # **工具谎报失败，和谎报成功一样糟**（会让人去查一个不存在的问题）。
        # 语义上"跳过"应当是**中性**：不算失败，也不能当成"卸载已验证"。
        un_ok = True
        un_skipped = True
    else:
        print("\n[4.6] 卸载检查（默认保留模型）")
        un_ok = False
        try:
            fake_local = os.path.join(os.path.dirname(dest), ".e2e-localappdata")
            # ⚠️ 桌面目录也要指到沙箱：`--e2e` 不该动用户真实的桌面快捷方式
            fake_desk = os.path.join(os.path.dirname(dest), ".e2e-desktop")
            os.makedirs(fake_desk, exist_ok=True)
            marker = os.path.join(dest, ".hf_cache", "e2e-keep.bin")
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            open(marker, "wb").write(b"KEEP")
            env2 = dict(os.environ, LOCALAPPDATA=fake_local,
                        PYTHONIOENCODING="utf-8")
            r = subprocess.run([exe, "--uninstall", dest, "--keep-models", "--yes",
                                "--desktop", fake_desk],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env2,
                               cwd=os.path.dirname(dest), timeout=600)
            moved = os.path.join(fake_local, "Lyric2TTML", ".hf_cache",
                                 "e2e-keep.bin")
            # ⚠️ **不能用"安装目录还在不在"当判据**：这个检查自己就是
            # `<安装目录>\Lyric2TTML.exe` 起的子进程，Windows 不允许删除正在运行的
            # 可执行文件，所以安装目录**必然**残留（真实用户是双击 .bat、
            # 由 cmd 起 exe，卸载完 exe 也结束了，没这个问题）。
            # 判据改成：① 模型被搬走 ② 主程序以外的文件删干净 ③ 快捷方式没了。
            prog_left = os.path.isfile(os.path.join(dest, "Lyric2TTML.exe"))
            # ⚠️ 判据说明（这一条踩过好几次，写清楚免得又误判）：
            #
            # * **不能用"安装目录还在不在"当判据** —— 这个检查自己就是
            #   `<安装目录>\Lyric2TTML.exe` 起的子进程，Windows **不允许删除正在
            #   运行的进程正在使用的文件**，所以目录必然残留（真实用户是双击
            #   `卸载程序.bat`、由 cmd 起 exe，卸载完 exe 立刻结束，不存在这个问题；
            #   而且用户按提示"再运行一次"就彻底干净了 —— uninstall 的返回值 1
            #   已经把这个话说明白了）。
            # * **模型必须被保留**：这是硬要求，不能因为这个环境限制就放过。
            # * **快捷方式必须删掉**。
            # * 残留文件数只作为**信息**打印（不是失败条件）。
            leftovers = []
            for _root, _dirs, _files in os.walk(dest):
                rel = os.path.relpath(_root, dest)
                if rel.split(os.sep)[0].startswith("."):
                    continue
                for _f in _files:
                    leftovers.append(os.path.join(_root, _f))
            link_gone = not os.path.isfile(os.path.join(fake_desk, "Lyric2TTML.lnk"))
            un_ok = (os.path.isfile(moved) and link_gone)
            print(f"    {'✓' if un_ok else '✗'} 退出码 {r.returncode}（1=有文件被占用，"
                  f"属预期）；模型已保留并搬走={os.path.isfile(moved)}；"
                  f"快捷方式已删={link_gone}")
            print(f"       （残留 {len(leftovers)} 个文件 —— 卸载进程自己就是"
                  f"<安装目录>\\Lyric2TTML.exe，Windows 删不掉正在运行的程序；"
                  f"已交给退出后运行的清理助手收尾）")
            # ⚠️⚠️ 这里曾经写"按提示再运行一次即可" —— **那句话是错的**，
            # 而且从没人验证过。实测（`tools/test_uninstall_real.py`）：
            # 老的卸载实现会把 `_internal/` 一起删掉（384 → 32 个文件，
            # 连 `encodings` 都没了），而冻结版 exe 必须靠它才能启动 ⇒
            # 用户"再运行一次"只会得到 `Failed to import encodings module`。
            #
            # ⚠️ 判据要分清两种"目录还在"：
            #   * **延迟清理还没跑完** —— 目录还在，但运行库完好，
            #     清理助手几秒后会删掉它。这是**正常**，不算失败。
            #   * **清理失败** —— 目录还在、运行库又坏了，那才是真失败
            #     （用户想重试都起不来）。
            # 我第一版写成了"目录还在就检查运行库"，结果**清理成功**时目录
            # 已经整没了，被判成 `encodings 完好=False` ⇒ 假失败（实测抓到）。
            if os.path.isdir(dest):
                # ⚠️ 判据**不能**只看 `_internal/encodings` 目录在不在 ——
                # PyInstaller 可能把 `encodings` 打进 exe 归档而不是摊成目录，
                # 那样"目录不存在"并不代表 exe 起不来（实测就是这样：
                # 目录判定 False，但 exe 其实完全正常）。
                # 真正的问题是"**用户还能不能再运行一次卸载**"，
                # 所以判据应该是：`_internal/` 里还有没有运行库文件（.pyd / python*.dll）。
                n_rt = 0
                internal = os.path.join(dest, "_internal")
                if os.path.isdir(internal):
                    for _r, _d, _fs in os.walk(internal):
                        for _f in _fs:
                            low = _f.lower()
                            if low.endswith(".pyd") or low.startswith("python"):
                                n_rt += 1
                print(f"       安装目录仍在（清理助手还在收尾）；"
                      f"运行库文件 {n_rt} 个")
                if n_rt == 0:
                    print("       ⚠ 运行库被删 ⇒ exe 无法再启动，卸载**无法重试**")
                    un_ok = False
            else:
                print("       安装目录已彻底删除（延迟清理已完成）")
            if not un_ok:
                print((r.stdout or "")[-900:])
                print((r.stderr or "")[-400:])
        except Exception as e:
            print(f"    [异常] {type(e).__name__}: {e}")

    root.destroy()
    print()
    print("=" * 70)
    if not ui_ok:
        print("  ✗ 桌面窗口起不来（装完双击也开不了）")
        return 1
    if not un_ok:
        print("  ✗ 卸载检查没过（可能删掉了用户的模型）")
        return 1
    if un_skipped:
        # 说清楚"没测卸载"，别让调用者以为这一项也过了
        print("  ✓ 通过（含桌面窗口；卸载检查被 --no-uninstall 跳过，**未验证**）")
        return 0
    print("  ✓ 全部通过（含桌面窗口与卸载）")
    return 0


def _is_elevated() -> bool:
    """当前进程是不是管理员（只用于**提示文案**，不再用来强制提权）。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated() -> bool:
    """**已停用（保留函数只是为了兼容旧调用点）** —— 永远返回 False。

    ⚠️⚠️ **为什么停用**（2026-10-13，用户明确要求方案①）：

    这里原来在启动时无条件自提权：只要不是管理员就 `ShellExecuteW "runas"`
    重启自己 —— 也就是**双击必弹 UAC**。它当年的理由是"安装程序确实需要管理员：
    要往用户选的任意目录写（含盘根下新建目录）、建桌面快捷方式"。
    但那个理由**站不住**：

    * **默认安装位置是 `%LOCALAPPDATA%\\Lyric2TTML`**（用户目录）——
      写它根本不需要管理员；
    * 开始菜单/桌面快捷方式走 `win_integration`，用的是 **HKCU + 用户自己的
      「开始菜单/程序」目录**，也不需要管理员；
    * 卸载注册表项同样写 **HKCU**；
    * 真正需要管理员只有一种情况：**用户主动选**了 `Program Files` 之类的
      机器级目录。那时该由**用户自己**右键「以管理员身份运行」，
      而不是安装程序擅自弹一个用户没预期的 UAC。

    代价（改前）：普通用户被强制弹 UAC，拒绝就装不了；而且**我无法在本机
    自动化验收正式发行的那个 exe**（带 UAC 的进程起不来）。
    改后：普通用户双击直接开始安装；`--e2e` 也能跑**正式发行的那一个文件**。

    权限不足时怎么处理：不再提前提权，而是**在真正要写的时候**判断
    （`ensure_dir()` 先试写、失败给人话 + 推荐位置 + 告诉用户可以用
    管理员身份运行）。失败要**可解释**，不要一开始就拦着所有人。
    """
    return False


def _declare_dpi_aware() -> bool:
    """声明**per-monitor DPI 感知**（必须在 `tk.Tk()` **之前**调）。

    ⚠️⚠️ 为什么必须有（第 23 轮踩到，而且是被"截图被切"顺带查出来的）：
    不声明的话，Windows 会把整个进程窗口当"不懂 DPI 的老程序"，
    **按显示器缩放做位图拉伸** —— 界面能用，但**所有字都是糊的**。
    而且 Tk 报出来的屏幕尺寸是**逻辑值**（本机 1536x864，真实是 1920x1080），
    于是"窗口坐标/尺寸"和"屏幕像素"差 1.25 倍（截图被切就是这个原因）。

    声明之后（实测）：Tk 报 1920x1080（物理值），`tk scaling` 由系统自动算对，
    字是**真渲染**的 —— 100%/125%/150%/200% 下都清晰。

    返回值只用于日志；**失败不抛异常**（老系统上没有 shcore 也要能跑）。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE（Win8.1+）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:                                   # noqa: BLE001
        try:
            import ctypes
            # 退路：Win7/Vista 的旧接口（1 = 系统 DPI 感知）
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:                               # noqa: BLE001
            return False


def main() -> int:
    # ---- 卸载：`卸载程序.bat` 和主程序的 `--uninstall` 都走这里 ----
    # ⚠️ 必须放在**提权之前**：卸载不需要管理员（删自己安装的目录），
    # 而且提权会让"双击卸载"多弹一次 UAC，用户会以为要装东西。
    if "--uninstall" in sys.argv:
        i = sys.argv.index("--uninstall")
        rest = sys.argv[i + 1:]
        return uninstall_cli(rest)

    # ---- 无界面自测：让"打包后的真安装程序"能被我自动化跑一遍 ----
    # 用法：安装程序.exe --e2e E:\Lyric2TTML
    # ⚠️ 自测**跳过提权**，否则 UAC 弹窗会把自动化测试挂死。
    if "--e2e" in sys.argv:
        i = sys.argv.index("--e2e")
        dest = sys.argv[i + 1] if len(sys.argv) > i + 1 else r"E:\Lyric2TTML"
        return e2e(dest)

    # ---- 截图 / 只看一眼：**不提权、不干活** ----
    # ⚠️ 必须在 `relaunch_elevated()` 之前 —— 否则截图也会弹 UAC（自动化挂死）。
    if shot_mode() or view_mode():
        _declare_dpi_aware()          # ⚠️ 必须在这里也声明（这条分支自建 Tk）
        root = tk.Tk()
        # ⚠️ 同样不再写死 `tk scaling 1.25` —— 见 main() 末尾的说明：
        # 声明 per-monitor DPI 感知后，Tk 会从系统拿到正确缩放
        # （实测声明后 Tk 报 1920x1080、scaling≈1.67），
        # 字是真渲染而不是被位图拉伸。
        inst = Installer(root, autostart=False)

        if shot_mode():
            out = _shot_path()

            def _prepare() -> None:
                # ⚠️⚠️ **必须把窗口摆到"完全落在屏幕内"的位置**（真踩到，第 23 轮）。
                # 之前是"提到最前 + 置顶后抓 (rootx, rooty, w, h)"，而
                # `ImageGrab.grab(bbox=…)` **抓的是屏幕像素** ——
                # 窗口只要有一部分越出屏幕（或系统把它摆到负坐标 / 贴右边缘），
                # 那部分就**抓不到**，图里右边和下面被整齐地切掉。
                # 我照着这种图判断"按钮不见了"，完全是假的。
                # 所以：先按屏幕尺寸算出能完整放下的坐标，再摆过去。
                root.update_idletasks()
                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()
                w, h = 860, 700
                x = max(0, min(40, sw - w - 8)) if sw > w else 0
                y = max(0, min(40, sh - h - 48)) if sh > h else 0
                root.geometry(f"{w}x{h}+{x}+{y}")
                root.update_idletasks()
                root.lift()
                root.attributes("-topmost", True)
                root.focus_force()
                root.update()
                time.sleep(0.3)          # 给窗口管理器时间真正落位
                root.update()

            def _grab() -> None:
                try:
                    from PIL import ImageGrab
                    _prepare()
                    root.update()
                    x = root.winfo_rootx()
                    y = root.winfo_rooty()
                    w, h = root.winfo_width(), root.winfo_height()
                    sw = root.winfo_screenwidth()
                    sh = root.winfo_screenheight()
                    # ⚠️⚠️ **必须按 DPI 缩放换算**（真踩到，第 23 轮，查了四轮）。
                    # `winfo_rootx/width` 是 **Tk 逻辑像素**，而 `ImageGrab` 抓的是
                    # **物理屏幕像素**。两者不一致时（本机实测：Tk 报屏幕
                    # 1536x864，整屏抓图却是 1920x1080 ⇒ 缩放 1.25），
                    # 抓 `(x, y, x+w, y+h)` 只覆盖窗口的左上 1/1.25，
                    # 图里右边和下面**被整齐切掉** ——
                    # 我照着这种图判断"按钮不见了""区块没显示"，全是假的。
                    #
                    # ⚠️ **不要用 `GetDpiForSystem()`**：本进程实测返回 96，
                    # 而真实缩放是 1.25（Tk 在 DPI-aware / 非 aware 之间口径不同）。
                    # 用"整屏抓图尺寸 ÷ Tk 报的屏幕尺寸"这个**可观测量**最稳：
                    # 它就是 ImageGrab 的像素与 Tk 坐标之间的换算比。
                    scale = 1.0
                    try:
                        sw_tk = max(1, root.winfo_screenwidth())
                        probe = ImageGrab.grab()
                        scale = probe.size[0] / sw_tk
                    except Exception:                   # noqa: BLE001
                        scale = 1.0
                    X, Y = int(round(x * scale)), int(round(y * scale))
                    W, H = int(round(w * scale)), int(round(h * scale))
                    img = ImageGrab.grab(bbox=(X, Y, X + W, Y + H))
                    if abs(scale - 1.0) > 0.01:
                        print(f"  （缩放 {scale:.3f}：逻辑 {w}x{h} → 物理 {W}x{H}）")
                    if X < 0 or Y < 0:
                        print(f"  ⚠️ 窗口未完整落在屏幕内：({X},{Y}) —— 截图可能被切")
                    img.save(out)
                    # ⚠️⚠️ **完整性必须几何化地判**（第 23 轮的教训：切过的图"看起来正常"）。
                    # 光看 `img.size` **没用** —— 抓到的图就是请求的尺寸，
                    # 被裁这件事只体现在"请求矩形超出屏幕 / 没按缩放换算"上。
                    # 判据：① 请求矩形（含缩放）要落在物理屏幕内；② 实得尺寸 = 请求尺寸。
                    over = (X + W > probe.size[0]) or (Y + H > probe.size[1])
                    if img.size != (W, H) or over:
                        print(f"  ✗ 这张图**不完整**，别拿它判断界面！"
                              f"请求 {X},{Y} {W}x{H}，屏幕 {probe.size}，"
                              f"实得 {img.size}")
                    else:
                        # ⚠️ **"图完不完整"能做到什么、做不到什么**（第 23 轮，写清楚）：
                        # 几何检查能抓"请求矩形越界"和"PIL 静默裁到屏幕内"；
                        # 但**抓不到"没按缩放换算"** —— 那种情况几何上全都合法，
                        # 只是内容少了右边/下面 25%。
                        # 我试过再加一条"内容级"判据（看四角是否是窗口底色），
                        # **决定不采用**：窗口里的滚动条等控件本来就比底色亮，
                        # 实测正常界面被误报成"被切"（3/4 同色）；把容差放宽又会失去意义。
                        # 与其塞一个会误报的检查，不如**把缩放比显式打出来** ——
                        # 一眼就能看到"有没有按缩放换算"（阴性对照也靠它）。
                        print(f"  已截图：{out}（{W}x{H} @ {X},{Y}，"
                              f"缩放 {scale:.2f}）")
                except Exception as e:                  # noqa: BLE001
                    print(f"  截图失败：{type(e).__name__}: {e}")
                root.destroy()

            root.update()
            # ⚠️ 也不能过早抓：窗口有淡入/重绘过程，抓早了会拍到**半透明**的中间态
            # （第二次抓到的是界面与桌面的混合图，能看见底下的头像）。
            # 先抬到最前，等合成器画完再抓。
            root.after(150, _prepare)
            root.after(2500, _grab)
        root.mainloop()
        return 0

    # ---- 正常双击：**不提权**，直接装到用户目录 ----
    # ⚠️ 这里原来是 `if not _is_elevated(): relaunch_elevated()` —— 即"双击必弹
    # UAC"。已按用户要求（方案①：per-user 安装）去掉。理由见
    # `relaunch_elevated()` 的说明：默认位置是 `%LOCALAPPDATA%`，
    # 快捷方式与卸载注册表项都走 HKCU，**没有任何一步真的需要管理员**。
    # 用户主动选机器级目录（如 Program Files）时，让他自己右键「以管理员身份运行」，
    # 权限不足由 `ensure_dir()` 在真正写入时给出可解释的失败提示。
    _declare_dpi_aware()
    root = tk.Tk()
    # ⚠️⚠️ **删掉了写死的 `tk scaling 1.25`**（第 23 轮修）。
    # 原来这里硬把 Tk 缩放设成 1.25 —— 那是**为了在 125% 的机器上看起来不糊**
    # 而打的补丁，实际效果是：
    #   * 进程**没声明 DPI 感知** ⇒ Windows 把整个窗口**位图拉伸**，
    #     所有文字/线条都被拉糊（不是"字变大"，是"变虚"）；
    #   * 于是 Tk 报的屏幕是**逻辑值**（1536x864），而真实屏幕是 1920x1080；
    #   * 再叠一个写死的 1.25，在 100% 的机器上字会**过大**、在 150% 的机器上**仍糊**。
    # 现在：先声明 **per-monitor DPI aware**，Tk 自己就能从系统拿到
    # 正确的 `tk scaling`（实测声明后 Tk 报 1920x1080，缩放自动算对），
    # 字是**真渲染**而不是被拉伸 —— 任何缩放下都清晰。
    Installer(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
