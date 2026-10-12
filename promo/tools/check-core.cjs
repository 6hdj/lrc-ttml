/* ============================================================================
 * core 自检（**不需要渲染、不需要 esbuild**）
 *
 * 为什么需要它：本机受限沙箱里"子进程 stdio:pipe"一律 EPERM，Remotion 打包必经
 * esbuild service（双工管道）⇒ `remotion still/render` 起不来。于是这里用两条
 * 完全在进程内的手段做真验证：
 *   1) 用 typescript 的 transpileModule（纯 JS，不起子进程）就地编译 TSX；
 *   2) 用 react-dom/server 真渲染组件，把 style 解析回来，与**独立转写的基线公式**逐项比。
 *
 * 它验证的是"core 的数值/结构 == slides.html"，**不替代** Remotion 的真实渲染。
 * 用法： node promo/tools/check-core.cjs
 * ========================================================================== */
'use strict';
const fs = require('fs');
const path = require('path');
const Module = require('module');

// 本脚本住在 promo/tools/：往上一级是 promo/，再往上一级才是仓库根。
const ROOT = path.resolve(__dirname, '..', '..');
const PROMO = path.resolve(__dirname, '..');
const SLIDES = path.join(ROOT, 'tools', 'demo_video', 'slides.html');
const projRequire = Module.createRequire(path.join(PROMO, 'package.json'));

const ts = projRequire('typescript');
const React = projRequire('react');
const {renderToStaticMarkup} = projRequire('react-dom/server');

/* ---------------------------------------------------------------- 结果统计 */
let pass = 0;
const failures = [];
const infos = [];
function ok(name, cond, detail = '') {
  if (cond) {
    pass++;
    console.log(`  PASS  ${name}`);
  } else {
    failures.push(`${name}${detail ? ' :: ' + detail : ''}`);
    console.log(`  FAIL  ${name}${detail ? ' :: ' + detail : ''}`);
  }
}
function info(msg) {
  infos.push(msg);
  console.log(`  INFO  ${msg}`);
}
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;

/* --------------------------------------------------- TSX 就地编译（无 esbuild） */
function compileTs(module, filename) {
  const source = fs.readFileSync(filename, 'utf8');
  const out = ts.transpileModule(source, {
    fileName: filename,
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
      allowSyntheticDefaultImports: true,
    },
  }).outputText;
  module._compile(out, filename);
}
require.extensions['.ts'] = compileTs;
require.extensions['.tsx'] = compileTs;

/* ------------------------------------------- stub 掉 remotion（SSR 里没有帧上下文） */
global.__PROMO_FRAME__ = 0;
global.__PROMO_FPS__ = 30;
global.__PROMO_SECONDS__ = 62;

const remotionStub = {
  // 与 remotion 真实 AbsoluteFill 的默认 style 对齐（position:absolute + flex column）
  AbsoluteFill: ({children, style, ...rest}) =>
    React.createElement(
      'div',
      {
        style: {position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, display: 'flex', flexDirection: 'column', ...style},
        ...rest,
      },
      children,
    ),
  Audio: () => null,
  staticFile: (p) => p,
  useCurrentFrame: () => global.__PROMO_FRAME__,
  useVideoConfig: () => ({
    fps: global.__PROMO_FPS__,
    width: 1920,
    height: 1080,
    durationInFrames: Math.round(global.__PROMO_SECONDS__ * global.__PROMO_FPS__),
  }),
};

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'remotion') return remotionStub;
  // Composition.tsx 里 `import ... from './scenes'`：由 scene-builder 维护。
  // 这里 stub 成"只画 SceneWindow 的空场景"，好让 core 自检与他并行、互不阻塞。
  if (request === './scenes' && parent && /Composition\.tsx$/.test(parent.filename || '')) {
    const {SceneWindow} = projRequire(path.join(PROMO, 'src', 'core', 'SceneWindow.tsx'));
    const {SCENE_BY_ID} = projRequire(path.join(PROMO, 'src', 'core', 'timeline.ts'));
    const make = (id) => ({t}) =>
      React.createElement(
        SceneWindow,
        {t, a: SCENE_BY_ID[id].a, b: SCENE_BY_ID[id].b},
        React.createElement('div', {'data-scene': id}, `stub ${id}`),
      );
    return {S0Opening: make('s0'), S1Problem: make('s1'), S2Input: make('s2'), S3Features: make('s3'), S4Stats: make('s4'), S5Ending: make('s5')};
  }
  return origLoad.apply(this, arguments);
};

