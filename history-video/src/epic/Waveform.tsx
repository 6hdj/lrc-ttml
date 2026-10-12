/**
 * 场 3 · 声音（场内 0–164，5.5s）
 *
 * 形式：底部一条人声波形，用 `rnd()` 造 140 根竖条从左到右逐根长出来（125 帧长满）；
 *       波形上方「夜色落在窗台上」七个字，**按波形扫过的顺序逐字亮起**（青底 + 青字），
 *       每个字亮起时从字的位置向下拉一条细线连到它对应的那根波形柱 —— "这个字在这一刻"。
 *
 * 帧设计（动画在 147 帧内做完，最后 12 帧留给总装叠化）：
 *   6–131   波形从左长到右；字随波形前沿依次亮起
 *   131–    长满后停住（最后 12 帧是总装叠化）
 *
 * 取舍：字块用 `shared.CharRun` 画（它自带"青底 + 青字"的逐字高亮），
 *       因此这排字是**居中排布**的；连线与波形柱的位置按 CharRun 的排版公式
 *       （每字占位 = fontSize + 12px 内边距 + gap，中日韩字形步进 = 1em）反推，
 *       保证线落在字中心、柱子落在字下方。见 `charCenter()`。
 *
 * 对应 SPEC.md §3「场 3」。所有数值都是帧的纯函数（无 Math.random / Date.now）。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../constants';
import {rnd} from './timing';
import {
  BG,
  CharRun,
  CornerLabel,
  EASE,
  Readout,
  TABULAR,
  appear,
  glowLayer,
  glowText,
  hexA,
  seg,
  stage,
} from './shared';

/* ------------------------------------------------------------------ *
 * 数据与几何
 * ------------------------------------------------------------------ */

const LINE = Array.from('夜色落在窗台上'); // 7 个字

const BARS = 140; // 竖条数
const BAR_X = 180; // 波形左端
const WAVE_W = 1560; // 波形宽度（180 → 1740）
const STEP = WAVE_W / BARS; // 每根竖条的步进
const BAR_W = 7; // 竖条宽度
const RY = 900; // 波形基线
const MAX_H = 300; // 最高一根柱子

/** 这条波形代表的音频时长（秒）—— 和场 2 的 00:12.480 呼应 */
const CLIP = 12.48;

const GROW_A = 6;
const GROW_B = 131; // 125 帧长满

/** 波形前沿（还能前进到 BARS 根） */
const grow = (f: number): number => seg(f, GROW_A, GROW_B, 0, BARS);

/**
 * 幅度包络：**先平后起伏** —— 前 20% 是还没开口的底噪，
 * 之后进入人声乐句；`rnd()` 出颗粒感，正弦出换气的起伏。
 */
const tone = (i: number): number => {
  const u = i / (BARS - 1);
  const ramp = Math.min(1, Math.max(0, (u - 0.2) / 0.42));
  const grain = 0.42 + 0.58 * rnd(i * 3 + 1);
  const phrase = 0.5 + 0.5 * Math.sin(u * Math.PI * 6.5 - 0.6);
  return 0.08 + 0.92 * ramp * grain * (0.55 + 0.45 * phrase);
};

/** 第 i 根柱子的生长进度 / 当前高度 */
const barP = (i: number, f: number): number => seg(grow(f), i, i + 5, 0, 1, EASE.outCubic);
const barH = (i: number, f: number): number => MAX_H * tone(i) * barP(i, f);

/* CharRun 的排版：每字占位 = fontSize + 左右内边距(2×6) + gap */
const CH_SIZE = 76;
const CH_GAP = 18;
const CH_PAD = 12;
const CH_SLOT = CH_SIZE + CH_PAD + CH_GAP;
const CH_ROW_W = LINE.length * (CH_SIZE + CH_PAD) + (LINE.length - 1) * CH_GAP;
const CH_ROW_X = (1920 - CH_ROW_W) / 2;
const CH_TOP = 330;

