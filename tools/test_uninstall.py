"""卸载逻辑的回归测试（**不用 .bat，直接测 Python 侧**）。

    python tools/test_uninstall.py

为什么要单独一个测试：卸载是**不可逆**的，而它要动的是用户下的 3GB 模型。
本仓真踩过的两个坑都在这里守着：

  1. `.bat` 里的 `xcopy` / `robocopy` 在"含中文的路径 + 目标子目录还不存在"
     时会**报成功却没搬**，随后 `rmdir /s /q 安装目录` 就把模型删了
     —— 现在搬移在 Python 侧（`installer_gui._move_tree`），
     并且**搬不动就什么都不删**；
  2. 卸载脚本必须**默认保留**模型（用户需求），只有明确选"删"才清。
"""
from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import installer_gui  # noqa: E402

SANDBOX = os.path.join(HERE, ".tmp", "test-uninstall")
FAILED = []


def case(name, fn):
    try:
        fn()
        print(f"  OK   {name}")
    except Exception as e:                              # noqa: BLE001
        FAILED.append((name, e))
        print(f"  FAIL {name}: {type(e).__name__}: {e}")


def _setup(hf_in_install=True, hf_in_data=False, model_bytes=4096):
    shutil.rmtree(SANDBOX, ignore_errors=True)
    install = os.path.join(SANDBOX, "Lyric2TTML")
    data = os.path.join(SANDBOX, "localappdata", "Lyric2TTML")
    desk = os.path.join(SANDBOX, "Desktop")
    os.makedirs(install, exist_ok=True)
    os.makedirs(desk, exist_ok=True)
    open(os.path.join(install, "Lyric2TTML.exe"), "wb").write(b"MZ" + b"\0" * 64)
    os.makedirs(os.path.join(install, "_internal"), exist_ok=True)
    open(os.path.join(install, "_internal", "x.dll"), "wb").write(b"\0" * 32)
    if hf_in_install:
        d = os.path.join(install, ".hf_cache", "models--Systran--x", "snap", "rev")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "model.bin"), "wb").write(b"M" * model_bytes)
    if hf_in_data:
        d = os.path.join(data, ".hf_cache", "models--Systran--x", "snap", "rev")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "model.bin"), "wb").write(b"M" * model_bytes)
    open(os.path.join(desk, "Lyric2TTML.lnk"), "w").write("x")
    return install, data, desk