/* ------------------------------------------------------------- 加载被测模块 */
const core = (f) => projRequire(path.join(PROMO, 'src', 'core', f));
const theme = core('theme.ts');
const timeline = core('timeline.ts');
const {Backdrop} = core('Backdrop.tsx');
const {RevealText, revealThreshold, REVEAL_PER, REVEAL_STEP} = core('RevealText.tsx');
const {ProgressBar} = core('ProgressBar.tsx');
const {SceneWindow} = core('SceneWindow.tsx');
const {Lyric2TTML, PROMO_DEFAULT_PROPS} = projRequire(path.join(PROMO, 'src', 'Composition.tsx'));

const html = fs.readFileSync(SLIDES, 'utf8');
const readSrc = (rel) => fs.readFileSync(path.join(PROMO, rel), 'utf8');
/** 静态扫描前先去掉注释 —— 否则"注释里说自己没有 setTimeout"会被判成违规（真踩过） */
const stripComments = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1');

/* ============================ 1. 颜色/常量 vs slides.html ============================ */
console.log('\n[1] 主题常量必须与 slides.html 逐条一致');
const literalPairs = [
  ['COLORS.bg', theme.COLORS.bg, '#0a0a12'],
  ['COLORS.text', theme.COLORS.text, '#e8e8f0'],
  ['COLORS.cyan', theme.COLORS.cyan, '#22d3ee'],
  ['COLORS.green', theme.COLORS.green, '#6cd97e'],
  ['COLORS.gray', theme.COLORS.gray, '#9aa4bf'],
  ['COLORS.dim', theme.COLORS.dim, '#6b7590'],
  ['COLORS.glowCyan', theme.COLORS.glowCyan, 'rgba(34,211,238,.16)'],
  ['COLORS.glowViolet', theme.COLORS.glowViolet, 'rgba(124,92,255,.15)'],
  ['COLORS.barGradient', theme.COLORS.barGradient, 'linear-gradient(90deg,#22d3ee,#6cd97e)'],
];
for (const [name, actual, expected] of literalPairs) {
  ok(`${name} == ${expected}`, actual === expected, `实际 ${JSON.stringify(actual)}`);
  ok(`slides.html 里确实有 ${expected}`, html.includes(expected), '基线源码里找不到该字面量');
}
ok('网格 opacity .05', theme.GRID.opacity === 0.05 && html.includes('opacity: .05'), `实际 ${theme.GRID.opacity}`);
ok('网格 80px 80px', theme.GRID.size === 80 && html.includes('80px 80px'), `实际 ${theme.GRID.size}`);
ok('光晕 blur 120px', theme.GLOW.blurPx === 120 && html.includes('blur(120px)'), `实际 ${theme.GLOW.blurPx}`);
ok('光晕尺寸 1100 / 900', theme.GLOW.cyan.size === 1100 && theme.GLOW.violet.size === 900);
ok('进度条高 6px', theme.VIDEO.progressBarHeight === 6 && html.includes('height: 6px'), `实际 ${theme.VIDEO.progressBarHeight}`);
ok('画布 1920x1080@30fps', theme.VIDEO.width === 1920 && theme.VIDEO.height === 1080 && theme.VIDEO.fps === 30);
ok('字体只列系统字体（无 @import/Google Fonts）', !/fonts\.googleapis|@import\s+url/.test(theme.FONT.family) && /Microsoft YaHei/.test(theme.FONT.family), theme.FONT.family);
ok('字号 h1 116 / h2 74 / sub 40 / tag 34', theme.SIZE.h1 === 116 && theme.SIZE.h2 === 74 && theme.SIZE.sub === 40 && theme.SIZE.tag === 34);
ok('逐字上移 26px', theme.SPACING.charLift === 26 && html.includes('translateY(26px)'));

