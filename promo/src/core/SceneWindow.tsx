import React from 'react';
import {AbsoluteFill} from 'remotion';
import {SPACING} from './theme';
import {easeOut, enter} from './timeline';

/**
 * 冻结的场景接口：每个场景组件都是 `({t}: SceneProps) => JSX`，
 * 只画内容；"何时显示 / 怎么进场"由 SceneWindow 统一负责（不要各写一遍）。
 */
export type SceneProps = {
  /** 当前时间（秒）= frame / fps */
  t: number;
};

export type SceneWindowProps = {
  /** 当前时间（秒） */
  t: number;
  /** 场景开始 / 结束（秒）——来自 core/timeline 的 SCENES */
  a: number;
  b: number;
  /** 进入时长，默认 0.7s（基线 enter 的默认值） */
  enterDuration?: number;
  children: React.ReactNode;
};

/**
 * 场景外壳：负责"什么时候显示 + 怎么进场"，**场景组件自己只画内容**。
 *
 * 逐条对应基线：
 *   .scene { position:absolute; inset:0; display:flex; flex-direction:column;
 *            justify-content:center; align-items:center; text-align:center; padding:0 140px }
 *   seek() 里：display = on ? flex : none；opacity = enter 进度；
 *              transform = translateY((1-easeOut(p)) * 26px)
 *
 * ⚠️ 分工是刻意的：6 个场景各写一遍这段必然漂移（还会出现"两个场景同时可见"）。
 *    场景组件拿到的还是 `{t}`，进场由这里统一负责。
 */
export const SceneWindow: React.FC<SceneWindowProps> = ({t, a, b, enterDuration = 0.7, children}) => {
  const on = t >= a && t < b;
  const p = enter(t, a, enterDuration);

  return (
    <AbsoluteFill
      style={{
        display: on ? 'flex' : 'none',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        textAlign: 'center',
        padding: `0 ${SPACING.scenePaddingX}px`,
        opacity: on ? p : 0,
        transform: `translateY(${(1 - easeOut(p)) * SPACING.sceneLift}px)`,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
