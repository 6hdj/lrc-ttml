/**
 * 真·运行时测试：在最小 DOM 里执行 index.html 的真实脚本，
 * 断言「音频播到 t 时哪些字被高亮」「两个页面是否一致」「点字是否跳转」
 * 「叠唱时两个声部是否同时亮」「咬字/拖音两阶段是否正确」。
 *
 * 为什么必须有这个：静态检查（CSS 在不在、语法对不对）**测不出高亮没生效**。
 * 它第一次跑就抓到三个真 bug（indexWords 清空元素表、缓存键导致重建后不亮、
 * 读数显示 undefined）。叠唱与两阶段同样只能靠它验证。
 */
const fs = require('fs');
const path = require('path');
const { makeSandbox } = require('./ui_dom_stub');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const pageJs = html.match(/<script>([\s\S]*?)<\/script>/)[1];

let pass = 0, fail = 0;
const ck = (name, cond, detail) => {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (detail ? '   ' + detail : '')); }
};

const W = (arr) => arr.map(([t, b, e]) => ({ text: t, begin: b, end: e, matched: true, prob: 0.9 }));

// L1(v1) 尾字拖很长 1.8~3.0；L2(v2) 从 2.0 起唱 —— 与 L1 尾字**重叠**（双人叠唱）
const DOC = {
  lines: [
    { begin: 1.0, end: 3.0, agent: 'v1', text: '站在尽',
      words: W([['站', 1.0, 1.4], ['在', 1.4, 1.8], ['尽', 1.8, 3.0]]) },
    { begin: 2.0, end: 2.8, agent: 'v2', text: '我等',
      words: W([['我', 2.0, 2.4], ['等', 2.4, 2.8]]) },
    { begin: 5.0, end: 5.6, agent: 'v1', text: '来',
      words: W([['来', 5.0, 5.6]]) },
  ],
  version: 1, write_options: {}, agent_types: {},
};
DOC.lines[0].words[1].prob = 0.2;        // 低可信
DOC.lines[2].words[0].matched = false;   // 插值

const S = makeSandbox(pageJs, [], html);
S.setup(DOC);
const nowW = () => S.getEl('editorBody').find('ed-w').filter(e => e._classes.has('now'));
const nowTail = () => S.getEl('editorBody').find('ed-w').filter(e => e._classes.has('now-tail'));
const nowA = () => S.getEl('editorBody').find('ed-aword').filter(e => e._classes.has('now'));
const txt = (els) => els.map(e => e.textContent).join('+');

// ---------------- 场景 1：单行逐字高亮（基础不能坏）----------------
console.log('=== 场景 1：时间轴页逐字高亮 ===');
S.run('switchEditMode("time");');
ck('渲染出 6 个字', S.getEl('editorBody').find('ed-w').length === 6,
   String(S.getEl('editorBody').find('ed-w').length));
ck('t=0.5 无高亮', (S.at(0.5), nowW().length === 0));
ck('t=1.2 高亮「站」', (S.at(1.2), txt(nowW()) === '站'), txt(nowW()));
ck('t=1.5 换成「在」', (S.at(1.5), txt(nowW()) === '在'), txt(nowW()));
ck('t=3.5（行间空档）无高亮', (S.at(3.5), nowW().length === 0), txt(nowW()));
ck('t=5.2 高亮「来」', (S.at(5.2), txt(nowW()) === '来'), txt(nowW()));

// ---------------- 场景 2：双人叠唱 → 两行同时高亮 ----------------
console.log('\n=== 场景 2：双人叠唱并行高亮（本次新增）===');
S.at(2.1);            // L1「尽」(1.8~3.0) 与 L2「我」(2.0~2.4) 同时
ck('t=2.1 同时高亮两个字', nowW().length === 2, txt(nowW()));
ck('两个分别是 v1 的「尽」和 v2 的「我」',
   txt(nowW()).split('+').sort().join('+') === ['尽', '我'].sort().join('+'), txt(nowW()));
ck('两个页面：声部页也同时亮两个字',
   (S.run('switchEditMode("agent");'), nowA().length === 2), txt(nowA()));
