/* 落盘编码体检：确认所有交付文件是 UTF-8（无 BOM 亦可），并给出 UTF-8 往返校验与中文存在性。
   ⚠️ 背景：PowerShell 5.1 的 `Get-Content` 默认按 ANSI/GBK 解，会把 UTF-8 无 BOM 的中文显示成乱码
   （`閰嶇疆` 这种）—— 那是"读的人"的问题，不是文件的问题。这里用字节级证据分辨两者。
   用法： node promo/tools/check-encoding.cjs  */
'use strict';
const fs = require('fs');
const path = require('path');

// 本脚本住在 promo/tools/ ⇒ 往上一级就是 promo/。
const PROMO = path.resolve(__dirname, '..');
const files = [
  'package.json',
  'tsconfig.json',
  'remotion.config.ts',
  'README.md',
  'src/index.ts',
  'src/Root.tsx',
  'src/Composition.tsx',
  'src/core/theme.ts',
  'src/core/timeline.ts',
  'src/core/Backdrop.tsx',
  'src/core/RevealText.tsx',
  'src/core/ProgressBar.tsx',
  'src/core/SceneWindow.tsx',
  'src/core/SmokeScene.tsx',
];

let bad = 0;
for (const rel of files) {
  const p = path.join(PROMO, rel);
  if (!fs.existsSync(p)) {
    console.log(`MISSING ${rel}`);
    bad++;
    continue;
  }
  const b = fs.readFileSync(p);
  const bom = b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf;
  const s = b.toString('utf8');
  const roundTrip = Buffer.from(s, 'utf8').equals(b);
  const cjk = (s.match(/[\u4e00-\u9fff]/g) || []).length;
  const replacement = (s.match(/\uFFFD/g) || []).length;
  const okFile = roundTrip && replacement === 0;
  if (!okFile) bad++;
  console.log(
    `${okFile ? 'OK  ' : 'BAD '} ${rel.padEnd(26)} bytes=${String(b.length).padStart(6)} bom=${bom ? 'yes' : 'no '} utf8往返=${roundTrip ? 'ok' : 'FAIL'} 汉字=${cjk} 替换符=${replacement}`,
  );
}
const cfg = fs.readFileSync(path.join(PROMO, 'remotion.config.ts'), 'utf8');
console.log('\nremotion.config.ts 关键行（按 UTF-8 解码）:');
console.log('  ' + cfg.split('\n').filter((l) => /Config\.|REMOTION_BROWSER/.test(l)).join('\n  '));
console.log(`\n结论：${bad === 0 ? '全部文件都是合法 UTF-8（无替换符）' : bad + ' 个文件有问题'}`);
process.exit(bad === 0 ? 0 : 1);