/* ============================ 2. 时间轴 vs 基线 SCENES ============================ */
console.log('\n[2] 场景区间与 TOTAL 必须与基线一致');
const baselineScenes = [...html.matchAll(/\{\s*id:\s*'(s\d)'\s*,\s*a:\s*([\d.]+)\s*,\s*b:\s*([\d.]+)\s*\}/g)].map((m) => ({
  id: m[1],
  a: parseFloat(m[2]),
  b: parseFloat(m[3]),
}));
const baselineTotal = parseFloat(/const TOTAL = ([\d.]+);/.exec(html)[1]);
ok('slide.html 解析出 6 段场景', baselineScenes.length === 6, `实际 ${baselineScenes.length}`);
ok('VIDEO_SECONDS == 基线 TOTAL', timeline.VIDEO_SECONDS === baselineTotal, `${timeline.VIDEO_SECONDS} vs ${baselineTotal}`);
ok('SCENES 段数与基线一致', timeline.SCENES.length === baselineScenes.length);
timeline.SCENES.forEach((s, i) => {
  const b = baselineScenes[i];
  ok(`场景 ${s.id} 区间 ${s.a}-${s.b}`, b && s.id === b.id && near(s.a, b.a) && near(s.b, b.b), `基线 ${b && `${b.a}-${b.b}`}`);
});
ok('区间连续无缝（无空档/无重叠）', timeline.SCENES.every((s, i) => i === 0 || s.a === timeline.SCENES[i - 1].b));
ok('末段结束 == TOTAL', timeline.SCENES[timeline.SCENES.length - 1].b === baselineTotal);
// 基线的 clamp / easeOut / enter 独立转写
const refClamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const refEaseOut = (x) => 1 - Math.pow(1 - x, 3);
const refEnter = (t, a, dur = 0.7) => refClamp((t - a) / dur, 0, 1);
let easeOk = true;
for (let i = 0; i <= 40; i++) {
  const x = i / 40;
  if (!near(timeline.clamp(x * 2 - 0.5, 0, 1), refClamp(x * 2 - 0.5, 0, 1)) || !near(timeline.easeOut(x), refEaseOut(x))) easeOk = false;
  const t = x * 62;
  if (!near(timeline.enter(t, 7.0), refEnter(t, 7.0))) easeOk = false;
}
ok('clamp / easeOut / enter 与基线公式逐点相同（41 个采样）', easeOk);

