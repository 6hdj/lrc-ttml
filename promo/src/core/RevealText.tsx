import React from 'react';
import {clamp} from './timeline';

/** 基线的 reveal(chars, p, per = 0.045) 默认节奏 */
export const REVEAL_PER = 0.045;
/** 基线里每一步的系数（`i * per / max(1,N) * N * 0.55`，N/N 约掉后就是 *0.55） */
export const REVEAL_STEP = 0.55;
/** 基线里的固定起跳量 +0.02 */
export const REVEAL_BASE = 0.02;

export type RevealTextProps = {
  /** 要逐字出现的字符串（按 code point 拆，emoji 不会被劈成两半） */
  text: string;
  /**
   * 出现进度 0→1（由调用方按 t 算好，例如 clamp((t-1.0)/2.6, 0, 1)）。
   * **不接受时间/帧之外的输入** —— 同一个 progress 永远得到同一张画面。
   */
  progress: number;
  /** 每个字的节奏，默认 0.045（基线值） */
  per?: number;
  /** 整段文字的样式（字号/字重/行高/颜色等由调用方给，与基线 h1/h2 一致） */
  style?: React.CSSProperties;
  /** 单个字的额外样式（可返回 undefined） */
  charStyle?: (char: string, index: number) => React.CSSProperties | undefined;
  /**
   * true = 完全照基线的"到点即亮"（opacity 0/1 跳变）。
   * false（默认）= 同一个节奏，但每个字在**一步之内**淡入 + 上移 26px 归位，
   * 视觉上更顺；两者的开亮时刻完全一致，差别只有约一个步长（≈2 帧 @30fps）。
   */
  snap?: boolean;
  className?: string;
};

/**
 * 第 i 个字的开亮阈值 —— 与基线 reveal() 逐字等价。
 *
 * 基线源码：`p >= i * per / Math.max(1, N) * N * 0.55 + 0.02`
 * 其中 N 就是 chars.length，`/N*N` 相消 ⇒ 阈值 = 0.02 + i*per*0.55。
 * （这里按消掉后的等价式写；结果与基线**逐帧一致**，不是近似。）
 */
export function revealThreshold(index: number, per: number = REVEAL_PER): number {
  return REVEAL_BASE + index * per * REVEAL_STEP;
}

/**
 * 逐字出现的文字。
 *
 * 每个字一个 `display:inline-block` 的 span；opacity 与 translateY 全部由 progress 决定，
 * **没有 setTimeout / CSS transition / requestAnimationFrame** —— 抽到任意一帧都能重放。
 */
export const RevealText: React.FC<RevealTextProps> = ({
  text,
  progress,
  per = REVEAL_PER,
  style,
  charStyle,
  snap = false,
  className,
}) => {
  const p = clamp(progress, 0, 1);
  const chars = React.useMemo(() => Array.from(text), [text]);
  // 一步的长度（progress 空间）。最后几个字阈值贴近 1 时压缩 ramp，保证 p=1 时全部归位。
  const step = Math.max(1e-6, per * REVEAL_STEP);

  return (
    <span className={className} style={style}>
      {chars.map((char, i) => {
        const threshold = revealThreshold(i, per);
        const local = snap
          ? p >= threshold && p > 0
            ? 1
            : 0
          : clamp((p - threshold) / Math.min(step, Math.max(1e-6, 1 - threshold)), 0, 1);
        return (
          <span
            key={`${i}-${char}`}
            style={{
              display: 'inline-block',
              // 基线把空格换成 nbsp，否则 inline-block 之间的空格会被折叠掉
              opacity: local,
              transform: `translateY(${(1 - local) * 26}px)`,
              ...charStyle?.(char, i),
            }}
          >
            {char === ' ' ? '\u00a0' : char}
          </span>
        );
      })}
    </span>
  );
};
