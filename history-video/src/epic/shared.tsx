/**
 * 十场共用的动画小工具与视觉件。
 *
 * 为什么要有这个文件：参考片十场各写各的，结果**同一支片里出现了好几种"进场手感"**
 * （有的 20 帧、有的 30 帧、缓动也不一样）。这里把"进场/上移/锐化/角标/环境光"
 * 固定成一套，各场只在**画面形式**上求差异，手感统一。
 *
 * 所有函数都是**帧的纯函数**：同一个 frame 永远得到同一个值（Remotion 并发渲染的前提）。
 * 严禁 `Math.random()` / `Date.now()` / `setTimeout` —— 要随机用 `timing.ts` 的 `rnd()`。
 */
import React from 'react';
import {Easing, interpolate} from 'remotion';
import {C, CORNER, FONT, GLOW, clamp} from '../constants';

/* ------------------------------------------------------------------ *
 * 数值工具
 * ------------------------------------------------------------------ */

/** 帧区间 → 数值。两端 clamp，等价于参考片里到处写的 `interpolate(frame,[a,b],[v0,v1],clamp)`。 */
export const seg = (
  frame: number,
  a: number,
  b: number,
  v0: number,
  v1: number,
  easing?: (t: number) => number,
): number =>
  interpolate(frame, [a, b], [v0, v1], {
    ...clamp,
    ...(easing ? {easing} : {}),
  });

/** 常用缓动 */
export const EASE = {
  outCubic: Easing.out(Easing.cubic),
  outQuad: Easing.out(Easing.quad),
  inCubic: Easing.in(Easing.cubic),
  inOutQuad: Easing.inOut(Easing.quad),
} as const;

/** 0→1 的进场进度（默认 18 帧，easeOutCubic） */
export const appear = (frame: number, at: number, dur = 18): number =>
  seg(frame, at, at + dur, 0, 1, EASE.outCubic);

/** 逐字/逐块"从模糊中锐化"的 blur 值（参考片最标志性的一个动作） */
export const blurIn = (frame: number, at: number, dur = 22, from = 16): number =>
  seg(frame, at, at + dur, from, 0);

/** 上移归位 */
export const riseIn = (frame: number, at: number, dur = 24, from = 26): number =>
  seg(frame, at, at + dur, from, 0, EASE.outCubic);

/** 数字滚动：从 v0 线性/easeOut 到 v1，在 [a,b] 内完成 */
export const rollNum = (
  frame: number,
  a: number,
  b: number,
  v0: number,
  v1: number,
  easing: (t: number) => number = EASE.outCubic,
): number => seg(frame, a, b, v0, v1, easing);

/** 每 3 位加逗号（等宽数字读数用） */
export const commas = (n: number): string =>
  Math.round(n)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ',');

/* ------------------------------------------------------------------ *
 * 视觉件
 * ------------------------------------------------------------------ */

/** 近黑底 + 可选环境光的 AbsoluteFill 样式 */
export const stage = (
  bg: string = C.bgA,
  glow: string | null = null,
): React.CSSProperties => ({
  backgroundColor: bg,
  backgroundImage: glow ?? undefined,
  fontFamily: FONT,
  overflow: 'hidden',
});

/** 顶部环境光单独一层（想叠多种光时用） */
export const glowLayer = (background: string): React.CSSProperties => ({
  background,
});

/** 文字发光预设 */
export const glowText = (color: string, r = 70, alpha = 0.45): string =>
  `0 0 ${r}px ${hexA(color, alpha)}`;

/** #rrggbb → rgba(r,g,b,a)；已经是 rgba() 的原样返回 */
export const hexA = (color: string, alpha: number): string => {
  if (!color.startsWith('#')) {
    return color;
  }
  const h = color.slice(1);
  const n = parseInt(
    h.length === 3
      ? h
          .split('')
          .map((c) => c + c)
          .join('')
      : h,
    16,
  );
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
};

