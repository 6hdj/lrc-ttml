"""只跑 build_installer()：把已构建好的 dist/Lyric2TTML 打成单个安装程序。

    python tools/build_installer_only.py

比 `tools/build_release.py --installer` 快得多（后者会先 clean 再重建主程序，
10 分钟起步）。用于"**主程序已经验过**、只想重新打安装包"的场景。

⚠️⚠️ **它只重打安装包，不会重建主程序。**
`uninstall_core.py` / `desktop/*` / `installer_gui.py` 这些是**编译进
`Lyric2TTML.exe`** 的 —— 改了它们却只跑这个脚本，产物里还是旧代码。
本仓真踩过：改完卸载逻辑只重打安装包，测试跑的还是旧实现，白查两轮。
改了主程序模块之后**必须先重建主程序**（见 `tools/release_report.py`
第六节的完整命令）。

⚠️ 这个文件原来在 `.tmp/` 下 —— 而 `.tmp/` 在 `.gitignore` 里，
所以**它在仓库里根本不存在**：别人 clone 下来照着文档敲命令会直接找不到文件。
（真踩到：交付报告的"可重复构建命令"里写着 `.tmp/build_installer_only.py`。）
现在挪到 `tools/` 并入库。
"""
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ⚠️ 不要依赖"调用者的当前目录"：脚本可能从任何地方被调用。
# 统一 `chdir` 到仓库根，后面所有相对路径（dist/、build/、assets/）才成立。
os.chdir(HERE)
sys.path.insert(0, HERE)
import tools.build_release as br  # noqa: E402

print("WITH_QT =", br.WITH_QT)
assert br.WITH_QT, "必须先设 L2T_WITH_QT=1（否则装完没有桌面窗口）"

app_dir = os.path.join("dist", br.APP)
assert os.path.isfile(os.path.join(app_dir, f"{br.APP}.exe")), \
    f"没有主程序：{app_dir}"

# ---- ⓪ ⚠️⚠️ **打包前先刷新"构建元信息"** ----
# `desktop/resources/build_info.json` 记录"这份产物是从哪个 commit 打的"，
# 用户在 `--version` 里能看到，交付报告的"产物新鲜度"也靠它比 commit。
# ⚠️ 但**主程序是上一步 PyInstaller 打进去的**：这里重新生成只会更新
# `desktop/resources/` 下的源文件，**不会**改已经打好的主程序。
# ⇒ 所以这里刷新它**只为**两个用途：
#   ① 提示"你现在这个主程序里的 commit 是哪个"（若与当前 HEAD 不符就该重建主程序）；
#   ② 让随后重打的安装包至少带上最新的元信息副本。
# 真正的做法仍是"改了源码 → 重建主程序"。这一点写在这里免得被误解成"刷新就够了"。
_info = os.path.join(HERE, "desktop", "resources", "build_info.json")
_before = ""
if os.path.isfile(_info):
    try:
        import json as _j
        _before = _j.load(open(_info, encoding="utf-8")).get("git_short", "")
    except Exception:                                   # noqa: BLE001
        pass
subprocess.run([sys.executable, os.path.join(HERE, "tools",
                                             "make_build_info.py")],
               cwd=HERE, check=False)
try:
    import json as _j2
    _after = _j2.load(open(_info, encoding="utf-8")).get("git_short", "")
except Exception:                                       # noqa: BLE001
    _after = ""
if _before and _after and _before != _after:
    print(f"  ⚠️ 主程序里记录的 commit（{_before}）与当前 HEAD（{_after}）**不一致**"
          f" —— 改了源码却只重打安装包，产物里还是旧代码，请重建主程序")

# ---- ① ⚠️ 打包前必须清掉"测试跑出来的运行时残留" ----
# 真踩过：验收脚本在 app 目录里留下了 `.hf_cache`（2.9 GB 的 whisper 模型）、
# `outputs/`、`uploads/`、`.accept-data/`，于是载荷变成 3.30 GB ——
# 安装包会大 3 GB，而且大的是**测试数据**不是程序。这条一漏，体积数字全是假的。
_STALE = [".hf_cache", "outputs", "uploads", ".accept-data", ".accept-out",
          "logs", ".probe-data"]
for _name in _STALE:
    _p = os.path.join(app_dir, _name)
    if not os.path.exists(_p):
        continue
    _size = sum(os.path.getsize(os.path.join(_r, _f))
                for _r, _d, _fs in os.walk(_p) for _f in _fs)
    print(f"[清理残留] {_name}  ({_size / 1048576:.1f} MiB)")
    if os.path.isdir(_p):
        shutil.rmtree(_p, ignore_errors=True)
    else:
        os.remove(_p)

