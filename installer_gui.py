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
import subprocess
import sys
import threading
import tkinter as tk
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


def ask_app_report() -> dict:
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
    exe = os.path.join(BASE, "Lyric2TTML.exe")
    if not os.path.exists(exe):
        return {}
    try:
        r = subprocess.run([exe, "--doctor", "--json"], cwd=BASE,
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


class Installer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.items: list = []          # [(键, 名称, 补全函数)]
        self.busy = False
        self.model = "large-v3"        # 补全时要下的 Whisper 模型（跟 exe 默认一致）

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
        self.root.after(300, self.start_check)

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
                    else:
                        self.btn_go.configure(command=self.start_install)
        except queue.Empty:
            pass
        # ⚠️ 这里**不要**再 after 一次：定时器由 _pump 统一续，
        # 两处都调会让每一轮多排一个定时器（指数增长，很快就卡死）。

    # ------------------------------------------------------------ 检查
    def start_check(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.btn_go.configure(state="disabled")
        self._scroll_top()          # 重新检查时清单滚回顶部，别停在上一轮的位置
        self.set_sub("正在检查你的电脑…")
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
            rep = ask_app_report()
            if rep and rep.get("items"):
                import types
                rows = [types.SimpleNamespace(**d) for d in rep["items"]]
                print("[检查] 用主程序 Lyric2TTML.exe --doctor --json 的报告",
                      flush=True)
            else:
                # ② 没有主程序（源码模式）才在进程内自检
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
                        self.items.append((it.key, it.name,
                                           actions.get(it.key, it.action)))

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
            self.set_sub("安装完成，可以使用了", OK)
            self.say("\n" + "=" * 46)
            self.say("  ✅ 安装完成！点下面的「▶ 启动软件」开始使用。")
            self.q.put(("done", "normal", "▶ 启动软件", "launch"))
        self.q.put(("quitbtn", None))
        self.root.after(0, lambda: self.btn_quit.configure(state="normal"))


def main() -> int:
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
