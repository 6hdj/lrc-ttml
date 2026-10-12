/**
 * 场 4 · 对比（场内 0–149，5.0s）
 *
 * 形式：左右分屏，中间一条竖分割线。
 *   左半（暖色 #f59e0b 调）= **旧做法**：整句话就是"一个横条"，一唱到这一句，
 *                            整条一起亮 —— 播放器只知道"唱到哪一句"。
 *   右半（青色）= **逐字高亮**：同一句话逐字依次点亮，每个字带一个小方块，
 *                  播放器知道"唱到哪一个字"。
 *   一条扫描线（playhead）从左到右匀速扫过，扫到哪半哪半就是"正在播"。
 *   结尾 20 帧：右半浮起来放大到 1.06，左半压暗到 0.45。
 *
 * 帧设计（动画在 132 帧内做完，最后 12 帧留给总装叠化）：
 *   4–30    分屏与两句话进场
 *   26–103  同时"演唱"：左半一次性整条亮；右半 7 个字每 11 帧亮一个
 *   14–132  扫描线匀速扫过
 *   116–132 收尾：右半 scale → 1.06，左半 → 0.45（压暗）
 *
 * 取舍：SPEC 说"结尾 20 帧"做放大/压暗，但本片硬约束是"动画在 duration-18（=132）帧内做完"，
 *       所以收尾放在 116–132 帧，之后保持不动 —— 效果一样落在结尾，且不跟退场叠化打架。
 *
 * 对应 SPEC.md §3「场 4」。所有数值都是帧的纯函数（无 Math.random / Date.now）。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../constants';
import {
  BG,
  Caption,
  CharRun,
  CornerLabel,
  EASE,
  TABULAR,
  appear,
  glowLayer,
  hexA,
  riseIn,
  seg,
  stage,
} from './shared';

/* ------------------------------------------------------------------ *
 * 数据与几何
 * ------------------------------------------------------------------ */

const LINE = Array.from('夜色落在窗台上'); // 两边是**同一句话** —— 对比才成立

const WARM = C.warm;

const HALF = 960; // 半屏宽
const PANEL_L = 56; // 面板在各自半屏里的左边距
const PANEL_W = 848;
const PANEL_T = 220;
const PANEL_B = 800;

const PLAY = 26; // 开始"演唱"
const CHAR_DUR = 11; // 右半每个字之间的间隔（帧）

const PUSH_A = 116; // 收尾：右半放大 / 左半压暗
const PUSH_B = 132;

/* 字块排版（与 shared.CharRun 一致：占位 = fontSize + 12px 内边距 + gap） */
const SIZE = 64;
const GAP = 14;
const SLOT = SIZE + 12 + GAP;
const ROW_W = LINE.length * (SIZE + 12) + (LINE.length - 1) * GAP;
const ROW_X0 = (HALF - ROW_W) / 2 + (SIZE + 12) / 2;

const SENT_T = 380; // 句子顶部
const BAR_T = 352; // 左半那个"整段亮"的横条
const BAR_H = 124;
const STRIP_T = 570; // 进度条
const SUB_T = 700; // SPEC 要求的小字

/** 半屏内的第 i 个字中心 */
const charX = (i: number): number => ROW_X0 + i * SLOT;

/* ------------------------------------------------------------------ *
 * 场景
 * ------------------------------------------------------------------ */

