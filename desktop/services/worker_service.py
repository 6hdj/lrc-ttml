"""Demucs Worker 可选运行组件：清单、安装状态机、发现与调用。

**主程序永不 import Worker 内部的 torch / demucs** —— 本模块只用 `subprocess`
调 `DemucsWorker.exe`，并用**独立 JSON 文件**读结果（不解析 stdout）。

## 状态（用户要求至少这些）

    not_installed   未安装
    downloading     下载中
    paused          下载暂停
    failed          下载失败（保留 .part，可重试/续传）
    verifying       校验中（SHA256）
    installing      安装中（解压 + 完整性 + 自检）
    selftest_failed 自检失败（torch/CUDA 不可用）
    installed       已安装
    incompatible    版本不兼容（与清单要求不符）

## 安装顺序（失败/取消/损坏都不得标记为已安装，也不得破坏旧版本）

    下载全部组件到缓存 → 逐个 SHA256 → 解压到 `<runtime>/demucs-worker.staging-<ts>/`
    → 完整性检查（关键文件在位 + 版本对得上）→ `--selftest`（torch._C/CUDA）
    → **原子切换**：旧目录改名成 `.old-<ts>` → staging 改名成正式名 → 删旧
    任何一步失败：删 staging，**旧版本原地不动**；切换后自检失败同样回滚。

## 远程可更新

清单优先读 `<app_dir>/worker_manifest.json`（用户/运维可单独替换、无需重发主程序），
找不到才回退到内置 `desktop/resources/worker_manifest.json`。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time

from desktop import paths
from desktop.services.downloader import (DownloadError, download_from_sources)

# ---- 状态常量 ----
NOT_INSTALLED = "not_installed"
DOWNLOADING = "downloading"
PAUSED = "paused"
FAILED = "failed"
VERIFYING = "verifying"
INSTALLING = "installing"
SELFTEST_FAILED = "selftest_failed"
INSTALLED = "installed"
INCOMPATIBLE = "incompatible"

WORKER_DIRNAME = "demucs-worker"
EXE_NAME = "DemucsWorker.exe"
CACHE_DIRNAME = "worker-components"          # 下载缓存（.part 保留，可续传）

# 关键文件：解压后必须存在，否则判定组件损坏（**不是**"解压没报错就算成功"）。
#
# ⚠️⚠️ **别把纯 Python 模块写成 `.pyc` 文件路径**（2026-10-15 实测踩到）：
# PyInstaller 把纯 Python 模块放进 **PYZ 归档**（嵌在 exe 里），磁盘上
# **没有** `_internal/torch/__init__.pyc` 这种文件 —— 我第一版就这么写的，
# 结果 7 个组件全部下载 + SHA256 通过之后，卡在"组件不完整"假失败。
# 这里只列**磁盘上真实存在**的关键件：exe、Python 运行时、原生扩展、核心 DLL。
# Python 侧的完整性交给 `--selftest`（它会真去 `import torch` / `demucs`）。
REQUIRED_FILES = [
    EXE_NAME,
    os.path.join("_internal", "python314.dll"),
    os.path.join("_internal", "torch", "_C.cp314-win_amd64.pyd"),
    os.path.join("_internal", "torch", "lib", "torch_cuda.dll"),
    os.path.join("_internal", "torch", "lib", "torch_cpu.dll"),
    os.path.join("_internal", "torch", "lib", "cublasLt64_13.dll"),
    os.path.join("_internal", "torch", "lib", "cudnn64_9.dll"),
    os.path.join("_internal", "numpy", "_core",
                 "_multiarray_umath.cp314-win_amd64.pyd"),
]


def _bundled_manifest_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "resources", "worker_manifest.json")


def load_manifest(path: str = "") -> dict:
    """读清单。

    ⚠️ **下载地址随程序发布，不做远程更新**（2026-10-15 用户决定）：
    只读内置的 `desktop/resources/worker_manifest.json`，**不再**去程序目录
    找同名文件覆盖。所以镜像地址变化时要**随版本更新**，而不是"远程换清单"。
    `path` 参数只为测试注入用。
    """
    p = path or _bundled_manifest_path()
    if p and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            d["_source"] = p
            return d
        except Exception as e:                               # noqa: BLE001
            return {"schema": 0, "components": [], "worker_version": "",
                    "_source": p, "error": f"清单解析失败：{type(e).__name__}: {e}"}
    return {"schema": 0, "components": [], "worker_version": "",
            "_source": "", "error": "找不到 worker_manifest.json"}


#: 可以被当作"能下"的源状态。`unpublished`（包还没上传）与 `unverified`
#: （上传了但没做大陆真机测速）都不算 —— 用户明确要求"不得把未经验证的
#: 地址写成已支持"，所以这里也不假装能用。
USABLE_SOURCE_STATUS = ("verified",)


def sources_ready(manifest: dict) -> tuple:
    """清单里有没有**可用的**下载源？返回 `(ok, 原因)`。

    没有可用源时**不要开始下载**：那必然是"下到一半才报错"，不如当场说清楚。
    """
    comps = [c for c in (manifest.get("components") or []) if c.get("required", True)]
    if not comps:
        return False, "清单里没有组件"
    usable = 0
    total = 0
    for c in comps:
        for s in (c.get("sources") or []):
            total += 1
            if str(s.get("status") or "") in USABLE_SOURCE_STATUS:
                usable += 1
    if usable == 0:
        return False, (f"下载源尚未发布（{total} 个源都还没上传/未验证）——"
                       f"组件包上传并验证后，随新版本一起生效")
    return True, ""


def worker_root() -> str:
    """正式安装目录：`paths.runtime_root()` + `demucs-worker`。

    **复用** `paths.runtime_root()`（= `data_dir()/runtime`），与现有
    `runtime/torch`、`runtime/cuda` 同级 —— 这样卸载器
    `uninstall_core._preserve_paths()` 对 `runtime` 的"默认保留"策略
    自动覆盖到 Worker，不用另写一套。
    """
    return os.path.join(paths.runtime_root(), WORKER_DIRNAME)


def worker_exe(root: str = "") -> str:
    return os.path.join(root or worker_root(), EXE_NAME)


def discover() -> str:
    """找到可用的 Worker exe；找不到返回空串。

    顺序：① 环境变量 `L2T_DEMUCS_WORKER`（指向 exe 或目录）；
          ② `<app_dir>/runtime/demucs-worker/DemucsWorker.exe`；
          ③ `<app_dir>/demucs-worker/DemucsWorker.exe`（便携布局）。
    """
    env = os.environ.get("L2T_DEMUCS_WORKER", "").strip()
    cands = []
    if env:
        cands.append(env if env.lower().endswith(".exe") else os.path.join(env, EXE_NAME))
    cands.append(worker_exe())
    cands.append(os.path.join(paths.app_dir(), WORKER_DIRNAME, EXE_NAME))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def _clean_env() -> dict:
    """调用 Worker 用的环境：清掉 PYTHONPATH/PYTHONHOME，避免开发环境串进来。"""
    e = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        e.pop(k, None)
    return e


def run_selftest(exe: str, timeout: float = 120) -> dict:
    """跑 Worker 自检，返回解析后的 JSON（失败也给结构化结果）。"""
    if not exe or not os.path.isfile(exe):
        return {"ok": False, "error": "Worker 未安装"}
    try:
        r = subprocess.run([exe, "--selftest"], capture_output=True,
                           timeout=timeout, env=_clean_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"自检超时（>{timeout:.0f}s）"}
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                d["ok"] = bool(d.get("torch_C") == "ok" and d.get("cuda_available"))
                d["exit_code"] = r.returncode
                return d
            except Exception:                                # noqa: BLE001
                continue
    return {"ok": False, "exit_code": r.returncode,
            "error": "自检输出无法解析", "raw": out[-400:]}


class WorkerError(Exception):
    """Worker 调用失败（带分类，供界面显示可理解的错误）。"""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def weights_present(torch_home: str, model: str = "htdemucs") -> bool:
    """Demucs 权重是否就位（`<torch_home>/hub/checkpoints/*.th` 有非 .partial 文件）。

    ⚠️⚠️ 为什么必须有这个预检（2026-10-16 **冻结版端到端**实测踩到）：
    安装包**按设计不含模型权重**；Worker 里 `get_model('htdemucs')` 会在权重缺失时
    **自己联网下载**（torch.hub），而且**没有任何进度提示**。
    实测：冻结版首次分离时它默默下了 **13 分钟只到 57 MB（≈0.07 MB/s）**，
    从用户视角就是"卡死"。⇒ 主程序必须先检查，缺权重就**当场说清楚**
    （去"模型管理"页下载 Demucs 条目，那里有镜像选择），而不是把它丢给 Worker。
    """
    if not torch_home:
        return False
    ck = os.path.join(torch_home, "hub", "checkpoints")
    if not os.path.isdir(ck):
        return False
    for f in os.listdir(ck):
        if f.endswith(".th") and not f.endswith(".partial"):
            try:
                if os.path.getsize(os.path.join(ck, f)) > 1024 * 1024:
                    return True
            except OSError:
                pass
    return False


def separate(audio: str, out: str, *, exe: str = "", torch_home: str = "",
             timeout: float = 1800, stop=None, echo=print,
             require_weights: bool = True) -> dict:
    """调 Worker 做一次人声分离。

    **契约**（用户要求）：
    * 输出先写 `out + ".tmp"`，成功才原子替换到 `out`；
    * 失败/超时/取消 **一律删掉临时文件**，绝不把混音当人声轨；
    * 用**独立 JSON 文件**读结果，不依赖 stdout；
    * CUDA 初始化失败、分离失败、超时、取消都返回明确错误；
    * **不静默回退 CPU**（Worker 自己保证，除非显式 `--device cpu`）。
    """
    exe = exe or discover()
    if not exe:
        raise WorkerError("not_installed", "Demucs 人声分离运行库未安装")
    # ⚠️ **权重预检**：没有权重就先说清楚，别让 Worker 闷头下载（见 weights_present 注释）
    if require_weights and torch_home and not weights_present(torch_home):
        raise WorkerError(
            "no_weights",
            "缺 Demucs 人声分离模型权重（安装包不含模型）：请先到"
            "「模型管理」页下载「Demucs 人声分离」模型，再重试。"
            f"（期望位置：{os.path.join(torch_home, 'hub', 'checkpoints')}）")
    if not os.path.isfile(audio):
        raise WorkerError("bad_input", f"输入音频不存在：{audio}")
    tmp = out + ".tmp.wav"
    jp = out + ".tmp.json"
    for f in (tmp, jp):
        if os.path.isfile(f):
            try:
                os.remove(f)
            except OSError:
                pass
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    cmd = [exe, "--audio", audio, "--out", tmp, "--json", jp, "--device", "cuda"]
    if torch_home:
        cmd += ["--torch-home", torch_home]
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=_clean_env())
    except OSError as e:
        raise WorkerError("spawn_failed", f"启动 Worker 失败：{e}") from e
    # 轮询等待，支持取消
    while p.poll() is None:
        if stop and stop():
            try:
                p.kill()
            except OSError:
                pass
            for f in (tmp, jp):
                _safe_remove(f)
            raise WorkerError("cancelled", "用户取消")
        if time.time() - t0 > timeout:
            try:
                p.kill()
            except OSError:
                pass
            for f in (tmp, jp):
                _safe_remove(f)
            raise WorkerError("timeout", f"分离超时（>{timeout:.0f}s）")
        time.sleep(0.2)
    try:
        p.stdout.close()                                     # type: ignore[union-attr]
    except Exception:                                        # noqa: BLE001
        pass
    meta = {}
    if os.path.isfile(jp):
        try:
            with open(jp, encoding="utf-8") as fh:
                meta = json.load(fh)
        except Exception:                                    # noqa: BLE001
            meta = {}
    ok = (p.returncode == 0 and meta.get("ok") and os.path.isfile(tmp))
    if not ok:
        for f in (tmp, jp):
            _safe_remove(f)
        kind = {3: "cuda_failed", 4: "separate_failed", 5: "invalid_output",
                2: "bad_input", 130: "cancelled"}.get(p.returncode, "failed")
        raise WorkerError(kind, str(meta.get("error") or
                                    f"Worker 退出码 {p.returncode}"))
    os.replace(tmp, out)
    _safe_remove(jp)
    meta["elapsed_wall_s"] = round(time.time() - t0, 2)
    meta["output_path"] = out
    echo(f"[Worker] 分离完成 {meta.get('sep_s', '?')}s "
         f"（{meta.get('device_name', '')}）→ {os.path.basename(out)}")
    return meta


def _safe_remove(p: str) -> None:
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


class WorkerService:
    """安装/升级/卸载 + 状态查询（界面 400ms 轮询可安全调用）。"""

    def __init__(self, manifest: dict | None = None, log=print):
        self.log = log
        self._m = manifest
        self._lock = threading.Lock()
        self._state = ""
        self._detail = ""
        self._progress = 0.0
        self._stop = threading.Event()
        # 并发下载用的共享状态（多线程同时写 ⇒ 必须加锁）
        self._plock = threading.Lock()
        self._pbytes: dict = {}
        self._ptotals: dict = {}
        self._fail_result: dict = {}
        self._cur_src = ""
        self._speed = 0.0
        self._got = 0
        self._total = 0

    # ---------- 清单 ----------
    @property
    def manifest(self) -> dict:
        if self._m is None:
            self._m = load_manifest()
        return self._m

    def component_sources(self, comp: dict) -> list:
        """一个组件的可用下载源（主源 + 备用源）。清单里可远程更新。"""
        srcs = comp.get("sources") or []
        if not srcs and comp.get("url"):
            srcs = [{"url": comp["url"], "label": "主源"}]
        return [s if isinstance(s, dict) else {"url": s} for s in srcs]

    # ---------- 状态 ----------
    def snapshot(self) -> dict:
        """给界面用的状态快照（**不重算 SHA256**，只做廉价检查）。"""
        exe = discover()
        m = self.manifest
        want = str(m.get("worker_version") or "")
        ready, why = sources_ready(m)
        if self._state in (DOWNLOADING, PAUSED, VERIFYING, INSTALLING):
            return {"state": self._state, "detail": self._detail,
                    "progress": self._progress, "exe": exe,
                    "source": self._cur_src, "speed_bps": self._speed,
                    "downloaded": self._got, "total": self._total,
                    "want_version": want}
        if not exe:
            return {"state": NOT_INSTALLED,
                    "detail": why if not ready else "未安装（不影响其他功能）",
                    "progress": 0.0, "exe": "", "want_version": want,
                    "sources_ready": ready, "sources_reason": why}
        # 已装：廉价读版本（不跑自检；自检只在安装/手动校验时跑）
        ver = ""
        vf = os.path.join(os.path.dirname(exe), "_internal", "worker.version")
        if os.path.isfile(vf):
            try:
                ver = open(vf, encoding="utf-8").read().strip()
            except OSError:
                ver = ""
        if want and ver and ver != want:
            return {"state": INCOMPATIBLE, "exe": exe, "installed_version": ver,
                    "want_version": want, "progress": 0.0,
                    "detail": f"已安装 {ver}，需要 {want}"}
        st = self._state or INSTALLED
        return {"state": st, "detail": self._detail or "已安装", "progress": 1.0,
                "exe": exe, "installed_version": ver or want,
                "want_version": want}

    def cancel(self) -> None:
        self._stop.set()
        self._state = PAUSED
        self._detail = "已暂停/取消（已下载的部分保留，可继续）"

    # ---------- 安装 ----------
    def install(self, *, torch_home: str = "", timeout: float = 1800) -> dict:
        """下载 → 校验 → 解压到 staging → 完整性 + 自检 → 原子切换。

        任何一步失败：**删 staging，旧版本原地不动**，状态为 failed/selftest_failed。
        """
        with self._lock:
            self._stop.clear()
            self._fail_result = {}
            with self._plock:
                self._pbytes, self._ptotals = {}, {}
                self._got = self._total = 0
            comps = [c for c in (self.manifest.get("components") or []) if c.get("required", True)]
            if not comps:
                self._state = FAILED
                self._detail = "清单里没有组件"
                return {"ok": False, "state": FAILED, "error": self._detail}
            # ⚠️ **没有可用下载源就不要开始**：否则用户等到下载中途才看到
            #    "所有源都失败"。这里当场说清楚，并保持"未安装"而不是"失败"。
            ready, why = sources_ready(self.manifest)
            if not ready:
                self._state = NOT_INSTALLED
                self._detail = why
                self.log(f"[组件] 未开始下载：{why}")
                return {"ok": False, "state": NOT_INSTALLED, "error": why}
            cache = os.path.join(paths.data_dir(), CACHE_DIRNAME)
            os.makedirs(cache, exist_ok=True)
            root = worker_root()
            staging = root + f".staging-{int(time.time())}"
            # ---- ① 下载 + 校验 ----
            self._state = DOWNLOADING
            # 并发度：默认 3（用户要求 2–3，且要限流）。
            # 并发安全性审查（2026-10-16）：
            #   * 每个组件写**自己的** `<file>.part` / `.part.meta.json` ⇒ 文件层面无共享；
            #   * 镜像探测/切源/退避都是组件内部状态 ⇒ 互不影响；
            #   * 共享的只有 progress/log 回调与整机进度 ⇒ 用锁保护；
            #   * 取消用同一个 `threading.Event`，所有组件的循环都会尽快看到 ⇒ 能一起停，
            #     且 `.part` **保留**（可续传）。
            par = int(self.manifest.get("download_parallel") or 3)
            par = max(1, min(3, par))          # 硬上限 3，避免打满带宽/磁盘
            if par > 1 and len(comps) > 1:
                self._download_parallel(comps, cache, par)
            else:
                self._download_serial(comps, cache)
            if self._stop.is_set():
                self._state = PAUSED
                self._detail = "已取消（已下载的部分保留，可继续）"
                return {"ok": False, "state": PAUSED, "error": self._detail}
            if self._fail_result:                 # 下载阶段有组件失败 ⇒ 不进入安装
                return dict(self._fail_result)
            # ---- ② 解压到 staging ----
            self._state = INSTALLING
            self._detail = "解压组件…"
            if os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(staging, exist_ok=True)
            for c in comps:
                arc = os.path.join(cache, c["file"])
                try:
                    with tarfile.open(arc, "r:*") as tf:
                        for m in tf.getmembers():
                            # 防目录穿越
                            tgt = os.path.abspath(os.path.join(staging, m.name))
                            if not tgt.startswith(os.path.abspath(staging)):
                                raise RuntimeError(f"包内路径越界：{m.name}")
                        tf.extractall(staging)
                except Exception as e:                       # noqa: BLE001
                    shutil.rmtree(staging, ignore_errors=True)
                    self._state = FAILED
                    self._detail = f"解压失败：{e}"
                    return {"ok": False, "state": FAILED, "error": self._detail}
            # ---- ③ 完整性检查 ----
            missing = [f for f in REQUIRED_FILES
                       if not os.path.isfile(os.path.join(staging, f))]
            if missing:
                shutil.rmtree(staging, ignore_errors=True)
                self._state = FAILED
                self._detail = f"组件不完整，缺少 {len(missing)} 个关键文件：" + \
                               "、".join(os.path.basename(x) for x in missing[:3])
                return {"ok": False, "state": FAILED, "error": self._detail,
                        "missing": missing}
            # 写版本文件（供廉价版本比对）
            try:
                with open(os.path.join(staging, "_internal", "worker.version"),
                          "w", encoding="utf-8") as fh:
                    fh.write(str(self.manifest.get("worker_version") or ""))
            except OSError:
                pass
            # ---- ④ 自检 ----
            self._detail = "运行 Worker 自检（torch._C / CUDA）…"
            st = run_selftest(worker_exe(staging), timeout=300)
            if not st.get("ok"):
                shutil.rmtree(staging, ignore_errors=True)
                self._state = SELFTEST_FAILED
                self._detail = f"自检失败：{st.get('error') or 'torch/CUDA 不可用'}"
                return {"ok": False, "state": SELFTEST_FAILED,
                        "error": self._detail, "selftest": st}
            # ---- ⑤ 原子切换（旧版本保留到切换成功为止）----
            old = ""
            try:
                if os.path.isdir(root):
                    old = root + f".old-{int(time.time())}"
                    os.rename(root, old)
                os.makedirs(os.path.dirname(root), exist_ok=True)
                os.rename(staging, root)
            except OSError as e:
                # 回滚
                if old and not os.path.isdir(root) and os.path.isdir(old):
                    try:
                        os.rename(old, root)
                    except OSError:
                        pass
                shutil.rmtree(staging, ignore_errors=True)
                self._state = FAILED
                self._detail = f"切换安装目录失败：{e}"
                return {"ok": False, "state": FAILED, "error": self._detail}
            if old:
                shutil.rmtree(old, ignore_errors=True)
            self._state = INSTALLED
            self._detail = f"已安装 {self.manifest.get('worker_version')}"
            self._progress = 1.0
            self.log(self._detail)
            return {"ok": True, "state": INSTALLED, "exe": worker_exe(),
                    "selftest": st, "version": self.manifest.get("worker_version")}

    # ---------- 下载（串行 / 并行两套，逻辑共用 _fetch_one）----------
    def _fetch_one(self, c: dict, cache: str) -> dict:
        """下载+校验**一个**组件。返回 `{"ok":bool,...}`（不抛异常，便于线程池收集）。

        先查缓存（大小 + SHA256 都对就跳过）—— 失败重试/续传时不会重复下载。
        """
        dest = os.path.join(cache, c["file"])
        sha = str(c.get("sha256") or "")
        want = int(c.get("bytes") or 0)
        if os.path.isfile(dest) and want and os.path.getsize(dest) == want and sha:
            from desktop.services.model_manager import sha256_file
            if sha256_file(dest).lower() == sha.lower():
                self.log(f"[缓存] {c['id']} 已下载且校验通过，跳过")
                with self._plock:
                    self._pbytes[c["id"]] = want
                return {"ok": True, "id": c["id"], "cached": True, "bytes": want}
            self.log(f"[缓存] {c['id']} 校验不过，重新下载")
        try:
            res = download_from_sources(
                self.component_sources(c), dest, sha256=sha,
                expected_size=want, log=self.log, stop=self._stop.is_set,
                progress=lambda got, total, info, _i=c["id"]: self._on_progress(
                    got, total, info, _i))
        except DownloadError as e:
            return {"ok": False, "id": c["id"], "kind": e.kind,
                    "error": str(e), "detail": e.detail}
        return {"ok": True, "id": c["id"], "bytes": res.get("bytes"),
                "label": res.get("label", "")}

    def _download_serial(self, comps: list, cache: str) -> None:
        n = len(comps)
        for i, c in enumerate(comps):
            self._detail = f"正在下载 {c['id']}（{i+1}/{n}）"
            r = self._fetch_one(c, cache)
            if not r["ok"]:
                self._fail_download(r)
                return
            self._cur_src = r.get("label", self._cur_src)
            self._progress = (i + 1) / n

    def _download_parallel(self, comps: list, cache: str, par: int) -> None:
        """并发下载（上限 3）。**全部成功**才返回正常；任一失败则停掉其余。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        n = len(comps)
        self._detail = f"并发下载 {n} 个组件（并发度 {par}）"
        self.log(f"[下载] 并发度 {par}（上限 3）")
        done = 0
        with ThreadPoolExecutor(max_workers=par,
                                thread_name_prefix="l2t-dl") as ex:
            futs = {ex.submit(self._fetch_one, c, cache): c for c in comps}
            try:
                for fut in as_completed(futs):
                    c = futs[fut]
                    try:
                        r = fut.result()
                    except Exception as e:                   # noqa: BLE001
                        r = {"ok": False, "id": c["id"], "kind": "exception",
                             "error": f"{type(e).__name__}: {e}",
                             "detail": str(e)}
                    if not r["ok"]:
                        # 任一失败：取消尚未开始的任务，其余组件保留 .part 可续传
                        for f2 in futs:
                            f2.cancel()
                        self._fail_download(r)
                        return
                    done += 1
                    with self._plock:
                        self._pbytes[c["id"]] = int(r.get("bytes") or 0)
                    self._progress = done / n
                    self._detail = f"已下载 {done}/{n} 个组件"
            finally:
                ex.shutdown(wait=True, cancel_futures=True)

    def _fail_download(self, r: dict) -> None:
        self._state = FAILED
        self._detail = r.get("detail") or r.get("error") or "下载失败"
        self._fail_result = {"ok": False, "state": FAILED, "error": r.get("error"),
                             "kind": r.get("kind"), "component": r.get("id")}

    def _on_progress(self, got: int, total: int, info: dict,
                     comp_id: str = "") -> None:
        """进度回调。**并发下载时会被多个线程同时调用** ⇒ 共享状态用锁保护。

        （`worker_service` 之前是串行下载，所以这里原来没有锁；加并发后
        不加锁会出现"进度数字乱跳/整体百分比回退"，属于典型并发缺陷。）
        """
        with self._plock:
            if comp_id:
                self._pbytes[comp_id] = got
                self._ptotals[comp_id] = total
                g = sum(self._pbytes.values())
                t = sum(v for v in self._ptotals.values() if v)
                if t:
                    self._got, self._total = g, t
            else:
                self._got, self._total = got, total
            self._speed = float(info.get("speed_bps") or 0)
            self._cur_src = info.get("source", self._cur_src)
            self._state = VERIFYING if info.get("verifying") else DOWNLOADING
            if info.get("verifying"):
                self._detail = "校验中（SHA256）"

    def verify_installed(self) -> dict:
        """对已安装的 Worker 重跑自检 + 版本比对（界面"校验"按钮）。"""
        exe = discover()
        if not exe:
            return {"ok": False, "state": NOT_INSTALLED, "error": "未安装"}
        st = run_selftest(exe, timeout=300)
        want = str(self.manifest.get("worker_version") or "")
        got = ""
        vf = os.path.join(os.path.dirname(exe), "_internal", "worker.version")
        if os.path.isfile(vf):
            try:
                got = open(vf, encoding="utf-8").read().strip()
            except OSError:
                got = ""
        if want and got and got != want:
            self._state = INCOMPATIBLE
            self._detail = f"版本不兼容：已装 {got}，需要 {want}"
            return {"ok": False, "state": INCOMPATIBLE, "detail": self._detail}
        self._state = INSTALLED if st.get("ok") else SELFTEST_FAILED
        self._detail = "自检通过" if st.get("ok") else f"自检失败：{st.get('error')}"
        return {"ok": bool(st.get("ok")), "state": self._state,
                "detail": self._detail, "selftest": st}

    def uninstall(self, remove_cache: bool = False) -> dict:
        root = worker_root()
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
        if remove_cache:
            shutil.rmtree(os.path.join(paths.data_dir(), CACHE_DIRNAME),
                          ignore_errors=True)
        self._state = NOT_INSTALLED
        self._detail = "已卸载"
        return {"ok": True, "removed": root}


