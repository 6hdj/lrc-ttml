"""把 CAM++ 声纹模型导出成 ONNX（供 `speaker_campplus` 的 ONNX 后端使用）。

    python tools/export_campplus_onnx.py                # 导出 + 数值一致性自检
    python tools/export_campplus_onnx.py --out X.onnx
    python tools/export_campplus_onnx.py --check-only   # 只验已有模型

## 为什么需要这个脚本（不是在本地导一次就完了）

`.bin` 里是**纯权重**（`strict=False` 加载，0 missing / 0 unexpected），
**网络结构在仓库里**（`models/campplus_src/`）。所以 ONNX 必须由本仓库
**按固定权重**导出来 —— 这样"ONNX 与 torch 同源"才是可复现的事实，
而不是某个中间产物的运气。

## ⚠️ 一个必须打的补丁：`seg_pooling` 的 `avg_pool1d(ceil_mode=True)`

原始 `components.py` 里 `CAMDenseTDNNLayer.seg_pooling()` 用了

    F.avg_pool1d(x, kernel_size=100, stride=100, ceil_mode=True)
    # 然后 expand 回原长度再 [..., :T] 切片

实测（`.tmp/exp_segpooling.py`）**这个算子在 ONNX 里语义与 PyTorch 不一致**：

| T（帧数） | torch 与 onnx 逐元素一致？ | max|Δ| |
| --- | --- | --- |
| 200 / 300 / 400 / 800 / 900（100 的整数倍） | ✅ | 0 |
| 199 / 201 / 301 / 799 / **801** / 901 | ❌ | 1.3e-3 ~ 2.1e+0 |

根因：`ceil_mode=True` 时最后一段是"越界帧"，PyTorch 只除以**有效帧数**，
ONNX 的 `AveragePool`（`count_include_pad=0`）在 ceil 边界上的除数与它不同。

**影响很大**：8 秒 @16k ≈ **801 帧**，正是生产里最常用的长度 —— 不修的话
ONNX 与 torch 的声纹向量 cos≈0.9995、max|Δ|≈0.1，分声部结果会漂。

**修法（已实测等价）**：把这段换成一个**手写分段均值**，只用
`ReduceMean / Concat / Slice` 这些语义无歧义的算子：

    每段取 [i*100, min((i+1)*100, T)) 的均值（只除以有效帧数）→ 再重复填回原长度

实测手写版与 PyTorch 原版在 199/301/799/801/901 上 max|Δ| ≤ 7.5e-8。
补丁只作用于导出过程（在导出脚本里给实例打补丁），
**不改仓库里的 `campplus_src`**（那是"网络定义"的唯一来源，改它会动到 torch 路径）。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

SEG_LEN = 100                    # `seg_pooling` 的默认段长（与源文件一致）
DEFAULT_OUT = os.path.join(HERE, "models", "campplus", "campplus.onnx")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ======================================================================
#  补丁：用"手写分段均值"替换 avg_pool1d(ceil_mode=True)
# ======================================================================
def patch_seg_pooling(net, seg_len: int = SEG_LEN, log=print,
                      form: str = "pad_slice") -> None:
    """把实例上所有 `seg_pooling` 换成语义无歧义的实现。

    ⚠️ 只改**实例**，不改源文件。等价性见模块 docstring 的实测表。

    `form` 有三种写法（导出实验里逐一试过，只有一种能通过 ONNX 形状推断）：
      * `repeat_interleave` —— 手写分段均值 + 平铺。数值上等价，但导出的
        图里有 `RepeatInterleave` 相关节点，ONNX 报
        `Concat [ShapeInferenceError] axis must be in [-rank, rank-1]`。
      * `cat`               —— 平铺改用 `torch.cat`（语义更直白）。
      * `pad_slice`（默认） —— **不用平铺**：把输入右侧补零到 seg_len 的整数倍，
        分段 reshape 后取均值，再 `repeat_interleave` 回原长并切片。
        数值上与原版等价（见 `.tmp/exp_segpooling.py` 的 5 组实测），
        而且形状全程是静态推导得出来的。
    """
    import torch
    import torch.nn.functional as F

    def _seg_expand(x, seg_len):
        """等价于：分段取均值 → 每段重复 seg_len 次 → 截回原长。

        ⚠️⚠️ 这个补丁被否过两版，两版的错因都记在这里（别再走一遍）：

        **第一版**：Python `for i in range(n_seg)` 逐段切片 + `min(hi, T)`。
        导出的图**只在导出时那个长度附近**正确 —— `min((i+1)*seg_len, T)`
        里的 `T` 是张量形状，`min` 的结果在 trace 时退化成**常量**
        （PyTorch 警告 `Converting a tensor to a Python boolean`）。

        **第二版**：加 `if n_pad:` 再 pad。`if` 同样被 trace 成常量，
        而且 `reshape(B, C, n_seg, seg_len)` 用的是**未 pad 的 n_seg** ——
        元素数对不上（实测 `RuntimeError: shape '[1,1,5,100]' is invalid
        for input of size 2500`）。**并且它把下游 `/xvector/stats/Concat`
        的形状推断带崩了**（ORT 报 `axis must be in [-rank, rank-1]`，
        实测 `a_original` 能过、`b_patched` 不能过 —— 所以错在补丁，不在模型）。

        **现在这版**：**全程无 Python 分支、无 Python 整数循环**，
        形状一律从 `T` 推导：
          ① `n_seg = ceil(T/seg_len)`（张量运算）
          ② pad 到 `n_seg*seg_len`（`n_pad` 可能是 0，处处等价）
          ③ reshape → 掩码均值（掩码按位置算，不靠 `abs>0`）
          ④ 用 `unsqueeze + expand + reshape` 平铺（不用 `repeat_interleave`）
        只用 reshape / expand / mul / sum，形状可静态推导，且与 PyTorch
        `ceil_mode=True` 的"只除以有效帧数"语义一致。
        """
        T = x.shape[-1]
        seg_len_t = seg_len
        n_seg = (T + seg_len_t - 1) // seg_len_t        # 张量运算，不是常量
        want = n_seg * seg_len_t
        n_pad = want - T                                 # 可能是 0（无分支）
        xp = _pad_last(x, n_pad)

        B, C = xp.shape[0], xp.shape[1]
        xr = xp.reshape(B, C, n_seg, seg_len_t)
        # 掩码：位置 < T 的算有效帧（不用 abs>0，真实值可能就是 0）
        idx = torch.arange(want, device=x.device).reshape(n_seg, seg_len_t)
        mask = (idx < T).to(x.dtype).reshape(1, 1, n_seg, seg_len_t)
        cnt = mask.sum(dim=-1).clamp_min(1.0)
        seg = (xr * mask).sum(dim=-1) / cnt              # (B, C, n_seg)
        # 平铺回 want 长，再截到 T（等价源码的 expand + 切片）
        tiled = seg.unsqueeze(-1).expand(B, C, n_seg, seg_len_t).reshape(
            B, C, want)
        return tiled[..., :T]

    def _pad_last(x, n_pad):
        """在最后一维右侧补 n 个 0（n 可以是 0；用 cat 而不是 F.pad，
        避免 pad 在动态长度下的语义歧义）。"""
        if n_pad is None:
            return x
        zeros = x.new_zeros(x.shape[0], x.shape[1], n_pad)
        return torch.cat([x, zeros], dim=-1)

    def make(seg_len: int):
        def seg_pooling_onnx_safe(x, seg_len=seg_len, stype="avg"):
            if stype != "avg":
                raise ValueError("这套导出只支持 avg（生产路径用的就是 avg）")
            return _seg_expand(x, seg_len)
        return seg_pooling_onnx_safe

    fn_impl = make(seg_len)
    patched = 0
    kinds = {}
    for mod in net.modules():
        # ⚠️ 按**能力**找（谁有 seg_pooling 就补谁），不写死类名 ——
        # 第一版写死了 `CAMDenseTDNNLayer`，结果一个都没找到
        # （真正拥有它的是 `CAMLayer`，`components.py` 里的缩进很容易看错）。
        # 写死类名的补丁一旦失效是**静默**的：不报错、只是导出的模型数值不对。
        fn = getattr(mod, "seg_pooling", None)
        if fn is None or not callable(fn):
            continue
        mod.seg_pooling = fn_impl
        patched += 1
        kinds[type(mod).__name__] = kinds.get(type(mod).__name__, 0) + 1
    log(f"  已给 {patched} 个模块打上 seg_pooling 补丁（form={form}）：{kinds}")
    assert patched > 0, ("没找到任何带 seg_pooling 的模块 —— 网络定义变了？"
                         "必须停下来查（静默漏补会让导出模型数值不对）")


def load_net(log=print):
    """按 `speaker_campplus._load_model()` 完全一致的方式建网 + 载权重。"""
    import torch
    src = os.path.join(HERE, "models", "campplus_src")
    w = os.path.join(HERE, "models", "campplus", "campplus_cn_common.bin")
    if not os.path.isfile(w):
        raise SystemExit(
            f"没有权重：{w}\n"
            f"  先在「模型管理」里下载「CAM++ 声纹」，或跑\n"
            f"  python -m desktop.services.runtime_service --install torch 之后再下")
    if src not in sys.path:
        sys.path.insert(0, src)
    spec = importlib.util.spec_from_file_location(
        "campplus_net", os.path.join(src, "model.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["campplus_net"] = mod
    spec.loader.exec_module(mod)

    net = mod.CAMPPlus(feat_dim=80, embedding_size=192, growth_rate=32, bn_size=4,
                       init_channels=128, config_str="batchnorm-relu",
                       memory_efficient=True, output_level="segment")
    state = torch.load(w, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    missing, unexpected = net.load_state_dict(state, strict=False)
    log(f"  权重：missing={len(missing)} unexpected={len(unexpected)}")
    if missing or unexpected:
        # ⚠️ 有缺/多就说明"网络定义与权重不是一套"，导出的东西不可信
        raise SystemExit(f"权重与网络不匹配（missing={missing[:3]} "
                         f"unexpected={unexpected[:3]}）—— 不能导出")
    net.eval()
    return net


def export(out_path: str, log=print) -> dict:
    import torch
    log("=" * 68)
    log("  1) 建网 + 载权重 + 打 seg_pooling 补丁")
    log("=" * 68)
    net = load_net(log=log)
    patch_seg_pooling(net, log=log)

    log("\n" + "=" * 68)
    log("  2) 导出 ONNX（动态长度轴）")
    log("=" * 68)
    import speaker_campplus as sp
    rng = np.random.default_rng(0)
    wav = (rng.standard_normal(8 * sp.SR) * 0.05).astype(np.float32)
    feat = sp.fbank(wav, sp.SR)                     # (T, 80)，沿用现有特征提取
    dummy = torch.from_numpy(feat).unsqueeze(0)     # (1, T, 80)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    torch.onnx.export(
        net, (dummy,), out_path,
        dynamo=False, opset_version=17,
        input_names=["feat"], output_names=["emb"],
        dynamic_axes={"feat": {0: "B", 1: "T"}, "emb": {0: "B"}},
        do_constant_folding=True)
    size = os.path.getsize(out_path)
    log(f"  已写出 {out_path}（{size / 1024:.0f} KB）")

    log("\n" + "=" * 68)
    log("  3) 数值一致性：torch vs onnxruntime（**这才是成功判据**）")
    log("=" * 68)
    import onnxruntime as ort
    sess = ort.InferenceSession(out_path, providers=["CPUExecutionProvider"])
    worst = 0.0
    worst_cos = 1.0
    rows = []
    # ⚠️ 必须覆盖**非 100 整数倍**的长度 —— 补丁修的正是这类（801 = 生产常用）
    for T in (100, 199, 200, 201, 301, 400, 500, 700, 799, 800, 801, 802,
              900, 901, 1000, 1200, 1600):
        if T > feat.shape[0]:
            continue
        f = feat[:T]
        with torch.no_grad():
            a = net(torch.from_numpy(f).unsqueeze(0)).numpy().reshape(-1)
        b = sess.run(None, {"feat": f[None].astype(np.float32)})[0].reshape(-1)
        mx = float(np.abs(a - b).max())
        cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
        worst = max(worst, mx)
        worst_cos = min(worst_cos, cos)
        rows.append((T, mx, cos, a.shape[0]))
        log(f"  T={T:5d}  max|Δ|={mx:.3e}  cos={cos:.8f}  维度={a.shape[0]}")

    dims_ok = all(r[3] == 192 for r in rows)
    ok = worst < 1e-4 and worst_cos > 0.999999 and dims_ok

    meta = {
        "source_weights": "models/campplus/campplus_cn_common.bin",
        "source_weights_sha256": sha256_file(
            os.path.join(HERE, "models", "campplus",
                         "campplus_cn_common.bin")),
        "network": "models/campplus_src (CAMPPlus, feat_dim=80, "
                   "embedding_size=192, output_level=segment)",
        "opset": 17,
        "input": "feat (B, T, 80) float32 —— 由 speaker_campplus.fbank 产出",
        "output": "emb (B, 192) —— **未 L2 归一化**，归一化在 Python 侧做",
        "seg_len": SEG_LEN,
        "patch": "seg_pooling 用手写分段均值替换 avg_pool1d(ceil_mode=True)；"
                 "原因是 ONNX AveragePool 在 ceil 边界的除数与 PyTorch 不一致，"
                 "导致非 100 整数倍长度（含生产常用的 801 帧）数值偏离。",
        "numerical_check": {
            "worst_max_abs_diff": worst,
            "worst_cosine": worst_cos,
            "dims_ok": dims_ok,
            "samples": len(rows),
        },
    }
    meta_path = out_path + ".json"
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    log(f"\n  元数据：{meta_path}")

    log("\n" + "=" * 68)
    if ok:
        log(f"  ✅ 数值一致（最坏 max|Δ|={worst:.2e}，最坏 cos={worst_cos:.8f}，"
            f"维度 192）")
    else:
        log(f"  ❌ 不一致（最坏 max|Δ|={worst:.2e}，cos={worst_cos:.8f}，"
            f"维度正确={dims_ok}）—— **不能用于替换**")
    log("=" * 68)
    return {"ok": ok, "worst": worst, "worst_cos": worst_cos,
            "size": size, "meta": meta}


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 CAM++ 到 ONNX（含数值自检）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--check-only", action="store_true",
                    help="只检查已有 ONNX 的数值一致性，不重新导出")
    a = ap.parse_args()

    if a.check_only:
        if not os.path.isfile(a.out):
            print(f"没有 {a.out}")
            return 2
        net = load_net()
        patch_seg_pooling(net, log=lambda m: None)
        import onnxruntime as ort
        import speaker_campplus as sp
        import torch
        sess = ort.InferenceSession(a.out, providers=["CPUExecutionProvider"])
        rng = np.random.default_rng(0)
        wav = (rng.standard_normal(8 * sp.SR) * 0.05).astype(np.float32)
        feat = sp.fbank(wav, sp.SR)
        worst = 0.0
        for T in (199, 201, 799, 801, 901):
            f = feat[:T]
            with torch.no_grad():
                x = net(torch.from_numpy(f).unsqueeze(0)).numpy().reshape(-1)
            y = sess.run(None, {"feat": f[None].astype(np.float32)})[0].reshape(-1)
            worst = max(worst, float(np.abs(x - y).max()))
        print(f"  最坏 max|Δ| = {worst:.3e}  {'✅' if worst < 1e-4 else '❌'}")
        return 0 if worst < 1e-4 else 1

    r = export(a.out)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
