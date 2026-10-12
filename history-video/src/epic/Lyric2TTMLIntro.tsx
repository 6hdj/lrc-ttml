/**
 * 总装：按 `timing.SCENES` 的顺序挂载十场，统一处理叠化。
 *
 * 叠化规则（学自参考片，别改成"上场自己淡出下场上场"）：
 * * 下一场**淡上来盖住**上一场，所以上一场在自己结束后还要多挂 `OVER` 帧；
 * * 退场那一场要**同时 blur(18px) + scale 1.08** —— 十场里大半是居中大字，
 *   单纯交叉淡化会让两段字叠成一团糊（「Lyric2TTML」压在「00:12.480」上那种）。
 *   模糊掉之后它退成一层背景雾，观众看到的是新画面浮上来，而不是两段字打架；
 * * 最后一场自己淡出（后面没有下一场了）。
 */
import React from 'react';
import {AbsoluteFill, Audio, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {clamp} from '../constants';
import {BGM, INTRO_DURATION, OVER, SCENES} from './timing';

import {Overture} from './Overture';
import {Counting} from './Counting';
import {Waveform} from './Waveform';
import {LineVsChar} from './LineVsChar';
import {Structure} from './Structure';
import {Duet} from './Duet';
import {Background} from './Background';
import {Editor} from './Editor';
import {Numbers} from './Numbers';
import {Finale} from './Finale';

/** 场景组件的统一签名：`frame` 是**本场第几帧**（不是全局帧号） */
export type Body = React.FC<{frame: number}>;

const BODIES: Record<string, Body> = {
  overture: Overture,
  counting: Counting,
  waveform: Waveform,
  linevschar: LineVsChar,
  structure: Structure,
  duet: Duet,
  background: Background,
  editor: Editor,
  numbers: Numbers,
  finale: Finale,
};

export const Lyric2TTMLIntro: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill style={{backgroundColor: '#03040a', overflow: 'hidden'}}>
      {/* 配乐：文件放 public/，在 timing.ts 里改 BGM；留 null 就是静音渲染 */}
      {BGM ? <Audio src={staticFile(BGM)} /> : null}

      {SCENES.map((s, i) => {
        const next = SCENES[i + 1];
        const stayUntil = next ? next.from + OVER : INTRO_DURATION;
        if (frame < s.from || frame > stayUntil) {
          return null;
        }
        const Body = BODIES[s.id];
        if (!Body) {
          return null;
        }

        const isLast = i === SCENES.length - 1;

        // 自己进场：0 → 1
        const enter = interpolate(frame, [s.from, s.from + OVER], [0, 1], clamp);
        // 退场：从下一场开始的地方算起；**最后一场提前 3 帧淡完**
        // （原来写到 INTRO_DURATION-2，等于最后一帧才淡到 0，末帧是全黑 —— 实跑踩过）
        const exit = next
          ? interpolate(frame, [next.from, next.from + OVER], [0, 1], clamp)
          : interpolate(frame, [INTRO_DURATION - OVER, INTRO_DURATION - 3], [0, 1], clamp);

        // 非最后一场不自己淡出 —— 让下一场盖住它，叠化才是等亮度交替
        const opacity = isLast ? enter * (1 - exit) : enter;
        const scale = 1 - (1 - enter) * 0.04 + exit * 0.08;
        const blur = exit * 18;

        return (
          <AbsoluteFill
            key={s.id}
            style={{
              opacity,
              transform: `scale(${scale})`,
              filter: blur > 0.05 ? `blur(${blur}px)` : undefined,
            }}
          >
            <Body frame={frame - s.from} />
          </AbsoluteFill>
        );
      })}
    </AbsoluteFill>
  );
};
