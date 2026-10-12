/**
 * 时间轴：场景分段 + 缓动 —— 逐条对应基线 `tools/demo_video/slides.html` 的
 * `TOTAL` / `SCENES` / `clamp` / `easeOut` / `enter`。
 *
 * ⚠️ 这里的函数都是**纯函数**：只由 t（秒）决定结果，不看时钟、不看 Math.random。
 *    逐帧渲染的确定性全靠这条（基线也是同一个理由才导出 seek(t)）。
 */

/**
 * 基线时间轴总长（秒）。**与音频时长解耦**：
 * 音频只是被放进 public/ 的素材，渲染时长由 composition 的 videoSeconds 决定。
 */
export const VIDEO_SECONDS = 62.0;

export const FPS = 30;

export type SceneSegment = {
  /** 与 slides.html 里的 DOM id 一致，便于逐帧对照基线 */
  id: 's0' | 's1' | 's2' | 's3' | 's4' | 's5';
  /** 场景开始（秒，含） */
  a: number;
  /** 场景结束（秒，不含） */
  b: number;
  /** 人类可读标题（只用于调试/文档，不参与渲染） */
  title: string;
};

/** 6 个场景的分段，与 slides.html 的 SCENES 完全一致 */
export const SCENES: readonly SceneSegment[] = [
  {id: 's0', a: 0.0, b: 7.0, title: '开场'},
  {id: 's1', a: 7.0, b: 17.0, title: '痛点：整行亮 vs 逐字亮'},
  {id: 's2', a: 17.0, b: 25.0, title: '做什么：只要两样东西'},
  {id: 's3', a: 25.0, b: 41.0, title: '能力列表'},
  {id: 's4', a: 41.0, b: 52.0, title: '实测数字'},
  {id: 's5', a: 52.0, b: 62.0, title: '结尾'},
] as const;

export const SCENE_BY_ID: Record<SceneSegment['id'], SceneSegment> = SCENES.reduce(
  (acc, s) => {
    acc[s.id] = s;
    return acc;
  },
  {} as Record<SceneSegment['id'], SceneSegment>,
);

export function clamp(x: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, x));
}

/** 基线的 easeOut：1-(1-x)^3 */
export function easeOut(x: number): number {
  return 1 - Math.pow(1 - x, 3);
}

/** 场景进入进度：0→1 用 0.7 秒（基线 enter） */
export function enter(t: number, a: number, dur = 0.7): number {
  return clamp((t - a) / dur, 0, 1);
}

/** 基线 enter + easeOut 的组合：返回 0→1 的缓动值 */
export function smoothstep(t: number, a: number, dur = 0.7): number {
  return easeOut(enter(t, a, dur));
}

/** 当前 t 命中的场景（含边界：t=b 时算下一个场景；t=TOTAL 时算最后一个） */
export function sceneAt(t: number, total = VIDEO_SECONDS): SceneSegment {
  const found = SCENES.find((s) => t >= s.a && t < s.b);
  if (found) return found;
  return t < SCENES[0].a ? SCENES[0] : SCENES[SCENES.length - 1];
}

/** 帧号 → 秒（frame/fps）。渲染里 t 只应该这样来。 */
export function frameToSeconds(frame: number, fps = FPS): number {
  return frame / fps;
}

/** 秒 → 帧号（四舍五入） */
export function secondsToFrame(seconds: number, fps = FPS): number {
  return Math.round(seconds * fps);
}