/** 等宽数字（读数/计时器必须加，否则数字跳动时宽度会抖） */
export const TABULAR: React.CSSProperties = {fontVariantNumeric: 'tabular-nums'};

/**
 * 左上角角标：44px/800 标题 + 19px 副标题，位置固定为 `constants.CORNER`。
 * 每场都调它 —— 这是"同一支片"的视觉锚点。
 */
export const CornerLabel: React.FC<{
  frame: number;
  title: string;
  sub?: string;
  color?: string;
  at?: number;
}> = ({frame, title, sub, color = C.text, at = 6}) => (
  <div
    style={{
      position: 'absolute',
      left: CORNER.left,
      top: CORNER.top,
      opacity: appear(frame, at, 24),
      transform: `translateY(${riseIn(frame, at + 4, 24, 14)}px)`,
    }}
  >
    <div style={{fontSize: 44, fontWeight: 800, color, letterSpacing: 6}}>{title}</div>
    {sub ? (
      <div style={{fontSize: 19, color: C.dim, letterSpacing: 4, marginTop: 12}}>{sub}</div>
    ) : null}
  </div>
);

/** 右上角读数块（等宽数字 + 细边框），参考片里每场几乎都有 */
export const Readout: React.FC<{
  frame: number;
  rows: string[];
  at?: number;
  right?: number;
  top?: number;
  color?: string;
  align?: 'right' | 'left';
}> = ({frame, rows, at = 10, right = 96, top = 78, color = C.cyanPale, align = 'right'}) => (
  <div
    style={{
      position: 'absolute',
      ...(align === 'right' ? {right} : {left: right}),
      top,
      textAlign: align,
      opacity: appear(frame, at, 22),
      fontFamily: FONT,
      ...TABULAR,
    }}
  >
    {rows.map((r, i) => (
      <div
        key={r}
        style={{
          fontSize: i === 0 ? 22 : 18,
          color: i === 0 ? color : C.dim,
          letterSpacing: 2,
          marginTop: i === 0 ? 0 : 8,
        }}
      >
        {r}
      </div>
    ))}
  </div>
);

/** 底部来源/说明小字（居中） */
export const Caption: React.FC<{
  frame: number;
  text: string;
  at?: number;
  bottom?: number;
  color?: string;
  size?: number;
}> = ({frame, text, at, bottom = 92, color = C.faint, size = 21}) => (
  <div
    style={{
      position: 'absolute',
      left: 0,
      right: 0,
      bottom,
      textAlign: 'center',
      fontSize: size,
      color,
      letterSpacing: 3,
      ...(at === undefined ? {} : {opacity: appear(frame, at, 24)}),
    }}
  >
    {text}
  </div>
);

/** 一行歌词字块：`active` 控制哪些字是亮的（逐字高亮的通用画法） */
export const CharRun: React.FC<{
  text: string;
  /** 每个字的亮度 0..1（数组长度按 code point 取） */
  lit: number[];
  size?: number;
  gap?: number;
  onColor?: string;
  offColor?: string;
  boxFill?: boolean;
}> = ({text, lit, size = 76, gap = 14, onColor = C.text, offColor = '#43506b', boxFill = true}) => (
  <div style={{display: 'flex', gap, justifyContent: 'center', alignItems: 'flex-end'}}>
    {Array.from(text).map((ch, i) => {
      const on = lit[i] ?? 0;
      return (
        <span
          key={`${i}-${ch}`}
          style={{
            display: 'inline-block',
            fontSize: size,
            fontWeight: 700,
            lineHeight: 1.1,
            color: on > 0.5 ? onColor : offColor,
            background: boxFill && on > 0.02 ? hexA(C.cyan, 0.22 * on) : 'transparent',
            borderRadius: 10,
            padding: '2px 6px',
            textShadow: on > 0.5 ? glowText(C.cyan, 34, 0.5) : undefined,
          }}
        >
          {ch}
        </span>
      );
    })}
  </div>
);

export const BG = GLOW;
