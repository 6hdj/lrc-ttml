"""用**真正的 AMLL 解析器**验一份 TTML 能不能在播放器里显示。

为什么需要它：我们自己的 `ttml_check.py` 只查规范，查不出"播放器里是空白"这类
语义问题。真实案例：`x-duet-a/b` 写成包住主唱 span 的包装层时，AMLL 解析器
（`@applemusic-like-lyrics/ttml`）对未知 role 走 `processWordElement`，而它
**不递归子节点**、只在包装层自己带 begin/end 时才产出一个词 —— 整行被当成一个词，
逐字时间轴全丢，播放器里**一片空白**。本工具直接跑真解析器，一跑就知道。

用法：
    python tools/verify_amll_parse.py outputs/在出发之前.ttml
    python tools/verify_amll_parse.py outputs/*.ttml          # 批量
    python tools/verify_amll_parse.py --setup                 # 只准备依赖

依赖（首次自动下载，放到 .tmp/amll-parser/，不走 npm）：
    @applemusic-like-lyrics/ttml  +  @xmldom/xmldom（npm registry / npmmirror）
需要系统里有 node。
"""
from __future__ import annotations

import glob
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS = os.path.join(ROOT, ".tmp", "amll-parser")
PKG_DIR = os.path.join(DEPS, "node_modules", "@applemusic-like-lyrics", "ttml")
DOM_DIR = os.path.join(DEPS, "node_modules", "@xmldom", "xmldom")
DRIVER = os.path.join(DEPS, "run.mjs")

REGISTRIES = ["https://registry.npmmirror.com", "https://registry.npmjs.org"]


def _meta(name: str) -> dict:
    last = None
    for base in REGISTRIES:
        try:
            req = urllib.request.Request(f"{base}/{name}", headers={"User-Agent": "probe"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:                       # noqa: PERF203
            last = e
    raise RuntimeError(f"拿不到 {name} 的元数据：{last}")


def _fetch(name: str, dest: str) -> None:
    if os.path.isdir(dest) and os.listdir(dest):
        return
    meta = _meta(name)
    ver = meta["dist-tags"]["latest"]
    url = meta["versions"][ver]["dist"]["tarball"]
    print(f"  下载 {name}@{ver}")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "probe"}),
                                timeout=120) as r:
        blob = r.read()
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(tf.extractfile(m).read())