/* ============================ 3. Backdrop 光晕逐帧位置 ============================ */
console.log('\n[3] Backdrop：光晕漂移逐帧位置（SSR 真实输出 vs 基线公式）');
const refGlow = (t) => ({
  left: -160 + 120 * Math.sin(t / 11),
  top: -260 + 90 * Math.cos(t / 13),
  right: -200 + 110 * Math.sin(t / 9 + 1.2),
  bottom: -220 + 100 * Math.cos(t / 15),
});
const glowSamples = [0, 0.5, 3, 7.2, 12.34, 25, 33.3333, 41.5, 51.999, 61.9];
let glowOk = true;
let glowDetail = '';
for (const t of glowSamples) {
  const markup = renderToStaticMarkup(React.createElement(Backdrop, {t}));
  const cyan = /left:(-?[\d.]+)px;top:(-?[\d.]+)px;width:1100px/.exec(markup);
  const violet = /right:(-?[\d.]+)px;bottom:(-?[\d.]+)px;width:900px/.exec(markup);
  const ref = refGlow(t);
  if (!cyan || !violet) {
    glowOk = false;
    glowDetail = `t=${t} 解析不到光晕 style`;
    break;
  }
  const got = {left: +cyan[1], top: +cyan[2], right: +violet[1], bottom: +violet[2]};
  for (const k of ['left', 'top', 'right', 'bottom']) {
    if (!near(got[k], ref[k], 1e-4)) {
      glowOk = false;
      glowDetail = `t=${t} ${k}: 组件 ${got[k]} vs 基线 ${ref[k]}`;
    }
  }
  if (!glowOk) break;
}
ok(`glow1/glow2 在 ${glowSamples.length} 个 t 上与基线公式相同`, glowOk, glowDetail);
const bd0 = renderToStaticMarkup(React.createElement(Backdrop, {t: 0}));
ok('背景底色 #0a0a12', bd0.includes('background-color:#0a0a12'), bd0.slice(0, 160));
ok('网格 80px 且 opacity 0.05', /opacity:0.05/.test(bd0) && bd0.includes('80px 80px'));
ok('光晕用 blur(120px)', bd0.includes('filter:blur(120px)'));
// 回归守卫：GLOW 里曾误写 borderRadius: GLOW.glow（该键不存在）⇒ 光晕变方角而无人察觉
ok('光晕是圆角（border-radius:50%，来自 RADIUS.glow）', (bd0.match(/border-radius:50%/g) || []).length === 2, `实际出现 ${(bd0.match(/border-radius:50%/g) || []).length} 次`);
ok('默认无暗角（不偏离基线）', !bd0.includes('radial-gradient'));
ok('vignette=true 时才出现暗角', renderToStaticMarkup(React.createElement(Backdrop, {t: 0, vignette: true})).includes('radial-gradient'));

