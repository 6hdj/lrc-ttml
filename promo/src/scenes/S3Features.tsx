/**
 * 场景 3（25–41s）：能力列表 —— h2 + 5 行「图标 + 主标题 + 右侧灰色说明」。
 *
 * 对应基线 `#s3` / `.rows` / `.row` 与 `seek()` 里的 s3 段：
 *   reveal(C.s3h2, clamp((t - 25.2) / 1.3, 0, 1));
 *   C.s3rows.forEach((r, i) => {
 *     const st = 26.6 + i * 2.3;                       // 每行隔 2.3s
 *     const p  = clamp((t - st) / 0.75, 0, 1);
 *     r.style.opacity = p;
 *     r.style.transform = translateX((1-easeOut(p)) * 60px);
 *   });
 *
 * 行版式（全部来自基线 CSS）：
 *   .row  = flex / align-items:center / gap:34 / padding:26px 40px / margin:18px 0 /
 *           radius:20 / rgba(255,255,255,.035) 底 + .07 边框 / font-size:46px / 左对齐
 *   .ico  = 52px，宽 66，居中       .t = 700 字重        .d = 34px，`margin-left:auto` 顶到最右
 *
 * ⚠️ 5 行文案严格照抄基线（含 `✏️` 的变体选择符与 ` · ` 两侧的空格）。
 * ⚠️ `<Scene>` 负责显隐 + 进场（0.7s 淡入 + 26px 上移）。
 */
import type React from 'react';
import {COLORS, LINE_HEIGHT, RADIUS, SIZE, SPACING, WEIGHT} from '../core/theme';
import {RevealText} from '../core/RevealText';
import {Scene, progress, progressEased} from './_shared';

/** 基线 `ROWS` 原样：[图标, 主标题, 右侧说明] */
const ROWS: ReadonlyArray<readonly [string, string, string]> = [
  ['🎯', '逐字时间轴', '真实音频对齐'],
  ['🎤', '对唱分声部', 'CAM++ 声纹'],
  ['🎼', '背景和声', 'x-bg 层'],
  ['✏️', '人工精修编辑台', '改字 / 改时间 / 改声部'],
  ['🌐', '翻译 · 六平台 ID', 'Apple / AMLL 双格式'],
];

/** 第一行的起点、行间隔、时长（秒），基线 `26.6 + i * 2.3` / 0.75。 */
const ROW_START = 26.6;
const ROW_STEP = 2.3;
const ROW_DUR = 0.75;
/** 每行横向滑入距离（px），基线 `translateX((1-easeOut(p)) * 60)`。 */
const ROW_SLIDE_X = SPACING.slideInX;

export const S3Features: React.FC<{t: number}> = ({t}) => (
  <Scene id="s3" t={t}>
    <RevealText
      text="它还能做这些"
      progress={progress(t, 25.2, 1.3)}
      style={{
        fontSize: SIZE.h2,
        fontWeight: WEIGHT.h2,
        lineHeight: LINE_HEIGHT.h2,
      }}
    />

    <div style={{width: '100%', marginTop: SPACING.rowsMarginTop}}>
      {ROWS.map(([ico, title, desc], i) => {
        const start = ROW_START + i * ROW_STEP;
        const p = progress(t, start, ROW_DUR);
        const eased = progressEased(t, start, ROW_DUR);
        return (
          <div
            key={title}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: SPACING.rowGap,
              padding: `${SPACING.rowPaddingY}px ${SPACING.rowPaddingX}px`,
              margin: `${SPACING.rowMarginY}px 0`,
              borderRadius: RADIUS.row,
              background: COLORS.rowBg,
              border: `1px solid ${COLORS.rowBorder}`,
              fontSize: SIZE.row,
              textAlign: 'left',
              opacity: p,
              transform: `translateX(${(1 - eased) * ROW_SLIDE_X}px)`,
            }}
          >
            <span
              style={{
                fontSize: SIZE.rowIco,
                width: SPACING.rowIcoWidth,
                textAlign: 'center',
                flexShrink: 0,
              }}
            >
              {ico}
            </span>
            <span style={{fontWeight: WEIGHT.rowTitle}}>{title}</span>
            <span
              style={{
                fontSize: SIZE.rowDesc,
                color: COLORS.rowDesc,
                marginLeft: 'auto',
                // 右侧说明在基线里不会折行；显式声明，避免以后改字号时突然换行
                whiteSpace: 'nowrap',
              }}
            >
              {desc}
            </span>
          </div>
        );
      })}
    </div>
  </Scene>
);
