/**
 * 场 8 · 编辑台（场内 0–209，7s）
 *
 * 形式：一个简化但**真实**的编辑台界面（不是编出来的假 UI）+ 一个发光光标在里面干活。
 *
 * 画进去的每一件东西在仓库的 `index.html` 里都能找到出处：
 *   · 三个标签 `⏱ 时间轴 / 👥 声部（谁唱的） / ✏️ 文本（插行/改字/和声）`（`#tabTime/#tabAgent/#tabText`）；
 *   · 播放控制条 `▶ 播放 / 时间读数 / 拖动定位条 / ⟲3s / 3s⟳`（`.editor-player`）；
 *   · 逐字读数 `L12 · 00:12.400 · 时长 0.140s`（`nowInfo` 那行读数的同构）；
 *   · 每行的 `L12` 行号 + `v1/v2` 声部标签 + 逐字 chip（`.ed-w`）；
 *   · 底部那条波形是`（底部一条波形进度条）`的示意，不是播放器的真实控件。
 * ⚠️ 只画真实存在的东西：不发明按钮、不发明数值。
 *
 * 动画全部在 192 帧内做完（duration-18），之后只有光标在面板里游走。
 * 对应 SPEC.md §3「场 8」。task-6。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C, FONT} from '../constants';
import {EASE, TABULAR, hexA, stage} from './shared';
import {rnd} from './timing';

/* ------------------------------------------------------------------ *
 * 数据：五行真实歌词（行号 L12–L16、声部、逐字状态）
 * ------------------------------------------------------------------ */

type Line = {
  n: number;
  agent: string;
  chars: string[];
  /** 该行第几号字是"低可信"（黄色）—— 三个标签页同等可见的状态色 */
  weak?: number;
  /** 该行第几号字是"插值补的"（红色）—— 匹配失败、最该人工看一眼 */
  interp?: number;
};

/** 行 12 就是读数里那一行：`L12 · 00:12.400 · 时长 0.140s` */
const LINES: Line[] = [
  {n: 12, agent: 'v1', chars: ['夜', '色', '落', '在', '窗'], weak: 1},
  {n: 13, agent: 'v1', chars: ['台', '上', '把', '歌', '词']},
  {n: 14, agent: 'v1', chars: ['做', '成', '逐', '字', '时']},
  {n: 15, agent: 'v1', chars: ['间', '轴', '对', '齐', '单'], interp: 2},
  {n: 16, agent: 'v1', chars: ['元', '全', '部', '对', '上']},
];

/** 被改成 v2 的行（中间三行）—— 对应编辑台"点行号选起点、再 Shift 点终点批量改声部" */
const CONVERT = [14, 15, 16];

const TABS = ['⏱ 时间轴', '👥 声部（谁唱的）', '✏️ 文本（插行/改字/和声）'];

/* 面板几何（面板左上角在画布 (150, 232)） */
const PW = 1620;
const PH = 690;
const PX = 150;
const PY = 232;
/** 行内容起点 / 行高 / 行间距（行内绝对定位的行号、声部标签、逐字芯片都按它算） */
const X0 = 26;
const ROWH = 44;
const ROWGAP = 9;
/** 逐字芯片：左起点 + 步距 */
const CHIP0 = 164;
const CHIPW = 56;
const CHIPSTEP = 70;
/** 行号列中心（框选起点/终点就点这里） */
const ROWNO_X = X0 + 24;

const wave = (k: number): number => {
  const v =
    0.36 +
    0.3 * Math.sin(k * 0.55) * Math.sin(k * 0.17 + 1.3) +
    0.16 * Math.sin(k * 0.13 + 0.5) +
    0.34 * rnd(k);
  return Math.max(0.2, Math.min(1, v));
};

/* ------------------------------------------------------------------ *
 * 光标路径：一串航点（相对面板），每点带"移动到这里的帧数"
 * ------------------------------------------------------------------ */

