/**
 * 场景 5（52–62s）：结尾 —— 标题 / 一句话 / 仓库地址 / 许可。
 *
 * 对应基线 `#s5` 与 `seek()` 里的 s5 段：
 *   reveal(C.s5h1, clamp((t - 52.4) / 2.0, 0, 1));
 *   C.s5sub.style.opacity = clamp((t - 55.0) / 0.9, 0, 1);
 *   #url.style.opacity    = clamp((t - 57.0) / 0.9, 0, 1);
 *   最后一个 .sub         = clamp((t - 58.6) / 0.9, 0, 1);
 *
 * ⚠️ 基线里 `#s5h1` 有 inline `font-size:92px`（h1 默认 116px 只给 s0 用），
 *    `#s5sub` 内层是 `font-size:32px` 的 `.dim`，最后一行 `.sub` 是 30px。
 *    `#url` 用等宽字体 + 青绿色 `#9fd3c7`。
 * ⚠️ `<Scene>` 负责显隐 + 进场（0.7s 淡入 + 26px 上移）。
 */
import type React from 'react';
import {COLORS, FONT, LINE_HEIGHT, SIZE, SPACING, WEIGHT} from '../core/theme';
import {RevealText} from '../core/RevealText';
import {Scene, progress} from './_shared';

export const S5Ending: React.FC<{t: number}> = ({t}) => (
  <Scene id="s5" t={t}>
    <RevealText
      text="Lyric2TTML"
      progress={progress(t, 52.4, 2.0)}
      style={{
        fontSize: SIZE.h1End, // 92px（基线 inline 覆盖）
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
        opacity: progress(t, 55.0, 0.9),
      }}
    >
      把普通歌词变成逐字时间轴
      <br />
      <span style={{fontSize: SIZE.subTiny, color: COLORS.dim}}>Windows 一键运行 · 也可 pip 安装</span>
    </div>

    <div
      style={{
        fontSize: SIZE.url,
        color: COLORS.url,
        fontFamily: FONT.mono,
        marginTop: SPACING.urlMarginTop,
        letterSpacing: SPACING.urlLetterSpacing,
        opacity: progress(t, 57.0, 0.9),
      }}
    >
      github.com/6hdj/lrc-ttml
    </div>

    <div
      style={{
        fontSize: SIZE.subEnd, // 30px（基线 inline 覆盖）
        color: COLORS.dim,
        marginTop: 22, // 基线 inline 覆盖
        lineHeight: LINE_HEIGHT.sub,
        opacity: progress(t, 58.6, 0.9),
      }}
    >
      MIT 开源 · 可自由使用与商用
    </div>
  </Scene>
);
