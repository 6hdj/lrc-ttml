"""卸载逻辑（**不依赖 tkinter**）。

为什么单独一个模块，而不是留在 `installer_gui.py` 里：
`launcher.py --uninstall` 要能跑在**主程序**（`Lyric2TTML.exe`）里，
而主程序的构建**特意排除了 tkinter**（几百 KB，GUI 用不上）。
放在 `installer_gui` 里会导致：

    File "installer_gui.py", line 27, in <module>
    ModuleNotFoundError: No module named 'tkinter'

—— 卸载直接失败（真踩过，E2E 里抓到的）。所以这里把卸载逻辑抽出来，
`installer_gui` 只负责"界面 + 打包"，卸载只依赖标准库。

⚠️⚠️ **搬移 3GB 的模型绝对不要交给 `.bat` 里的 xcopy / robocopy**：
实测在"路径含中文 + 目标子目录还不存在"时，两者都会**报成功却没搬**
（`xcopy` 打印 `1 File(s) copied` 返回 0；`robocopy /MOVE` 报告
"1 个文件已复制"、源也消失，但存在性和 `os.path` 都看不到目标目录）。
随后如果 `rmdir /s /q` 安装目录，用户那 3GB 就白下了。
所以：**搬移在 Python 里做**（`shutil.move`，同盘是 rename），
并且**搬完真的去验证**"源已空 + 目标有东西"，验证不过就什么都不删。
"""
from __future__ import annotations

import os
import shutil
import sys