/* ============================ 4. RevealText 逐字语义 ============================ */
console.log('\n[4] RevealText：逐字阈值/位移 vs 基线 reveal()');
// 基线原式（保留 /N*N，不做任何化简）：p >= i*per/max(1,N)*N*0.55 + 0.02
function refLitSet(text, p, per) {
  const chars = [...text];
  const N = chars.length;
  return chars.map((_, i) => p > 0 && p >= (i * per) / Math.max(1, N) * N * 0.55 + 0.02);
}
function parseChars(markup) {
  return [...markup.matchAll(/<span style="display:inline-block;opacity:([\d.]+);transform:translateY\((-?[\d.]+)px\)">([\s\S]*?)<\/span>/g)].map((m) => ({
    opacity: parseFloat(m[1]),
    dy: parseFloat(m[2]),
    char: m[3],
  }));
}
const texts = ['把歌词做成逐字时间轴', '你的歌词，现在还只能整行亮', 'Lyric2TTML', 'a b c', '它还能做这些'];
const ps = [0, 0.01, 0.05, 0.1, 0.13, 0.2, 0.25, 0.35, 0.5, 0.66, 0.8, 0.99, 1];
let snapOk = true;
let earlyOk = true;
let lateOk = true;
let shapeOk = true;
let detail = '';
for (const text of texts) {
  for (const p of ps) {
    const snapMarkup = renderToStaticMarkup(React.createElement(RevealText, {text, progress: p, snap: true}));
    const fadeMarkup = renderToStaticMarkup(React.createElement(RevealText, {text, progress: p}));
    const snapChars = parseChars(snapMarkup);
    const fadeChars = parseChars(fadeMarkup);
    const lit = refLitSet(text, p, REVEAL_PER);
    const chars = [...text];
    if (snapChars.length !== chars.length) {
      shapeOk = false;
      detail = `拆字数量 ${snapChars.length} != ${chars.length}（${text}）`;
      break;
    }
    snapChars.forEach((c, i) => {
      if (c.opacity !== (lit[i] ? 1 : 0)) {
        snapOk = false;
        detail = `snap: ${text} p=${p} i=${i} opacity=${c.opacity} 基线lit=${lit[i]}`;
      }
      if (c.dy !== (lit[i] ? 0 : 26) && !(c.opacity === 0 && c.dy === 26)) {
        shapeOk = false;
        detail = `snap: dy 应随 opacity 归零（i=${i} op=${c.opacity} dy=${c.dy}）`;
      }
    });
    fadeChars.forEach((c, i) => {
      const thr = (i * REVEAL_PER) / Math.max(1, chars.length) * chars.length * 0.55 + 0.02;
      if (c.opacity > 0 && p < thr - 1e-9) {
        earlyOk = false;
        detail = `fade 提前显示：${text} p=${p} i=${i} thr=${thr}`;
      }
      // 一步之内必须归位（基线在该 p 已经全亮）
      const ramp = Math.min(REVEAL_PER * REVEAL_STEP, Math.max(1e-6, 1 - thr));
      if (lit[i] && p >= thr + ramp - 1e-9 && c.opacity !== 1) {
        lateOk = false;
        detail = `fade 迟于基线一步：${text} p=${p} i=${i} op=${c.opacity}`;
      }
      // 位移与不透明度严格配对
      if (!near(c.dy, (1 - c.opacity) * 26, 1e-6) && !(c.opacity === 1 && c.dy === 0)) {
        shapeOk = false;
        detail = `fade 位移与透明度不配对：i=${i} op=${c.opacity} dy=${c.dy}`;
      }
    });
  }
}
ok('snap 模式与基线 reveal() 的 lit 集合逐帧完全相同（65 组）', snapOk, detail);
ok('fade 模式绝不在基线阈值之前显示（不抢跑）', earlyOk, detail);
ok('fade 模式全亮时刻不晚于基线阈值 + 一步', lateOk, detail);
ok('逐字 span 结构 / 位移与透明度配对 / 空格换 nbsp', shapeOk, detail);
ok('阈值公式 0.02+i*per*0.55', near(revealThreshold(7, 0.045), 0.02 + 7 * 0.045 * 0.55));
ok('p=1 时所有字都归位', (() => {
  const chars = parseChars(renderToStaticMarkup(React.createElement(RevealText, {text: '把歌词做成逐字时间轴', progress: 1})));
  return chars.length === 10 && chars.every((c) => c.opacity === 1 && c.dy === 0);
})());
const emojiText = '🎯测试';
ok(
  'emoji/代理对不被劈开（按 code point 拆）',
  parseChars(renderToStaticMarkup(React.createElement(RevealText, {text: emojiText, progress: 1}))).length === [...emojiText].length,
  `拆出 ${parseChars(renderToStaticMarkup(React.createElement(RevealText, {text: emojiText, progress: 1}))).length} 个 span，期望 ${[...emojiText].length}`,
);

/* ============================ 5. ProgressBar ============================ */
console.log('\n[5] ProgressBar：宽度 = t/TOTAL*1920');
let barOk = true;
let barDetail = '';
for (const [frame, total] of [[0, 62], [90, 62], [1000, 62], [930, 62], [1859, 62], [30, 3]]) {
  global.__PROMO_FRAME__ = frame;
  global.__PROMO_SECONDS__ = total;
  const markup = renderToStaticMarkup(React.createElement(ProgressBar, {t: frame / 30, totalSeconds: total}));
  // ⚠️ React 对数值 0 不补 px（value !== 0 才补），所以 px 必须可选
  const m = /bottom:0;height:6px;width:(-?[\d.]+)(?:px)?/.exec(markup);
  const expected = Math.min(frame / 30 / total, 1) * 1920;
  if (!m || !near(parseFloat(m[1]), expected, 1e-3)) {
    barOk = false;
    barDetail = `frame=${frame} total=${total} 组件=${m && m[1]} 期望=${expected}`;
  }
}
ok('6 组 (frame,total) 宽度全部等于 min(t/TOTAL,1)*1920', barOk, barDetail);
global.__PROMO_SECONDS__ = 62;
global.__PROMO_FRAME__ = 0;
const barMarkup = renderToStaticMarkup(React.createElement(ProgressBar, {t: 0}));
ok('进度条渐变与高度来自主题', barMarkup.includes('linear-gradient(90deg,#22d3ee,#6cd97e)') && barMarkup.includes('height:6px'));