ck('两行同时是"当前行"',
   S.getEl('editorBody').find('ed-line').filter(e => e._classes.has('now')).length === 2,
   String(S.getEl('editorBody').find('ed-line').filter(e => e._classes.has('now')).length));
ck('播放器读数列出了两个声部',
   S.getEl('nowInfo').textContent.includes('v1') &&
   S.getEl('nowInfo').textContent.includes('v2'),
   S.getEl('nowInfo').textContent);
S.run('switchEditMode("time");');
ck('切回时间轴仍是两个字',
   (S.at(2.1), nowW().length === 2), txt(nowW()));

console.log('\n=== 场景 3：不重叠时不得误亮（并行不能变成"到处亮"）===');
ck('t=1.2 只有 1 个字', (S.at(1.2), nowW().length === 1), txt(nowW()));
ck('t=2.9（v2 已唱完）只剩 1 个字', (S.at(2.9), nowW().length === 1), txt(nowW()));
ck('t=3.5 一个字都没有', (S.at(3.5), nowW().length === 0), txt(nowW()));

// ---------------- 场景 4：咬字阶段 / 拖音阶段 ----------------
console.log('\n=== 场景 4：咬字 vs 拖音两阶段（本次新增）===');
// L1 中位字长 0.4s → 「尽」(1.8~3.0, 1.2s) 的咬字段 = 1.8~2.2，之后是拖音
S.at(2.0);
const at20 = nowW();
ck('t=2.0「尽」在咬字阶段（还没到 2.2）',
   at20.some(e => e.textContent === '尽' && !e._classes.has('now-tail')),
   txt(nowW()) + ' tail=' + txt(nowTail()));
S.at(2.35);
const at235 = nowW();
ck('t=2.35「尽」进入拖音阶段（过了咬字段）',
   at235.some(e => e.textContent === '尽' && e._classes.has('now-tail')),
   txt(nowW()) + ' tail=' + txt(nowTail()));
ck('同行的短字不会被标成拖音',
   nowTail().every(e => e.textContent === '尽'), txt(nowTail()));
ck('读数里标出了「拖音」', S.getEl('nowInfo').textContent.includes('拖音'),
   S.getEl('nowInfo').textContent);
S.at(5.2);
ck('正常长度的字永远是咬字阶段（不会乱进拖音）',
   nowTail().length === 0, txt(nowTail()));

// ---------------- 场景 5：状态标识（保留原有优化）----------------
console.log('\n=== 场景 5：状态标识 ===');
const statusOf = (s) => s.split(/\s+/).filter(c => c && c !== 'now' && c !== 'now-tail').join(' ');
S.run('switchEditMode("time");');
const tw = S.classesOf('editorBody', 'ed-w').map(c => statusOf(c.replace('ed-w', '')));
S.run('switchEditMode("agent");');
const aw = S.classesOf('editorBody', 'ed-aword').map(c => statusOf(c.replace('ed-aword', '')));
ck('两页状态类完全相同', JSON.stringify(tw) === JSON.stringify(aw),
   `时间轴=${JSON.stringify(tw)} 声部=${JSON.stringify(aw)}`);
ck('低可信 = is-weak', tw[1] === 'is-weak', tw[1]);
ck('插值 = is-interp', tw[5] === 'is-interp', tw[5]);
ck('高亮与状态标识共存', (S.at(2.0), true) &&
   S.classesOf('editorBody', 'ed-aword')[1].includes('is-weak'));

// ---------------- 场景 6：点哪播哪 ----------------
console.log('\n=== 场景 6：点哪播哪 ===');
S.run('switchEditMode("time");');
const cells = S.getEl('editorBody').find('ed-w');
S.audio.currentTime = 0;
S.audio.paused = true;
cells[2].onclick({ target: { tagName: 'B' } });
ck('点第 3 个字 → 跳到 1.8', Math.abs(S.audio.currentTime - 1.8) < 1e-6,
   String(S.audio.currentTime));
ck('点字后自动播放', S.audio.paused === false);

