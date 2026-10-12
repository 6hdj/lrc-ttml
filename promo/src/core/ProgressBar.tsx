import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS, VIDEO} from './theme';
import {clamp} from './timeline';

export type ProgressBarProps = {
  /** 当前时间（秒）。默认取当前帧 / fps */
  t?: number;
  /** 总时长（秒）。默认取 composition 的 durationInFrames / fps，也就是 videoSeconds */
  totalSeconds?: number;
  /** 条高，默认 6px（基线 #bar） */
  height?: number;
  /** 画布宽，默认 1920 */
  width?: number;
};

/**
 * 底部进度条：宽度 = t / TOTAL * 1920，渐变 #22d3ee → #6cd97e。
 *
 * ⚠️ TOTAL 取 composition 时长（= props.videoSeconds），**不读音频时长** ——
 *    宣传片要能脱离音频单独渲染/改时长（音频只是 public/audio.wav 素材）。
 */
export const ProgressBar: React.FC<ProgressBarProps> = ({
  t,
  totalSeconds,
  height = VIDEO.progressBarHeight,
  width = VIDEO.width,
}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const time = t ?? frame / fps;
  const total = totalSeconds ?? durationInFrames / fps;
  const progress = total > 0 ? clamp(time / total, 0, 1) : 0;

  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        bottom: 0,
        height,
        width: progress * width,
        background: COLORS.barGradient,
      }}
    />
  );
};
