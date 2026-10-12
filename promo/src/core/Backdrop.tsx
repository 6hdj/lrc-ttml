import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS, GLOW, GRID, RADIUS} from './theme';

export type BackdropProps = {
  /**
   * 当前时间（秒）。**默认取当前帧 / fps** —— 逐帧确定性由它保证。
   * 显式传入只用于测试（同一个 t 永远得到同一张画面）。
   */
  t?: number;
  /** 可选暗角（基线没有；默认关闭，开了会偏离基线） */
  vignette?: boolean;
  /** 暗角强度 0→1，只在 vignette=true 时生效 */
  vignetteStrength?: number;
};

/**
 * 背景层：两个缓慢漂移的模糊光晕 + 网格。
 *
 * 漂移公式逐字来自基线 slides.html 的 seek()：
 *   glow1: left = -160 + 120*sin(t/11)   top    = -260 + 90*cos(t/13)
 *   glow2: right = -200 + 110*sin(t/9+1.2) bottom = -220 + 100*cos(t/15)
 *
 * ⚠️ 这些位置**每一帧都是算出来的**，不用 CSS animation / transition
 *    （基线就是踩了"截图速度影响动画"才改成 seek(t) 的）。
 */
export const Backdrop: React.FC<BackdropProps> = ({t, vignette = false, vignetteStrength = 0.55}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const time = t ?? frame / fps;

  const g1Left = GLOW.cyan.leftBase + GLOW.cyan.leftAmp * Math.sin(time / GLOW.cyan.leftPeriod);
  const g1Top = GLOW.cyan.topBase + GLOW.cyan.topAmp * Math.cos(time / GLOW.cyan.topPeriod);
  const g2Right = GLOW.violet.rightBase + GLOW.violet.rightAmp * Math.sin(time / GLOW.violet.rightPeriod + GLOW.violet.rightPhase);
  const g2Bottom = GLOW.violet.bottomBase + GLOW.violet.bottomAmp * Math.cos(time / GLOW.violet.bottomPeriod);

  return (
    <AbsoluteFill style={{backgroundColor: COLORS.bg, overflow: 'hidden'}}>
      <div
        style={{
          position: 'absolute',
          left: g1Left,
          top: g1Top,
          width: GLOW.cyan.size,
          height: GLOW.cyan.size,
          borderRadius: RADIUS.glow,
          background: COLORS.glowCyan,
          filter: `blur(${GLOW.blurPx}px)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          right: g2Right,
          bottom: g2Bottom,
          width: GLOW.violet.size,
          height: GLOW.violet.size,
          borderRadius: RADIUS.glow,
          background: COLORS.glowViolet,
          filter: `blur(${GLOW.blurPx}px)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: GRID.opacity,
          backgroundImage: `linear-gradient(${GRID.line} 1px, transparent 1px), linear-gradient(90deg, ${GRID.line} 1px, transparent 1px)`,
          backgroundSize: `${GRID.size}px ${GRID.size}px`,
        }}
      />
      {vignette ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: `radial-gradient(ellipse at 50% 50%, rgba(0,0,0,0) 45%, rgba(0,0,0,${vignetteStrength}) 100%)`,
          }}
        />
      ) : null}
    </AbsoluteFill>
  );
};
