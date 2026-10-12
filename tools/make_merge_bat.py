"""生成合并+解压脚本，并**验证字节级正确性**再上传。

⚠️ 踩过的坑：cmd.exe 读 .bat 时，**LF 换行会让它吞掉下一行的第一个字符**。
症状是 `copy` 变成 `opy`、`[失败]` 变成 `失败]`，报
    'opy' 不是内部或外部命令
用户拿到就是这么坏的脚本。所以这里**显式用 \\r\\n**，并在写完后逐行断言：
每行都以 \\r\\n 结束，且关键命令的第一个字符没丢。
"""
import io
import os

OUT = os.path.join("dist", "split", "merge-then-extract.bat")
LINES = [
    "@echo off",
    "setlocal",
    "title 合并并解压 Lyric2TTML 安装包",
    "echo.",
    "echo   正在合并两个分卷...",
    'copy /b "%~dp0Lyric2TTML-1.0.3-part1.bin" + "%~dp0Lyric2TTML-1.0.3-part2.bin" "%~dp0Lyric2TTML-1.0.3-installer.zip" >nul',
    'if not exist "%~dp0Lyric2TTML-1.0.3-installer.zip" (',
    "  echo   [失败] 合并没有成功。",
    "  echo          请确认 part1 和 part2 都在本文件所在的文件夹里。",
    "  pause",
    "  exit /b 1",
    ")",
    "echo   合并完成。",
    "echo.",
    "echo   正在解压（约 1.8GB，需要一两分钟，请不要关窗口）...",
    'powershell -NoProfile -Command "Expand-Archive -LiteralPath \'%~dp0Lyric2TTML-1.0.3-installer.zip\' -DestinationPath \'%~dp0\' -Force"',
    "echo.",
    "echo   解压完成！",
    "echo.",
    "echo   接下来：进入 安装程序_onedir 文件夹，双击 安装程序_onedir.exe",
    "echo.",
    "pause",
]

# ⚠️ 必须 CRLF —— 用 \n 会让 cmd 吞掉下一行的首字符
data = ("\r\n".join(LINES) + "\r\n").encode("gbk")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "wb") as f:
    f.write(data)

# ---- 写完后验证 ----
print(f"  已写出 {OUT}（{len(data)} 字节）")
raw = open(OUT, "rb").read()
text = raw.decode("gbk")

ok = True
if b"\n" in raw.replace(b"\r\n", b""):
    print("  ✗ 存在裸 LF（cmd 会吞字符）")
    ok = False
else:
    print("  ✓ 全部是 CRLF，没有裸 LF")

for must in ("copy /b ", "[失败]", "Expand-Archive", "pause"):
    if must in text:
        print(f"  ✓ 含 {must!r}")
    else:
        print(f"  ✗ 缺 {must!r}")
        ok = False

for i, ln in enumerate(text.split("\r\n"), 1):
    if not ln:
        continue
    # 关键行首字符必须完好
    if ln.startswith(("copy", "[", "if", "echo", "pause", "exit", "setlocal",
                      "title", "powershell", ")")):
        continue
    print(f"  ⚠ 第 {i} 行开头可疑：{ln[:40]!r}")

print("  ✓ 校验通过" if ok else "  ✗ 校验失败")
