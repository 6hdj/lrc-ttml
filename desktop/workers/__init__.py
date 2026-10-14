"""后台 worker：把服务层接到任务中心上。

**唯一职责**：把一个 ``(kind, title, callable)`` 提交给 ``TaskManager``，
并把服务层的进度回调转成任务中心的进度与日志。
界面只监听任务中心的通知，**不直接调服务层**（否则耗时操作会卡住 Qt 主线程，
而且进度、取消、日志三件事要各写一遍）。

⚠️ 这里**不导入 Qt**。界面通过 ``task_manager.add_listener`` 收通知。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from desktop.services import backend_service, model_manager
from desktop.services.task_manager import TaskCancelled, TaskHandle, TaskManager


class Workers:
    """任务中心 + 服务层的胶水。"""

    def __init__(self, mgr: Optional[TaskManager] = None,
                 mm: Optional[model_manager.ModelManager] = None):
        self.tasks = mgr or TaskManager()
        self.models = mm or model_manager.ModelManager()

    # ------------------------------------------------------------ 歌词转换
    def start_convert(self, params: Dict[str, Any],
                      output_dir: Optional[str] = None,
                      title: str = "") -> str:
        """起一次转换任务，返回任务 id。"""
        name = title or _short(params.get("audio") or params.get("title") or "转换")

        def _job(h: TaskHandle):
            out: Dict[str, Any] = {}

            def _progress(pct: Optional[float], msg: str = "") -> None:
                h.progress(pct, msg)

            res = backend_service.convert(
                params,
                log=h.log,
                progress=_progress,
                cancelled=lambda: h.cancelled,
                output_dir=output_dir,
                task_id=h.id,
            )
            out.update(res)
            if not res.get("ok"):
                kind = res.get("error_kind")
                if kind == "cancelled":
                    raise TaskCancelled()
                raise ConversionFailed(res.get("error") or "转换失败", kind or "unknown")
            # 产物路径、摘要写进任务结果（界面"打开文件/打开编辑台"要用）
            h.set_result(
                output_path=res.get("path", ""),
                summary=res.get("summary", {}),
                match_rate=(res.get("summary") or {}).get("match_rate", 0),
                lines=(res.get("summary") or {}).get("lines", 0),
                words=(res.get("summary") or {}).get("words", 0),
                format_ok=(res.get("summary") or {}).get("format_ok", False),
                elapsed=res.get("elapsed", 0),
            )
            return {"output_path": res.get("path", "")}

        return self.tasks.submit("convert", name, _job)

    # ------------------------------------------------------------ 运行时组件
    def start_runtime_download(self, rid: str) -> str:
        """下载并安装一个**运行组件**（GPU/CUDA、PyTorch）。

        ⚠️ 与 `start_model_download` 分开，不要图省事复用：
        两者虽然都是"下载 + 校验 + 落盘"，但
          * 清单不同（`runtime_manifest.json` vs `model_manifest.json`）；
          * 校验方式不同（CUDA 是几个 DLL，**没有模块可 import**，
            所以走文件校验；torch 走 import + 版本前缀）；
          * 安装位置不同（`<data_dir>/runtime/<id>` vs 模型缓存）。
        复用会逼出一个满屏 if 的函数。

        这里同样遵循"**认不出的 id 不抛异常**"：要变成失败的任务，
        界面照常显示原因，而不是崩在按钮回调里。
        """
        try:
            from desktop.services import runtime_service
            spec = runtime_service.RuntimeManager().spec(rid)
            name = f"下载 {spec.get('name', rid)}"
        except Exception as e:                            # noqa: BLE001
            def _bad(h: TaskHandle):
                raise RuntimeError(str(e))
            return self.tasks.submit("runtime_download", f"下载 {rid}", _bad)

        def _job(h: TaskHandle):
            from desktop.services import runtime_service
            h.progress(None, "准备中")
            st = runtime_service.RuntimeManager().download(
                rid,
                log=h.log,
                progress=lambda pct, msg: h.progress(pct, msg),
                cancel=lambda: h.cancelled,
            )
            h.set_result(runtime_id=rid, status=st.status, detail=st.detail)
            return {"runtime_id": rid, "status": st.status}

        return self.tasks.submit("runtime_download", name, _job)

    # ------------------------------------------------------------ 模型下载
    def start_model_download(self, model_id: str) -> str:
        # ⚠️ 认不出来的 id **不能在这里抛异常**：那样调用方（界面）会直接崩在
        # 按钮回调里，用户什么都看不到。要变成一个"失败的任务"，
        # 界面照常显示红色 + 原因。
        try:
            name = f"下载 {self.models.spec(model_id).get('name', model_id)}"
        except KeyError as e:
            model_id = str(model_id)
            name = f"下载 {model_id}"

            def _bad(h: TaskHandle):
                raise RuntimeError(str(e))

            return self.tasks.submit("model_download", name, _bad)

        def _job(h: TaskHandle):
            h.progress(None, "准备中")
            st = self.models.download(
                model_id,
                log=h.log,
                progress=lambda pct, msg: h.progress(pct, msg),
                cancel=lambda: h.cancelled,
            )
            h.set_result(model_id=model_id, status=st.status,
                         location=st.location, detail=st.detail)
            return {"model_id": model_id, "status": st.status}

        return self.tasks.submit("model_download", name, _job)

    def start_model_download_many(self, ids, title: str = "") -> str:
        """依次下载多个模型（一个任务里串行，日志连在一起更好读）。"""
        ids = [i for i in ids if i]
        label = title or f"下载 {len(ids)} 个模型"

        def _job(h: TaskHandle):
            done, failed = [], []
            for n, mid in enumerate(ids, 1):
                h.raise_if_cancelled()
                h.stage(f"[{n}/{len(ids)}] {mid}")
                try:
                    st = self.models.download(
                        mid, log=h.log,
                        progress=lambda pct, msg, n=n: h.progress(
                            ((n - 1) * 100.0 / len(ids) + (pct or 0) / len(ids)) if pct is not None else None,
                            msg),
                        cancel=lambda: h.cancelled)
                    done.append(mid)
                    h.log(f"[OK] {st.detail}")
                except model_manager.DownloadCancelled:
                    raise
                except Exception as e:                  # noqa: BLE001
                    failed.append((mid, f"{type(e).__name__}: {e}"))
                    h.log(f"[失败] {mid}：{type(e).__name__}: {e}")
            h.set_result(models_done=done, models_failed=[f[0] for f in failed])
            if failed and not done:
                raise RuntimeError("；".join(f"{m}：{e}" for m, e in failed))
            if failed:
                h.log(f"⚠ {len(failed)} 个没下成，其余已就绪")
            return {"done": done, "failed": [f[0] for f in failed]}

        return self.tasks.submit("model_download", label, _job)

    # ------------------------------------------------------------ 环境体检
    def start_env_check(self, full: bool = True) -> str:
        from desktop.services import environment_service as es
        title = "环境体检（含显卡）" if full else "环境体检"

        def _job(h: TaskHandle):
            h.progress(None, "检查中…")
            items = es.full_check(self.models) if full else es.quick_check()
            rep = es.summarize(items)
            for it in items:
                mark = {"ok": "✓", "warn": "!", "error": "✗", "info": "·"}.get(
                    it["level"], "·")
                h.log(f"  [{mark}] {it['name']}：{it['detail']}"
                      + (f"　→ {it['fix']}" if it["fix"] else ""))
            h.set_result(env=rep)
            h.log(rep["headline"])
            return rep

        return self.tasks.submit("env_check", title, _job)

    # ------------------------------------------------------------ TTML 校验
    def start_check_ttml(self, path: str, strict_time: bool = False) -> str:
        import os
        name = f"校验 {os.path.basename(path)}"

        def _job(h: TaskHandle):
            h.progress(None, "校验中…")
            r = backend_service.check_ttml(path, strict_time=strict_time)
            for e in r["errors"][:20]:
                h.log(f"  ✗ {e.get('where', '')} {e.get('detail', e)}")
            for w in r["warnings"][:20]:
                h.log(f"  ! {w.get('where', '')} {w.get('detail', w)}")
            h.set_result(ttml_check=r)
            if not r["ok"]:
                raise RuntimeError(f"{len(r['errors'])} 个错误")
            return r

        return self.tasks.submit("check_ttml", name, _job)

    # ------------------------------------------------------------ 清理模型
    def start_model_cleanup(self, model_id: str) -> str:
        try:
            name = self.models.spec(model_id).get("name", model_id)
        except KeyError:
            name = model_id

        def _job(h: TaskHandle):
            st = self.models.cleanup(model_id, log=h.log)
            h.set_result(model_id=model_id, status=st.status, detail=st.detail)
            return {"model_id": model_id, "status": st.status}

        return self.tasks.submit("model_cleanup", f"清理 {name}", _job)


class ConversionFailed(Exception):
    """转换失败（带分类，界面照分类给建议）。"""

    def __init__(self, message: str, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


def _short(path: str, n: int = 42) -> str:
    import os
    base = os.path.basename(str(path))
    return base if len(base) <= n else base[: n - 1] + "…"
