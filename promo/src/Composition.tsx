import React from 'react';
import {AbsoluteFill, Audio, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Backdrop} from './core/Backdrop';
import {ProgressBar} from './core/ProgressBar';
import {COLORS, FONT} from './core/theme';
import {FPS, SCENES, VIDEO_SECONDS, type SceneSegment} from './core/timeline';
import {S0Opening, S1Problem, S2Input, S3Features, S4Stats, S5Ending} from './scenes';

/**
 * Composition 的可参数化 props。
 *
 * ⚠️ `videoSeconds` 只影响 composition 时长，**与 public/audio.wav 的实际时长解耦**：
 *    音频是素材，渲染时长由这个 props 决定（默认 62s = 基线成片长度）。
 */
export type PromoProps = {
  /** 成片时长（秒）。默认 62 —— 基线长度 */
  videoSeconds: number;
  /** 可选暗角，默认关闭（基线没有；开了会偏离基线） */
  vignette?: boolean;
};

export const PROMO_DEFAULT_PROPS: PromoProps = {
  videoSeconds: VIDEO_SECONDS,
  vignette: false,
};

/**
 * s0…s5 ↔ 场景组件。顺序/区间只从 `core/timeline` 的 SCENES 来，不在这里另抄一份。
 *
 * ⚠️ 分工（已与 Lead/scene-builder 冻结）：**场景自持 `SceneWindow`**
 *    （scene-builder 的 `_shared.tsx` 里那个 `Scene` 就是包 core 的 SceneWindow），
 *    所以这里**不再**包一层 —— 包两层会让 opacity 变成 p²、进场也跟着快一倍。
 */
const SCENE_SLOTS: Readonly<Record<SceneSegment['id'], React.FC<{t: number}>>> = {
  s0: S0Opening,
  s1: S1Problem,
  s2: S2Input,
  s3: S3Features,
  s4: S4Stats,
  s5: S5Ending,
};

/**
 * 宣传片合成：背景 + 6 个场景（各自在时间窗内进场）+ 底部进度条 + 旁白/配乐音轨。
 *
 * 逐帧确定性：这里所有可见状态都只由 `frame` 推出（t = frame / fps），
 * 没有任何 CSS animation / transition / setTimeout。
 */
export const Lyric2TTML: React.FC<PromoProps> = ({videoSeconds = VIDEO_SECONDS, vignette = false}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: COLORS.bg,
        fontFamily: FONT.family,
        color: COLORS.text,
        overflow: 'hidden',
      }}
    >
      <Backdrop t={t} vignette={vignette} />

      {SCENES.map((seg) => {
        const Scene = SCENE_SLOTS[seg.id];
        return <Scene key={seg.id} t={t} />;
      })}

      <ProgressBar t={t} totalSeconds={videoSeconds} />

      {/* 音轨：62s 中文旁白 + 自制配乐（基线 .demo/out/audio.wav 的逐字节副本） */}
      <Audio src={staticFile('audio.wav')} />
    </AbsoluteFill>
  );
};

/** 时长换算（秒 → 帧），供 Root.tsx 与调试使用 */
export const promoDurationInFrames = (seconds: number = VIDEO_SECONDS): number =>
  Math.max(1, Math.round(seconds * FPS));

/** 让 composition 时长跟着 props 走（Remotion 的 calculateMetadata） */
export const calcPromoMetadata = ({props}: {props: PromoProps}) => ({
  durationInFrames: promoDurationInFrames(props.videoSeconds),
});
