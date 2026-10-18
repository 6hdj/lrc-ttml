/* Lyric2TTML landing — interactions. No dependencies. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------- aurora background ---------------- */
  const canvas = $('#aurora');
  if (canvas && !reduce) {
    const ctx = canvas.getContext('2d');
    let w = 0, h = 0, dpr = 1;
    const blobs = [
      { x: .22, y: .18, r: .46, c: [124, 77, 255], ax: .00018, ay: .00012, p: 0 },
      { x: .82, y: .28, r: .38, c: [255, 94, 196], ax: .00014, ay: .0002, p: 2.1 },
      { x: .55, y: .86, r: .52, c: [74, 36, 201], ax: .00011, ay: .00016, p: 4.2 },
      { x: .12, y: .74, r: .34, c: [88, 255, 238], ax: .0002, ay: .0001, p: 1.2 },
    ];
    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = canvas.width = Math.floor(innerWidth * dpr);
      h = canvas.height = Math.floor(innerHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    addEventListener('resize', resize);

    let mx = .5, my = .5;
    addEventListener('pointermove', e => { mx = e.clientX / innerWidth; my = e.clientY / innerHeight; });

    let t = 0;
    const draw = () => {
      t += 1;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      // 星空点阵
      const cols = Math.floor(innerWidth / 54), rows = Math.floor(innerHeight / 54);
      ctx.fillStyle = 'rgba(180,168,210,.16)';
      for (let i = 0; i < cols; i++) {
        for (let j = 0; j < rows; j++) {
          const dx = i / cols - mx, dy = j / rows - my;
          if ((i * 7 + j * 13) % 17 === 0 && dx * dx + dy * dy < .35) {
            ctx.fillRect(i * 54 + 26, j * 54 + 26, 1.4, 1.4);
          }
        }
      }
      // 柔光团
      for (const b of blobs) {
        const x = (b.x + Math.sin(t * b.ax * 60 + b.p) * .07 + (mx - .5) * .05) * innerWidth;
        const y = (b.y + Math.cos(t * b.ay * 60 + b.p) * .06 + (my - .5) * .04) * innerHeight;
        const r = b.r * Math.max(innerWidth, innerHeight) * (0.82 + 0.18 * Math.sin(t * .004 + b.p));
        const g = ctx.createRadialGradient(x, y, 0, x, y, r);
        g.addColorStop(0, `rgba(${b.c[0]},${b.c[1]},${b.c[2]},.24)`);
        g.addColorStop(.55, `rgba(${b.c[0]},${b.c[1]},${b.c[2]},.07)`);
        g.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      }
      requestAnimationFrame(draw);
    };
    requestAnimationFrame(draw);
  }

  /* ---------------- cursor glow ---------------- */
  const glow = $('#cursorGlow');
  if (glow && matchMedia('(pointer:fine)').matches) {
    let gx = innerWidth / 2, gy = innerHeight / 2, cx = gx, cy = gy;
    addEventListener('pointermove', e => { gx = e.clientX; gy = e.clientY; });
    const loop = () => {
      cx += (gx - cx) * .08; cy += (gy - cy) * .08;
      glow.style.transform = `translate(${cx}px,${cy}px) translate(-50%,-50%)`;
      requestAnimationFrame(loop);
    };
    loop();
  }

  /* ---------------- reveal on scroll ---------------- */
  const io = new IntersectionObserver((ents) => {
    for (const e of ents) {
      if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
    }
  }, { threshold: .14, rootMargin: '0px 0px -8% 0px' });
  $$('.reveal').forEach(el => io.observe(el));

  /* ---------------- nav / progress / active link ---------------- */
  const nav = $('#nav');
  const progress = $('#progress');
  const links = $$('[data-nav]');
  const sections = links.map(a => $(a.getAttribute('href'))).filter(Boolean);
  const onScroll = () => {
    const y = scrollY;
    nav.classList.toggle('scrolled', y > 20);
    const max = document.documentElement.scrollHeight - innerHeight;
    progress.style.width = Math.min(100, (y / Math.max(1, max)) * 100) + '%';
    let cur = null;
    for (const s of sections) if (s.getBoundingClientRect().top <= 140) cur = s;
    if (scrollY < 200) cur = null;
    links.forEach(a => a.classList.toggle('active', !!cur && a.getAttribute('href') === '#' + cur.id));
  };
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---------------- mobile menu ---------------- */
  const burger = $('#burger');
  burger && burger.addEventListener('click', () => {
    nav.classList.toggle('open');
    burger.classList.toggle('open');
  });
  $$('.nav-links a').forEach(a => a.addEventListener('click', () => nav.classList.remove('open')));

  /* ---------------- hero timeline animation ---------------- */
  const track = $('#tlTrack');
  if (track) {
    const line = '我的破木箱装满枯萎的花';
    const times = ['00:47.312', '00:47.390', '00:47.468', '00:47.546', '00:47.624', '00:47.702', '00:47.780', '00:47.858', '00:47.936', '00:48.014', '00:48.092'];
    const chars = [];
    [...line].forEach((ch, i) => {
      const el = document.createElement('span');
      el.className = 'tl-ch';
      el.innerHTML = `${ch}<span class="ts">${times[i] || ''}</span>`;
      track.appendChild(el);
      chars.push(el);
    });
    let idx = 0;
    const tick = () => {
      chars.forEach(c => c.classList.remove('now'));
      const c = chars[idx];
      if (c) { c.classList.add('lit', 'now'); }
      idx++;
      if (idx > chars.length) {
        setTimeout(() => { chars.forEach(c => c.classList.remove('lit', 'now')); idx = 0; tick(); }, 1700);
        return;
      }
      setTimeout(tick, idx === chars.length ? 900 : 330);
    };
    setTimeout(tick, 1200);
  }

  /* ---------------- counters ---------------- */
  const counters = $$('.num[data-count]');
  const cio = new IntersectionObserver((ents) => {
    for (const e of ents) {
      if (!e.isIntersecting) continue;
      const el = e.target;
      cio.unobserve(el);
      const target = parseFloat(el.dataset.count || '0');
      const suffix = el.dataset.suffix || '';
      const dur = 1400; const t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / dur);
        const v = Math.round(target * (1 - Math.pow(1 - k, 3)));
        el.textContent = v + suffix;
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
  }, { threshold: .5 });
  counters.forEach(c => cio.observe(c));

  /* ---------------- pipeline fill ---------------- */
  const pipe = $('#pipeFill');
  const pipeline = $('.pipeline');
  if (pipe && pipeline) {
    const pio = new IntersectionObserver((ents) => {
      for (const e of ents) {
        if (e.isIntersecting) {
          const rect = pipeline.getBoundingClientRect();
          pipe.style.width = '100%';
          pio.unobserve(pipeline);
        }
      }
    }, { threshold: .35 });
    pio.observe(pipeline);
  }

  /* ---------------- tilt ---------------- */
  if (matchMedia('(pointer:fine)').matches && !reduce) {
    $$('[data-tilt]').forEach(el => {
      el.addEventListener('pointermove', e => {
        const r = el.getBoundingClientRect();
        const px = (e.clientX - r.left) / r.width - .5;
        const py = (e.clientY - r.top) / r.height - .5;
        el.style.transform = `perspective(900px) rotateX(${(-py * 4).toFixed(2)}deg) rotateY(${(px * 5).toFixed(2)}deg) translateY(-3px)`;
      });
      el.addEventListener('pointerleave', () => { el.style.transform = ''; });
    });
  }

  /* ---------------- video modal ---------------- */
  const modal = $('#modal');
  const frame = $('#biliFrame');
  const openModal = () => {
    if (!modal) return;
    if (frame && !frame.src) frame.src = frame.dataset.src;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  };
  const closeModal = () => {
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  };
  $('#videoBtn')?.addEventListener('click', openModal);
  $$('[data-close]').forEach(el => el.addEventListener('click', closeModal));
  addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

  /* ---------------- copy download link ---------------- */
  const copyBtn = $('#copyLink');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const link = copyBtn.dataset.link || '';
      try {
        await navigator.clipboard.writeText(link);
        copyBtn.textContent = '✓ 已复制';
        setTimeout(() => { copyBtn.textContent = '复制下载链接'; }, 1800);
      } catch {
        window.prompt('复制下载链接：', link);
      }
    });
  }
})();
