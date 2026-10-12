/**
 * 场 10 · 尾声（场内 0–254，8.5s）
 *
 * 形式：上一场三根柱子的三色汇聚成中心一点光 → 光点涨起来 → 片名逐字从模糊中锐化浮现
 *      → 两行说明 → 最后一行小字；结束前 30 帧整体轻微收缩、中心留一点余光。
 *
 * 收尾的节奏是刻意"越走越慢"的：所有新元素在**第 208 帧**前全部出场（SPEC 只要求别把
 * 新元素压到 240 帧之后），最后 46 帧只有极缓慢的收缩与一点余晖 —— 这才有"落定"感，
 * 而不是"最后一秒还在塞东西"。
 *
 * 组件内部不设根节点 opacity/进出场：最后一场的淡出由总装
 * （`Lyric2TTMLIntro` 的 `isLast` 分支）负责。
 * 对应 SPEC.md §3「场 10」。task-6。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C, FONT} from '../constants';
import {EASE, TABULAR, glowText, hexA, riseIn, seg, stage} from './shared';

const TITLE = 'Lyric2TTML';
const LINE1 = '把普通歌词，变成逐字时间轴';
const REPO = 'github.com/6hdj/lrc-ttml';
const LINE2 = 'MIT 开源 · 全程离线 · 无需 API Key';
const CX = 960;
const CY = 500;

/** 三色来自上一场的三根柱子，开局就收向中心一点 */
const DOTS = [
  {x: 502, y: 620, color: C.cyan},
  {x: 960, y: 620, color: C.green},
  {x: 1418, y: 620, color: C.violet},
];

