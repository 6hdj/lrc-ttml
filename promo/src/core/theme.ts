/**
 * 宣传片视觉常量 —— **每一个数值都逐条来自基线 `tools/demo_video/slides.html`**。
 *
 * 基线成片 `Lyric2TTML-宣传片.mp4`（62s / 1920x1080 / 30fps）就是照着这份 CSS 出的，
 * 所以改这里的任何数字都等于偏离基线。改之前先对照 slides.html 的 <style> 块。
 *
 * 除颜色外还导出：字号（SIZE）、字重（WEIGHT）、行高（LINE_HEIGHT）、
 * 间距（SPACING）、圆角（RADIUS）、网格（GRID）、光晕（GLOW）、画布（VIDEO）。
 */

export const COLORS = {
  /** 页面底色 */
  bg: '#0a0a12',
  /** 正文 */
  text: '#e8e8f0',
  /** 主强调色（青） */
  cyan: '#22d3ee',
  /** 次强调色（绿） */
  green: '#6cd97e',
  /** 次要正文（.sub） */
  gray: '#9aa4bf',
  /** 暗灰（.dim） */
  dim: '#6b7590',

  /** .row .d —— 能力列表右侧说明 */
  rowDesc: '#8b95ad',
  /** .cline .cap —— 对比块的小标题 */
  capText: '#7d879f',
  /** #url —— 仓库地址 */
  url: '#9fd3c7',
  /** .box 里的文字 */
  boxText: '#eafcff',

  /** .row 的底/边 */
  rowBg: 'rgba(255,255,255,.035)',
  rowBorder: 'rgba(255,255,255,.07)',

  /** .cline.bad —— 整行高亮那一侧 */
  badBg: 'rgba(255,90,90,.06)',
  badBorder: 'rgba(255,90,90,.28)',
  /** .cline.good —— 逐字那一侧 */
  goodBg: 'rgba(34,211,238,.07)',
  goodBorder: 'rgba(34,211,238,.34)',

  /** .box 高亮字块 */
  boxBg: 'rgba(34,211,238,.20)',

  /** 两个背景光晕 */
  glowCyan: 'rgba(34,211,238,.16)',
  glowViolet: 'rgba(124,92,255,.15)',

  /** 底部进度条渐变（#bar） */
  barGradient: 'linear-gradient(90deg,#22d3ee,#6cd97e)',
} as const;

export const FONT = {
  /** 中文字体只用系统字体，**绝不联网取字体** */
  family: "'Microsoft YaHei', 'Microsoft YaHei UI', 'YH', sans-serif",
  mono: "Consolas, 'Courier New', monospace",
} as const;

/** 字号（px），与 slides.html 一一对应 */
export const SIZE = {
  h1: 116, // h1
  h1End: 92, // #s5h1 的 inline font-size:92px
  h2: 74, // h2
  sub: 40, // .sub
  subSmall: 36, // #s2note 的 inline font-size:36px
  subTiny: 32, // #s5sub 内层 span 的 font-size:32px
  subEnd: 30, // #s5 最后一行 sub 的 inline font-size:30px
  tag: 34, // .tag
  row: 46, // .row
  rowIco: 52, // .row .ico
  rowDesc: 34, // .row .d
  statNumber: 130, // .stat .n
  statLabel: 34, // .stat .l
  cline: 50, // .cline
  clineCap: 30, // .cline .cap
  url: 40, // #url
} as const;

export const WEIGHT = {
  h1: 800,
  h2: 700,
  tag: 600,
  rowTitle: 700,
  statNumber: 800,
  highlight: 700,
  box: 700,
} as const;

export const LINE_HEIGHT = {
  h1: 1.18,
  h2: 1.25,
  sub: 1.6,
  statNumber: 1,
} as const;

/** 间距 / 内边距（px） */
export const SPACING = {
  /** .scene 的左右内边距 */
  scenePaddingX: 140,
  /** h1/h2 的字距 */
  h1LetterSpacing: 2,
  tagLetterSpacing: 8,
  urlLetterSpacing: 1,

  subMarginTop: 34,
  tagMarginBottom: 26,

  rowsMarginTop: 40,
  rowPaddingY: 26,
  rowPaddingX: 40,
  rowMarginY: 18,
  rowGap: 34,
  rowIcoWidth: 66,

  statsMarginTop: 60,
  statsGap: 90,
  statLabelMarginTop: 18,

  compareMarginTop: 50,
  compareGap: 46,
  clinePaddingY: 34,
  clinePaddingX: 50,
  clineCapMarginBottom: 14,

  urlMarginTop: 30,

  /** 逐字出现时每个字的上移距离（.ch 的 translateY(26px)） */
  charLift: 26,
  /** 场景进入时的上移距离（seek() 里的 translateY） */
  sceneLift: 26,
  /** 场景 1 里 .good 块横向滑入的距离 */
  slideInX: 60,
  /** 场景 1 里对比块横向滑入的距离（translateX 40px） */
  compareSlideX: 40,
} as const;

export const RADIUS = {
  row: 20,
  cline: 22,
  box: 12,
  glow: '50%',
} as const;

/** 背景网格：#grid */
export const GRID = {
  opacity: 0.05,
  size: 80,
  line: '#fff',
} as const;

/** 两个光晕：尺寸 + blur + 漂移公式（漂移在 Backdrop.tsx 里按 t 逐帧算） */
export const GLOW = {
  blurPx: 120,
  cyan: {
    size: 1100,
    leftBase: -160,
    leftAmp: 120,
    leftPeriod: 11, // left = -160 + 120*sin(t/11)
    topBase: -260,
    topAmp: 90,
    topPeriod: 13, // top  = -260 + 90*cos(t/13)
  },
  violet: {
    size: 900,
    rightBase: -200,
    rightAmp: 110,
    rightPeriod: 9,
    rightPhase: 1.2, // right  = -200 + 110*sin(t/9 + 1.2)
    bottomBase: -220,
    bottomAmp: 100,
    bottomPeriod: 15, // bottom = -220 + 100*cos(t/15)
  },
} as const;

/** 画布与时间：基线的 TOTAL 与 #bar 高度 */
export const VIDEO = {
  width: 1920,
  height: 1080,
  fps: 30,
  /** 基线时间轴总长（秒）。只作为 props 的默认值 —— 渲染时长由 videoSeconds 决定，与音频时长解耦 */
  seconds: 62,
  progressBarHeight: 6,
} as const;

/* ------------------------------------------------------------------ *
 * 兼容再导出：时间轴工具在 ./timeline 里，但场景常只需 import 一个模块。
 * ------------------------------------------------------------------ */
export {SCENES, SCENE_BY_ID, VIDEO_SECONDS, FPS, clamp, easeOut, enter, smoothstep} from './timeline';
export type {SceneSegment} from './timeline';
