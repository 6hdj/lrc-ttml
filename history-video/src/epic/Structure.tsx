/**
 * 场 5 · 结构（场内 0–164，5.5s）
 *
 * 形式：一座 TTML 结构在画面中央**自上而下逐块搭出来**（每块间隔 14 帧，
 * `blurIn` + `riseIn`），结构搭完后 `<p>` 里那行词块**逐字点亮**，
 * 右侧 `K = 1…n` 计数同步滚动 —— 这就是"逐字歌词格式"的可视化。
 *
 * 内容正确性（本仓库真在用的规则，别写错）：
 * * 对唱分栏的依据是**不同的 `ttm:agent`**（真实 AMLL DB 文件里没有 `x-duet-*`）；
 * * 背景和声 `x-bg` 必须是**主唱 `<p>` 的子内容**，单独成行 = 空行 = 播放器看不见。
 *   所以这一场把 x-bg 画在 p 框**内部**，并标出「p 的子内容」。
 *
 * 对应 SPEC.md §3「场 5」。
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C} from '../constants';
import {
  Caption,
  CornerLabel,
  EASE,
  Readout,
  TABULAR,
  blurIn,
  glowText,
  hexA,
  riseIn,
  seg,
  stage,
} from './shared';

/** 等宽代码块：中文栈（constants.FONT）是比例字体，标签要对齐必须单独配 monospace */
const MONO = 'Consolas, "Cascadia Mono", "DejaVu Sans Mono", monospace';

/** 逐字词块：把这几个字做成 span 小方块依次点亮 */
const CHARS = '把歌词做成逐字时间轴';

/** 左栏各行的 x 坐标（缩进 → 嵌套关系一眼可见） */
const X = {spine: 150, tt: 150, div: 222, p: 150, bg: 212, inner: 192, note: 186};
const W_P = 906;

/** 各行的 y 坐标 */
const Y = {tt: 250, div: 322, p: 392, row: 566, bg: 682};

/** 每块的进场延迟（14 帧一块） */
const AT = {tt: 6, div: 20, p: 34, row: 48, bg: 62};

/** 左侧主干：结构搭到哪，线长到哪 */
const SPINE_TOP = 258;
const SPINE_BOTTOM = 734;

/** 第 i 个词块的点亮进度 */
const chipLit = (frame: number, i: number): number =>
  seg(frame, 84 + i * 4.2, 88 + i * 4.2, 0, 1, EASE.outQuad);

/** 块进场（opacity / 上移 / 模糊三件套，用同一套手感） */
const blockStyle = (frame: number, at: number, extra?: React.CSSProperties): React.CSSProperties => ({
  opacity: seg(frame, at, at + 20, 0, 1, EASE.outCubic),
  transform: `translateY(${riseIn(frame, at, 26, 20)}px)`,
  filter: `blur(${blurIn(frame, at, 22, 12)}px)`,
  ...extra,
});

/** 描边标签块 */
const tagBox = (o: {
  border: string;
  bg: string;
  color: string;
  glow?: boolean;
  size?: number;
  height?: number;
}): React.CSSProperties => ({
  border: `1px solid ${o.border}`,
  background: o.bg,
  color: o.color,
  borderRadius: 10,
  fontFamily: MONO,
  fontWeight: 600,
  letterSpacing: 0.6,
  fontSize: o.size ?? 26,
  padding: '0 18px',
  height: o.height ?? 54,
  display: 'inline-flex',
  alignItems: 'center',
  boxShadow: o.glow ? `0 0 34px ${hexA(o.border, 0.45)}` : undefined,
  whiteSpace: 'nowrap',
});

const abs = (left: number, top: number): React.CSSProperties => ({position: 'absolute', left, top});

