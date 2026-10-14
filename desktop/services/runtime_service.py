"""运行时组件管理（**不是模型**）：下载 / 校验 / 安装 / 复用 PyTorch 这类大依赖。

    python -m desktop.services.runtime_service --status        # 看状态
    python -m desktop.services.runtime_service --install torch # 下载并安装
    python -m desktop.services.runtime_service --remove torch  # 清理（要用户确认）

## 为什么单独一层

PyTorch 只服务 CAM++ 声纹（对唱分声部）与 Demucs 人声分离，却占 **425 MiB** ——
是主安装包体积第二大头。把它做成"按需下载的运行时组件"之后，主安装包从
0.78 GB 降到约 0.5 GB（需求原文 §七-B 明确允许这种做法）。

## 四条硬约束（需求第 3/4/8/9 条）

1. **装在用户可写目录**，**绝不能**放进 PyInstaller 的临时解包目录
   （`_MEIPASS` 会被清理，而且写入会失败）。这里用
   `<数据目录>/runtime/<id>/`（默认在 `%LOCALAPPDATA%` 下的 Lyric2TTML 里）。
2. **`.part` 永远不删**（除非校验证明内容是错的）—— 断点续传全靠它。
   这条是从模型下载那里学来的（真踩过：删了 `.part`，3 GB 白下好几次）。
3. **必须真的加载验证**才算 `READY`：光看"文件都在"没有意义，
   实测过"文件齐了但 import 失败"的情况（ABI 不匹配）。
4. **升级要保留**：装在数据目录而不是程序目录，所以卸载/升级安装目录都不动它；
   只有用户明确同意才清理。

⚠️ 与 `model_manager` 的分工：模型权重在 `models/`，运行时在 `runtime/`，
两份清单、两个状态机，**互不冒充**（需求第 9 条：不得把运行时缺失误报成
权重缺失，反之亦然）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

try:
    from desktop import paths
    from desktop.services.model_manager import (
        DownloadCancelled, _http_get_to_file, atomic_replace, human,
        sha256_file,
    )
except ImportError:                                   # 单文件直接跑
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from desktop import paths
    from desktop.services.model_manager import (
        DownloadCancelled, _http_get_to_file, atomic_replace, human,
        sha256_file,
    )

Readiness = Callable[[], bool]

NOT_INSTALLED = "not_installed"
DOWNLOADING = "downloading"
VERIFYING = "verifying"
READY = "ready"
FAILED = "failed"
PARTIAL = "partial"          # 有 .part，可以续传

STATUS_LABEL = {
    NOT_INSTALLED: "未安装",
    DOWNLOADING: "下载中",
    VERIFYING: "校验中",
    READY: "已就绪",
    FAILED: "失败",
    PARTIAL: "下载未完成（可续传）",
}


def default_manifest_path() -> str:
    """运行时清单的位置（打包版在 `_internal`，源码版在仓库）。"""
    env = os.environ.get("L2T_RUNTIME_MANIFEST")
    if env and os.path.isfile(env):
        return env
    for root in paths.resource_roots():
        p = os.path.join(root, "desktop", "resources", "runtime_manifest.json")
        if os.path.isfile(p):
            return p
    return os.path.join(paths.source_dir(), "desktop", "resources",
                        "runtime_manifest.json")


def load_manifest(path: str = "") -> dict:
    path = path or default_manifest_path()
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class RuntimeState:
    id: str
    name: str = ""
    purpose: str = ""
    note: str = ""
    status: str = NOT_INSTALLED
    detail: str = ""
    location: str = ""
    download_bytes: int = 0        # 需要下载的字节数（供"下载前告知"用）
    installed_bytes: int = 0       # 装完占多少磁盘（估算）
    present_bytes: int = 0
    can_download: bool = True
    spec: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {
            "id": self.id, "name": self.name, "purpose": self.purpose,
            "note": self.note, "status": self.status,
            "status_label": STATUS_LABEL.get(self.status, self.status),
            "detail": self.detail, "location": self.location,
            "download_bytes": self.download_bytes,
            "download_human": human(self.download_bytes),
            "installed_bytes": self.installed_bytes,
            "installed_human": human(self.installed_bytes or self.download_bytes),
            "present_bytes": self.present_bytes,
            "can_download": self.can_download,
        }


class RuntimeManager:
    """运行时组件的状态 + 下载安装。

    ⚠️ 本模块**不许 import Qt**（和 `services/*` 的其他模块一样），
    这样 `desktop/selftest.py` 才能在无图形环境里跑它。
    """

    def __init__(self, manifest: dict | None = None,
                 root: str | None = None) -> None:
        self._manifest = manifest or load_manifest()
        self._root_override = root
        self._cache: Dict[str, RuntimeState] = {}

    # ------------------------------------------------------------ 路径
    @property
    def root(self) -> str:
        """运行时的安装根目录（**用户可写**，不在程序目录里）。"""
        if self._root_override:
            return self._root_override
        return os.path.join(paths.data_dir(), "runtime")

    def dir_for(self, rid: str) -> str:
        return os.path.join(self.root, rid)

    def site_dir(self, rid: str) -> str:
        """把这个目录加进 `sys.path` 就能 `import torch`。"""
        return self.dir_for(rid)

    def wheel_cache(self) -> str:
        """wheel 的落盘位置 —— **不要放进 site 目录**（否则会被当成包扫到）。"""
        return os.path.join(self.root, ".wheels")

    # ------------------------------------------------------------ 清单
    def all_specs(self) -> List[dict]:
        return list(self._manifest.get("runtimes") or [])

    def spec(self, rid: str) -> dict:
        for s in self.all_specs():
            if s.get("id") == rid:
                return s
        raise KeyError(f"清单里没有运行时 {rid!r}")

    # ------------------------------------------------------------ 状态
    def invalidate(self, rid: str = "") -> None:
        if rid:
            self._cache.pop(rid, None)
        else:
            self._cache.clear()

    def check(self, rid: str, use_cache: bool = True) -> RuntimeState:
        if use_cache and rid in self._cache:
            return self._cache[rid]
        st = self._check_uncached(rid)
        self._cache[rid] = st
        return st

    def _check_uncached(self, rid: str) -> RuntimeState:
        spec = self.spec(rid)
        st = RuntimeState(
            id=rid,
            name=spec.get("name", rid),
            purpose=spec.get("purpose", ""),
            note=spec.get("note", ""),
            location=self.dir_for(rid),
            download_bytes=sum(int(w.get("size") or 0)
                               for w in ((spec.get("wheels") or [])
                                         + (spec.get("archives") or []))),
            installed_bytes=int(spec.get("installed_size") or 0),
            spec=spec,
        )
        site = self.site_dir(rid)
        if os.path.isdir(site):
            st.present_bytes = _dir_size(site)
            ok, why = _verify_installed(spec, site)
            if ok:
                st.status = READY
                st.detail = why
                return st
            st.status = FAILED
            st.detail = why
            return st

        # ⚠️⚠️ **还要认"不是我们下载的"那些来源**（第 20 轮修的真 bug）。
        # 上面只看了 `<data_dir>/runtime/<id>` —— 但 CUDA 运行库可能来自：
        #   * **完整安装包**（`_internal/nvidia/cublas/bin`，`L2T_FULL_CUDA=1` 打的那种）；
        #   * 系统装的 CUDA Toolkit（`CUDA_PATH`）；
        #   * 源码模式 venv 里的 `nvidia-cublas-cu12`。
        # 只认自己下载的那份会怎样（实测过）：
        #   `gpu_probe.cuda_libs()` 说 **ok=True**（DLL 真能加载），
        #   而 `check('cuda')` 说 **not_installed** —— 于是
        #     ① 「模型」页显示"GPU 运行时未安装"，引导用户**白下 529 MB**；
        #     ② 「开始制作」的前置检查直接**拦住转换**，
        #        也就是**完整包（离线版）反而做不了歌词**，比小包还糟。
        # 判据用 `cuda_libs()`（`ctypes.WinDLL` 真加载）而不是"看目录在不在"：
        # 文件在但加载不了（ABI 不匹配等）不该算就绪。
        if spec.get("verify_dll_probe") == "cuda":
            try:
                from desktop.services import gpu_probe
                libs = gpu_probe.cuda_libs()
                if libs.get("ok"):
                    st.status = READY
                    st.detail = f"已可用（不是本程序下载的）：{libs.get('detail', '')}"
                    return st
            except Exception:                           # noqa: BLE001
                # 探测失败**不改变结论**（继续按"未安装"走）——
                # 这里不能因为一个探测异常就把可用的环境判成不可用。
                pass

        # 没有装：看有没有 .part 可以续传
        # ⚠️ `archives` 也要看：CUDA 走的是 zip，不看的话
        # "下载中断后还想续传"会被判成"从未开始"（真踩到：detail 显示 0B）。
        partial = []
        for w in ((spec.get("wheels") or []) + (spec.get("archives") or [])):
            p = os.path.join(self.wheel_cache(), w["filename"] + ".part")
            if os.path.isfile(p):
                partial.append((p, os.path.getsize(p), int(w.get("size") or 0)))
        if partial:
            st.status = PARTIAL
            done = sum(a for _p, a, _t in partial)
            st.present_bytes = done
            st.detail = (f"已下载 {human(done)} / {human(st.download_bytes)}，"
                         f"可以接着下")
        else:
            st.status = NOT_INSTALLED
            if spec.get("download_policy") == "bundled":
                st.status = READY
                st.detail = "随主程序提供"
            else:
                st.detail = f"需要下载 {human(st.download_bytes)}"
        return st

    def check_all(self, use_cache: bool = True) -> List[RuntimeState]:
        return [self.check(s["id"], use_cache) for s in self.all_specs()]

    def missing_for(self, feature: str) -> List[RuntimeState]:
        """某个功能需要但**还没就绪**的运行时（给"要用之前先准备"用）。"""
        out = []
        for spec in self.all_specs():
            if feature in (spec.get("required_for") or []):
                st = self.check(spec["id"])
                if st.status != READY:
                    out.append(st)
        return out

    def report(self) -> List[dict]:
        return [s.public() for s in self.check_all()]

    # ------------------------------------------------------- 下载 / 安装
    def download(self, rid: str,
                 log: Callable[[str], None] = print,
                 progress: Optional[Callable[[Optional[float], str], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None) -> RuntimeState:
        """下载并安装这个运行时。**已经 READY 就直接返回**（幂等）。

        进度是**真实字节数算出来的**，不是假动画：多个 wheel 时按总字节加权。
        """
        p = progress or (lambda pct, msg: None)
        stop = cancel or (lambda: False)

        def _stop() -> None:
            if stop():
                raise DownloadCancelled("用户取消")

        st = self.check(rid, use_cache=False)
        if st.status == READY:
            log(f"{st.name} 已就绪，无需下载")
            return st

        spec = self.spec(rid)
        wheels = spec.get("wheels") or []
        # ⚠️⚠️ **两种分发形态都要支持**（真踩到）：
        #   * `wheels`：PyPI 的 .whl（torch 走这条，解包后要 flatten）；
        #   * `archives`：我们自己打的 zip（**CUDA 运行时**走这条 ——
        #     它是三个 `.dll`，不是 python 包，没有 wheel 可发）。
        # 第 17 轮用户选了"方案 D：GPU 运行时按需下载"，主包降到 ~190 MB，
        # 所以必须有这条路。原来只认 `wheels`，遇到 CUDA 这种
        # `archives` 形态会直接抛 `清单里没有 wheel`。
        archives = spec.get("archives") or []
        if not wheels and not archives:
            raise ValueError(f"{rid}: 清单里既没有 wheel 也没有 archives")

        items = wheels or archives
        total = sum(int(w.get("size") or 0) for w in items)
        cache = self.wheel_cache()
        paths.ensure_dir(cache)

        p(0.0, f"准备下载 {human(total)}")
        log(f"=== {spec.get('name', rid)} ===")
        log(f"需要下载 {human(total)}，装好后约占用 "
            f"{human(int(spec.get('installed_size') or total))}")
        _require_space(self.root, total)

        done_bytes = 0
        files: List[str] = []
        for w in items:
            _stop()
            fn = w["filename"]
            dst = os.path.join(cache, fn)
            want = int(w.get("size") or 0)
            sha = str(w.get("sha256") or "").lower()

            if os.path.isfile(dst) and want and os.path.getsize(dst) == want \
                    and sha and sha256_file(dst).lower() == sha:
                log(f"[跳过] {fn} 已下载且校验通过")
                done_bytes += want
                files.append(dst)
                p(done_bytes * 100.0 / total if total else None, "已下载")
                continue

            urls = list(w.get("urls") or [])
            if not urls:
                raise ValueError(f"{rid}: {fn} 没有下载地址")

            part = dst + ".part"
            base = done_bytes
            last_err = None
            for url in urls:
                _stop()
                log(f"下载 {fn}（{human(want)}）← {url.split('/')[2]}")
                try:
                    _http_get_to_file(
                        url, part, log=log, stop=stop, label=fn,
                        p=lambda pct, msg, _b=base: p(
                            ((_b + (pct or 0)) * 100.0 / total)
                            if (total and pct is not None) else None,
                            f"下载中 {human(int(_b + (pct or 0)))}"
                            f"/{human(total)}"))
                except DownloadCancelled:
                    raise
                except Exception as e:                 # noqa: BLE001
                    last_err = e
                    log(f"[失败] {type(e).__name__}: {str(e)[:160]}")
                    continue
                _stop()

                # ---- 校验：只在"内容确实错了"时才删（否则留着续传）----
                p(None, "校验中…")
                got = os.path.getsize(part)
                if want and got != want:
                    log(f"[校验失败] 字节数 {got} != {want}，删掉重新下")
                    _safe_remove(part)
                    last_err = RuntimeError(f"字节数不对（{got} != {want}）")
                    continue
                if sha:
                    digest = sha256_file(part).lower()
                    if digest != sha:
                        log(f"[校验失败] SHA256 不匹配（{digest[:16]}…），"
                            f"删掉重新下")
                        _safe_remove(part)
                        last_err = RuntimeError("SHA256 不匹配")
                        continue
                    log("SHA256 一致 ✓")
                atomic_replace(part, dst)
                done_bytes += want
                files.append(dst)
                p(done_bytes * 100.0 / total if total else None, "已下载")
                break
            else:
                # ⚠️ 失败时**绝不删 .part**（那是断点续传的全部指望）
                raise RuntimeError(
                    f"{fn} 下载失败（最后：{type(last_err).__name__}: "
                    f"{str(last_err)[:140]}）。已下好的部分保留在 {part}，"
                    f"重试会**接着下**。")

        # ⚠️⚠️ **下载完、还没解压，这里必须再查一次取消** —— 这是阴性对照逼出来的真 bug。
        # 原流程是：文件下载循环结束 → **直接进入解压**。
        # 用户若在**这中间**点取消（下载刚完成的那一刻），取消信号没人看，
        # 程序照样解压、照样报"完成"。
        # `tools/test_runtime_install.py` 的取消用例恰好落在这个窗口；
        # 我把"解压时的取消检查"故意去掉后测试**仍然通过** ——
        # 证明真正让那次取消生效的不是解压循环，而是这个缺口。
        _stop()

        # ---- 解包到**临时目录**，验完再原子换上去 ----
        # ⚠️⚠️ 两个都必须修（第 21 轮）：
        #  ① **不能直接往 site 解压**：解到一半崩了/被强杀，`site` 里就是
        #     半截文件。`check()` 看到 `site` 存在就会去验证 ——
        #     万一关键文件恰好已解出来，就会判 READY（**假就绪**）。
        #  ② **`extractall` 完全不理会取消**：一个 529 MB 的 zip 解压要几十秒，
        #     用户点了取消却停不下来，装完还会显示"完成"。
        # 现在：解到 `site + ".extract"` → 在那里验证 → 换上去。
        p(None, "正在安装…")
        site = self.site_dir(rid)
        log(f"解包到 {site}")
        tmp_site = site + ".extract"
        try:
            self._unpack(spec, files, tmp_site, log=log, p=p, stop=stop)

            # ---- 在**临时目录**验一次（需求第 3 条：光有文件不算就绪）----
            p(None, "正在验证…")
            if spec.get("verify_files"):
                # 非 python 包（CUDA 运行时）：验关键文件
                ok, why = _verify_files_present(spec, tmp_site)
            else:
                ok, why = _verify_loaded(spec, tmp_site)
            self.invalidate(rid)
            if not ok:
                st = self.check(rid, use_cache=False)
                st.status = FAILED
                st.detail = f"装好了但加载失败：{why}"
                log(f"[失败] {st.detail}")
                raise RuntimeError(
                    f"{spec.get('name', rid)} 安装完成但验证失败：{why}\n"
                    f"  文件在 {site}。可以点「重试」重新下载；"
                    f"如果反复失败请到「帮助与诊断」导出日志。")

            self._activate(spec, tmp_site, site, why, log=log)
        except BaseException:
            # ⚠️ 任何中断（取消/异常）都要清掉临时目录 ——
            # 留着既占空间，也可能被误当成安装现场。
            _safe_rmtree(tmp_site)
            raise

        # ⚠️ 这里**不要**再调一次 `check()` 当判据：那次会**再 import 一次 torch**，
        # 而第二次 import 走的是缓存路径、判据可能不同 —— 于是出现
        # "日志写着「真实加载通过」，却报「装好了但加载失败」"这种自相矛盾的
        # 假失败（真踩过）。`_verify_loaded` 已经真的加载并核对过版本与位置，
        # 它就是判据；下面只是把状态刷成 READY 供界面显示。
        st = self.check(rid, use_cache=False)
        st.status = READY
        st.detail = why
        log(f"[完成] {st.name}：{st.detail}")
        p(100.0, "完成")
        return st

    def _unpack(self, spec: dict, files: list, tmp_site: str,
                log=print, p=None, stop=None) -> None:
        """把下载好的包解到 `tmp_site`（**逐条检查取消**）。"""
        p = p or (lambda pct, msg: None)
        stop = stop or (lambda: False)

        def _stop2() -> None:
            if stop():
                raise DownloadCancelled("用户取消")

        paths.ensure_dir(tmp_site)
        for f in files:
            _stop2()
            if f.lower().endswith(".whl"):
                _extract_wheel(f, tmp_site, log=log, stop=stop)
            else:
                # `archives`（我们自己打的 zip）：逐条解开并检查取消，
                # 里面就是 `bin/*.dll` —— 正好是 `gpu_probe.cuda_dirs()`
                # 会去找的 `<data_dir>/runtime/cuda/bin`。
                log(f"解压 {os.path.basename(f)} …")
                with zipfile.ZipFile(f) as z:
                    members = z.infolist()
                    total_m = len(members) or 1
                    for i, m in enumerate(members, 1):
                        _stop2()
                        z.extract(m, tmp_site)
                        if i % 4 == 0 or i == total_m:
                            p(i * 100.0 / total_m, f"解压 {i}/{total_m}")
        # ⚠️ 只有 wheel 需要 flatten（whl 里带 `torch/` 之类的前缀）。
        # zip 归档是我们自己排的目录结构，flatten 反而会把 `bin/` 打平。
        if any(f.lower().endswith(".whl") for f in files):
            _flatten_site(tmp_site, log=log)

    def _activate(self, spec: dict, tmp_site: str, site: str, why: str,
                  log=print) -> None:
        """验证通过后，把临时目录**原子**换成正式的 site。

        ⚠️ 顺序：先把"已验证"状态写进**临时目录**，再删旧的 site、改名换上去。
        反过来（先 `_write_state(site)` 再换）状态文件会写进临时目录随后丢失，
        `check()` 读不到标记就要再 import 一次（冻结版里那次 import 有崩的风险）。
        """
        _write_state(tmp_site, spec, why)
        _safe_rmtree(site)
        try:
            os.replace(tmp_site, site)
        except OSError:
            # 跨卷等情况下 `os.replace` 对目录可能失败 → 退回 shutil.move。
            shutil.move(tmp_site, site)

    # ------------------------------------------------------------- 清理
    def remove(self, rid: str, log: Callable[[str], None] = print) -> RuntimeState:
        """删掉这个运行时（**只有用户明确同意才调**）。"""
        spec = self.spec(rid)
        log(f"正在删除 {spec.get('name', rid)} …")
        _safe_rmtree(self.dir_for(rid))
        _safe_rmtree(os.path.join(self.wheel_cache()))
        self.invalidate(rid)
        return self.check(rid, use_cache=False)

    def apply_to_path(self) -> List[str]:
        """把所有已就绪的运行时目录加进 `sys.path`，返回加进去的列表。

        ⚠️ **必须在任何 `import torch` 之前调用**（`desktop/app.py` 的启动
        顺序里就是这么安排的）。已经导入过 torch 再调是无效的。
        """
        added: List[str] = []
        for spec in self.all_specs():
            rid = spec["id"]
            if self.check(rid).status != READY:
                continue
            d = self.site_dir(rid)
            if d not in sys.path:
                sys.path.insert(0, d)
                added.append(d)
        return added


# =====================================================================
#  辅助
# =====================================================================
def _dir_size(path: str) -> int:
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _safe_remove(p: str) -> None:
    try:
        os.remove(p)
    except OSError:
        pass


def _safe_rmtree(p: str) -> None:
    try:
        shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _require_space(root: str, need: int) -> None:
    """磁盘空间检查（需求第 2 条：下载前要看剩余空间）。"""
    import shutil as _sh
    try:
        probe = root
        while probe and not os.path.isdir(probe):
            probe = os.path.dirname(probe)
        free = _sh.disk_usage(probe or ".").free
    except OSError:
        return
    if free < need * 1.2:
        raise RuntimeError(
            f"磁盘空间不足：需要约 {human(need)}，{probe} 上只剩 {human(free)}。\n"
            f"  请先清理磁盘，或到「设置」把数据目录改到空间更大的盘。")


def _extract_wheel(wheel: str, site: str, log=print,
                   stop: Optional[Callable[[], bool]] = None) -> None:
    """把一个纯 Python wheel 解到 ``site``。

    ⚠️ **必须防目录穿越**：wheel 里的名字理论上可以是 `../../evil`。
    只接受解压后仍在 `site` 之下的成员。
    """
    stop = stop or (lambda: False)
    site_abs = os.path.abspath(site)
    n = 0
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            if stop():
                raise DownloadCancelled("用户取消")
            name = info.filename
            if name.endswith("/"):
                continue
            target = os.path.abspath(os.path.join(site_abs, name))
            if not (target == site_abs
                    or target.startswith(site_abs + os.sep)):
                log(f"[跳过] 可疑路径：{name}")
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)
            n += 1
    log(f"  解出 {n} 个文件 → {site}")


def _flatten_site(site: str, log=print) -> None:
    """把"多套了一层"的布局摊平（**必需，不是洁癖**）。

    wheel 里本来就是 `torch/…` 顶层，解到 `<site>` 之后目录是
    `<site>/torch/torch/…`。而 torch 自己会 `import torch._custom_class_base`
    这类子模块 —— 它按**包所在目录的父目录**去解析，多一层就变成
    `ModuleNotFoundError: No module named 'torch._custom_class_base'`
    （实测踩到：import 报这个错，看着像"wheel 坏了"，其实是路径套了两层）。

    做法：如果 `<site>` 下**只有一个目录**，就把它的内容整体上移一层。
    """
    try:
        entries = [e for e in os.listdir(site) if not e.startswith(".")]
    except OSError:
        return
    for name in entries:
        sub = os.path.join(site, name)
        if not os.path.isdir(sub):
            continue
        # 只有当"里面还有同名目录"时才认为套了一层
        inner = os.path.join(sub, name)
        if not os.path.isdir(inner):
            continue
        for item in os.listdir(sub):
            src = os.path.join(sub, item)
            dst = os.path.join(site, item)
            if os.path.exists(dst):
                continue
            shutil.move(src, dst)
        try:
            os.rmdir(sub)
        except OSError:
            pass
        log(f"  已摊平 {name}/（避免 import 子模块时找不到）")


def _state_file(site: str) -> str:
    return os.path.join(site, ".l2t-installed.json")


def _crash_marker(site: str) -> str:
    """记录"在这个环境里，import 这个运行时会让进程崩"。

    ⚠️⚠️ 为什么需要它（真踩过，而且是**致命**的那种）：
    冻结版里主进程 `import torch` 会以 `0xC0000005`（访问冲突）**崩掉整个程序**。
    崩在 `_verify_loaded()` 里面时**连记录都来不及写** ——
    于是下一次启动还会再崩一次，用户看到的是"一开软件就闪退"。

    所以留一个**跨进程**的记号：再遇到时不再尝试 import，
    直接把状态标成失败 + 给出可执行提示，让**基础功能照常可用**
    （单声部 / 编辑 / 导出都不需要 torch）。
    """
    return os.path.join(site, ".l2t-import-crashed")


def _write_state(site: str, spec: dict, why: str) -> None:
    """记下"这一次安装已经真的加载验证过"。

    ⚠️ **为什么必须记文件，而不是每次 check 都 import 一次**（踩出来的）：
    在**冻结版**里 `import torch` 会让进程以 `0xC0000005`（访问冲突）崩掉
    （源码模式没事）。如果 `check()` 每次刷新都去 import，
    那"装了运行时"就变成"一打开界面就崩" —— 比不装还糟。

    ⇒ 分工：
      * **安装时**：真的 import 一次 + 核对版本与路径（`_verify_loaded`），
        通过了才把这个记录写下来；
      * **刷新时**（`check`）：只读这个记录 + 核对文件还在、字节数接近，
        **不 import**。轻量、不会崩、界面 400ms 刷一次也没负担。
    """
    try:
        cur = {}
        if os.path.isfile(_state_file(site)):
            with open(_state_file(site), encoding="utf-8") as fh:
                cur = json.load(fh)
        v = (spec.get("verify_load") or {})
        cur.update({
            "id": spec.get("id"),
            "verified": True,
            "why": why,
            "module": v.get("module", ""),
            "expected_version_prefix": v.get("expected_version_prefix", ""),
            "installed_at": _now(),
            "files": cur.get("files") or 0,
            "bytes": _dir_size(site),
        })
        # ⚠️ **原子写 + 立刻落盘**：冻结版可能在写完记录之后**立刻崩**
        # （退出阶段的原生崩溃）。如果只是 `json.dump` 到文件对象，
        # 数据还在缓冲区里，进程一崩就丢了 —— 实测就是这样：
        # `[完成] …（真实加载通过）` 打印了，但 `.l2t-installed.json` **不存在**，
        # 于是下一次启动判定为 failed（用户看到"装了没用"）。
        # 先写 `.tmp` 再 `os.replace`，并且 `flush + fsync` 强制落盘。
        tmp = _state_file(site) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=1)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        atomic_replace(tmp, _state_file(site))
        # 记录写完就清掉"上次崩过"的记号：这次的加载是成功的
        try:
            if os.path.isfile(_crash_marker(site)):
                os.remove(_crash_marker(site))
        except OSError:
            pass
    except OSError:
        pass


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _verify_installed(spec: dict, site: str) -> tuple:
    """轻量状态判定：**读安装时留下的验证记录** + 核对文件还在。

    不 import（冻结版会崩，见 `_write_state`）；真正的加载验证在
    `_verify_loaded` 里做，只在安装/显式"重新验证"时调用。
    """
    sf = _state_file(site)
    if not os.path.isfile(sf):
        # 有"上次 import 崩过"的记号、又没有成功记录 → 说清是怎么回事，
        # 而不是让用户看到一句没头没脑的"缺少安装记录"。
        if _crashed_before(site):
            return False, (
                "文件已解包，但在这个环境里**加载 PyTorch 会让进程崩溃**"
                "（上次尝试时崩了，记录已保留）。\n"
                "   受影响的只有「对唱分声部」；单声部歌词制作、"
                "TTML 编辑与导出都不受影响。\n"
                "   可以点「重试」再试一次；若反复崩溃请到"
                "「帮助与诊断」导出日志反馈。")
        return False, "目录在但缺少安装记录（可能是手工拷贝的）—— 建议重新下载"
    try:
        with open(sf, encoding="utf-8") as fh:
            rec = json.load(fh)
    except Exception as e:                              # noqa: BLE001
        return False, f"安装记录损坏：{type(e).__name__}"
    if not rec.get("verified"):
        return False, "安装记录显示未通过加载验证"
    # 文件数/字节数对不上 → 被删过或拷坏了
    want_bytes = int(rec.get("bytes") or 0)
    got_bytes = _dir_size(site)
    if want_bytes and got_bytes < want_bytes * 0.95:
        return False, (f"文件不完整：记录 {human(want_bytes)}，"
                       f"现在只有 {human(got_bytes)}")
    return True, f"{rec.get('why') or '已安装'}（{_now_note(rec)}）"


def _now_note(rec: dict) -> str:
    when = rec.get("installed_at") or ""
    return f"验证于 {when}" if when else "已验证"


def _verify_files_present(spec: dict, site: str) -> tuple:
    """非 python 包（如 CUDA 运行时）的验证：**核对关键文件真的在**。

    CUDA 运行时就是几个 `.dll`，没有模块可以 `import`，
    所以 `_verify_loaded` 那套（import + 版本前缀）在这里用不上。
    但**不能因此不验**：光看目录存在会给出一个骗人的 READY。

    判据取自清单的 `verify_files`（文件名列表）。
    """
    want = list(spec.get("verify_files") or [])
    if not want:
        return True, "未声明 verify_files（跳过文件检查）"
    missing = []
    for rel in want:
        # 允许 `bin/xxx.dll` 也允许已被 flatten 的 `xxx.dll`
        cands = [os.path.join(site, *rel.split("/")),
                 os.path.join(site, os.path.basename(rel))]
        if not any(os.path.isfile(c) and os.path.getsize(c) > 0 for c in cands):
            missing.append(rel)
    if missing:
        return False, f"缺少文件：{', '.join(missing[:4])}"
    return True, f"{len(want)} 个文件齐全"


def _verify_loaded(spec: dict, site: str) -> tuple:
    """**真的 import 一次**，核对版本前缀与加载位置（只在安装/显式重验时调）。

    为什么不能只看文件：实测过"文件齐全但 import 失败"（ABI 不匹配），
    以及"装的是另一个版本" —— 只看目录存在会给出一个骗人的 READY。

    ⚠️⚠️ **不能用 `subprocess([sys.executable, "-c", ...])`**。
    第一版就是那么写的，在**冻结版里必挂**：打包之后 `sys.executable` 是
    **Lyric2TTML.exe**，它不认 `-c`，于是报
    `unrecognized arguments: -c import sys, json;...` ——
    下载、校验、解包**全部成功**，最后卡在"验证"这一步，
    用户看到的是"装好了但加载失败"（**假失败**，比真失败更难查）。

    ⇒ 改成**本进程内**验证：临时把 site 插到 `sys.path` 最前，import 一次，
    核对版本与 `__file__` 位置，然后**还原 sys.path**。

    ⚠️⚠️ **不要在 `check()`（界面定时刷新）里调它**：冻结版里这次 import
    可能以 `0xC0000005`（访问冲突）崩掉进程，于是"装了运行时"会变成
    "一开界面就崩"。状态刷新走 `_verify_installed()`（读安装记录）。
    """
    v = (spec.get("verify_load") or {})
    if not v:
        return True, "已安装"
    module = v.get("module") or "torch"
    want_prefix = str(v.get("expected_version_prefix") or "")
    site_abs = os.path.abspath(site)

    # ⚠️⚠️ **先立"正在尝试"的记号，再 import** —— 顺序不能反。
    # 因为这次 import 可能把整个进程崩掉（冻结版实测 0xC0000005），
    # 崩了就没有机会写任何东西；标记必须**已经在磁盘上**，
    # 下一个进程才能看到"上次在这个环境里 import 崩过"，
    # 从而不再尝试、改成优雅降级（基础功能照常可用）。
    _mark_attempt(site)

    import importlib
    saved_path = list(sys.path)
    try:
        sys.path.insert(0, site_abs)
        for name in [k for k in list(sys.modules)
                     if k == module or k.startswith(module + ".")]:
            del sys.modules[name]           # 只清这个模块自己的
        try:
            mod = importlib.import_module(module)
        except Exception as e:                          # noqa: BLE001
            return False, (f"import {module} 失败：{type(e).__name__}: "
                           f"{str(e)[:160]}")
        got = str(getattr(mod, "__version__", "") or "")
        origin = os.path.abspath(str(getattr(mod, "__file__", "") or ""))
        if origin and not origin.startswith(site_abs):
            # 加载到了别处那份（例如系统里也装了 torch）—— 那不是我们要的结果
            return False, f"import 到的不是运行时目录里那份：{origin}"
        if want_prefix and not got.startswith(want_prefix):
            return False, f"版本不符：需要 {want_prefix}.*，实际 {got}"
        return True, f"{module} {got}（真实加载通过）"
    finally:
        sys.path[:] = saved_path


def _mark_attempt(site: str) -> None:
    """在 `_verify_loaded()` 动 import 之前，把"尝试"写到磁盘上（含 fsync）。"""
    try:
        marker = _crash_marker(site)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(_now())
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
    except OSError:
        pass


def _crashed_before(site: str) -> bool:
    """这个环境里上次 import 是不是崩过（且之后没有成功记录）。"""
    try:
        if not os.path.isfile(_crash_marker(site)):
            return False
        sf = _state_file(site)
        if os.path.isfile(sf):
            with open(sf, encoding="utf-8") as fh:
                if json.load(fh).get("verified"):
                    return False        # 后来成功过 → 不是问题
        return True
    except OSError:
        return False


def _cli(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="runtime", description="运行时组件管理")
    ap.add_argument("--status", action="store_true", help="打印状态")
    ap.add_argument("--install", default="", help="下载并安装（如 torch）")
    ap.add_argument("--remove", default="", help="删除（需确认）")
    ap.add_argument("--yes", action="store_true", help="配合 --remove 免确认")
    ap.add_argument("--json", action="store_true", help="状态用 JSON 输出")
    a = ap.parse_args(argv)

    mgr = RuntimeManager()
    if a.remove:
        if not a.yes:
            print("删除运行时需要 --yes（界面上是二次确认弹窗）")
            return 2
        st = mgr.remove(a.remove)
        print(f"已删除；现在状态：{st.status}")
        return 0
    if a.install:
        try:
            st = mgr.download(a.install)
        except DownloadCancelled:
            print("已取消")
            return 130
        except Exception as e:                          # noqa: BLE001
            print(f"[失败] {type(e).__name__}: {e}")
            return 1
        print(f"状态：{st.status} — {st.detail}")
        print(f"位置：{st.location}")
        return 0

    rows = mgr.report()
    if a.json:
        print(json.dumps({"root": mgr.root, "runtimes": rows},
                         ensure_ascii=False))
        return 0
    print(f"运行时目录：{mgr.root}")
    for r in rows:
        print(f"  [{r['status_label']}] {r['name']}  "
              f"（需下载 {r['download_human']}，装后约 {r['installed_human']}）")
        print(f"      {r['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
