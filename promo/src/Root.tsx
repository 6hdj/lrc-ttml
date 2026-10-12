import React from 'react';
import {Composition} from 'remotion';
import {Lyric2TTML, PROMO_DEFAULT_PROPS, calcPromoMetadata, type PromoProps} from './Composition';
// ⚠️ VIDEO 在 theme 里（不在 timeline）；FPS / VIDEO_SECONDS 两处都有，这里从 timeline 取。
import {FPS, VIDEO_SECONDS} from './core/timeline';
import {VIDEO} from './core/theme';

/**
 * Composition 注册表。
 *
 * `Lyric2TTML`：1920x1080 @ 30fps，默认 62 秒（= FPS * VIDEO_SECONDS）。
 * 时长由 props.videoSeconds 经 calculateMetadata 决定 ⇒ 与音频时长解耦：
 *   npx remotion render Lyric2TTML out/3s.mp4 --props='{"videoSeconds":3}'
 */
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Lyric2TTML"
      component={Lyric2TTML}
      durationInFrames={Math.round(FPS * VIDEO_SECONDS)}
      fps={VIDEO.fps}
      width={VIDEO.width}
      height={VIDEO.height}
      defaultProps={PROMO_DEFAULT_PROPS}
      calculateMetadata={calcPromoMetadata as (args: {
        props: PromoProps;
        defaultProps: PromoProps;
        abortSignal: AbortSignal;
        compositionId: string;
      }) => {durationInFrames: number}}
    />
  );
};