/* ============================ 6. SceneWindow 进场 ============================ */
console.log('\n[6] SceneWindow：显示窗口 + 0.7s 进场');
function parseWindow(markup) {
  const m = /display:(flex|none);[^"]*opacity:([\d.]+);transform:translateY\((-?[\d.]+)px\)/.exec(markup);
  return m ? {display: m[1], opacity: parseFloat(m[2]), dy: parseFloat(m[3])} : null;
}
let winOk = true;
let winDetail = '';
for (const [t, a, b] of [[0, 0, 7], [3.0, 0, 7], [6.999, 0, 7], [7.0, 0, 7], [3.0, 7, 17], [7.35, 7, 17], [52.0, 52, 62], [33.3333, 25, 41]]) {
  const markup = renderToStaticMarkup(React.createElement(SceneWindow, {t, a, b}, React.createElement('span', null, 'x')));
  const w = parseWindow(markup);
  const on = t >= a && t < b;
  const p = refEnter(t, a);
  if (!w || w.display !== (on ? 'flex' : 'none') || !near(w.opacity, on ? p : 0) || !near(w.dy, (1 - refEaseOut(p)) * 26, 1e-6)) {
    winOk = false;
    winDetail = `t=${t} 区间[${a},${b}) 组件=${JSON.stringify(w)} 期望 on=${on} p=${p}`;
    break;
  }
}
ok('窗口内 flex/窗口外 none，opacity/位移 = 基线 enter+easeOut', winOk, winDetail);
ok('左内边距 140px（.scene padding）', renderToStaticMarkup(React.createElement(SceneWindow, {t: 0, a: 0, b: 7}, null)).includes('padding:0 140px'));

/* ============================ 7. Composition 端到端（scenes 被 stub） ============================ */
console.log('\n[7] Composition：帧号 → 场景可见性 / 进度条（scenes 用 stub，core 自检与 scene-builder 解耦）');
function renderComposition(frame) {
  global.__PROMO_FRAME__ = frame;
  return renderToStaticMarkup(React.createElement(Lyric2TTML, {...PROMO_DEFAULT_PROPS}));
}
for (const [frame, expectScene] of [[90, 's0'], [1000, 's3']]) {
  const markup = renderComposition(frame);
  const visible = [];
  for (const seg of timeline.SCENES) {
    const idx = markup.indexOf(`data-scene="${seg.id}"`);
    if (idx < 0) continue;
    // 往前找最近的 SceneWindow div 的 display
    const before = markup.slice(Math.max(0, idx - 900), idx);
    const displays = [...before.matchAll(/display:(flex|none)/g)];
    const last = displays.length ? displays[displays.length - 1][1] : '?';
    if (last === 'flex') visible.push(seg.id);
  }
  ok(`frame=${frame} (t=${(frame / 30).toFixed(3)}s) 只有 ${expectScene} 可见`, visible.length === 1 && visible[0] === expectScene, `实际可见 ${JSON.stringify(visible)}`);
  const expectedWidth = Math.min(frame / 30 / 62, 1) * 1920;
  const bar = /bottom:0;height:6px;width:(-?[\d.]+)px/.exec(markup);
  ok(`frame=${frame} 进度条宽度 ≈ ${expectedWidth.toFixed(2)}px`, bar && near(parseFloat(bar[1]), expectedWidth, 1e-3), bar && bar[1]);
}
ok('6 个场景组件都被渲染（各自负责显示/隐藏）', timeline.SCENES.every((s) => renderComposition(90).includes(`data-scene="${s.id}"`)));
ok('同一帧两次渲染逐字节相同（纯函数）', renderComposition(123) === renderComposition(123));
ok('默认 props videoSeconds=62', PROMO_DEFAULT_PROPS.videoSeconds === 62);

/* ============================ 8. 静态检查：接线 + 确定性 ============================ */
console.log('\n[8] 静态检查：场景接线与"禁用不确定性 API"');
const comp = readSrc(path.join('src', 'Composition.tsx'));
const root = readSrc(path.join('src', 'Root.tsx'));
const index = readSrc(path.join('src', 'index.ts'));
const FROZEN = ['S0Opening', 'S1Problem', 'S2Input', 'S3Features', 'S4Stats', 'S5Ending'];
ok("Composition 从 './scenes' 导入", /import\s*\{[^}]*\}\s*from\s*'\.\/scenes'/.test(comp));
for (const name of FROZEN) {
  ok(`Composition 导入并使用 ${name}`, new RegExp(`\\b${name}\\b`).test(comp));
}
const mapping = /const SCENE_SLOTS[\s\S]*?\n\};/.exec(comp);
ok('SCENE_SLOTS 把 s0..s5 依次映射到 6 个场景', !!mapping && FROZEN.every((n, i) => new RegExp(`s${i}:\\s*${n}`).test(mapping[0])), mapping ? mapping[0].replace(/\s+/g, ' ').slice(0, 160) : 'not found');
ok('Composition 不再自己包 SceneWindow（避免 opacity p²）', !/SceneWindow/.test(stripComments(comp)));
ok('Root 注册 id="Lyric2TTML"', /id="Lyric2TTML"/.test(root) && /registerRoot/.test(index));
ok('Root 用 1920x1080 / fps 来自主题', /width=\{VIDEO\.width\}/.test(root) && /height=\{VIDEO\.height\}/.test(root) && /fps=\{VIDEO\.fps\}/.test(root));
ok('时长走 calculateMetadata（与音频解耦）', /calculateMetadata/.test(root) && /videoSeconds/.test(root));
ok('Composition 含 <Audio staticFile(\'audio.wav\')>', /Audio/.test(comp) && /staticFile\('audio\.wav'\)/.test(comp));

