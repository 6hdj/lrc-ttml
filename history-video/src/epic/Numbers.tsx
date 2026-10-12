/**
 * 场 9 · 实测（场内 0–179，6s）
 *
 * 形式：三根竖向柱状从左到右依次长出来，柱顶数字滚到目标值，到顶时加一圈淡光晕。
 *
 * 数字是**仓库里真跑出来的**，不为好看改：
 *   · `100%` 字级匹配率（字全唱对时）—— `tools/eval_word_timing.py`；
 *   · `85%`  对唱分声部（与人工标注一致率）—— `tools/eval_diarization.py`；
 *   · `0`    联网需求（全程离线、无 API Key）—— 本项目不接任何在线服务。
 * ⚠️ 前两根柱按 0–100 的同一比例画；第三根目标值是 0，**故意不画假高度**：
 *    只留一根描边柱 + 基线上一颗亮点，"0" 本身就是要说的话。
 *
 * 全部动画在 162 帧内做完（duration-18）。
 * 对应 SPEC.md §3「场 9」。task-6。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../constants';
import {Caption, CornerLabel, EASE, TABULAR, glowText, hexA, rollNum, seg, stage} from './shared';

const BASE = 900; // 柱底
const BARM = 560; // 100% 对应的柱高
const BARW = 260;
const BARS_X = [372, 830, 1288]; // 三根柱的左端（组宽 372→1548，居中）
const GAP = 30; // 每根柱先后错开 30 帧

type Metric = {
  /** 柱高按 0..100 归一化，所以 85 的柱就是 100 的 85% 高 */
  v: number;
  /** 数字滚动的起点帧（= 这根柱开始长的时候） */
  a: number;
  b: number;
  label: string;
  note: string;
  color: string;
  /** 千分位之后的单位；空字符串 = 不要单位 */
  unit: string;
};

const METRICS: Metric[] = [
  {
    v: 100,
    a: 22,
    b: 50,
    label: '字级匹配率',
    note: '字全都唱对时（外部逐字真值对比）',
    color: C.cyan,
    unit: '%',
  },
  {
    v: 85,
    a: 52,
    b: 80,
    label: '对唱分声部',
    note: '与人工标注一致率 · CAM++ 声纹',
    color: C.green,
    unit: '%',
  },
  {
    v: 0,
    a: 82,
    b: 110,
    label: '联网需求',
    note: '全程离线 · 无需 API Key',
    color: C.violet,
    unit: '',
  },
];