/** 第 i 个字在画面里的中心 x（与 CharRun 的居中排布一致） */
const charCenter = (i: number): number => CH_ROW_X + i * CH_SLOT + (CH_SIZE + CH_PAD) / 2;

/** 第 i 个字对应的波形柱（取字正下方那一根） */
const barOf = (i: number): number => Math.max(0, Math.round((charCenter(i) - BAR_X - BAR_W / 2) / STEP));

/** 字亮起来的那一刻 = 波形前沿扫到它下面那根柱子时 */
const litAt = (i: number): number => barOf(i) + 1;

const pad = (n: number, w: number): string => String(Math.floor(n)).padStart(w, '0');
const clock = (t: number): string => {
  const v = Math.max(0, t);
  const whole = Math.floor(v);
  return `${pad(whole / 60, 2)}:${pad(whole % 60, 2)}.${pad(Math.round((v - whole) * 1000), 3)}`;
};

/* ------------------------------------------------------------------ *
 * 场景
 * ------------------------------------------------------------------ */

export const Waveform: React.FC<{frame: number}> = ({frame}) => {
  const g = grow(frame);
  const frontX = BAR_X + Math.min(g, BARS) * STEP;
  const elapsed = (Math.min(g, BARS) / BARS) * CLIP;
  /** 每个字的亮起进度 0→1：波形前沿扫到它下面那根柱子时开始 */
  const progress = LINE.map((_c, i) => seg(g, litAt(i), litAt(i) + 5, 0, 1, EASE.outCubic));
  const axisIn = appear(frame, 30, 26);
  /** 前沿那根柱子的当前高度（前沿竖线跟着它长） */
  const fH = barH(Math.min(BARS - 1, Math.max(0, Math.floor(g))), frame);

  return (
    <AbsoluteFill style={stage(C.bgA)}>
      {/* 波形后面的环境光 */}
      <div style={glowLayer(BG.cyanSoft)} />
      <div
        style={glowLayer(
          'radial-gradient(72% 34% at 50% 86%, rgba(34,211,238,0.10) 0%, transparent 72%)',
        )}
      />

      <CornerLabel frame={frame} title="声音" sub="时间戳全部来自真实音频，不是按字数估算" />

      <Readout
        frame={frame}
        rows={['faster-whisper large-v3', 'beam 5', 'language 自动检测']}
      />

      {/* 顶部：转写进度（跟波形前沿同一条进度） */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: 172,
          textAlign: 'center',
          opacity: axisIn,
        }}
      >
        <div style={{fontSize: 19, letterSpacing: 10, color: C.dim}}>语音转写 · 逐字时间戳</div>
        <div
          style={{
            margin: '18px auto 0',
            width: 760,
            height: 2,
            background: 'rgba(255,255,255,0.08)',
            borderRadius: 2,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${(Math.min(g, BARS) / BARS) * 100}%`,
              height: '100%',
              background: hexA(C.cyan, 0.8),
              boxShadow: `0 0 12px ${hexA(C.cyan, 0.6)}`,
            }}
          />
        </div>
        <div style={{marginTop: 12, fontSize: 15, letterSpacing: 3, color: C.faint, ...TABULAR}}>
          {`已识别 ${LINE.filter((_c, i) => progress[i] > 0.5).length} / ${LINE.length} 字`}
        </div>
      </div>

      {/* ---------------- 字 → 波形柱的细线 ---------------- */}
      {LINE.map((_c, i) => {
        const p = progress[i];
        const b = barOf(i);
        const bottom = RY - barH(b, frame) - 8;
        const top = CH_TOP + CH_SIZE + 16;
        return (
          <div
            key={`ln${i}`}
            style={{
              position: 'absolute',
              left: charCenter(i),
              top,
              width: 1,
              height: Math.max(0, bottom - top) * p,
              background: hexA(C.cyan, 0.45 * p),
            }}
          >
            <div
              style={{
                position: 'absolute',
                left: -3,
                bottom: -3,
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: hexA(C.cyan, 0.95 * p),
                boxShadow: `0 0 12px ${hexA(C.cyan, 0.7 * p)}`,
              }}
            />
          </div>
        );
      })}

      {/* ---------------- 逐字亮起的歌词 ---------------- */}
      <div style={{position: 'absolute', left: 0, right: 0, top: CH_TOP}}>
        <CharRun
          text="夜色落在窗台上"
          lit={progress}
          size={CH_SIZE}
          gap={CH_GAP}
          onColor={C.cyanPale}
          offColor="#39445c"
        />
      </div>

      {/* ---------------- 波形：140 根竖条从左长出来 ---------------- */}
      {Array.from({length: BARS}).map((_v, i) => {
        const p = barP(i, frame);
        const h = MAX_H * tone(i) * p;
        return (
          <div
            key={`b${i}`}
            style={{
              position: 'absolute',
              left: BAR_X + i * STEP,
              top: RY - h,
              width: BAR_W,
              height: h,
              borderRadius: BAR_W / 2,
              background: `linear-gradient(180deg, ${hexA(C.cyan, 0.95)} 0%, ${hexA(
                C.cyan,
                0.32,
              )} 100%)`,
              opacity: 0.18 + 0.82 * p,
            }}
          />
        );
      })}

      {/* 基线：已扫过的部分亮起来 */}
      <div
        style={{
          position: 'absolute',
          left: BAR_X,
          top: RY,
          width: WAVE_W,
          height: 1,
          background: `linear-gradient(90deg, ${hexA(C.cyan, 0.5)} 0%, ${hexA(
            C.cyan,
            0.5,
          )} ${(Math.min(g, BARS) / BARS) * 100}%, rgba(255,255,255,0.10) ${(Math.min(g, BARS) / BARS) * 100}%, rgba(255,255,255,0.10) 100%)`,
        }}
      />

      {/* 波形前沿（就是"正在唱到哪儿"）：高度跟着当前那根柱子，读起来像一支笔尖 */}
      <div
        style={{
          position: 'absolute',
          left: frontX - 1,
          top: RY - fH - 84,
          width: 2,
          height: fH + 116,
          background: hexA(C.cyan, 0.9),
          boxShadow: `0 0 20px ${hexA(C.cyan, 0.75)}`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: frontX - 30,
          top: RY - fH - 114,
          width: 60,
          height: 60,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${hexA(C.cyan, 0.5)} 0%, ${hexA(
            C.cyan,
            0,
          )} 70%)`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: frontX - 70,
          top: RY + 54,
          width: 140,
          textAlign: 'center',
          fontSize: 20,
          letterSpacing: 2,
          color: C.cyanPale,
          opacity: axisIn,
          textShadow: glowText(C.cyan, 26, 0.5),
          ...TABULAR,
        }}
      >
        {clock(elapsed)}
      </div>

      {/* ---------------- 波形下的时间刻度 ---------------- */}
      <div style={{position: 'absolute', inset: 0, opacity: axisIn}}>
        <div
          style={{
            position: 'absolute',
            left: BAR_X,
            top: RY + 22,
            width: WAVE_W,
            height: 1,
            background: C.line,
          }}
        />
        {Array.from({length: 7}).map((_v, k) => {
          const s = k * 2;
          const x = BAR_X + (s / CLIP) * WAVE_W;
          return (
            <React.Fragment key={`ax${k}`}>
              <div
                style={{
                  position: 'absolute',
                  left: x,
                  top: RY + 22,
                  width: 1,
                  height: 10,
                  background: s / CLIP <= Math.min(g, BARS) / BARS ? C.axis : C.line,
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  left: x,
                  top: RY + 38,
                  transform: 'translateX(-50%)',
                  fontSize: 15,
                  color: C.faint,
                  ...TABULAR,
                }}
              >
                {`00:${pad(s, 2)}`}
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