export const LineVsChar: React.FC<{frame: number}> = ({frame}) => {
  /* ---- 共同的"演唱"时间轴 ---- */
  const wholeLit = seg(frame, PLAY, PLAY + 10, 0, 1, EASE.outCubic); // 左半：整条一起亮
  const charLit = LINE.map((_c, i) =>
    seg(frame, PLAY + i * CHAR_DUR, PLAY + i * CHAR_DUR + 5, 0, 1, EASE.outCubic),
  );
  const sung = LINE.reduce((n, _c, i) => (frame >= PLAY + i * CHAR_DUR ? n + 1 : n), 0);
  const singing = Math.floor((frame - PLAY) / CHAR_DUR); // 正在唱的那个字（-1 = 还没开始）

  /* ---- 扫描线：从左到右匀速，扫到哪半哪半在播 ---- */
  const playX = seg(frame, 14, 132, 40, 1880);
  const liveLeft = playX < HALF;

  /* ---- 收尾 ---- */
  const push = seg(frame, PUSH_A, PUSH_B, 0, 1, EASE.outCubic);

  /* ---- 进场 ---- */
  const panelIn = appear(frame, 4, 24);
  const dividerIn = appear(frame, 8, 20);
  const pulse = 0.4 + 0.6 * Math.abs(Math.sin(frame * 0.18));

  const liveDot = (live: boolean, color: string): React.CSSProperties => ({
    width: 12,
    height: 12,
    borderRadius: '50%',
    background: live ? color : 'rgba(255,255,255,0.14)',
    boxShadow: live ? `0 0 14px ${hexA(color, 0.85)}` : undefined,
    opacity: live ? pulse : 1,
  });

  return (
    <AbsoluteFill style={stage(C.bgB, BG.cyanSoft)}>
      <CornerLabel frame={frame} title="对比" sub="整行高亮 vs 逐字高亮" />

      {/* =========================== 左半：旧做法 =========================== */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: HALF,
          height: 1080,
          opacity: (1 - 0.55 * push) * panelIn,
        }}
      >
        <div
          style={glowLayer(
            'radial-gradient(60% 42% at 50% 46%, rgba(245,158,11,0.08) 0%, transparent 72%)',
          )}
        />
        {/* 面板 */}
        <div
          style={{
            position: 'absolute',
            left: PANEL_L,
            top: PANEL_T + riseIn(frame, 4, 24, 18),
            width: PANEL_W,
            height: PANEL_B - PANEL_T,
            borderRadius: 18,
            border: `1px solid ${
              liveLeft ? hexA(WARM, 0.45) : 'rgba(255,255,255,0.09)'
            }`,
            background: liveLeft ? 'rgba(245,158,11,0.03)' : 'transparent',
            boxSizing: 'border-box',
          }}
        />

        {/* 小标题 + "在播"指示 */}
        <div
          style={{
            position: 'absolute',
            left: PANEL_L + 60,
            top: PANEL_T + 44,
            display: 'flex',
            alignItems: 'center',
            gap: 14,
          }}
        >
          <div style={liveDot(liveLeft, WARM)} />
          <div style={{fontSize: 30, fontWeight: 800, letterSpacing: 8, color: '#e0b071'}}>
            整行高亮
          </div>
          <div style={{fontSize: 18, letterSpacing: 3, color: liveLeft ? '#c99a54' : C.faint}}>
            {liveLeft ? '播放中' : '待播'}
          </div>
        </div>

        {/* 一句话 = 一个横条：整段一起亮 */}
        <div
          style={{
            position: 'absolute',
            left: PANEL_L + 60,
            top: BAR_T,
            width: PANEL_W - 120,
            height: BAR_H,
            borderRadius: 12,
            background: hexA(WARM, 0.05 + 0.2 * wholeLit),
            border: `1px solid ${hexA(WARM, 0.2 + 0.5 * wholeLit)}`,
            boxShadow: wholeLit > 0.5 ? `0 0 40px ${hexA(WARM, 0.18)}` : undefined,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: PANEL_L,
            top: SENT_T,
            width: PANEL_W,
          }}
        >
          <CharRun
            text="夜色落在窗台上"
            lit={LINE.map(() => wholeLit)}
            size={SIZE}
            gap={GAP}
            boxFill={false}
            onColor="#ffe6bd"
            offColor="#6c5b45"
          />
        </div>

        {/* 进度：一个整块，要么 0 要么 100% */}
        <div
          style={{
            position: 'absolute',
            left: PANEL_L + 60,
            top: STRIP_T,
            width: PANEL_W - 120,
            height: 8,
            borderRadius: 4,
            background: 'rgba(255,255,255,0.07)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${wholeLit * 100}%`,
              height: '100%',
              background: hexA(WARM, 0.75),
              boxShadow: `0 0 16px ${hexA(WARM, 0.5)}`,
            }}
          />
        </div>
        <div
          style={{
            position: 'absolute',
            left: PANEL_L + 60,
            top: STRIP_T + 22,
            fontSize: 17,
            letterSpacing: 2,
            color: '#a98a5c',
            ...TABULAR,
          }}
        >
          1 / 1 时间点 · 整句一个区间
        </div>

        <div
          style={{
            position: 'absolute',
            left: PANEL_L,
            top: SUB_T,
            width: PANEL_W,
            textAlign: 'center',
            fontSize: 22,
            letterSpacing: 4,
            color: '#9a8060',
          }}
        >
          整行高亮 · 唱到哪一句亮哪一句
        </div>
      </div>

      {/* =========================== 右半：逐字 =========================== */}
      <div
        style={{
          position: 'absolute',
          left: HALF,
          top: 0,
          width: HALF,
          height: 1080,
          opacity: panelIn,
          transform: `scale(${1 + 0.06 * push})`,
        }}
      >
        <div
          style={glowLayer(
            'radial-gradient(60% 42% at 50% 46%, rgba(34,211,238,0.10) 0%, transparent 72%)',
          )}
        />
        <div
          style={{
            position: 'absolute',
            left: PANEL_L,
            top: PANEL_T + riseIn(frame, 4, 24, 18),
            width: PANEL_W,
            height: PANEL_B - PANEL_T,
            borderRadius: 18,
            border: `1px solid ${
              liveLeft ? 'rgba(255,255,255,0.09)' : hexA(C.cyan, 0.5)
            }`,
            background: liveLeft ? 'transparent' : 'rgba(34,211,238,0.03)',
            boxShadow: push > 0.2 ? `0 0 ${60 * push}px ${hexA(C.cyan, 0.16)}` : undefined,
            boxSizing: 'border-box',
          }}
        />

        <div
          style={{
            position: 'absolute',
            left: PANEL_L + 60,
            top: PANEL_T + 44,
            display: 'flex',
            alignItems: 'center',
            gap: 14,
          }}
        >
          <div style={liveDot(!liveLeft, C.cyan)} />
          <div style={{fontSize: 30, fontWeight: 800, letterSpacing: 8, color: C.cyanPale}}>
            逐字高亮
          </div>
          <div
            style={{
              fontSize: 18,
              letterSpacing: 3,
              color: liveLeft ? C.faint : hexA(C.cyan, 0.9),
            }}
          >
            {liveLeft ? '待播' : '播放中'}
          </div>
        </div>

        {/* 同一句话：逐字依次点亮 */}
        <div style={{position: 'absolute', left: PANEL_L, top: SENT_T, width: PANEL_W}}>
          <CharRun
            text="夜色落在窗台上"
            lit={charLit}
            size={SIZE}
            gap={GAP}
            onColor={C.cyanPale}
            offColor="#39445c"
          />
        </div>

        {/* 每字一个小方块：正在唱的那个更亮 */}
        {LINE.map((_c, i) => {
          const p = charLit[i];
          const now = singing === i;
          const s = now ? 16 : 13;
          return (
            <React.Fragment key={`sq${i}`}>
              <div
                style={{
                  position: 'absolute',
                  left: charX(i) - s / 2,
                  top: SENT_T + SIZE + 28 - (s - 13) / 2,
                  width: s,
                  height: s,
                  borderRadius: 4,
                  background: hexA(C.cyan, 0.25 + 0.7 * p),
                  border: `1px solid ${hexA(C.cyan, 0.35 + 0.6 * p)}`,
                  boxShadow: now
                    ? `0 0 22px ${hexA(C.cyan, 0.9)}`
                    : p > 0.5
                      ? `0 0 10px ${hexA(C.cyan, 0.35)}`
                      : undefined,
                  transform: `scale(${0.5 + 0.5 * p})`,
                }}
              />
              {i < LINE.length - 1 ? (
                <div
                  style={{
                    position: 'absolute',
                    left: charX(i) + SLOT / 2,
                    top: SENT_T + SIZE + 32,
                    width: GAP + 6,
                    height: 1,
                    background: now ? hexA(C.cyan, 0.6) : 'rgba(255,255,255,0.07)',
                  }}
                />
              ) : null}
            </React.Fragment>
          );
        })}

        {/* 进度：七个格子，一个一个填 */}
        <div
          style={{
            position: 'absolute',
            left: PANEL_L + 60,
            top: STRIP_T,
            width: PANEL_W - 120,
            display: 'flex',
            gap: 8,
          }}
        >
          {LINE.map((_c, i) => (
            <div
              key={`g${i}`}
              style={{
                flex: 1,
                height: 8,
                borderRadius: 4,
                background:
                  charLit[i] > 0.02 ? hexA(C.cyan, 0.3 + 0.5 * charLit[i]) : 'rgba(255,255,255,0.07)',
                boxShadow: singing === i ? `0 0 16px ${hexA(C.cyan, 0.7)}` : undefined,
              }}
            />
          ))}
        </div>
        <div
          style={{
            position: 'absolute',
            left: PANEL_L + 60,
            top: STRIP_T + 22,
            fontSize: 17,
            letterSpacing: 2,
            color: hexA(C.cyan, 0.85),
            ...TABULAR,
          }}
        >
          {sung} / 7 时间点 · 每个字一个区间
        </div>

        <div
          style={{
            position: 'absolute',
            left: PANEL_L,
            top: SUB_T,
            width: PANEL_W,
            textAlign: 'center',
            fontSize: 22,
            letterSpacing: 4,
            color: C.dim,
          }}
        >
          逐字高亮 · 唱到哪个字亮哪个字
        </div>
      </div>

      {/* =========================== 中缝竖线 + 扫描线 =========================== */}
      <div
        style={{
          position: 'absolute',
          left: HALF - 1,
          top: 200,
          width: 1,
          height: 620,
          background: `linear-gradient(180deg, ${hexA(C.white, 0)} 0%, ${hexA(
            C.white,
            0.22,
          )} 18%, ${hexA(C.white, 0.22)} 82%, ${hexA(C.white, 0)} 100%)`,
          opacity: dividerIn,
        }}
      />

      <div
        style={{
          position: 'absolute',
          left: playX - 40,
          top: 190,
          width: 80,
          height: 640,
          background: `linear-gradient(90deg, ${hexA(C.white, 0)} 0%, ${hexA(
            C.white,
            0.05,
          )} 50%, ${hexA(C.white, 0)} 100%)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: playX - 1,
          top: 200,
          width: 2,
          height: 620,
          background: '#eaf6ff',
          boxShadow: `0 0 20px ${hexA(C.cyan, 0.85)}`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: playX - 8,
          top: 182,
          width: 16,
          height: 16,
          transform: 'rotate(45deg)',
          background: '#eaf6ff',
          boxShadow: `0 0 18px ${hexA(C.cyan, 0.9)}`,
        }}
      />

      <Caption frame={frame} text="同一句话 · 同一条时间轴 —— 差别只在粒度" at={40} />
    </AbsoluteFill>
  );
};
