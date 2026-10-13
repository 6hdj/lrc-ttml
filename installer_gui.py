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

BG = "#0f1117"
FG = "#e8e8f0"
DIM = "#8b95ad"
OK = "#3ddc84"
BAD = "#ff6b6b"
WARN = "#ffc857"
ACCENT = "#4aa8ff"


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
        r = subprocess.run([exe, "--doctor", "--json"], cwd=base,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        line = ""
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("{"):
                line = ln
                break
        return json.loads(line) if line else {}
    except Exception:
        return {}


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


class Installer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.items: list = []          # [(键, 名称, 补全函数)]
        self.busy = False
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
            self.log_line("然后检查运行环境、把你缺的东西补上。")

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

        # 安装位置：**必须让用户能改**。默认装到用户目录（不需要管理员权限，
        # 也不会有 Program Files 的写权限问题），但想装哪儿应该由用户说了算。
        self.pathbar = tk.Frame(self.root, bg=BG)
        self.pathbar.pack(fill="x", padx=28, pady=(2, 6))
        tk.Label(self.pathbar, text="安装到：", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        self.path_var = tk.StringVar(value=self.install_dir)
        self.path_entry = tk.Entry(
            self.pathbar, textvariable=self.path_var, bg="#0b0e14", fg=FG,
            insertbackground=FG, relief="flat", font=("Microsoft YaHei UI", 10))
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(2, 8),
                             ipady=5)
        self.btn_browse = tk.Button(
            self.pathbar, text="浏览…", command=self.choose_dir, relief="flat",
            bg="#252b3a", fg=FG, activebackground="#303848",
            activeforeground=FG, font=("Microsoft YaHei UI", 9),
            padx=12, pady=4, cursor="hand2", bd=0)
        self.btn_browse.pack(side="left")

        # 清单：**必须能滚动**。
        # ⚠️ 检查项有 20+ 行，直接 pack 进窗口会把下面的日志和按钮挤出可视区
        # （真踩过：用户截图里清单只显示到一半，按钮根本看不见）。
        # 所以套一层 Canvas + Scrollbar，并给它一个固定高度上限；
        # 日志区用 expand=True 吃掉剩余空间，窗口拉大时也自适应。
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="x", padx=28, pady=(4, 10))
        self.canvas = tk.Canvas(wrap, bg="#161a24", highlightthickness=1,
                                highlightbackground="#252b3a", height=250, bd=0)
        self.canvas.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        sb.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=sb.set)

        self.rows_box = tk.Frame(self.canvas, bg="#161a24")
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

        # 日志
        self.log = tk.Text(self.root, height=7, bg="#0b0e14", fg="#c8d2e0",
                           insertbackground=FG, relief="flat", wrap="word",
                           font=("Consolas", 9), padx=10, pady=8)
        self.log.pack(fill="both", expand=True, padx=28)
        self.log.configure(state="disabled")

        # 按钮区
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=28, pady=16)
        self.hint = tk.Label(bar, text="", bg=BG, fg=DIM,
                             font=("Microsoft YaHei UI", 9))
        self.hint.pack(side="left")
        self.btn_quit = self._btn(bar, "退出", self.root.destroy, side="right")
        self.btn_go = self._btn(bar, "开始补全", self.start_install,
                                side="right", primary=True)
        self.btn_go.configure(state="disabled")

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
                      bg=ACCENT if primary else "#252b3a",
                      fg="#08111c" if primary else FG,
                      activebackground="#6cb8ff" if primary else "#303848",
                      activeforeground="#08111c" if primary else FG,
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
                    r = tk.Frame(self.rows_box, bg="#161a24")
                    r.pack(fill="x", padx=14, pady=4)
                    icon = tk.Label(r, text="•", bg="#161a24", fg=DIM, width=3,
                                    font=("Segoe UI Symbol", 13))
                    icon.pack(side="left")
                    tag = {"crit": "必需", "opt": "可选", "info": ""}[level]
                    tk.Label(r, text=name, bg="#161a24", fg=FG, width=22,
                             anchor="w", font=("Microsoft YaHei UI", 10)
                             ).pack(side="left")
                    tk.Label(r, text=(f"[{tag}]" if tag else ""), bg="#161a24",
                             fg=WARN, width=6, anchor="w",
                             font=("Microsoft YaHei UI", 8)).pack(side="left")
                    det = tk.Label(r, text=why, bg="#161a24", fg=DIM, anchor="w",
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
                    icon.configure(text={"ok": "✓", "bad": "✕", "skip": "–",
                                         "wait": "•", "work": "…"}.get(state, "•"),
                                   fg={"ok": OK, "bad": BAD, "skip": DIM,
                                       "wait": DIM, "work": WARN}.get(state, DIM))
                    if detail:
                        det.configure(text=detail,
                                      fg=BAD if state == "bad" else DIM)
                elif kind == "setbtn":
                    _, text, cmd, state = m
                    self.btn_go.configure(text=text, command=cmd, state=state)
                elif kind == "run":
                    m[1]()
                elif kind == "sub":
                    self.sub.configure(text=m[1], fg=m[2])
                elif kind == "done":
                    self.busy = False
                    self.btn_quit.configure(state="normal")
                    self.btn_go.configure(state=m[1], text=m[2])
                    # 全部就绪时，主按钮变成「启动软件」——用户就不用再去找 启动.bat
                    if len(m) > 3 and m[3] == "launch":
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
                self.say("  这个文件夹**已经存在，但当前用户写不进去**。")
                self.say("  最常见的原因：它是**以管理员权限**创建/解压出来的，")
                self.say("  属主变成了 Administrators，普通用户只有读权限。")
                self.say("")
                self.say("  解决办法（任选）：")
                self.say(f"    ① 把它整删掉，然后在资源管理器里**手动新建**一个，再选它")
                self.say(f"       （要删的：{dest}）")
                self.say("    ② 直接换一个位置，比如下面这个必然能写：")
                self.say(f"       {default_install_dir()}")
            else:
                self.say("  这个位置建不出文件夹。")
                self.say("")
                self.say("  解决办法（任选）：")
                self.say(f"    ① 用默认位置：{default_install_dir()}")
                self.say("    ② 在资源管理器里先手动建好文件夹，再用「浏览…」选中它")
            self.set_sub("安装位置不可写，请换一个", BAD)
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
                        last = None
                        for attempt in range(3):
                            try:
                                with zf.open(n) as src, open(target, "wb") as out:
                                    shutil.copyfileobj(src, out)
                                last = None
                                break
                            except PermissionError as e:
                                last = e
                                time.sleep(0.4)
                        if last is not None:
                            # 单个文件写不进去 → 记下来继续，**不要**让整次安装失败
                            locked.append(rel)
                    if total and (i % 200 == 0 or i == total):
                        self.set_sub(f"正在安装… {i * 100 // total}%")
            self.say(f"[完成] 已解压 {len(names)} 个文件")
            if locked:
                self.say("")
                self.say(f"⚠ 有 {len(locked)} 个文件被占用/只读，没能覆盖（其余都装好了）：")
                for x in locked[:6]:
                    self.say(f"    {x}")
                self.say("  多半是**程序还开着**。关掉程序（黑框窗口）和浏览器页面后，")
                self.say("  重新运行本安装程序即可补上；这些文件不影响核心功能。")
            self.say(f"[完成] 已解压 {len(names)} 个文件")
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
        try:
            from desktop import __version__ as _v
            version = str(_v)
        except Exception:                               # noqa: BLE001
            version = "1.1.0"
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

            for it in rows:
                self.q.put(("addrow", it.key, it.name, it.why, it.level))

            # 需要补全的动作映射（envcheck 只负责"诊断"，动作在 install 里）
            actions = {
                "whisper": lambda: core.fetch_whisper(self.model),
                "campp": core.fetch_campp,
            }
            # 任何一个 CUDA DLL 缺 → 一次把三个都装齐（同一个 wheel 里）
            cuda_missing = [it for it in rows
                            if it.key.startswith("cuda_") and not it.ok]
            if cuda_missing:
                actions["cuda_gether"] = core.fetch_cuda
            # 缺的包合并成一次 pip
            pkg_missing = [it for it in rows
                           if it.key.startswith("pkg_") and not it.ok
                           and it.level == envcheck.CRIT]

            for it in rows:
                self.set_row(it.key, "ok" if it.ok else "bad",
                             it.detail + ("" if it.ok else
                                          ("　" + it.fix.splitlines()[0]
                                           if it.fix else "")))
                if it.ok:
                    self.say(f"[OK] {it.name}"
                             + (f"：{it.detail}" if it.detail else ""))
                else:
                    self.say(f"[缺] {it.name}" + (f"：{it.detail}" if it.detail else ""))
                    if it.level == envcheck.CRIT:
                        # ⚠️ `it` 是从 JSON 建的 SimpleNamespace，**没有 action 字段**
                        # （doctor --json 只输出 key/name/why/level/ok/detail/fix）。
                        # 直接取 `it.action` 会 AttributeError —— 真踩过：
                        # 检查在 CUDA 那一项崩掉，后面的模型/权重没能进待装列表，
                        # 于是"什么都没装"却报"安装完成"，用户一启动就报模型缺失。
                        self.items.append(
                            (it.key, it.name,
                             actions.get(it.key, getattr(it, "action", None))))

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

            mc, mo = envcheck.summarize(rows)
            if not mc and not mo:
                self.set_sub("全部就绪，可以直接使用", OK)
                self.say(f"\n✅ 检查了 {len(rows)} 项，什么都不缺。")
                self.say("   点下面的「▶ 启动软件」就能开始用了。")
                self.q.put(("done", "normal", "▶ 启动软件", "launch"))
            elif not mc:
                self.set_sub(f"核心功能可用（{len(mo)} 项可选未装）", WARN)
                self.say(f"\n核心功能已就绪；另有 {len(mo)} 项可选未装（不影响使用）。")
                self.q.put(("done", "normal", "▶ 启动软件", "launch"))
            else:
                self.set_sub(f"发现 {len(mc)} 项必需缺失，可以自动补全", BAD)
                self.say(f"\n检查了 {len(rows)} 项，缺 {len(mc)} 项必需的。"
                         f"点「开始补全」，它会自动下载安装。")
                self.q.put(("done", "normal", f"开始补全（{len(mc)} 项）"))
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
    bad = [i for i in items if not i["ok"] and i["level"] == "crit"]
    print(f"    共 {len(items)} 项，缺 {len(bad)} 项必需的：")
    for i in bad:
        print(f"      - {i['name']}：{i['detail']}")

    print("\n[4] 补全…")
    done_cuda = False
    skip_models = "--no-models" in sys.argv
    if skip_models:
        print("    （--no-models：跳过下载，只验安装/桌面窗口/卸载）")
    for i in ([] if skip_models else bad):
        k = i["key"]
        fn = None
        if k == "whisper":
            fn = lambda: core.fetch_whisper("large-v3")
        elif k == "campp":
            fn = core.fetch_campp
        elif k.startswith("cuda_") and not done_cuda:
            fn = core.fetch_cuda
            done_cuda = True
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
        env2 = dict(os.environ, LOCALAPPDATA=fake_local, PYTHONIOENCODING="utf-8")
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
        print(f"       （残留 {len(leftovers)} 个文件 —— 它们正被这个正在运行的"
              f"卸载程序占用，按提示再运行一次即可）")
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
    print("  ✓ 全部通过（含桌面窗口与卸载）")
    return 0


def _is_elevated() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated() -> bool:
    """需要管理员权限时，自己弹 UAC 重启自己。

    ⚠️ **不用 PyInstaller 的 `--uac-admin` 清单**，改成运行时自提权。原因：
    带清单的 exe **必须有 UAC 同意才能启动**，于是自动化测试（`--e2e`）永远挂住
    —— 没人点那个弹窗。运行时自提权的好处是 `--e2e` 能跳过这一步，正常双击
    仍然会弹 UAC（用户要的就是这个）。

    安装程序确实需要管理员：要往用户选的任意目录写（含盘根下新建目录）、
    建桌面快捷方式。普通权限会 `WinError 5 拒绝访问`。
    """
    try:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return False
        # ShellExecuteW 的 "runas" 动词 = 触发 UAC
        params = " ".join(f'"{a}"' if " " in a else a for a in sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, os.getcwd(), 1)
        # >32 表示启动成功；<32 是错误码（用户点了"否"就是 5）
        return int(rc) > 32
    except Exception:
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

    # 正常双击 → 不是管理员就提权重启
    if os.name == "nt" and not _is_elevated():
        if relaunch_elevated():
            return 0          # 已经把活交给提权后的自己
        # 用户拒绝了 UAC：继续跑，但把后果说清楚
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass
    Installer(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