export const Structure: React.FC<{frame: number}> = ({frame}) => {
  const lit = CHARS.length;
  // 逐字点亮：K = 已经点亮的词块数
  const chipCount = seg(frame, 84, 88 + (lit - 1) * 4.2, 0, lit, EASE.outQuad);
  const kNow = Math.round(chipCount);
  const spine = seg(frame, AT.tt, AT.row + 24, 0, 1, EASE.inOutQuad);

  return (
    <AbsoluteFill style={stage(C.bgA, undefined)}>
      {/* 环境光：结构搭起来的过程里一直有一层淡青 */}
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(58% 48% at 46% 52%, rgba(34,211,238,0.09) 0%, transparent 72%)',
        }}
      />

      <CornerLabel frame={frame} title="结构" sub="AMLL / Apple Music 逐字歌词格式" />

      {/* 左侧主干 + 三个节点 */}
      <div
        style={{
          ...abs(X.spine - 46, SPINE_TOP),
          width: 2,
          height: (SPINE_BOTTOM - SPINE_TOP) * spine,
          background: `linear-gradient(180deg, ${hexA(C.cyan, 0.7)} 0%, ${hexA(C.cyan, 0.12)} 100%)`,
        }}
      />
      {[Y.tt, Y.div, Y.p].map((y, i) => (
        <div
          key={y}
          style={{
            ...abs(X.spine - 52, y + 27),
            width: 14,
            height: 2,
            background: hexA(C.cyan, 0.35 + 0.5 * seg(frame, 4 + i * 14, 16 + i * 14, 0, 1)),
          }}
        />
      ))}

      {/* <tt xml:lang="zh-Hans"> */}
      <div style={{...abs(X.tt, Y.tt), ...blockStyle(frame, AT.tt)}}>
        <div
          style={tagBox({
            border: hexA(C.cyan, 0.78),
            bg: hexA(C.cyan, 0.07),
            color: C.cyanPale,
            glow: true,
            size: 28,
            height: 56,
          })}
        >
          &lt;tt xml:lang="zh-Hans"&gt;
        </div>
      </div>

      {/* <div>（暗块：只是分组，不是重点） */}
      <div style={{...abs(X.div, Y.div), ...blockStyle(frame, AT.div)}}>
        <div
          style={tagBox({
            border: 'rgba(255,255,255,0.14)',
            bg: 'rgba(255,255,255,0.035)',
            color: C.sub,
            size: 25,
            height: 52,
          })}
        >
          &lt;div&gt;
        </div>
      </div>

      {/* <p itunes:key="L1" ttm:agent="v1"> —— 一行歌词；agent 决定左右分栏 */}
      <div
        style={{
          ...abs(X.p, Y.p),
          width: W_P,
          height: 288,
          borderRadius: 16,
          border: `1px solid ${hexA(C.cyan, 0.85)}`,
          background: hexA(C.cyan, 0.045),
          boxShadow: `0 0 46px ${hexA(C.cyan, 0.26)}, inset 0 0 60px ${hexA(C.cyan, 0.05)}`,
          ...blockStyle(frame, AT.p),
        }}
      >
        {/* p 的标签贴在框内侧顶部 */}
        <div
          style={{
            ...abs(18, 14),
            fontFamily: MONO,
            fontSize: 26,
            fontWeight: 600,
            color: C.cyanPale,
            opacity: seg(frame, AT.p + 6, AT.p + 24, 0, 1),
          }}
        >
          &lt;p itunes:key="L1" ttm:agent="v1"&gt;
        </div>

        {/* 一行 span 小方块：每个字一个，逐字点亮 */}
        <div
          style={{
            ...abs(76, 146),
            display: 'flex',
            gap: 12,
            opacity: seg(frame, AT.row, AT.row + 20, 0, 1, EASE.outCubic),
            transform: `translateY(${riseIn(frame, AT.row, 26, 14)}px)`,
          }}
        >
          {Array.from(CHARS).map((ch, i) => {
            const on = chipLit(frame, i);
            return (
              <div
                key={`${i}-${ch}`}
                style={{
                  width: 56,
                  height: 62,
                  borderRadius: 10,
                  border: `1px solid ${on > 0.02 ? C.cyan : 'rgba(255,255,255,0.16)'}`,
                  background:
                    on > 0.02 ? hexA(C.cyan, 0.1 + 0.26 * on) : 'rgba(255,255,255,0.03)',
                  boxShadow: on > 0.4 ? `0 0 18px ${hexA(C.cyan, 0.42 * on)}` : undefined,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 34,
                  fontWeight: 700,
                  color: on > 0.5 ? C.white : C.dim,
                  fontFamily: MONO,
                }}
              >
                {ch}
              </div>
            );
          })}
        </div>

        {/* x-bg 绿色块 —— 画在 p 框**内部**，这才是"子内容" */}
        <div
          style={{
            ...abs(46, 228),
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            paddingLeft: 36,
            opacity: seg(frame, AT.bg, AT.bg + 20, 0, 1, EASE.outCubic),
            transform: `translateY(${riseIn(frame, AT.bg, 26, 12)}px)`,
            filter: `blur(${blurIn(frame, AT.bg, 22, 10)}px)`,
          }}
        >
          {/* 缩进括号：直观表达"它是 p 的孩子" */}
          <div
            style={{
              position: 'absolute',
              left: 6,
              top: 2,
              bottom: 2,
              width: 22,
              borderLeft: `2px solid ${hexA(C.green, 0.55)}`,
              borderTop: `2px solid ${hexA(C.green, 0.55)}`,
              borderBottom: `2px solid ${hexA(C.green, 0.55)}`,
              borderRadius: '6px 0 0 6px',
            }}
          />
          <div
            style={tagBox({
              border: hexA(C.green, 0.85),
              bg: hexA(C.green, 0.1),
              color: C.green,
              glow: true,
              size: 23,
              height: 52,
            })}
          >
            &lt;span ttm:role="x-bg"&gt;(一路践行)&lt;/span&gt;
          </div>
          <div
            style={{
              fontFamily: MONO,
              fontSize: 15,
              letterSpacing: 2,
              color: hexA(C.green, 0.9),
              whiteSpace: 'nowrap',
            }}
          >
            和声 · p 的子内容
          </div>
        </div>
      </div>

      {/* 词块那一行的小注：放进 p 框**右侧的空档**（x 744 = p 框右缘 1056 之外、K 计数 1244 之内；
          y 482 在 <div> 底 374 之下很远，也不碰 p 框右描边），
          不再吊在 <div> 与 <p> 之间那 18px 的缝里 —— 那样顶端会被 div 描边压边。 */}
      <div
        style={{
          ...abs(744, 482),
          fontFamily: MONO,
          fontSize: 16,
          letterSpacing: 2,
          color: C.faint,
          whiteSpace: 'nowrap',
          opacity: seg(frame, AT.row + 8, AT.row + 26, 0, 1),
        }}
      >
        &lt;span&gt; × {CHARS.length} · 每个字一个自己的时间
      </div>

      {/* 逐字计数：K = 1…n，随点亮滚动（放在中央块右侧的空档里） */}
      <div
        style={{
          ...abs(1244, Y.row - 18),
          width: 220,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'baseline',
          gap: 12,
          opacity: seg(frame, 78, 96, 0, 1),
          ...TABULAR,
        }}
      >
        <span style={{fontFamily: MONO, fontSize: 20, color: C.dim, letterSpacing: 2}}>K =</span>
        <span
          style={{
            fontFamily: MONO,
            fontSize: 56,
            fontWeight: 700,
            color: C.cyan,
            textShadow: glowText(C.cyan, 40, 0.55),
            width: 62,
            textAlign: 'center',
            display: 'inline-block',
          }}
        >
          {kNow}
        </span>
        <span style={{fontFamily: MONO, fontSize: 20, color: C.faint}}>/ {lit}</span>
      </div>
      {/* 进度轨：避免只有数字在跳 */}
      <div
        style={{
          ...abs(1244, Y.row + 60),
          width: 210,
          height: 3,
          borderRadius: 2,
          background: 'rgba(255,255,255,0.10)',
          opacity: seg(frame, 78, 96, 0, 1),
        }}
      >
        <div
          style={{
            width: `${(chipCount / lit) * 100}%`,
            height: '100%',
            borderRadius: 2,
            background: C.cyan,
            boxShadow: `0 0 14px ${hexA(C.cyan, 0.8)}`,
          }}
        />
      </div>

      <Readout
        frame={frame}
        at={62}
        rows={['嵌套：tt › div › p › span', 'x-bg ⊆ p（和声是子内容）', 'agent = 左右分栏（非 x-duet）']}
        top={704}
        right={152}
      />

      <Caption
        frame={frame}
        text="一行 <p> 的里面：一段文字切成若干 <span>，每个 span 都有自己的时间"
        at={96}
        bottom={58}
      />
    </AbsoluteFill>
  );
};
