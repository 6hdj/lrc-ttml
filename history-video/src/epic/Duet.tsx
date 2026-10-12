/**
 * 场 6 · 对唱（场内 0–179，6.0s）
 *
 * 形式：中间散着一片**声纹点阵**（36 个点，`rnd()` 定位，统一灰色）；
 * 约 60 帧处聚成两簇（左青 / 右紫），中间出现分界竖线；
 * 两簇成型后各自拉出一个横条写 `v1` / `v2`，模拟 Apple Music 的左右分栏。
 *
 * ⚠️ 诚实性：读数里的 **85%** 是仓库里 `tools/eval_diarization.py` 与 AMLL DB
 * 人工真值的实测一致率（90.6% 那一版是 29/32 行的旧口径，别改成好看的数字）。
 *
 * 对应 SPEC.md §3「场 6」。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../constants';
import {rnd} from './timing';
import {
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
  seg,
  stage,
} from './shared';

const MONO = 'Consolas, "Cascadia Mono", "DejaVu Sans Mono", monospace';

const N = 36; // 36 个点 = 36 行歌词的声纹
const CX = 960;
const CY = 452;
const SX = 350; // 散开时的横向半径
const SY = 150; // 纵向压扁，视觉上更像"在一个平面里聚"

/** 两簇的中心：左青 / 右紫 */
const CL = {x: 736, y: 444};
const CL_R = {x: 1184, y: 452};

type P = {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  z: number;
  r: number;
  side: 0 | 1;
  lag: number;
  dx: number;
  dy: number;
  ph: number;
};

/** 帧纯函数：36 个点的位置全部来自 `rnd()`，同一帧永远一样 */
const POINTS: P[] = new Array(N).fill(0).map((_, i) => {
  const ang = rnd(i * 3 + 1) * Math.PI * 2;
  const rad = 0.28 + rnd(i * 3 + 2) * 0.72;
  const x0 = CX + Math.cos(ang) * rad * SX;
  const y0 = CY + Math.sin(ang) * rad * SY;
  const side: 0 | 1 = i % 2 === 0 ? 0 : 1;
  const cc = side === 0 ? CL : CL_R;
  const a2 = rnd(i * 5 + 11) * Math.PI * 2;
  const rr = Math.sqrt(rnd(i * 5 + 12)) * 140;
  return {
    x0,
    y0,
    x1: cc.x + Math.cos(a2) * rr,
    y1: cc.y + Math.sin(a2) * rr * 0.68,
    z: rnd(i * 7 + 21) * 2 - 1,
    r: 3 + rnd(i * 7 + 22) * 4.2,
    side,
    lag: 6 + rnd(i * 11 + 31) * 26,
    dx: 3 + rnd(i * 11 + 32) * 3,
    dy: 2 + rnd(i * 13 + 33) * 3,
    ph: rnd(i * 17 + 41) * Math.PI * 2,
  };
});

/** 两条分栏横条 */
const BARS = [
  {side: 0 as const, label: 'v1', color: C.cyan, from: 920, to: 250, at: 108},
  {side: 1 as const, label: 'v2', color: C.violet, from: 1000, to: 1672, at: 118},
];

