/**
 * 场 7 · 和声（场内 0–149，5.0s）
 *
 * 形式：一行**主唱轨**（青，0 帧起从左画出）与一行**和声轨**（绿，晚 20 帧起、
 * 只覆盖后半段）错位叠唱；两轨之间细线相连；和声出现时主唱轨对应位置套一个
 * **嵌套**的绿框 —— 表示 `x-bg` 嵌在主唱 `<p>` **内部**。
 * 尾部用红叉闪一下错误示范「单独成行 = 播放器看不见」（不超过 2 秒，SPEC 要求）。
 *
 * 内容正确性（这是本仓库踩过的坑，别写反）：
 * 背景和声**必须是主唱行的子内容** `<span ttm:role="x-bg">`；单独成一行 `<p>`，
 * AMLL 解析后那一行文本为空 —— 播放器里什么都看不见。
 *
 * 对应 SPEC.md §3「场 7」。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../constants';
import {
  Caption,
  CornerLabel,
  EASE,
  Readout,
  TABULAR,
  blurIn,
  glowText,
  hexA,
  riseIn,
  seg,
  stage,
} from './shared';

const MONO = 'Consolas, "Cascadia Mono", "DejaVu Sans Mono", monospace';

/** 时间轴（横向 = 时间，从左到右） */
const X0 = 320;
const X1 = 1538;
/** 和声只覆盖后半段：从 62% 处开始 */
const BG_X0 = X0 + Math.round((X1 - X0) * 0.62);

const nSung = (frame: number, t0: number, t1: number, parts: number): number =>
  Math.floor(seg(frame, t0, t1, 0, parts + 0.999, EASE.outQuad));

/** 主唱轨的字块（10 个） */
const MAIN = {x: X0, y: 398, h: 42, gap: 6, parts: 10, at: 8};
/** 和声轨的字块（5 个：只覆盖后半段） */
const BG = {x: BG_X0, y: 508, h: 44, gap: 8, parts: 5, at: 30};