/* --- 回归守卫：相对 import 的每个名字必须真的存在 ---------------------------
 * 真踩过：`import {FPS, VIDEO, VIDEO_SECONDS} from './core/timeline'` —— timeline
 * 没有 VIDEO 这个导出，tsc 报错、运行期 `VIDEO.fps` TypeError 白屏。 */
function knownExports(rel) {
  const names = new Set();
  try {
    const mod = projRequire(path.join(PROMO, rel));
    for (const k of Object.keys(mod)) names.add(k);
  } catch (e) {
    /* 只导出类型、或依赖 remotion 运行期 API 的模块：退回源码正则 */
  }
  const src = readSrc(rel);
  for (const m of src.matchAll(/export\s+(?:type|interface|const|function|class)\s+([A-Za-z0-9_$]+)/g)) names.add(m[1]);
  for (const m of src.matchAll(/export\s*(?:type\s*)?\{([^}]*)\}/g)) {
    for (const part of m[1].split(',')) {
      const n = part.trim().replace(/^type\s+/, '').split(/\s+as\s+/).pop();
      if (n) names.add(n.trim());
    }
  }
  return names;
}
const REL_IMPORT = /import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+'(\.[^']+)'/g;
const ownFiles = [
  'src/Composition.tsx',
  'src/Root.tsx',
  ...['theme.ts', 'timeline.ts', 'Backdrop.tsx', 'RevealText.tsx', 'ProgressBar.tsx', 'SceneWindow.tsx', 'SmokeScene.tsx'].map((f) => 'src/core/' + f),
];
let importOk = true;
let importDetail = '';
for (const rel of ownFiles) {
  for (const m of readSrc(rel).matchAll(REL_IMPORT)) {
    const spec = m[2];
    const base = path.join(path.dirname(path.join(PROMO, rel)), spec);
    const cand = [base + '.ts', base + '.tsx', path.join(base, 'index.ts'), path.join(base, 'index.tsx')].find((f) => fs.existsSync(f));
    if (!cand) {
      importOk = false;
      importDetail = `${rel} 找不到模块 ${spec}`;
      continue;
    }
    const targetRel = path.relative(PROMO, cand).replace(/\\/g, '/');
    const exp = knownExports(targetRel);
    for (const part of m[1].split(',')) {
      const name = part.trim().replace(/^type\s+/, '').split(/\s+as\s+/)[0].trim();
      if (name && !exp.has(name)) {
        importOk = false;
        importDetail = `${rel} 从 ${spec} 导入的 ${name} 在 ${targetRel} 里不存在`;
      }
    }
  }
}
ok(`相对 import 的每个名字都真实存在（扫 ${ownFiles.length} 个文件）`, importOk, importDetail);