export const Duet: React.FC<{frame: number}> = ({frame}) => {
  // 散点淡入 → 聚类进度
  const scatterO = appear(frame, 6, 20);
  const cluster = seg(frame, 60, 112, 0, 1, EASE.outCubic);
  const dim = seg(frame, 112, 140, 1, 0.42);
  const divider = seg(frame, 72, 104, 0, 1, EASE.outCubic);
  const barsO = seg(frame, 106, 126, 0, 1, EASE.outCubic);
  // 聚类的"吸引力"提示：光晕在 60–90 帧才涨出来，避免"一上来就已经分好"了
  const haloO = seg(frame, 58, 96, 0, 1, EASE.outCubic);

  return (
    <AbsoluteFill style={stage(C.bgB, undefined)}>
      {/* 双色环境光：左边青、右边紫 —— 与两簇同色，画面才有"分栏"的预感 */}
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(46% 52% at 30% 44%, rgba(34,211,238,0.10) 0%, transparent 70%),' +
            'radial-gradient(46% 52% at 72% 46%, rgba(167,139,250,0.11) 0%, transparent 70%)',
          opacity: seg(frame, 24, 70, 0.35, 1),
        }}
      />

      <CornerLabel frame={frame} title="对唱" sub="不同 ttm:agent = 左右分栏" />
      <Readout
        frame={frame}
        at={12}
        rows={['CAM++ 声纹 · k-means', '与人工标注一致率 85%', '36 行 → v1 18 / v2 18']}
        top={78}
        right={110}
      />

      {/* 两簇的光晕 */}
      <div
        style={{
          position: 'absolute',
          left: CL.x - 210,
          top: CL.y - 144,
          width: 420,
          height: 288,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${hexA(C.cyan, 0.26)} 0%, transparent 68%)`,
          opacity: haloO,
          filter: 'blur(4px)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: CL_R.x - 210,
          top: CL_R.y - 144,
          width: 420,
          height: 288,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${hexA(C.violet, 0.24)} 0%, transparent 68%)`,
          opacity: haloO,
          filter: 'blur(4px)',
        }}
      />

      {/* 分界竖线：两簇成型时从中间长出来 */}
      <div
        style={{
          position: 'absolute',
          left: 958,
          top: 450 - 176 * divider,
          width: 3,
          height: 352 * divider,
          borderRadius: 2,
          background:
            'linear-gradient(180deg, transparent 0%, rgba(255,255,255,0.42) 16%, rgba(255,255,255,0.42) 84%, transparent 100%)',
          opacity: divider,
          boxShadow: `0 0 18px rgba(255,255,255,${0.18 * divider})`,
        }}
      />

      {/* 36 个声纹点：先散、后聚 */}
      {POINTS.map((p, i) => {
        const q = seg(frame, 60 + p.lag, 108 + p.lag, 0, 1, EASE.outCubic);
        const x = p.x0 + (p.x1 - p.x0) * q;
        const y = p.y0 + (p.y1 - p.y0) * q;
        const breath = q * 2.4;
        const isCyan = p.side === 0;
        const color = isCyan ? C.cyan : C.violet;
        const col = seg(frame, 82 + p.lag * 0.5, 104 + p.lag * 0.5, 0, 1, EASE.outCubic);
        const near = 1 - p.z * 0.3;
        const size = (p.r * 2 * near + q * 3) * (0.82 + 0.18 * cluster);
        // 灰 → 本簇颜色：簇内点用**本簇色**，不会被中点混成第三种颜色
        const to = isCyan ? [34, 211, 238] : [167, 139, 250];
        const mix = (ch: number): number => Math.round(255 + (ch - 255) * col);
        return (
          <div key={i}>
            {/* 聚好之后的彩色光晕（散着的时候没有） */}
            <div
              style={{
                position: 'absolute',
                left: x - size * 2.2,
                top: y - size * 2.2,
                width: size * 4.4,
                height: size * 4.4,
                borderRadius: '50%',
                background: `radial-gradient(circle, ${hexA(color, 0.85 * col)} 0%, transparent 60%)`,
                opacity: col * 0.5,
              }}
            />
            <div
              style={{
                position: 'absolute',
                left: x + Math.sin(frame * 0.05 + p.ph) * p.dx * breath,
                top: y + Math.cos(frame * 0.043 + p.ph) * p.dy * breath,
                width: size,
                height: size,
                borderRadius: '50%',
                background: `rgb(${mix(to[0])},${mix(to[1])},${mix(to[2])})`,
                border: `1px solid ${hexA(color, 0.5 * col)}`,
                boxShadow: `0 0 ${10 + 20 * col}px ${hexA(color, 0.35 + 0.6 * col)}`,
                opacity: (0.72 + 0.28 * near) * (0.7 + 0.3 * col),
              }}
            />
          </div>
        );
      })}

      {/* 一行行歌词在左右两边跟着亮（左上/右上），说明"点分完了" */}
      {[0, 1].map((s) => {
        const color = s === 0 ? C.cyan : C.violet;
        const on = seg(frame, 130 + s * 6, 150 + s * 6, 0, 1, EASE.outCubic);
        return (
          <div
            key={s}
            style={{
              position: 'absolute',
              left: s === 0 ? 330 : 1130,
              top: 214,
              width: 460,
              textAlign: s === 0 ? 'left' : 'right',
              fontFamily: MONO,
              fontSize: 19,
              letterSpacing: 4,
              color: hexA(color, 0.9),
              opacity: 0.4 * on,
            }}
          >
            {s === 0 ? 'L1 … L17 归 v1' : 'L18 … L36 归 v2'}
          </div>
        );
      })}

      {/* 两簇成型 → 各拉出一条分栏横条，写 v1 / v2 */}
      {BARS.map((b) => {
        const w = seg(frame, b.at, b.at + 30, 0, Math.abs(b.to - b.from), EASE.outCubic);
        const left = b.to < b.from ? b.from - w : b.from;
        const isL = b.side === 0;
        const o = barsO * (1 - dim * 0.25);
        return (
          <div key={b.label}>
            <div
              style={{
                position: 'absolute',
                left,
                top: isL ? 636 : 714,
                width: w,
                height: 58,
                borderRadius: isL ? '14px 4px 4px 14px' : '4px 14px 14px 4px',
                border: `1px solid ${hexA(b.color, 0.9)}`,
                background: `linear-gradient(${isL ? 270 : 90}deg, ${hexA(b.color, 0.22)} 0%, ${hexA(
                  b.color,
                  0.05,
                )} 100%)`,
                boxShadow: `0 0 38px ${hexA(b.color, 0.32)}`,
                opacity: o,
                display: 'flex',
                alignItems: 'center',
                justifyContent: isL ? 'flex-start' : 'flex-end',
                paddingLeft: isL ? 20 : 0,
                paddingRight: isL ? 0 : 20,
                gap: 14,
              }}
            >
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 34,
                  fontWeight: 700,
                  color: b.color,
                  textShadow: glowText(b.color, 34, 0.5),
                }}
              >
                {b.label}
              </span>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 19,
                  letterSpacing: 3,
                  color: C.dim,
                  ...TABULAR,
                }}
              >
                ttm:agent="{b.label}"
              </span>
            </div>
            {/* 从簇里拉向横条的细线 */}
            <div
              style={{
                position: 'absolute',
                left: isL ? 726 : 1174,
                top: CL.y + 4,
                width: 2,
                height: (isL ? 636 : 714) - (CL.y + 4),
                background: `linear-gradient(180deg, ${hexA(b.color, 0.65)} 0%, ${hexA(
                  b.color,
                  0.06,
                )} 100%)`,
                opacity: barsO * 0.9,
              }}
            />
          </div>
        );
      })}

      {/* 分栏说明：这不是"特效"，是 ttm:agent 决定的 */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: 806,
          textAlign: 'center',
          fontFamily: MONO,
          fontSize: 20,
          letterSpacing: 5,
          color: C.dim,
          opacity: seg(frame, 132, 154, 0, 1),
        }}
      >
        v1 / v2 分栏 = Apple Music 左右两栏
      </div>

      <Caption
        frame={frame}
        text="同一时刻谁在唱：声纹聚类给出 v1 / v2，全曲只有 v1 就一定不会分栏"
        at={140}
        bottom={70}
      />
    </AbsoluteFill>
  );
};
