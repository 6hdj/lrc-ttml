/**
 * 画布与基础常量。
 *
 * 风格参考《人类简史 · 史诗版》：近黑底 + 一点环境光 + 青色强调 + 等宽数字。
 * 改这里的任何数值都会影响全部 10 场，改前先看 SPEC.md §1。
 */
export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

/** 中文字体只用系统字体，绝不联网取 */
export const FONT =
  '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui, sans-serif';

/** interpolate 的两端 clamp 预设（几乎所有动画都要） */
export const clamp = {
  extrapolateLeft: 'clamp',
  extrapolateRight: 'clamp',
} as const;

/** 调色板：SPEC.md §1 的那张表 */
export const C = {
  /** 近黑底（两档，交替用让相邻场有细微差别） */
  bgA: '#03040a',
  bgB: '#04050c',
  /** 文字 */
  text: '#f2f7ff',
  white: '#ffffff',
  sub: '#8fa6c4',
  dim: '#6f8db8',
  faint: '#5f7396',
  /** 强调 */
  cyan: '#22d3ee',
  cyanPale: '#a5f3fc',
  green: '#a3e635',
  violet: '#a78bfa',
  /** "旧做法"用暖色对比 */
  warm: '#f59e0b',
  /** 线条 */
  line: 'rgba(255,255,255,0.08)',
  axis: 'rgba(255,255,255,0.20)',
} as const;

/** 环境光预设（放在场景最底层） */
export const GLOW = {
  cyanSoft:
    'radial-gradient(60% 50% at 62% 42%, rgba(34,211,238,0.08) 0%, transparent 70%)',
  cyanCenter:
    'radial-gradient(60% 45% at 50% 48%, rgba(34,211,238,0.10) 0%, transparent 72%)',
  violetSoft:
    'radial-gradient(55% 50% at 38% 46%, rgba(167,139,250,0.09) 0%, transparent 72%)',
  warmSoft:
    'radial-gradient(60% 45% at 50% 48%, rgba(245,158,11,0.09) 0%, transparent 72%)',
} as const;

/** 角标的固定位置（每场都用同一个位置，观众才会觉得"这是同一支片"） */
export const CORNER = {left: 96, top: 78} as const;
