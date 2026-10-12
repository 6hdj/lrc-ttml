/**
 * 全片场景表 —— **只改这里就能调节奏**。
 *
 * 各场内部的动画都按「本场第几帧」驱动（`<Body frame={frame - s.from} />`），
 * 所以挪动 `from` / `duration` 不会影响场内动画，只影响"什么时候出现"。
 *
 * ⚠️ 改 `duration` 时必须同步改后面所有 `from`（等于前面 duration 累加），
 *    并保证 `INTRO_DURATION` == 最后一场的 from + duration，否则结尾会缺画面。
 * ⚠️ 每场内部动画要在 `duration - 18` 帧内做完：最后 12 帧是退场叠化（OVER），
 *    再留 6 帧余量。见 SPEC.md §2。
 */
export const INTRO_FPS = 30;
export const INTRO_DURATION = 1800; // 60 秒

/** 叠化时长（帧）：上一场多挂这么久，让下一场淡上来盖住它 */
export const OVER = 12;

/**
 * 配乐。把音频放进 `public/`，这里改成文件名即可（如 `"bgm.wav"`）。
 * 留 `null` = 静音渲染，不会报错。
 */
export const BGM: string | null = 'bgm.wav';

export type SceneDef = {
  id: string;
  /** 场名（中文，便于对照 SPEC.md） */
  name: string;
  /** 起始帧（全局帧号） */
  from: number;
  /** 持续帧数 */
  duration: number;
};

export const SCENES: SceneDef[] = [
  {id: 'overture', name: '序', from: 0, duration: 105}, // 3.5s
  {id: 'counting', name: '打轴', from: 105, duration: 240}, // 8.0s
  {id: 'waveform', name: '声音', from: 345, duration: 165}, // 5.5s
  {id: 'linevschar', name: '对比', from: 510, duration: 150}, // 5.0s
  {id: 'structure', name: '结构', from: 660, duration: 165}, // 5.5s
  {id: 'duet', name: '对唱', from: 825, duration: 180}, // 6.0s
  {id: 'background', name: '和声', from: 1005, duration: 150}, // 5.0s
  {id: 'editor', name: '编辑台', from: 1155, duration: 210}, // 7.0s
  {id: 'numbers', name: '实测', from: 1365, duration: 180}, // 6.0s
  {id: 'finale', name: '尾声', from: 1545, duration: 255}, // 8.5s
];

/**
 * 稳定伪随机。
 *
 * ⚠️ Remotion 会并发渲染不同帧，**绝不能用 `Math.random()` / `Date.now()`**：
 *    那样每次渲染同一帧都会不一样，成片会随机闪烁、也没法复现。
 */
export const rnd = (n: number): number => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};
