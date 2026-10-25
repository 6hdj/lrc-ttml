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

# ⚠️ 与 `win_integration.EXE_NAME` 必须一致（那边是"建快捷方式/注册表"用的，
# 这边是"卸载时要保留哪个 exe"用的）。两处不一致会出现
# "卸载时把正在运行的 exe 当普通文件删"这类怪问题。
EXE_NAME = "Lyric2TTML.exe"


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
        # ⚠️⚠️ **只有"里面真有东西"才值得为它中止卸载**。真踩到：
        # `_e2e` 装完（没下模型）时 `<安装目录>/.hf_cache` 是个**空目录**
        # （0.0B），但老代码照样走"搬模型"分支 → `move_tree` 在受限环境里
        # `WinError 5` → 打印"模型没能安全搬走，**没有删除任何东西**" →
        # **整个卸载什么都不做**，用户看到的是"点了卸载没反应"，
        # 还被要求去手工复制一个**空目录**。
        # 现在：没有模型内容就直接删掉那个空壳，继续正常卸载。
        # ⚠️⚠️ **只在目录"完全是空的"时才跳过搬移。**
        # 真踩到：`--e2e` 装完（没下模型）时 `<安装目录>/.hf_cache` 是个**空目录**
        # （0.0B），但老代码照样走"搬模型"分支 → `move_tree` 在受限环境里
        # `WinError 5` → 打印"模型没能安全搬走，**没有删除任何东西**" →
        # **整个卸载什么都不做**（用户看到"点了卸载没反应"，还被要求去手工
        # 复制一个空目录）。
        #
        # ⚠️ 判据必须是"**大小为 0**"，不能是"小于某个阈值"（我第一版用了 1MB，
        # 结果把"搬不动就什么都别删"这条**数据安全保证**给绕过了 ——
        # `tools/test_uninstall.py` 当场抓出来：4KB 的模型搬不动时它照样删了）。
        # 只要里面有**任何**内容，就必须坚持"搬不走就什么都不删"。
        _empty = dir_size(hf_in_install) == 0
        if keep_models and not _empty:
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
        elif keep_models:
            print(f"  安装目录里的模型缓存是空的（{human(dir_size(hf_in_install))}），"
                  f"直接清掉。")
            shutil.rmtree(hf_in_install, ignore_errors=True)
        else:
            print(f"  正在删除安装目录里的模型（{human(dir_size(hf_in_install))}）…")
            shutil.rmtree(hf_in_install, ignore_errors=True)

    # ---- ② 用户数据目录里的模型 ----
    if os.path.isdir(hf_in_data) and not keep_models:
        print(f"  正在删除模型缓存：{data_dir}")
        shutil.rmtree(data_dir, ignore_errors=True)
    elif os.path.isdir(hf_in_data):
        print(f"  模型缓存保留在：{data_dir}")

    # ---- ②.5 运行组件（CUDA 运行库 / PyTorch）----
    # ⚠️ 这是**用户下载的**东西（CUDA 529 MB + PyTorch 509 MB），
    # 2026-10-15 实测：旧逻辑把它当垃圾删了 → 重装后软件认为 CUDA 没装 →
    # 在约 0.5 MB/s 的链路上重下 529 MB。所以这里明确保留并**告诉用户它在哪**，
    # 让想彻底删的人自己删（不能替他决定花掉这 1 GB 流量）。
    rt = os.path.join(install_dir, "runtime")
    if not keep_models:
        print(f"  正在删除运行组件（CUDA / PyTorch）：{human(dir_size(rt))}")
        shutil.rmtree(rt, ignore_errors=True)
    elif os.path.isdir(rt) and dir_size(rt) > 0:
        print(f"  [OK] 运行组件已保留（CUDA / PyTorch，{human(dir_size(rt))}）：{rt}")
        print("        重装时会自动复用，不必重新下载；想彻底删掉请手动删除该目录。")

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

    # ---- ④ 程序文件：**先 cd 走**，再删自己所在的目录 ----
    try:
        os.chdir(os.environ.get("TEMP") or os.path.expanduser("~"))
    except OSError:
        pass
    print("  正在删除程序文件…")

    # ==================================================================
    #  ⚠️⚠️ **绝对不能直接 `rmtree(install_dir)`** —— 那会毁掉"重试"能力
    # ==================================================================
    # 真踩到（`tools/test_uninstall_real.py`）：Windows **只锁正在运行的那个
    # exe，不锁 `_internal/`**（Python 运行库已经加载进内存，磁盘上的文件是可删的）。
    # 于是 `rmtree` 把 `_internal/` 删了个精光 —— 实测 **384 个文件 → 剩 32 个**，
    # 连 `encodings` 模块都没了。而冻结版 exe **必须靠 `_internal/` 才能启动**，
    # 所以用户按提示"再运行一次"，拿到的是：
    #
    #     Failed to start embedded python interpreter:
    #     Failed to import encodings module
    #
    # ⇒ **永远清不干净**，而日志还在说"再运行一次就没了"。这是把用户引进死路。
    #
    # 现在：**保留 exe 与 `_internal/`**（这两个是"卸载程序还能再跑一次"的前提），
    # 其余文件照删；最后交给退出后运行的清理助手把整个目录收掉。
    _keep = _preserve_paths(install_dir)
    _rmtree_except(install_dir, _keep)

    if not os.path.isdir(install_dir):
        print("  [完成] 已卸载。")
        return 0

    # ------------------------------------------------------------------
    # 让"删除自己"变成可能：把 exe 登记为**重启时删除**
    # ------------------------------------------------------------------
    # Windows 不允许删除正在运行的可执行文件，这是操作系统层面的限制
    # （实测带被占用文件的目录连**重命名**都做不了：`Access is denied`）。
    # `MoveFileEx(..., MOVEFILE_DELAY_UNTIL_REBOOT)` 是官方给的办法：
    # 登记之后由系统在下次重启时删掉它。**只影响这一个文件**，
    # 不会像 `PendingFileRenameOperations` 那样动别的程序。
    # 用户界面上的"立即重启？"提示由提供方负责 —— 我们**不弹**，只在日志里说明。
    exe = os.path.join(install_dir, EXE_NAME)
    if os.path.isfile(exe) and _schedule_delete_on_reboot(exe):
        print("  [提示] 主程序文件正在运行、无法当场删除，已登记为**重启时自动删除**。")
        print("         （不重启也不影响使用：卸载本身已经完成，快捷方式与"
              "「已安装的应用」条目都已移除。）")

    if not os.path.isdir(install_dir):
        print("  [完成] 已卸载。")
        return 0

    # ==================================================================
    #  ⚠️⚠️ 删不干净时的处理 —— 这里原来有个**会毁掉"重试"能力的真 bug**
    # ==================================================================
    # 原行为：`rmtree(ignore_errors=True)` 能删多少删多少，然后告诉用户
    # "再运行一次这个卸载程序就干净了"。
    #
    # 实测（`tools/test_uninstall_retry.py`）这条路**走不通**：
    #   * Windows 只锁**正在运行的那个 exe**，不锁 `_internal/`（Python 运行库
    #     已经被加载进内存，文件本身是可删的）；
    #   * 于是 rmtree 把 `_internal/` 删了个精光（384 个文件 → 剩 32 个，
    #     `encodings` 模块都没了），只留下那个 exe；
    #   * 而冻结版 exe **必须靠 `_internal/` 才能启动** —— 用户按提示再运行一次，
    #     得到的是 `Failed to start embedded python interpreter:
    #     Failed to import encodings module`，**永远清不干净**。
    #   * 更糟的是它还在日志里说"再运行一次就没了"，等于把用户引进死路。
    #
    # 正确做法：**把剩下的整个目录交给一个"退出后才跑"的清理助手**。
    # 这样既不用在运行中自残（`_internal` 保持完好），也能真正删干净。
    # 另外这也是唯一可行的路径 —— 实测**带被占用文件的目录连重命名都做不了**
    # （`Access is denied`），所以"改名后再删"这种取巧行不通。
    helper = _schedule_cleanup(install_dir)
    if helper:
        print(f"  [提示] 剩下的文件要等本程序退出后才能删（Windows 不允许删"
              f"正在运行的程序）。")
        print(f"         已安排自动清理，**几秒后会自动删干净**，不用你手动处理。")
        print(f"         （清理脚本：{helper}）")
        print("  [完成] 已卸载（最后一步在后台完成）。")
        return 0

    # 连清理助手都写不出来（TEMP 不可写等）：老实说清楚，别再骗用户"再点一次"
    leftover = _leftovers(install_dir)
    print(f"  [提示] 还有 {human(dir_size(install_dir))} 没删掉"
          f"（{len(leftover)} 个文件）。")
    print("         原因是 Windows 不允许删除正在运行的程序，而自动清理"
          "没能安排成功。")
    print("         请**手动把下面这个文件夹删掉**即可：")
    print(f"           {install_dir}")
    return 1