# --------------------------------------------------------------------------
# 命令行入口：`Lyric2TTML.exe --worker-cli --status|--install|--verify|--uninstall`
#
# 为什么需要它：用户要求"在**冻结版 EXE** 里验证运行库下载/安装/自检"。
# 界面点击没法自动化，所以照 `--model-cli` / `--runtime-cli` 的既有约定
# 加一个等价入口 —— 走**同一套服务层代码**（`WorkerService`），
# 不是另写一条只有测试才走的路径。
# --------------------------------------------------------------------------
def _cli(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="Lyric2TTML --worker-cli",
                                 description="人声分离运行库（可选组件）管理")
    ap.add_argument("--status", action="store_true", help="打印状态 JSON")
    ap.add_argument("--install", action="store_true", help="下载并安装")
    ap.add_argument("--verify", action="store_true", help="对已安装的重跑自检")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--remove-cache", action="store_true",
                    help="卸载时连下载缓存一起删")
    ap.add_argument("--json", default="", help="把结果也写到这个文件")
    ap.add_argument("--no-cache-reuse", action="store_true",
                    help="先清掉下载缓存（测串行/并行对比用）")
    a = ap.parse_args(argv)

    def out(d: dict) -> None:
        print(json.dumps(d, ensure_ascii=False))
        if a.json:
            try:
                with open(a.json, "w", encoding="utf-8") as fh:
                    json.dump(d, fh, ensure_ascii=False, indent=2)
            except OSError:
                pass

    if a.no_cache_reuse:
        shutil.rmtree(os.path.join(paths.data_dir(), CACHE_DIRNAME),
                      ignore_errors=True)

    svc = WorkerService(log=lambda m: print(f"[worker] {m}", flush=True))
    if a.status:
        out(svc.snapshot())
        return 0
    if a.verify:
        out(svc.verify_installed())
        return 0
    if a.uninstall:
        out(svc.uninstall(remove_cache=a.remove_cache))
        return 0
    if a.install:
        t0 = time.time()
        res = svc.install()
        res["elapsed_s"] = round(time.time() - t0, 1)
        res["manifest_source"] = svc.manifest.get("_source", "")
        out(res)
        return 0 if res.get("ok") else 1
    ap.print_help()
    return 2