DRIVER_SRC = r"""
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const xmldom = require("./node_modules/@xmldom/xmldom");
const { TTMLParser } = await import("./node_modules/@applemusic-like-lyrics/ttml/dist/amll-ttml.mjs");

const asJson = process.env.AMLL_JSON === "1";
const results = [];
let bad = 0;
for (const f of process.argv.slice(2)) {
    const text = fs.readFileSync(f, "utf8");
    let res = null;
    let error = null;
    try {
        res = new TTMLParser({ domParser: new xmldom.DOMParser() }).parse(text);
    } catch (e) {
        error = e.message;
    }
    if (error) {
        results.push({ file: f, ok: false, error });
        if (!asJson) console.log(`${f}\n  ✗ 解析异常: ${error}`);
        bad++;
        continue;
    }
    const lines = res?.lines ?? [];
    const words = lines.reduce((n, l) => n + (l.words?.length ?? 0), 0);
    const empty = lines.filter((l) => !l.words || l.words.length === 0);
    const bg = lines.filter((l) => l.backgroundVocal);
    const byAgent = {};
    for (const l of lines) byAgent[l.agentId ?? "(无)"] = (byAgent[l.agentId ?? "(无)"] || 0) + 1;
    const perLine = lines.length ? words / lines.length : 0;
    // 逐行判断有没有"塌陷"：CJK 歌词在逐字模式下每个字一个 word，
    // 若某行 words 数明显少于文本长度，说明这一行的逐字时间轴丢了
    // （典型原因：未知 role 的包装 span 让整行变成一个词）。
    const collapsed = lines.filter((l) => {
        const t = (l.text ?? "").length;
        const w = l.words?.length ?? 0;
        const cjk = ((l.text ?? "").match(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]/g) || []).length;
        return t > 0 && cjk >= t * 0.5 && w > 0 && w < t * 0.6;
    });
    const wordMode = (res?.metadata?.timingMode ?? "Word") === "Word";
    const suspicious = wordMode && lines.length > 0 && perLine < 2;
    const ok = lines.length > 0 && empty.length === 0 && !suspicious && collapsed.length === 0;
    results.push({
        file: f, ok, lines: lines.length, words, emptyLines: empty.length,
        perLine, byAgent, bgLines: bg.length, collapsedLines: collapsed.length,
        collapsedExample: collapsed.length
            ? { text: collapsed[0].text, words: collapsed[0].words?.length ?? 0 } : null,
        timing: res?.metadata?.timingMode ?? "?", suspicious,
    });
    if (!ok) bad++;
    if (asJson) continue;
    console.log(`${f}`);
    console.log(`  ${ok ? "✓" : "✗"} 行 ${lines.length} / 字 ${words}` +
        `（平均 ${perLine.toFixed(1)} 字每行）` +
        `  没有 words 的行 ${empty.length}`);
    console.log(`    agent 分布 ${JSON.stringify(byAgent)}；和声层 ${bg.length} 行` +
        `；timing=${res?.metadata?.timingMode ?? "?"}`);
    if (empty.length) {
        console.log(`    ⚠ 例：text=${JSON.stringify((empty[0].text ?? "").slice(0, 24))}` +
            ` —— 播放器里这行不会显示`);
    }
    if (suspicious) {
        console.log(`    ⚠ 逐字模式却平均只有 ${perLine.toFixed(1)} 字每行 —— ` +
            `逐字时间轴很可能已经丢失（检查是否有未知 role 的包装 span）`);
    }
    if (collapsed.length) {
        const c = collapsed[0];
        console.log(`    ⚠ ${collapsed.length} 行的字数明显少于文本长度 —— ` +
            `这一行的逐字时间轴丢了。例：text=${JSON.stringify((c.text ?? "").slice(0, 24))}` +
            ` 但只有 ${c.words?.length ?? 0} 个 word`);
    }
}
if (asJson) console.log(JSON.stringify(results));
process.exit(bad ? 1 : 0);
"""


def setup(verbose: bool = True) -> None:
    os.makedirs(DEPS, exist_ok=True)
    if verbose:
        print("准备 AMLL 解析器依赖（首次约 200KB）…")
    _fetch("@applemusic-like-lyrics/ttml", PKG_DIR)
    _fetch("@xmldom/xmldom", DOM_DIR)
    with open(DRIVER, "w", encoding="utf-8", newline="\n") as f:
        f.write(DRIVER_SRC)


def parse_check(text: str, tag: str = "_inline_check.ttml"):
    """给流水线用的：把一段 TTML 丢给真解析器，返回结果 dict（不可用则 None）。

    不可用的情况（缺 node / 抓不到 npm 包）返回 ``None``，**绝不抛异常** ——
    它只是额外保险，不能因为环境问题把转换搞挂。
    """
    import shutil
    if shutil.which("node") is None:
        return None
    try:
        setup(verbose=False)
    except Exception:
        return None
    path = os.path.join(DEPS, tag)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        env = dict(os.environ, AMLL_JSON="1")
        proc = subprocess.run(["node", DRIVER, os.path.abspath(path)], cwd=DEPS,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=120)
        line = (proc.stdout or "").strip().splitlines()
        if not line:
            return None
        data = json.loads(line[-1])
        return data[0] if data else None
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def verify(paths) -> int:
    setup(verbose=False)
    files = []
    for p in paths:
        files.extend(glob.glob(p) or [p])
    files = [os.path.abspath(f) for f in files if os.path.exists(f)]
    if not files:
        print("没有可验的文件")
        return 2
    cmd = ["node", DRIVER] + files
    proc = subprocess.run(cmd, cwd=DEPS, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    try:
        print(out.rstrip())
    except UnicodeEncodeError:
        print(out.encode("utf-8", "replace").decode("utf-8", "replace"))
    return proc.returncode


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--setup"]
    if "--setup" in sys.argv or not args:
        setup()
        if not args:
            sys.exit(0)
    sys.exit(verify(args))