def _run(install, desk, keep=True, yes=True):
    """在**子进程**里跑卸载（避免它 os.chdir 影响本测试进程）。"""
    import subprocess
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import installer_gui as g;"
        "sys.exit(g.uninstall(r'%s', keep_models=%r, assume_yes=%r, desk=r'%s'))"
        % (HERE, install, keep, yes, desk)
    )
    env = dict(os.environ, LOCALAPPDATA=os.path.join(SANDBOX, "localappdata"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       env=env, timeout=300, cwd=SANDBOX)
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


def _models_under(root):
    out = []
    for a, _d, fs in os.walk(root):
        for f in fs:
            if f == "model.bin":
                out.append(os.path.join(a, f))
    return out


# ---------------------------------------------------------------- 用例

def t_keep_models_from_install():
    """模型在安装目录里 + 选保留 → 必须**搬到用户数据目录**，不能被删。"""
    install, data, desk = _setup(hf_in_install=True)
    rc, out = _run(install, desk, keep=True)
    assert rc == 0, f"退出码 {rc}\n{out}"
    assert not os.path.isdir(install), "安装目录没删掉"
    found = _models_under(SANDBOX)
    assert found, "模型被删了！这会让用户白下 3GB"
    assert any(os.path.abspath(p).startswith(os.path.abspath(data))
               for p in found), f"模型没搬到数据目录：{found}"
    assert not os.path.isfile(os.path.join(desk, "Lyric2TTML.lnk")), \
        "桌面快捷方式没删"


def t_keep_models_already_in_data():
    """模型本来就在用户数据目录（桌面版默认位置）→ 只删程序，别动模型。"""
    install, data, desk = _setup(hf_in_install=False, hf_in_data=True)
    rc, out = _run(install, desk, keep=True)
    assert rc == 0, f"退出码 {rc}\n{out}"
    assert not os.path.isdir(install), "安装目录没删掉"
    found = _models_under(SANDBOX)
    assert found and all(os.path.abspath(p).startswith(os.path.abspath(data))
                         for p in found), f"数据目录里的模型被动了：{found}"


def t_delete_models_when_asked():
    """明确选"连模型一起删" → 两个位置都要清干净。"""
    install, data, desk = _setup(hf_in_install=True, hf_in_data=True)
    rc, out = _run(install, desk, keep=False)
    assert rc == 0, f"退出码 {rc}\n{out}"
    assert not os.path.isdir(install), "安装目录没删掉"
    assert not _models_under(SANDBOX), "选了删除却还有模型残留"


def t_refuse_when_move_fails():
    """搬不动模型时**必须什么都不删**（宁可卸载不干净，也不能丢 3GB）。

    ⚠️ 这个用例在**本进程**里调 `uninstall()`，所以必须先把 `LOCALAPPDATA`
    指到沙箱 —— 否则它会去动用户真实的 `%LOCALAPPDATA%\\Lyric2TTML`
    （写测试时真踩过：把测试用的小模型文件写进了真实数据目录，得手工清理）。
    """
    install, data, desk = _setup(hf_in_install=True)
    old_local = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = os.path.join(SANDBOX, "localappdata")
    real_move = installer_gui._move_tree

    def boom(src, dst):
        return False                                   # 模拟"搬不动"

    installer_gui._move_tree = boom
    try:
        rc = installer_gui.uninstall(install, keep_models=True, assume_yes=True,
                                     desk=desk)
    finally:
        installer_gui._move_tree = real_move
        if old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local
    assert rc == 3, f"搬不动时应该返回 3（拒绝执行），实际 {rc}"
    assert os.path.isdir(install), "搬不动却把安装目录删了！"
    assert os.path.isfile(os.path.join(install, "Lyric2TTML.exe")), \
        "搬不动却删了程序文件"


def t_move_tree_verifies():
    """`_move_tree` 必须**真的搬**并验证结果（不信"命令返回 0"）。"""
    shutil.rmtree(SANDBOX, ignore_errors=True)
    src = os.path.join(SANDBOX, "src", ".hf_cache", "m", "rev")
    dst = os.path.join(SANDBOX, "dst", ".hf_cache")
    os.makedirs(src, exist_ok=True)
    open(os.path.join(src, "model.bin"), "wb").write(b"M" * 128)
    ok = installer_gui._move_tree(os.path.join(SANDBOX, "src", ".hf_cache"), dst)
    assert ok, "_move_tree 说没搬成功"
    assert not os.path.isdir(os.path.join(SANDBOX, "src", ".hf_cache")), \
        "源还在（没搬走）"
    assert os.path.isfile(os.path.join(dst, "m", "rev", "model.bin")), \
        "目标里没有文件"
    # 目标已存在时也要能合并
    src2 = os.path.join(SANDBOX, "src2", ".hf_cache", "m2", "rev")
    os.makedirs(src2, exist_ok=True)
    open(os.path.join(src2, "model2.bin"), "wb").write(b"M" * 64)
    ok2 = installer_gui._move_tree(os.path.join(SANDBOX, "src2", ".hf_cache"), dst)
    assert ok2, "目标已存在时合并失败"
    assert os.path.isfile(os.path.join(dst, "m2", "rev", "model2.bin")), \
        "合并后没看到第二个模型"


def t_uninstaller_bat_is_thin_launcher():
    """生成的 .bat 必须是**瘦启动器**（GBK + CRLF + 不含搬移逻辑）。"""
    shutil.rmtree(SANDBOX, ignore_errors=True)
    install, data, desk = _setup(hf_in_install=False)
    app = installer_gui.Installer.__new__(installer_gui.Installer)
    app.install_dir = install
    app.say = lambda m: None
    app.write_uninstaller()
    bat = os.path.join(install, "卸载程序.bat")
    assert os.path.isfile(bat), "没生成卸载脚本"
    raw = open(bat, "rb").read()
    text = raw.decode("gbk")                            # 必须是 GBK
    assert b"\n" not in raw.replace(b"\r\n", b""), "有裸 LF（cmd 会吞掉下一行首字符）"
    assert "chcp 65001" not in text, "不许 chcp 65001（中文会乱）"
    assert "--uninstall" in text, "没有把卸载交给主程序"
    # ⚠️ 搬移绝不能再回到 .bat 里
    for bad in ("robocopy", "xcopy", "rmdir /s"):
        assert bad not in text, f"卸载脚本里不该再出现 {bad}（会报成功却没搬）"


case("保留模型：从安装目录搬到数据目录，程序删掉", t_keep_models_from_install)
case("保留模型：模型本来就在数据目录时不动它", t_keep_models_already_in_data)
case("明确要求：连模型一起删干净", t_delete_models_when_asked)
case("搬不动时拒绝执行（什么都不删）", t_refuse_when_move_fails)
case("_move_tree 真搬 + 验证 + 目标已存在时合并", t_move_tree_verifies)
case("卸载脚本是瘦启动器（GBK/CRLF/无搬移逻辑）", t_uninstaller_bat_is_thin_launcher)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项")
    for n, e in FAILED:
        print(f"  ✗ {n}: {type(e).__name__}: {e}")
    sys.exit(1)
print("全部通过 ✓")
