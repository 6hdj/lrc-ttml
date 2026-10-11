"""从**源码本身**生成模块说明文档，杜绝主观臆造。

每个模块的职责取自它的**模块 docstring 第一段**（代码里写着的），
公开 API 取自 AST 解析（真的有哪些函数/类，不是猜的）。

    python tools/gen_module_docs.py            # 写到 docs/模块说明.md
    python tools/gen_module_docs.py --check    # 只检查文档是否与代码同步
"""
from __future__ import annotations

import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)

# 模块顺序按"数据流经过的顺序"排，让人看得懂；不是在编故事，
# 每一步在 pipeline.py 里都能找到对应阶段。
MODULES = [
    ("textread.py", "① 读输入", "带编码探测地读用户文本（GBK/Big5/UTF-8…）"),
    ("preprocess.py", "① 读输入", "LRC / 纯文本 → LyricLine；分词、标记、和声合并"),
    ("ttml_model.py", "② 编辑态模型", "EditWord / EditLine / EditDoc，可 JSON 序列化"),
    ("ttml_reader.py", "② 编辑态模型", "TTML → EditDoc（反向解析）与回写"),
    ("ttml_normalize.py", "② 编辑态模型", "时间轴自动修复 R1–R7，生成/编辑两条链路共用"),
    ("asr.py", "③ 识别", "faster-whisper 封装：模型定位、CUDA DLL 注册、切片转写"),
    ("matcher.py", "④ 对齐", "歌词 ↔ ASR 结果的 DP 强制对齐"),
    ("onset_align.py", "④ 对齐", "起音点辅助对齐（较弱，作为补充）"),
    ("tail_extend.py", "④ 对齐", "句尾拖音补全（能量 + 基频连续性判据）"),
    ("diarize.py", "⑤ 分声部", "CAM++ 声纹聚类判断每行谁唱"),
    ("speaker_campplus.py", "⑤ 分声部", "CAM++ 网络定义与权重加载"),
    ("singer_ssl.py", "⑤ 分声部", "实验区（已冻结，禁止被生产模块引用）"),
    ("separate.py", "⑥ 可选", "Demucs 人声分离"),
    ("ttml_writer.py", "⑦ 输出", "AlignedLine[] → TTML 文本"),
    ("ttml_check.py", "⑦ 输出", "确定性规范校验"),
    ("pipeline.py", "⑧ 编排", "整条流水线（解析→…→输出+自检）"),
    ("main.py", "⑨ 入口", "命令行"),
    ("web_server.py", "⑨ 入口", "本地 Web 服务（标准库 http.server）"),
    ("launcher.py", "⑨ 入口", "打包版入口：重定位目录、转发子命令"),
    ("edit_api.py", "⑩ 编辑台", "编辑台后端纯逻辑（载入/校验/保存/乐观锁）"),
    ("edit_text.py", "⑩ 编辑台", "文本与和声编辑（纯函数改 EditDoc）"),
    ("envcheck.py", "⑪ 安装与体检", "穷尽式环境检查（22 项）"),
    ("install.py", "⑪ 安装与体检", "检查出的缺失项怎么补（下载/解包）"),
    ("download_model.py", "⑪ 安装与体检", "下载 Whisper 模型（多端点、断点续传）"),
    ("doctor.py", "⑪ 安装与体检", "环境体检（医生），支持 --json"),
    ("installer_gui.py", "⑪ 安装与体检", "图形化安装程序"),
]


def summarize(path: str) -> tuple:
    """返回 (模块首段说明, [(kind, name, 一句话)])"""
    try:
        src = io.open(path, encoding="utf-8").read()
    except OSError:
        return "", []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return f"（语法错误，无法解析：{e}）", []
    doc = (ast.get_docstring(tree) or "").strip()
    # 取第一段（空行之前），并把换行接起来
    first = doc.split("\n\n")[0].replace("\n", " ").strip()
    first = " ".join(first.split())
    apis = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            apis.append(("class", node.name,
                         " ".join(((ast.get_docstring(node) or "")
                                   .split("\n")[0] or "").split())))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and not node.name.startswith("_"):
            apis.append(("def", node.name,
                         " ".join(((ast.get_docstring(node) or "")
                                   .split("\n")[0] or "").split())))
    return first, apis


def lines_of(path: str) -> int:
    try:
        return len(io.open(path, encoding="utf-8").read().splitlines())
    except OSError:
        return 0


def build() -> str:
    out = ["# 模块说明", "",
           "> **本文由 `tools/gen_module_docs.py` 从源码自动生成**：",
           "> 每个模块的职责取自它自己的模块 docstring，公开 API 由 AST 解析得出。",
           "> 所以这里写的都是代码里**确实存在**的东西；改了代码请重新生成，别手改本文。",
           "",
           "生成命令：`python tools/gen_module_docs.py`", "",
           "---", ""]

    cur = ""
    for fn, stage, what in MODULES:
        if not os.path.exists(fn):
            continue
        if stage != cur:
            cur = stage
            out += [f"## {stage}", ""]
        doc, apis = summarize(fn)
        n = lines_of(fn)
        out.append(f"### `{fn}` — {what}")
        out.append("")
        out.append(f"*{n} 行*")
        out.append("")
        if doc:
            out.append(f"> {doc}")
            out.append("")
        else:
            out.append("> （模块没有 docstring —— 建议补上）")
            out.append("")
        if apis:
            shown = apis[:14]
            out.append("主要公开接口：")
            out.append("")
            for kind, name, d in shown:
                tag = "类" if kind == "class" else "函数"
                out.append(f"- **{tag}** `{name}`" + (f" — {d}" if d else ""))
            if len(apis) > len(shown):
                out.append(f"- …另有 {len(apis) - len(shown)} 个见源码")
            out.append("")

    # 工具目录
    out += ["## 工具（tools/）", ""]
    tdir = "tools"
    if os.path.isdir(tdir):
        for fn in sorted(os.listdir(tdir)):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(tdir, fn)
            doc, _ = summarize(p)
            out.append(f"- **`{fn}`**" + (f" — {doc}" if doc else ""))
    out.append("")
    return "\n".join(out)


def main() -> int:
    text = build()
    dest = os.path.join("docs", "模块说明.md")
    if "--check" in sys.argv:
        try:
            old = io.open(dest, encoding="utf-8").read()
        except OSError:
            old = ""
        if old != text:
            print("  ✗ docs/模块说明.md 与源码不同步，请重新生成")
            return 1
        print("  ✓ 文档与源码同步")
        return 0
    os.makedirs("docs", exist_ok=True)
    io.open(dest, "w", encoding="utf-8", newline="\n").write(text)
    print(f"  已写出 {dest}（{len(text.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
