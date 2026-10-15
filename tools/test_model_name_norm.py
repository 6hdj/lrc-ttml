"""验证模型名归一化 + 报错分类（用户实测"每次转换都失败"的两个根因）。

    python .tmp/test_model_norm.py
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import asr  # noqa: E402

_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


print("=" * 74)
print("  ① 模型名归一化（清单 id → whisper 尺寸名）")
print("=" * 74)
CASES = [
    ("whisper-large-v3", "large-v3"),
    ("whisper-medium", "medium"),
    ("whisper-small", "small"),
    ("large-v3", "large-v3"),          # 已经是规范名，不能改动
    ("medium", "medium"),
    ("", ""),
]
for src, want in CASES:
    got = asr.norm_model(src)
    check(got == want, f"norm_model({src!r}) == {want!r}", f"实际 {got!r}")

# 本地目录必须原样保留（用户自己放的模型）
tmpdir = os.path.join(HERE, ".tmp", "fake-model-dir")
os.makedirs(tmpdir, exist_ok=True)
check(asr.norm_model(tmpdir) == tmpdir,
      "本地模型目录保持不变（不能把路径当尺寸名处理）",
      asr.norm_model(tmpdir))

print()
print("=" * 74)
print("  ② WhisperASR 真的用了归一化后的名字")
print("=" * 74)
a = asr.WhisperASR(model="whisper-large-v3", log=lambda m: None)
check(a.model_name == "large-v3",
      "WhisperASR('whisper-large-v3').model_name == 'large-v3'",
      a.model_name)
# 仓库映射也要对（否则会去下一个不存在的仓库）
check(asr._REPO_MAP.get(a.model_name) == "Systran/faster-whisper-large-v3",
      "缓存/下载用的是正确仓库 Systran/faster-whisper-large-v3",
      str(asr._REPO_MAP.get(a.model_name)))

print()
print("=" * 74)
print("  ③ 报错分类：模型名问题不能再说成「显卡初始化失败」")
print("=" * 74)
msg = ("Invalid model size 'whisper-large-v3', expected one of: tiny.en, tiny, "
       "base.en, base, small.en, small, medium.en, medium, large-v1, large-v2, "
       "large-v3, large, distil-large-v2")
help_txt = a._gpu_help("whisper-large-v3", msg)
print("  （节选）", help_txt.split("\n")[0])
check("显卡" in help_txt and "不是显卡问题" in help_txt,
      "明确写出「不是显卡问题」", help_txt[:80])
check("cublas" not in help_txt.lower() and "DLL" not in help_txt,
      "**不再给装 CUDA DLL 的指引**（那会把人引错方向）",
      help_txt[:120])
check("large-v3" in help_txt, "告诉用户正确的模型名", help_txt[:120])

print()
print("  --- 阴性对照：真正的 CUDA 报错仍要给 CUDA 指引 ---")
real = a._gpu_help("large-v3", "Library cublas64_12.dll is not found or cannot be loaded")
print("  （节选）", real.split("\n")[0])
check("CUDA 运行库" in real, "真 CUDA 报错仍判为运行库缺失", real[:80])
check("显卡初始化失败" not in real or "CUDA 运行库" in real,
      "真 CUDA 报错仍走 CUDA 指引", real[:80])

print()
print("=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
