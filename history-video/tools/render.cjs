/**
 * 全片渲染 + 十场抽帧自检。
 *
 *   node history-video/tools/render.cjs                 # 只出十张自检图（快，约 1 分钟）
 *   node history-video/tools/render.cjs --video         # 出十张图 + 整片 mp4
 *   node history-video/tools/render.cjs --video --frames 0-299   # 只渲前 10 秒
 *
 * 为什么不直接 `npx remotion render`：本机沙箱禁止"子进程 stdio:pipe"，
 * 而 Remotion CLI 内部要用管道 spawn esbuild service ⇒ CLI 会 `spawn EPERM`。
 * 这里走 `@remotion/bundler` + `@remotion/renderer` 的进程内 API，规避这件事。
 * 不受限的环境里两条路等价。
 *
 * ⚠️ 跑之前把 TMP/TEMP 指到工作区内：Remotion 自带 ffmpeg 往系统临时目录写
 *    pre-encode.mp4 会 Permission denied（实跑踩过，第 78 帧挂掉）。
 * ⚠️ 音频必须显式 44100：Remotion 默认 48000，会把 44.1kHz 的 bgm.wav 升采样。
 */
'use strict';
const path = require('path');
const fs = require('fs');

const PROJ = path.resolve(__dirname, '..');
const ROOT = path.resolve(PROJ, '..');
const OUT = path.join(PROJ, 'out');
fs.mkdirSync(OUT, {recursive: true});

const argv = process.argv.slice(2);
const has = (f) => argv.includes('--' + f);
const opt = (f, d) => {
  const i = argv.indexOf('--' + f);
  return i >= 0 ? argv[i + 1] : d;
};

/** 十场各一张自检图（帧号刻意避开每场最后 20 帧的退场叠化区）。 */
const CHECKS = [
  {scene: 1, name: '序', frame: 70},
  {scene: 2, name: '打轴', frame: 290},
  {scene: 3, name: '声音', frame: 460},
  {scene: 4, name: '对比', frame: 620},
  {scene: 5, name: '结构', frame: 790},
  {scene: 6, name: '对唱', frame: 960},
  {scene: 7, name: '和声', frame: 1100},
  {scene: 8, name: '编辑台', frame: 1330},
  {scene: 9, name: '实测', frame: 1500},
  {scene: 10, name: '尾声', frame: 1690},
];

const since = (t0) => ((Date.now() - t0) / 1000).toFixed(1) + 's';

(async () => {
  const bundler = require(path.join(PROJ, 'node_modules/@remotion/bundler'));
  const renderer = require(path.join(PROJ, 'node_modules/@remotion/renderer'));
  const log = (...a) => console.log('[hist]', ...a);

  let t0 = Date.now();
  const serveUrl = await bundler.bundle({
    entryPoint: path.join(PROJ, 'src/index.ts'),
    outDir: path.join(PROJ, 'node_modules/.remotion-bundle'),
    publicDir: path.join(PROJ, 'public'),
  });
  log('bundle', since(t0));

  const compositions = await renderer.getCompositions({serveUrl, inputProps: {}, logLevel: 'warn'});
  const composition = compositions.find((c) => c.id === 'Lyric2TTMLIntro');
  if (!composition) throw new Error('composition Lyric2TTMLIntro not found');
  log(
    'composition',
    `${composition.width}x${composition.height}`,
    `${composition.fps}fps`,
    `${composition.durationInFrames} frames = ${(composition.durationInFrames / composition.fps).toFixed(2)}s`,
  );

  // 十场自检图
  for (const c of CHECKS) {
    const file = path.join(OUT, `s${String(c.scene).padStart(2, '0')}_f${c.frame}.png`);
    const ts = Date.now();
    await renderer.renderStill({
      serveUrl,
      composition,
      output: file,
      frame: c.frame,
      imageFormat: 'png',
      logLevel: 'warn',
    });
    log(`still 场${c.scene} ${c.name} frame=${c.frame} -> ${path.basename(file)} ${fs.statSync(file).size}B ${since(ts)}`);
  }

  if (!has('video')) {
    log('（未加 --video，跳过整片渲染）');
    log('ALL OK');
    return;
  }

  const outFile = path.join(OUT, 'Lyric2TTML-Intro.mp4');
  const frameRange = opt('frames', null);
  const range = frameRange ? frameRange.split('-').map(Number) : null;
  const total = range ? range[1] - range[0] + 1 : composition.durationInFrames;

  t0 = Date.now();
  let last = -1;
  await renderer.renderMedia({
    serveUrl,
    composition: range ? {...composition, durationInFrames: range[1] + 1} : composition,
    outputLocation: outFile,
    codec: 'h264',
    crf: Number(opt('crf', 19)),
    x264Preset: opt('preset', 'slow'),
    pixelFormat: 'yuv420p',
    audioCodec: 'aac',
    audioBitrate: '192k',
    sampleRate: 44100,
    ...(range ? {frameRange: [range[0], range[1]]} : {}),
    logLevel: 'info',
    onProgress: ({renderedFrames, encodedFrames, stitchStage}) => {
      const done = Math.max(renderedFrames, encodedFrames);
      if (done - last >= 300 || done === total) {
        last = done;
        log(`  ${done}/${total} 帧 ${since(t0)} ${stitchStage || ''}`);
      }
    },
  });
  const secs = (Date.now() - t0) / 1000;
  log(
    `DONE ${outFile} ${(fs.statSync(outFile).size / 1048576).toFixed(2)}MB ${secs.toFixed(1)}s (${(total / secs).toFixed(1)} fps)`,
  );
})().catch((e) => {
  console.error('[hist] FAILED:', e && e.stack ? e.stack : e);
  process.exit(1);
});
