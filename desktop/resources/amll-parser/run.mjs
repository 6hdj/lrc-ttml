
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
