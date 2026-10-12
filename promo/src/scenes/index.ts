/**
 * 六个场景的汇总导出 —— `Composition.tsx` / `Root.tsx` 从这里按顺序取用。
 *
 * 统一签名：`React.FC<{ t: number }>`，`t` = 全局秒数（`useCurrentFrame() / fps`）。
 * 每个场景内部只画内容，进场（0.7s 淡入 + 26px 上移）与显隐交给 core 的 `SceneWindow`，
 * 区间取自 core 的 `SCENES`（与基线 `tools/demo_video/slides.html` 同源）。
 *
 * 纯函数约束：所有可见状态都只由 `t` 决定；没有 CSS transition/animation、
 * 没有 `Date.now()`、没有随机数。
 */
import {S0Opening} from './S0Opening';
import {S1Problem} from './S1Problem';
import {S2Input} from './S2Input';
import {S3Features} from './S3Features';
import {S4Stats} from './S4Stats';
import {S5Ending} from './S5Ending';

export {S0Opening, S1Problem, S2Input, S3Features, S4Stats, S5Ending};

/** 按时间顺序的六个场景，供 `Composition` 直接 map（顺序 = 基线 `SCENES` 顺序）。 */
export const SCENE_COMPONENTS = [
  {id: 's0', Component: S0Opening},
  {id: 's1', Component: S1Problem},
  {id: 's2', Component: S2Input},
  {id: 's3', Component: S3Features},
  {id: 's4', Component: S4Stats},
  {id: 's5', Component: S5Ending},
] as const;