def _preserve_paths(install_dir: str) -> set:
    """卸载时必须**留下**的路径（相对安装目录）—— 它们决定"还能不能再跑一次"。

    * `Lyric2TTML.exe`：正在运行的那个进程本体，Windows 删不掉；
    * `_internal/`：冻结版的 Python 运行库。**删了 exe 就再也起不来**
      （实测 `Failed to import encodings module`），
      于是用户按提示重试也会失败 —— 必须保住。
    * 任何 PyInstaller 运行库目录（老版本布局可能不同名）。
    * ⚠️⚠️ **`runtime/`：用户下载的运行组件（CUDA 529 MB + PyTorch 509 MB）**。
      这是 2026-10-15 用户实测的**真事故**：他重装一次，`runtime/` 被清空，
      软件于是认为"CUDA 没装"，**在一条约 0.5 MB/s 的链路上重下 529 MB**
      （界面显示"下载中 9.0B/528.8MB"卡了 638 秒以上）。
      `runtime/` 和模型缓存一样是**用户花流量换来的**，不能当垃圾删。
    """
    keep = {EXE_NAME, "_internal", "runtime"}
    # 兼容"onedir 但把运行库放在别处"的情况：凡是 exe 旁边的 dll 也留下
    return keep


def _rmtree_except(root: str, keep: set) -> None:
    """删掉 `root` 下除了 `keep` 里那些顶层项之外的所有东西（尽力而为）。

    ⚠️ 用"自底向上"删：先删能删的，删不掉的（被占用的）留在原地，
    不影响其它条目 —— `shutil.rmtree(ignore_errors=True)` 遇到删不掉的文件时
    行为不够可预期，而且它**没法排除**要保留的目录。
    """
    keep_lower = {k.lower() for k in keep}
    for base, dirs, files in os.walk(root, topdown=False):
        rel = os.path.relpath(base, root)
        top = rel.split(os.sep)[0]
        if rel != "." and top.lower() in keep_lower:
            continue                              # 整个保留目录，跳过
        for f in files:
            p = os.path.join(base, f)
            if os.path.relpath(p, root).split(os.sep)[0].lower() in keep_lower:
                continue
            try:
                os.chmod(p, 0o666)
            except OSError:
                pass
            try:
                os.remove(p)
            except OSError:
                pass                              # 被占用就留着，由清理助手收尾
        for d in dirs:
            p = os.path.join(base, d)
            if os.path.relpath(p, root).split(os.sep)[0].lower() in keep_lower:
                continue
            try:
                os.rmdir(p)
            except OSError:
                pass


