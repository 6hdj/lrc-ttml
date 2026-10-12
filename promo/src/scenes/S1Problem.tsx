/**
 * 场景 1（7–17s）：痛点 —— 「整行亮」vs「逐字亮」两块对比。
 *
 * 对应基线 `#s1` 与 `seek()` 里的 s1 段：
 *   reveal(C.s1h2, clamp((t - 7.2) / 1.6, 0, 1));
 *   C.s1a.style.opacity   = clamp((t - 9.4)  / 0.8, 0, 1);
 *   C.s1b.style.opacity   = clamp((t - 11.6) / 0.8, 0, 1);
 *   C.s1b.style.transform = translateX((1-easeOut(clamp((t-11.6)/0.8,0,1))) * 40px);
 *
 * ⚠️ `.compare { width: 100% }` 决定了整块**占满场景内容宽度**（1920-140*2 = 1640px），
 *    所以两块对比是宽的、文字左对齐；`.cap` 是 `display:block` 的小标题。
 * ⚠️ `<Scene>` 负责显隐 + 进场（0.7s 淡入 + 26px 上移）。
 */
import type React from 'react';
import {COLORS, LINE_HEIGHT, RADIUS, SIZE, SPACING, WEIGHT} from '../core/theme';
import {RevealText} from '../core/RevealText';
import {Scene, progress, progressEased} from './_shared';

/** `.cline` 的公共形状；配色由 `bg` / `border` 给。 */
const cline = (bg: string, border: string): React.CSSProperties => ({
  padding: `${SPACING.clinePaddingY}px ${SPACING.clinePaddingX}px`,
  borderRadius: RADIUS.cline,
  fontSize: SIZE.cline,
  textAlign: 'left',
  background: bg,
  border: `1px solid ${border}`,
});

/** `.cline .cap` */
const cap: React.CSSProperties = {
  display: 'block',
  fontSize: SIZE.clineCap,
  color: COLORS.capText,
  marginBottom: SPACING.clineCapMarginBottom,
};

/** `.box` —— 逐字高亮的青色小块 */
const box: React.CSSProperties = {
  display: 'inline-block',
  padding: '2px 8px',
  borderRadius: RADIUS.box,
  background: COLORS.boxBg,
  color: COLORS.boxText,
  fontWeight: WEIGHT.box,
};

export const S1Problem: React.FC<{t: number}> = ({t}) => {
  // 青框行：整体 0.8s 淡入 + 从右滑入 40px（easeOut cubic）
  const goodP = progress(t, 11.6, 0.8);
  const goodEased = progressEased(t, 11.6, 0.8);

  return (
    <Scene id="s1" t={t}>
      <RevealText
        text="你的歌词，现在还只能整行亮"
        progress={progress(t, 7.2, 1.6)}
        style={{
          fontSize: SIZE.h2,
          fontWeight: WEIGHT.h2,
          lineHeight: LINE_HEIGHT.h2,
        }}
      />

      <div
        style={{
          width: '100%',
          marginTop: SPACING.compareMarginTop,
          display: 'flex',
          flexDirection: 'column',
          gap: SPACING.compareGap,
        }}
      >
        {/* 红框：普通歌词 / 整行高亮 */}
        <div style={{...cline(COLORS.badBg, COLORS.badBorder), opacity: progress(t, 9.4, 0.8)}}>
          <span style={cap}>普通歌词文件 / 大多数播放器</span>
          整行高亮 —— 唱到哪一句，亮哪一句
        </div>

        {/* 青框：逐字歌词 / 逐字高亮（box 里的是被点亮的字） */}
        <div
          style={{
            ...cline(COLORS.goodBg, COLORS.goodBorder),
            opacity: goodP,
            transform: `translateX(${(1 - goodEased) * SPACING.compareSlideX}px)`,
          }}
        >
          <span style={cap}>逐字歌词（TTML）</span>
          <span style={box}>唱</span>到<span style={box}>哪</span>个<span style={box}>字</span>，亮<span style={box}>哪</span>个<span style={box}>字</span>
        </div>
      </div>
    </Scene>
  );
};
