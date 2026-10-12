"""模型管理器：清单 → 状态机 → 下载 → **校验** → READY。

为什么单独一层：安装器只管"把软件装好"，模型 3GB+ 不可能塞进安装包
（GitHub 单文件 2GB 上限）。所以模型的获取必须由**运行中的程序**负责，
而且必须能回答三个问题：

1. 现在有没有？
2. 有的话，**是不是完整的**？
3. 缺的话，**怎么补**，补到哪一步了？

⚠️ **"请求返回 200" ≠ "模型可用"**。``READY`` 的判据是
"文件清单齐全 + 关键文件字节数对得上（有 SHA256 就核 SHA256）"，
不是"下载函数没抛异常"。半截的 ``model.bin`` 会让 faster-whisper 在加载时
才炸（而且报的是莫名其妙的错误）。

⚠️ **临时文件与正式缓存分开**：直接下载到目标位置的话，程序中断留下的
半个文件会被下一次当成"已存在"。这里一律先下到 ``<目标>.part``，
校验通过才 ``os.replace`` 上位。

本模块**不导入 Qt**。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:                                                  # 作为包的一部分导入
    from desktop import paths
except ImportError:                                   # 单文件直接跑（python model_manager.py）
    import paths                                      # type: ignore

MANIFEST_NAME = "model_manifest.json"

# 状态机
NOT_INSTALLED = "not_installed"
QUEUED = "queued"
DOWNLOADING = "downloading"
VERIFYING = "verifying"
READY = "ready"
FAILED = "failed"

STATUS_LABEL = {
    NOT_INSTALLED: "未安装",
    QUEUED: "排队中",
    DOWNLOADING: "下载中",
    VERIFYING: "校验中",
    READY: "已就绪",
    FAILED: "失败",
}

HF_ENDPOINTS = ["https://hf-mirror.com", "https://huggingface.co"]


# --------------------------------------------------------------------- 工具

def human(n: Optional[float]) -> str:
    if not n:
        return "0B"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def sha256_file(path: str, chunk: int = 1 << 20,
                progress: Optional[Callable[[int, int], None]] = None) -> str:
    h = hashlib.sha256()
    total = 0
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
            total += len(b)
            if progress:
                progress(total, size)
    return h.hexdigest()


def atomic_replace(src: str, dst: str) -> None:
    """把临时文件"上位"成正式文件（同目录，``os.replace`` 是原子的）。"""
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    os.replace(src, dst)


def default_manifest_path() -> str:
    """清单文件位置：优先程序自带的（只读资源），其次数据目录里的覆盖版。

    ⚠️ **不要在这里建目录**：这个函数会在纯查询路径上被调用
    （``ModelManager()`` 的构造函数），建目录可能因为权限失败，
    结果"只是想看一眼模型状态"都打不开界面。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.join(here, "resources", MANIFEST_NAME)
    if os.path.isfile(bundled):
        return bundled
    # desktop/resources/（与界面资源放一起；也是打包时 --add-data 的目标）
    shared = os.path.join(os.path.dirname(here), "resources", MANIFEST_NAME)
    if os.path.isfile(shared):
        return shared
    from_env = os.environ.get("L2T_MODEL_MANIFEST")
    if from_env and os.path.isfile(from_env):
        return from_env
    return shared


def load_manifest(path: Optional[str] = None) -> dict:
    p = path or default_manifest_path()
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data.get("models"), list) or not data["models"]:
        raise ValueError(f"模型清单里没有 models：{p}")
    return data


# ------------------------------------------------------------------ 数据类

@dataclass
class ModelState:
    """一个模型的"现状"。UI 只认这个。"""
    id: str
    name: str = ""
    category: str = ""
    purpose: str = ""
    note: str = ""
    default: bool = False
    policy: str = ""
    approx_size: int = 0
    license: str = ""
    status: str = NOT_INSTALLED
    detail: str = ""
    location: str = ""
    present_bytes: int = 0
    missing: List[str] = field(default_factory=list)
    size_mismatch: List[str] = field(default_factory=list)
    can_download: bool = False
    download_hint: str = ""
    spec: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict:
        d = dict(self.__dict__)
        d["status_label"] = STATUS_LABEL.get(self.status, self.status)
        d["approx_size_h"] = human(self.approx_size)
        d["present_h"] = human(self.present_bytes)
        d.pop("spec", None)
        return d


# ------------------------------------------------------------------ 管理器

