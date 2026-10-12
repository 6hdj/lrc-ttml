"""冒烟打包：只带 Qt（约 60MB，构建约 1 分钟），专门验证
"冻结之后桌面版的入口/参数转发/资源路径还对不对"。

比全量重建（8-10 分钟、3GB）快得多；全量只在最后出安装包时做一次。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
os.chdir(ROOT)
HERE = os.path.join(ROOT, ".tmp")
os.makedirs(HERE, exist_ok=True)
assert os.path.isfile(os.path.join(ROOT, "launcher.py")), ROOT

SPEC = os.path.join(HERE, "smoke_qt.spec")
LAUNCHER = os.path.join(ROOT, "launcher.py")
with open(SPEC, "w", encoding="utf-8") as fh:
    fh.write('''# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

ROOT = r"%s"
WORK = os.getcwd()

datas = [(os.path.join(ROOT, "desktop/resources/model_manifest.json"),
          "desktop/resources")]
binaries = []
hidden = ["desktop", "desktop.app", "desktop.main_window", "desktop.context",
          "desktop.paths", "desktop.config", "desktop.fileguess",
          "desktop.pages", "desktop.pages.base", "desktop.pages.welcome",
          "desktop.pages.home", "desktop.pages.tasks", "desktop.pages.models",
          "desktop.pages.settings", "desktop.services",
          "desktop.services.task_manager", "desktop.services.model_manager",
          "desktop.services.convert_params",
          "desktop.services.environment_service", "desktop.services.gpu_probe",
          "desktop.services.log_service", "desktop.workers",
          "desktop.selftest", "desktop.uicheck"]
for m in ("PySide6", "shiboken6"):
    r = collect_all(m)
    datas += r[0]; binaries += r[1]; hidden += r[2]

a = Analysis([os.path.join(ROOT, "launcher.py")], pathex=[ROOT, WORK],
             binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=[
                 "torch", "numpy", "faster_whisper", "ctranslate2", "av",
                 "onnxruntime", "demucs", "pypinyin", "huggingface_hub",
                 "tkinter"],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Lyric2TTML",
          console=True, upx=True)
coll = COLLECT(exe, a.binaries, a.datas, upx=True, name="Lyric2TTML")
''' % ROOT.replace("\\", "\\\\"))

env = dict(os.environ)
env["L2T_WITH_QT"] = "1"
env["TMP"] = env["TEMP"] = os.path.join(ROOT, ".tmp", "pyi-smoke")
os.makedirs(env["TMP"], exist_ok=True)
cmd = [os.path.join(ROOT, ".venvpack", "Scripts", "python.exe"), "-m", "PyInstaller",
       "--noconfirm", "--distpath", os.path.join(ROOT, "dist", "_smoke"),
       "--workpath", os.path.join(ROOT, "build", "_smoke"), SPEC]
print("building smoke bundle…", flush=True)
p = subprocess.run(cmd, cwd=ROOT, env=env)
if p.returncode != 0:
    sys.exit(p.returncode)
print("build done", flush=True)

exe = os.path.join(ROOT, "dist", "_smoke", "Lyric2TTML", "Lyric2TTML.exe")
base = os.path.dirname(exe)
data = os.path.join(base, ".smoke-data")
senv = dict(os.environ)
senv.update({"QT_QPA_PLATFORM": "offscreen", "L2T_ALLOW_MULTI": "1",
             "L2T_HEADLESS": "1", "L2T_DATA_DIR": data,
             "PYTHONIOENCODING": "utf-8"})

CASES = [
    ("--version", ["--version"], 0),
    ("--help", ["--help"], 0),
    ("--model-cli check", ["--model-cli", "check"], None),
    ("--selftest 1 2 3", ["--selftest", "1", "2", "3"], None),
    ("--uicheck", ["--uicheck"], 0),
]
fail = []
for label, args, expect in CASES:
    try:
        r = subprocess.run([exe] + args, capture_output=True, timeout=300,
                           cwd=base, env=senv)
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT {label}")
        fail.append(label)
        continue
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    good = (expect is None) or (r.returncode == expect)
    print(f"  {'OK  ' if good else 'FAIL'} {label}  exit={r.returncode}")
    if not good:
        fail.append(label)
        print("    stdout:", out.strip()[-900:])
        print("    stderr:", err.strip()[-900:])
    elif label == "--uicheck":
        tail = [ln for ln in out.splitlines() if "通过" in ln]
        print("    ", " / ".join(tail[-2:]) or out.strip()[-200:])

print()
if fail:
    print("未通过：", fail)
    sys.exit(1)
print("冒烟打包全部通过 ✓")
