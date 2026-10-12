"""后台任务的统一任务中心。

桌面软件不能"点一下卡住、只有一个转圈"。所有耗时操作
（歌词转换、模型下载、环境体检、TTML 校验）都走这里：

* **后台线程执行** —— 界面永远可响应（Qt 主线程只收通知）。
* **可取消** —— 每个任务一个 ``threading.Event``，worker 用 ``handle.cancelled``
  查；取消是"合作式"的，worker 不查就停不下来（见下面 ⚠️）。
* **可持久化** —— 任务列表写 ``<数据目录>/state/tasks.json``，重启后还能看到
  刚才那首歌跑到哪一步了。
* **原子落盘** —— 先写临时文件再 ``os.replace``，"保存一半断电"不会留下坏 JSON。

⚠️ **取消的边界（不要假装做得到）**：``LyricToTTML.convert()`` 内部（ASR 推理、
DP 对齐、声纹）没有取消检查点，所以点"取消"**不会**立刻掐断正在跑的 GPU 推理。
本层只能保证：
  * 排队中（还没开始）的任务**立即取消**；
  * 正在跑的**阶段之间**取消（worker 自己检查）；
  * 界面立刻把状态切成"取消中…"，不假装已经停了。
（``pipeline.py`` 的取消检查点是后续工作，不在这一版里动核心算法。）

本模块**不导入 Qt**，可以在无图形环境下完整测试。
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# 任务状态
QUEUED = "queued"
RUNNING = "running"
VERIFYING = "verifying"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = (DONE, FAILED, CANCELLED)

MAX_LOGS = 500
MAX_KEPT = 60           # 最多保留多少条历史任务


def _now() -> float:
    return time.time()


def _fmt_ts(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else ""


@dataclass
class Task:
    id: str
    kind: str
    title: str
    state: str = QUEUED
    progress: Optional[float] = None        # 0~100；None = 进度未知
    message: str = "排队中"
    error: Optional[str] = None
    result: Dict[str, Any] = field(default_factory=dict)
    created: float = field(default_factory=_now)
    started: Optional[float] = None
    ended: Optional[float] = None
    cancel_requested: bool = False
    logs: List[dict] = field(default_factory=list)

    # ------------------------------------------------------------- 展示用
    def elapsed(self) -> float:
        end = self.ended or _now()
        return max(0.0, end - (self.started or self.created))

    def public(self, with_logs: bool = True) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "created": self.created,
            "started": self.started,
            "ended": self.ended,
            "elapsed": round(self.elapsed(), 1),
            "cancel_requested": self.cancel_requested,
            "running": self.state in (QUEUED, RUNNING, VERIFYING),
            "terminal": self.state in TERMINAL,
            "log_count": len(self.logs),
            "rev": self.rev(),
        }
        if with_logs:
            d["logs"] = list(self.logs)
        return d

    def rev(self) -> str:
        """一个**内容指纹**：变了就说明这个任务有更新。

        UI 靠比较 ``rev`` 判断"要不要重画"。
        ⚠️ 不要拿"进度百分比"当变化判据：同一个百分比可能对应
        完全不同的阶段文字，也会漏掉"只在日志里多了一行"的情况。
        """
        return (f"{self.state}|{self.progress}|{len(self.logs)}|"
                f"{self.message}|{self.ended or 0}")

    def to_json(self) -> dict:
        d = self.public()
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Task":
        t = cls(id=d.get("id") or uuid.uuid4().hex[:10],
                kind=d.get("kind") or "unknown",
                title=d.get("title") or "")
        t.state = d.get("state") or QUEUED
        t.progress = d.get("progress")
        t.message = d.get("message") or ""
        t.error = d.get("error")
        t.result = d.get("result") or {}
        t.created = float(d.get("created") or _now())
        t.started = d.get("started")
        t.ended = d.get("ended")
        t.logs = list(d.get("logs") or [])
        # 重启后不可能还有线程在跑：把"进行中"收成"已中断"，别让它永远转圈
        if t.state in (QUEUED, RUNNING, VERIFYING):
            t.state = FAILED
            t.error = t.error or "程序退出，任务已中断"
            t.message = "已中断"
            t.ended = t.ended or _now()
        return t


class TaskHandle:
    """交给 worker 的句柄。worker 只通过它汇报，不碰 Task 内部。"""

    def __init__(self, manager: "TaskManager", task: Task):
        self._m = manager
        self._t = task
        self.cancel_event = threading.Event()

    # -------------------------------------------------------------- 只读
    @property
    def id(self) -> str:
        return self._t.id

    @property
    def cancelled(self) -> bool:
        """worker 在**阶段之间**查这个。"""
        return self.cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled()

    # -------------------------------------------------------------- 汇报
    def log(self, msg: str) -> None:
        self._m._add_log(self._t, str(msg))

    def progress(self, pct: Optional[float], message: str = "") -> None:
        self._m._set_progress(self._t, pct, message)
        if message:
            # 进度说明也要进日志：用户事后翻日志要知道"卡在哪一步"，
            # 而不是只看到一堆百分比
            self._m._add_log(self._t, message)

    def stage(self, message: str) -> None:
        self._m._set_progress(self._t, self._t.progress, message)

    def verifying(self, message: str = "校验中") -> None:
        self._m._set_state(self._t, VERIFYING, message)

    def set_result(self, **kw) -> None:
        self._t.result.update(kw)
        self._m._persist()

    def write_atomic(self, path: str, text: str) -> str:
        """先写临时文件再原子替换 —— 失败时**不覆盖已有的有效产物**。"""
        return atomic_write_text(path, text)


class TaskCancelled(Exception):
    """worker 主动抛出来表示"是用户取消的"，不算失败。"""


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> str:
    """原子写文本：同目录临时文件 + ``os.replace``。"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)
    return path


