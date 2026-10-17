"""可选运行库集成测试：主程序冷启动 / 未安装 / 已安装 / 失败路径。

    .venv\\Scripts\\python.exe tools\\test_worker_integration.py

用户要求逐条对应：

* **不安装 Worker**：冷启动、普通歌词处理、TTML 导出照常；人声分离与尾音延长
  给出**明确提示**，不伪造成功结果。
* **安装 Worker 后**：真 GPU 分离、输出时长一致、音频有效、模型复用、尾音延长。
* **Worker 不可用 / CUDA 失败 / 超时 / 取消 / 分离失败**：清理临时输出 + 明确错误；
  **不把混音当人声轨**、**不产生伪造的尾音延长**。

⚠️ 本测试用 `L2T_DEMUCS_WORKER` 指向**冻结版 Worker**，不依赖开发环境的 torch。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

W = os.path.join(HERE, ".tmp", "worker-pkg")
WORKER = os.path.join(HERE, ".tmp", "demucs-worker", "dist", "DemucsWorker",
                      "DemucsWorker.exe")
AUDIO = os.path.join(HERE, "uploads",
                    "1791454712_在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav")
LRC = os.path.join(HERE, "uploads", "1791454729_在出发之前 - 陶典.lrc")
TORCH_HOME = os.path.join(HERE, "models", "demucs")
RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append({"test": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n        {detail}" if detail else ""))


def _convert(**over):
    """跑一次真转换（不显示界面）。"""
    import textread
    from pipeline import ConvertOptions, LyricToTTML, vocals_cache_path
    text = textread.read_text(LRC)
    kw = dict(model="large-v3", singer_mode="2", mode="amll", verify_amll=False)
    kw.update(over)
    logs = []
    res = LyricToTTML(ConvertOptions(**kw), log=lambda m: logs.append(str(m))).convert(
        audio_path=AUDIO, lyrics_text=text, song_title="在出发之前", artists="",
        output_path=os.path.join(W, f"out-{int(time.time()*1000)}.ttml"))
    return res, logs, vocals_cache_path(AUDIO, over.get("demucs_model", "htdemucs"))


def _rm(p):
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


def t_no_worker_baseline():
    """① 不装 Worker：普通流程 + TTML 导出照常。"""
    os.environ.pop("L2T_DEMUCS_WORKER", None)
    from desktop.services import worker_service as ws
    rec("未装 Worker 时 discover() 为空（不误判为已装）", ws.discover() == "",
        f"discover={ws.discover()!r}")
    res, logs, _ = _convert()                     # 完全不开分离
    ok = bool(res.ttml) and "<tt" in res.ttml and res.report.match_rate > 0.5
    rec("未装 Worker：冷启动 + 普通处理 + TTML 导出照常",
        ok, f"匹配率={res.report.match_rate:.4f} TTML {len(res.ttml)} 字节")
    return res


def t_no_worker_separation_hint():
    """② 打包条件（未装 Worker **且**进程内 demucs 不可用）：明确提示 + 不伪造。

    ⚠️ 必须把 `separate.available()` 打桩成 False 才是打包版的条件：
    开发环境里进程内 demucs 是可用的，管线会**合法地**走它并成功 ——
    那不是"伪造成功"，而是老路径仍在工作。第一版用例没打桩，于是假失败。
    """
    os.environ.pop("L2T_DEMUCS_WORKER", None)
    import separate
    from pipeline import vocals_cache_path as _vcp
    voc = _vcp(AUDIO, "htdemucs")
    _rm(voc)                                    # 必须清掉遗留缓存，否则会命中"复用"
    old_avail = separate.available
    old_why = separate.why_unavailable
    separate.available = lambda: False
    separate.why_unavailable = lambda: "打包版没有内置 demucs（torch 冻结下加载不了）"
    try:
        res, logs, _ = _convert(separate_vocals=True, extend_tails=True)
    finally:
        separate.available = old_avail
        separate.why_unavailable = old_why
    said = any("人声分离不可用" in l or "两条路都不可用" in l for l in logs)
    skip = any("跳过" in l and ("尾音" in l or "拖音" in l) for l in logs)
    no_fake = not os.path.isfile(voc)
    has_ttml = bool(res.ttml) and "<tt" in res.ttml
    rec("打包条件未装 Worker：明确提示 + 尾音如实跳过 + 不产出人声轨 + 仍出 TTML",
        said and skip and no_fake and has_ttml,
        f"明确原因={said} 尾音跳过={skip} 未产出人声轨={no_fake} 仍导出TTML={has_ttml}")


def t_worker_installed():
    """③ 装好 Worker：真 GPU 分离 + 时长/有效性 + 模型复用 + 尾音延长。"""
    if not os.path.isfile(WORKER):
        rec("Worker 已安装：真 GPU 分离", False, f"缺 {WORKER}")
        return
    os.environ["L2T_DEMUCS_WORKER"] = WORKER
    from desktop.services import worker_service as ws
    from pipeline import vocals_cache_path
    voc = vocals_cache_path(AUDIO, "htdemucs")
    _rm(voc)                                   # 强制真跑一次分离
    rec("装好 Worker 后 discover() 能发现", ws.discover() == WORKER,
        os.path.basename(ws.discover()))
    t0 = time.time()
    res, logs, voc = _convert(separate_vocals=True, extend_tails=True)
    used_worker = any("使用独立 Worker" in l for l in logs)
    tails = [l for l in logs if "[尾音]" in l]
    ok = os.path.isfile(voc) and used_worker and len(tails) > 0
    with wave.open(voc, "rb") as w:
        dur = w.getnframes() / w.getframerate()
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    rec("装好 Worker：走独立进程分离（日志可证）+ 产出人声轨",
        ok, f"用了 Worker={used_worker} 尾音延长 {len(tails)} 处 用时 {time.time()-t0:.0f}s")
    rec("Worker 人声轨：时长一致、16k 单声道、无 NaN/Inf",
        abs(dur - 288.0) < 1.0 and sr == 16000 and ch == 1 and not np.isnan(x).any(),
        f"时长={dur:.1f}s（原始 288.0）{sr}Hz {ch}ch nan={np.isnan(x).any()}")
    rec("Worker 人声进入现有 vocals_cache_path 流程", os.path.isfile(voc), os.path.basename(voc))
    # 再跑一次：必须复用，不再分离
    res2, logs2, _ = _convert(separate_vocals=True, extend_tails=True)
    reuse = any("复用已有人声轨" in l for l in logs2)
    rec("同一首歌再跑：复用已有人声轨（不重复分离）", reuse,
        [l for l in logs2 if "复用" in l][:1])


def _stub_exe(path: str, code: int, wait: float = 0.0) -> str:
    """造一个"坏 Worker"：立刻以指定退出码结束（用于失败路径）。

    用 Python 自己当宿主？不行 —— 要的是独立 exe。这里用 **cmd.exe 的副本** 行为：
    直接写一个 .bat 不行（要 exe）。改用系统 `cmd.exe` + 参数不可行，
    于是退一步：复制一个小的系统 exe，靠它非零退出。
    实际用的是 `where.exe`（必然存在、无参数时返回非零）——
    ⚠️ 它只是"必然失败的 exe"，用来验证**调用方**的失败处理。
    """
    src = shutil.which("where.exe") or shutil.which("findstr.exe")
    shutil.copyfile(src, path)
    return path


def t_worker_failure_paths():
    """④ Worker 失败/超时/取消：清临时输出 + 明确错误 + 不伪造。"""
    from desktop.services import worker_service as ws
    from pipeline import vocals_cache_path
    voc = vocals_cache_path(AUDIO, "htdemucs")
    _rm(voc)
    # --- 失败：exe 存在但不是 Worker（非零退出）。同时模拟"进程内也不可用"= 打包版 ---
    import separate as _sep
    old_avail = _sep.available
    _sep.available = lambda: False
    stub = os.path.join(W, "stub-fail.exe")
    _stub_exe(stub, 1)
    os.environ["L2T_DEMUCS_WORKER"] = stub
    try:
        res, logs, _ = _convert(separate_vocals=True, extend_tails=True)
    finally:
        _sep.available = old_avail
    tmp_left = [f for f in os.listdir(os.path.dirname(voc))
                if f.endswith(".tmp.wav") or f.endswith(".tmp.json")]
    said_fail = any("Worker 失败" in l for l in logs)
    said_both = any("两条路都不可用" in l for l in logs)
    skip_tail = any("跳过" in l and ("尾音" in l or "拖音" in l) for l in logs)
    rec("Worker 失败且进程内不可用（打包版）：明确报错 + 清理临时输出 + 跳过尾音 + 无产物",
        said_fail and said_both and skip_tail and not os.path.isfile(voc) and not tmp_left,
        f"Worker失败={said_fail} 双失败说明={said_both} 尾音跳过={skip_tail} "
        f"产物={os.path.isfile(voc)} 残留={tmp_left}")

    # --- 超时：把超时调到 0.5s，真 Worker 也会被超时杀掉 ---
    os.environ["L2T_DEMUCS_WORKER"] = WORKER
    import pipeline as P
    old = P.SEP_TIMEOUT_S
    P.SEP_TIMEOUT_S = 0.5
    old_avail2 = _sep.available
    _sep.available = lambda: False
    try:
        _rm(voc)
        res2, logs2, _ = _convert(separate_vocals=True, extend_tails=True)
        tmp_left2 = [f for f in os.listdir(os.path.dirname(voc))
                     if f.endswith(".tmp.wav") or f.endswith(".tmp.json")]
        said_timeout = any("超时" in l for l in logs2)
        said_fb2 = any("两条路都不可用" in l for l in logs2)
        ok2 = (said_timeout and said_fb2 and not tmp_left2
               and not os.path.isfile(voc))
        rec("Worker 超时：被终止 + 清理临时输出 + 如实说明",
            ok2,
            f"{[l[:70] for l in logs2 if 'Worker 失败' in l][:1]} "
            f"残留={tmp_left2} 产物={os.path.isfile(voc)}")
    finally:
        P.SEP_TIMEOUT_S = old
        _sep.available = old_avail2

    # --- 直接测服务层的取消与超时（不经管线）---
    out = os.path.join(W, "cancel-out.wav")
    _rm(out)
    flag = {"v": False}

    def stop():
        flag["v"] = True
        return True
    try:
        ws.separate(AUDIO, out, exe=WORKER, torch_home=TORCH_HOME,
                    timeout=600, stop=stop, echo=lambda *a: None)
        rec("Worker 取消：立刻终止并清理", False, "居然跑完了")
    except ws.WorkerError as e:
        left = [f for f in os.listdir(os.path.dirname(out)) if ".tmp." in f]
        rec("Worker 取消：立刻终止并清理临时文件",
            e.kind == "cancelled" and not os.path.isfile(out) and not left,
            f"kind={e.kind} 产物={os.path.isfile(out)} 残留={left}")
    os.environ.pop("L2T_DEMUCS_WORKER", None)


def t_no_torch_in_main():
    """⑤ 主程序进程里**没有** Worker 的 torch（结构断言，不看竞态）。"""
    import subprocess
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import pipeline, desktop.services.worker_service as ws;"
        "bad=[m for m in sys.modules if m=='torch' or m.startswith('torch.')];"
        "print('TORCH_MODULES=', len(bad));"
        "print('DEMUCS_MODULES=', len([m for m in sys.modules if m=='demucs']))"
        % HERE
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=HERE)
    out = r.stdout or ""
    rec("主程序导入 pipeline 后，进程内**没有** torch/demucs",
        "TORCH_MODULES= 0" in out and "DEMUCS_MODULES= 0" in out,
        out.strip().replace("\n", " | ")[:160])


def main() -> int:
    print("=" * 92)
    print("  一、不安装 Worker")
    print("=" * 92)
    t_no_worker_baseline()
    t_no_worker_separation_hint()
    print()
    print("=" * 92)
    print("  二、安装 Worker 后")
    print("=" * 92)
    t_worker_installed()
    print()
    print("=" * 92)
    print("  三、失败路径与结构断言")
    print("=" * 92)
    t_worker_failure_paths()
    t_no_torch_in_main()
    n = sum(1 for r in RESULTS if r["ok"])
    print()
    print("=" * 92)
    print(f"  合计 通过 {n} / {len(RESULTS)}")
    print("=" * 92)
    for r in RESULTS:
        if not r["ok"]:
            print(f"  ✗ {r['test']}: {r['detail'][:160]}")
    os.makedirs(W, exist_ok=True)
    with open(os.path.join(W, "integration-tests.json"), "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, ensure_ascii=False, indent=2)
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