type Pt = {
  f: number;
  x: number;
  y: number;
  /** 从上一个航点滑到这里用多少帧 */
  mv?: number;
  /** 这一站是不是"点了一下" */
  click?: boolean;
  /** 这一站是不是"框选" */
  box?: boolean;
};

/** 一次点击的环形扩散由 `PULSES` 描述（t 用航点帧，即分段起点） */
const PULSES = [
  {t: 8, x: 133, y: 79},
  {t: 22, x: 432, y: 79},
  {t: 50, x: X0 + CHIP0 + CHIPSTEP, y: 38},
  {t: 58, x: X0 + CHIP0 + 2 * CHIPSTEP, y: 38},
  {t: 66, x: X0 + CHIP0 + 3 * CHIPSTEP, y: 38},
  {t: 74, x: X0 + CHIP0 + 4 * CHIPSTEP, y: 38},
  {t: 88, x: X0 + CHIP0 + 5 * CHIPSTEP, y: ROWGAP + ROWH + 38},
  {t: 96, x: ROWNO_X, y: 2 * (ROWGAP + ROWH) + 22},
  {t: 104, x: ROWNO_X, y: 3 * (ROWGAP + ROWH) + 22},
  {t: 112, x: ROWNO_X, y: 4 * (ROWGAP + ROWH) + 22},
  {t: 118, x: 222, y: 2 * (ROWGAP + ROWH) + 22},
  {t: 130, x: 222, y: 3 * (ROWGAP + ROWH) + 22},
  {t: 142, x: 222, y: 4 * (ROWGAP + ROWH) + 22},
  {t: 156, x: 420, y: 610},
];

const WAYPOINTS: Pt[] = [
  {f: 0, x: 116, y: 96, mv: 18},
  {f: 8, x: 133, y: 79, mv: 12, click: true}, // 点一下「时间轴」
  {f: 22, x: 432, y: 79, mv: 16, click: true}, // 切到「声部（谁唱的）」
  {f: 36, x: 356, y: 38, mv: 18}, // 落到 L12 第一个字
  {f: 50, x: 426, y: 38, mv: 8, click: true}, // 点「色」——跳转播放
  {f: 58, x: 496, y: 38, mv: 8, click: true}, // 点「落」
  {f: 66, x: 566, y: 38, mv: 8, click: true}, // 点「在」
  {f: 74, x: 636, y: 38, mv: 8, click: true}, // 点「窗」
  {f: 84, x: 566, y: 118, mv: 12}, // 下移到 L13「把」
  {f: 88, x: 496, y: 118, mv: 8, click: true}, // 点「上」
  {f: 96, x: ROWNO_X, y: 128, mv: 8, click: true}, // 点 L14 行号 = 框选起点
  {f: 104, x: ROWNO_X, y: 181, mv: 8, click: true},
  {f: 112, x: ROWNO_X, y: 234, mv: 8, click: true}, // 点 L16 行号 = 终点
  {f: 118, x: 222, y: 128, mv: 8, click: true}, // 逐个改声部：L14 v1→v2
  {f: 130, x: 222, y: 181, mv: 8, click: true}, // L15
  {f: 142, x: 222, y: 234, mv: 8, click: true}, // L16（192 帧内全部做完）
  {f: 156, x: 420, y: 610, mv: 14}, // 下到波形进度条
  {f: 172, x: 1090, y: 610, mv: 44}, // 顺着波形慢慢走
  {f: 196, x: 1462, y: 648, mv: 24}, // 停在进度条右端，安静收尾
];

/** 找到当前正处在哪一段航点之间（返回这一段的两端） */
const segAt = (f: number): {a: Pt; p: Pt} => {
  let a = WAYPOINTS[0];
  let p = WAYPOINTS[0];
  for (const w of WAYPOINTS) {
    if (w.f > f) {
      break;
    }
    a = p;
    p = w;
  }
  return {a, p};
};

/** 光标位置：每段用自己的缓动，不是一条全局直线 */
const cursorAt = (f: number): {x: number; y: number} => {
  const {a, p} = segAt(f);
  const dur = p.mv ?? 12;
  const e = EASE.outCubic(Math.max(0, Math.min(1, (f - p.f) / dur)));
  return {x: a.x + (p.x - a.x) * e, y: a.y + (p.y - a.y) * e};
};