_payload_guess = sum(os.path.getsize(os.path.join(_r, _f))
                     for _r, _d, _fs in os.walk(app_dir) for _f in _fs)
print(f"主程序目录：{_payload_guess / 1048576:.0f} MiB")
assert _payload_guess < 1_600 * 1024 * 1024, (
    f"主程序目录 {_payload_guess / 1048576:.0f} MiB 明显偏大 —— "
    f"八成又混进了模型缓存/输出。先查清楚再打包，别打出一个假的大包。")

# ---- ② 打成载荷 zip ----
os.makedirs("build", exist_ok=True)
# ⚠️ 文件名**必须**叫 `_app_payload.zip`：`payload_zip()` 就是按这个名字去
# `_MEIPASS/payload/` 里找内置载荷的。改名会让安装程序报"取不到内置载荷"
# （E2E 第一轮就这么白跑了一次）。
payload = os.path.join("build", "_app_payload.zip")
if os.path.exists(payload):
    os.remove(payload)
print(f"打包载荷 → {payload}")
br.zip_dir(app_dir, payload)
size = os.path.getsize(payload) / 1024 ** 3
print(f"载荷 {size:.2f} GB")
assert size < 2.0, (
    f"⚠️ 载荷 {size:.2f} GB 超过 GitHub 单文件 2GB 上限 —— 发布时这个\n"
    f"   安装程序必须是**附件直传**或改成分卷；别把它塞进 release 里等失败。")

# ---- ③ 安装程序本体 ----
out = os.path.join("dist", "安装程序.exe")
# ⚠️ 先删掉旧的：失败时它还在，会让人以为"构建成功了"
if os.path.exists(out):
    os.remove(out)
# ⚠️ **必须用绝对路径**：PyInstaller 6 的 `--add-data` / 脚本路径是相对
# **spec 文件所在目录**解析的，我们传了 `--specpath build`，
# 于是 `build\_app_payload_qt.zip` 被解析成 `build\build\_app_payload_qt.zip`
# → "Unable to find ... when adding binary and data files"（真踩过）。
root = HERE
payload_abs = os.path.abspath(payload)
# ==========================================================================
#  ⚠️⚠️ **不再加 `--uac-admin`**（2026-10-13 用户明确要求，方案①）
# ==========================================================================
# 原来正式安装程序带 `--uac-admin` 清单 ⇒ **双击必弹 UAC**，普通用户装不了；
# 而它的默认安装位置是 `%LOCALAPPDATA%\Lyric2TTML`（**用户目录，根本不需要
# 管理员**）。三者自相矛盾，而且和任务书"普通用户不应强制要求管理员"冲突。
#
# 现在：**per-user 安装，默认不提权**。用户想装到 `Program Files` 那种
# 机器级目录时，自己右键「以管理员身份运行」即可 —— 那时安装器要做的
# 权限判断见 `installer_gui.ensure_dir()`（先试写、失败给人话）。
#
# 副作用（是好事）：`--e2e` 自动化测试终于能直接跑**正式发行的那一个 exe**，
# 不必再单独打"无清单变体"—— 以前"测的那个和发的那个不是同一个文件"。
# 副作用（是好事）：`--e2e` 自动化测试终于能直接跑**正式发行的那一个 exe**，
# 不必再单独打"无清单变体"—— 以前"测的那个和发的那个不是同一个文件"。
# ⇒ 现在**只产出一个** `安装程序.exe`（`--uac-admin` 已移除，两个变体已无差别）。
# ⚠️ 不要再按 `L2T_NO_UAC` 分名字：那会让人分不清"该发哪一个"，
# 而且真正的验证必须在**要发出去的那个文件**上做。
name = "安装程序"
out = os.path.join("dist", f"{name}.exe")
if os.path.exists(out):
    os.remove(out)
variant_dir = os.path.join(root, "build", "_installer_ship")
cmd = [sys.executable, "-m", "PyInstaller",
       "--noconfirm", "--clean", "--onefile", "--name", name,
       ]
