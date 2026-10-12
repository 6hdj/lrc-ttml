/**
 * 场景 0（0–7s）：开场 —— tag / h1 / sub 三层。
 *
 * 逐条对应基线 `tools/demo_video/slides.html` 的 `#s0` 与 `seek()` 里的 s0 段：
 *   reveal(C.s0h1, clamp((t - 1.0) / 2.6, 0, 1));
 *   C.s0tag.style.opacity  = clamp((t - 0.25) / 0.7, 0, 1);
 *   C.s0sub.style.opacity  = clamp((t - 3.6)  / 0.9, 0, 1);
 *
 * ⚠️ 文案一个字都没改：注意 ` + `、` → `（箭头两侧各一个空格）都是基线原文。
 * ⚠️ `<Scene>` 负责显隐 + 进场（0.7s 淡入 + 26px 上移）；三个块是它的直接 flex 子项，
 *    与基线 `.scene` 里 h1/.tag/.sub 的层级一致。
 */
import type React from 'react';
import {COLORS, LINE_HEIGHT, SIZE, SPACING, WEIGHT} from '../core/theme';
import {RevealText} from '../core/RevealText';
import {Scene, progress} from './_shared';

export const S0Opening: React.FC<{t: number}> = ({t}) => (
  <Scene id="s0" t={t}>
    <div
      style={{
        fontSize: SIZE.tag,
        fontWeight: WEIGHT.tag,
        color: COLORS.cyan,
        letterSpacing: SPACING.tagLetterSpacing,
        marginBottom: SPACING.tagMarginBottom,
        // 基线 `.tag { letter-spacing: 8px }`；末尾字距保留，视觉与基线一致
        opacity: progress(t, 0.25, 0.7),
      }}
    >
      全程离线 · 无需 API Key
    </div>

    {/* 10 字 ⇒ 最后一个字的阈值 0.02 + 9*0.045*0.55 = 0.2428 < 1，进度 1 时全部归位 */}
    <RevealText
      text="把歌词做成逐字时间轴"
      progress={progress(t, 1.0, 2.6)}
      style={{
        fontSize: SIZE.h1,
        fontWeight: WEIGHT.h1,
        letterSpacing: SPACING.h1LetterSpacing,
        lineHeight: LINE_HEIGHT.h1,
      }}
    />

    <div
      style={{
        fontSize: SIZE.sub,
        color: COLORS.gray,
        marginTop: SPACING.subMarginTop,
        lineHeight: LINE_HEIGHT.sub,
        opacity: progress(t, 3.6, 0.9),
      }}
    >
      普通歌词 + 歌曲音频 <span style={{color: COLORS.cyan}}>→</span> Apple Music / AMLL 逐字时间轴
    </div>
  </Scene>
);