/** 用一个 0→1→0 的脉冲做"点了一下"的反馈 */
const pulse = (f: number, t: number, dur = 14): number => {
  const u = Math.max(0, Math.min(1, (f - t) / dur));
  return u <= 0 || u >= 1 ? 0 : Math.sin(Math.PI * u);
};

/** 一整段（含移动过程）的进度 0..1 */
const segSpan = (f: number, a: number, b: number): number =>
  Math.max(0, Math.min(1, (f - a) / (b - a)));

export const Editor: React.FC<{frame: number}> = ({frame}) => {
  const f = frame;
  const {p: cur} = segAt(f);
  const cpos = cursorAt(f);

  /* —— 光标当前压在谁身上 —— */
  const over = cur.click || cur.box ? cur : null;
  const onTab = over && over.y < 110 ? (over.x < 290 ? 0 : over.x < 490 ? 1 : 2) : -1;
  const onRowNo = !!over && over.x < 150 && over.y > 110;
  const rowIdx = (y: number): number =>
    Math.max(0, Math.min(LINES.length - 1, Math.floor((y - 10) / (ROWGAP + ROWH))));
  const onChipRow = !!over && over.x > 150 && over.x < 1500 && over.y < 290 ? rowIdx(over.y) : -1;
  const chipCol =
    onChipRow >= 0 && over
      ? Math.max(0, Math.min(4, Math.round((over.x - (X0 + CHIP0)) / CHIPSTEP)))
      : -1;
  const onAgent = onRowNo;

  /* —— 框选与批量改声部 —— */
  const boxOn = f >= 96 && f < 158;
  const boxO = Math.min(1, segSpan(f, 96, 104)) * (1 - segSpan(f, 150, 158));
  const rowOn = (i: number): number => {
    const a = 96 + i * 8;
    if (!boxOn || f < a) {
      return 0;
    }
    return 1 - segSpan(f, 150, 158);
  };
  const conv = (n: number, i: number): number =>
    CONVERT.indexOf(n) < 0 ? 0 : Math.max(0, Math.min(1, (f - (118 + i * 12)) / 10));

  /* —— 底部波形进度条 —— */
  const grow = (k: number): number => Math.max(0, Math.min(1, (f - 24 - k * 0.9) / 24));
  const sweep = segSpan(f, 0, 176);

  const panelO = Math.max(0, Math.min(1, f / 18));
  const panelRise = 26 * (1 - EASE.outCubic(Math.max(0, Math.min(1, f / 26))));

  const readO = Math.max(0, Math.min(1, (f - 30) / 20));

  /* 顶部标签：当前激活 + 刚点过的暖光 */
  const tabGlow = (i: number): number => {
    const on = i === 1 ? 1 - segSpan(f, 22, 34) : i === 0 ? segSpan(f, 8, 20) : 0;
    const clk = i === 0 ? pulse(f, 8, 14) : i === 1 ? pulse(f, 22, 14) : 0;
    return Math.max(on, clk);
  };
  const tabActive = f >= 22 ? 1 : 0;

  /* 读数就是 SPEC 里那一条（示例值，和播放控制条上的时间读数一致） */
  const ms = (t: number): string => {
    const m = Math.floor(t / 60);
    const s = t - m * 60;
    const whole = Math.floor(s);
    const milli = Math.round((s - whole) * 1000);
    return `${String(m).padStart(2, '0')}:${String(whole).padStart(2, '0')}.${String(
      milli,
    ).padStart(3, '0')}`;
  };

  const chipW = 44;
  const chipH = ROWH - 8;

  return (
    <AbsoluteFill style={stage(C.bgA)}>
      <AbsoluteFill style={{background: 'radial-gradient(62% 52% at 58% 44%, rgba(34,211,238,0.075) 0%, transparent 72%)'}} />

      {/* 角标：固定在左上角，和其余九场同一个位置 */}
      <div
        style={{
          position: 'absolute',
          left: 96,
          top: 78,
          opacity: readO,
          transform: `translateY(${26 * (1 - readO)}px)`,
        }}
      >
        <div style={{fontSize: 44, fontWeight: 800, color: C.text, letterSpacing: 6}}>编辑台</div>
        <div style={{fontSize: 19, color: C.dim, letterSpacing: 4, marginTop: 12}}>
          改字 · 改时间 · 改声部
        </div>
      </div>

      {/* ============ 编辑台面板 ============ */}
      <div
        style={{
          position: 'absolute',
          left: PX,
          top: PY,
          width: PW,
          height: PH,
          opacity: panelO,
          transform: `translateY(${panelRise}px)`,
          borderRadius: 18,
          border: `1px solid ${hexA(C.cyan, 0.14)}`,
          background: 'linear-gradient(180deg, #070c18 0%, #04060e 100%)',
          boxShadow: `0 40px 120px rgba(0,0,0,0.6), inset 0 0 90px ${hexA(C.cyan, 0.035)}`,
          overflow: 'hidden',
        }}
      >
        {/* 颗粒感（参考片的噪点底） */}
        <svg width={PW} height={PH} style={{position: 'absolute', left: 0, top: 0, opacity: 0.05}}>
          <filter id="edGrain">
            <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves={3} seed={7} />
            <feColorMatrix type="saturate" values="0" />
          </filter>
          <rect width={PW} height={PH} filter="url(#edGrain)" />
        </svg>

        {/* 标题行 */}
        <div style={{position: 'absolute', left: 26, top: 0, display: 'flex', alignItems: 'center', height: 56}}>
          <span style={{fontSize: 21, color: '#2a3750'}}>▾</span>
          <span style={{fontSize: 18, color: C.text, fontWeight: 700, letterSpacing: 3, marginLeft: 14}}>
            逐字时间轴
          </span>
          <span style={{fontSize: 15, color: C.faint, marginLeft: 18, letterSpacing: 1}}>
            点任意一个字的时间即可改；改完自动跑时间轴修复
          </span>
          <span
            style={{
              marginLeft: 16,
              fontSize: 13,
              color: C.dim,
              border: `1px solid ${hexA('#ffffff', 0.1)}`,
              borderRadius: 999,
              padding: '3px 11px',
              ...TABULAR,
            }}
          >
            v7
          </span>
        </div>
        <div style={{position: 'absolute', left: 0, top: 56, width: PW, height: 1, background: C.line}} />

        {/* 三个标签 */}
        {TABS.map((tb, i) => {
          const on = i === tabActive;
          const g = tabGlow(i);
          return (
            <div
              key={tb}
              style={{
                position: 'absolute',
                left: 26 + i * 300,
                top: 70,
                height: 32,
                padding: '0 18px',
                display: 'flex',
                alignItems: 'center',
                fontSize: 17,
                letterSpacing: 1,
                borderRadius: 9,
                color: on ? '#04121a' : C.dim,
                fontWeight: on ? 700 : 400,
                background: on ? C.cyan : hexA('#ffffff', 0.045),
                border: `1px solid ${on ? hexA(C.cyan, 0.9) : hexA('#ffffff', 0.07)}`,
                boxShadow: `0 0 ${10 + 26 * g}px ${hexA(C.cyan, 0.18 + 0.42 * g)}`,
              }}
            >
              {tb}
            </div>
          );
        })}

        {/* 播放控制条（编辑台里真实存在的一排：▶ 播放 / 读数 / 拖动定位 / ⟲3s / 3s⟳） */}
        <div
          style={{
            position: 'absolute',
            left: 26,
            top: 116,
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            fontSize: 14,
            color: C.dim,
          }}
        >
          <span
            style={{
              border: `1px solid ${hexA('#ffffff', 0.1)}`,
              borderRadius: 8,
              padding: '5px 12px',
              background: hexA('#ffffff', 0.04),
              letterSpacing: 1,
            }}
          >
            ▶ 播放
          </span>
          <span
            style={{
              fontFamily: FONT,
              border: `1px solid ${hexA('#ffffff', 0.1)}`,
              borderRadius: 8,
              padding: '5px 12px',
              background: hexA('#ffffff', 0.03),
              letterSpacing: 1,
            }}
          >
            ⟲3s
          </span>
          <span
            style={{
              border: `1px solid ${hexA('#ffffff', 0.1)}`,
              borderRadius: 8,
              padding: '5px 12px',
              background: hexA('#ffffff', 0.03),
              letterSpacing: 1,
            }}
          >
            3s⟳
          </span>
          {/* 拖动定位条 */}
          <div style={{position: 'relative', width: 545, height: 4, borderRadius: 2, background: hexA('#ffffff', 0.1), marginLeft: 8}}>
            <div
              style={{
                position: 'absolute',
                left: 0,
                top: 0,
                width: 545 * (0.06 + 0.94 * sweep),
                height: 4,
                borderRadius: 2,
                background: hexA(C.cyan, 0.55),
              }}
            />
            <div
              style={{
                position: 'absolute',
                left: 545 * (0.06 + 0.94 * sweep) - 5,
                top: -3,
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: C.cyanPale,
                boxShadow: `0 0 12px ${hexA(C.cyan, 0.8)}`,
              }}
            />
          </div>
          <span style={{...TABULAR, color: C.faint, letterSpacing: 1}}>00:12.400 / 03:41.200</span>
        </div>

        {/* ============ 右上角：播放读数 ============ */}
        <div
          style={{
            position: 'absolute',
            right: 40,
            top: 158,
            textAlign: 'right',
            opacity: readO,
            transform: `translateY(${10 * (1 - readO)}px)`,
            ...TABULAR,
          }}
        >
          <div style={{fontSize: 23, color: C.cyanPale, letterSpacing: 2}}>
            {`L${LINES[0].n} · ${ms(12.4)} · 时长 0.140s`}
          </div>
          <div style={{fontSize: 17, color: C.dim, letterSpacing: 2, marginTop: 6}}>
            已选 3 行 · 批量改声部
          </div>
        </div>

        {/* ============ 歌词区 ============ */}
        <div style={{position: 'absolute', left: 0, top: 232, width: PW, height: 296}}>
          {LINES.map((ln, i) => {
            const top = i * (ROWGAP + ROWH);
            const on = rowOn(i);
            const c = conv(ln.n, Math.max(0, CONVERT.indexOf(ln.n)));
            return (
              <div
                key={ln.n}
                style={{
                  position: 'absolute',
                  left: 0,
                  top,
                  width: PW,
                  height: ROWH,
                  background: on > 0.02 ? hexA(C.cyan, 0.105 * on) : 'transparent',
                  boxShadow: on > 0.02 ? `inset 0 0 0 1px ${hexA(C.cyan, 0.18 * on)}` : undefined,
                  borderRadius: 10,
                }}
              >
                {/* 行号：点它是框选起点/终点 */}
                <div
                  style={{
                    position: 'absolute',
                    left: ROWNO_X - 24,
                    top: 7,
                    width: 48,
                    height: chipH,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 15,
                    lineHeight: 1,
                    borderRadius: 8,
                    ...TABULAR,
                    letterSpacing: 0,
                    color: onAgent && rowIdx(cur.y) === i && f >= 96 ? C.cyanPale : C.faint,
                    border: `1px solid ${
                      onAgent && rowIdx(cur.y) === i && f >= 96 ? hexA(C.cyan, 0.6) : 'transparent'
                    }`,
                    background: onAgent && rowIdx(cur.y) === i && f >= 96 ? hexA(C.cyan, 0.12) : 'transparent',
                  }}
                >
                  {onAgent && rowIdx(cur.y) === i && f >= 96 ? '◉' : ''}
                  {onAgent && rowIdx(cur.y) === i && f >= 96 ? '' : `L${ln.n}`}
                </div>

                {/* 声部标签 v1 → v2（批量改声部就是改它） */}
                <div style={{position: 'absolute', left: 150, top: 7, width: 64, height: chipH}}>
                  {c > 0.02 ? (
                    <div
                      style={{
                        width: '100%',
                        height: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 16,
                        fontWeight: 700,
                        letterSpacing: 1,
                        borderRadius: 9,
                        background: hexA(C.cyan, 0.14 + 0.28 * c),
                        border: `1px solid ${hexA(C.cyan, 0.55 + 0.4 * c)}`,
                        boxShadow: c > 0.55 ? `0 0 16px ${hexA(C.cyan, 0.4 * c)}` : 'none',
                        color: C.cyanPale,
                        ...TABULAR,
                      }}
                    >
                      {c > 0.5 ? 'v2' : 'v1'}
                    </div>
                  ) : (
                    <div
                      style={{
                        width: '100%',
                        height: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 16,
                        fontWeight: 700,
                        letterSpacing: 1,
                        borderRadius: 9,
                        background: hexA('#ffffff', 0.05),
                        border: `1px solid ${hexA('#ffffff', 0.1)}`,
                        color: C.sub,
                        ...TABULAR,
                      }}
                    >
                      {ln.agent}
                    </div>
                  )}
                </div>

                {/* 逐字芯片 */}
                {ln.chars.map((ch, k) => {
                  const isNow = LINES[0].n === ln.n && k >= 3;
                  const isHover = onChipRow === i && chipCol === k;
                  const ring = isHover ? segSpan(f, cur.f + 6, cur.f + 18) : 0;
                  const weak = ln.weak === k;
                  const interp = ln.interp === k;
                  const bg = isNow
                    ? hexA(C.cyan, 0.24)
                    : interp
                      ? hexA('#ef4444', on * 0.26)
                      : weak
                        ? hexA(C.warm, on * 0.2)
                        : hexA('#ffffff', 0.055);
                  const col = isNow
                    ? '#eafdff'
                    : interp
                      ? '#ffb3b3'
                      : weak
                        ? '#f8d18a'
                        : on > 0.02
                          ? C.text
                          : C.sub;
                  const bd = isNow
                    ? hexA(C.cyan, 0.9)
                    : interp
                      ? hexA('#ef4444', 0.55)
                      : weak
                        ? hexA(C.warm, 0.45)
                        : on > 0.02
                          ? hexA(C.cyan, 0.3)
                          : hexA('#ffffff', 0.06);
                  const deco = interp
                    ? `underline dotted ${hexA('#ef4444', 0.9)}`
                    : weak
                      ? `underline dotted ${hexA(C.warm, 0.85)}`
                      : undefined;
                  return (
                    <div
                      key={`${k}-${ch}`}
                      style={{
                        position: 'absolute',
                        left: X0 + CHIP0 + k * CHIPSTEP,
                        top: 8,
                        width: chipW,
                        height: chipH,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        borderRadius: 9,
                        fontSize: 24,
                        lineHeight: 1,
                        background: bg,
                        border: `1px solid ${bd}`,
                        color: col,
                        boxShadow: isNow ? `0 0 20px ${hexA(C.cyan, 0.35)}` : undefined,
                      }}
                    >
                      <span
                        style={{
                          textDecoration: deco,
                          textUnderlineOffset: 3,
                          textDecorationThickness: 1.5,
                          opacity: on > 0.05 || isNow ? 1 : 0.95,
                          transform: `scale(${1 + 0.1 * ring})`,
                          display: 'inline-block',
                        }}
                      >
                        {ch}
                      </span>
                      {ring > 0.01 ? (
                        <div
                          style={{
                            position: 'absolute',
                            left: -6 - 8 * (1 - ring),
                            top: -6 - 8 * (1 - ring),
                            width: chipW + 12 + 16 * (1 - ring),
                            height: chipH + 12 + 16 * (1 - ring),
                            borderRadius: 13,
                            border: `1.5px solid ${hexA(C.cyan, 0.5 * ring)}`,
                            boxShadow: `0 0 18px ${hexA(C.cyan, 0.3 * ring)}`,
                            pointerEvents: 'none',
                          }}
                        />
                      ) : null}
                    </div>
                  );
                })}
              </div>
            );
          })}

          {/* 框选矩形 */}
          {boxOn ? (
            <div
              style={{
                position: 'absolute',
                left: 8,
                top: 6,
                width: 180,
                height: 3 * (ROWGAP + ROWH) - ROWGAP - 10,
                opacity: boxO,
                border: `1.5px dashed ${hexA(C.cyan, 0.6)}`,
                borderRadius: 12,
                background: hexA(C.cyan, 0.06),
                boxShadow: `0 0 26px ${hexA(C.cyan, 0.16)}`,
              }}
            />
          ) : null}
        </div>

        {/* ============ 底部：波形进度条 + 状态图例 ============ */}
        <div style={{position: 'absolute', left: 110, top: 590, width: 1400, height: 70}}>
          {new Array(120).fill(0).map((_, k) => {
            const h = wave(k) * 56 * grow(k);
            return (
              <div
                key={k}
                style={{
                  position: 'absolute',
                  left: k * 11,
                  top: 60 - h,
                  width: 2,
                  height: h,
                  borderRadius: 1,
                  background: hexA('#ffffff', 0.26),
                }}
              />
            );
          })}
          <div style={{position: 'absolute', left: 0, top: 62, width: 1400, height: 1, background: C.line}} />
          {/* 播放头 */}
          <div
            style={{
              position: 'absolute',
              left: 1400 * sweep - 1,
              top: -6,
              width: 2,
              height: 76,
              background: hexA(C.cyan, 0.75),
              boxShadow: `0 0 14px ${hexA(C.cyan, 0.55)}`,
            }}
          />
        </div>

        <div
          style={{
            position: 'absolute',
            left: 110,
            bottom: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 22,
            fontSize: 14,
            color: C.faint,
            letterSpacing: 1,
          }}
        >
          {(
            [
              ['#5b6b82', '正常'],
              [C.warm, '低可信（ASR 概率低）'],
              ['#ef4444', '插值补的（没匹配上）'],
              [C.cyan, '正在播放（三个标签页都是它）'],
            ] as const
          ).map(([co, tx]) => (
            <span key={tx} style={{display: 'flex', alignItems: 'center', gap: 8}}>
              <span
                style={{
                  width: 11,
                  height: 11,
                  borderRadius: 3,
                  background: hexA(co, 0.35),
                  border: `1px solid ${hexA(co, 0.75)}`,
                }}
              />
              {tx}
            </span>
          ))}
        </div>
      </div>

      {/* ============ 点击的环形扩散 ============ */}
      {PULSES.map((pu) => {
        const a = pulse(f, pu.t, 16);
        if (a <= 0.02) {
          return null;
        }
        const r = 16 + 30 * (1 - a);
        return (
          <div
            key={`${pu.t}-${pu.x}`}
            style={{
              position: 'absolute',
              left: PX + pu.x - r,
              top: PY + pu.y - r,
              width: r * 2,
              height: r * 2,
              borderRadius: '50%',
              border: `1.5px solid ${hexA(C.cyan, 0.5 * a)}`,
              boxShadow: `0 0 20px ${hexA(C.cyan, 0.3 * a)}`,
            }}
          />
        );
      })}

      {/* ============ 发光光标 ============ */}
      <div
        style={{
          position: 'absolute',
          left: PX + cpos.x,
          top: PY + cpos.y,
          transform: 'translate(-50%, -50%)',
        }}
      >
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: '50%',
            background:
              'radial-gradient(circle, rgba(230,253,255,0.95) 0%, rgba(34,211,238,0.55) 38%, rgba(34,211,238,0) 70%)',
            boxShadow: `0 0 26px ${hexA(C.cyan, 0.75)}`,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            width: 7,
            height: 7,
            marginLeft: -3.5,
            marginTop: -3.5,
            borderRadius: '50%',
            background: '#ffffff',
            boxShadow: `0 0 10px #ffffff`,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
