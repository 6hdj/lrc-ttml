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
import sys
import threading
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

FROZEN = getattr(sys, "frozen", False)
BASE = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE

# 复用 install.py 的检查逻辑，避免两套判定漂移
import install as core  # noqa: E402

BG = "#0f1117"
FG = "#e8e8f0"
DIM = "#8b95ad"
OK = "#3ddc84"
BAD = "#ff6b6b"
WARN = "#ffc857"
ACCENT = "#4aa8ff"


class Installer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.items: list = []          # [(键, 名称, 说明, 状态, 补全函数)]
        self.busy = False

        root.title("Lyric2TTML 安装程序")
        root.configure(bg=BG)
        root.geometry("760x560")
        root.minsize(680, 500)
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

        # 清单
        box = tk.Frame(self.root, bg="#161a24", highlightthickness=1,
                       highlightbackground="#252b3a")
        box.pack(fill="both", expand=False, padx=28, pady=(4, 10))
        self.rows: dict = {}
        for key, name, why in (
            ("python", "Python 运行环境", "跑程序用（打包版自带）"),
            ("pkgs", "Python 依赖库", "语音识别 / 音频处理"),
            ("whisper", "语音识别模型", "约 3 GB，只需下载一次"),
            ("cuda", "CUDA 运行库", "约 2 GB，显卡加速必需"),
            ("campp", "声纹分声部模型", "约 300 MB，对唱分声部必需"),
            ("ffmpeg", "ffmpeg", "可选，个别音频格式需要"),
        ):
            r = tk.Frame(box, bg="#161a24")
            r.pack(fill="x", padx=14, pady=7)
            icon = tk.Label(r, text="•", bg="#161a24", fg=DIM, width=3,
                            font=("Segoe UI Symbol", 13))
            icon.pack(side="left")
            tk.Label(r, text=name, bg="#161a24", fg=FG, width=18, anchor="w",
                     font=("Microsoft YaHei UI", 11)).pack(side="left")
            det = tk.Label(r, text=why, bg="#161a24", fg=DIM, anchor="w",
                           font=("Microsoft YaHei UI", 9))
            det.pack(side="left", fill="x", expand=True)
            self.rows[key] = (icon, det)

        # 日志
        self.log = tk.Text(self.root, height=9, bg="#0b0e14", fg="#c8d2e0",
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

    def _pump(self) -> None:
        """主线程里消费后台线程发来的消息（tkinter 只能主线程碰控件）"""
        try:
            while True:
                m = self.q.get_nowait()
                kind = m[0]
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", m[1] + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "row":
                    _, key, state, detail = m
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
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

    # ------------------------------------------------------------ 检查
    def start_check(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.btn_go.configure(state="disabled")
        self.set_sub("正在检查你的电脑…")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self) -> None:
        try:
            self.items = []

            # 1. Python
            v = sys.version_info
            if FROZEN:
                self.set_row("python", "ok", f"打包版自带 {v.major}.{v.minor}")
                self.say(f"[OK] Python（打包版内置 {v.major}.{v.minor}.{v.micro}）")
            elif (v.major, v.minor) >= (3, 10):
                self.set_row("python", "ok", f"{v.major}.{v.minor}.{v.micro}")
                self.say(f"[OK] Python {v.major}.{v.minor}.{v.micro}")
            else:
                self.set_row("python", "bad", "需要 3.10 或更高")
                self.say("[缺] Python 版本过低。请到 python.org 装 3.10+，"
                         "安装时勾选 Add Python to PATH")
                self.items.append(("python", "Python 3.10+", None))

            # 2. 依赖
            miss = [] if FROZEN else core.missing_pkgs()
            if miss:
                self.set_row("pkgs", "bad", "、".join(miss))
                self.say(f"[缺] 依赖库：{'、'.join(miss)}")
                self.items.append(("pkgs", "Python 依赖库",
                                   lambda: core.install_pkgs(miss)))
            else:
                self.set_row("pkgs", "ok", "齐全")
                self.say("[OK] 依赖库齐全")

            # 3. Whisper 模型
            wm = core.whisper_models()
            if wm:
                best = max(wm, key=lambda k: wm[k])
                self.set_row("whisper", "ok",
                             f"{best}（{wm[best] / 1024 / 1024:.0f} MB）")
                self.say(f"[OK] 语音识别模型：{'、'.join(wm)}")
            else:
                self.set_row("whisper", "bad", "未下载")
                self.say("[缺] 语音识别模型（约 3 GB）")
                self.items.append(("whisper", "语音识别模型",
                                   lambda: core.fetch_whisper("large-v3")))

            # 4. CUDA
            if core.cuda_dlls_present():
                self.set_row("cuda", "ok", "已就位")
                self.say("[OK] CUDA 运行库已就位")
            else:
                self.set_row("cuda", "bad", "未安装（显卡加速需要）")
                self.say("[缺] CUDA 运行库（cublas/cudnn，约 2 GB）")
                self.items.append(("cuda", "CUDA 运行库", core.fetch_cuda))

            # 5. CAM++
            if core.campp_present():
                self.set_row("campp", "ok", "已就位")
                self.say("[OK] 声纹分声部模型已就位")
            else:
                self.set_row("campp", "bad", "未安装（对唱分声部需要）")
                self.say("[缺] 声纹分声部模型（约 300 MB）")
                self.items.append(("campp", "声纹分声部模型", core.fetch_campp))

            # 6. ffmpeg（可选）
            ff = core.ffmpeg_present()
            self.set_row("ffmpeg", "ok" if ff else "skip",
                         ff or "未安装（可选）")
            self.say(("[OK] ffmpeg：" + ff) if ff else
                     "[跳过] 没装 ffmpeg —— 可选，个别格式解不开时再装")

            n = len(self.items)
            if n == 0:
                self.set_sub("全部就绪，可以直接使用", OK)
                self.say("\n✅ 什么都不缺。点「退出」，然后双击 启动.bat 就行。")
                self.q.put(("done", "disabled", "全部就绪"))
            else:
                self.set_sub(f"发现 {n} 项需要补全", BAD)
                self.say(f"\n共 {n} 项需要补全，点下面的「开始补全」继续。")
                self.q.put(("done", "normal", f"开始补全（{n} 项）"))
        except Exception as e:
            self.say(f"[错误] 检查时出问题：{type(e).__name__}: {e}")
            self.set_sub("检查失败", BAD)
            self.q.put(("done", "normal", "重试"))

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
            self.set_sub("全部就绪，可以使用了", OK)
            self.say("\n" + "=" * 46)
            self.say("  安装完成！关掉本窗口，双击 启动.bat 开始使用。")
            self.q.put(("done", "normal", "重新检查"))
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