def _schedule_delete_on_reboot(path: str) -> bool:
    """把 `path` 登记为"下次重启时删除"（Windows 官方口子）。

    为什么需要：卸载进程就是 `<安装目录>\\Lyric2TTML.exe` 起起来的，
    而 **Windows 不允许删除正在运行的可执行文件**（也无法通过重命名绕过 ——
    实测带被占用文件的目录 `Rename-Item` 会 `Access is denied`）。
    唯一的官方办法是 `MoveFileEx(..., MOVEFILE_DELAY_UNTIL_REBOOT)`。

    ⚠️ 只登记**这一个文件**，不会像 `PendingFileRenameOperations` 那样
    影响别的程序；不重启也不影响"软件已经卸载"这件事
    （快捷方式、注册表项、其它文件都已经处理掉了）。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
        return bool(ctypes.windll.kernel32.MoveFileExW(
            str(path), None, MOVEFILE_DELAY_UNTIL_REBOOT))
    except Exception:                                   # noqa: BLE001
        return False


def _pick_writable_dir(target: str) -> str:
    """挑一个**真能写**的临时目录放清理脚本；都不行返回空串。

    ⚠️ 三条不能省（都是真踩到的）：
    1. **必须真写一个探针文件**再删掉 —— `os.path.isdir()` + `os.access(W_OK)`
       在 Windows 上对 ACL **不可靠**（实测说"可写"却 `PermissionError`）；
    2. **排除 `target` 内部** —— 清理脚本要删的就是 `target`，
       脚本自己住在里面时 `rmdir /s /q` 会失败（文件被 cmd 占用），
       等于白排一个清理；
    3. 候选顺序：环境变量 → `LOCALAPPDATA\\Temp` → `USERPROFILE` 下的 Temp →
       `C:\\Windows\\Temp` → **`target` 的上一级目录**。
    """
    # ⚠️⚠️ **不要用 `tempfile.gettempdir()`**（第二次踩到才发现）：
    # 它内部有"候选全失败就退回当前工作目录"的逻辑，而卸载过程中
    # 当前目录**可能已经被删掉**（安装目录就是 cwd 时）——
    # 那时它会直接抛 `FileNotFoundError`，把整个清理安排搞崩。
    # 实测：探针里 print(tempfile.gettempdir()) 直接把卸载进程打挂了。
    # 所以这里用**确定性候选**，一项项真写探针。
    cands = []
    for k in ("TEMP", "TMP"):
        v = os.environ.get(k)
        if v:
            cands.append(v)
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(os.path.join(la, "Temp"))
    up = os.environ.get("USERPROFILE")
    if up:
        cands.append(os.path.join(up, "AppData", "Local", "Temp"))
    cands.append(r"C:\Windows\Temp")
    # 最后兜底：安装目录的**上一级**（通常可写，且不在要删的目录里）
    try:
        cands.append(os.path.dirname(os.path.abspath(target)))
    except Exception:                                   # noqa: BLE001
        pass

    tgt = os.path.abspath(target)
    seen = set()
    for c in cands:
        if not c:
            continue
        try:
            c = os.path.abspath(c)
        except Exception:                               # noqa: BLE001
            continue
        if c in seen or not os.path.isdir(c):
            continue
        seen.add(c)
        # 不能是 target 自己或它的子目录
        if c == tgt or c.startswith(tgt + os.sep):
            continue
        probe = os.path.join(c, f".l2t-wtest-{os.getpid()}")
        try:
            with open(probe, "w") as fh:
                fh.write("x")
            os.remove(probe)
            return c
        except OSError:
            continue
    return ""


def _schedule_cleanup(target: str) -> str:
    """写一个"等我们退出后删除 `target`"的批处理，并**脱离**启动它。

    返回脚本路径；失败返回空串。

    ⚠️ 为什么用 .bat 而不是在 Python 里循环重试：卸载进程**自己就住在**
    要删的目录里，它不可能删掉自己运行所需的文件。必须换一个**不在那个目录里**
    的进程来收尾。批处理是最小、最不依赖环境的选择（cmd.exe 必然存在）。

    ⚠️ 批处理必须 **CRLF + GBK**（LF 会让 cmd 吞掉下一行首字符；
    见 AGENTS.md 里 `.bat` 的铁律）。
    """
    import subprocess
    import tempfile
    import time
    # ⚠️⚠️ **临时目录要"试着写一下"再选**（2026-10-14 真踩到）。
    #
    # 原来的写法是 `os.environ.get("TEMP") or tempfile.gettempdir()`：
    # **只要 TEMP 有值就直接用它**，而 TEMP 不可写时不会回退 ——
    # 于是清理助手写不出来 → 卸载报"没删干净、请手动删"。
    #
    # 实测触发场景（我自己的机器上就复现了）：
    #   * `TEMP` / `TMP` 都指向 `%TEMP%`；
    #   * `os.path.isdir()` 说存在、`os.access(W_OK)` 说可写
    #     （**Windows 上 `os.access` 对 ACL 不可靠**）；
    #   * 但真去 `open(..., "w")` 就是 `PermissionError: [Errno 13]`。
    #   * `tempfile.gettempdir()` 于是逐个候选回退，最后**返回了工作目录** ——
    #     而把清理脚本写进工作目录/安装目录是**无效甚至有害**的
    #     （脚本要删的目录里包含它自己时 `rmdir` 会失败）。
    #
    # 现在：列出候选 → **排除 target 内部** → 逐个真写一个探针文件 →
    # 用第一个真正能写的。都写不了才老实返回空（并让上层给出人工提示）。
    tmp = _pick_writable_dir(target)
    if not tmp:
        return ""
    script = os.path.join(tmp, f"l2t-cleanup-{os.getpid()}.bat")
    log = os.path.join(tmp, f"l2t-cleanup-{os.getpid()}.log")
    # timeout /t 2 等 2 秒让本进程退出；然后最多重试 10 次（每次 1 秒）
    #
    # ⚠️⚠️ **不能直接 `rmdir /s /q "<target>"`** —— 那会把 `runtime/`
    # （用户下的 CUDA 529 MB + PyTorch 509 MB）一起收掉，下一次装完又要重下。
    # 2026-10-15 用户实测就是这么丢的（重装 → 认为 CUDA 没装 → 重下 529 MB，
    # 在约 0.5 MB/s 的链路上卡了 10 分钟以上）。
    # 所以这里**逐项删，跳过 runtime**：
    #   * 先删所有子目录（`runtime` 除外）；
    #   * 再删目录下的散文件（exe / bat / 说明…）；
    #   * `runtime` 与目录本身留着 —— 下次安装直接复用。
    # 想彻底删的用户按结尾提示删一次即可（不能替用户做，那是 1 GB 流量）。
    lines = [
        "@echo off",
        "rem Lyric2TTML 卸载收尾：等主进程退出后清空安装目录（保留 runtime 运行组件）",
        f'echo cleanup start > "{log}"',
        "ping -n 3 127.0.0.1 > nul",
        "for /L %%i in (1,1,10) do (",
        f'  if not exist "{target}" goto done',
        f'  for /d %%D in ("{target}\\*") do (',
        '    if /i not "%%~nxD"=="runtime" rd /s /q "%%D" 2>nul',
        "  )",
        f'  del /q "{target}\\*.*" 2>nul',
        # ⚠️ **不带 /s**：目录空了才删得掉；还留着 runtime 时它会失败（正是我们要的）。
        # 少了这一句，`runtime` 不存在的普通机器上目录会**残留** ——
        # `tools/test_uninstall.py` 的"安排了清理助手但目录没被清掉"当场抓出来了。
        f'  rd "{target}" 2>nul',
        "  ping -n 2 127.0.0.1 > nul",
        ")",
        ":done",
        f'if exist "{target}\\Lyric2TTML.exe" (echo leftover >> "{log}") else (echo clean >> "{log}")',
        f'if exist "{target}\\runtime" (echo kept-runtime >> "{log}")',
        'del "%~f0"',
    ]
    try:
        with open(script, "w", encoding="gbk", newline="\r\n", errors="replace") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        return ""
    try:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：不与本进程共享控制台，
        # 也不会因为本进程退出而被带走
        subprocess.Popen(["cmd", "/c", script],
                         creationflags=0x00000008 | 0x00000200,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         close_fds=True)
    except OSError:
        return ""
    time.sleep(0.2)                       # 给它一点时间真的起来
    return script


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
