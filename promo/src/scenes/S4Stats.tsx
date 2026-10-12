/**
 * 场景 4（41–52s）：实测数字 —— h2 + 3 个大数字 + 一行注解。
 *
 * 对应基线 `#s4` / `.stats` / `.stat` 与 `seek()` 里的 s4 段：
 *   reveal(C.s4h2, clamp((t - 41.2) / 1.3, 0, 1));
 *   C.s4stats.forEach((d, i) => {                      // 每 1.5s 弹一个
 *     const p = clamp((t - (43.0 + i * 1.5)) / 0.7, 0, 1);
 *     d.style.opacity = p;
 *     d.style.transform = `scale(${0.72 + 0.28 * easeOut(p)})`;   // 0.72 → 1
 *   });
 *   C.s4note.style.opacity = clamp((t - 48.6) / 0.9, 0, 1);
 *
 * 版式：`.stat .n` = 130px / 800 / 青色 / line-height:1；`.stat .l` = 34px / #9aa4bf /
 *       margin-top:18px，`\n` 换成 `<br>`（两行居中）；`.stats` 是 flex / gap:90 / margin-top:60px。
 * ⚠️ `<Scene>` 负责显隐 + 进场（0.7s 淡入 + 26px 上移）。
 */
import React from 'react';
import {COLORS, LINE_HEIGHT, SIZE, SPACING, WEIGHT} from '../core/theme';
import {RevealText} from '../core/RevealText';
import {Scene, progress, progressEased} from './_shared';

/** 基线 `STATS` 原样：[数字, 说明（`\n` → 换行）] */
const STATS: ReadonlyArray<readonly [string, string]> = [
  ['100%', '字级匹配率\n（字全唱对时）'],
  ['85%', '对唱分声部\n（人工真值）'],
  ['0', '联网需求\n完全离线'],
];

/** 第一个数字的起点、间隔、时长（秒），基线 `43.0 + i * 1.5` / 0.7。 */
const STAT_START = 43.0;
const STAT_STEP = 1.5;
const STAT_DUR = 0.7;

export const S4Stats: React.FC<{t: number}> = ({t}) => (
  <Scene id="s4" t={t}>
    <RevealText
      text="一些实测数字"
      progress={progress(t, 41.2, 1.3)}
      style={{
        fontSize: SIZE.h2,
        fontWeight: WEIGHT.h2,
        lineHeight: LINE_HEIGHT.h2,
      }}
    />

    <div
      style={{
        display: 'flex',
        gap: SPACING.statsGap,
        marginTop: SPACING.statsMarginTop,
      }}
    >
      {STATS.map(([num, label], i) => {
        const start = STAT_START + i * STAT_STEP;
        const p = progress(t, start, STAT_DUR);
        const eased = progressEased(t, start, STAT_DUR);
        return (
          <div
            key={num}
            style={{
              textAlign: 'center',
              opacity: p,
              transform: `scale(${0.72 + 0.28 * eased})`,
            }}
          >
            <div
              style={{
                fontSize: SIZE.statNumber,
                fontWeight: WEIGHT.statNumber,
                color: COLORS.cyan,
                lineHeight: LINE_HEIGHT.statNumber,
              }}
            >
              {num}
            </div>
            <div
              style={{
                fontSize: SIZE.statLabel,
                color: COLORS.gray,
                marginTop: SPACING.statLabelMarginTop,
                // ⚠️ 基线 `.stat .l` **没有** line-height（用字体 normal）：
                //    实测 t051 两行标签的中心距 = 45.5px ≈ 1.32em，而 `.sub` 的 1.6 会是 54.4px。
                //    这里绝不能顺手写上 1.6，否则整块统计会高 19px、位置整体下移。
              }}
            >
              {label.split('\n').map((line, j) => (
                <React.Fragment key={line}>
                  {j > 0 ? <br /> : null}
                  {line}
                </React.Fragment>
              ))}
            </div>
          </div>
        );
      })}
    </div>

    <div
      style={{
        fontSize: SIZE.sub,
        color: COLORS.gray,
        marginTop: SPACING.subMarginTop,
        lineHeight: LINE_HEIGHT.sub,
        opacity: progress(t, 48.6, 0.9),
      }}
    >
      <span style={{color: COLORS.dim}}>数字来自仓库里的评估脚本，可复现</span>
    </div>
  </Scene>
);
