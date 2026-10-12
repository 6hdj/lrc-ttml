/**
 * 场 1 · 序（帧 0–104，3.5s）
 *
 * 形式：纯黑中一点光炸开成星尘，标题逐字从模糊中锐化浮现。
 * 这一场只负责"抓住眼睛"，不堆信息 —— 全片的信息从场 2 才开始。
 *
 * 对应 SPEC.md §3「场 1」。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C, FONT} from '../constants';
import {rnd} from './timing';
import {EASE, appear, blurIn, riseIn, seg, stage} from './shared';

const TITLE = '逐字时间轴';
const SUB = '把普通歌词，做成逐字时间轴';
const PARTICLES = 44;
const CX = 960;
const CY = 512;

export const Overture: React.FC<{frame: number}> = ({frame}) => {
  // 核心光点从零涨起来
  const coreScale = seg(frame, 0, 18, 0, 1, EASE.outCubic);
  // 炸开
  const burst = seg(frame, 22, 66, 0, 1, EASE.outQuad);
  // 标题与副标题
  const titleO = appear(frame, 28, 24);
  const subO = appear(frame, 62, 22);

  return (
    <AbsoluteFill style={stage(C.bgA)}>
      {/* 中心余晖：炸开时整体提亮一下 */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            'radial-gradient(circle at 50% 47%, rgba(140,190,255,0.10) 0%, transparent 58%)',
          opacity: burst * (1 - burst) * 4,
        }}
      />

      {/* 炸开的星尘 */}
      {new Array(PARTICLES).fill(0).map((_, i) => {
        const angle = (i / PARTICLES) * Math.PI * 2 + rnd(i) * 0.5;
        const reach = 300 + rnd(i + 90) * 540;
        const d = burst * reach;
        const x = CX + Math.cos(angle) * d;
        // 纵向压扁 0.62，视觉上更像"在一个平面里炸开"
        const y = CY + Math.sin(angle) * d * 0.62;
        const o = seg(burst, 0, 0.14, 0, 0.85) * (1 - seg(burst, 0.5, 1, 0, 1));
        const size = 1.5 + rnd(i + 7) * 2.5;
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x,
              top: y,
              width: size,
              height: size,
              borderRadius: '50%',
              background: '#cfe2ff',
              opacity: o,
              boxShadow: '0 0 8px rgba(170,205,255,0.9)',
            }}
          />
        );
      })}

      {/* 核心光点 */}
      <div
        style={{
          position: 'absolute',
          left: CX - 200,
          top: CY - 200,
          width: 400,
          height: 400,
          borderRadius: '50%',
          background:
            'radial-gradient(circle, rgba(190,220,255,0.55) 0%, rgba(120,170,255,0.12) 34%, transparent 68%)',
          transform: `scale(${coreScale})`,
        }}
      />

      {/* 标题：逐字从模糊中锐化 */}
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
        <div
          style={{
            display: 'flex',
            marginTop: -40,
            opacity: titleO,
            transform: `translateY(${riseIn(frame, 28, 24, 26)}px)`,
          }}
        >
          {Array.from(TITLE).map((ch, i) => {
            const d = 28 + i * 4;
            return (
              <span
                key={`${i}-${ch}`}
                style={{
                  display: 'inline-block',
                  fontFamily: FONT,
                  fontSize: 116,
                  fontWeight: 800,
                  color: C.white,
                  letterSpacing: 10,
                  opacity: appear(frame, d, 20),
                  filter: `blur(${blurIn(frame, d, 20, 16)}px)`,
                  textShadow: '0 0 60px rgba(130,180,255,0.5)',
                }}
              >
                {ch}
              </span>
            );
          })}
        </div>

        {/* 副标题 */}
        <div
          style={{
            marginTop: 46,
            fontFamily: FONT,
            fontSize: 30,
            letterSpacing: 8,
            color: C.sub,
            opacity: subO,
            transform: `translateY(${riseIn(frame, 62, 22, 18)}px)`,
          }}
        >
          {SUB}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