// ---------------- 场景 7：重建后高亮立刻还在 ----------------
console.log('\n=== 场景 7：切标签页/重建后高亮不丢 ===');
S.at(2.35);
S.run('switchEditMode("agent");');
ck('切到声部页立刻有 2 个字亮着', nowA().length === 2, txt(nowA()));
S.run('switchEditMode("time");');
ck('切回时间轴立刻有 2 个字亮着', nowW().length === 2, txt(nowW()));

// ---------------- 场景 8：跟随滚动 ----------------
console.log('\n=== 场景 8：视图跟随 ===');
S.getEl('followScroll').checked = true;
S.run('switchEditMode("time");');
S.getEl('editorBody').scrollTop = 0;
S.at(1.2);                       // 当前行先变成 {0}
const rows8 = S.getEl('editorBody').find('ed-line');
rows8[0]._rect = { top: 0, bottom: 20, left: 0, right: 100, height: 20, width: 100 };
rows8[1]._rect = { top: 900, bottom: 920, left: 0, right: 100, height: 20, width: 100 };
S.at(2.6);                       // 行变化 {0} → {0,1}，叠唱时两行都要照顾到
ck('叠唱时把两行都滚进视野（不是只看第一行）',
   S.getEl('editorBody').scrollTop !== 0,
   String(S.getEl('editorBody').scrollTop));
S.getEl('followScroll').checked = false;
S.getEl('editorBody').scrollTop = 0;
S.at(1.2);                       // 换行 → 本该滚动
S.at(3.5);                       // 再换一次，关掉开关就不该动
ck('关掉后不滚', S.getEl('editorBody').scrollTop === 0);

console.log('\n=== 场景 9：元数据自动识别（文件名 → 歌名/艺人）===');
{
  const T = (file, wantTitle, wantArtists, note) => {
    S.setValue('songTitle', '');
    S.setValue('artists', '');
    S.run(`autofillMeta(${JSON.stringify(file)})`);
    const t = S.getEl('songTitle').value;
    const a = S.getEl('artists').value;
    ck(`${note}：${file}`, t === wantTitle && a === wantArtists,
       `歌名=${t} 艺人=${a}（期望 ${wantTitle} / ${wantArtists}）`);
  };
  // ⚠️ 用户报的错位：这个是「艺人 - 歌名 (Live)」，以前会把两者调换
  T('黄霄雲 _ 刘端端 - 空心 (Live).flac', '空心 (Live)', '黄霄雲, 刘端端', '艺人-歌名顺序');
  T('于文文 - 体面.flac', '体面', '于文文', '单艺人-歌名');
  T('我不曾忘记 - 花玲 _ 张安琪 _ 沐霏.flac', '我不曾忘记', '花玲, 张安琪, 沐霏', '歌名-多艺人');
  T('在出发之前 - 陶典&hanser&爆裂菊是也&HOYO-MiX.wav', '在出发之前',
    '陶典, hanser, 爆裂菊是也, HOYO-MiX', '歌名-&分隔');

  // ⚠️ 用户报的"换歌不更新"：字段里还是上一首的值时，也必须更新
  S.setValue('songTitle', '');
  S.setValue('artists', '');
  S.run('autofillMeta("我不曾忘记 - 花玲 _ 张安琪 _ 沐霏.flac")');
  S.run('autofillMeta("黄霄雲 _ 刘端端 - 空心 (Live).flac")');
  ck('换第二首歌会更新（不是只在空字段才填）',
     S.getEl('songTitle').value === '空心 (Live)'
     && S.getEl('artists').value === '黄霄雲, 刘端端',
     `${S.getEl('songTitle').value} / ${S.getEl('artists').value}`);

  // 用户手改过的不许被覆盖
  S.setValue('songTitle', '我自己起的名字');
  S.run('autofillMeta("黄霄雲 _ 刘端端 - 空心 (Live).flac")');
  ck('用户手输的歌名不被自动填充覆盖',
     S.getEl('songTitle').value === '我自己起的名字', S.getEl('songTitle').value);

  // 没有 " - " 的文件名不该乱猜
  S.setValue('songTitle', '');
  S.run('autofillMeta("track01.flac")');
  ck('没有分隔符时不动', S.getEl('songTitle').value === '', S.getEl('songTitle').value);
}