# ⚠️⚠️ **双击时不能弹出控制台黑窗**（2026-10-15 用户实测："软件运行和安装的时候
# 不能有这个命令窗口"）。
#
# 安装程序原来只有 `--onefile`，**没有** `hide_console` —— exe 是控制台子系统的，
# 双击就弹一个黑框，里面滚着 `[检查] 程序目录…`、huggingface 的下载进度条，
# 甚至把**它启动的主程序**的输出也一起显示在里面（子进程继承了那个控制台）。
# 用户看到的就是"装个软件还开个命令行"。
#
# `--hide-console hide-early` 是两全的做法（主程序已经用它跑了很久）：
#   * **双击**（程序自己拥有控制台窗口）→ bootloader 立刻把窗口隐藏 ⇒ 看不到黑框；
#   * **从已有控制台 / 带着管道启动**（我们的 `--e2e` 自动化测试、`--version`、
#     用户自己在 cmd 里跑）→ 控制台**不属于它**，所以**不隐藏**，
#     stdout/stderr 照常可用 ⇒ 测试仍然抓得到输出（`test_peruser_e2e` /
#     `accept_frozen` 都靠这个）。
# ⇒ **不要**改成 `--windowed`：那样 `sys.stdout` 会是 None，自动化全瞎。
_HIDE_CONSOLE = "hide-early"
# `--uac-admin` 已移除：见上面的说明。**不要**加回来。
# ⚠️ 安装程序**自己也要带图标** —— 它是用户双击/下载的那个文件。
# 没有图标的话用户在资源管理器里看到的是一张白板，"第一印象"就废了。
_icon = os.path.join(root, "assets", "icon.ico")
if os.path.isfile(_icon):
    cmd += ["--icon", _icon]
    print(f"[icon] 安装程序使用 {_icon}（{os.path.getsize(_icon)} 字节）")
else:
    print("[icon] ⚠️ 没有 assets/icon.ico —— 安装程序会是 PyInstaller 默认图标")
# ⚠️ 版本资源：安装程序**自己也要有**（它也是用户会在属性里看的文件）
_vi = os.path.join(root, "build", "version_info.txt")
if os.path.isfile(_vi):
    cmd += ["--version-file", _vi]
    print(f"[version] 安装程序使用 {_vi}")
else:
    print("[version] ⚠️ 没有 build/version_info.txt（先跑 tools/make_version_info.py）")
cmd += [
       # 双击不弹黑窗；从控制台/管道启动时不隐藏（见上面 _HIDE_CONSOLE 的说明）
       "--hide-console", _HIDE_CONSOLE,
       "--add-data", f"{payload_abs}{os.pathsep}payload",
       # ⚠️⚠️ **不要用 `--runtime-tmpdir` 指到自建子目录**（2026-10-15 试过又撤掉）
       #
       # 背景：用户报"安装程序闪退"，抓到的原始错误是
       #     [PYI-9588:ERROR] Could not create temporary directory!
       # （onefile **必须**先把自己解压到 `_MEIxxxx` 才能跑；这一步失败连
       # Python 都起不来，所以用户看不到任何提示，只有一闪）。
       #
       # 我一度按这个思路改成 `--runtime-tmpdir %LOCALAPPDATA%\Lyric2TTML\tmp`，
       # 实测该选项**确实会展开环境变量**，但**又撤掉了**，原因：
       #   * PyInstaller 只会创建 `_MEIxxxx` **最后一层**，父目录必须已存在；
       #   * `%LOCALAPPDATA%\Lyric2TTML\tmp` 在**全新机器上是两层都不存在**的
       #     ⇒ 会把安装程序**在正常机器上也弄坏**，而且我在这台机器上
       #     无法验证"正常机器"（见下），不能拿用户的安装包赌。
       #
       # 这台机器上的实测结论（都用**计划任务**启动，脱离我的会话沙箱，避免自欺）：
       #   * 同一环境里 `cmd` / `Python` 都能在 `%TEMP%`、`%LOCALAPPDATA%`
       #     建目录并写文件（`icacls` 显示用户 FullControl）；
       #   * 但**任何 PyInstaller onefile**（连 7 MB 的 hello world、
       #     不带 runtime-tmpdir、放 E: 或 C: 都一样）都报
       #     "Could not create temporary directory!"；
       #   * `_MEI` 这个名字本身没被拦（cmd 能建同名目录）；
       #   * **主程序（onedir）在用户环境里完全正常** —— 界面自检 15/15 通过。
       # ⇒ 结论：**这台机器上 onefile 的解压环节不可用**（bootloader 层面），
       #    与我们的代码无关。**保持默认**（用系统 TEMP）——
       #    正常机器上本来就好用，不要为了这台机器把所有人的安装包搭进去。
       "--hidden-import", "install",
       "--hidden-import", "download_model",
       "--hidden-import", "asr",
       "--hidden-import", "envcheck",
       "--hidden-import", "installer_gui",
       "--hidden-import", "uninstall_core",
       "--distpath", os.path.join(root, "dist"),
       "--workpath", variant_dir,
       "--specpath", variant_dir,
       "--exclude-module", "torch",
       "--exclude-module", "demucs",
       "--exclude-module", "numpy",
       "--exclude-module", "ctranslate2",
       "--exclude-module", "onnxruntime",
       "--exclude-module", "faster_whisper",
       "--exclude-module", "matplotlib",
       "--exclude-module", "PySide6",
       os.path.join(root, "installer_gui.py")]