const forbidden = /Math\.random|Date\.now|performance\.now|setTimeout|setInterval|requestAnimationFrame|new Date\(|transition:|animation:/;
const scanned = ['src/Composition.tsx', 'src/Root.tsx', ...['theme.ts', 'timeline.ts', 'Backdrop.tsx', 'RevealText.tsx', 'ProgressBar.tsx', 'SceneWindow.tsx', 'SmokeScene.tsx'].map((f) => 'src/core/' + f)];
const dirty = scanned.filter((rel) => forbidden.test(stripComments(readSrc(rel))));
ok(`core+Composition+Root 无不确定性 API（扫 ${scanned.length} 个文件）`, dirty.length === 0, dirty.join(', '));
ok('core 里没有 Google Fonts / 网络字体', scanned.every((rel) => !/fonts\.googleapis|https?:\/\//.test(readSrc(rel))));

// scene-builder 的交付情况（不判失败，只报告）
const sceneFiles = fs.existsSync(path.join(PROMO, 'src', 'scenes')) ? fs.readdirSync(path.join(PROMO, 'src', 'scenes')) : [];
for (const name of FROZEN) {
  const f = path.join(PROMO, 'src', 'scenes', `${name}.tsx`);
  if (!fs.existsSync(f)) info(`场景文件尚未交付：src/scenes/${name}.tsx`);
  else if (!new RegExp(`export\\s+const\\s+${name}\\b`).test(fs.readFileSync(f, 'utf8'))) failures.push(`src/scenes/${name}.tsx 里没有 export const ${name}`);
}
if (fs.existsSync(path.join(PROMO, 'src', 'scenes', 'index.ts'))) {
  const idx = fs.readFileSync(path.join(PROMO, 'src', 'scenes', 'index.ts'), 'utf8');
  ok('scenes/index.ts re-export 六个场景', FROZEN.every((n) => idx.includes(n)));
} else {
  info('src/scenes/index.ts 尚未交付（Composition 的静态 import 在它出现前无法打包）');
}
if (sceneFiles.includes('_shared.ts')) failures.push('src/scenes/_shared.ts 含 JSX 但扩展名是 .ts —— 必须改名为 _shared.tsx（esbuild 只对 .tsx 认 JSX）');

/* ================================ 汇总 ================================ */
console.log('\n' + '='.repeat(72));
console.log(`core 自检：${pass} 项通过，${failures.length} 项失败`);
if (infos.length) console.log(`（${infos.length} 条信息：场景交付进度）`);
for (const f of failures) console.log('  ✗ ' + f);
console.log('='.repeat(72));
process.exit(failures.length ? 1 : 0);
