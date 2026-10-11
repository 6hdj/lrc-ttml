/* 逐帧渲染 slides.html → PNG 序列（配 tools/demo_video/build.py 用）。
 *
 *   node tools/demo_video/record.js <debugPort> <outDir> <fps> <htmlUrl>
 *
 * ⚠️ 为什么是"逐帧 seek 再截图"而不是录屏：
 *   slides.html 导出了 seek(t)，任何一帧只取决于 t。所以我可以按帧号顺序
 *   一帧一帧地设状态、截图 —— **渲染结果与截图速度完全无关**，
 *   掉帧/卡顿都不会让动画跑偏，而且可以重跑、可以换帧率。
 *   用 CDP 的 Page.startScreencast 反而要处理可变帧间隔，得不偿失。
 *
 * 截图用 JPEG（quality 92）：PNG 一帧几 MB，1800 帧就是几个 GB。
 */
"use strict";
const fs = require("fs");
const path = require("path");
const http = require("http");

const [, , portArg, outDir, fpsArg, url] = process.argv;
const PORT = parseInt(portArg, 10);
const OUT = outDir;
const FPS = parseFloat(fpsArg || "30");

// 必须与 slides.html 的 TOTAL 一致。可用 DEMO_TOTAL 覆盖（只录前几秒来试水）
const TOTAL = parseFloat(process.env.DEMO_TOTAL || "62.0");
const W = 1920, H = 1080;
const N = Math.round(TOTAL * FPS);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function httpJson(method, u) {
  return new Promise((res, rej) => {
    const r = http.request(u, { method }, (x) => {
      let b = ""; x.on("data", (c) => (b += c));
      x.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(new Error(b.slice(0, 200))); } });
    });
    r.on("error", rej); r.end();
  });
}

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    ws.addEventListener("message", (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error("CDP 超时: " + method)); }
      }, 60000);
    });
  }
  static async connect(u) {
    const ws = new WebSocket(u);
    await new Promise((res, rej) => {
      ws.addEventListener("open", res, { once: true });
      ws.addEventListener("error", () => rej(new Error("WS 连接失败")), { once: true });
    });
    return new Cdp(ws);
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  // 清掉上一轮
  for (const f of fs.readdirSync(OUT)) {
    if (f.endsWith(".jpg")) fs.unlinkSync(path.join(OUT, f));
  }

  const target = await httpJson("PUT", `http://127.0.0.1:${PORT}/json/new?about:blank`);
  const cdp = await Cdp.connect(target.webSocketDebuggerUrl);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride",
    { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await cdp.send("Page.navigate", { url });

  // 等页面与字体就绪
  for (let i = 0; i < 60; i++) {
    await sleep(200);
    const r = await cdp.send("Runtime.evaluate",
      { expression: "typeof seek === 'function' && document.readyState === 'complete'",
        returnByValue: true });
    if (r.result && r.result.value === true) break;
  }
  await sleep(600);          // 给字体加载留点时间

  const t0 = Date.now();
  for (let i = 0; i < N; i++) {
    const t = i / FPS;
    // 设状态 → 截图。两次 evaluate 之间不 sleep，让浏览器自己排帧。
    await cdp.send("Runtime.evaluate",
      { expression: `seek(${t.toFixed(4)})`, returnByValue: false });
    const shot = await cdp.send("Page.captureScreenshot",
      { format: "jpeg", quality: 92, captureBeyondViewport: false });
    fs.writeFileSync(path.join(OUT, `f${String(i).padStart(5, "0")}.jpg`),
      Buffer.from(shot.data, "base64"));
    if (i % 150 === 0 || i === N - 1) {
      const el = (Date.now() - t0) / 1000;
      const eta = el / (i + 1) * (N - i - 1);
      console.log(`  ${i + 1}/${N} 帧  ${el.toFixed(0)}s 已用  ` +
                  `预计还要 ${eta.toFixed(0)}s  (t=${t.toFixed(1)}s)`);
    }
  }
  const mins = (Date.now() - t0) / 1000;
  console.log(`  完成：${N} 帧，用时 ${mins.toFixed(0)}s（${(N / mins).toFixed(1)} fps 渲染速度）`);

  await httpJson("GET", `http://127.0.0.1:${PORT}/json/close/${target.id}`).catch(() => {});
  ws_close(cdp);
  process.exit(0);
})();

function ws_close(cdp) { try { cdp.ws.close(); } catch (e) { /* ignore */ } }
