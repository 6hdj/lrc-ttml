/** 占位场景：只画场名，用来在真实场景到位前保持工程可渲染。被替换后即可删除本文件。 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {C, FONT} from '../constants';

export const SceneStub: React.FC<{frame: number; no: number; name: string; form: string}> = ({
  frame,
  no,
  name,
  form,
}) => (
  <AbsoluteFill
    style={{
      backgroundColor: C.bgA,
      fontFamily: FONT,
      justifyContent: 'center',
      alignItems: 'center',
      color: C.dim,
    }}
  >
    <div style={{fontSize: 64, fontWeight: 800, color: C.sub, letterSpacing: 8}}>
      {no}. {name}
    </div>
    <div style={{fontSize: 24, marginTop: 22, letterSpacing: 4}}>（占位）{form}</div>
    <div style={{fontSize: 18, marginTop: 40, ...{fontVariantNumeric: 'tabular-nums'}}}>
      frame {frame}
    </div>
  </AbsoluteFill>
);