export const Finale: React.FC<{frame: number}> = ({frame}) => {
  const f = frame;

  /* —— 一点光：涨一下 → 让位给片名 → 最后再留一点余晖 —— */
  const gw = seg(f, 0, 30, 0, 1, EASE.outCubic);
  const gUp = seg(f, 26, 84, 0, 1, EASE.outCubic);
  const gEnd = seg(f, 206, 240, 0, 0.5, EASE.inOutQuad);
  const glow = gw * (1 - gUp) + 0.45 * gEnd;
  const core = 90 * gw + 26 * gEnd;

  /* —— 收束 —— */
  const shrink = seg(f, 226, 254, 1, 0.982, EASE.inOutQuad);

  const lineO = seg(f, 120, 158, 0, 1);
  const ruleO = seg(f, 130, 156, 0, 1);
  const repoO = seg(f, 148, 180, 0, 1);
  const fineO = seg(f, 180, 208, 0, 1);

  return (
    <AbsoluteFill style={stage(C.bgA)}>
      {/* 环境光：中心一点 */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at 50% 46%, ${hexA(C.cyan, 0.16)} 0%, ${hexA(
            C.cyan,
            0.05,
          )} 26%, transparent 62%)`,
          opacity: 0.5 + 0.5 * glow,
        }}
      />
      {/* 中心余晖：点亮一片，但压得住，不糊字 */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at 50% 46%, rgba(190,240,255,0.42) 0%, ${hexA(
            '#7dd3fc',
            0.14,
          )} 30%, transparent 68%)`,
          opacity: Math.min(1, (glow + 0.35 * gEnd) * 1.1),
          transform: `scale(${gw * (1 - 0.3 * gUp) + 0.55 * gEnd})`,
        }}
      />
      {/* 中心那颗核：收束时很亮，之后收小、压暗，只在文字后面留一点余温 */}
      <div
        style={{
          position: 'absolute',
          left: CX - core,
          top: CY - core,
          width: core * 2,
          height: core * 2,
          borderRadius: '50%',
          background:
            'radial-gradient(circle, rgba(255,255,255,0.55) 0%, rgba(205,245,255,0.4) 26%, rgba(34,211,238,0.12) 58%, rgba(34,211,238,0) 76%)',
          boxShadow: `0 0 ${60 * gw + 40 * gEnd}px ${hexA(C.cyan, 0.6)}`,
          opacity: (1 - 0.5 * gUp + 0.4 * gEnd) * 0.85,
        }}
      />

      {/* 三色收束到一点 */}
      {DOTS.map((d, i) => {
        const t = seg(f, i * 5, 30 + i * 5, 0, 1, EASE.outCubic);
        const x = d.x + (CX - d.x) * t;
        const y = d.y + (CY - d.y) * t;
        const o = gw * 0.9 * (1 - seg(f, 26 + i * 5, 40 + i * 5, 0, 1));
        const r = 18 - 8 * t;
        return (
          <div
            key={d.color}
            style={{
              position: 'absolute',
              left: x - r,
              top: y - r,
              width: r * 2,
              height: r * 2,
              borderRadius: '50%',
              background: `radial-gradient(circle, #ffffff 0%, ${hexA(d.color, 0.85)} 45%, ${hexA(
                d.color,
                0,
              )} 100%)`,
              boxShadow: `0 0 32px ${hexA(d.color, 0.7)}`,
              opacity: o,
            }}
          />
        );
      })}

      {/* ============ 文字块（整体轻微收缩） ============ */}
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
        <div style={{transform: `scale(${shrink})`, textAlign: 'center'}}>
          <div
            style={{
              fontSize: 21,
              letterSpacing: 12,
              color: C.cyan,
              opacity: seg(f, 36, 74, 0, 1),
              marginBottom: 14,
              ...TABULAR,
            }}
          >
            逐字时间轴 · TTML
          </div>

          {/* 片名：逐字从模糊中锐化，每字间隔 8 帧，比场 1 更慢更稳 */}
          <div style={{display: 'flex', justifyContent: 'center', lineHeight: 1.05}}>
            {Array.from(TITLE).map((ch, i) => {
              const d = 52 + i * 8;
              return (
                <span
                  key={`${i}-${ch}`}
                  style={{
                    display: 'inline-block',
                    fontFamily: FONT,
                    fontSize: 132,
                    fontWeight: 800,
                    letterSpacing: 8,
                    color: C.white,
                    opacity: seg(f, d, d + 32, 0, 1, EASE.outCubic),
                    filter: `blur(${seg(f, d, d + 34, 18, 0, EASE.outCubic)}px)`,
                    transform: `translateY(${riseIn(f, d, 32, 18)}px)`,
                    textShadow: glowText(C.cyan, 70, 0.3),
                  }}
                >
                  {ch}
                </span>
              );
            })}
          </div>

          {/* 片名下的细线 */}
          <div
            style={{
              position: 'relative',
              width: 560,
              height: 1.5,
              margin: '24px auto 0',
              background: `linear-gradient(90deg, transparent 0%, ${hexA(C.cyan, 0.9)} 50%, transparent 100%)`,
              opacity: ruleO,
              transform: `scaleX(${seg(f, 130, 162, 0.15, 1, EASE.outCubic)})`,
            }}
          />

          <div
            style={{
              marginTop: 30,
              fontSize: 34,
              letterSpacing: 7,
              color: C.text,
              opacity: lineO,
              transform: `translateY(${riseIn(f, 120, 30, 14)}px)`,
            }}
          >
            {LINE1}
          </div>

          <div
            style={{
              marginTop: 20,
              fontSize: 27,
              letterSpacing: 2,
              color: C.cyanPale,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              opacity: repoO,
              transform: `translateY(${riseIn(f, 148, 30, 12)}px)`,
            }}
          >
            {REPO}
          </div>
        </div>
      </AbsoluteFill>

      {/* 最后一行小字 */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 74,
          textAlign: 'center',
          fontSize: 21,
          letterSpacing: 6,
          color: C.faint,
          opacity: fineO,
          transform: `translateY(${riseIn(f, 180, 28, 10)}px)`,
        }}
      >
        {LINE2}
      </div>
    </AbsoluteFill>
  );
};