console.log('\n=== 场景 10：连续两首歌，编辑台必须跟着换 ===');
{
  const mk = (txt) => ({
    lines: [{ begin: 1.0, end: 2.0, agent: 'v1', text: txt,
              words: [{ text: txt, begin: 1.0, end: 2.0, matched: true, prob: 0.9 }] }],
    version: 1, write_options: {}, agent_types: {},
  });
  const docs = { A: mk('第一首'), B: mk('第二首') };
  S.setFetch((url) => Promise.resolve({
    json: () => Promise.resolve(
      url.includes('/api/edit')
        ? { doc: docs[S.run('currentTaskId')], version: 1, source: 'memory',
            stats: { lines: 1, words: 1 } }
        : { status: 'done', progress: 100, logs: [], summary: {} }),
  }));
  // 先渲染第一首
  S.run(`currentTaskId = 'A'; editDoc = ${JSON.stringify(docs.A)}; editMode = 'time';`);
  S.run('renderEditor();');
  const before = S.getEl('editorBody').find('ed-w').map(e => e.textContent).join('');
  ck('第一首渲染出来了', before === '第一首', before);
  // ⚠️ 关键：editDoc 被清空后渲染不能崩（refreshEditorIfOpen 会先置空再异步重拉）
  S.run('editDoc = null;');
  let crashed = false;
  try { S.run('switchEditMode("time");'); } catch (e) { crashed = true; }
  ck('editDoc 为空时渲染不抛异常（否则刷新期间会崩）', !crashed);
  // 新任务完成 → 编辑台内容跟着换
  S.run(`currentTaskId = 'B'; editDoc = ${JSON.stringify(docs.B)}; renderEditor();`);
  const after = S.getEl('editorBody').find('ed-w').map(e => e.textContent).join('');
  ck('换歌后编辑台内容跟着换', after === '第二首', after);

  // refreshEditorIfOpen 必须真的重置旧状态，否则会残留上一首
  S.run(`editDoc = ${JSON.stringify(docs.A)}; renderEditor(); tickHighlight();`);
  S.getEl('editorPanel').style.display = 'block';
  S.run("currentTaskId = 'B'; refreshEditorIfOpen();");
  ck('refreshEditorIfOpen 会清掉上一首的模型', S.run('editDoc === null'),
     String(S.run('editDoc')));
  ck('refreshEditorIfOpen 会清掉逐字索引', S.run('wordIndex.length') === 0,
     String(S.run('wordIndex.length')));
  ck('refreshEditorIfOpen 会清掉高亮缓存', S.run('Object.keys(nowWordState).length') === 0);
}

