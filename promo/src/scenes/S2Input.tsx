/**
 * 场景 2（17–25s）：做什么 —— 「只要两样东西」。
 *
 * 对应基线 `#s2` 与 `seek()` 里的 s2 段：
 *   reveal(C.s2h2, clamp((t - 17.2) / 1.4, 0, 1));
 *   C.s2sub.style.opacity  = clamp((t - 19.4) / 0.8, 0, 1);
 *   C.s2note.style.opacity = clamp((t - 21.2) / 0.8, 0, 1);
 *
 * ⚠️ 基线两处 inline 覆盖（照抄，不"顺手统一"）：
 *   `#s2sub  style="margin-top:44px"`                 —— 不是 .sub 默认的 34px
 *   `#s2note style="margin-top:36px;font-size:36px"`  —— 36px，不是 .sub 的 40px
 * 第二行「识别 → 强制对齐 → 输出规范 TTML」是 `.dim`（#6b7590）。
 * ⚠️ `<Scene>` 负责显隐 + 进场（0.7s 淡入 + 26px 上移）。
 */
import type React from 'react';
import {COLORS, LINE_HEIGHT, SIZE, WEIGHT} from '../core/theme';
import {RevealText} from '../core/RevealText';
import {Scene, progress} from './_shared';

export const S2Input: React.FC<{t: number}> = ({t}) => (
  <Scene id="s2" t={t}>
    <RevealText
      text="只要两样东西"
      progress={progress(t, 17.2, 1.4)}
      style={{
        fontSize: SIZE.h2,
        fontWeight: WEIGHT.h2,
        lineHeight: LINE_HEIGHT.h2,
      }}
    />

    <div
      style={{
        fontSize: SIZE.sub,
        color: COLORS.gray,
        marginTop: 44, // 基线 inline 覆盖
        lineHeight: LINE_HEIGHT.sub,
        opacity: progress(t, 19.4, 0.8),
      }}
    >
      给它一段音频 + 一份歌词
      <br />
      <span style={{color: COLORS.dim}}>识别 → 强制对齐 → 输出规范 TTML</span>
    </div>

    <div
      style={{
        fontSize: SIZE.subSmall, // 36px（基线 inline 覆盖）
        color: COLORS.gray,
        marginTop: 36, // 基线 inline 覆盖
        lineHeight: LINE_HEIGHT.sub,
        opacity: progress(t, 21.2, 0.8),
      }}
    >
      时间戳<span style={{color: COLORS.cyan}}>全部来自真实音频</span>，不是按字数估算
    </div>
  </Scene>
);
