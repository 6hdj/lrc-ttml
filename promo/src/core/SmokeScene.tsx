import React from 'react';
import {useCurrentFrame} from 'remotion';
import {RevealText} from './RevealText';
import {COLORS, FONT, SIZE, SPACING, WEIGHT} from './theme';
import {clamp, sceneAt} from './timeline';

/**
 * ⚠️ 临时冒烟场景（core-builder 自己的作用域）。
 *
 * 用途：在 `src/scenes/*` 还没交付时，证明「Backdrop + RevealText + SceneWindow + ProgressBar」
 * 这条链路能真的逐帧出图。**最终成片里不会出现它** —— Composition.tsx 的
 * SCENE-SLOTS 接线完成后它就不再被引用（保留是为了以后 core 单独自检）。
 *
 * 它刻意把 `frame` / `t` 印在画面上：出图后一眼就能确认"这一帧确实是那一帧"
 * （渲染管线接错帧号时，画面上的读数会立刻暴露问题）。
 */
export const SmokeScene: React.FC<{t: number}> = ({t}) => {
  const frame = useCurrentFrame();
  const seg = sceneAt(t);
  // 只用 t 算 → 同一个 t 永远同一张图
  const progress = clamp((t - seg.a - 0.2) / 2.6, 0, 1);

  return (
    <>
      <div
        style={{
          fontSize: SIZE.tag,
          color: COLORS.cyan,
          letterSpacing: SPACING.tagLetterSpacing,
          fontWeight: WEIGHT.tag,
          marginBottom: SPACING.tagMarginBottom,
        }}
      >
        CORE SMOKE TEST
      </div>
      <div style={{fontSize: SIZE.h1, fontWeight: WEIGHT.h1, letterSpacing: SPACING.h1LetterSpacing, lineHeight: 1.18}}>
        <RevealText text="把歌词做成逐字时间轴" progress={progress} />
      </div>
      <div
        style={{
          fontSize: SIZE.sub,
          color: COLORS.gray,
          marginTop: SPACING.subMarginTop,
          lineHeight: 1.6,
        }}
      >
        普通歌词 + 歌曲音频 <span style={{color: COLORS.cyan}}>→</span> Apple Music / AMLL 逐字时间轴
      </div>
      <div
        style={{
          marginTop: 46,
          padding: '14px 26px',
          borderRadius: 14,
          border: '1px solid rgba(255,255,255,.07)',
          background: 'rgba(255,255,255,.035)',
          fontFamily: FONT.mono,
          fontSize: 30,
          color: COLORS.text,
        }}
      >
        frame {frame} · t={t.toFixed(3)}s · scene {seg.id} ({seg.title}) · reveal p={progress.toFixed(3)}
      </div>
      <div style={{marginTop: 22, fontSize: 24, color: COLORS.dim}}>
        临时占位场景：由 core-builder 提供，等 scene-builder 交付 6 个真实场景后从 Composition 摘除
      </div>
    </>
  );
};
