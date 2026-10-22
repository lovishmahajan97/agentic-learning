/* AI Roles Academy — single-file app. Data in window.LMS_DATA (built by scripts/build.py). */
(function () {
  'use strict';
  const D = window.LMS_DATA || { modules: [], tiers: {}, role_paths: {}, meta: {} };
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const DAY = 864e5;
  const STEPS = [1, 3, 7, 14, 30, 60, 120];
  const ROLE = { engineer: 'AI Engineer', architect: 'AI Architect', fde: 'FDE' };

  /* ---------------- markdown ---------------- */
  if (window.marked) marked.setOptions({ gfm: true, breaks: false, headerIds: false, mangle: false });
  const md = s => window.marked ? marked.parse(String(s || '')) : '<p>' + esc(s) + '</p>';
  const mdi = s => window.marked ? marked.parseInline(String(s || '')) : esc(s);
  let mermaidSeq = 0;
  function isDark() { const t = document.documentElement.getAttribute('data-theme'); return t ? t === 'dark' : !!(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches); }
  function renderDiagrams(root) {
    const blocks = root.querySelectorAll('pre > code.language-mermaid');
    if (!blocks.length) return;
    const nodes = [];
    blocks.forEach(code => {
      const src = code.textContent; const pre = code.parentElement;
      const wrap = document.createElement('div'); wrap.className = 'diagram';
      const el = document.createElement('pre'); el.className = 'mermaid'; el.id = 'mmd-' + (++mermaidSeq); el.textContent = src;
      wrap.appendChild(el); wrap.dataset.src = src; pre.replaceWith(wrap); nodes.push(el);
    });
    if (!window.mermaid) { nodes.forEach(n => n.closest('.diagram').classList.add('diagram-src')); return; }
    try {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: isDark() ? 'dark' : 'neutral',
        fontFamily: getComputedStyle(document.body).fontFamily, flowchart: { htmlLabels: true, curve: 'basis', useMaxWidth: false, nodeSpacing: 36, rankSpacing: 44 }, sequence: { useMaxWidth: false }, state: { useMaxWidth: false } });
      mermaid.run({ nodes, suppressErrors: true }).finally(() => nodes.forEach(n => {
        if (!n.querySelector('svg')) { const w = n.closest('.diagram'); if (w) { w.classList.add('diagram-src'); n.textContent = w.dataset.src; } }
      }));
    } catch (e) { nodes.forEach(n => n.closest('.diagram').classList.add('diagram-src')); }
  }
  function highlight(root) {
    renderDiagrams(root);
    if (!window.hljs) return;
    root.querySelectorAll('pre code:not(.language-mermaid)').forEach(b => { try { hljs.highlightElement(b); } catch (e) { } });
  }

  /* ---------------- state ---------------- */
  const KEY = 'ai-roles-academy.v1';
  const defaults = () => ({ path: 'all', learned: {}, review: {}, quiz: {}, modquiz: {}, notes: {}, capstone: {}, visits: {}, last: null, days: [], theme: 'light', open: {} });
  let S = defaults();
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) { }
  if (!S.themeV2) { S.theme = 'light'; S.themeV2 = true; }   // light is the default look; dark stays one click away
  let saveT; function save() { clearTimeout(saveT); saveT = setTimeout(() => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { } }, 150); }

  /* ---------------- index ---------------- */
  const topics = new Map();
  const trackKey = m => (m.track_order != null ? m.track_order : 100 + m.order);
  const modules = D.modules.slice().sort((a, b) => trackKey(a) - trackKey(b));
  modules.forEach((m, i) => { m.num = i + 1; });
  const STAGES = (D.stages && D.stages.length ? D.stages : [1, 2, 3].map(t => ({ id: 'tier' + t, title: (D.tiers[t] || {}).title || 'Tier ' + t, blurb: (D.tiers[t] || {}).blurb || '' })))
    .map((s, i) => ({ ...s, n: i + 1, mods: modules.filter(m => (m.stage || 'tier' + m.tier) === s.id) }))
    .filter(s => s.mods.length);
  const stageOf = m => STAGES.find(s => s.mods.includes(m)) || STAGES[0];
  const ROLE_FOR_TIER = { 1: 'All roles', 2: 'AI Architect', 3: 'FDE' };
  const pad2 = n => String(n).padStart(2, '0');
  const modById = new Map();
  modules.forEach(m => {
    modById.set(m.id, m);
    m.topics.forEach(t => { t.module = m.id; t.tier = m.tier; t.moduleTitle = m.title; t.key = t.key || (m.id + '/' + t.id); t.name = t.name || t.taxonomy_name; topics.set(t.key, t); });
    m.ordered = (function () {
      if (m.intro && Array.isArray(m.intro.order)) {
        const by = new Map(m.topics.map(t => [t.id, t]));
        const arr = m.intro.order.map(s => by.get(s)).filter(Boolean);
        if (arr.length === m.topics.length) return arr;
      }
      return m.topics.slice();
    })();
  });
  const flat = []; modules.forEach(m => m.ordered.forEach(t => flat.push(t)));
  const TOTAL = flat.length;

  /* ---------------- lazy lesson payloads (site/data/<module>.js) ---------------- */
  const loaded = new Set(), loading = new Map();
  window.LMS_LOAD = (mid, payload) => {
    (payload.topics || []).forEach(full => { const t = topics.get(full.key); if (t) Object.assign(t, full); });
    loaded.add(mid); const w = loading.get(mid); if (w) { w.resolve(); loading.delete(mid); }
  };
  function ensureModule(mid) {
    if (loaded.has(mid)) return Promise.resolve();
    if (loading.has(mid)) return loading.get(mid).promise;
    let resolve; const promise = new Promise(r => { resolve = r; }); loading.set(mid, { promise, resolve });
    const sc = document.createElement('script'); sc.src = 'data/' + mid + '.js?b=' + encodeURIComponent(D.meta.build_id || '0');
    sc.onerror = () => { loaded.add(mid); resolve(); loading.delete(mid); }; document.head.appendChild(sc);
    return promise;
  }
  const ensureModules = ids => Promise.all([...new Set(ids)].map(ensureModule));
  const allModuleIds = () => modules.filter(m => modStatus(m) !== 'skip').map(m => m.id);
  function prefetchAll() { const ids = modules.map(m => m.id); let i = 0; (function next() { if (i >= ids.length) return; ensureModule(ids[i++]).then(() => setTimeout(next, 50)); })(); }
  const loadingView = what => `<div class="page-narrow"><div class="empty">Loading ${what}…</div></div>`;

  /* ---------------- role paths ---------------- */
  function modStatus() { return 'core'; }
  function topicStatus() { return 'core'; }
  const inPath = t => topicStatus(t) !== 'skip';

  /* ---------------- progress helpers ---------------- */
  const isLearned = t => !!S.learned[t.key];
  const now = () => Date.now();
  function reviewRec(key) { return S.review[key]; }
  function isDue(t) { const r = S.review[t.key]; return !!(r && r.due <= now()); }
  function dueTopics() { return flat.filter(t => isDue(t) && inPath(t)); }
  function schedule(key, grade) { // 0 again, 1 good, 2 easy
    const r = S.review[key] || { step: -1, reps: 0, lapses: 0 };
    if (grade === 0) { r.step = 0; r.lapses = (r.lapses || 0) + 1; } else { r.step = Math.min(STEPS.length - 1, Math.max(0, r.step) + (grade === 2 ? 2 : 1)); }
    r.reps = (r.reps || 0) + 1; r.last = now(); r.due = now() + STEPS[r.step] * DAY; S.review[key] = r; save(); refreshChrome();
  }
  function setLearned(t, on) {
    if (on) { S.learned[t.key] = now(); if (!S.review[t.key]) S.review[t.key] = { step: 0, reps: 0, lapses: 0, due: now() + DAY, last: now() }; }
    else { delete S.learned[t.key]; delete S.review[t.key]; }
    save(); refreshChrome();
  }
  function topicDot(t) { if (!inPath(t)) return ''; if (isDue(t) && isLearned(t)) return 'due'; if (isLearned(t)) return 'learned'; if (S.visits[t.key]) return 'started'; return ''; }
  function modProgress(m) { const ts = m.topics.filter(inPath); const done = ts.filter(isLearned).length; return { done, total: ts.length, pct: ts.length ? Math.round(100 * done / ts.length) : 0 }; }
  function touchDay() { const d = new Date().toISOString().slice(0, 10); if (!S.days.includes(d)) { S.days.push(d); S.days = S.days.slice(-400); save(); } }
  function streak() { const set = new Set(S.days); let n = 0; const d = new Date(); for (; ;) { const k = d.toISOString().slice(0, 10); if (set.has(k)) { n++; d.setDate(d.getDate() - 1); } else break; } return n; }
  const minutesLeft = ts => ts.filter(t => !isLearned(t)).reduce((a, t) => a + (t.est_minutes || 12), 0);
  const fmtMin = m => m >= 60 ? Math.round(m / 60) + ' h' : m + ' min';
  function paceDate(remaining) { const d = new Date(); d.setDate(d.getDate() + Math.ceil(remaining / (S.pace || 3))); return remaining ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined }) : 'today'; }

  /* ---------------- chrome: sidebar, counts, theme ---------------- */
  const main = $('#main'), tree = $('#tree'), app = $('#app');
  function buildPathSelect() { /* the role-path dropdown was removed: one fixed track */ }
  function renderTree(filter) {
    const q = (filter || '').trim().toLowerCase();
    let html = '';
    STAGES.forEach(stg => {
      const ms = stg.mods;
      let inner = '';
      ms.forEach(m => {
        const items = m.ordered.filter(t => inPath(t) && (!q || (t.name + ' ' + m.title).toLowerCase().includes(q)));
        if (q && !items.length) return;
        const p = modProgress(m), open = q ? true : !!S.open[m.id], st = modStatus(m);
        inner += `<div class="mod ${open ? 'open' : ''} ${st}" data-mod="${m.id}" style="--tier: var(--t${m.tier})">
          <button class="mod-head" data-toggle="${m.id}" aria-expanded="${open}">
            <svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <span class="mod-num">${pad2(m.num)}</span><span class="mod-title">${esc(m.title)}</span>
            <span class="mod-count">${p.done}/${p.total}</span></button>
          <div class="mod-bar"><i style="width:${p.pct}%"></i></div>
          <ul class="mod-items">${items.map(t => `<li><a href="#t.${m.id}.${t.id}" data-key="${t.key}" class="${t.status !== 'ok' ? 'missing' : ''}"><span class="dot ${topicDot(t)}"></span><span>${esc(t.name)}</span>${t.agent_relevance === 'core' ? '<i class="core-dot" title="Core for agents"></i>' : ''}</a></li>`).join('')}</ul></div>`;
      });
      if (!inner) return;
      html += `<div class="tier-group" style="--st: var(--s${stg.n}); --st2: var(--s${stg.n}b)"><div class="tier-label stage-label"><span class="stage-n">${stg.n}</span>${esc(stg.title)}</div>${inner}</div>`;
    });
    tree.innerHTML = html || '<div class="tree-empty">No lessons match.</div>';
    tree.querySelectorAll('[data-toggle]').forEach(b => b.onclick = () => { const id = b.dataset.toggle; S.open[id] = !S.open[id]; save(); b.closest('.mod').classList.toggle('open', S.open[id]); b.setAttribute('aria-expanded', !!S.open[id]); });
    markActive();
  }
  function markActive() {
    tree.querySelectorAll('a.active').forEach(a => a.classList.remove('active'));
    const h = location.hash; const a = tree.querySelector(`a[href="${CSS.escape ? h.replace(/"/g, '') : h}"]`);
    if (a) { a.classList.add('active'); const mod = a.closest('.mod'); if (mod && !mod.classList.contains('open')) { mod.classList.add('open'); S.open[mod.dataset.mod] = true; save(); } a.scrollIntoView({ block: 'nearest' }); }
  }
  function refreshChrome() {
    const n = dueTopics().length; const c = $('#dueCount'); c.textContent = n; c.classList.toggle('zero', n === 0);
    tree.querySelectorAll('a[data-key]').forEach(a => { const t = topics.get(a.dataset.key); const d = a.querySelector('.dot'); if (t && d) d.className = 'dot ' + topicDot(t); });
    tree.querySelectorAll('.mod').forEach(el => { const m = modById.get(el.dataset.mod); if (!m) return; const p = modProgress(m); el.querySelector('.mod-count').textContent = `${p.done}/${p.total}`; el.querySelector('.mod-bar i').style.width = p.pct + '%'; });
  }
  function applyTheme() {
    const root = document.documentElement; if (S.theme === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', S.theme);
    $('#themeToggle').title = 'Theme: ' + S.theme;
  }
  $('#themeToggle').onclick = () => { S.theme = { light: 'dark', dark: 'system', system: 'light' }[S.theme] || 'light'; save(); applyTheme(); toast('Theme: ' + S.theme); if (document.querySelector('.diagram')) route(); };
  const navToggle = $('#navToggle'), scrim = $('#scrim');
  function setNav(open) { app.classList.toggle('nav-open', open); scrim.hidden = !open; navToggle.setAttribute('aria-expanded', open); }
  navToggle.onclick = () => setNav(!app.classList.contains('nav-open'));
  scrim.onclick = () => setNav(false);
  tree.addEventListener('click', e => { if (e.target.closest('a')) setNav(false); });
  let toastT; function toast(msg) { let t = $('.toast'); if (!t) { t = document.createElement('div'); t.className = 'toast'; document.body.appendChild(t); } t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 1800); }

  /* ---------------- search ---------------- */
  const search = $('#search');
  let searchT; search.addEventListener('input', () => { clearTimeout(searchT); searchT = setTimeout(() => renderTree(search.value), 120); });
  search.addEventListener('keydown', e => { if (e.key === 'Enter' && search.value.trim()) { location.hash = '#search'; renderSearch(search.value.trim()); } if (e.key === 'Escape') { search.value = ''; renderTree(''); search.blur(); } });
  document.addEventListener('keydown', e => {
    if (e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); search.focus(); search.select(); }
    else if (e.key === 'n' || e.key === ']') { const a = $('.pager a.next'); if (a) location.hash = a.getAttribute('href'); }
    else if (e.key === 'p' || e.key === '[') { const a = $('.pager a.prev'); if (a) location.hash = a.getAttribute('href'); }
    else if (e.key === 'l') { const b = $('#learnBtn'); if (b) b.click(); }
  });

  /* ---------------- views ---------------- */
  const stv = m => `--st: var(--s${stageOf(m).n}); --st2: var(--s${stageOf(m).n}b)`;
  const REL = { core: 'Core for agents', useful: 'Useful for agents', background: 'Background' };
  function pillRel(r) { return r && REL[r] ? `<span class="pill rel rel-${r}">${REL[r]}</span>` : ''; }
  function pillTier(tier) { return `<span class="pill tier-${tier}">Tier ${tier}</span>`; }
  function pillStatus(st) { return st === 'background' ? '<span class="pill">Background reading</span>' : ''; }

  function renderDashboard() {
    touchDay();
    const ts = flat.filter(inPath); const learned = ts.filter(isLearned).length; const due = dueTopics().length;
    const last = S.last && topics.get(S.last);
    const nextUp = ts.filter(t => !isLearned(t) && topicStatus(t) === 'core' && t.status === 'ok').slice(0, 6);
    const quizzes = Object.values(S.quiz); const quizAvg = quizzes.length ? Math.round(100 * quizzes.reduce((a, q) => a + q.best / q.of, 0) / quizzes.length) : null;
    const okCount = D.meta.ok_count != null ? D.meta.ok_count : flat.filter(t => t.status === 'ok').length;
    let html = `<div class="page">
      <div class="hero">
        <div class="eyebrow">AI Roles Skills Taxonomy · ${TOTAL} lessons · ${modules.length} modules · ${STAGES.length} stages</div>
        <h1>Learn to build AI agents, one reasoned lesson at a time.</h1>
        <p class="lede">Every lesson is written from the agent builder's seat: why it matters when you build agents, how it works, the tools and production patterns teams use, and the interview questions you will face. Work through the track from Python to customer delivery; lessons tagged <b>Core for agents</b> are the ones to never skip.</p>
        <ol class="stage-strip">${STAGES.map(s => `<li style="--st: var(--s${s.n}); --st2: var(--s${s.n}b)"><a href="#stage-${s.id}"><span class="stage-n">${s.n}</span>${esc(s.title)}</a></li>`).join('')}</ol>
      </div>
      <div class="stats">
        <div class="stat"><div class="label">Learned</div><div class="value">${learned}<span class="muted" style="font-size:16px;font-weight:400"> / ${ts.length}</span></div><div class="sub">${fmtMin(minutesLeft(ts))} of reading left · at <select id="pace" class="pace">${[1, 2, 3, 5, 8].map(n => `<option value="${n}" ${(S.pace || 3) == n ? 'selected' : ''}>${n}</option>`).join('')}</select>/day, done by <b>${paceDate(ts.length - learned)}</b></div><div class="bar"><i style="width:${ts.length ? 100 * learned / ts.length : 0}%"></i></div></div>
        <div class="stat"><div class="label">Due for review</div><div class="value">${due}</div><div class="sub">${due ? '<a href="#review">Start a review session</a>' : 'Nothing due right now'}</div></div>
        <div class="stat"><div class="label">Streak</div><div class="value">${streak()}<span class="muted" style="font-size:16px;font-weight:400"> days</span></div><div class="sub">${S.days.length} study days total</div></div>
        <div class="stat"><div class="label">Quiz accuracy</div><div class="value">${quizAvg == null ? '–' : quizAvg + '%'}</div><div class="sub">${quizzes.length} topic quizzes taken</div></div>
      </div>`;
    if (okCount < TOTAL) html += `<div class="banner">Lessons are still being generated: ${okCount} of ${TOTAL} are ready. Topics without a lesson appear greyed out in the curriculum.</div>`;
    html += `<div class="row2">
        <div class="card"><h3>${last ? 'Continue' : 'Start here'}</h3>
          ${last ? `<ul class="next-list"><li><a href="#t.${last.module}.${last.id}"><span>${esc(last.name)}<div class="sub">${esc(last.moduleTitle)} · ${last.est_minutes || 12} min</div></span><span class="pill">Resume</span></a></li></ul>` : ''}
          <div style="margin-top:10px;display:flex;justify-content:space-between;align-items:center"><span class="eyebrow">Next up in the track</span><button class="btn sm" id="randomBtn">Random lesson</button></div>
          <ul class="next-list" style="margin-top:6px">${nextUp.map(t => `<li><a href="#t.${t.module}.${t.id}"><span>${esc(t.name)}<div class="sub">${esc(t.moduleTitle)}</div></span><span class="muted mono" style="font-size:12px">${t.est_minutes || 12} min</span></a></li>`).join('') || '<li class="muted">Everything in the track is learned.</li>'}</ul>
        </div>
        <div class="card"><h3>How this works</h3>
          <div class="prose" style="font-size:15px">
            <p><strong>Read</strong> a lesson top to bottom: the forcing problem, the mechanism, a concrete example, trade-offs and pitfalls. <strong>Check yourself</strong> with the interview question before revealing the answer, then <strong>take the quiz</strong>. Three correct answers marks the lesson learned and schedules it for review.</p>
            <p><strong>Interview drill</strong> shuffles the check questions of a module or the whole track so you can rehearse reasoning out loud. The <strong>review queue</strong> uses expanding intervals (1, 3, 7, 14, 30, 60, 120 days). Grade yourself honestly; a miss resets the interval. Each module ends with a <strong>capstone</strong> you can build and a <strong>module quiz</strong> drawn from all its lessons.</p>
            <p>Keys: <span class="kbd">/</span> search · <span class="kbd">n</span> / <span class="kbd">p</span> next / previous lesson · <span class="kbd">l</span> mark learned.</p>
          </div>
        </div>
      </div>`;
    STAGES.forEach(stg => {
      const ms = stg.mods;
      const tts = ms.flatMap(m => m.topics); const tl = tts.filter(isLearned).length;
      html += `<section class="tier-section" id="stage-${stg.id}" style="--st: var(--s${stg.n}); --st2: var(--s${stg.n}b)">
        <div class="tier-head"><div><h2><span class="stage-n big">${stg.n}</span>${esc(stg.title)}</h2><div class="muted" style="margin-top:4px;font-size:14px">${esc(stg.blurb || '')}</div></div>
        <div class="tier-prog"><span>${tl} / ${tts.length} learned</span><div class="bar"><i style="width:${tts.length ? 100 * tl / tts.length : 0}%"></i></div></div></div>
        <div class="mod-grid">${ms.map(m => { const p = modProgress(m); return `<a class="mod-card" href="#m.${m.id}" style="--tier: var(--t${m.tier}); ${stv(m)}">
            <span class="pct">${p.pct}%</span><div class="mod-card-num">Module ${pad2(m.num)}</div><h3>${esc(m.title)}</h3>
            <div class="meta"><span>${p.total} lessons</span><span>·</span><span>${fmtMin(minutesLeft(m.topics))} left</span>${m.topics.filter(t => t.agent_relevance === 'core').length ? `<span>·</span><span class="core-count">${m.topics.filter(t => t.agent_relevance === 'core').length} core for agents</span>` : ''}</div>
            <div class="topic-meta">${pillTier(m.tier)}<span class="pill">${ROLE_FOR_TIER[m.tier]}</span></div>
            <div class="bar tier-${m.tier}"><i style="width:${p.pct}%"></i></div></a>`; }).join('')}</div></section>`;
    });
    html += `<details class="about"><summary>About this curriculum and how to read it</summary><div class="prose" style="font-size:15px">
      <p>Tier 1 is the shared core every AI role is expected to know; Tiers 2 and 3 list only what the AI Architect and the Forward Deployed Engineer add on top of it. Each item is a concept, pattern, metric or tool you should be able to explain or use.</p>
      <ul><li><strong>Tools vs concepts.</strong> Names in parentheses after a label (vLLM, RAGAS, LangGraph, Zod…) are tools or frameworks, not concepts. Know one well per category rather than all of them.</li>
      <li><strong>Caveated items.</strong> Distributed training (Tier 1) and feature stores (Tier 2) are marked with a caveat tag; skip them for LLM-app roles.</li>
      <li><strong>Shared items appear once, in Tier 1.</strong> Kubernetes, Terraform, RBAC, OAuth, MCP and golden datasets are designed by the Architect, implemented by the Engineer, and mapped into customer environments by the FDE.</li>
      <li><strong>Golden dataset</strong> is the one deliberate repeat: creation and metrics in Tier 1, strategy in Tier 2, customer-specific creation in Tier 3.</li>
      <li><strong>The track.</strong> Modules run in one order, from Programming foundations through Customer delivery; tier and role tags show who each module is for.</li>
      <li><strong>Trimming per role.</strong> LLM-app AI Engineer: Tier 1 minus ML-fundamentals depth. ML-heavy AI Engineer: Tier 1 in full. AI Architect: Tier 1 breadth plus Tier 2. FDE: Tier 1 plus Tier 3, with Tier 2 Reliability and Security as background reading.</li></ul>
      <p class="muted">Source: AI Roles Skills Taxonomy, Oct 20 2026. Built ${esc(D.meta.built_at || '')}.</p></div></details></div>`;
    main.innerHTML = html;
    main.querySelectorAll('.stage-strip a').forEach(x => x.onclick = e => { e.preventDefault(); const el = document.getElementById(x.getAttribute('href').slice(1)); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    const pace = $('#pace'); if (pace) pace.onchange = () => { S.pace = +pace.value; save(); renderDashboard(); };
    const rb = $('#randomBtn'); if (rb) rb.onclick = () => { const pool = ts.filter(t => t.status === 'ok' && !isLearned(t)); const pick = pool.length ? pool[Math.floor(Math.random() * pool.length)] : ts[0]; if (pick) location.hash = `#t.${pick.module}.${pick.id}`; };
  }

  function renderModule(mid) {
    const m = modById.get(mid); if (!m) return renderMissing();
    touchDay();
    const p = modProgress(m), st = modStatus(m), intro = m.intro;
    const first = m.ordered.find(t => inPath(t) && !isLearned(t)) || m.ordered[0];
    const reasons = {}; // suggested-order reasons are not stored separately; order list carries the sequence
    let html = `<div class="page-narrow">
      <div class="crumbs"><a href="#dashboard">Home</a><i>›</i><span>Stage ${stageOf(m).n}: ${esc(stageOf(m).title)}</span><i>›</i><span>Module ${pad2(m.num)}</span></div>
      <div class="mod-hero band" style="--tier: var(--t${m.tier}); ${stv(m)}">
        <div class="topic-meta">${pillTier(m.tier)}<span class="pill">${ROLE_FOR_TIER[m.tier]}</span><span class="pill">${m.topics.length} lessons</span><span class="pill">${fmtMin(m.topics.reduce((a, t) => a + (t.est_minutes || 12), 0))} total</span></div>
        <h1>${esc(m.title)}</h1>
        <div class="bar tier-${m.tier}"><i style="width:${p.pct}%"></i></div>
        <div class="muted">${p.done} of ${p.total} learned${S.modquiz[mid] ? ` · best module quiz ${S.modquiz[mid].best}/${S.modquiz[mid].of}` : ''}</div>
        <div class="btn-row"><a class="btn primary" href="#t.${m.id}.${first.id}">${p.done ? 'Continue module' : 'Start module'}</a><a class="btn" href="#q.${m.id}">Module quiz</a><a class="btn" href="#drill.${m.id}">Interview drill</a></div>
      </div>`;
    if (intro) {
      html += `<section class="section"><h2>Overview</h2><div class="prose">${md(intro.overview)}</div></section>
        <section class="section"><h2>Why it matters</h2><div class="lens">${['engineer', 'architect', 'fde'].map(r => `<div><b>${ROLE[r]}</b>${mdi(intro.why[r] || '')}</div>`).join('')}</div></section>
        <section class="section"><h2>Mental model</h2><div class="prose">${md(intro.mental_model)}</div></section>`;
    } else {
      html += `<div class="banner info">The module guide (overview, mental model, capstone) has not been generated yet. The lesson list below is in taxonomy order.</div>`;
    }
    html += `<section class="section"><h2>Lessons <span class="num">${intro ? 'suggested order' : 'taxonomy order'}</span></h2><ol class="topic-list">${m.ordered.map((t, i) => {
      const ts = topicStatus(t); return `<li><a href="#t.${m.id}.${t.id}" class="${t.status !== 'ok' ? 'missing' : ''}"><span class="n">${String(i + 1).padStart(2, '0')}</span>
        <span class="name"><span class="dot ${topicDot(t)}"></span>${esc(t.name)}${pillRel(t.agent_relevance)}${t.caveat ? '<span class="pill caveat">caveat</span>' : ''}${ts === 'skip' ? '<span class="pill">not on path</span>' : ''}${t.status !== 'ok' ? '<span class="pill">not generated</span>' : ''}</span>
        <span class="meta">${t.kind ? `<span class="pill">${esc(t.kind)}</span>` : ''}<span>${t.est_minutes || 12} min</span></span>${intro && intro.order_reasons && intro.order_reasons[t.id] ? `<span class="reason">${mdi(intro.order_reasons[t.id])}</span>` : ''}</a></li>`; }).join('')}</ol></section>`;
    if (intro && intro.capstone) {
      const cs = S.capstone[mid] || {};
      html += `<section class="section"><h2>Capstone</h2><div class="prose">${md(intro.capstone.brief)}</div>
        <h3 style="margin:18px 0 8px">Deliverables</h3><ul class="checklist">${intro.capstone.deliverables.map((d, i) => `<li><label class="${cs[i] ? 'done' : ''}"><input type="checkbox" data-cap="${i}" ${cs[i] ? 'checked' : ''}><span>${mdi(d)}</span></label></li>`).join('')}</ul>
        <h3 style="margin:18px 0 8px">Rubric</h3><ul class="list-cards">${intro.capstone.rubric.map((r, i) => `<li><span class="mark">${i + 1}</span><span>${mdi(r)}</span></li>`).join('')}</ul></section>`;
    }
    html += '</div>';
    main.innerHTML = html;
    highlight(main);
    main.querySelectorAll('[data-cap]').forEach(cb => cb.onchange = () => { S.capstone[mid] = S.capstone[mid] || {}; S.capstone[mid][cb.dataset.cap] = cb.checked; save(); cb.closest('label').classList.toggle('done', cb.checked); });
  }

  function renderTopic(key) {
    const t = topics.get(key); if (!t) return renderMissing();
    const m = modById.get(t.module);
    if (!loaded.has(m.id) && t.status !== 'missing') { main.innerHTML = loadingView('lesson'); ensureModule(m.id).then(() => { if (location.hash === '#t.' + m.id + '.' + t.id) renderTopic(key); }); return; }
    touchDay(); S.visits[key] = now(); S.last = key; save();
    const idx = m.ordered.indexOf(t); const prev = m.ordered[idx - 1], next = m.ordered[idx + 1];
    const nextMod = modules[modules.indexOf(m) + 1];
    const learned = isLearned(t), st = topicStatus(t), rec = reviewRec(key);
    const head = `<div class="crumbs"><a href="#dashboard">Home</a><i>›</i><a href="#m.${m.id}">${esc(m.title)}</a><i>›</i><span>${idx + 1} of ${m.ordered.length}</span></div>
      <div class="topic-head band" style="${stv(m)}">
        <div class="topic-meta">${pillRel(t.agent_relevance)}${pillTier(m.tier)}${t.kind ? `<span class="pill">${esc(t.kind)}</span>` : ''}${t.difficulty ? `<span class="pill">${esc(t.difficulty)}</span>` : ''}<span class="pill">${t.est_minutes || 12} min</span>${t.caveat ? `<span class="pill caveat" title="${esc(t.caveat)}">caveated</span>` : ''}${pillStatus(st)}${st === 'skip' ? '<span class="pill">not on your path</span>' : ''}</div>
        <h1>${esc(t.name)}</h1>
        <div class="topic-actions">
          <button class="btn ${learned ? 'good' : 'primary'}" id="learnBtn">${learned ? '✓ Learned' : 'Mark as learned'}</button>
          ${rec ? `<span class="muted" style="font-size:13px">${rec.due <= now() ? 'Due for review now' : 'Next review ' + new Date(rec.due).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>` : ''}
        </div>
      </div>`;
    if (t.status !== 'ok' && !t.tldr) {
      main.innerHTML = `<div class="page-narrow">${head}<div class="empty"><p><strong>This lesson has not been generated yet.</strong></p><p>Taxonomy item: ${esc(t.taxonomy_name || t.name)}${t.caveat ? ' · ' + esc(t.caveat) : ''}${t.note ? ' · ' + esc(t.note) : ''}</p>${t.errors ? `<pre style="text-align:left;font-size:12px">${esc(t.errors.join('\n'))}</pre>` : ''}</div>${pager(prev, next, m, nextMod)}</div>`;
      bindLearn(t); return;
    }
    const secs = [
      ['why', 'Why it exists', md(t.why)],
      ['how', 'How it works', md(t.how)],
      ...(t.diagram && t.diagram.code ? [['diagram', 'Diagram', `<div class="prose diagram-caption">${md(t.diagram.caption)}</div><pre><code class="language-mermaid">${esc(t.diagram.code)}</code></pre>`]] : []),
      ['example', t.format === 'crisp' ? 'Examples' : 'Example', md(t.example)],
      ...((t.patterns && t.patterns.length) ? [['patterns', 'Production patterns', `<ul class="list-cards patterns">${t.patterns.map(b => `<li><span class="mark">▸</span><span>${mdi(b)}</span></li>`).join('')}</ul>`]] : []),
      ['tradeoffs', 'Trade-offs', `<ul class="list-cards tradeoffs">${(t.tradeoffs || []).map(b => `<li><span class="mark">⇄</span><span>${mdi(b)}</span></li>`).join('')}</ul>`],
      ['pitfalls', 'Pitfalls', `<ul class="list-cards pitfalls">${(t.pitfalls || []).map(b => `<li><span class="mark">!</span><span>${mdi(b)}</span></li>`).join('')}</ul>`],
      ['when', 'When to use', md(t.when)],
      ['lens', 'Role lens', `<div class="lens">${['engineer', 'architect', 'fde'].map(r => `<div><b>${ROLE[r]}</b>${mdi((t.role_lens || {})[r] || '')}</div>`).join('')}</div>`],
    ];
    const related = (t.related || []).map(k => topics.get(k)).filter(Boolean);
    const ivq = t.interview || [];
    let html = `<div class="reader"><article>${head}
      ${t.status === 'invalid' ? `<div class="banner">This lesson failed validation and may be incomplete.<pre style="font-size:12px;white-space:pre-wrap">${esc((t.errors || []).join('\n'))}</pre></div>` : ''}
      ${t.caveat ? `<div class="banner">Caveat: ${esc(t.caveat)}.</div>` : ''}
      <div class="tldr"><div class="eyebrow">In one breath</div><div class="prose">${md(t.tldr)}</div></div>
      ${t.agent ? `<div class="agent-box"><div class="agent-box-head"><span class="agent-glyph" aria-hidden="true"></span><span>For agent builders</span>${pillRel(t.agent_relevance)}</div><div class="prose">${md(t.agent)}</div></div>` : ''}
      ${secs.map(([id, title, body], i) => `<section class="section" id="s-${id}"><h2><span class="num">${String(i + 1).padStart(2, '0')}</span>${title}</h2>${body.startsWith('<ul') || body.startsWith('<div') ? body : `<div class="prose">${body}</div>`}</section>`).join('')}
      ${related.length ? `<section class="section" id="s-related"><h2><span class="num">${pad2(secs.length + 1)}</span>Related lessons</h2><div class="chips">${related.map(r => `<a class="chip" href="#t.${r.module}.${r.id}"><span class="dot ${topicDot(r)}"></span>${esc(r.name)}<span class="sub">${esc(r.moduleTitle)}</span></a>`).join('')}</div></section>` : ''}
      <section class="section" id="s-check"><h2><span class="num">${pad2(secs.length + 2)}</span>Check yourself</h2>
        <div class="reveal"><div class="q prose">${md(t.check.q)}</div><button class="btn sm" id="revealBtn">Reveal a model answer</button><div class="a prose" id="answer" hidden>${md(t.check.a)}</div></div></section>
      ${ivq.length ? `<section class="section" id="s-interview"><h2><span class="num">${pad2(secs.length + 3)}</span>Interview questions</h2>
        <div class="ivq-list">${ivq.map((x, xi) => `<details class="ivq"><summary><span class="ivq-n">Q${xi + 1}</span><span>${mdi(x.q)}</span></summary><div class="prose">${md(x.a)}</div></details>`).join('')}</div>
        <div class="btn-row" style="margin-top:12px"><button class="btn sm" id="ivqAll">Expand all answers</button></div></section>` : ''}
      <section class="section" id="s-quiz"><h2><span class="num">${pad2(secs.length + (ivq.length ? 4 : 3))}</span>Quiz</h2><div class="quiz" id="quiz">${(t.quiz || []).map((q, qi) => `<div class="qcard" data-q="${qi}"><div class="q"><span class="n">Q${qi + 1}</span>${mdi(q.q)}</div><div class="opts">${q.options.map((o, oi) => `<button class="opt" data-o="${oi}"><span class="k">${'ABCD'[oi]}</span><span>${mdi(o)}</span></button>`).join('')}</div><div class="expl" hidden></div></div>`).join('')}<div id="quizResult"></div></div></section>
      <section class="section notes" id="s-notes"><h2><span class="num">${pad2(secs.length + (ivq.length ? 5 : 4))}</span>Your notes</h2><textarea id="notes" placeholder="What clicked, what is still fuzzy, how this connects to your own work…">${esc(S.notes[key] || '')}</textarea></section>
      ${t.sources && t.sources.length ? `<section class="section"><h2>Sources</h2><ul class="sources">${t.sources.map(s => `<li>${esc(s)}</li>`).join('')}</ul></section>` : ''}
      ${pager(prev, next, m, nextMod)}
    </article>
    <aside class="toc"><div class="toc-title">On this page</div>
      ${secs.map(([id, title]) => `<a href="#s-${id}" data-toc="s-${id}">${title}</a>`).join('')}
      <a href="#s-check" data-toc="s-check">Check yourself</a>${ivq.length ? '<a href="#s-interview" data-toc="s-interview">Interview questions</a>' : ''}<a href="#s-quiz" data-toc="s-quiz">Quiz</a>
      <div class="toc-side"><div class="toc-title">Module</div><a href="#m.${m.id}" style="border:0;padding-left:0">${esc(m.title)} →</a><div class="bar tier-${m.tier}"><i style="width:${modProgress(m).pct}%"></i></div><div class="muted" style="font-size:12px">${modProgress(m).done}/${modProgress(m).total} learned</div></div>
    </aside></div>`;
    main.innerHTML = html;
    highlight(main);
    bindLearn(t);
    $('#revealBtn').onclick = () => { $('#answer').hidden = false; $('#revealBtn').hidden = true; };
    const ivAll = $('#ivqAll'); if (ivAll) ivAll.onclick = () => { const all = [...main.querySelectorAll('details.ivq')]; const open = !all.every(d => d.open); all.forEach(d => { d.open = open; }); ivAll.textContent = open ? 'Collapse all answers' : 'Expand all answers'; };
    main.querySelectorAll('.toc a[data-toc]').forEach(a => a.onclick = e => { e.preventDefault(); const el = document.getElementById(a.dataset.toc); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
    const notes = $('#notes'); let nt; notes.oninput = () => { clearTimeout(nt); nt = setTimeout(() => { if (notes.value.trim()) S.notes[key] = notes.value; else delete S.notes[key]; save(); }, 300); };
    bindQuiz(t);
  }
  function pager(prev, next, m, nextMod) {
    return `<nav class="pager">${prev ? `<a class="prev" href="#t.${m.id}.${prev.id}"><span class="eyebrow">← Previous</span><span>${esc(prev.name)}</span></a>` : '<span></span>'}
      ${next ? `<a class="next" href="#t.${m.id}.${next.id}"><span class="eyebrow">Next →</span><span>${esc(next.name)}</span></a>` : nextMod ? `<a class="next" href="#m.${nextMod.id}"><span class="eyebrow">Next module →</span><span>${esc(nextMod.title)}</span></a>` : ''}</nav>`;
  }
  function bindLearn(t) {
    const b = $('#learnBtn'); if (!b) return;
    b.onclick = () => { const on = !isLearned(t); setLearned(t, on); b.className = 'btn ' + (on ? 'good' : 'primary'); b.textContent = on ? '✓ Learned' : 'Mark as learned'; toast(on ? 'Marked learned · review tomorrow' : 'Unmarked'); };
  }
  function bindQuiz(t) {
    const box = $('#quiz'); if (!box) return;
    const answers = {}; const total = (t.quiz || []).length;
    box.querySelectorAll('.qcard').forEach(card => {
      const qi = +card.dataset.q, q = t.quiz[qi];
      card.querySelectorAll('.opt').forEach(btn => btn.onclick = () => {
        if (answers[qi] != null) return;
        const oi = +btn.dataset.o; answers[qi] = oi === q.answer;
        card.querySelectorAll('.opt').forEach(o => { o.disabled = true; const i = +o.dataset.o; if (i === q.answer) o.classList.add('correct'); else if (i === oi) o.classList.add('wrong'); });
        const ex = card.querySelector('.expl'); ex.hidden = false; ex.innerHTML = `<b>${answers[qi] ? 'Correct.' : 'Not quite.'}</b> ${mdi(q.explanation)}`;
        if (Object.keys(answers).length === total) finishQuiz(t, Object.values(answers).filter(Boolean).length, total);
      });
    });
  }
  function finishQuiz(t, score, of) {
    const prev = S.quiz[t.key] || { best: 0, attempts: 0, of };
    S.quiz[t.key] = { best: Math.max(prev.best, score), last: score, attempts: prev.attempts + 1, of, at: now() };
    const perfect = score === of;
    if (perfect) { if (!isLearned(t)) { S.learned[t.key] = now(); } schedule(t.key, 1); } else if (isLearned(t)) { schedule(t.key, 0); }
    save(); refreshChrome();
    const r = $('#quizResult'); r.innerHTML = `<div class="quiz-result ${perfect ? 'good' : 'warn'}">${score}/${of} correct. ${perfect ? 'Marked learned; next review ' + new Date(S.review[t.key].due).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + '.' : 'Re-read the sections behind the misses, then try again later.'}</div>`;
    const b = $('#learnBtn'); if (b && isLearned(t)) { b.className = 'btn good'; b.textContent = '✓ Learned'; }
  }

  function renderModuleQuiz(mid) {
    const m = modById.get(mid); if (!m) return renderMissing();
    if (!loaded.has(mid)) { main.innerHTML = loadingView('quiz'); ensureModule(mid).then(() => { if (location.hash === '#q.' + mid) renderModuleQuiz(mid); }); return; }
    const pool = []; m.topics.filter(t => t.status === 'ok' && inPath(t)).forEach(t => (t.quiz || []).forEach(q => pool.push({ t, q })));
    for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
    const qs = pool.slice(0, Math.min(10, pool.length)); let i = 0, score = 0; const misses = [];
    const shell = `<div class="page-narrow"><div class="crumbs"><a href="#dashboard">Home</a><i>›</i><a href="#m.${m.id}">${esc(m.title)}</a><i>›</i><span>Module quiz</span></div><h1>${esc(m.title)} quiz</h1><p class="lede">${qs.length} questions drawn from the module's lessons. One attempt per question.</p><div id="mq"></div></div>`;
    main.innerHTML = shell; const box = $('#mq');
    if (!qs.length) { box.innerHTML = '<div class="empty">No lessons with quizzes are available in this module yet.</div>'; return; }
    function step() {
      if (i >= qs.length) {
        const prev = S.modquiz[mid] || { best: 0 }; S.modquiz[mid] = { best: Math.max(prev.best, score), last: score, of: qs.length, at: now() }; save();
        box.innerHTML = `<div class="quiz-result ${score === qs.length ? 'good' : 'warn'}">${score}/${qs.length} correct.</div>
          ${misses.length ? `<h3 style="margin:18px 0 8px">Revisit</h3><ul class="review-list">${[...new Set(misses)].map(t => `<li><a href="#t.${t.module}.${t.id}"><span>${esc(t.name)}</span><span class="sub">${esc(t.moduleTitle)}</span></a></li>`).join('')}</ul>` : '<p class="muted">No misses. Consider the capstone next.</p>'}
          <div class="btn-row" style="margin-top:16px"><a class="btn" href="#q.${mid}">Try again</a><a class="btn primary" href="#m.${mid}">Back to module</a></div>`;
        return;
      }
      const { t, q } = qs[i];
      box.innerHTML = `<div class="muted mono" style="margin-bottom:8px">${i + 1} / ${qs.length} · from “${esc(t.name)}”</div><div class="qcard"><div class="q">${mdi(q.q)}</div><div class="opts">${q.options.map((o, oi) => `<button class="opt" data-o="${oi}"><span class="k">${'ABCD'[oi]}</span><span>${mdi(o)}</span></button>`).join('')}</div><div class="expl" hidden></div><div class="btn-row" style="margin-top:12px"><button class="btn primary" id="mqNext" hidden>Next</button></div></div>`;
      box.querySelectorAll('.opt').forEach(btn => btn.onclick = () => {
        const oi = +btn.dataset.o; const ok = oi === q.answer; if (ok) score++; else misses.push(t);
        box.querySelectorAll('.opt').forEach(o => { o.disabled = true; const k = +o.dataset.o; if (k === q.answer) o.classList.add('correct'); else if (k === oi) o.classList.add('wrong'); });
        const ex = box.querySelector('.expl'); ex.hidden = false; ex.innerHTML = `<b>${ok ? 'Correct.' : 'Not quite.'}</b> ${mdi(q.explanation)} <a href="#t.${t.module}.${t.id}">Open lesson</a>`;
        const nb = $('#mqNext'); nb.hidden = false; nb.textContent = i === qs.length - 1 ? 'See results' : 'Next'; nb.onclick = () => { i++; step(); }; nb.focus();
      });
    }
    step();
  }

  function renderReview() {
    touchDay();
    const due = dueTopics();
    const need = [...new Set(due.map(t => t.module))].filter(id => !loaded.has(id));
    if (need.length) { main.innerHTML = loadingView('review queue'); ensureModules(need).then(() => { if (location.hash === '#review') renderReview(); }); return; }
    const upcoming = flat.filter(t => inPath(t) && S.review[t.key] && S.review[t.key].due > now()).sort((a, b) => S.review[a.key].due - S.review[b.key].due).slice(0, 12);
    let html = `<div class="page-narrow"><div class="crumbs"><a href="#dashboard">Home</a><i>›</i><span>Review</span></div><h1>Review queue</h1><p class="lede">Active recall on what you have learned, spaced so each lesson comes back just before you would forget it. Answer the question in your head, reveal, then grade yourself.</p><div id="rv"></div>`;
    html += `<h3 style="margin:28px 0 8px">Coming up</h3>${upcoming.length ? `<ul class="review-list">${upcoming.map(t => `<li><a href="#t.${t.module}.${t.id}"><span>${esc(t.name)}<div class="sub">${esc(t.moduleTitle)}</div></span><span class="sub">${new Date(S.review[t.key].due).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></a></li>`).join('')}</ul>` : '<p class="muted">Nothing scheduled yet. Mark lessons as learned or pass their quizzes to start a schedule.</p>'}</div>`;
    main.innerHTML = html;
    const box = $('#rv'); let i = 0, done = 0;
    function step() {
      if (i >= due.length) { box.innerHTML = `<div class="${done ? 'quiz-result good' : 'empty'}">${done ? `Session complete: ${done} lesson${done === 1 ? '' : 's'} reviewed.` : 'Nothing is due right now.'}</div>`; refreshChrome(); return; }
      const t = due[i]; const mode = (t.quiz && t.quiz.length && Math.random() < 0.5) ? 'quiz' : 'card';
      if (mode === 'quiz') {
        const q = t.quiz[Math.floor(Math.random() * t.quiz.length)];
        box.innerHTML = `<div class="flash"><div class="where">${i + 1} / ${due.length} · ${esc(t.moduleTitle)} · <a href="#t.${t.module}.${t.id}">${esc(t.name)}</a></div><div class="qcard" style="border:0;padding:0"><div class="q">${mdi(q.q)}</div><div class="opts">${q.options.map((o, oi) => `<button class="opt" data-o="${oi}"><span class="k">${'ABCD'[oi]}</span><span>${mdi(o)}</span></button>`).join('')}</div><div class="expl" hidden></div></div><div class="grades" id="grades" hidden></div></div>`;
        box.querySelectorAll('.opt').forEach(btn => btn.onclick = () => {
          const oi = +btn.dataset.o; const ok = oi === q.answer;
          box.querySelectorAll('.opt').forEach(o => { o.disabled = true; const k = +o.dataset.o; if (k === q.answer) o.classList.add('correct'); else if (k === oi) o.classList.add('wrong'); });
          const ex = box.querySelector('.expl'); ex.hidden = false; ex.innerHTML = `<b>${ok ? 'Correct.' : 'Not quite.'}</b> ${mdi(q.explanation)}`;
          const g = $('#grades'); g.hidden = false; g.innerHTML = ok ? `<button class="btn" data-g="1">Good <small>· ${STEPS[Math.min(STEPS.length - 1, Math.max(0, S.review[t.key].step) + 1)]}d</small></button><button class="btn good" data-g="2">Easy <small>· ${STEPS[Math.min(STEPS.length - 1, Math.max(0, S.review[t.key].step) + 2)]}d</small></button>` : `<button class="btn danger" data-g="0">Again <small>· 1d</small></button>`;
          g.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { schedule(t.key, +b.dataset.g); done++; i++; step(); });
        });
      } else {
        box.innerHTML = `<div class="flash"><div class="where">${i + 1} / ${due.length} · ${esc(t.moduleTitle)} · <a href="#t.${t.module}.${t.id}">${esc(t.name)}</a></div><div class="prose">${md(t.check ? t.check.q : t.tldr)}</div><div class="btn-row" style="margin-top:14px"><button class="btn primary" id="rvReveal">Reveal</button></div><div id="rvA" hidden><div class="prose" style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--line-strong)">${md(t.check ? t.check.a : t.tldr)}</div><div class="grades"><button class="btn danger" data-g="0">Again <small>· 1d</small></button><button class="btn" data-g="1">Good <small>· ${STEPS[Math.min(STEPS.length - 1, Math.max(0, S.review[t.key].step) + 1)]}d</small></button><button class="btn good" data-g="2">Easy <small>· ${STEPS[Math.min(STEPS.length - 1, Math.max(0, S.review[t.key].step) + 2)]}d</small></button></div></div></div>`;
        $('#rvReveal').onclick = () => { $('#rvA').hidden = false; $('#rvReveal').hidden = true; highlight(box); };
        box.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { schedule(t.key, +b.dataset.g); done++; i++; step(); });
      }
    }
    step();
  }

  function renderDrill(mid) {
    touchDay();
    const m = mid && modById.get(mid);
    const need = (m ? [m.id] : allModuleIds()).filter(id => !loaded.has(id));
    if (need.length) { main.innerHTML = loadingView('drill'); ensureModules(need).then(() => { if (location.hash === (m ? '#drill.' + m.id : '#drill')) renderDrill(mid); }); return; }
    const pool = [];
    (m ? m.ordered : flat).filter(t => t.status === 'ok').forEach(t => {
      if (t.check && t.check.q) pool.push({ t, q: t.check.q, a: t.check.a, kind: 'Check yourself' });
      (t.interview || []).forEach(x => pool.push({ t, q: x.q, a: x.a, kind: 'Interview' }));
    });
    for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
    const scope = m ? m.title : 'the whole track';
    main.innerHTML = `<div class="page-narrow"><div class="crumbs"><a href="#dashboard">Home</a><i>›</i>${m ? `<a href="#m.${m.id}">${esc(m.title)}</a><i>›</i>` : ''}<span>Interview drill</span></div>
      <h1>Interview drill</h1><p class="lede">${pool.length} questions from <strong>${esc(scope)}</strong>, shuffled. Answer out loud or in writing before you reveal; this does not change your review schedule unless you ask it to.</p>
      ${m ? '' : `<div class="btn-row" style="margin-bottom:16px"><span class="muted" style="font-size:13px">Narrow to a module:</span><select id="drillMod" class="select" style="width:auto"><option value="">Whole track</option>${modules.map(mm => `<option value="${mm.id}">${pad2(mm.num)} · ${esc(mm.title)}</option>`).join('')}</select></div>`}
      <div id="drill"></div></div>`;
    const sel = $('#drillMod'); if (sel) sel.onchange = () => { location.hash = sel.value ? `#drill.${sel.value}` : '#drill'; };
    const box = $('#drill'); let i = 0;
    if (!pool.length) { box.innerHTML = '<div class="empty">No lessons with check questions here yet.</div>'; return; }
    function step() {
      if (i >= pool.length) { box.innerHTML = `<div class="quiz-result good">Drill complete: ${pool.length} questions.</div><div class="btn-row" style="margin-top:14px"><a class="btn primary" href="${m ? '#drill.' + m.id : '#drill'}">Shuffle again</a></div>`; return; }
      const it = pool[i], t = it.t;
      box.innerHTML = `<div class="flash"><div class="where">${i + 1} / ${pool.length} · <span class="pill">${it.kind}</span> ${esc(t.moduleTitle)} · <a href="#t.${t.module}.${t.id}">${esc(t.name)}</a></div><div class="prose drill-q">${md(it.q)}</div>
        <div class="btn-row" style="margin-top:14px"><button class="btn primary" id="dReveal">Reveal model answer</button><button class="btn" id="dSkip">Skip</button></div>
        <div id="dA" hidden><div class="prose" style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--line-strong)">${md(it.a)}</div>
        <div class="grades"><button class="btn primary" id="dNext">Next question</button><button class="btn" id="dQueue">${S.review[t.key] ? 'Bring forward in review queue' : 'Add to review queue'}</button></div></div></div>`;
      $('#dReveal').onclick = () => { $('#dA').hidden = false; $('#dReveal').hidden = true; $('#dSkip').hidden = true; highlight(box); };
      $('#dSkip').onclick = () => { i++; step(); };
      $('#dNext').onclick = () => { i++; step(); };
      $('#dQueue').onclick = () => { S.review[t.key] = Object.assign(S.review[t.key] || { step: 0, reps: 0, lapses: 0 }, { due: now() }); save(); refreshChrome(); toast('Queued for review'); $('#dQueue').disabled = true; };
    }
    step();
  }

  function renderSearch(q) {
    const ql = q.toLowerCase(); const words = ql.split(/\s+/).filter(Boolean);
    const scored = flat.map(t => {
      const name = t.name.toLowerCase(), tl = (t.tldr || '').toLowerCase(), body = ((t.why || '') + ' ' + (t.how || '')).toLowerCase();
      let s = 0; words.forEach(w => { if (name.includes(w)) s += 10; if (tl.includes(w)) s += 4; if (body.includes(w)) s += 1; if (t.moduleTitle.toLowerCase().includes(w)) s += 2; });
      return { t, s };
    }).filter(x => x.s > 0).sort((a, b) => b.s - a.s).slice(0, 40);
    const hi = s => { let out = esc(s); words.forEach(w => { out = out.replace(new RegExp('(' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig'), '<mark>$1</mark>'); }); return out; };
    const snip = t => { const src = (t.tldr || '') + ' ' + (t.why || ''); const i = src.toLowerCase().indexOf(words[0] || ''); const s = Math.max(0, i - 80); return (s > 0 ? '…' : '') + src.slice(s, s + 220).replace(/[#*`>]/g, '') + '…'; };
    main.innerHTML = `<div class="page-narrow"><div class="crumbs"><a href="#dashboard">Home</a><i>›</i><span>Search</span></div><h1>“${esc(q)}”</h1><p class="lede">${scored.length} matching lesson${scored.length === 1 ? '' : 's'}.${loaded.size < modules.length ? ' <span class="muted">(full-text index still loading; names and summaries are searched now)</span>' : ''}</p>
      <div class="results">${scored.map(({ t }) => `<a class="result" href="#t.${t.module}.${t.id}"><div class="where">${esc(t.moduleTitle)} · Tier ${t.tier}${inPath(t) ? '' : ' · not on your path'}${t.status !== 'ok' ? ' · not generated yet' : ''}</div><div><span class="dot ${topicDot(t)}" style="display:inline-block;margin-right:8px"></span><strong>${hi(t.name)}</strong></div>${t.tldr ? `<div class="snip">${hi(snip(t))}</div>` : ''}</a>`).join('') || '<div class="empty">No matches. Try a shorter or different term.</div>'}</div></div>`;
  }

  function renderSettings() {
    const json = JSON.stringify(S);
    main.innerHTML = `<div class="page-narrow settings"><div class="crumbs"><a href="#dashboard">Home</a><i>›</i><span>Settings</span></div><h1>Settings</h1>
      <section class="section"><h2>Appearance</h2><div class="btn-row">${['system', 'light', 'dark'].map(t => `<button class="btn ${S.theme === t ? 'primary' : ''}" data-theme="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</div></section>
      <section class="section"><h2>Your progress</h2><p class="muted">Progress lives in this browser only. Copy the JSON to move it to another device or keep a backup; paste it below to restore.</p>
        <div class="btn-row" style="margin-bottom:8px"><button class="btn" id="copyBtn">Copy progress JSON</button><span class="muted" style="font-size:13px">${Object.keys(S.learned).length} learned · ${Object.keys(S.notes).length} notes · ${Object.keys(S.quiz).length} quizzes</span></div>
        <textarea id="exportBox" readonly>${esc(json)}</textarea>
        <h3 style="margin:18px 0 8px">Restore</h3><textarea id="importBox" placeholder="Paste progress JSON here"></textarea>
        <div class="btn-row" style="margin-top:8px"><button class="btn" id="importBtn">Import (merge)</button><button class="btn danger" id="resetBtn">Reset all progress</button><span id="resetConfirm" hidden><span class="muted" style="margin:0 8px">Really erase everything?</span><button class="btn danger sm" id="resetYes">Yes, erase</button><button class="btn sm" id="resetNo">Keep</button></span></div></section>
      <section class="section"><h2>Keyboard</h2><div class="prose" style="font-size:15px"><p><span class="kbd">/</span> focus search · <span class="kbd">n</span> or <span class="kbd">]</span> next lesson · <span class="kbd">p</span> or <span class="kbd">[</span> previous lesson · <span class="kbd">l</span> toggle learned · <span class="kbd">Esc</span> clear search.</p></div></section>
      <section class="section"><h2>About</h2><div class="prose" style="font-size:15px"><p>Built from the AI Roles Skills Taxonomy (Nugen IT Services, Oct 20 2026). ${TOTAL} lessons across 30 modules; data built ${esc(D.meta.built_at || '')}.</p></div></section></div>`;
    main.querySelectorAll('[data-theme]').forEach(b => b.onclick = () => { S.theme = b.dataset.theme; save(); applyTheme(); renderSettings(); });
    $('#copyBtn').onclick = async () => { const box = $('#exportBox'); try { await navigator.clipboard.writeText(box.value); toast('Copied'); } catch (e) { box.select(); toast('Select and copy manually'); } };
    $('#importBtn').onclick = () => { try { const inc = JSON.parse($('#importBox').value); ['learned', 'review', 'quiz', 'modquiz', 'notes', 'capstone', 'visits', 'open'].forEach(k => { if (inc[k]) S[k] = Object.assign({}, S[k], inc[k]); }); if (inc.days) S.days = [...new Set([...(S.days || []), ...inc.days])].sort(); if (inc.path) S.path = inc.path; save(); renderTree(); refreshChrome(); toast('Imported'); renderSettings(); } catch (e) { toast('That is not valid progress JSON'); } };
    $('#resetBtn').onclick = () => { $('#resetConfirm').hidden = false; };
    $('#resetNo').onclick = () => { $('#resetConfirm').hidden = true; };
    $('#resetYes').onclick = () => { S = defaults(); save(); applyTheme(); buildPathSelect(); renderTree(); refreshChrome(); toast('Progress erased'); location.hash = '#dashboard'; };
  }
  function renderMissing() { main.innerHTML = `<div class="page-narrow"><div class="empty">That page does not exist. <a href="#dashboard">Back to the dashboard</a>.</div></div>`; }

  /* ---------------- router ---------------- */
  function route() {
    const h = (location.hash || '#dashboard').slice(1);
    const parts = h.split('.');
    main.scrollTop = 0;
    if (h === '' || h === 'dashboard') renderDashboard();
    else if (parts[0] === 't' && parts.length >= 3) renderTopic(parts[1] + '/' + parts.slice(2).join('.'));
    else if (parts[0] === 'm' && parts[1]) renderModule(parts[1]);
    else if (parts[0] === 'q' && parts[1]) renderModuleQuiz(parts[1]);
    else if (h === 'review') renderReview();
    else if (parts[0] === 'drill') renderDrill(parts[1]);
    else if (h === 'settings') renderSettings();
    else if (h === 'search') { if (search.value.trim()) renderSearch(search.value.trim()); else renderDashboard(); }
    else if (h.startsWith('s-')) { /* in-page anchor from toc; keep current view */ return; }
    else renderMissing();
    markActive(); refreshChrome(); main.focus({ preventScroll: true });
  }
  window.addEventListener('hashchange', route);

  /* ---------------- boot ---------------- */
  search.placeholder = `Search ${TOTAL} lessons  ( / )`;
  applyTheme(); buildPathSelect(); renderTree(); route();
  setTimeout(prefetchAll, 800);
})();
