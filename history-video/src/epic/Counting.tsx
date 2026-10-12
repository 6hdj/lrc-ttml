/**
 * 场 2 · 打轴（场内 0–239，8.0s）
 *
 * 形式：巨型计时器在跑（tabular-nums + 2 帧残影），一条横向标尺随计时从左扫过；
 *       计时器经过某个字的拍点时，那个字在标尺上"落下"一个青色方块 —— 这就是"逐字"的可视化。
 *
 * 帧设计（动画在 222 帧内做完，最后 12 帧留给总装叠化）：
 *   6–146    第一遍：0 → 62.000（10 个字依次落点，读数从 0/10 数到 10/10）
 *   150–186  回卷：62.000 → 0（体现"反复打轴"）
 *   190–222  第二遍：0 → 62.000 再扫一遍（方块留在原处，表示对齐结果不随回卷丢失）
 *
 * 说明：方块一旦落下就**不再消失**。打轴打出来的时间戳是一次对齐的产物，
 * 回卷时把它抹掉会让人以为"结果没了"；保留着才能读出「10 个字 = 10 个时间戳」。
 *
 * 对应 SPEC.md §3「场 2」。所有数值都是帧的纯函数（无 Math.random / Date.now）。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../constants';
import {
  BG,
  Caption,
  CornerLabel,
  EASE,
  Readout,
  TABULAR,
  appear,
  blurIn,
  glowText,
  hexA,
  riseIn,
  rollNum,
  seg,
  stage,
} from './shared';

/* ------------------------------------------------------------------ *
 * 数据与几何
 * ------------------------------------------------------------------ */

/** 十个字 —— 每个字在标尺上有一个拍点 */
const CHARS = Array.from('把歌词做成逐字时间轴');
const T_START = 3.2; // 第一个字的拍点（秒）
const T_STEP = 6.4; // 相邻两字间隔（秒）→ 3.2 + 6.4 × 9 = 60.8
const T_MAX = 62; // 计时器的量程

/* 计时器的三段（场内帧） */
const PASS1_A = 6;
const PASS1_B = 146;
const HOLD1_END = 150;
const REWIND_B = 186;
const HOLD2_END = 190;
const PASS2_B = 222;

/** 计时器是匀速走的（真实的打轴读数就该是线性的，不能用缓动） */
const LINEAR = (t: number): number => t;

/** 场上第 f 帧的计时器读数（秒） */
const timerAt = (f: number): number => {
  if (f <= PASS1_A) {
    return 0;
  }
  if (f <= PASS1_B) {
    return rollNum(f, PASS1_A, PASS1_B, 0, T_MAX, LINEAR);
  }
  if (f <= HOLD1_END) {
    return T_MAX;
  }
  if (f <= REWIND_B) {
    return rollNum(f, HOLD1_END, REWIND_B, T_MAX, 0, LINEAR);
  }
  if (f <= HOLD2_END) {
    return 0;
  }
  return rollNum(f, HOLD2_END, PASS2_B, 0, T_MAX, EASE.outQuad);
};

/** 第 i 个字"落下"的那一帧（= 计时器第一次走过它的拍点） */
const stampAt = (i: number): number => {
  const thr = T_START + i * T_STEP;
  for (let f = 0; f <= 240; f++) {
    if (timerAt(f) >= thr) {
      return f;
    }
  }
  return 240;
};

const pad = (n: number, w: number): string => String(Math.floor(n)).padStart(w, '0');

/** 62.000 → `01:02.000`（SPEC 要的 `00:12.480` 同款） */
const clock = (t: number): string => {
  const v = Math.max(0, t);
  const whole = Math.floor(v);
  return `${pad(whole / 60, 2)}:${pad(whole % 60, 2)}.${pad(Math.round((v - whole) * 1000), 3)}`;
};

/** 刻度标签：5 → `00:05` */
const clockShort = (s: number): string => `${pad(s / 60, 2)}:${pad(s % 60, 2)}`;

const RX = 150; // 标尺左端
const RW = 1620; // 标尺宽度
const RY = 700; // 标尺基线 y
const CHAR_Y = 486; // 字块顶部 y

const tX = (t: number): number => RX + (Math.min(Math.max(t, 0), T_MAX) / T_MAX) * RW;
const TICKS = Array.from({length: T_MAX + 1}, (_v, s) => s);

