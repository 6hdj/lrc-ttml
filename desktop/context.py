"""界面与服务层之间的**唯一接缝**。

为什么要有这一层：页面直接 new 服务对象的话，
"配置文件在哪、任务中心谁持有、模型管理器几个实例"会散落到每个页面里，
最后出现两个 ``TaskManager``（一个收任务、一个收通知）这种鬼问题。

``AppContext`` 持有全部单例，并对外只暴露"页面会用到的那几件事"：
起任务、读模型状态、读环境结论、打开文件/目录。

⚠️ 这个模块**导入 Qt 只为了"打开文件/目录"和"弹提示"**；
核心逻辑都在服务层，所以 ``desktop/selftest.py`` 不需要 Qt 也能验服务层。
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

from desktop import __version__, config as config_mod, fileguess, paths
from desktop.services import environment_service, model_manager
from desktop.services.task_manager import TaskManager
from desktop.workers import Workers


class AppContext:
    def __init__(self, mgr: Optional[TaskManager] = None):
        self.config: config_mod.Config = config_mod.current()
        self.models = model_manager.ModelManager()
        self.tasks = mgr or TaskManager(state_path=paths.state_path("tasks.json"),
                                        log_path=paths.log_path())
        self.workers = Workers(self.tasks, self.models)
        self.env_items: List[Dict[str, Any]] = []
        self.env_summary: Dict[str, Any] = {}
        self._listeners: List[Callable[[dict], None]] = []

    # ------------------------------------------------------------ 通知
    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """订阅任务变更。

        ⚠️⚠️ **回调在 worker 线程里跑**，里面**不能碰 Qt 控件**。
        界面要更新请用 ``drain_changes()``（在主线程定时调），
        它只会把"有新变化的那些任务"交出来。
        """
        self._listeners.append(fn)
        return self.tasks.add_listener(fn)

    def drain_changes(self, reset: bool = False) -> List[Dict[str, Any]]:
        """返回**自上次调用以来有变化**的任务（内容指纹 ``rev`` 变了）。

        这是界面唯一该用的变更来源：
        * 在主线程里调 → 所有 Qt 操作都在主线程，不会崩；
        * 用 ``rev`` 去重 → 不会因为 1.5 秒轮询把同一份内容重画很多遍；
        * 不会漏 → 哪怕某次通知丢了，轮询也会把它捞回来。

        ``reset=True`` 用于"清空历史"之后重新建立基线。
        """
        if reset:
            self._revs = {}
            return []
        revs = getattr(self, "_revs", None)
        if revs is None:
            revs = self._revs = {}
        out = []
        seen = set()
        for t in self.tasks.list(with_logs=True):
            tid = t["id"]
            seen.add(tid)
            rev = t.get("rev") or f"{t.get('state')}|{t.get('log_count')}"
            if revs.get(tid) != rev:
                revs[tid] = rev
                out.append(t)
        for gone in list(revs):
            if gone not in seen:
                revs.pop(gone, None)
        return out

    def running_count(self) -> int:
        return sum(1 for t in self.tasks.list(with_logs=False) if t.get("running"))

    # ------------------------------------------------------------ 转换
    def start_convert(self, params: Dict[str, Any], title: str = "") -> str:
        """起一次转换。输出目录取设置里的工作目录。"""
        out_dir = self.config.outputs_dir()
        return self.workers.start_convert(params, output_dir=out_dir, title=title)

    def convert_params(self, **overrides) -> Dict[str, Any]:
        return self.config.to_convert_params(**overrides)

    # ------------------------------------------------------------ 模型
    def model_states(self) -> List[Dict[str, Any]]:
        return [s.public() for s in self.models.check_all()]

    def model_state(self, model_id: str) -> Dict[str, Any]:
        return self.models.check(model_id).public()

    def default_asr_model(self) -> str:
        """默认用的 ASR 模型 id（优先用配置里选的，其次清单里的 default）。"""
        want = str(self.config.get("asr_model") or "")
        ids = [s["id"] for s in self.models.all_specs()
               if s.get("category") == "asr"]
        if want in ids:
            return want
        for s in self.models.all_specs():
            if s.get("category") == "asr" and s.get("default"):
                return s["id"]
        return ids[0] if ids else "whisper-large-v3"

    def asr_ready(self) -> bool:
        st = self.models.check(self.default_asr_model())
        return st.status == model_manager.READY

    # ------------------------------------------------------------ 环境
    def env_check(self, full: bool = False) -> List[Dict[str, Any]]:
        items = environment_service.full_check(self.models) if full \
            else environment_service.quick_check()
        self.env_items = items
        self.env_summary = environment_service.summarize(items)
        return items

    # ------------------------------------------------------------ 文件
    def open_path(self, path: str) -> None:
        """用系统默认程序打开文件或目录。"""
        if not path:
            return
        path = os.path.abspath(path)
        try:
            if os.name == "nt":
                os.startfile(path)                       # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError:
            pass

    def reveal(self, path: str) -> None:
        """在资源管理器里**选中**这个文件（比打开文件本身更常用）。"""
        path = os.path.abspath(path or "")
        if not path:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", path])
            else:
                self.open_path(os.path.dirname(path))
        except OSError:
            pass

    def guess_metadata(self, audio: str, lyrics: str) -> Dict[str, Any]:
        return fileguess.suggest(audio, lyrics)

    # ------------------------------------------------------------ 编辑台
    def editor_url(self, ttml_path: str = "") -> str:
        """编辑台的 URL。

        ⚠️ 编辑台仍然是**浏览器里的那个**（``web_server`` + ``index.html``），
        桌面窗口只是把它叫起来 —— 这样不用引入 QtWebEngine
        （那会让安装包再大几百 MB），第一版没必要。

        服务是**懒启动**的：没人点编辑台就不占端口。
        """
        import socket
        port = int(self.config.get("web_port") or 8765)
        if port in (0, None):
            port = 8765
        if not _port_open(port):
            self._ensure_web_server(port)
        base = f"http://127.0.0.1:{port}/"
        if ttml_path:
            # web_server 的编辑态是按 task id 存的：<outputs>/<id>.ttml
            tid = os.path.splitext(os.path.basename(ttml_path))[0]
            base += f"#edit={tid}"
        return base

    def _ensure_web_server(self, port: int) -> bool:
        """在后台线程里起本地 Web 服务（只起一次）。"""
        if getattr(self, "_web_started", False):
            return True
        self._web_started = True

        def _run():
            try:
                import web_server
                web_server.UPLOAD_DIR = self.config.uploads_dir()
                web_server.OUTPUT_DIR = self.config.outputs_dir()
                paths.ensure_dir(web_server.UPLOAD_DIR)
                paths.ensure_dir(web_server.OUTPUT_DIR)
                os.environ["L2T_PORT"] = str(port)
                os.environ["L2T_NO_BROWSER"] = "1"
                web_server.main()
            except Exception:                            # noqa: BLE001
                from desktop.services import log_service
                log_service.get().exception("编辑台服务启动失败")

        import threading
        threading.Thread(target=_run, name="l2t-web", daemon=True).start()
        for _ in range(40):                              # 最多等 4 秒
            if _port_open(port):
                return True
            import time
            time.sleep(0.1)
        return False

    def shutdown(self) -> None:
        """退出前把该停的停掉。

        ⚠️ 正在跑的任务**不强制杀**（GPU 推理中途掐掉会留下半截产物），
        但要求它们在自己的阶段边界停下；状态落盘，下次打开能看到"已中断"。
        """
        try:
            self.tasks.cancel_all()
        except Exception:                                # noqa: BLE001
            pass
        try:
            self.tasks.flush()
        except Exception:                                # noqa: BLE001
            pass
        try:
            self.config.save()
        except Exception:                                # noqa: BLE001
            pass

    @property
    def version(self) -> str:
        return __version__


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    """端口上有没有人在听（用来判断"编辑台服务起没起来"）。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, int(port))) == 0