console.log('\n=== 场景 11：✏️ 文本编辑（插行 / 改字 / 和声）===');
{
  const line = (key, text, bg, agent) => {
    const words = [];
    [...text].forEach((ch, i) => words.push({
      text: ch, begin: i * 0.3, end: (i + 1) * 0.3, is_bg: false,
      layer: 'main', prob: 0.9, matched: true, muted: false, index: i,
    }));
    [...(bg || '')].forEach((ch, i) => words.push({
      text: ch, begin: i * 0.2, end: (i + 1) * 0.2, is_bg: true,
      layer: 'bg', prob: 0.9, matched: true, muted: false, index: i,
    }));
    return {key, xml_id: key, text, agent: agent || 'v1', role: '', translation: '',
            paragraph: 0, begin: 0, end: text.length * 0.3, locked: false, words};
  };
  const docOf = (lines) => ({
    version: 1, lang: 'zh-Hans', timing: 'Word', duration: null,
    write_options: {}, meta: {}, agents: {}, agent_types: {},
    lines: lines.map((l, i) => {
      l.begin = i * 4; l.end = i * 4 + l.text.length * 0.3; return l;
    }),
  });

  let sent = null;
  S.setFetch((url, opt) => {
    const d = JSON.parse(opt.body);
    sent = {url, body: d};
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        doc: d.doc, version: (d.version || 0) + 1, ttml: '<tt/>',
        normalize: {fixes: [], unresolved: [], summary: '无需修复'},
        check: {ok: true, errors: [], warnings: [], summary: '通过'},
        stats: {lines: d.doc.lines.length, words: 99, bg_words: 3, unmatched: 0},
      }),
    });
  });
  const mainIns = () => S.getEl('editorBody').find('txt-in')
    .filter(e => e._classes.has('main'));
  const bgIns = () => S.getEl('editorBody').find('txt-in')
    .filter(e => e._classes.has('bg'));

  S.run("currentTaskId = 'T'; editVersion = 1;");
  S.setup(docOf([line('L1', '第一行', '和声甲', 'v1'),
                 line('L2', '第二行', '', 'v2')]));
  S.run("editMode = 'text'; renderEditor();");

  ck('文本模式渲染出每一行',
     S.getEl('editorBody').find('txt-line').length === 2,
     String(S.getEl('editorBody').find('txt-line').length));
  ck('整行文本框显示主唱文字',
     mainIns().map(e => e.value).join('|') === '第一行|第二行',
     mainIns().map(e => e.value).join('|'));
  ck('和声框显示和声文字',
     bgIns().map(e => e.value).join('|') === '和声甲|',
     bgIns().map(e => e.value).join('|'));

  // --- 整行改文本 ---
  mainIns()[0].value = '整行都换了';
  mainIns()[0].onchange();
  ck('改整行 → set_line_text', sent && sent.body.op === 'set_line_text',
     sent && sent.body.op);
  ck('set_line_text 带对行号与新文本',
     sent.body.payload.key === 'L1' && sent.body.payload.text === '整行都换了',
     JSON.stringify(sent.body.payload));

  // --- 点单字改字 ---
  S.run("editMode = 'text'; renderEditor();");
  const chips = S.getEl('editorBody').find('txt-ch');
  ck('逐字 chip 渲染出来（L1 主唱 3 + 和声 3 + L2 主唱 3 = 9）',
     chips.length === 9, String(chips.length));
  chips.filter(e => !e._classes.has('bg'))[0].onclick();
  const ei = S.getEl('editorBody').find('txt-edit');
  ck('点字后出现就地输入框', ei.length === 1, String(ei.length));
  ck('就地输入框预填该字', ei[0].value === '第', ei[0].value);
  ei[0].value = '改';
  ei[0].onkeydown({key: 'Enter', preventDefault() {}});
  ck('改字 → set_word', sent && sent.body.op === 'set_word', sent && sent.body.op);
  ck('set_word 带对 index/layer/文本',
     sent.body.payload.index === 0 && sent.body.payload.text === '改'
     && sent.body.payload.layer === 'main', JSON.stringify(sent.body.payload));

  // --- 字间插字 ---
  S.run("editMode = 'text'; renderEditor();");
  const pluses = S.getEl('editorBody').find('txt-plus');
  ck('字与字之间有插入点', pluses.length >= 5, String(pluses.length));
  pluses[1].onclick();
  const ii = S.getEl('editorBody').find('txt-edit');
  ck('点 ＋ 出现空的插入框', ii.length === 1 && ii[0].value === '', String(ii.length));
  ii[0].value = '插';
  ii[0].onkeydown({key: 'Enter', preventDefault() {}});
  ck('插字 → insert_word', sent && sent.body.op === 'insert_word', sent && sent.body.op);
  ck('insert_word 插在 index=1',
     sent.body.payload.index === 1 && sent.body.payload.text === '插',
     JSON.stringify(sent.body.payload));

  // --- 删字 ---
  S.run("editMode = 'text'; renderEditor();");
  S.getEl('editorBody').find('txt-ch').filter(e => !e._classes.has('bg'))[1]
    .find('x')[0].onclick({stopPropagation() {}});
  ck('删字 → delete_word', sent && sent.body.op === 'delete_word', sent && sent.body.op);
  ck('delete_word 带对 index', sent.body.payload.index === 1,
     JSON.stringify(sent.body.payload));

  // --- 插行 / 删行 ---
  S.run("editMode = 'text'; renderEditor();");
  S.getEl('editorBody').find('txt-btn')
    .find(b => b.textContent.indexOf('下方插行') >= 0).onclick();
  ck('插行 → insert_line', sent && sent.body.op === 'insert_line', sent && sent.body.op);
  ck('insert_line 以本行为锚点', sent.body.payload.key === 'L1',
     JSON.stringify(sent.body.payload));

  S.run("editMode = 'text'; renderEditor();");
  S.getEl('editorBody').find('txt-btn')
    .find(b => b.textContent.indexOf('删行') >= 0).onclick();
  ck('删行 → delete_line', sent && sent.body.op === 'delete_line', sent && sent.body.op);

  // --- 和声编辑 ---
  S.run("editMode = 'text'; renderEditor();");
  bgIns()[0].value = '新的和声';
  bgIns()[0].onchange();
  ck('改和声 → set_bg', sent && sent.body.op === 'set_bg', sent && sent.body.op);
  ck('set_bg 带新和声文本', sent.body.payload.text === '新的和声',
     JSON.stringify(sent.body.payload));

  S.run("editMode = 'text'; renderEditor();");
  bgIns()[0].value = '';
  bgIns()[0].onchange();
  ck('清空和声 → remove_bg', sent && sent.body.op === 'remove_bg', sent && sent.body.op);

  S.run("editMode = 'text'; renderEditor();");
  ck('只有带和声的行才有和声 chip（L1 的「和声甲」3 个字）',
     S.getEl('editorBody').find('txt-ch').filter(e => e._classes.has('bg')).length === 3,
     String(S.getEl('editorBody').find('txt-ch').filter(e => e._classes.has('bg')).length));

  // 空文本必须拦住（否则写出没有文字的 <p>）
  sent = null;
  mainIns()[0].value = '   ';
  mainIns()[0].onchange();
  ck('整行清空不发请求（拦住空行）', sent === null, String(sent && sent.body.op));

  // 乐观锁：请求里必须带**当前**版本号，否则并发保存会互相覆盖
  S.run("editVersion = 7; editMode = 'text'; renderEditor();");
  sent = null;
  mainIns()[0].value = '再改一次';
  mainIns()[0].onchange();
  ck('文本编辑请求带上当前 version（乐观锁）',
     sent && sent.body.version === 7, String(sent && sent.body.version));
  ck('请求打到 /api/text', sent && sent.url === '/api/text', sent && sent.url);
}