export const Numbers: React.FC<{frame: number}> = ({frame}) => {
  const f = frame;

  return (
    <AbsoluteFill style={stage(C.bgB)}>
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(64% 54% at 50% 52%, rgba(34,211,238,0.075) 0%, transparent 72%)',
        }}
      />

      <CornerLabel frame={f} title="实测" sub="不是感觉，是脚本跑出来的" />

      {/* 基线 + 极淡的 50% / 100% 参考横线 */}
      <div style={{position: 'absolute', left: 150, top: BASE, width: 1620, height: 1, background: C.axis}} />
      {[0.5, 1].map((r) => (
        <React.Fragment key={r}>
          <div
            style={{
              position: 'absolute',
              left: 150,
              top: BASE - BARM * r,
              width: 1620,
              height: 1,
              background: r === 1 ? C.line : hexA('#ffffff', 0.045),
            }}
          />
          <div
            style={{
              position: 'absolute',
              left: 150,
              top: BASE - BARM * r - 12,
              width: 80,
              textAlign: 'right',
              fontSize: 15,
              color: '#33405a',
              ...TABULAR,
            }}
          >
            {r * 100}
          </div>
        </React.Fragment>
      ))}

      {METRICS.map((m, i) => {
        const x = BARS_X[i];
        const g = seg(f, m.a, m.b, 0, 1, EASE.outCubic);
        const h = (m.v / 100) * BARM * g;
        const gd = Math.max(0, Math.min(1, (f - (m.b + 6)) / 14)); // 到顶后再补光晕
        const cy = BASE - h;

        return (
          <React.Fragment key={m.label}>
            {/* 数字：等宽 + 固定居中框，滚动时宽度不抖。
                目标值 0 的那一根**不假装有高度**：数字就摆在基线上方，
                底下只有一颗亮点 —— 位置本身就在说"这根柱子是空的"。 */}
            <div
              style={{
                position: 'absolute',
                left: x - 60,
                top: m.v > 0 ? BASE - BARM - 118 : BASE - 148,
                width: BARW + 120,
                textAlign: 'center',
                opacity: seg(f, m.a, m.a + 12, 0, 1),
                ...TABULAR,
              }}
            >
              <span
                style={{
                  fontSize: 62,
                  fontWeight: 800,
                  lineHeight: 1.1,
                  color: m.color,
                  textShadow: glowText(m.color, 46, 0.35),
                }}
              >
                {rollNum(f, m.a, m.b, 0, m.v).toFixed(0)}
              </span>
              <span
                style={{
                  fontSize: 30,
                  fontWeight: 700,
                  color: hexA(m.color, 0.72),
                  marginLeft: 6,
                }}
              >
                {m.unit}
              </span>
            </div>

            {/* 柱：描边柱定住位置，实心柱长高 */}
            <div
              style={{
                position: 'absolute',
                left: x,
                top: BASE - BARM,
                width: BARW,
                height: BARM,
                borderRadius: 10,
                border: `1px solid ${hexA(m.color, 0.16)}`,
                background: hexA(m.color, 0.028),
                opacity: seg(f, m.a, m.b, 0, 1),
              }}
            />
            <div
              style={{
                position: 'absolute',
                left: x,
                top: cy,
                width: BARW,
                height: Math.max(h, 2),
                borderRadius: 10,
                background: `linear-gradient(180deg, ${hexA(m.color, 0.92)} 0%, ${hexA(
                  m.color,
                  0.32,
                )} 100%)`,
                opacity: seg(f, m.a, m.b, 0, 1),
              }}
            />
            {/* 到顶的一圈淡光晕 */}
            <div
              style={{
                position: 'absolute',
                left: x - 40,
                top: cy - 40,
                width: BARW + 80,
                height: 80,
                borderRadius: '50%',
                background: `radial-gradient(closest-side, ${hexA(m.color, 0.5)} 0%, ${hexA(
                  m.color,
                  0,
                )} 78%)`,
                opacity: gd * (0.55 + 0.2 * Math.sin((f - m.b) * 0.18)),
              }}
            />
            {/* 柱顶那颗亮点（0 那一根也留着，它在基线上） */}
            <div
              style={{
                position: 'absolute',
                left: x + BARW / 2 - 5,
                top: cy - 5,
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: m.color,
                boxShadow: `0 0 18px ${hexA(m.color, 0.9)}`,
                opacity: seg(f, m.a, m.b, 0, 1),
              }}
            />
            {/* 空柱（0）在基线上单独留一圈光晕，让"零"是一句完整的话 */}
            {m.v === 0 ? (
              <div
                style={{
                  position: 'absolute',
                  left: x + BARW / 2 - 70,
                  top: BASE - 70,
                  width: 140,
                  height: 140,
                  borderRadius: '50%',
                  background: `radial-gradient(closest-side, ${hexA(m.color, 0.42)} 0%, ${hexA(
                    m.color,
                    0,
                  )} 78%)`,
                  opacity: seg(f, m.b, m.b + 22, 0, 1) * (0.6 + 0.18 * Math.sin((f - m.b) * 0.16)),
                }}
              />
            ) : null}

            {/* 柱下方的小字说明 */}
            <div
              style={{
                position: 'absolute',
                left: x - 40,
                top: BASE + 30,
                width: BARW + 80,
                textAlign: 'center',
                opacity: seg(f, m.b, m.b + 24, 0, 1),
                transform: `translateY(${seg(f, m.b, m.b + 24, 12, 0, EASE.outCubic)}px)`,
              }}
            >
              <div style={{fontSize: 26, color: hexA(m.color, 0.92), letterSpacing: 3}}>
                {m.label}
              </div>
              <div style={{fontSize: 17, color: hexA('#ffffff', 0.42), letterSpacing: 1, marginTop: 10}}>
                {m.note}
              </div>
            </div>
          </React.Fragment>
        );
      })}

      <Caption
        frame={f}
        text="数字来自仓库里的评估脚本，可复现"
        at={144}
        bottom={70}
        color={C.dim}
        size={20}
      />
    </AbsoluteFill>
  );
};