print("开始打安装程序（onefile，几分钟）…")
rc = br.run(cmd)
print("pyinstaller rc =", rc)

# ⚠️⚠️ **onefile 在某些机器上根本起不来**（2026-10-15 用户实测"安装程序闪退"）。
#
# 症状：双击 → 黑框一闪就没。原始错误：
#     [PYI-xxxx:ERROR] Could not create temporary directory!
# onefile **必须**先把自身解压到 `_MEIxxxx` 才能跑；这一步失败连 Python 都起不来，
# 所以用户看不到任何提示。
#
# 实测（都用**计划任务**启动，脱离我的会话沙箱，避免自欺）：
#   * 同一环境里 cmd / Python 都能在 `%TEMP%`、`%LOCALAPPDATA%` 建目录写文件；
#   * **但任何 PyInstaller onefile**（连 7 MB 的 hello world、放 E: 或 C: 都一样）
#     都报 "Could not create temporary directory!"；
#   * **同代码改成 onedir → 一切正常**（安装程序能起来、能真装出主程序）。
# ⇒ 这台机器上 onefile 的解压环节不可用（bootloader 层面），与我们的代码无关。
#   onefile 在**正常机器**上是好的（别动默认），但必须给用户一条**能走通的路**：
#   所以额外产出一个 **onedir + ZIP** 版（解压后双击 `安装程序.exe`）。
#   用法：`python tools/build_installer_only.py --onedir`
_ONEDIR = "--onedir" in sys.argv
if _ONEDIR:
    print("")
    print("=" * 70)
    print("  额外产出：onedir 版安装程序（给 onefile 起不来的机器用）")
    print("=" * 70)
    od_dir = os.path.join(root, "build", "_installer_onedir")
    od_name = "安装程序"
    od_out = os.path.join(root, "build", "_installer_onedir_dist")
    cmd2 = [a for a in cmd]
    cmd2[cmd2.index("--onefile")] = "--onedir"
    cmd2[cmd2.index("--name") + 1] = od_name
    cmd2[cmd2.index("--distpath") + 1] = od_out
    cmd2[cmd2.index("--workpath") + 1] = od_dir + "_work"
    cmd2[cmd2.index("--specpath") + 1] = od_dir + "_spec"
    rc2 = br.run(cmd2)
    print("onedir pyinstaller rc =", rc2)
    src = os.path.join(od_out, od_name)
    if os.path.isdir(src):
        zip_path = os.path.join(root, "dist", f"{name}-解压即用.zip")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for base, _dirs, files in os.walk(src):
                for f in files:
                    p = os.path.join(base, f)
                    zf.write(p, os.path.relpath(p, src))
        print(f"→ {zip_path}  "
              f"（{os.path.getsize(zip_path) / 1024 ** 2:.1f} MB，解压后双击 "
              f"{od_name}.exe）")
    else:
        print(f"⚠️ onedir 产物没找到：{src}")

# ⚠️ PyInstaller 6 在 `--specpath` 指向别处时，**产物可能跑到 spec 旁边**
# 而不是 `--distpath`（实测：同名变体写到了当前工作目录、
# 另一个写到了临时目录）。所以这里把几个可能的位置都找一遍，
# 找不到就明确报错，而不是打印一句"没看到"就结束。
WANT = os.path.join(root, "dist", f"{name}.exe")
if not os.path.exists(WANT):
    cands = [os.path.join(root, f"{name}.exe"),
             os.path.join(root, "build", f"{name}.exe"),
             os.path.join(HERE, f"{name}.exe"),
             os.path.join(os.environ.get("TEMP", ""), f"{name}.exe")]
    for c in cands:
        if os.path.isfile(c):
            os.makedirs(os.path.dirname(WANT), exist_ok=True)
            shutil.move(c, WANT)
            print(f"（产物落在 {c}，已挪到 dist/）")
            break
if os.path.exists(WANT):
    print(f"→ {WANT}  （{os.path.getsize(WANT) / 1024 ** 3:.2f} GB）")
    sys.exit(0)
print(f"✗ 构建失败或找不到产物：{WANT}")
sys.exit(1)
