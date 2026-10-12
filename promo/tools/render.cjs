/**
 * 全片渲染：bundle → renderMedia(1860 帧, h264 + aac) → 可选 still（用于确定性对比）。
 *
 * 用法（在仓库根）：
 *   node promo/tools/render.cjs [--crf 19] [--preset slow] [--concurrency 8]
 *                               [--sample-rate 44100]
 *                               [--still 1110:run1.png --still 1110:run2.png]
 *
 * 为什么不让 `npx remotion render` 直接跑：本机受限沙箱里"子进程 stdio:pipe"一律 EPERM，
 * 而 Remotion CLI 内部要用管道 spawn esbuild service ⇒ CLI 起不来。这个脚本走
 * `@remotion/bundler` + `@remotion/renderer` 的进程内 API，在放宽权限的一次调用里跑完。
 * 在**不受限**的环境里两条路等价，`npx remotion render Lyric2TTML out/x.mp4` 也可以。
 *
 * ⚠️ TMP/TEMP 必须指到工作区内：自带 ffmpeg 往 %LOCALAPPDATA%\Temp 写 pre-encode.mp4
 *    会 Permission denied（实跑踩过，第 78 帧挂掉）。
 */
"use strict";
const path = require("path");
const fs = require("fs");

// 本脚本住在 promo/tools/ ⇒ 上一级是 promo/，再上一级是仓库根。
const PROMO = path.resolve(__dirname, "..");
const ROOT = path.resolve(PROMO, "..");
const OUT = path.join(PROMO, "out");
const VERIFY_DIR = path.resolve(ROOT, ".tmp", "verify", "determinism");
fs.mkdirSync(OUT, {recursive: true});

const argv = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = argv.indexOf("--" + name);
  return i >= 0 ? argv[i + 1] : dflt;
};
const stills = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--still") {
    const [frame, file] = argv[i + 1].split(":");
    stills.push({frame: Number(frame), file});
  }
}

const CRF = Number(opt("crf", 19));
const PRESET = opt("preset", "slow");
const CONCURRENCY = opt("concurrency", null);
// ⚠️ 必须显式给 44100：Remotion 默认 48000，会把 44.1kHz 母带升采样，
//    与基线（aac 44100Hz）不一致 —— 实跑 ffprobe 抓到的差异。
const SAMPLE_RATE = Number(opt("sample-rate", 44100));
const OUT_FILE = path.resolve(ROOT, opt("out", "out/Lyric2TTML-promo.mp4"));

const since = (t0) => ((Date.now() - t0) / 1000).toFixed(1) + "s";

(async () => {
  const bundler = require(path.join(ROOT, "node_modules/@remotion/bundler"));
  const renderer = require(path.join(ROOT, "node_modules/@remotion/renderer"));
  const log = (...a) => console.log("[final]", ...a);

  let t0 = Date.now();
  const serveUrl = await bundler.bundle({
    entryPoint: path.join(ROOT, "src/index.ts"),
    outDir: path.join(ROOT, "node_modules/.remotion-bundle"),
    publicDir: path.join(ROOT, "public"),
  });
  log("bundle", since(t0));

  const compositions = await renderer.getCompositions({serveUrl, inputProps: {}, logLevel: "warn"});
  const composition = compositions.find((c) => c.id === "Lyric2TTML");
  if (!composition) throw new Error("composition Lyric2TTML not found");
  log(
    "composition",
    `${composition.width}x${composition.height}`,
    `${composition.fps}fps`,
    `${composition.durationInFrames} frames = ${(composition.durationInFrames / composition.fps).toFixed(2)}s`,
  );

  for (const s of stills) {
    const dest = path.join(VERIFY_DIR, s.file);
    fs.mkdirSync(path.dirname(dest), {recursive: true});
    const ts = Date.now();
    await renderer.renderStill({serveUrl, composition, output: dest, frame: s.frame, imageFormat: "png", logLevel: "warn"});
    log(`still frame=${s.frame} -> ${dest} ${fs.statSync(dest).size}B in ${since(ts)}`);
  }

  t0 = Date.now();
  let lastRendered = -1;
  await renderer.renderMedia({
    serveUrl,
    composition,
    outputLocation: OUT_FILE,
    codec: "h264",
    crf: CRF,
    x264Preset: PRESET,
    pixelFormat: "yuv420p",
    audioCodec: "aac",
    audioBitrate: "192k",
    sampleRate: SAMPLE_RATE,
    concurrency: CONCURRENCY,
    logLevel: "info",
    onProgress: ({renderedFrames, encodedFrames, stitchStage}) => {
      const done = Math.max(renderedFrames, encodedFrames);
      if (done - lastRendered >= 200 || done === composition.durationInFrames) {
        lastRendered = done;
        const el = (Date.now() - t0) / 1000;
        log(`  ${done}/${composition.durationInFrames} 帧  ${el.toFixed(0)}s  ${stitchStage || ""}`);
      }
    },
  });
  const secs = (Date.now() - t0) / 1000;
  const st = fs.statSync(OUT_FILE);
  log(`DONE ${OUT_FILE}  ${(st.size / 1048576).toFixed(2)}MB  in ${secs.toFixed(1)}s (${(composition.durationInFrames / secs).toFixed(1)} fps)`);
})().catch((e) => {
  console.error("[final] FAILED:", e && e.stack ? e.stack : e);
  process.exit(1);
});
