import React from 'react';
import {Composition} from 'remotion';
import {HEIGHT, WIDTH} from './constants';
import {Lyric2TTMLIntro} from './epic/Lyric2TTMLIntro';
import {INTRO_DURATION, INTRO_FPS} from './epic/timing';

/**
 * 注册表。本片只有一个 composition。
 *
 * composition id = `Lyric2TTMLIntro`
 *   1920×1080 / 30fps / 1800 帧（60 秒）/ 十场
 *
 * 单帧自检（比整片渲染快几十倍，调画面时用这个）：
 *   npx remotion still src/index.ts Lyric2TTMLIntro out/check.png --frame=750
 */
export const RemotionRoot: React.FC = () => (
  <Composition
    id="Lyric2TTMLIntro"
    component={Lyric2TTMLIntro}
    durationInFrames={INTRO_DURATION}
    fps={INTRO_FPS}
    width={WIDTH}
    height={HEIGHT}
  />
);
