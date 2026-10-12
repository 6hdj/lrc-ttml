/**
 * 场景公共层：动画小工具 + 一行式「场景外壳」。
 *
 * ⚠️ 作用域：本文件属于 task-2（`promo/src/scenes/**`）。
 *    `promo/src/core/**`、`Root.tsx`、`Composition.tsx` 是 core-builder 的，这里只**读**。
 *
 * 层级（Lead 冻结）：**场景外壳由场景自己包** —— 本文件导出的 `<Scene>` =
 * core 的 `<SceneWindow>` + 自动取区间；`Composition.tsx` 只负责 `map` 出六个场景，
 * 不再额外包一层（包两层会让进场动画叠加：上移 26px 变 52px、opacity 变平方）。
 *
 * ⚠️ 本文件含 JSX，所以扩展名必须是 `.tsx`（`.ts` 里写 JSX 连 esbuild 都不认）。
 *
 * 每帧都是 t 的纯函数：没有 CSS transition/animation、没有 Date.now、没有随机数。
 * 动画值一律用 Remotion 的 `interpolate` / `Easing` 算；基线
 * `tools/demo_video/slides.html` 的手写 clamp 链换成 `interpolate` 的 clamp 外推，
 * 数值逐项相同（`easeOut(x)=1-(1-x)^3` ≡ `Easing.out(Easing.cubic)`）。
 */
import type React from 'react';
import {Easing, interpolate} from 'remotion';
import {SceneWindow} from '../core/SceneWindow';
import {SCENE_BY_ID} from '../core/timeline';
import type {SceneSegment} from '../core/timeline';

/** 场景 id：s0…s5，与基线 DOM id 一致。 */
export type SceneId = SceneSegment['id'];

/** 基线的 `easeOut` = easeOutCubic，用 Remotion 的缓动表达同一个函数。 */
export const EASE_CUBIC = Easing.out(Easing.cubic);

/**
 * `clamp((t - a) / dur, 0, 1)` —— 基线到处都是这条。
 * 用 `interpolate` 的两端 clamp 外推等价实现。
 */
export const progress = (t: number, a: number, dur: number): number =>
  interpolate(t, [a, a + dur], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

/** 同上，但带 easeOut cubic 缓动（基线 `easeOut(clamp(...))`）。 */
export const progressEased = (t: number, a: number, dur: number): number =>
  interpolate(t, [a, a + dur], [0, 1], {
    easing: EASE_CUBIC,
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

/**
 * 场景外壳：`SceneWindow`（core 提供的进场/显隐）+ 自动取区间。
 *
 * 区间只从 core 的 `SCENES` 取，**不在这里再抄一份**，
 * 这样六个场景不会各写一段「什么时候显示、怎么进场」而漂移。
 */
export const Scene: React.FC<{id: SceneId; t: number; children?: React.ReactNode}> = ({
  id,
  t,
  children,
}) => {
  const {a, b} = SCENE_BY_ID[id];
  return (
    <SceneWindow t={t} a={a} b={b}>
      {children}
    </SceneWindow>
  );
};
