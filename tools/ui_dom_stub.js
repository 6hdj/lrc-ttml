/**
 * 最小 DOM 桩 —— 用来在 Node 里执行 index.html 的真实脚本并断言 UI 行为。
 *
 * 为什么需要：静态检查（CSS 在不在、语法对不对）根本测不出"高亮没生效"。
 * 这里实现到足够跑真实脚本的程度，不追求完整 DOM 语义。
 */
const vm = require('vm');

function makeClassList(el) {
  return {
    add: (...cs) => { const s = new Set(el._cls()); cs.forEach(c => c && s.add(c)); el._setCls(s); },
    remove: (...cs) => { const s = new Set(el._cls()); cs.forEach(c => s.delete(c)); el._setCls(s); },
    toggle: (c, on) => {
      const s = new Set(el._cls());
      const has = s.has(c);
      const want = (on === undefined) ? !has : !!on;
      if (want) s.add(c); else s.delete(c);
      el._setCls(s);
      return want;
    },
    contains: (c) => el._cls().has(c),
  };
}

let idSeq = 0;

class El {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this._id = '';
    this._classes = new Set();
    this.children = [];
    this.parent = null;
    this.style = {};
    this.dataset = {};
    this._text = '';
    this.listeners = {};
    this.value = '';
    this.checked = false;
    this.title = '';
    this.size = 0;
    this.disabled = false;
    this.scrollTop = 0;
    this.clientHeight = 420;
    this._rect = { top: 0, bottom: 20, left: 0, right: 100, height: 20, width: 100 };
    this.onclick = null;
    this.__uid = ++idSeq;
  }
  get id() { return this._id; }
  set id(v) { this._id = v; if (v) this._reg[v] = this; }
  get className() { return [...this._classes].join(' '); }
  set className(v) { this._classes = new Set(String(v || '').split(/\s+/).filter(Boolean)); }
  _cls() { return new Set(this._classes); }
  _setCls(s) { this._classes = s; }
  get classList() { return makeClassList(this); }
  // ⚠️ 真 DOM 的 textContent 会**拼接所有后代的文本**。这里必须照做：
  // 时间轴页的字文本在子元素 <b> 里，单元格自身的 textContent 是空的字符串。
  get textContent() {
    return this.children.length ? this.children.map(c => c.textContent).join('') : this._text;
  }
  set textContent(v) { this._text = String(v == null ? '' : v); this.children = []; }
  set innerHTML(v) { this.children = []; this._text = String(v == null ? '' : v); }
  get innerHTML() { return this._text; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  removeEventListener() {}
  dispatch(t, ev) { (this.listeners[t] || []).forEach(f => f(ev || {})); }
  getBoundingClientRect() { return this._rect; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  focus() {}
  select() {}
  /** 真 DOM 有，文本编辑的"就地输入框"依赖它把 chip 换成 input */
  replaceWith(node) {
    if (!this.parent) return node;
    const i = this.parent.children.indexOf(this);
    if (i >= 0) { this.parent.children[i] = node; node.parent = this.parent; }
    this.parent = null;
    return node;
  }
  remove() {
    if (!this.parent) return;
    const i = this.parent.children.indexOf(this);
    if (i >= 0) this.parent.children.splice(i, 1);
    this.parent = null;
  }
  setAttribute(k, v) { if (k === 'id') this.id = v; else this.dataset[k] = v; }
  removeAttribute(k) { if (k === 'id') this._id = ''; else delete this.dataset[k]; }
  getAttribute() { return null; }
  /** 深度优先收集所有后代 */
  all() {
    const out = [];
    const walk = (n) => n.children.forEach(c => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  /** 找带某 class 的后代 */
  find(cls) { return this.all().filter(e => e._classes.has(cls)); }
}

/** 默认需要预创建的 id（页面加载时会立刻 getElementById 并 addEventListener） */
const DEFAULT_IDS = [
  'editorBody', 'agentOverview', 'editorPanel', 'rangeBar', 'editorStatus',
  'tabHint', 'editorVersion', 'timeLabel', 'nowInfo', 'seekBar', 'playBtn',
  'audioFile', 'lyricsFile', 'transFile', 'logContainer', 'progressFill',
  'progressStage', 'progressPct', 'resultSection', 'statMatch', 'statFormat',
  'statLines', 'statTime', 'ttmlPreview', 'rangeText', 'followScroll',
  'loopWord', 'edAudio',
  // 元数据表单（autofillMeta 要往里写）
  'songTitle', 'artists', 'album', 'author', 'authorId', 'songwriters',
  'ncmId', 'appleId', 'qqId', 'spotifyId',
];

/**
 * 建一个能跑页面脚本的 vm 沙箱。
 * @param {string} pageJs  index.html 里 <script> 的内容
 * @param {string[]} extraIds 额外预创建的 id
 * @param {string} [rawHtml] 完整页面 HTML（可选）—— 用来解析 <select> 的选项，
 *        只传 pageJs 时 <select> 是空的（脚本里没有那段标记）
 */
function makeSandbox(pageJs, extraIds = [], rawHtml = '') {
  const REG = {};
  El.prototype._reg = REG;

  function getEl(id) {
    if (!REG[id]) {
      const e = new El(id === 'edAudio' ? 'audio' : 'div');
      e.id = id;
    }
    return REG[id];
  }
  DEFAULT_IDS.concat(extraIds).forEach(getEl);
  getEl('editorBody').clientHeight = 420;

  // <select> 以前是测不到的（桩不解析 HTML）。这里把页面里的
  // <select id="x">…<option value=…> 解析出来挂到元素上，
  // 运行时测试才能验"选了某一项之后前端到底发了什么"。
  const selRe = /<select[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g;
  let sm;
  while ((sm = selRe.exec(rawHtml || pageJs || ''))) {
    const el = getEl(sm[1]);
    el.options = [...sm[2].matchAll(
      /<option[^>]*value="([^"]*)"[^>]*>([\s\S]*?)<\/option>/g)]
      .map(o => ({value: o[1], text: o[2].replace(/<[^>]+>/g, '').trim()}));
    const chosen = el.options.find((o, i) =>
      new RegExp(`<option[^>]*value="${o.value}"[^>]*selected`).test(sm[2])) 
      || el.options[0];
    el.value = chosen ? chosen.value : '';
  }

  const audio = getEl('edAudio');
  audio.currentTime = 0;
  audio.duration = 100;
  audio.paused = true;
  audio.src = '';
  audio.load = () => {};
  audio.play = () => { audio.paused = false; audio.dispatch('play'); return Promise.resolve(); };
  audio.pause = () => { audio.paused = true; audio.dispatch('pause'); };

  const rafQueue = [];
  const sandbox = {
    console,
    document: {
      getElementById: getEl,
      createElement: (t) => new El(t),
      addEventListener: () => {},
      get activeElement() { return null; },
      body: new El('body'),
    },
    window: { open: () => {} },
    requestAnimationFrame: (fn) => { rafQueue.push(fn); return rafQueue.length; },
    cancelAnimationFrame: () => {},
    setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: () => Promise.reject(new Error('测试里不联网')),
    // 删行会 confirm；测试里默认放行，需要时可改成 false 验证"取消不删"
    confirm: () => true,
    isFinite, parseInt, parseFloat, JSON, Math, Date, Object, Array, String, Number,
    encodeURIComponent, decodeURIComponent, Promise, Set, Map, RegExp, Error,
    Symbol, Boolean, Function,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(pageJs, sandbox, { filename: 'index.html' });

  return {
    sandbox,
    getEl,
    audio,
    rafQueue,
    run: (code) => vm.runInContext(code, sandbox),
    /** 替换 fetch（测试里喂固定响应） */
    setFetch(fn) { sandbox.fetch = fn; },
    /** 把某个 id 的输入框设成指定值（模拟用户手输） */
    setValue(id, v) { getEl(id).value = v; },
    setup(doc) {
      this.run(`editDoc = ${JSON.stringify(doc)};`
               + ` currentTaskId = 'testtask'; editMode = 'time';`);
    },
    /** 模拟"播到某个时刻" */
    at(t) { audio.currentTime = t; this.run('tickHighlight();'); },
    /** 某容器里当前带 .now 的字（文本） */
    nowWords(container, wordCls) {
      return getEl(container).find(wordCls).filter(e => e._classes.has('now'))
        .map(e => e.textContent);
    },
    classesOf(container, cls) {
      return getEl(container).find(cls).map(e => e.className);
    },
  };
}

module.exports = { makeSandbox, El };