console.log('\n=== 场景 12：演唱者分段选项（关闭/强制1/2/3）===');
{
  const sel = S.getEl('singers');
  ck('选项值只有 off/1/2/3 四个',
     ['off', '1', '2', '3'].every(v => sel.options
       && sel.options.some(o => o.value === v)),
     sel.options ? sel.options.map(o => o.value).join(',') : '(无 options)');

  let body = null;
  S.setFetch((url, opt) => {
    if (url === '/api/convert') body = JSON.parse(opt.body);
    return Promise.resolve({ json: () => Promise.resolve({error: '测试中止'}) });
  });
  S.run("audioServerPath = 'a.wav'; lyricsServerPath = 'b.lrc';");
  for (const want of ['off', '1', '2', '3']) {
    body = null;
    sel.value = want;
    S.run('startConversion();');
    ck(`选「${want}」→ 请求里 singer_mode=${want}`,
       !!body && body.singer_mode === want, String(body && body.singer_mode));
    ck(`选「${want}」→ 不再发旧的 auto_agents/split_singers`,
       !!body && body.auto_agents === undefined && body.split_singers === undefined,
       `auto=${body && body.auto_agents} split=${body && body.split_singers}`);
  }
}

console.log('\n=== 场景 13：✏️ 文本模式的播放高亮 ===');
{
  // ⚠️ 故意把和声字**插在主唱字中间**（真实文件就是这样：读取器按文档序 append）。
  // 这时"层内下标"和"扁平 line.words 下标"不一样 ——
  // 元素地图的键必须用**扁平下标**（indexWords 就是这么建的），
  // 用层内下标会让含和声的行整体错位、亮错字。
  const W = (t, b, e, bg) => ({
    text: t, begin: b, end: e, is_bg: !!bg,
    layer: bg ? 'bg' : 'main', prob: 0.9, matched: true, muted: false, index: 0,
  });
  const doc = {
    version: 1, lang: 'zh-Hans', timing: 'Word', duration: null,
    write_options: {}, meta: {}, agents: {}, agent_types: {},
    lines: [{
      key: 'L1', xml_id: 'L1', text: '主唱', agent: 'v1', role: '',
      translation: '', paragraph: 0, begin: 1.0, end: 3.0, locked: false,
      words: [
        W('主', 1.00, 1.40),          // 扁平 0 / 主唱 0
        W('唱', 1.40, 1.80),          // 扁平 1 / 主唱 1
        W('和', 1.20, 1.50, true),    // 扁平 2 / 和声 0
        W('拖', 2.20, 3.00),          // 扁平 3 / 主唱 2 —— 长音（中位 0.4 的两倍），测拖音阶段
        W('声', 1.50, 1.80, true),    // 扁平 4 / 和声 1
      ],
    }],
  };
  S.setup(doc);
  S.run("editMode = 'text'; renderEditor();");
  const chips = S.getEl('editorBody').find('txt-ch');
  ck('文本模式渲染出 5 个逐字 chip', chips.length === 5, String(chips.length));
  // ⚠️ chip 的 textContent 会把删除按钮的「×」一起拼进来，要取字符那个子元素
  const ch = (e) => (e.children[0] ? e.children[0].textContent : e.textContent);

  // t=1.65：主唱「唱」(扁平1) + 和声「声」(扁平4) 同时在唱
  S.at(1.65);
  const nowTexts = chips.filter(e => e._classes.has('now')).map(ch);
  ck('播放时文本页有字高亮', nowTexts.length > 0, String(nowTexts.length));
  ck('高亮的是「唱」和「声」（同一行主唱+和声并行）',
     nowTexts.length === 2 && nowTexts.indexOf('唱') >= 0 && nowTexts.indexOf('声') >= 0,
     nowTexts.join(','));
  ck('和声字高亮的是它自己（用层内下标会亮到「唱」上）',
     chips.filter(e => e._classes.has('bg') && e._classes.has('now')).map(ch).join(',') === '声',
     chips.filter(e => e._classes.has('bg')).map(e => ch(e) + ':' + e._classes.has('now')).join(' '));

  // 行级高亮
  ck('当前行有 .now（行底色 + 左竖边）',
     S.getEl('editorBody').find('txt-line').filter(e => e._classes.has('now')).length === 1,
     String(S.getEl('editorBody').find('txt-line').filter(e => e._classes.has('now')).length));

  // t=2.8：「拖」是长音 → 应该进拖音阶段
  S.at(2.8);
  const tail = S.getEl('editorBody').find('txt-ch').filter(e => e._classes.has('now-tail'));
  ck('长音字进入拖音阶段（now-tail）', tail.length === 1, String(tail.length));
  ck('拖音阶段仍然带 .now', tail.length === 1 && tail[0]._classes.has('now'));

  // t=0.5：没人在唱 → 不该有高亮
  S.at(0.5);
  ck('没在唱时不留残留高亮',
     S.getEl('editorBody').find('txt-ch').filter(e => e._classes.has('now')).length === 0,
     String(S.getEl('editorBody').find('txt-ch').filter(e => e._classes.has('now')).length));

  // 切页之后高亮仍在（走 syncHighlightAfterRender）
  S.at(1.65);
  S.run("editMode = 'agent'; renderEditor();");
  S.run("editMode = 'text'; renderEditor();");
  ck('从声部页切回文本页，高亮立刻还在（不是等下一个字才亮）',
     S.getEl('editorBody').find('txt-ch').filter(e => e._classes.has('now')).length === 2,
     String(S.getEl('editorBody').find('txt-ch').filter(e => e._classes.has('now')).length));
}

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