class ModelManager:
    """按清单检查 / 下载 / 校验模型。

    ``manifest_path`` 可注入（测试用）；``hf_cache`` 可注入（测试用）。
    """

    def __init__(self, manifest_path: Optional[str] = None,
                 hf_cache: Optional[str] = None,
                 app_dir: Optional[str] = None):
        self.manifest_path = manifest_path or default_manifest_path()
        self.manifest = load_manifest(self.manifest_path)
        self._hf_cache_override = hf_cache
        self._app_dir_override = app_dir
        # 检查结果缓存。
        # ⚠️ **必须有**：界面每 400ms 轮询刷新一次，而 ``file_sha256`` 那种
        # 校验要**真读一遍文件**（模型 28MB 到 3GB）。不缓存的话界面会因为
        # 反复算 SHA256 而肉眼可见地卡。
        # 缓存只在本对象里有效，且**任何下载/清理之后必须清掉**，
        # 否则会出现"下完了但界面还说没装"。
        self._cache: Dict[str, ModelState] = {}

    # ------------------------------------------------------------- 位置
    @property
    def hf_cache(self) -> str:
        return self._hf_cache_override or paths.hf_cache_dir()

    @property
    def app_dir(self) -> str:
        return self._app_dir_override or paths.app_dir()

    def spec(self, model_id: str) -> dict:
        for m in self.manifest["models"]:
            if m["id"] == model_id:
                return m
        known = ", ".join(m["id"] for m in self.manifest["models"])
        raise KeyError(f"模型清单里没有 {model_id!r}（清单里有：{known}）")

    def all_specs(self) -> List[dict]:
        return list(self.manifest["models"])

    def _model_roots(self) -> List[str]:
        """模型权重可能待的目录（按优先级）。

        * 程序目录优先 —— 旧版（Web/安装器）把 ``models/`` 放在程序旁边，
          桌面版必须能就地认出来，不能让用户重下；
        * **包内部（``_internal``）也要找** —— 安装包自带的 CAM++ 权重
          是 PyInstaller 的 data，落在 ``_internal/models/`` 里。
          只找程序目录的话会报"声纹模型缺失"（真踩过：打包版
          `--doctor` 说找不到 models/campplus，而它明明在包里）。

        ⚠️ 注入了 ``app_dir`` 时（测试）只用注入的那个，不要再去翻真实的
        程序目录 —— 否则测试会"看到"开发机上真有的模型，用例全乱。
        """
        if self._app_dir_override:
            return [self._app_dir_override]
        return list(paths.resource_roots())

    def _resolve_target(self, rel: str) -> Optional[str]:
        """把清单里的相对路径（``models/campplus/xxx.bin``）解析成真实路径。"""
        rel = rel.replace("\\", "/")
        for root in self._model_roots():
            p = os.path.join(root, *rel.split("/"))
            if os.path.exists(p):
                return p
        return None

    def _target_for_write(self, rel: str) -> str:
        """要写入时用哪个根。

        ⚠️ **永远是程序目录**，不是包内部：``_internal`` 是只读资源区，
        升级/重装会整个替换掉，把 3GB 模型写进去等于白下。
        """
        return os.path.join(self.app_dir, *rel.replace("\\", "/").split("/"))

    def _hf_base(self, spec: dict) -> str:
        """HF 类模型的缓存根。

        Demucs 走 ``torch.hub``，权重在 ``<程序目录>/models/demucs/hub/checkpoints``
        （``separate.py`` 把 ``TORCH_HOME`` 指到那里），**不在** HuggingFace 缓存里，
        所以清单里用 ``hub_dir`` 显式指定。
        """
        hub_dir = (spec.get("source") or {}).get("hub_dir")
        if hub_dir:
            rel = hub_dir.replace("\\", "/").split("/")
            return os.path.join(self.app_dir, *rel)
        return self.hf_cache

    def hf_snapshot_dir(self, cache_key: str, spec: Optional[dict] = None) -> Optional[str]:
        """这个仓库的权重目录（有内容才算）。

        支持两种布局：
        * ``<cache>/models--<org>--<repo>/snapshots/<rev>/``（huggingface_hub）
        * ``<cache>/<cache_key>/`` 或 torch.hub 的 ``checkpoints`` 目录
        """
        base = os.path.join(self._hf_base(spec) if spec else self.hf_cache,
                            cache_key, "snapshots")
        if os.path.isdir(base):
            subs = [os.path.join(base, d) for d in os.listdir(base)]
            subs = [d for d in subs if os.path.isdir(d)]
            if subs:
                # 取文件最多的那个 revision
                subs.sort(key=lambda d: sum(len(f) for _, _, f in os.walk(d)),
                          reverse=True)
                return subs[0]
        direct = os.path.join(self._hf_base(spec) if spec else self.hf_cache,
                              cache_key)
        if os.path.isdir(direct):
            for _r, _d, files in os.walk(direct):
                if files:
                    return direct
        return None

    # ------------------------------------------------------------- 检查
    def _hf_files(self, spec: dict) -> Dict[str, int]:
        snap = self.hf_snapshot_dir(spec["source"]["cache_key"], spec)
        out: Dict[str, int] = {}
        if not snap:
            return out
        for root, _dirs, files in os.walk(snap):
            for f in files:
                p = os.path.join(root, f)
                try:
                    out[os.path.relpath(p, snap)] = os.path.getsize(p)
                except OSError:
                    pass
        return out

    def _hf_size(self, spec: dict) -> int:
        return sum(self._hf_files(spec).values())

    def check(self, model_id: str, use_cache: bool = True) -> ModelState:
        """**这是唯一的判据来源**，UI 与命令行都调它。

        结果只看"文件在不在、字节数对不对"，不做任何网络请求。
        走缓存（原因见 ``__init__``）；要强制重算就 ``use_cache=False``。
        """
        if use_cache:
            hit = self._cache.get(model_id)
            if hit is not None:
                return hit
        st = self._check_uncached(model_id)
        self._cache[model_id] = st
        return st

    def invalidate(self, model_id: Optional[str] = None) -> None:
        """丢掉检查缓存。

        ⚠️ 下载、清理、换目录之后**必须**调，否则会出现
        "明明下完了，界面还说未安装"（缓存里是旧结论）。
        """
        if model_id is None:
            self._cache.clear()
        else:
            self._cache.pop(model_id, None)

    def _check_uncached(self, model_id: str) -> ModelState:
        spec = self.spec(model_id)
        st = ModelState(
            id=spec["id"], name=spec.get("name", spec["id"]),
            category=spec.get("category", ""), purpose=spec.get("purpose", ""),
            note=spec.get("note", ""), default=bool(spec.get("default")),
            policy=spec.get("download_policy", "on_demand"),
            approx_size=int(spec.get("approx_size") or 0),
            license=spec.get("license", ""), spec=spec,
        )
        verify = spec.get("verify") or {}
        kind = verify.get("kind", "")

        if kind == "hf_snapshot":
            files = self._hf_files(spec)
            st.present_bytes = sum(files.values())
            snap = self.hf_snapshot_dir(spec["source"]["cache_key"], spec)
            st.location = snap or ""
            if not files:
                st.status = NOT_INSTALLED
                st.detail = "缓存里没有这个模型"
                return st
            want = verify.get("files") or {}
            missing = [n for n in want if n not in files]
            bad = [f"{n}（应为 {want[n]} 字节，实际 {files.get(n)}）"
                   for n in want if n in files and files[n] != want[n]]
            st.missing, st.size_mismatch = missing, bad
            if missing:
                st.status = FAILED
                st.detail = "缺文件：" + "、".join(missing)
            elif bad:
                st.status = FAILED
                st.detail = "文件不完整：" + "、".join(bad)
            else:
                st.status = READY
                st.detail = "文件齐全，字节数一致"
            return st

        if kind == "hf_presence":
            files = self._hf_files(spec)
            st.present_bytes = sum(files.values())
            snap = self.hf_snapshot_dir(spec["source"]["cache_key"], spec)
            st.location = snap or ""
            need = verify.get("must_have") or []
            missing = [n for n in need if not any(k.endswith(n) for k in files)]
            st.missing = missing
            if not files:
                st.status = NOT_INSTALLED
                st.detail = "缓存里没有这个模型"
            elif missing:
                st.status = FAILED
                st.detail = "缺文件：" + "、".join(missing)
            elif st.present_bytes < 10 * 1024 * 1024:
                st.status = FAILED
                st.detail = f"体积异常（只有 {human(st.present_bytes)}）"
            else:
                st.status = READY
                st.detail = f"文件齐全（{human(st.present_bytes)}）"
            return st

        if kind == "file_sha256":
            rel = verify["target"]
            p = self._resolve_target(rel)
            if not p:
                st.status = NOT_INSTALLED
                st.detail = f"没有找到 {rel}"
                st.can_download = True
                st.download_hint = "点「下载」补齐"
                return st
            st.location = p
            try:
                st.present_bytes = os.path.getsize(p)
            except OSError:
                st.present_bytes = 0
            want_size = verify.get("size")
            if want_size and st.present_bytes != want_size:
                st.status = FAILED
                st.size_mismatch = [f"{os.path.basename(p)} 应为 {want_size} 字节，"
                                    f"实际 {st.present_bytes}"]
                st.detail = "文件大小不对，多半是下载不完整"
                st.can_download = True
                st.download_hint = "重新下载"
                return st
            got = sha256_file(p)
            if got.lower() != str(verify["sha256"]).lower():
                st.status = FAILED
                st.detail = f"SHA256 不匹配（实际 {got[:16]}…）"
                st.can_download = True
                st.download_hint = "重新下载"
                return st
            st.status = READY
            st.detail = f"文件完整（SHA256 已核对，{human(st.present_bytes)}）"
            return st

        if kind == "files_exist":
            rel = verify["target"]
            base = None
            for root in self._model_roots():
                cand = os.path.join(root, *rel.replace("\\", "/").split("/"))
                if os.path.isdir(cand):
                    base = cand
                    break
            need = verify.get("must_have") or []
            if not base:
                st.status = NOT_INSTALLED
                st.detail = f"没有找到 {rel}"
                st.missing = list(need)
                return st
            st.location = base
            missing = [n for n in need if not os.path.isfile(os.path.join(base, n))]
            st.missing = missing
            st.present_bytes = sum(os.path.getsize(os.path.join(r, f))
                                   for r, _d, fs in os.walk(base) for f in fs
                                   if os.path.isfile(os.path.join(r, f)))
            if missing:
                st.status = FAILED
                st.detail = "缺文件：" + "、".join(missing)
            else:
                st.status = READY
                st.detail = "随程序提供，已就位"
            return st

        if kind == "bundled":
            st.status = READY
            st.detail = "随程序提供"
            return st

        st.status = NOT_INSTALLED
        st.detail = f"清单里没写怎么校验（kind={kind!r}）"
        return st

    def check_all(self) -> List[ModelState]:
        return [self.check(m["id"]) for m in self.all_specs()]

    def report(self) -> dict:
        states = self.check_all()
        ready = [s for s in states if s.status == READY]
        missing = [s for s in states if s.status != READY]
        return {
            "hf_cache": self.hf_cache,
            "manifest": self.manifest_path,
            "total": len(states),
            "ready": len(ready),
            "missing": len(missing),
            "models": [s.public() for s in states],
            "asr_ready": any(s.category == "asr" and s.status == READY for s in states),
        }

    # ------------------------------------------------------------- 下载
    def download(self, model_id: str, log: Callable[[str], None] = print,
                 progress: Optional[Callable[[Optional[float], str], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None) -> ModelState:
        """下载 + 校验。返回**校验后**的状态（失败会抛异常）。"""
        spec = self.spec(model_id)
        src = spec.get("source") or {}
        kind = src.get("kind")
        p = progress or (lambda pct, msg: None)
        c = cancel or (lambda: False)
        # 下载前后都清缓存：下载过程中界面会一直刷，"开始前"那次旧结论
        # 和"结束后"的新结论必须都重算
        self.invalidate(model_id)

        def _stop():
            if c():
                raise DownloadCancelled()

        try:
            if kind == "bundled":
                return self.check(model_id)
            if kind == "hf":
                return self._download_hf(spec, log, p, _stop)
            if kind == "http":
                return self._download_http(spec, log, p, _stop)
            raise ValueError(f"{model_id}: 不知道怎么下载（kind={kind!r}）")
        finally:
            self.invalidate(model_id)

    # -------------------------------------------------------------- HF
    def _download_hf(self, spec: dict, log, p, stop) -> ModelState:
        repo = spec["source"]["repository"]
        hub = self.hf_cache
        paths.ensure_dir(hub)
        os.environ["HUGGINGFACE_HUB_CACHE"] = hub
        log(f"模型仓库：{repo}")
        log(f"缓存目录：{hub}")

        have = self._hf_size(spec)
        if have:
            log(f"缓存里已有 {human(have)}，尝试断点续传")

        from huggingface_hub import snapshot_download  # 延迟导入（很重）

        last_err = None
        t0 = time.time()
        for ep in HF_ENDPOINTS:
            stop()
            os.environ["HF_ENDPOINT"] = ep
            log(f"尝试端点：{ep}")
            p(None, f"连接 {ep} …")
            try:
                snapshot_download(repo_id=repo, resume_download=True)
                break
            except Exception as e:                     # noqa: BLE001
                last_err = e
                log(f"[失败] {type(e).__name__}: {str(e)[:200]}")
        else:
            raise RuntimeError(
                f"两个端点都下载失败，最后错误：{type(last_err).__name__}: "
                f"{str(last_err)[:200]}。可以挂代理后重试（支持断点续传），"
                f"或手动把已有模型目录放进 {hub}")

        log(f"下载完成，用时 {time.time() - t0:.0f} 秒，开始校验")
        p(None, "校验文件完整性…")
        st = self.check(spec["id"])
        if st.status != READY:
            raise RuntimeError(f"下载完成但校验没过：{st.detail}")
        return st

    # ------------------------------------------------------------ 直链 HTTP
    def _download_http(self, spec: dict, log, p, stop) -> ModelState:
        verify = spec.get("verify") or {}
        rel = verify["target"]
        dst = self._target_for_write(rel)
        tmp = dst + ".part"
        paths.ensure_dir(os.path.dirname(dst))

        urls = list(spec["source"].get("urls") or [])
        if not urls:
            raise ValueError(f"{spec['id']}: 没有下载地址")

        last_err = None
        for url in urls:
            stop()
            log(f"尝试地址：{url.split('/')[2]}")
            try:
                got = _http_get_to_file(url, tmp, log=log, p=p, stop=stop,
                                        label=os.path.basename(dst))
            except DownloadCancelled:
                raise
            except Exception as e:                     # noqa: BLE001
                last_err = e
                log(f"[失败] {type(e).__name__}: {str(e)[:160]}")
                continue
            stop()
            # ---- 校验临时文件，通过才上位 ----
            p(None, "校验下载结果…")
            size = verify.get("size")
            if size and got != size:
                log(f"[校验失败] 字节数不对：应为 {size}，实际 {got}")
                _safe_remove(tmp)
                last_err = RuntimeError(f"字节数不对（{got} != {size}）")
                continue
            if verify.get("sha256"):
                log("计算 SHA256 …")
                digest = sha256_file(tmp, progress=lambda d, t: p(
                    (d * 100.0 / t) if t else None, "校验中"))
                if digest.lower() != str(verify["sha256"]).lower():
                    log(f"[校验失败] SHA256 不匹配：{digest[:16]}…")
                    _safe_remove(tmp)
                    last_err = RuntimeError("SHA256 不匹配")
                    continue
                log("SHA256 一致 ✓")
            atomic_replace(tmp, dst)
            log(f"已就位：{dst}")
            st = self.check(spec["id"])
            if st.status != READY:
                raise RuntimeError(f"落盘后校验仍不过：{st.detail}")
            return st

        # ⚠️ **失败时千万不要删掉 .part** —— 那是"已下好的那部分"，
        # 断点续传全靠它。第一版这里写了 `_safe_remove(tmp)`，
        # 结果"下载中断→重试"永远从 0 开始，3GB 的模型能白下好几次。
        # （真踩过；`.tmp/t_http_dl.py` 的"断点续传"用例就是守这条的。）
        # 只有"校验不通过"时才删 —— 那种 .part 的内容是**错的**，
        # 留着只会让下一次继续拼出一个永远过不了校验的文件。
        raise RuntimeError(
            f"所有下载地址都失败了（最后：{type(last_err).__name__}: "
            f"{str(last_err)[:160]}）。已下好的部分保留在 {tmp}，"
            f"重试会**接着下**。")

    # ------------------------------------------------------------- 清理
    def cleanup(self, model_id: str, log: Callable[[str], None] = print) -> ModelState:
        """清掉这个模型（用户明确要求时才调）。"""
        spec = self.spec(model_id)
        src = spec.get("source") or {}
        if src.get("kind") == "hf":
            base = self._hf_base(spec)
            d = os.path.join(base, src["cache_key"])
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
                log(f"已删除 {d}")
            else:
                log(f"没找到 {d}（可能已经被删过）")
        elif src.get("kind") == "http":
            t = self._resolve_target((spec.get("verify") or {})["target"])
            if t:
                _safe_remove(t)
                log(f"已删除 {t}")
            else:
                log("没找到文件（可能已经被删过）")
        else:
            log(f"{model_id} 是随程序提供的，不能单独清理")
        self.invalidate(model_id)
        return self.check(model_id)

    def missing_summary(self) -> str:
        miss = [s for s in self.check_all() if s.status != READY]
        if not miss:
            return "全部就绪"
        return "、".join(f"{s.name}（{STATUS_LABEL.get(s.status, s.status)}）"
                        for s in miss)


class DownloadCancelled(Exception):
    """用户取消了下载。"""


def _safe_remove(p: str) -> None:
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


def _http_get_to_file(url: str, dst: str, log=print, p=None, stop=None,
                      label: str = "", retries: int = 3) -> int:
    """下载到 ``dst``，**支持断点续传**（用 HTTP Range）。

    返回最终字节数。中途失败会保留 ``.part``，下一次接着下。
    """
    import urllib.error
    import urllib.request

    p = p or (lambda pct, msg: None)
    stop = stop or (lambda: False)
    have = os.path.getsize(dst) if os.path.isfile(dst) else 0

    last = None
    for attempt in range(1, retries + 1):
        stop()
        req = urllib.request.Request(url, headers={
            "User-Agent": "Lyric2TTML/desktop",
            "Accept": "*/*",
        })
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                code = getattr(r, "status", 200)
                if have and code != 206:
                    # 服务器不支持续传：**从头来**。
                    # ⚠️ 这里必须把已下的那截丢掉：不能拿 "ab" 接着写，
                    # 否则文件里会出现"前半段重复两次"的畸形内容，
                    # 最后只有 SHA256 对不上（用户看到的是"校验失败"，
                    # 而真正的原因是拼接错位）。
                    log(f"服务器不支持断点续传（HTTP {code}），从头下载")
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                    have = 0
                clen = int(r.headers.get("Content-Length") or 0)
                total = (have + clen) if clen else 0
                mode = "ab" if have else "wb"
                got = have
                t0 = time.time()
                last_tick = 0.0
                with open(dst, mode) as fh:
                    while True:
                        stop()
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        now = time.time()
                        if now - last_tick > 0.4:
                            last_tick = now
                            rate = got / max(0.001, now - t0) / 1024
                            if total:
                                p(got * 100.0 / total,
                                  f"{label} {got*100//total}% "
                                  f"({human(got)}/{human(total)}, {rate:.0f} KB/s)")
                            else:
                                p(None, f"{label} {human(got)} ({rate:.0f} KB/s)")
                if total and got < total:
                    raise IOError(f"连接中断：{got}/{total} 字节")
                return got
        except DownloadCancelled:
            raise
        except (urllib.error.URLError, OSError, IOError) as e:
            last = e
            log(f"第 {attempt}/{retries} 次失败：{type(e).__name__}: {str(e)[:120]}")
            have = os.path.getsize(dst) if os.path.isfile(dst) else 0
            if attempt < retries:
                time.sleep(min(2.0 * attempt, 5.0))
    raise IOError(f"下载失败：{type(last).__name__}: {str(last)[:160]}")


# --------------------------------------------------------------- CLI（自测用）

def _cli(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="模型管理器（检查 / 下载 / 清理）")
    ap.add_argument("action", choices=["list", "check", "download", "cleanup", "json"])
    ap.add_argument("model", nargs="?", default="")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--hf-cache", default="")
    args = ap.parse_args(argv)

    mm = ModelManager(manifest_path=args.manifest or None,
                      hf_cache=args.hf_cache or None)
    if args.action == "json":
        print(json.dumps(mm.report(), ensure_ascii=False, indent=2))
        return 0
    if args.action in ("list", "check"):
        for s in mm.check_all():
            flag = {"ready": "✓", "not_installed": "·"}.get(s.status, "!")
            print(f"  [{flag}] {s.id:22} {STATUS_LABEL.get(s.status, s.status):6} "
                  f"{s.detail}")
        return 0
    if not args.model:
        print("要指定模型 id")
        return 2
    if args.action == "download":
        st = mm.download(args.model, log=print,
                         progress=lambda pct, msg: print(f"    {msg}"))
        print(f"  → {st.status} {st.detail}")
        return 0 if st.status == READY else 1
    if args.action == "cleanup":
        st = mm.cleanup(args.model, log=print)
        print(f"  → {st.status} {st.detail}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
