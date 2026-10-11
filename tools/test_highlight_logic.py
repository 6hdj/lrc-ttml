"""从 index.html 里抽出真的 findWord，在 Node 里测边界。

高亮靠二分找"当前时间落在哪个字"，边界错了就会高亮错字或一直不亮。
测的是**页面里实际的代码**，不是重新实现一遍。
"""
import io
import re
import subprocess
import sys

html = io.open("index.html", encoding="utf-8").read()
js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)

m = re.search(r"function findWord\(t\) \{.*?\n\}", js, re.S)
if not m:
    print("找不到 findWord")
    sys.exit(1)

harness = """
// 用几个手写的字时间构造索引（和 indexWords() 的产物形状一致，且已按 b 排序）
const wordIndex = [
  { b: 1.0, e: 1.5, li: 0, wi: 0 },
  { b: 1.5, e: 2.0, li: 0, wi: 1 },
  { b: 2.0, e: 2.4, li: 0, wi: 2 },
  { b: 5.0, e: 9.0, li: 1, wi: 0 },   // 行间空档：2.4~5.0 之间应该没有当前字
  { b: 9.0, e: 9.3, li: 1, wi: 1 },
];
%s

const cases = [
  [0.5,  null,        '第一个字之前'],
  [1.0,  '0:0',       '正好等于 begin（含）'],
  [1.2,  '0:0',       '第一个字中间'],
  [1.5,  '0:1',       '正好等于下一个 begin（左闭）'],
  [1.999,'0:1',       '第一个字末尾前'],
  [2.0,  '0:2',       '第二个字 begin'],
  [2.4,  null,        '正好等于末尾（右开，不该命中）'],
  [3.0,  null,        '行间空档（不该乱亮）'],
  [5.0,  '1:0',       '第二行第一个字'],
  [9.29, '1:1',       '最后一个字内部'],
  [9.3,  null,        '最后一个字末尾（右开）'],
  [999,  null,        '超出全部范围'],
];
let bad = 0;
for (const [t, want, desc] of cases) {
  const w = findWord(t);
  const got = w ? w.li + ':' + w.wi : null;
  const ok = got === want;
  if (!ok) bad++;
  console.log(`  ${ok ? '✓' : '✗'} ${desc.padEnd(26)} t=${String(t).padEnd(6)} → ${got}（期望 ${want}）`);
}
console.log(bad === 0 ? '\\n二分查找边界全部正确 ✓' : `\\n有 ${bad} 项失败 ✗`);
process.exit(bad === 0 ? 0 : 1);
""" % m.group(0)

io.open(".tmp/_findword_test.js", "w", encoding="utf-8", newline="\n").write(harness)
r = subprocess.run(["node", ".tmp/_findword_test.js"], capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
print(r.stdout or r.stderr)
sys.exit(r.returncode)