def default_data_dir() -> str:
    """桌面版放配置/模型/日志的地方（与 ``desktop/paths.data_dir()`` 一致）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Lyric2TTML")


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def move_tree(src: str, dst: str) -> bool:
    """把 ``src`` 搬到 ``dst``，返回是否**确认成功**（见模块 docstring）。"""
    try:
        parent = os.path.dirname(dst.rstrip("\\/"))
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.isdir(dst):
            # 目标已存在：逐个合并，免得 shutil.move 把整个目录塞成子目录
            for name in os.listdir(src):
                s, d = os.path.join(src, name), os.path.join(dst, name)
                if os.path.exists(d):
                    if os.path.isdir(s) and os.path.isdir(d):
                        if not move_tree(s, d):
                            return False
                        continue
                    if os.path.isdir(d):
                        shutil.rmtree(d, ignore_errors=True)
                    else:
                        os.remove(d)
                shutil.move(s, d)
        else:
            shutil.move(src, dst)
    except Exception as e:                              # noqa: BLE001
        print(f"  [搬移失败] {type(e).__name__}: {e}")
        return False
    # **验证**：源已经空了（不存在或没内容）、目标里有东西，才算成功。
    # ⚠️ 不能只判"源不存在"：目标已存在时是**逐个合并**，源目录会留下来、
    # 只是变空 —— 那种情况同样是成功的。
    try:
        src_empty = (not os.path.exists(src)) or (not os.listdir(src))
        return src_empty and os.path.isdir(dst) and bool(os.listdir(dst))
    except OSError:
        return False


def uninstall(install_dir: str, keep_models: bool = True,
              assume_yes: bool = False, desk: str = "",
              programs: str = "") -> int:
    """删安装目录 + 快捷方式 + 卸载注册表项；**默认保留模型**。

    返回 0 成功 / 1 有东西没删掉 / 2 用户取消 / 3 拒绝执行（怕丢模型）。

    ⚠️ `desk` / `programs` 是**测试注入点**：不传就会落到**用户真实的
    桌面与开始菜单**（真踩过：测试跑完在真实开始菜单里留了东西）。
    自测请一律传沙箱路径，见 `tools/test_uninstall.py`。
    """
    install_dir = os.path.abspath(install_dir)
    if not os.path.isdir(install_dir):
        print(f"  目录不存在：{install_dir}")
        return 1
    data_dir = default_data_dir()
    desk = desk or os.path.join(os.path.expanduser("~"), "Desktop")

    print("=" * 58)
    print("  卸载 Lyric2TTML")
    print(f"  安装目录：{install_dir}")
    hf_in_install = os.path.join(install_dir, ".hf_cache")
    hf_in_data = os.path.join(data_dir, ".hf_cache")
    if os.path.isdir(hf_in_install):
        print(f"  安装目录里有模型：{human(dir_size(hf_in_install))}")
    if os.path.isdir(hf_in_data):
        print(f"  用户数据目录里有模型：{human(dir_size(data_dir))}")
    print("=" * 58)

    if not assume_yes:
        try:
            ans = input("  确定卸载吗？[Y=是  N=取消] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            print("  已取消。")
            return 2

    # ---- ① 安装目录里的模型：默认先搬走 ----
    if os.path.isdir(hf_in_install):
        if keep_models:
            print(f"  正在把模型搬到：{hf_in_data}")
            if not move_tree(hf_in_install, hf_in_data):
                # ⚠️ **搬不动就什么都别删** —— 宁可卸载不干净，
                # 也不能让用户白下 3GB
                print("  [中止] 模型没能安全搬走，所以**没有删除任何东西**。")
                print("         请先手工把下面这个目录复制到别处：")
                print(f"           {hf_in_install}")
                print("         复制好之后再运行一次本卸载程序。")
                return 3
            print(f"  [OK] 模型已保留：{hf_in_data}")
        else:
            print(f"  正在删除安装目录里的模型（{human(dir_size(hf_in_install))}）…")
            shutil.rmtree(hf_in_install, ignore_errors=True)

    # ---- ② 用户数据目录里的模型 ----
    if os.path.isdir(hf_in_data) and not keep_models:
        print(f"  正在删除模型缓存：{data_dir}")
        shutil.rmtree(data_dir, ignore_errors=True)
    elif os.path.isdir(hf_in_data):
        print(f"  模型缓存保留在：{data_dir}")

    # ---- ③ 桌面 / 开始菜单快捷方式 + 卸载注册表项 ----
    # ⚠️⚠️ **必须与安装用同一份逻辑**（`win_integration`），否则必然漂移：
    # 安装时写进去的开始菜单项和注册表项，卸载时漏删就成了
    # "设置里留一个删不掉的条目 + 桌面留一个死链接"，用户会认定没卸干净。
    _integrated = False
    try:
        import win_integration
        win_integration.integrate_remove(install_dir, desk=desk,
                                         programs=programs)
        _integrated = True
    except Exception as e:                              # noqa: BLE001
        print(f"  [提示] 外壳集成清理出错（继续卸载）：{type(e).__name__}: {e}")
    if not _integrated:
        # 退路：至少把桌面快捷方式删掉，别让小错挡住卸载
        for name in ("Lyric2TTML.lnk", "卸载 Lyric2TTML.lnk"):
            p = os.path.join(desk, name)
            try:
                if os.path.isfile(p):
                    os.remove(p)
                    print(f"  已删除快捷方式：{name}")
            except OSError as e:
                print(f"  [提示] 快捷方式没删掉（{name}）：{e}")

    # ---- ④ 程序文件：**先 cd 走**再删自己所在的目录 ----
    try:
        os.chdir(os.environ.get("TEMP") or os.path.expanduser("~"))
    except OSError:
        pass
    print("  正在删除程序文件…")
    shutil.rmtree(install_dir, ignore_errors=True)
    if os.path.isdir(install_dir):
        # ⚠️ 常见情况：**卸载脚本自己就是 `<安装目录>\Lyric2TTML.exe` 起的**，
        # Windows 不允许删除正在运行的可执行文件，于是目录里只剩它一个
        # —— 那不是失败，只是"必须先退出才能删自己"。
        # 处理：把还能删的再删一遍，然后明确告诉用户"再点一次就干净了"
        # （第二次运行时 exe 已经不在安装目录里了）。
        leftover = _leftovers(install_dir)
        only_exe = all(p.lower().endswith(".exe") for p in leftover)
        if leftover:
            print(f"  [提示] 还有 {human(dir_size(install_dir))} 没删掉"
                  f"（{len(leftover)} 个文件）。")
            if only_exe:
                print("         剩下的就是**正在运行的卸载程序自己**，"
                      "关掉之后它就没了；")
                print("         想立刻清干净，可以再运行一次这个卸载程序。")
            else:
                print("         多半被占用/只读：把软件关掉之后再运行一次即可。")
            return 1
    print("  [完成] 已卸载。")
    return 0


def _leftovers(root: str, limit: int = 8):
    """列出删不掉的文件（给提示用，最多列一些，避免刷屏）。"""
    out = []
    for r, _d, fs in os.walk(root):
        for f in fs:
            out.append(os.path.join(r, f))
            if len(out) >= limit:
                return out
    return out


def uninstall_cli(argv=None) -> int:
    """``Lyric2TTML.exe --uninstall <目录> [--with-models] [--yes]``

    `卸载程序.bat` 就是调这个。放在这里而不是写成一大串 .bat，
    是为了**把"搬 3GB 模型"交给可测的 Python 代码**（见模块 docstring）。
    """
    import argparse
    ap = argparse.ArgumentParser(prog="卸载", description="卸载 Lyric2TTML")
    ap.add_argument("install_dir")
    ap.add_argument("--keep-models", action="store_true", default=True,
                    help="保留模型（默认）")
    ap.add_argument("--with-models", action="store_true",
                    help="连模型一起删除（约 3GB）")
    ap.add_argument("--yes", action="store_true",
                    help="不再问一次（给脚本 / 自动测试用）")
    ap.add_argument("--desktop", default="", help="桌面目录（测试用）")
    a = ap.parse_args(argv)
    return uninstall(a.install_dir, keep_models=not a.with_models,
                     assume_yes=a.yes, desk=a.desktop)


if __name__ == "__main__":
    sys.exit(uninstall_cli())
