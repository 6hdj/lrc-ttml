"""桌面应用离线自检（不开窗口、不联网、不用大模型）。

    python desktop/selftest.py            # 全部
    python desktop/selftest.py 3          # 只跑第 3 组
    python desktop/selftest.py -v         # 打印每组细节

被测对象是**不依赖 Qt 的那一层**（paths / config / task_manager /
model_manager / convert_params / environment / log_service / workers）。
界面层靠 ``desktop/uicheck.py`` 单独验（能在无显示环境下跑）。

为什么必须离线：这三件事最容易悄悄坏掉，而它们坏了以后
"装完能打开、一点就崩"：
  1. 路径解析（冻结后 ``__file__`` 指向 ``_internal``，模型被下到包内部）
  2. 参数映射（界面调了、后端没收到 —— 加一个参数就漏一条入口）
  3. 任务状态机（取消/失败/重启恢复）
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---- 关键：把可变数据目录指到临时目录，别污染真实用户目录 ----
_SANDBOX = os.path.join(_ROOT, ".tmp", "desktop-selftest")
shutil.rmtree(_SANDBOX, ignore_errors=True)
os.makedirs(_SANDBOX, exist_ok=True)
os.environ["L2T_DATA_DIR"] = _SANDBOX
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS, FAIL = [], []
VERBOSE = "-v" in sys.argv
ONLY = [a for a in sys.argv[1:] if a.isdigit()]


def group(n: int, title: str):
    def deco(fn):
        fn._group = n
        fn._title = title
        return fn
    return deco


def ok(cond, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "断言失败")


def eq(a, b, msg: str = "") -> None:
    if a != b:
        raise AssertionError(f"{msg}：期望 {b!r}，实际 {a!r}")


# ==================================================================== 1 路径

@group(1, "paths：程序目录 / 数据目录 / 模型缓存复用")
def t_paths():
    from desktop import paths

    eq(paths.data_dir(), _SANDBOX, "L2T_DATA_DIR 必须被尊重")
    # ⚠️ 这一条**不能写死**（2026-10-16 冻结版实测踩到）：
    # 原来硬断言 `frozen() == False`，但冻结版 `--selftest` 里它**本来就是 True**，
    # 于是第 1 组在打包版永远失败（`AssertionError: 源码模式不该是 frozen`）。
    # 正确的断言是"**与运行环境一致**"：冻结 exe ⇒ True；源码 python ⇒ False。
    eq(paths.frozen(), bool(getattr(sys, "frozen", False)),
       f"frozen() 与实际运行环境不符（sys.frozen={getattr(sys, 'frozen', False)}）")
    eq(paths.portable(), False, "没放 portable.txt 就不是便携模式")

    # 所有子目录都必须落在数据目录下（不能跑到 Program Files / 仓库根）
    for name, p in (("config", paths.config_dir()), ("logs", paths.logs_dir()),
                    ("projects", paths.projects_dir()), ("temp", paths.temp_dir()),
                    ("state", paths.state_dir()), ("models", paths.models_dir())):
        ok(os.path.isdir(p), f"{name} 目录没建出来：{p}")
        ok(p.startswith(paths.data_dir()),
           f"{name} 跑到数据目录外面了：{p}")

    # 数据目录必须可写（否则转换结果存不下）
    probe = os.path.join(paths.data_dir(), "probe.txt")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("hi")
    os.remove(probe)

    # 模型缓存：程序目录下有内容时要**就地复用**（用户升级后不用重下 3GB）
    legacy = paths.legacy_hf_cache()
    ok(legacy.endswith(".hf_cache"), f"旧版缓存路径不对：{legacy}")
    # ⚠️ 这里**不能再用模块级 `_ROOT`**（2026-10-16 冻结版实测踩到）：
    # `_ROOT` 是"包内目录"（源码 = 仓库根；冻结 = `_internal` 的上一级），
    # 而这条断言要验的是**程序目录**（`app_dir()`）下的 `.hf_cache` 在不在探测列表里。
    # 冻结版两者不是一回事 ⇒ 该组在打包版永远失败：
    # `AssertionError: 探测列表里必须包含旧版缓存位置`。
    # `legacy_hf_cache()` 本来就定义为"程序目录/.hf_cache"，直接用它，两种模式都对，
    # 且检查强度不变（仍然是"必须在这个列表里"）。
    real = legacy
    if os.path.isdir(real):
        eq(paths.hf_cache_dir(), real,
           "程序目录下已有 .hf_cache 时必须复用，不能让用户重下")
    ok(real in paths.hf_cache_roots(), "探测列表里必须包含旧版缓存位置")

    d = paths.describe()
    for k in ("app_dir", "data_dir", "config", "hf_cache", "portable"):
        ok(k in d, f"describe() 少字段 {k}")

    # apply_env 之后，asr 模块读到的一定是同一个目录
    hub = paths.apply_env()
    eq(os.environ["HUGGINGFACE_HUB_CACHE"], hub, "HF 缓存环境变量没对齐")

    # default_workspace：源码模式 = 仓库根（产物能直接看到）；安装版 = `<数据目录>`
    # ⚠️ 原来写死 `eq(paths.default_workspace(), _ROOT, "源码模式的默认工作目录应是仓库根")`，
    # 冻结版必然失败（`_ROOT` 是包内的 `_internal`）。2026-10-16 冻结版实测报
    # `期望 '<exe>/_internal'，实际 '<exe>'`。
    # 按 `paths.default_workspace()` 自己的契约分环境断言；**"可写"这条真实要求不放松**
    # （工作目录不可写 = 转换结果存不下，这才是这个函数要保证的东西）。
    _ws = paths.default_workspace()
    ok(os.path.isdir(_ws), f"默认工作目录不存在：{_ws}")
    if getattr(sys, "frozen", False):
        ok(_ws in (paths.app_dir(), paths.data_dir()),
           f"冻结版的默认工作目录应是程序目录或数据目录，实际 {_ws}")
    else:
        eq(_ws, _ROOT, "源码模式的默认工作目录应是仓库根")
    _probe2 = os.path.join(_ws, "probe_ws.txt")
    with open(_probe2, "w", encoding="utf-8") as fh:
        fh.write("hi")
    os.remove(_probe2)

    # ---- 打包版的"资源在哪"（这条是真踩出来的）----
    # 冻结时 app_dir() = exe 旁边，_source_dir() = PyInstaller 的 _internal，
    # **两者不是同一个地方**。安装包自带的 models/ 落在 _internal 里，
    # 只找 exe 旁边的话会报"声纹模型缺失"（打包版 --doctor 就这么错过一次）。
    roots = paths.resource_roots()
    ok(roots, "resource_roots() 不能为空")
    eq(roots[0], paths.app_dir(), "程序目录应该排第一（用户自己的模型优先）")
    ok(paths._source_dir() in roots,
       f"资源目录里必须包含包内部 {paths._source_dir()}")
    ok(len(roots) == len(set(roots)), "资源目录有重复")

    # 模型管理器必须用同一套根（否则打包版找不到自带权重）
    from desktop.services.model_manager import ModelManager
    mm = ModelManager()
    eq(mm._model_roots(), roots, "ModelManager 的模型根与 resource_roots() 不一致")
    # 写入永远去程序目录，**不能**写进 _internal
    w = mm._target_for_write("models/campplus/x.bin")
    ok(w.startswith(paths.app_dir()), f"写入目标跑出程序目录了：{w}")


# ================================================================ 2 配置

@group(2, "config：读写 / 坏文件降级 / 未知键保留 / 原子写")
def t_config():
    from desktop import config, paths

    p = os.path.join(_SANDBOX, "cfg", "settings.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.isfile(p):
        os.remove(p)

    c = config.Config(path=p)
    eq(c.loaded_from, "defaults", "没有文件时应是 defaults")
    eq(c.get("model"), "large-v3")

    c.set("model", "small")
    c.set("custom_future_key", {"a": 1})        # 未来版本/用户自己加的键
    c.save()
    ok(os.path.isfile(p), "save() 没写出文件")

    c2 = config.Config(path=p)
    eq(c2.loaded_from, "disk")
    eq(c2.get("model"), "small", "值没读回来")
    eq(c2.get("custom_future_key"), {"a": 1}, "未知键被丢掉了")

    # 坏 JSON：不能抛异常，要降级 + 留 .bak
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    c3 = config.Config(path=p)
    ok(c3.load_error, "坏文件应该记下错误")
    eq(c3.get("model"), "large-v3", "坏文件时要用默认值")
    ok(os.path.isfile(p + ".bak"), "坏文件应该留一份 .bak")

    # 原子写：不能留下 .tmp
    c3.set("model", "medium")
    c3.save()
    ok(not os.path.isfile(p + ".tmp"), "原子写之后不该残留 .tmp")
    eq(config.Config(path=p).get("model"), "medium")

    # 配置文件必须落在 data_dir 下（不能写到仓库根或 Program Files）
    ok(config.Config().path.startswith(paths.data_dir()),
       f"默认配置路径应在数据目录下：{config.Config().path}")


# ========================================================= 3 任务中心基础

@group(3, "task_manager：提交 / 进度 / 结果 / 回调")
def t_task_basic():
    from desktop.services.task_manager import DONE, TaskManager

    mgr = TaskManager(persist=False)
    seen = []
    mgr.add_listener(lambda t: seen.append((t["id"], t["state"], t["progress"])))

    def job(h):
        h.log("开始")
        h.progress(10, "第一步")
        h.progress(50, "第二步")
        h.set_result(foo="bar", n=3)
        return {"extra": 1}

    tid = mgr.submit("test", "测试任务", job)
    t = _wait(mgr, tid)
    eq(t.state, DONE, "任务应为完成")
    eq(t.progress, 100.0, "完成时进度应为 100")
    eq(t.result.get("foo"), "bar", "set_result 没生效")
    eq(t.result.get("extra"), 1, "worker 返回的 dict 应并进结果")
    ok(any(l["m"] == "开始" for l in t.logs), "日志没记下来")
    ok(any(l["m"] == "第一步" for l in t.logs), "进度说明没进日志？")
    ok(len(seen) >= 4, f"监听器回调太少：{len(seen)}")
    ok(t.elapsed() >= 0)
    d = t.public()
    for k in ("id", "state", "progress", "message", "running", "terminal"):
        ok(k in d, f"public() 少字段 {k}")


# ============================================================== 4 失败与取消

@group(4, "task_manager：失败分类 / 取消（排队中立刻取消）")
def t_task_fail_cancel():
    from desktop.services.task_manager import (CANCELLED, FAILED, TaskCancelled,
                                               TaskManager)

    mgr = TaskManager(persist=False)

    def boom(h):
        h.log("要炸了")
        raise ValueError("故意的")

    tid = mgr.submit("test", "会失败的任务", boom)
    t = _wait(mgr, tid)
    eq(t.state, FAILED, "抛异常应为失败")
    ok("故意的" in (t.error or ""), f"错误信息没带上：{t.error}")
    ok(t.ended, "失败也要有结束时间")

    # worker 主动抛 TaskCancelled → 状态是"已取消"，不是"失败"
    def cancel_self(h):
        raise TaskCancelled()

    tid2 = mgr.submit("test", "自我取消", cancel_self)
    t2 = _wait(mgr, tid2)
    eq(t2.state, CANCELLED, "TaskCancelled 应变成已取消，而不是失败")

    # 排队中等一会儿的任务，cancel() 要**立刻**变已取消
    import threading
    gate = threading.Event()

    def slow(h):
        gate.wait(5)
        return {}

    tid3 = mgr.submit("test", "慢任务", slow)
    time.sleep(0.05)
    ok(mgr.cancel(tid3), "cancel() 应返回 True")
    t3 = mgr.get(tid3)
    ok(t3.cancel_requested, "cancel_requested 没置上")
    ok(t3.state in ("running", "cancelled"), f"状态异常：{t3.state}")
    gate.set()
    _wait(mgr, tid3)
    eq(mgr.get(tid3).state, CANCELLED, "取消后不该变成完成")


# =========================================================== 5 任务持久化

@group(5, "task_manager：持久化 / 重启后不装死")
def t_task_persist():
    from desktop.services.task_manager import DONE, FAILED, TaskManager

    p = os.path.join(_SANDBOX, "state", "tasks.json")
    mgr = TaskManager(state_path=p)
    tid = mgr.submit("test", "会被记住的任务", lambda h: {"v": 42})
    t = _wait(mgr, tid)
    eq(t.state, DONE)
    mgr.flush()
    ok(os.path.isfile(p), "任务状态没落盘")
    data = json.load(open(p, encoding="utf-8"))
    ok(any(x["id"] == tid for x in data["tasks"]), "落盘里没有这个任务")

    # 模拟"程序被强杀"：手写一个 running 状态进去
    data["tasks"].append({"id": "deadbeef", "kind": "convert", "title": "跑到一半",
                          "state": "running", "progress": 40})
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)

    mgr2 = TaskManager(state_path=p)
    dead = mgr2.get("deadbeef")
    ok(dead is not None, "重启后应该还能看到历史任务")
    eq(dead.state, FAILED, "重启后『进行中』的任务必须收成已中断，不能永远转圈")
    ok(dead.error, "中断的任务要有原因")
    ok(mgr2.get(tid) is not None, "已完成的任务也该恢复出来")

    # 坏的状态文件不能让程序起不来
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("not json at all")
    mgr3 = TaskManager(state_path=p)
    eq(mgr3.list(), [], "坏状态文件时应该是空列表而不是异常")


# ========================================================= 6 模型清单与判据

@group(6, "model_manager：清单结构 / 判据 / 不联网也能查")
def t_model_manifest():
    from desktop.services import model_manager as mm_mod

    mm = mm_mod.ModelManager()
    specs = mm.all_specs()
    ok(len(specs) >= 5, f"模型太少：{len(specs)}")
    ids = [s["id"] for s in specs]
    eq(len(ids), len(set(ids)), "模型 id 有重复")

    for s in specs:
        for k in ("id", "category", "name", "purpose", "download_policy",
                  "source", "verify"):
            ok(k in s, f"{s.get('id')} 缺字段 {k}")
        ok(s["download_policy"] in ("first_run", "on_demand", "bundled"),
           f"{s['id']} 的 download_policy 不认识：{s['download_policy']}")
        ok(s["source"].get("kind") in ("hf", "http", "bundled"),
           f"{s['id']} 的 source.kind 不认识：{s['source'].get('kind')}")
        ok(s.get("license"), f"{s['id']} 没写许可信息")

    # 基础功能必须有默认可用的 ASR 模型
    defaults = [s for s in specs if s.get("default") and s["category"] == "asr"]
    ok(defaults, "至少要有一个默认 ASR 模型")

    # 状态判据齐全
    for st in mm.check_all():
        ok(st.status in (mm_mod.NOT_INSTALLED, mm_mod.READY, mm_mod.FAILED),
           f"{st.id} 状态异常：{st.status}")
        ok(st.detail, f"{st.id} 没有说明文字")
        ok(st.status in mm_mod.STATUS_LABEL, f"{st.id} 状态没中文名")
    rep = mm.report()
    ok("asr_ready" in rep and "models" in rep, "report() 字段不全")

    # 清单里承诺的 SHA256 必须格式合法（32 字节十六进制）
    for s in specs:
        v = s.get("verify") or {}
        if v.get("kind") == "file_sha256":
            h = str(v.get("sha256") or "")
            eq(len(h), 64, f"{s['id']} SHA256 长度不对")
            ok(all(c in "0123456789abcdef" for c in h.lower()),
               f"{s['id']} SHA256 不是十六进制")


# ========================================================= 7 模型校验行为

@group(7, "model_manager：SHA256 校验真的会拒绝坏文件")
def t_model_verify():
    from desktop.services import model_manager as mm_mod

    fake_app = os.path.join(_SANDBOX, "fakeapp")
    fake_hub = os.path.join(_SANDBOX, "fakehub")
    os.makedirs(fake_app, exist_ok=True)
    os.makedirs(fake_hub, exist_ok=True)

    mm = mm_mod.ModelManager(hf_cache=fake_hub, app_dir=fake_app)

    # 自造一个"直链下载"的模型，验证 大小 + SHA256 两条判据
    rel = "models/campplus/campplus_cn_common.bin"
    target = os.path.join(fake_app, "models", "campplus",
                          "campplus_cn_common.bin")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    body = b"hello-campplus" * 1000
    import hashlib
    digest = hashlib.sha256(body).hexdigest()

    spec = {"id": "probe", "category": "x", "name": "探针",
            "download_policy": "on_demand",
            "source": {"kind": "http", "urls": ["http://127.0.0.1:1/x"]},
            "verify": {"kind": "file_sha256", "target": rel,
                       "sha256": digest, "size": len(body)}}
    mm.manifest = {"schema_version": 1, "models": [spec]}

    st = mm.check("probe")
    eq(st.status, mm_mod.NOT_INSTALLED, "文件还没放就该是未安装")

    with open(target, "wb") as fh:                    # 正确的文件
        fh.write(body)
    mm.invalidate("probe")                            # 模拟"下载完之后界面重查"
    st = mm.check("probe")
    eq(st.status, mm_mod.READY, f"内容正确却没通过：{st.detail}")
    ok("SHA256" in st.detail, "应该说明核过 SHA256")

    with open(target, "wb") as fh:                    # 被截断
        fh.write(body[: len(body) // 2])
    mm.invalidate("probe")
    st = mm.check("probe")
    eq(st.status, mm_mod.FAILED, "大小不对必须判失败（不能只看文件存在）")
    ok("大小" in st.detail or "字节" in st.detail, f"说明不清楚：{st.detail}")

    with open(target, "wb") as fh:                    # 大小对、内容错
        fh.write(b"x" * len(body))
    mm.invalidate("probe")
    st = mm.check("probe")
    eq(st.status, mm_mod.FAILED, "SHA256 不符必须判失败")

    # 半截的 .part 文件**绝不算**已安装
    part = target + ".part"
    with open(part, "wb") as fh:
        fh.write(body)
    os.remove(target)
    mm.invalidate("probe")
    st = mm.check("probe")
    eq(st.status, mm_mod.NOT_INSTALLED, ".part 临时文件不能被当成已安装")
    os.remove(part)

    # 缓存不能"自己骗自己"：新建一个管理器（= 用户重开软件）必须给出同样的结论
    fake_manifest = os.path.join(_SANDBOX, "probe_manifest.json")
    with open(fake_manifest, "w", encoding="utf-8") as fh:
        json.dump({"schema_version": 1, "models": [spec]}, fh,
                  ensure_ascii=False)
    st2 = mm_mod.ModelManager(manifest_path=fake_manifest, hf_cache=fake_hub,
                              app_dir=fake_app).check("probe")
    eq(st2.status, mm_mod.NOT_INSTALLED, "新实例的结论与旧实例不一致")

    # HF 快照：字节数对不上要报出来
    snap = os.path.join(fake_hub, "models--Fake--Repo", "snapshots", "rev1")
    os.makedirs(snap, exist_ok=True)
    with open(os.path.join(snap, "config.json"), "wb") as fh:
        fh.write(b"{}")
    with open(os.path.join(snap, "model.bin"), "wb") as fh:
        fh.write(b"0" * 10)
    spec2 = {"id": "hfprobe", "category": "asr", "name": "HF 探针",
             "download_policy": "on_demand",
             "source": {"kind": "hf", "repository": "Fake/Repo",
                        "cache_key": "models--Fake--Repo"},
             "verify": {"kind": "hf_snapshot",
                        "files": {"config.json": 2, "model.bin": 999}}}
    mm.manifest = {"schema_version": 1, "models": [spec2]}
    mm.invalidate()
    st = mm.check("hfprobe")
    eq(st.status, mm_mod.FAILED, "model.bin 字节数不对必须判失败")
    ok(st.size_mismatch, "应该把不一致的文件列出来")

    # 缺文件也要报
    os.remove(os.path.join(snap, "model.bin"))
    mm.invalidate()
    st = mm.check("hfprobe")
    eq(st.status, mm_mod.FAILED, "缺文件必须判失败")
    ok(st.missing, "应该列出缺了哪个文件")


# ============================================================= 8 参数映射

@group(8, "convert_params：字段映射与 ConvertOptions 不漂移")
def t_params():
    from desktop.services import convert_params as cp

    drift = cp.field_drift()
    eq(drift["unknown"], [],
       f"这些字段 ConvertOptions 里已经没有了（改名/删了？）：{drift['unknown']}")
    eq(drift["missing"], [],
       f"ConvertOptions 有这些字段但我们没映射，用户设了不会生效：{drift['missing']}")

    o = cp.build_options({"model": "small", "singer_mode": "2", "language": "auto",
                          "refine": "false", "pause_space": "0.35"})
    eq(o.model, "small")
    eq(o.singer_mode, "2")
    eq(o.forced_singers(), 2, "强制 2 声部没解析出来")
    eq(o.language, "auto")
    eq(o.refine, False, "字符串 'false' 应该转成 False")
    eq(o.pause_space, 0.35)

    # 空 params 也要能构造（界面上什么都没选时）
    o2 = cp.build_options({})
    eq(o2.model, "large-v3", "空参数时要用 ConvertOptions 自己的默认值")
    eq(o2.mode, "amll")

    # 平台 ID 从 ttml_writer.META_KEYS 取，不另抄一份
    from ttml_writer import META_KEYS
    eq(list(META_KEYS).count("isrc"), 1, "isrc 重复了（提示词里也出现过这个坑）")
    ids = cp.platform_ids({"qqMusicId": " 123 ", "kuwoMusicId": "456",
                           "ncmMusicId": "", "isrc": "USX1"})
    eq(ids.get("qqMusicId"), "123", "平台 ID 应该去空格")
    eq(ids.get("kuwoMusicId"), "456")
    ok("ncmMusicId" not in ids, "空 ID 不该带上")
    ok("isrc" in ids, "isrc 也要带上")
    ok(all(k in list(META_KEYS) + ["isrc"] for k in ids),
       "出现了写器不认识的关键字")
    # 界面上每个平台 ID 都必须写进文件（"填了没用"是最难查的一类问题）
    for k in META_KEYS:
        got = cp.platform_ids({k: "X"})
        ok(k in got, f"平台 ID {k} 没被带上（界面填了也写不进文件）")

    # 艺人多分隔符
    eq(cp.artists_list({"artists": "陶典、hanser, HOYO-MiX；爆裂菊是也"}),
       ["陶典", "hanser", "HOYO-MiX", "爆裂菊是也"])

    # 参数校验：不存在的文件要报出来，且**不能**只说一句"失败"
    probs = cp.validate({})
    ok(len(probs) >= 2, f"空参数至少报两条问题：{probs}")
    probs2 = cp.validate({"audio": __file__, "lyrics": os.path.join(_ROOT, "main.py")})
    ok(any("只支持" in p for p in probs2), f"非 txt/lrc 应该拦下来：{probs2}")

    # GBK 歌词必须能读（中文 Windows 上的 .lrc 大量是 GBK）
    lrc = os.path.join(_SANDBOX, "gbk.lrc")
    with open(lrc, "wb") as fh:
        fh.write("[00:01.00]测试歌词\n".encode("gbk"))
    text, enc, dirty = cp.read_lyrics(lrc)
    ok("测试歌词" in text, f"GBK 歌词没读对：{text!r}（enc={enc}）")
    ok(not dirty, f"GBK 是正常编码，不该标可疑：{enc} {dirty}")


# ============================================================= 9 环境体检

@group(9, "environment_service：条目结构 / 判据来源统一")
def t_env():
    from desktop.services import environment_service as es
    from desktop.services import model_manager as mm_mod

    items = es.quick_check()
    ok(len(items) >= 5, f"检查项太少：{len(items)}")
    keys = [i["key"] for i in items]
    eq(len(keys), len(set(keys)), f"检查项 key 有重复：{keys}")
    for i in items:
        for k in ("key", "name", "ok", "level", "detail", "fix", "fix_action"):
            ok(k in i, f"{i.get('key')} 缺字段 {k}")
        ok(i["level"] in ("ok", "warn", "error", "info"),
           f"{i['key']} level 不认识：{i['level']}")
        ok(isinstance(i["ok"], bool), f"{i['key']} 的 ok 必须是 bool")

    rep = es.summarize(items)
    for k in ("total", "ok", "errors", "warnings", "ready", "headline", "items"):
        ok(k in rep, f"summarize() 少字段 {k}")
    eq(rep["total"], len(items))

    # 模型项必须与 ModelManager 的判据一致（不能两套说法）
    mm = mm_mod.ModelManager()
    model_items = [i for i in items if i["key"].startswith("model:")]
    eq(len(model_items), len(mm.all_specs()), "模型项数量对不上清单")
    for i in model_items:
        mid = i["key"].split(":", 1)[1]
        st = mm.check(mid)
        eq(i["ok"], st.status == mm_mod.READY,
           f"{mid} 环境页与模型页结论不一致")

    # 重活（GPU）必须在 full_check 里，不能在 quick_check 里
    ok(not any(i["key"] == "gpu" for i in items),
       "quick_check 不能碰 GPU（要 import torch，会卡界面）")
    full = es.full_check(mm)
    ok(any(i["key"] in ("gpu", "cuda") for i in full),
       "full_check 必须包含 GPU / CUDA 项")
    gpu = [i for i in full if i["key"] == "gpu"][0]
    ok(gpu["detail"], "GPU 项必须有说明")

    # 这条机器上 GPU 是好的，就应该判 ok（顺带验证探测真的在做实事）
    if gpu["ok"]:
        ok(gpu["detail"], "GPU 项的说明不能是空的")
        print("      GPU：", gpu["detail"][:100])


# ================================================================ 10 日志

@group(10, "log_service：文件 + 内存缓冲 + 崩溃兜底")
def t_log():
    from desktop.services import log_service as ls

    log = ls.setup()
    ls.clear()
    log.info("自检写入的一条日志 %s", 12345)
    snap = ls.snapshot()
    ok(any("自检写入的一条日志 12345" in s["m"] for s in snap),
       f"内存缓冲里没有：{snap[-3:]}")
    ok(os.path.isfile(ls.paths.log_path()), "日志文件没建出来")
    with open(ls.paths.log_path(), encoding="utf-8") as fh:
        body = fh.read()
    ok("自检写入的一条日志 12345" in body, "日志没写进文件")

    # 环形缓冲要有上限（否则跑一晚上内存就没了）
    ls.clear()
    for i in range(ls.MAX_LINES + 50):
        log.info("刷屏 %d", i)
    eq(len(ls.snapshot(limit=10 ** 6)), ls.MAX_LINES, "环形缓冲没截断")

    ok("Traceback" in ls.crash_text(ValueError("x")) or
       "ValueError" in ls.crash_text(ValueError("x")), "crash_text 不像 traceback")


# ============================================================== 11 workers

@group(11, "workers：转换参数校验 / 模型下载任务接线")
def t_workers():
    from desktop.services import task_manager as tm
    from desktop.workers import Workers

    mgr = tm.TaskManager(persist=False)
    w = Workers(mgr)

    # 参数不全时**必须立刻给出人话错误**，而不是跑一半崩在流水线里
    tid = w.start_convert({})
    t = _wait(mgr, tid)
    eq(t.state, tm.FAILED, "参数不全应失败")
    ok("音频" in (t.error or "") or "歌词" in (t.error or ""),
       f"错误信息应该指出缺什么：{t.error}")
    ok("失败" in (t.message or "") or t.error, "失败任务要有可读信息")

    # 模型下载任务：用一个不存在的 id 也要给出清楚错误，而不是静默
    tid2 = w.start_model_download("no-such-model")
    t2 = _wait(mgr, tid2)
    eq(t2.state, tm.FAILED)
    ok("no-such-model" in (t2.error or ""), f"错误里要带模型 id：{t2.error}")

    # 环境体检任务：能跑完，且把结果塞进 result
    tid3 = w.start_env_check(full=False)
    t3 = _wait(mgr, tid3, timeout=60)
    eq(t3.state, tm.DONE, f"环境体检失败：{t3.error}")
    ok(t3.result.get("env"), "环境体检结果没放进 result")
    ok(t3.result["env"].get("total"), "环境体检结果缺 total")

    # TTML 校验任务
    from desktop.services import backend_service as bs
    tmp = os.path.join(_SANDBOX, "probe.ttml")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("not a ttml")
    tid4 = w.start_check_ttml(tmp)
    t4 = _wait(mgr, tid4, timeout=60)
    eq(t4.state, tm.FAILED, "坏 TTML 应该判失败（有错误）")
    ok(len(t4.logs) >= 1, "校验失败也要有日志")


# ========================================================= 12 转换落盘保护

@group(12, "backend_service：转换失败不覆盖已有产物 + 参数错误分类")
def t_backend_guard():
    from desktop.services import backend_service as bs

    out = os.path.join(_SANDBOX, "outputs")
    os.makedirs(out, exist_ok=True)
    tid = "guardtest"
    existing = os.path.join(out, f"{tid}.ttml")
    with open(existing, "w", encoding="utf-8") as fh:
        fh.write("上次做好的产物")

    # 参数不合法 → 立刻失败，且**绝不能**碰已有文件
    r = bs.convert({}, log=lambda m: None, output_dir=out, task_id=tid)
    eq(r["ok"], False)
    eq(r["error_kind"], "params")
    with open(existing, encoding="utf-8") as fh:
        eq(fh.read(), "上次做好的产物", "参数校验失败却动了已有产物！")
    ok(not os.path.isfile(existing + ".tmp"), "不该留下 .tmp")

    # 歌词文件为空 → 也应该是"说人话的失败"，且不动产物
    empty = os.path.join(_SANDBOX, "empty.lrc")
    with open(empty, "w", encoding="utf-8") as fh:
        fh.write("\n\n")
    audio = os.path.join(_SANDBOX, "fake.wav")
    with open(audio, "wb") as fh:
        fh.write(b"RIFF")
    r2 = bs.convert({"audio": audio, "lyrics": empty}, log=lambda m: None,
                    output_dir=out, task_id=tid)
    eq(r2["ok"], False)
    eq(r2["error_kind"], "lyrics")
    with open(existing, encoding="utf-8") as fh:
        eq(fh.read(), "上次做好的产物", "空歌词却动了已有产物！")

    # 分类函数：GPU / 显存 / 下载 / 编码 / 依赖 各来一条
    cases = [
        (RuntimeError("Library cublas64_12.dll is not found"), "gpu"),
        (RuntimeError("CUDA out of memory"), "memory"),
        (RuntimeError("LocalEntryNotFoundError: offline"), "download"),
        (UnicodeDecodeError("utf-8", b"\xce", 0, 1, "bad"), "encoding"),
        (ModuleNotFoundError("No module named 'torch'"), "dependency"),
        (ValueError("???"), "unknown"),
    ]
    for exc, want in cases:
        eq(bs._classify(exc), want, f"{exc} 的分类应该是 {want}")

    # 环境准备：HF 缓存必须指向我们算出来的目录
    hub = bs.prepare_environment()
    eq(os.environ["HUGGINGFACE_HUB_CACHE"], hub)
    ok(os.path.isdir(hub), "缓存目录没建出来")

    # ⚠️ 这条是最贵的一条教训：CUDA DLL 目录**必须**在 prepare_environment()
    # 里注册好，否则 torch 一加载就 "cublas64_12.dll is not found"，
    # 而用户看到这句话会去重装显卡驱动（其实驱动没问题）。
    from desktop.services import gpu_probe
    for d in gpu_probe.cuda_dirs():
        sep_parts = os.environ.get("PATH", "").split(os.pathsep)
        ok(d in sep_parts or getattr(sys, "getdlldirectory", None) is not None,
           f"CUDA 目录没注册进搜索路径：{d}")
    libs = gpu_probe.cuda_libs()
    if libs.get("ok"):
        print("      CUDA 运行库：", libs["detail"][:90])
    else:
        # 没有 CUDA 运行库的机器上不该"假装通过"
        ok("detail" in libs, "CUDA 检查必须给出说明")


# ============================================================== 13 版本一致

@group(13, "版本号 / 文档 / 打包脚本一致性")
def t_consistency():
    """版本号一致性。

    ⚠️⚠️ 冻结环境要**换一种取法**（2026-10-16 冻结版实测踩到）：
    原来直接 `open(desktop/__init__.py)` 读源码 —— 冻结之后**纯 Python 模块在
    PYZ 归档里，磁盘上没有这个文件**，于是冻结版 `--selftest` 第 13 组报
    `FileNotFoundError: <exe>/_internal/desktop/__init__.py`。
    正确做法（**不是跳过**）：冻结时用 **import 拿 `__version__`**，
    并额外校验**打包进去的 `build_info.json`** 里版本一致 —— 这比对源码更贴近产物。
    `CHANGELOG.md` 属仓库文件，冻结版没有，保持"有才查"（源码模式仍全量查）。
    """
    import re
    from desktop import paths as _paths

    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        import desktop as _d
        ver = getattr(_d, "__version__", "")
        ok(ver, "冻结版拿不到 desktop.__version__（PYZ 里的模块应可 import）")
    else:
        init = os.path.join(_HERE, "__init__.py")
        with open(init, encoding="utf-8") as fh:
            m = re.search(r'__version__\s*=\s*"([^"]+)"', fh.read())
        ok(m, "desktop/__init__.py 里没有 __version__")
        ver = m.group(1)
    ok(re.match(r"^\d+\.\d+\.\d+$", ver), f"版本号格式：{ver}")

    # 打包产物里的 build_info.json 与代码版本必须一致（冻结时这条尤其有意义：
    # 用户报问题时我们靠它判断"装的是哪一次构建"）
    for _root in _paths.resource_roots():
        bi = os.path.join(_root, "desktop", "resources", "build_info.json")
        if os.path.isfile(bi):
            import json as _json
            with open(bi, encoding="utf-8") as fh:
                info = _json.load(fh)
            eq(str(info.get("version")), ver,
               f"build_info.json 版本 {info.get('version')} 与 desktop 版本 {ver} 不一致")
            if frozen:
                # 冻结版必须真的带上了 build_info（否则上面的 eq 不会执行）
                ok(info.get("built_at"), "build_info.json 缺 built_at")
            break

    # 仓库里其它地方的版本号不能各说各话（仓库文件；冻结版没有就跳过）
    cl = os.path.normpath(os.path.join(_HERE, "../CHANGELOG.md"))
    if os.path.isfile(cl):
        with open(cl, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        found = re.findall(r"^##\s*\[(\d+\.\d+\.\d+)\]", body, re.M)
        ok(found, "CHANGELOG 里没有任何版本号")
        eq(found[0], ver, f"CHANGELOG 最新版本 {found[0]} 与桌面版 {ver} 不一致")

    # 清单文件必须是合法 JSON（打包脚本要读它）
    from desktop.services import model_manager as mm_mod
    man = mm_mod.load_manifest()
    eq(man["schema_version"], 1)


# ========================================================== 14 真下载（本地 HTTP）


def _start_local_server(handler_cls, ports=(18765, 18766, 28765, 38765, 0)):
    """起一个**真的能连上**的本地 HTTP 服务，返回 server 对象。

    ⚠️⚠️ 为什么不能直接 `TCPServer(("127.0.0.1", 0), H)`（2026-10-15 踩到）：
    在本机（受限/带防火墙的环境）实测 —— 绑 `127.0.0.1:0` **报成功**、
    `server_address[1]` 也给出一个正常端口号，但**连过去是
    `WinError 10061 目标计算机积极拒绝`**；而绑**固定端口**（18765）则完全正常。
    于是这条"真下载"用例在**这台机器上必然失败**，
    而它测的是 HTTP 下载/续传/校验/取消，**跟端口怎么选毫无关系** ——
    属于测试基础设施问题，不是产品问题（不能靠"跳过"来掩盖）。

    做法：逐个候选端口**绑上并真连一次**，第一个连得通的才用它；
    连不通就关掉换下一个。都不行才抛异常（如实报错，不静默跳过）。
    """
    import socket as _socket
    # ⚠️ `socketserver` / `threading` 原来是在 `t_http_download()` **函数内部**
    # 导入的，模块级 helper 看不到 —— 所以这里自己导一次（我第一版漏了，
    # 直接 NameError）。
    import socketserver
    import threading
    last = None
    for port in ports:
        try:
            srv = socketserver.TCPServer(("127.0.0.1", port), handler_cls)
        except OSError as e:
            last = e
            continue
        p = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = _socket.create_connection(("127.0.0.1", p), timeout=2)
            c.close()
            srv.timeout = 1
            return srv
        except OSError as e:
            last = e
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:                           # noqa: BLE001
                pass
            continue
    raise RuntimeError(f"没有可用的本地端口：{last}")


@group(14, "model_manager：真下载（本地 HTTP）—— 续传 / 校验 / 取消")
def t_http_download():
    """起一个本地 HTTP 服务，把"下载"这条路径**真跑一遍**。

    为什么必须真跑：这一段全是 IO 与边界（Range 续传、连接中断、
    Content-Length 不符、SHA256 对不上、取消）。只 mock 的话，
    下面那个真 bug 一辈子抓不到：

    ⚠️ **失败时删掉 .part** 曾把"下载中断→重试"变成"永远从 0 开始"，
    3GB 的模型能白下好几次。所以这条用例是**守着报错信息的**。

    ⚠️⚠️ **必须绕开系统代理**（2026-10-15 踩到，查了三轮）：
    这台机器上 `urllib.getproxies()` 返回
    `http=http://127.0.0.1:65532`（注册表里配的本地代理，不是环境变量），
    于是 `urlopen("http://127.0.0.1:18765/…")` **被发给那个代理**，
    代理拒连 → `WinError 10061 目标计算机积极拒绝`。
    症状极具误导性：**原始 socket 连 `127.0.0.1:18765` 完全正常**，
    只有走 urllib 的下载失败，看着像产品下载坏了。
    产品**不该改** —— 它下的是真实外网地址，走代理是**对的**；
    要绕开代理的是**这个测本地服务的用例**。
    """
    import http.server
    import socketserver
    import threading
    import urllib.request as _ur

    _orig_getproxies = _ur.getproxies
    _ur.getproxies = lambda: {}          # 本组全部是 127.0.0.1，直接连
    try:
        return _http_download_body()
    finally:
        _ur.getproxies = _orig_getproxies


def _http_download_body():
    import http.server
    import socketserver
    import threading

    body = os.urandom(256 * 1024)
    digest = hashlib.sha256(body).hexdigest()
    # fail_times: 0 = 正常；-1 = 一直"发一半就断"；N>0 = 再断 N 次
    state = {"range_ok": True, "fail_times": 0}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if state["fail_times"] != 0:
                if state["fail_times"] > 0:
                    state["fail_times"] -= 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                # ⚠️⚠️ **必须"发一截 → 等一下 → 再断开"**，不能"写完立刻 close"。
                # 原因（实测，见 `.tmp/ab_read.py`）：立刻 `close()` 走的是
                # **RST（强制断开）**，内核会把**还没被应用读走**的字节**丢掉**；
                # 客户端 `read(1MB)` 因为要"读满"而一个字节都拿不到 ⇒
                # `.part` 变 0 字节，于是第 14 组**时好时坏**（6 次里挂 5 次）。
                # 停一下让客户端先把这半截读进用户态，再断开 ——
                # 这才对应真实的"下到一半连接中断"，也才有**确定**的行为。
                chunk = body[: len(body) // 2]
                for i in range(0, len(chunk), 16 * 1024):
                    self.wfile.write(chunk[i:i + 16 * 1024])
                    self.wfile.flush()
                    time.sleep(0.02)
                self.connection.close()
                return
            rng = self.headers.get("Range")
            if rng and state["range_ok"]:
                start = int(rng.split("=")[1].split("-")[0])
                chunk = body[start:]
                self.send_response(206)
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # ⚠️⚠️ **不能直接用 `("127.0.0.1", 0)`（随机端口）**（2026-10-15 修）：
    # 在本机（受限/带防火墙的环境）实测：绑 `127.0.0.1:0` **报成功**、
    # 拿到的端口号看着正常，但**连过去是 `WinError 10061 目标计算机积极拒绝`**；
    # 换成固定端口（18765）则完全正常。
    # 原写法让这条用例在**这台机器上必然失败**，而它测的是"HTTP 下载/续传"，
    # 跟端口选法无关 —— 属于**测试基础设施**的问题，不是产品问题。
    # 现在：逐个候选端口**真连一次**验证可用，再用它起服务。
    srv = _start_local_server(H)
    url = f"http://127.0.0.1:{srv.server_address[1]}/model.bin"

    root = os.path.join(_SANDBOX, "httpdl")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    manifest = os.path.join(root, "manifest.json")
    rel = "models/campplus/campplus_cn_common.bin"
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"schema_version": 1, "models": [{
            "id": "probe", "category": "diarization", "name": "下载探针",
            "download_policy": "on_demand", "approx_size": len(body),
            "source": {"kind": "http", "urls": [url]},
            "verify": {"kind": "file_sha256", "target": rel,
                       "sha256": digest, "size": len(body)},
        }]}, fh, ensure_ascii=False)

    app = os.path.join(root, "app")
    os.makedirs(app, exist_ok=True)
    target = os.path.join(app, *rel.split("/"))
    part = target + ".part"

    def fresh():
        from desktop.services import model_manager as mm_mod
        for p in (target, part):
            if os.path.isfile(p):
                os.remove(p)
        return mm_mod.ModelManager(manifest_path=manifest, app_dir=app)

    try:
        # ---- a) 正常下载 + SHA256 ----
        mm = fresh()
        st = mm.download("probe", log=lambda m: None)
        eq(st.status, "ready", f"正常下载没成功：{st.detail}")
        ok(not os.path.isfile(part), "下载成功后不该留 .part")
        with open(target, "rb") as fh:
            eq(hashlib.sha256(fh.read()).hexdigest(), digest, "内容不对")

        # ---- b) 内容损坏（大小对）必须判失败 ----
        with open(target, "wb") as fh:
            fh.write(b"x" * len(body))
        mm.invalidate("probe")
        st = mm.check("probe")
        eq(st.status, "failed", "大小对但内容错，必须判失败")

        # ---- c) 中断后**必须留着 .part**，重试接着下 ----
        from desktop.services import model_manager as mm_mod
        mm = fresh()
        state["fail_times"] = -1
        try:
            mm.download("probe", log=lambda m: None)
            raise AssertionError("服务器一直断，下载不该成功")
        except mm_mod.DownloadCancelled:
            raise
        except Exception:                             # noqa: BLE001
            pass
        state["fail_times"] = 0
        ok(os.path.isfile(part) and os.path.getsize(part) > 0,
           "下载失败后必须保留 .part —— 删掉它会让断点续传变成永远重下")
        st = mm.download("probe", log=lambda m: None)
        eq(st.status, "ready", f"续传后没成功：{st.detail}")
        with open(target, "rb") as fh:
            eq(hashlib.sha256(fh.read()).hexdigest(), digest, "续传后内容不对")

        # ---- d) 服务器不支持 Range → 从头下，不能拼畸形文件 ----
        mm = fresh()
        state["range_ok"] = False
        state["fail_times"] = -1
        try:
            mm.download("probe", log=lambda m: None)
        except Exception:                             # noqa: BLE001
            pass
        ok(os.path.isfile(part) and os.path.getsize(part) > 0,
           "没留下 .part，这条用例没测到续传路径")
        state["fail_times"] = 0
        logs = []
        st = mm.download("probe", log=logs.append)
        state["range_ok"] = True
        eq(st.status, "ready", f"不支持 Range 时没成功：{st.detail}")
        with open(target, "rb") as fh:
            eq(hashlib.sha256(fh.read()).hexdigest(), digest,
               "不支持 Range 时拼出了畸形文件")
        ok(any("从头下载" in x for x in logs),
           f"应该明确说明『服务器不支持续传』，日志：{logs[:6]}")

        # ---- e) 取消：抛 DownloadCancelled，且不留下正式文件 ----
        mm = fresh()
        n = {"i": 0}

        def cancel():
            n["i"] += 1
            return n["i"] > 2

        try:
            mm.download("probe", log=lambda m: None, cancel=cancel)
            raise AssertionError("取消之后不该成功")
        except mm_mod.DownloadCancelled:
            pass
        ok(not os.path.isfile(target), "取消之后不该有正式文件")
        ok(mm.check("probe").status != "ready", "取消之后不该判就绪")
    finally:
        srv.shutdown()
        srv.server_close()


def _wait(mgr, tid, timeout: float = 30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = mgr.get(tid)
        if t is not None and t.state in ("done", "failed", "cancelled"):
            return t
        time.sleep(0.02)
    t = mgr.get(tid)
    raise AssertionError(f"任务 {tid} 超时（{timeout}s），当前状态 "
                         f"{t.state if t else 'None'}")


# ===================================================================== 主流程

def main(argv=None) -> int:
    """跑全部离线自检。返回退出码。

    ⚠️ 和 ``uicheck.main`` 一样：**必须是可调用的函数**。冻结之后
    ``runpy.run_path`` 找不到包内部脚本的 ``__main__``，
    打包版要走 ``import desktop.selftest``。
    """
    global VERBOSE, ONLY
    argv = list(argv if argv is not None else sys.argv[1:])
    VERBOSE = "-v" in argv
    ONLY = [a for a in argv if a.isdigit()]

    tests = []
    for name, obj in sorted(globals().items()):
        if callable(obj) and getattr(obj, "_group", None):
            tests.append((obj._group, obj._title, obj))
    tests.sort()

    print("=" * 72)
    print("  Lyric2TTML 桌面版自检（离线）")
    print(f"  沙箱数据目录：{_SANDBOX}")
    print("=" * 72)

    for n, title, fn in tests:
        if ONLY and str(n) not in ONLY:
            continue
        t0 = time.time()
        try:
            fn()
            dt = time.time() - t0
            PASS.append((n, title))
            print(f"  [{n:>2}] ✓ {title}  ({dt:.2f}s)")
        except Exception as e:                          # noqa: BLE001
            FAIL.append((n, title, e))
            print(f"  [{n:>2}] ✗ {title}")
            print(f"        {type(e).__name__}: {e}")
            if VERBOSE:
                traceback.print_exc()

    print("-" * 72)
    print(f"  通过 {len(PASS)} 组，失败 {len(FAIL)} 组")
    if FAIL:
        for n, title, e in FAIL:
            print(f"    ✗ [{n}] {title}: {type(e).__name__}: {e}")
        return 1
    print("  全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
