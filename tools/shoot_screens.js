/* CDP 截图客户端（配 tools/shoot_screens.ps1 用）。
 *
 * 为什么要有这个文件：Edge 的 `--screenshot` 是"**load 事件一过就截**"，
 * 而编辑台的数据是页面加载完之后异步 fetch 的 —— 截出来永远停在首屏。
 * 能等的 `--virtual-time-budget` 又会让 Edge 卡住不退出（实测）。
 *
 * 所以改成用 **Chrome DevTools Protocol**：
 *   打开页面 → 轮询"编辑器真的渲染出行了没" → 再 captureScreenshot。
 * Node 24 自带 WebSocket，不需要装任何包。
 *
 *   node tools/shoot_screens.js <debugPort> <outDir> <url>
 */
"use strict";
const fs = require("fs");
const path = require("path");
const http = require("http");

const [, , debugPortArg, outDir, baseUrl] = process.argv;
const DEBUG_PORT = parseInt(debugPortArg, 10);
const OUT = outDir;
const BASE = baseUrl;

// [文件名, 网址路径, 视口宽, 视口高, 等待条件(JS 表达式，返回真表示渲染好了)]
// [文件名, 网址路径, 视口宽, 视口高, 等待条件(渲染好的判据), 裁剪选择器(null=整个视口)]
const SHOTS = [
  ["01-main.png", "/", 1280, 1000,
    `document.readyState === 'complete' && document.querySelectorAll('#idSearchRow > *').length > 0`,
    null],
  // ⚠️ 编辑台在首屏**下面**：只截视口的话永远是页面顶部（实测 5 张图字节完全相同）。
  // 所以按 `#editorPanel` 元素裁剪，顺便还更清楚 —— README 里要的就是编辑台本身。
  ["02-editor-time.png", "/?task=_shot_demo&mode=time", 1280, 1000,
    `document.querySelectorAll('#editorBody .ed-line').length > 0`,
    "#editorPanel"],
  ["03-editor-agent.png", "/?task=_shot_demo&mode=agent", 1280, 1000,
    `document.querySelectorAll('#editorBody .agent-line').length > 0`,
    "#editorPanel"],
  ["04-editor-text.png", "/?task=_shot_demo&mode=text", 1280, 1000,
    `document.querySelectorAll('#editorBody .txt-line').length > 0`,
    "#editorPanel"],
  ["05-narrow.png", "/", 760, 1000,
    `document.readyState === 'complete' && document.querySelectorAll('#idSearchRow > *').length > 0`,
    null],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* 给演示任务配一段静音音频 + sidecar。
   不这么做的话，编辑台会显示红字「⚠ 音频加载失败（可能这个任务没有音频）」——
   截图放进 README 会让人以为工具坏了。
   ⚠️ 路径必须落在 uploads/ 里：web_server 的 safe_audio_path() 只认这个目录。 */
function seedDemoAudio() {
  const sr = 16000, secs = 30, n = sr * secs;
  const data = Buffer.alloc(44 + n * 2);          // 16-bit 单声道，全 0 = 静音
  data.write("RIFF", 0); data.writeUInt32LE(36 + n * 2, 4); data.write("WAVE", 8);
  data.write("fmt ", 12); data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22); data.writeUInt32LE(sr, 24);
  data.writeUInt32LE(sr * 2, 28); data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34); data.write("data", 36); data.writeUInt32LE(n * 2, 40);
  fs.mkdirSync("uploads", { recursive: true });
  fs.mkdirSync("outputs", { recursive: true });
  const wav = path.resolve("uploads/_shot_demo.wav");
  fs.writeFileSync(wav, data);
  fs.writeFileSync(path.resolve("outputs/_shot_demo.src.json"),
    JSON.stringify({ audio: wav, lyrics: "" }), "utf8");
}
function cleanDemoAudio() {
  for (const f of ["uploads/_shot_demo.wav", "outputs/_shot_demo.src.json"]) {
    try { fs.unlinkSync(path.resolve(f)); } catch (e) { /* ignore */ }
  }
}

function httpJson(method, url) {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method }, (res) => {
      let b = "";
      res.on("data", (c) => (b += c));
      res.on("end", () => {
        try { resolve(JSON.parse(b)); } catch (e) { reject(new Error(b.slice(0, 200))); }
      });
    });
    req.on("error", reject);
    req.end();
  });
}

/** 极简 CDP 客户端：一个连接，按 id 匹配回包 */
class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP 超时: ${method}`));
        }
      }, 30000);
    });
  }
  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      ws.addEventListener("open", res, { once: true });
      ws.addEventListener("error", () => rej(new Error("WS 连接失败")), { once: true });
    });
    return new Cdp(ws);
  }
}

async function shoot(shot) {
  const [name, urlPath, w, h, cond, clipSel] = shot;
  const file = path.join(OUT, name);

  // 每个页面用一个新标签页，避免复用上一个页面的状态
  const target = await httpJson("PUT",
    `http://127.0.0.1:${DEBUG_PORT}/json/new?about:blank`);
  const cdp = await Cdp.connect(target.webSocketDebuggerUrl);
  try {
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride",
      { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: BASE + urlPath });

    // 轮询等待"真的渲染好了"（最多 20 秒）
    let ready = false;
    for (let i = 0; i < 100; i++) {
      await sleep(200);
      try {
        const r = await cdp.send("Runtime.evaluate",
          { expression: cond, returnByValue: true });
        if (r && r.result && r.result.value === true) { ready = true; break; }
      } catch (e) { /* 页面还在导航，继续等 */ }
    }
    await sleep(500);                    // 再给一帧让样式落定

    const opts = { format: "png", captureBeyondViewport: true };
    if (clipSel) {
      const r = await cdp.send("Runtime.evaluate", {
        expression: `(() => { const e = document.querySelector(${JSON.stringify(clipSel)});
          if (!e) return null; const b = e.getBoundingClientRect();
          return { x: b.x + window.scrollX, y: b.y + window.scrollY,
                   width: b.width, height: b.height }; })()`,
        returnByValue: true,
      });
      const clip = r && r.result && r.result.value;
      if (clip && clip.width > 50 && clip.height > 50) {
        opts.clip = { x: clip.x, y: clip.y, width: clip.width,
                      height: clip.height, scale: 1 };
      } else {
        console.log(`  !! ${name}: 裁剪选择器 ${clipSel} 取不到尺寸，改截整个视口`);
      }
    }
    const shotRes = await cdp.send("Page.captureScreenshot", opts);
    fs.writeFileSync(file, Buffer.from(shotRes.data, "base64"));
    const kb = (fs.statSync(file).size / 1024).toFixed(0);
    console.log(`  ${ready ? "ok" : "!!"} ${name.padEnd(22)} ${kb.padStart(7)} KB` +
      (ready ? "" : "   ⚠ 等待条件没满足，可能截到的是未渲染状态"));
    return ready;
  } finally {
    try { await httpJson("GET", `http://127.0.0.1:${DEBUG_PORT}/json/close/${target.id}`); }
    catch (e) { /* ignore */ }
    try { cdp.ws.close(); } catch (e) { /* ignore */ }
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  seedDemoAudio();
  let ok = true;
  for (const s of SHOTS) {
    try {
      ok = (await shoot(s)) && ok;
    } catch (e) {
      console.log(`  xx ${s[0].padEnd(22)} ${e.message}`);
      ok = false;
    }
  }
  cleanDemoAudio();
  process.exit(ok ? 0 : 1);
})();
