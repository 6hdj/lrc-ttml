"""**变异测试**：故意把代码改坏，验证测试真的能抓到。

    python tools/mutation_check.py                 # 全部
    python tools/mutation_check.py --only cuda     # 只看某几条
    python tools/mutation_check.py --list          # 只列出变异

## 为什么要有这个工具（不是一个理论上的好习惯，是本项目血的教训）

这个项目里**四次**靠"故意弄坏再测"才发现**测试本身是假的**：

1. **CUDA 裁剪**：把 `cublas/` 目录**改名**（仍在该目录的 DLL 搜索路径上）
   → 转换**成功**，差点得出"637 MB 可以删"。**改名 ≠ 移除**；
2. **假失败的解释器**：交付报告用系统 python 跑需要 Qt 的测试
   → `ModuleNotFoundError: PySide6`，报告的"失败"其实是工具用错了 python；
3. **验收脚本没传 `L2T_DATA_DIR`**：运行时"不见了"，报
   `cublas64_12.dll is not found`，看着像打包版坏了；
4. **取消用例测不出坏**：去掉"解压时检查取消"后测试**照样通过** ——
   查下去发现真凶在别处（下载完到解压之间没有取消检查），
   而且用例的取消时机是"数调用次数"、落点不确定。

共同点：**测试在，断言在，但它测的不是它声称测的东西**。
这种"假绿"比没有测试更危险 —— 没有测试你会小心，
假绿会让你以为有保障。

`tools/release_report.py` 只能证明"测试现在通过"，
**证明不了"测试能失败"**。本工具补的就是这一块：
对每个关键防护点，注入一个**最小的**破坏，
断言对应测试**必须失败**。失败的变异 = 测试测不出坏 = 假绿。

⚠️ 判据要严：变异后测试**通过**就是问题（不管什么理由）。
⚠️ 每次变异后**无论成败都还原源码**（用 finally + 字节级备份）。
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (名字, 目标文件, 原文, 改坏成, 应该抓到这个的测试)
MUTATIONS = [
    ("cuda-probe", "desktop/services/runtime_service.py",
     'if spec.get("verify_dll_probe") == "cuda":',
     'if False:',
     "tools/test_runtime_source.py",
     "拿掉「认已有 CUDA 来源」→ 完整包会判未安装、白下 529 MB"),

    ("cancel-unpack", "desktop/services/runtime_service.py",
     "                        _stop2()\n                        z.extract(m, tmp_site)",
     "                        z.extract(m, tmp_site)",
     "tools/test_runtime_install.py",
     "拿掉解压时的取消检查 → 取消会被无视，半截安装被判 ready"),

    # ⚠️ 刻意**不**把"下载完到解压之间那个 `_stop()`"列进来 —— 它是**等价变异**：
    # 拿掉它之后，取消仍会在解压循环里被抓到（只是晚 ~100 个条目），
    # 对外的保证（取消 ⇒ 不安装）不变，所以任何测试都不该因此失败。
    # 第一次把它列进来时，harness 报"没抓到"，我一度以为测试是假绿 ——
    # 其实是变异本身没有可观测效果。等价变异在变异测试里是已知现象，
    # 正确处理是**记录并排除**，而不是去改测试迎合它。

    ("verify-files", "desktop/services/runtime_service.py",
     "        if not any(os.path.isfile(c) and os.path.getsize(c) > 0 for c in cands):\n            missing.append(rel)",
     "        if False:\n            missing.append(rel)",
     "tools/test_cuda_runtime.py",
     "拿掉「关键文件必须存在」的检查 → 空目录也会被判就绪"),

    ("no-part-keep", "desktop/services/model_manager.py",
     'read = getattr(r, "read1", None) or r.read',
     "read = r.read",
     "desktop/selftest.py",
     "把 read1 换回 read → 连接中断时 .part 拿不到数据，断点续传失效"),

    ("hf-alias", "asr.py",
     'or os.environ.get("L2T_HF_CACHE")',
     'or os.environ.get("L2T_HF_CACHE_X")',
     "tools/test_hf_cache.py",
     "改坏 L2T_HF_CACHE 别名的取值 → 本地有 3GB 模型却重新联网下载"),

    ("gpu-guard-nocard", "desktop/pages/home.py",
     "                if not gpu_probe.has_nvidia_gpu():",
     "                if False:",
     "tools/test_gpu_guard.py",
     "拿掉「没有 N 卡」的分支 → 没卡用户会被引导白下 529 MB"),

    # ---------------------------------------------------------------- 长音重叠 opt-in
    # 这一组变异守的是「尾音补全允许同歌手跨行重叠」这条实验开关的**边级语义**：
    # 默认关闭时零回归；打开后只有实际越界才打 marker；marker 只豁免**相邻**边；
    # 而且消费者（pipeline/matcher）必须真的认这个 marker。
    # 目标测试：tools/test_tail_overlap_optin.py（55 项断言）。
    ("tail-overlap-m1-same-agent-clamp", "tail_extend.py",
     "            if same_agent and not allow_same_agent_overlap:",
     "            if same_agent:",
     "tools/test_tail_overlap_optin.py",
     "把同歌手分支改回无条件 min(hard, nxt.begin) → opt-in 形同虚设，"
     "尾音仍被硬切在下一行开始"),

    ("tail-overlap-m2-pipeline-flag", "pipeline.py",
     "allow_same_agent_overlap=self.opt.allow_same_agent_overlap,",
     "allow_same_agent_overlap=False,",
     "tools/test_tail_overlap_optin.py",
     "流水线永远传 False → 界面/CLI 打开开关也不生效（死字段），"
     "只有真把参数传下去才叫「接上了」"),

    ("tail-overlap-m3-marker-after-overlap-pair", "matcher.py",
     "            if getattr(a, \"overlap_with_next\", False):\n"
     "                # \u8fb9\u7ea7\u8bc1\u636e\uff1a\u672c\u884c\u4e0e\u7d27\u90bb"
     "\u4e0b\u4e00\u884c**\u5b9e\u9645**\u53d1\u751f\u6709\u8bc1\u636e\u7684"
     "\u5ef6\u957f \u2192 \u4e0d\u56de\u6536\n"
     "                continue\n"
     "            new_a_end = overlap_pair(a.units, b.units, a.begin, self.min_unit_duration)\n",
     "            new_a_end = overlap_pair(a.units, b.units, a.begin, self.min_unit_duration)\n"
     "            if getattr(a, \"overlap_with_next\", False):\n"
     "                # \u8fb9\u7ea7\u8bc1\u636e\uff1a\u672c\u884c\u4e0e\u7d27\u90bb"
     "\u4e0b\u4e00\u884c**\u5b9e\u9645**\u53d1\u751f\u6709\u8bc1\u636e\u7684"
     "\u5ef6\u957f \u2192 \u4e0d\u56de\u6536\n"
     "                continue\n",
     "tools/test_tail_overlap_optin.py",
     "把 marker 判断移回 overlap_pair() 之后 → overlap_pair 就地改 units，"
     "尾字 end 已经被压回去，行区间看起来没动、字级证据被吃掉（真踩过的 bug）"),

    ("tail-overlap-m4-edge-ok-always-false", "pipeline.py",
     "            edge_ok = (prev is not None and prev[0] == i - 1\n"
     "                       and getattr(lines[prev[0]], \"overlap_with_next\", False))",
     "            edge_ok = False",
     "tools/test_tail_overlap_optin.py",
     "edge_ok 恒 False → 相邻边的 marker 不再豁免，opt-in 产出的合法叠唱"
     "会被流水线自己压回去（补了等于没补）"),

    ("tail-overlap-m5-marker-always-true", "tail_extend.py",
     "            line.overlap_with_next = bool(overlap)",
     "            line.overlap_with_next = True",
     "tools/test_tail_overlap_optin.py",
     "marker 恒 True → 没实际越界的行也被标成「允许重叠」，"
     "下游会放过本该收紧的同歌手重叠（边级证据泄漏）"),
]


def apply_mutation(path: str, old: str, new: str) -> bytes:
    """就地把 `old` 换成 `new`，返回**原始字节**用于还原。"""
    raw = open(path, "rb").read()
    text = raw.decode("utf-8")
    if old not in text:
        raise LookupError("找不到要替换的原文（结构可能变了，脚本要跟着改）")
    io.open(path, "w", encoding="utf-8", newline="").write(text.replace(old, new, 1))
    return raw


def restore(path: str, raw: bytes) -> None:
    open(path, "wb").write(raw)


def run_test(rel: str) -> tuple:
    """跑一个测试，返回 (exit_code, 尾部输出)。

    ⚠️ 需要 Qt 的测试要用 `.venv` 的解释器 —— 否则会"因为导入失败而失败"，
    那样变异检查会**假通过**（测试失败，但不是因为变异）。
    """
    py = sys.executable
    if any(k in rel for k in ("gpu_guard", "theme", "uicheck")):
        venv = os.path.join(HERE, ".venv", "Scripts", "python.exe")
        if os.path.isfile(venv):
            py = venv
    r = subprocess.run([py, rel], cwd=HERE, capture_output=True, timeout=1800)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    return r.returncode, out


def main() -> int:
    ap = argparse.ArgumentParser(description="变异测试：测试真的能抓到 bug 吗")
    ap.add_argument("--only", default="", help="只跑名字里含这个子串的")
    ap.add_argument("--list", action="store_true", help="只列出变异")
    a = ap.parse_args()

    if a.list:
        for name, f, _o, _n, test, why in MUTATIONS:
            print(f"  {name:22s} {f}  →  {test}")
            print(f"      {why}")
        return 0

    todo = [m for m in MUTATIONS if a.only in m[0]]
    print("=" * 74)
    print("  变异测试：故意改坏，测试必须抓到")
    print("=" * 74)

    print("\n[0] 先确认**未变异**时这些测试是通过的（否则变异结果没有意义）")
    baseline = {}
    for name, _f, _o, _n, test, _why in todo:
        code, out = run_test(test)
        baseline[test] = code
        mark = "✓" if code == 0 else "✗"
        print(f"    {mark} {test}  exit={code}")
    broken_baseline = [t for t, c in baseline.items() if c != 0]
    if broken_baseline:
        print(f"\n  ✗ 这些测试**本来就没通过**，无法判断变异效果：{broken_baseline}")
        return 2

    print("\n[1] 逐个注入变异，测试**必须失败**")
    bad = []
    for name, rel, old, new, test, why in todo:
        path = os.path.join(HERE, rel)
        raw = None
        try:
            raw = apply_mutation(path, old, new)
            # ⚠️ 变异必须**确认已落到磁盘**再跑测试。第一次实现没做这个确认，
            # 结果某个变异"时灵时不灵"（隔离跑能抓到、批量跑抓不到），
            # 查半天都在怀疑测试本身。先把"变异到底在不在"变成可证事实。
            on_disk = io.open(path, encoding="utf-8").read()
            if new not in on_disk or old in on_disk:
                print(f"    {name:22s} ⚠ 变异没写进去（跳过，不计入结论）")
                continue
            if os.environ.get("L2T_MUT_DEBUG") == "1":
                import hashlib
                print(f"        [debug] {rel} sha1="
                      f"{hashlib.sha1(on_disk.encode('utf-8')).hexdigest()[:12]}"
                      f" 大小={len(on_disk)} 旧片段还在={old in on_disk}")
            code, out = run_test(test)
            if os.environ.get("L2T_MUT_VERBOSE") == "1":
                print(f"    ---- {name} 测试输出 ----")
                for ln in out.strip().split("\n"):
                    s = ln.strip()
                    if s and any(k in s for k in (
                            "通过 ", "FAIL", "OK ", "取消", "用例有效",
                            "Traceback", "Error", "exit")):
                        print(f"      {s[:118]}")
        except LookupError as e:
            print(f"    {name:22s} ⚠ 跳过：{e}")
            continue
        except Exception as e:                          # noqa: BLE001
            print(f"    {name:22s} ⚠ 出错：{type(e).__name__}: {e}")
            continue
        finally:
            if raw is not None:
                restore(path, raw)                      # 无论成败都还原
        caught = code != 0
        # ⚠️⚠️ **必须区分"测试抓到了"和"测试自己崩了"**（真踩到，而且很隐蔽）：
        # 有个变异把源码改成了**语法错误**，测试于是以非 0 退出 ——
        # harness 记成"✅ 抓到了"，其实是 `SyntaxError`，
        # 那个变异到底有没有被**断言**抓住，根本没测出来。
        # "崩了"也算失败的话，变异测试会给出**假的安心**。
        crashed = ("SyntaxError" in out or "IndentationError" in out
                   or "Traceback (most recent call last)" in out
                   and "通过 " not in out)
        if caught and crashed:
            print(f"    {name:22s} ⚠ **不可判定**：测试因异常退出"
                  f"（不是断言抓到的）")
            for ln in out.strip().split("\n")[-6:]:
                if ln.strip():
                    print(f"        {ln.strip()[:110]}")
            bad.append((name, test, why + "（变异导致语法/运行错误，无法判定）"))
            continue
        print(f"    {name:22s} {'✅ 抓到了' if caught else '❌ 没抓到'}  "
              f"({test} exit={code})")
        if not caught:
            # 把测试输出里最相关的一行留出来，便于判断"是测试没抓"还是"别的原因"
            rel_lines = [ln.strip() for ln in out.split("\n")
                         if "FAIL" in ln or "通过" in ln][-3:]
            for ln in rel_lines:
                print(f"        {ln[:110]}")
            bad.append((name, test, why))

    print("\n" + "=" * 74)
    if bad:
        print(f"  ❌ {len(bad)} 个变异**没被测试抓到** —— 这些测试是假绿：")
        for name, test, why in bad:
            print(f"      {name}：{test}")
            print(f"          本应抓：{why}")
    else:
        print(f"  ✓ {len(todo)} 个变异全部被抓到 —— 这些测试真的能失败")
    print("=" * 74)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