class TaskManager:
    """任务中心。线程安全；不依赖 Qt。"""

    def __init__(self, state_path: Optional[str] = None,
                 persist: bool = True,
                 log_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._tasks: Dict[str, Task] = {}
        self._order: List[str] = []
        self._handles: Dict[str, TaskHandle] = {}
        self._listeners: List[Callable[[dict], None]] = []
        self._persist_enabled = persist
        self._state_path = state_path
        self._last_flush = 0.0
        self._dirty = False
        self._log_path = log_path
        if persist and state_path and os.path.isfile(state_path):
            self._load()

    # ------------------------------------------------------------ 监听器
    def add_listener(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """注册变更回调。

        ⚠️⚠️ **回调是在 worker 线程里被调用的**（任务就跑在那里）。
        所以：
          * 回调里**绝不能碰 Qt 控件** —— 跨线程改 UI 会让 Qt 直接崩
            （实测表现为 ``QObject: Cannot create children for a parent that
            is in a different thread``，然后进程 0xC0000005 退出）。
          * 回调要快，别在里面做 IO 或重活。
        需要更新界面时，请**轮询** ``list()`` 并比较每个任务的 ``rev``
        （``MainWindow._tick`` 就是这么做的），或者用一个队列把事件
        交给主线程。本项目的选择是轮询：简单、不会漏、不会崩。
        """
        with self._lock:
            self._listeners.append(fn)

        def _off():
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)
        return _off

    def _notify(self, task: Task) -> None:
        payload = task.public()
        for fn in list(self._listeners):
            try:
                fn(payload)
            except Exception:
                # 一个监听器出错不能影响任务本身
                traceback.print_exc()

    # ------------------------------------------------------------ 提交/取消
    def submit(self, kind: str, title: str, fn: Callable[[TaskHandle], Any],
               task_id: Optional[str] = None) -> str:
        """起一个后台任务。``fn(handle)`` 在子线程里跑。

        返回值是任务 id。
        """
        t = Task(id=task_id or uuid.uuid4().hex[:10], kind=kind, title=title)
        with self._lock:
            self._tasks[t.id] = t
            self._order.append(t.id)
            self._handles[t.id] = TaskHandle(self, t)
            self._trim()
        self._persist(force=True)
        self._notify(t)

        def _run():
            h = self._handles.get(t.id)
            if h is None:                      # 提交后立刻被取消并清理
                return
            if h.cancel_event.is_set():
                self._finish(t, CANCELLED, "已取消")
                return
            with self._lock:
                t.started = _now()
                t.state = RUNNING
                t.message = "开始"
            self._persist(force=True)
            self._notify(t)
            try:
                out = fn(h)
                if h.cancelled:
                    # ⚠️ 用户点了取消、worker 又"正常跑完"了 —— 状态必须是取消，
                    # 不能因为返回值正常就报"完成"（不然用户看到"完成"却知道
                    # 自己取消过，只会以为软件没听他的）。
                    self._finish(t, CANCELLED, "已取消")
                    return
                self._finish(t, DONE, t.message or "完成", result=out)
            except TaskCancelled:
                self._finish(t, CANCELLED, "已取消")
            except Exception as e:
                # worker 可以用"带 cancel 属性的异常"表示"这是取消，不是失败"。
                # 分开的意义：失败会弹红色错误 + 建议，取消是用户自己按的，
                # 再报一堆红只会让人以为出故障了。
                if getattr(e, "cancel", False) or h.cancelled:
                    self._finish(t, CANCELLED, "已取消", error=str(e) or None)
                    return
                tb = traceback.format_exc()
                self._add_log(t, tb.strip().splitlines()[-1] if tb else str(e))
                self._finish(t, FAILED, f"{type(e).__name__}: {e}",
                             error=f"{type(e).__name__}: {e}")

        threading.Thread(target=_run, name=f"l2t-task-{t.id}", daemon=True).start()
        return t.id

    def cancel(self, task_id: str) -> bool:
        """请求取消。排队中的任务会立刻转成"已取消"。"""
        with self._lock:
            t = self._tasks.get(task_id)
            h = self._handles.get(task_id)
            if t is None or t.state in TERMINAL:
                return False
            t.cancel_requested = True
            if h:
                h.cancel_event.set()
            if t.state == QUEUED:
                t.state = CANCELLED
                t.message = "已取消"
                t.ended = _now()
        if t.state == CANCELLED:
            self._persist(force=True)
            self._notify(t)
        else:
            self._set_state(t, t.state, "取消中…（当前阶段跑完才停）")
        return True

    def cancel_all(self) -> int:
        n = 0
        for tid in list(self._order):
            if self.cancel(tid):
                n += 1
        return n

    def has_running(self) -> bool:
        with self._lock:
            return any(t.state in (QUEUED, RUNNING, VERIFYING) for t in self._tasks.values())

    # ------------------------------------------------------------ 查询
    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def handle(self, task_id: str) -> Optional[TaskHandle]:
        with self._lock:
            return self._handles.get(task_id)

    def list(self, with_logs: bool = False) -> List[dict]:
        with self._lock:
            ids = list(reversed(self._order))
            return [self._tasks[i].public(with_logs=with_logs)
                    for i in ids if i in self._tasks]

    def remove_finished(self) -> int:
        """清掉所有**已结束**的任务，返回清掉的条数。

        "进行中"的任务绝不动 —— 界面上的"清掉已完成"按钮走这里。
        """
        with self._lock:
            keep = [i for i in self._order
                    if i in self._tasks and self._tasks[i].state not in TERMINAL]
            removed = len(self._order) - len(keep)
            for i in list(self._order):
                if i not in keep:
                    self._tasks.pop(i, None)
                    self._handles.pop(i, None)
            self._order = keep
        if removed:
            self._persist(force=True)
        return removed

    # ------------------------------------------------------------ 内部
    def _add_log(self, t: Task, msg: str) -> None:
        with self._lock:
            t.logs.append({"t": _fmt_ts(_now()), "m": msg})
            if len(t.logs) > MAX_LOGS:
                t.logs = t.logs[-MAX_LOGS:]
        if self._log_path:
            try:
                with open(self._log_path, "a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{t.kind}:{t.id}] {msg}\n")
            except OSError:
                pass
        self._notify(t)

    def _set_progress(self, t: Task, pct: Optional[float], message: str = "") -> None:
        with self._lock:
            if pct is not None:
                pct = max(0.0, min(100.0, float(pct)))
                t.progress = pct
            if message:
                t.message = message
        self._persist()
        self._notify(t)

    def _set_state(self, t: Task, state: str, message: str = "") -> None:
        with self._lock:
            t.state = state
            if message:
                t.message = message
        self._persist(force=True)
        self._notify(t)

    def _finish(self, t: Task, state: str, message: str,
                error: Optional[str] = None, result: Any = None) -> None:
        with self._lock:
            t.state = state
            t.message = message
            t.error = error
            t.ended = _now()
            if state == DONE:
                t.progress = 100.0
                if isinstance(result, dict):
                    t.result.update(result)
            if isinstance(result, dict) and state != DONE:
                t.result.update(result)
        self._persist(force=True)
        self._notify(t)

    def _trim(self) -> None:
        while len(self._order) > MAX_KEPT:
            old = self._order[0]
            if self._tasks.get(old) and self._tasks[old].state not in TERMINAL:
                break
            self._order.pop(0)
            self._tasks.pop(old, None)
            self._handles.pop(old, None)

    # ------------------------------------------------------------ 落盘
    def _persist(self, force: bool = False) -> None:
        if not self._persist_enabled or not self._state_path:
            return
        now = _now()
        with self._lock:
            if not force and now - self._last_flush < 0.75:
                self._dirty = True
                return
            self._last_flush = now
            self._dirty = False
            data = {"version": 1, "tasks": [self._tasks[i].to_json()
                                           for i in self._order if i in self._tasks]}
        try:
            atomic_write_text(self._state_path,
                              json.dumps(data, ensure_ascii=False, indent=1))
        except OSError:
            pass

    def _load(self) -> None:
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        for d in (data.get("tasks") or []):
            try:
                t = Task.from_json(d)
            except Exception:
                continue
            self._tasks[t.id] = t
            self._order.append(t.id)

    def flush(self) -> None:
        """把攒着的变更立刻写盘（退出前调）。"""
        with self._lock:
            need = self._dirty
        if need:
            self._persist(force=True)
