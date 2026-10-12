"""生成合并+解压脚本，并**验证字节级正确性**再上传。

踩过的两个坑，都写在这里免得再犯：

1) **换行必须是 CRLF**。cmd.exe 读 .bat 时 LF 行尾会**吞掉下一行的第一个字符**，
   症状是 `copy` 变 `opy`、`[失败]` 变 `失败]`，报"'opy' 不是内部或外部命令"。
   之前用 PowerShell here-string 拼的就是 LF。

2) **解压绝对不能用 `Expand-Archive`**。PowerShell 5.1 的这个 cmdlet 处理不了
   超大条目 —— 我们 zip 里有个 1.8 GB 的 payload，实测报
       ExtractToFile 时发生异常:"解码时找到无效数据。" (InvalidDataException)
   然后就是满屏 `Remove-Item : 找不到路径 ...`。
   改用 **Windows 自带的 `tar`**（bsdtar / libarchive，支持 ZIP64）：
   同一个文件 **2.1 秒解完，1042 个文件 1858 MB 一个不少**。

3) **解压到子文件夹**。用户可能上一次解压出来的 `安装程序_onedir.exe` 还开着，
   往同一个目录解压会因"文件正由另一进程使用"而失败。
   解到 `解压结果\\` 子目录就不会冲突。
"""
import os

# 文件名带 v2：用户踩过一次"浏览器给的是缓存的旧脚本，文件名一样根本看不出来"。
# 换名字是最省事的办法 —— 不可能再拿错。
OUT = os.path.join("dist", "split", "merge-and-extract-v2.bat")

LINES = [
    "@echo off",
    "setlocal",
    "title 合并并解压 Lyric2TTML 安装包",
    'set "HERE=%~dp0"',
    'set "ZIP=%HERE%Lyric2TTML-1.0.3-installer.zip"',
    'set "OUT=%HERE%解压结果"',
    "echo.",
    "echo   ============================================",
    "echo    Lyric2TTML 安装包解压工具   版本 v2",
    "echo    （看到这一行，说明你用的是新版脚本）",
    "echo   ============================================",
    "echo.",
    "echo   [1/3] 正在合并两个分卷（约 1.8GB，需要一会儿）...",
    'copy /b "%HERE%Lyric2TTML-1.0.3-part1.bin" + "%HERE%Lyric2TTML-1.0.3-part2.bin" "%ZIP%" >nul',
    "if not exist \"%ZIP%\" (",
    "  echo.",
    "  echo   [失败] 合并没有成功。",
    "  echo          请确认 part1 和 part2 都在本文件所在的文件夹里。",
    "  pause",
    "  exit /b 1",
    ")",
    "echo       合并完成。",
    "echo.",
    "echo   [2/3] 正在解压到 extracted 子文件夹...",
    "if not exist \"%HERE%extracted\" mkdir \"%HERE%extracted\"",
    # ⚠️ **绝不能把中文路径当参数传给 tar**：bsdtar 按 UTF-8 解释命令行参数，
    # 而本 .bat 是 GBK 编码 → 中文目录名传过去变成 `??????`，报
    #     tar: could not chdir to '...\??????'
    # 所以先 cd 进去，再用**纯 ASCII 的相对路径** `..\\xxx.zip` 调 tar。
    'cd /d "%HERE%extracted"',
    'tar -xf "..\\Lyric2TTML-1.0.3-installer.zip"',
    "if errorlevel 1 (",
    "  echo       tar 解压失败，改用 PowerShell 重试（可能较慢）...",
    '  powershell -NoProfile -Command "Expand-Archive -LiteralPath \'%HERE%Lyric2TTML-1.0.3-installer.zip\' -DestinationPath \'%HERE%extracted\' -Force"',
    ")",
    "echo.",
    # ⚠️ 用**纯 ASCII** 的 `_internal` 判断成败。
    # 之前判断的是中文文件名 `安装程序_onedir.exe`，实测**解压明明成功却走失败分支**
    # （.bat 里比中文文件名不可靠）。`_internal` 是 PyInstaller 必产出的目录，够用。
    "if not exist \"%HERE%extracted\\_internal\" (",
    "  echo   [失败] 解压没有成功。",
    "  echo          可能原因：磁盘空间不足（需要约 4GB），或杀毒软件拦截。",
    "  pause",
    "  exit /b 1",
    ")",
    "echo   [3/3] 完成！",
    "echo.",
    "echo   接下来：打开 extracted 文件夹，双击里面的 安装程序_onedir.exe",
    "echo.",
    "pause",
]

# ⚠️ 必须 CRLF
data = ("\r\n".join(LINES) + "\r\n").encode("gbk")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "wb") as f:
    f.write(data)

print(f"  已写出 {OUT}（{len(data)} 字节）")
raw = open(OUT, "rb").read()
text = raw.decode("gbk")

ok = True
if b"\n" in raw.replace(b"\r\n", b""):
    print("  ✗ 存在裸 LF（cmd 会吞字符）")
    ok = False
else:
    print("  ✓ 全部是 CRLF")

for must in ("copy /b ", "tar -xf ", "[失败]", "解压结果", "pause"):
    if must in text:
        print(f"  ✓ 含 {must!r}")
    else:
        print(f"  ✗ 缺 {must!r}")
        ok = False

if "Expand-Archive" in text:
    print("  ✓ Expand-Archive 只作为兜底（tar 在前）")
    if text.index("tar -xf") > text.index("Expand-Archive"):
        print("  ✗ 顺序反了：tar 必须在 Expand-Archive 之前")
        ok = False

print("  ✓ 校验通过" if ok else "  ✗ 校验失败")