export const Background: React.FC<{frame: number}> = ({frame}) => {
  const mainN = Math.min(MAIN.parts, nSung(frame, MAIN.at, 44, MAIN.parts));
  const bgN = Math.min(BG.parts, nSung(frame, BG.at, 78, BG.parts));
  const bgOn = seg(frame, 30, 56, 0, 1, EASE.outCubic);
  // 嵌套绿框：把和声那一小段**圈在主唱轨里面**
  const nest = seg(frame, 66, 88, 0, 1, EASE.outCubic);
  const nestW = (X1 - BG_X0) * nest;
  // 错误示范：64 → 122（1.93s ≤ 2s）。64 起就在画，76 帧完全成型 ——
  // 自检帧 1080（场内 75）正好落在它完全可见的时候。
  const errT = seg(frame, 64, 80, 0, 1, EASE.outCubic);
  const errO = seg(frame, 64, 76, 0, 1) * (1 - seg(frame, 116, 123, 0, 1));
  // ⚠️ 播放头本身是"时间在走"的示意：本场**没有音频**，下方 `t = x.xxs` 读数是把
  //    播放头进度线性映射成 0–5s 的**示意值**，不是任何真实音轨的时间轴，别拿它当对齐依据。
  const playhead = seg(frame, 20, 136, 0, 1, EASE.outQuad);

  return (
    <AbsoluteFill style={stage(C.bgB, undefined)}>
      {/* 环境光：左青（主唱）右绿（和声） */}
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(50% 46% at 34% 40%, rgba(34,211,238,0.09) 0%, transparent 70%),' +
            'radial-gradient(46% 42% at 74% 62%, rgba(163,230,53,0.09) 0%, transparent 72%)',
        }}
      />

      <CornerLabel frame={frame} title="和声" sub="x-bg 是主唱行的子内容" />
      <Readout
        frame={frame}
        at={14}
        rows={['x-bg ⊆ p（一定在 p 内部）', '单独成行 ⇒ 解析为空行', '规范：AMLL §7.3']}
        top={78}
        right={104}
      />

      {/* 一句话把结论说在前面 */}
      <div
        style={{
          position: 'absolute',
          left: 96,
          top: 318,
          fontSize: 34,
          fontWeight: 700,
          color: C.text,
          letterSpacing: 3,
          opacity: seg(frame, 16, 40, 0, 1, EASE.outCubic),
          transform: `translateY(${riseIn(frame, 16, 26, 14)}px)`,
          filter: `blur(${blurIn(frame, 16, 24, 10)}px)`,
        }}
      >
        和声不是单独一行 —— 它是主唱行的<span style={{color: C.green}}>子内容</span>
      </div>

      {/* 主唱轨：青色，从 0 帧起从左画 */}
      <div
        style={{
          position: 'absolute',
          left: MAIN.x - 18,
          top: MAIN.y - 22,
          fontFamily: MONO,
          fontSize: 15,
          letterSpacing: 2,
          color: hexA(C.cyan, 0.85),
          opacity: seg(frame, 8, 26, 0, 1),
        }}
      >
        &lt;p ttm:agent="v1"&gt; 主唱
      </div>
      {new Array(MAIN.parts).fill(0).map((_, i) => {
        const w = (X1 - MAIN.x) / MAIN.parts - MAIN.gap;
        const on = i < mainN;
        return (
          <div
            key={`m${i}`}
            style={{
              position: 'absolute',
              left: MAIN.x + i * (w + MAIN.gap),
              top: MAIN.y,
              width: w,
              height: MAIN.h,
              borderRadius: 8,
              border: `1px solid ${hexA(C.cyan, on ? 0.85 : 0.18)}`,
              background: hexA(C.cyan, on ? 0.24 : 0.05),
              boxShadow: on ? `0 0 26px ${hexA(C.cyan, 0.34)}` : undefined,
              opacity: on ? 1 : 0.5,
              transform: `scaleX(${on ? 1 : 0.55})`,
              transformOrigin: 'left center',
            }}
          />
        );
      })}

      {/* 和声轨：绿色，晚 20 帧起，只覆盖后半段 */}
      <div
        style={{
          position: 'absolute',
          left: BG.x - 18,
          top: BG.y - 24,
          fontFamily: MONO,
          fontSize: 15,
          letterSpacing: 2,
          color: hexA(C.green, 0.9),
          opacity: bgOn,
        }}
      >
        &lt;span ttm:role="x-bg"&gt; 和声（整段嵌在上面那一行里）
      </div>
      {new Array(BG.parts).fill(0).map((_, i) => {
        const w = (X1 - BG.x) / BG.parts - BG.gap;
        const on = i < bgN;
        return (
          <div
            key={`b${i}`}
            style={{
              position: 'absolute',
              left: BG.x + i * (w + BG.gap),
              top: BG.y,
              width: w,
              height: BG.h,
              borderRadius: 8,
              border: `1px solid ${hexA(C.green, on ? 0.9 : 0.18)}`,
              background: hexA(C.green, on ? 0.26 : 0.05),
              boxShadow: on ? `0 0 30px ${hexA(C.green, 0.34)}` : undefined,
              opacity: bgOn * (on ? 1 : 0.45),
              transform: `scaleX(${on ? 1 : 0.5})`,
              transformOrigin: 'left center',
            }}
          />
        );
      })}

      {/* 两轨之间的细线：和声的起止都"挂"在主唱轨上 */}
      <div
        style={{
          position: 'absolute',
          left: 880,
          top: MAIN.y + MAIN.h,
          width: 1,
          height: BG.y - (MAIN.y + MAIN.h),
          background: `linear-gradient(180deg, ${hexA(C.cyan, 0.5)} 0%, ${hexA(C.green, 0.75)} 100%)`,
          opacity: bgOn * 0.9,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: BG.x + 70,
          top: MAIN.y + MAIN.h,
          width: 1,
          height: BG.y - (MAIN.y + MAIN.h),
          background: `linear-gradient(180deg, ${hexA(C.cyan, 0.45)} 0%, ${hexA(C.green, 0.7)} 100%)`,
          opacity: bgOn * 0.8,
        }}
      />
      {/* 时间轴基线 */}
      <div
        style={{
          position: 'absolute',
          left: X0,
          top: MAIN.y + MAIN.h + 8,
          width: X1 - X0,
          height: 1,
          background: 'rgba(255,255,255,0.10)',
        }}
      />

      {/* 嵌套绿框：在主唱轨内部圈出 x-bg 覆盖的那一段 */}
      <div
        style={{
          position: 'absolute',
          left: BG_X0 - 6,
          top: MAIN.y - 8,
          width: nestW + 12,
          height: MAIN.h + 16,
          borderRadius: 12,
          border: `2px solid ${hexA(C.green, 0.85)}`,
          boxShadow: `0 0 34px ${hexA(C.green, 0.3)}`,
          opacity: nest,
        }}
      >
        <div
          style={{
            position: 'absolute',
            right: 10,
            top: 8,
            padding: '2px 10px',
            borderRadius: 6,
            background: hexA(C.green, 0.92),
            color: '#0b1206',
            fontFamily: MONO,
            fontSize: 15,
            fontWeight: 700,
            letterSpacing: 1,
            whiteSpace: 'nowrap',
          }}
        >
          x-bg 嵌在这个 p 里
        </div>
      </div>
      {/* 从嵌套框往和声轨接一条虚线，说明"框里就是这条绿轨" */}
      <div
        style={{
          position: 'absolute',
          left: BG_X0,
          top: MAIN.y + MAIN.h + 6,
          width: X1 - BG_X0,
          height: 0,
          borderTop: `1px dashed ${hexA(C.green, 0.55)}`,
          opacity: nest * 0.9,
        }}
      />

      {/* 播放头：横扫一遍，表示"这两条轨是同时唱的" */}
      <div
        style={{
          position: 'absolute',
          left: X0 + (X1 - X0) * playhead,
          top: MAIN.y - 28,
          width: 2,
          height: BG.y + BG.h + 10 - (MAIN.y - 28),
          background: `linear-gradient(180deg, ${hexA(C.cyanPale, 0.0)} 0%, ${hexA(
            C.cyanPale,
            0.75,
          )} 22%, ${hexA(C.green, 0.75)} 86%, ${hexA(C.green, 0.0)} 100%)`,
          opacity: seg(frame, 16, 30, 0, 1) * (1 - seg(frame, 136, 148, 0, 1) * 0.6),
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: X0 + (X1 - X0) * playhead - 60,
          top: MAIN.y - 62,
          width: 120,
          textAlign: 'center',
          fontFamily: MONO,
          fontSize: 16,
          letterSpacing: 1,
          color: C.cyanPale,
          opacity: seg(frame, 16, 30, 0, 1) * 0.9,
          ...TABULAR,
        }}
      >
        {`t = ${(playhead * 5).toFixed(2)}s`}
      </div>

      {/* 错误示范：单独成行 = 播放器看不见（1.93s 内闪完） */}
      <div
        style={{
          position: 'absolute',
          left: 640,
          top: 750,
          width: 640,
          height: 176,
          borderRadius: 16,
          border: `2px solid rgba(248,113,113,${0.85 * errT})`,
          background: `linear-gradient(180deg, rgba(248,113,113,${0.12 * errT}) 0%, rgba(248,113,113,0.02) 100%)`,
          boxShadow: `0 0 ${50 * errT}px rgba(248,113,113,${0.3 * errT})`,
          opacity: errO,
          transform: `translateY(${riseIn(frame, 64, 24, 16)}px) scale(${seg(
            frame,
            64,
            84,
            0.96,
            1,
            EASE.outCubic,
          )})`,
        }}
      >
        <div
          style={{
            position: 'absolute',
            right: 14,
            top: 12,
            fontFamily: MONO,
            fontSize: 15,
            letterSpacing: 3,
            color: 'rgba(252,165,165,0.95)',
          }}
        >
          错误示范
        </div>
        {/* 红叉 */}
        <div
          style={{
            position: 'absolute',
            left: 34,
            top: 44,
            width: 78,
            height: 78,
            opacity: errT,
            transform: `rotate(${seg(frame, 64, 80, -24, 0, EASE.outCubic)}deg) scale(${seg(
              frame,
              64,
              80,
              0.4,
              1,
              EASE.outCubic,
            )})`,
          }}
        >
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: 36,
              width: 78,
              height: 7,
              borderRadius: 4,
              background: '#f87171',
              boxShadow: '0 0 22px rgba(248,113,113,0.9)',
              transform: 'rotate(45deg)',
            }}
          />
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: 36,
              width: 78,
              height: 7,
              borderRadius: 4,
              background: '#f87171',
              boxShadow: '0 0 22px rgba(248,113,113,0.9)',
              transform: 'rotate(-45deg)',
            }}
          />
        </div>
        <div style={{position: 'absolute', left: 150, top: 40, width: 470}}>
          <div
            style={{
              fontSize: 30,
              fontWeight: 800,
              color: '#fca5a5',
              letterSpacing: 2,
              textShadow: glowText('#f87171', 32, 0.4),
            }}
          >
            单独成行 = 播放器看不见
          </div>
          <div
            style={{
              marginTop: 12,
              fontFamily: MONO,
              fontSize: 17,
              letterSpacing: 1,
              color: C.dim,
              whiteSpace: 'nowrap',
            }}
          >
            {'<p> <span ttm:role="x-bg">…</span> </p>'} ← 整行只有和声
          </div>
          <div
            style={{
              marginTop: 8,
              fontSize: 19,
              letterSpacing: 2,
              color: '#fca5a5',
            }}
          >
            AMLL 解析后这一行是空的，一个字都不显示
          </div>
        </div>
      </div>

      <Caption
        frame={frame}
        text="正确写法：主唱 span 与 <span ttm:role='x-bg'> 同级、都在同一个 <p> 里面"
        at={20}
        bottom={56}
      />
    </AbsoluteFill>
  );
};