/* ------------------------------------------------------------------ *
 * 场景
 * ------------------------------------------------------------------ */

export const Counting: React.FC<{frame: number}> = ({frame}) => {
  const t = timerAt(frame);
  const ghost1 = timerAt(frame - 1);
  const ghost2 = timerAt(frame - 2);
  const headX = tX(t);
  const swept = Math.min(Math.max(t / T_MAX, 0), 1);
  const stamped = CHARS.reduce((n, _c, i) => (frame >= stampAt(i) ? n + 1 : n), 0);

  const uiIn = appear(frame, 4, 22);
  const rulerIn = appear(frame, 10, 26);

  return (
    <AbsoluteFill style={stage(C.bgB, BG.cyanSoft)}>
      <CornerLabel frame={frame} title="打轴" sub="每一个字都有自己的时间" />

      <Readout frame={frame} rows={[`对齐单元 ${stamped} / 10`, '状态：DP 强制对齐']} />

      {/* ---------------- 巨型计时器（主数字 + 前两帧残影） ---------------- */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: 196,
          textAlign: 'center',
          opacity: uiIn,
          filter: `blur(${blurIn(frame, 4, 22, 14)}px)`,
        }}
      >
        <div
          style={{
            fontSize: 19,
            letterSpacing: 10,
            color: C.dim,
            transform: `translateY(${riseIn(frame, 6, 24, 12)}px)`,
          }}
        >
          逐字时间轴 · 正在对齐
        </div>

        <div style={{position: 'relative', height: 212, marginTop: 4}}>
          {[{v: ghost2, o: 0.08}, {v: ghost1, o: 0.14}].map((g, k) => (
            <div
              key={`g${k}`}
              style={{
                position: 'absolute',
                inset: 0,
                fontSize: 190,
                fontWeight: 900,
                lineHeight: '1.05',
                letterSpacing: 6,
                color: C.cyan,
                opacity: g.o,
                filter: 'blur(6px)',
                ...TABULAR,
              }}
            >
              {clock(g.v)}
            </div>
          ))}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              fontSize: 190,
              fontWeight: 900,
              lineHeight: '1.05',
              letterSpacing: 6,
              color: C.white,
              textShadow: glowText(C.cyan, 90, 0.4),
              ...TABULAR,
            }}
          >
            {clock(t)}
          </div>
        </div>

        {/* 量程进度：跑了多少，再压一条线出来 */}
        <div
          style={{
            margin: '6px auto 0',
            width: 820,
            height: 2,
            background: 'rgba(255,255,255,0.08)',
            borderRadius: 2,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${swept * 100}%`,
              height: '100%',
              background: hexA(C.cyan, 0.8),
              boxShadow: `0 0 12px ${hexA(C.cyan, 0.6)}`,
            }}
          />
        </div>
      </div>

      {/* ---------------- 逐字落下：字 → 细线 → 标尺上的方块 ---------------- */}
      {CHARS.map((ch, i) => {
        const sf = stampAt(i);
        const p = seg(frame, sf, sf + 12, 0, 1, EASE.outCubic);
        const on = frame >= sf;
        const x = tX(T_START + i * T_STEP);
        return (
          <React.Fragment key={`c${i}`}>
            <div
              style={{
                position: 'absolute',
                left: x,
                top: CHAR_Y,
                transform: `translate(-50%, ${(1 - p) * -22}px)`,
                fontSize: 58,
                fontWeight: 800,
                letterSpacing: 2,
                color: on ? C.cyanPale : C.faint,
                opacity: on ? 0.5 + 0.5 * p : 0.42,
                filter: `blur(${on ? (1 - p) * 7 : 3}px)`,
                textShadow: on ? glowText(C.cyan, 44, 0.5) : undefined,
              }}
            >
              {ch}
            </div>
            <div
              style={{
                position: 'absolute',
                left: x,
                top: CHAR_Y + 78,
                width: 1,
                height: Math.max(0, RY - 26 - (CHAR_Y + 78)) * p,
                background: hexA(C.cyan, 0.4 * p),
              }}
            />
            <div
              style={{
                position: 'absolute',
                left: x - 10,
                top: RY - 10,
                width: 20,
                height: 20,
                borderRadius: 5,
                background: hexA(C.cyan, 0.9 * p),
                boxShadow: `0 0 ${22 * p}px ${hexA(C.cyan, 0.75 * p)}`,
                transform: `scale(${0.15 + 0.85 * p})`,
              }}
            />
          </React.Fragment>
        );
      })}

      {/* ---------------- 标尺：基线 + 刻度 + 扫过的光 + 游标 ---------------- */}
      <div style={{position: 'absolute', inset: 0, opacity: rulerIn}}>
        <div
          style={{
            position: 'absolute',
            left: RX,
            top: RY,
            width: RW,
            height: 1,
            background: `linear-gradient(90deg, ${hexA(C.cyan, 0.55)} 0%, ${hexA(
              C.cyan,
              0.55,
            )} ${swept * 100}%, rgba(255,255,255,0.10) ${swept * 100}%, rgba(255,255,255,0.10) 100%)`,
          }}
        />

        {TICKS.map((s) => {
          const on = s <= t + 0.02;
          const long = s % 5 === 0;
          const h = (long ? 30 : 15) * rulerIn;
          return (
            <div
              key={`t${s}`}
              style={{
                position: 'absolute',
                left: tX(s),
                top: RY - h,
                width: 1,
                height: h,
                background: on
                  ? long
                    ? hexA(C.cyan, 0.85)
                    : 'rgba(255,255,255,0.42)'
                  : long
                    ? C.axis
                    : C.line,
              }}
            />
          );
        })}

        {TICKS.filter((s) => s % 10 === 0).map((s) => (
          <div
            key={`tl${s}`}
            style={{
              position: 'absolute',
              left: tX(s),
              top: RY + 14,
              transform: 'translateX(-50%)',
              fontSize: 15,
              letterSpacing: 1,
              color: s <= t + 0.02 ? C.dim : C.faint,
              ...TABULAR,
            }}
          >
            {clockShort(s)}
          </div>
        ))}

        {/* 扫过的光柱 + 游标 */}
        <div
          style={{
            position: 'absolute',
            left: headX - 30,
            top: RY - 168,
            width: 60,
            height: 150,
            background: `linear-gradient(180deg, ${hexA(C.cyan, 0)} 0%, ${hexA(
              C.cyan,
              0.14,
            )} 62%, ${hexA(C.cyan, 0)} 100%)`,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: headX - 1,
            top: RY - 108,
            width: 2,
            height: 168,
            background: hexA(C.cyan, 0.95),
            boxShadow: `0 0 18px ${hexA(C.cyan, 0.8)}`,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: headX - 7,
            top: RY + 50,
            width: 14,
            height: 14,
            transform: 'rotate(45deg)',
            background: hexA(C.cyan, 0.9),
            boxShadow: `0 0 16px ${hexA(C.cyan, 0.8)}`,
          }}
        />
      </div>

      {/* ---------------- 底部：逐字时间戳分段条 ---------------- */}
      <div
        style={{
          position: 'absolute',
          left: RX,
          top: 806,
          width: RW,
          display: 'flex',
          gap: 10,
          opacity: uiIn,
        }}
      >
        {CHARS.map((_c, i) => {
          const on = frame >= stampAt(i);
          return (
            <div
              key={`s${i}`}
              style={{
                flex: 1,
                height: 6,
                borderRadius: 3,
                background: on ? hexA(C.cyan, 0.7) : 'rgba(255,255,255,0.07)',
                boxShadow: on ? `0 0 14px ${hexA(C.cyan, 0.35)}` : undefined,
              }}
            />
          );
        })}
      </div>
      <div
        style={{
          position: 'absolute',
          left: RX,
          top: 826,
          fontSize: 16,
          letterSpacing: 3,
          color: C.faint,
          opacity: uiIn,
        }}
      >
        逐字时间戳
      </div>
      <div
        style={{
          position: 'absolute',
          left: RX + RW - 160,
          width: 160,
          top: 826,
          textAlign: 'right',
          fontSize: 16,
          letterSpacing: 2,
          color: C.dim,
          opacity: uiIn,
          ...TABULAR,
        }}
      >
        {stamped} / 10 字
      </div>

      <Caption frame={frame} text="10 个字 = 10 段独立时间 · 不是按字数平均切分" at={44} />
    </AbsoluteFill>
  );
};
