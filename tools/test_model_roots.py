"""验证「按文件找根」的修复：程序目录有同名文件夹但缺文件时，要去找 _internal。

    python .tmp/test_roots_files.py
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from desktop.services import model_manager as mm  # noqa: E402

_ok = _fail = 0


def check(cond, label, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _fail += 1
        print(f"  FAIL {label}" + (f"   ← {extra}" if extra else ""))


SB = os.path.join(HERE, ".tmp", "roots-files")
shutil.rmtree(SB, ignore_errors=True)
app = os.path.join(SB, "app")            # 程序目录（模拟安装目录）
internal = os.path.join(SB, "_internal")  # 包内部（PyInstaller 的 _internal）
for d in (app, internal):
    os.makedirs(d, exist_ok=True)

# 程序目录：有 models/campplus 目录，但只有**权重**（用户下过），没有 onnx
d1 = os.path.join(app, "models", "campplus")
os.makedirs(d1, exist_ok=True)
with open(os.path.join(d1, "campplus_cn_common.bin"), "wb") as fh:
    fh.write(b"W" * 4096)
# 包内部：有随包提供的 onnx
d2 = os.path.join(internal, "models", "campplus")
os.makedirs(d2, exist_ok=True)
with open(os.path.join(d2, "campplus.onnx"), "wb") as fh:
    fh.write(b"O" * 8192)

manifest = {"schema_version": 1, "models": [{
    "id": "campplus-onnx", "category": "diarization", "name": "CAM++ ONNX 图",
    "download_policy": "bundled", "approx_size": 8192,
    "source": {"kind": "bundled", "path": "models/campplus/campplus.onnx"},
    "verify": {"kind": "files_exist", "target": "models/campplus",
               "must_have": ["campplus.onnx"]},
}]}
mp = os.path.join(SB, "manifest.json")
with open(mp, "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, ensure_ascii=False)


class M(mm.ModelManager):
    """把资源根固定成 [程序目录, _internal]，模拟冻结版的顺序。"""

    def _model_roots(self):
        return [app, internal]


m = M(manifest_path=mp, app_dir=app)
st = m.check("campplus-onnx", use_cache=False)
print("=" * 74)
print("  程序目录有同名目录但缺文件 → 应该去 _internal 找")
print("=" * 74)
print(f"  status = {st.status}")
print(f"  detail = {st.detail}")
print(f"  location = {st.location}")
check(st.status == mm.READY,
      "**判为已就绪**（原来因为只看第一个目录而报缺失）",
      f"{st.status} / {st.detail}")
check(os.path.join(internal, "models") in (st.location or ""),
      "定位到 _internal 里那份（而不是程序目录那份）", str(st.location))

print()
print("  --- 对照：两个根都没有时必须如实报缺失 ---")
shutil.rmtree(d2)
st2 = m.check("campplus-onnx", use_cache=False)
print(f"  status = {st2.status}  detail = {st2.detail}")
check(st2.status == mm.FAILED and "campplus.onnx" in st2.detail,
      "都没有时仍如实报「缺文件：campplus.onnx」", f"{st2.status}/{st2.detail}")

print()
print("=" * 74)
print(f"  通过 {_ok} 项，失败 {_fail} 项")
print("=" * 74)
sys.exit(1 if _fail else 0)
