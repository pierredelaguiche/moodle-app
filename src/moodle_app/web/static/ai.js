'use strict';
/* AI: the one page that can reach the LLM. The note-taker (an editable plan), generated files, the prompts, and a question about chosen files.
   PDFs are turned into text behind the scenes, once, only when notes or a question need it. Every call that may send text to the model goes through aiApi() below, which
   marks it with X-Page: ai; the server refuses those endpoints without it, and no other page file contains them. */
(function () {
  const aiApi = (path, body) => api(path, body, { 'X-Page': 'ai' });
  const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  const ALL = '*';
  const KINDS = [
    ['Per lecture / workshop', [['lectures', 'Lecture notes'], ['summaries', 'Workshop summaries'], ['code', 'Workshop code files']]],
    ['Per course / coursework', [['coursework', 'Coursework notes (with deadlines)'], ['course', 'Course notes (whole course)']]],
  ];
  const KIND_LABEL = { lecture_notes: 'Lecture', workshop_summary: 'Workshop summary', workshop_code: 'Workshop code', coursework_notes: 'Coursework', study_guide: 'Course notes' };
  const store = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem('ai.' + k)); localStorage.setItem('ai.' + k, JSON.stringify(v)); } catch (e) {} return null; };

  let host = null, S = null, picked = [], mod = '', plan = null, ticked = new Set(), gen = null, chosen = new Set(), showGen = false;
  const on = Object.assign({ lectures: true, summaries: true, code: false, coursework: false, course: false }, store('kinds') || {});

  const el = (tag, props, ...kids) => h(tag, props || {}, ...kids);
  const sel = (opts, value, onchange, label) => h('select', { 'aria-label': label, onchange: (e) => onchange(e.target.value) },
    opts.map((o) => { const [v, t] = Array.isArray(o) ? o : [o, o]; return h('option', { value: v, text: t, selected: v === value }); }));
  const busy = async (btn, fn) => { btn.disabled = true; try { await fn(); } catch (e) { toast(e.message, { err: true }); } finally { btn.disabled = false; } };
  const kb = (n) => (n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : (n / 1048576).toFixed(1) + ' MB');
  const when = (t) => new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  const jobRunning = () => !!(App.S.badges && App.S.badges.job_running);
  const modOf = (rel) => (S ? S.modules : []).filter((m) => rel === m || rel.startsWith(m + '/')).sort((a, b) => b.length - a.length)[0] || rel.split('/')[0];
  const scopeLabel = (m) => (m === ALL ? `Everything in ${S.root}` : m);

  /* ---------- header */
  function head() {
    const chk = h('button', { text: 'Check connection', title: 'One tiny call: proves the login works and reads your plan usage' });
    chk.onclick = () => busy(chk, async () => { const r = await aiApi('/api/ai/check', {}); S.usage = r.usage; S.to = r.to; S.limit_problem = r.limit_problem; S.error = null; draw(); toast('Connected: ' + r.to); });
    const files = h('button', { text: showGen ? 'Hide generated files' : 'Check generated files', title: 'Every note and every deadline saved from notes' });
    files.onclick = () => busy(files, async () => { showGen = !showGen; if (showGen) gen = await api('/api/ai/generated', {}); draw(); if (showGen) $('#ai-gen').scrollIntoView({ behavior: 'smooth' }); });
    return h('div', { class: 'phead' }, h('h1', { text: 'AI' }), h('span', { class: 'sub', text: S.error ? 'Not connected' : 'Sends text to ' + S.to + '. Only this page and the Study assistant can.' }), h('span', { class: 'sp' }), files, chk);
  }
  function setupHelp() {
    return h('section', { class: 'card ai-card ai-warn' }, h('h2', { text: 'Connect a model first' }), h('p', { text: S.error }),
      h('p', { class: 'muted', text: 'Easiest: log in to Claude Code once (run `claude` in Terminal), then set "llm": {"provider": "auto", "model": "haiku"} in config.json. Or add an API key under Settings › Connections. Generated files and the prompts work without a model.' }));
  }

  /* ---------- Note-taker with an editable plan */
  const sumPlan = () => plan ? plan.jobs.filter((j) => ticked.has(j.module + '\0' + j.key)) : [];
  function notesCard() {
    const mods = S.modules, cur = mod === ALL || mods.includes(mod) ? mod : mods[0];
    const m = sel([...mods.map((x) => [x, x]), [ALL, scopeLabel(ALL) + ' (all modules)']], cur, (v) => { mod = v; store('mod', v); if (v === ALL) picked = []; plan = null; draw(); }, 'Scope');
    const wk = h('input', { type: 'text', size: 3, placeholder: 'all', 'aria-label': 'Only this week' });
    const out = h('div', { class: 'ai-out' });
    const body = (select) => ({ module: m.value, what: KINDS.flatMap(([, ks]) => ks).filter(([k]) => on[k]).map(([k]) => k).join(',') || 'lectures', week: wk.value.trim() ? +wk.value : null, rels: m.value === ALL ? [] : picked, select });
    const pv = h('button', { text: 'Show plan' }), create = h('button', { class: 'primary', text: 'Create notes', disabled: true });
    const total = h('span', { class: 'muted' });
    function paint() {
      clear(out);
      if (!plan) return;
      out.append(h('p', { class: 'muted', text: plan.usage }));
      if (plan.limit_problem) out.append(h('p', { class: 'err', text: plan.limit_problem + ' Nothing will be sent.' }));
      if (!plan.jobs.length) { out.append(h('p', { class: 'muted', text: 'Nothing to write: everything is up to date' + (plan.skipped ? ` (${plan.skipped} skipped, up to date or edited by you)` : '') + '.' })); create.disabled = true; total.textContent = ''; return; }
      const bar = h('div', { class: 'row' }, h('b', { text: 'Edit the plan:' }),
        h('button', { class: 'ghost', text: 'All', onclick: () => { plan.jobs.forEach((j) => ticked.add(j.module + '\0' + j.key)); paint(); } }),
        h('button', { class: 'ghost', text: 'None', onclick: () => { ticked.clear(); paint(); } }),
        h('button', { class: 'ghost', text: 'Only new', onclick: () => { ticked.clear(); plan.jobs.filter((j) => j.status === 'new').forEach((j) => ticked.add(j.module + '\0' + j.key)); paint(); } }), total);
      const list = h('div', { class: 'ai-plan' });
      let lastMod = null;
      plan.jobs.forEach((j) => {
        if (j.module !== lastMod && (plan.scope === 'all')) list.append(h('div', { class: 'ai-mod', text: j.module }));
        lastMod = j.module;
        const id = j.module + '\0' + j.key;
        list.append(h('label', { class: 'row ai-job' }, h('input', { type: 'checkbox', checked: ticked.has(id), onchange: (e) => { e.target.checked ? ticked.add(id) : ticked.delete(id); upd(); } }),
          h('span', { class: 'badge', text: KIND_LABEL[j.kind] || j.kind }), h('span', { class: 'grow', text: j.title + (j.week ? ` (week ${j.week})` : '') }),
          j.status !== 'new' ? h('span', { class: 'badge b-update', title: j.note || 'Written before; the source changed or you forced it', text: j.status === 'refresh' ? 'Out of date' : 'Replace' }) : null,
          h('span', { class: 'muted num', text: '~' + j.in_tokens.toLocaleString() + ' tokens' })));
      });
      out.append(bar, list);
      upd();
    }
    function upd() {
      const j = sumPlan(), t = j.reduce((a, x) => a + x.in_tokens + x.out_tokens, 0);
      total.textContent = `${j.length} of ${plan.jobs.length} ticked · about ${t.toLocaleString()} tokens`;
      create.textContent = j.length ? `Create ${plural(j.length, 'note')}` : 'Create notes';
      create.disabled = !j.length || !!plan.limit_problem;
    }
    pv.onclick = () => busy(pv, async () => {
      pv.textContent = 'Planning…';
      try {
        plan = await aiApi('/api/ai/plan', body());
        if (plan.error) { plan = null; throw new Error(plan.error); }
        ticked = new Set(plan.jobs.filter((j) => j.status === 'new').map((j) => j.module + '\0' + j.key));
        if (!ticked.size) plan.jobs.forEach((j) => ticked.add(j.module + '\0' + j.key));
        paint();
      } finally { pv.textContent = 'Show plan'; }
    });
    create.onclick = () => busy(create, async () => {
      const j = sumPlan(); if (!j.length) return;
      const t = j.reduce((a, x) => a + x.in_tokens, 0), to = j.reduce((a, x) => a + x.out_tokens, 0);
      const ok = await dialog({ title: `Create ${plural(j.length, 'note')}?`, text: `Sends the course text to ${plan.to}. About ${t.toLocaleString()} tokens in, up to ${to.toLocaleString()} out. ${plan.usage} The run stops by itself if your plan limit is reached; what is written stays.`, ok: 'Create' });
      if (!ok) return;
      await Jobs.run(aiApi('/api/ai/notes', body(j.map((x) => ({ module: x.module, key: x.key })))));
      plan = null; clear(out); await refresh(false); if (showGen) gen = await api('/api/ai/generated', {}); draw();
    });
    const kinds = KINDS.map(([title, ks]) => h('div', { class: 'ai-kinds' }, h('span', { class: 'muted', text: title }), ks.map(([k, l]) => h('label', { class: 'row' },
      h('input', { type: 'checkbox', checked: on[k], onchange: (e) => { on[k] = e.target.checked; store('kinds', on); plan = null; clear(out); create.disabled = true; } }), l))));
    paint();
    return h('section', { class: 'card ai-card' }, h('h2', { text: 'Note-taker' }),
      h('p', { class: 'muted', text: 'Writes notes under <module>/Notes. Pick one module or everything in the study folder, choose what to write, look at the plan and untick what you do not want. Nothing is sent until you press Create. Files you edited are never overwritten. Deadlines found in coursework notes go to your Calendar.' }),
      h('div', { class: 'row' }, m, h('label', { class: 'row muted' }, 'Week', wk)), kinds,
      picked.length && cur !== ALL ? h('div', { class: 'row' }, h('span', { class: 'badge', text: plural(picked.length, 'selected item') + ' from Files' }), h('span', { class: 'muted', text: picked.map((r) => r.split('/').pop()).slice(0, 4).join(', ') + (picked.length > 4 ? '…' : '') }),
        h('button', { class: 'ghost', text: 'Use the whole module', onclick: () => { picked = []; plan = null; draw(); } })) : null,
      h('div', { class: 'row' }, pv, create), out);
  }

  /* ---------- Generated files */
  function genCard() {
    if (!showGen || !gen) return null;
    const open = (rel) => go('edit', rel);
    const rowOf = (f, kind) => h('div', { class: 'row ai-gen' }, h('a', { href: '#/edit/' + encodeURIComponent(f.rel), class: 'grow', text: f.name, title: f.rel }),
      f.state ? h('span', { class: 'badge' + (f.state === 'yours' ? ' b-update' : ''), text: f.state === 'yours' ? 'Edited / yours' : 'Generated' }) : null,
      h('span', { class: 'muted num', text: kb(f.size) + ' · ' + when(f.mtime) }),
      h('button', { class: 'ghost', text: 'Ask about it', onclick: () => { chosen.add(f.rel); mod = modOf(f.rel); draw(); $('#ai-ask').scrollIntoView({ behavior: 'smooth' }); } }));
    const body = gen.modules.length ? gen.modules.map((g) => h('div', { class: 'ai-genmod' }, h('h3', { text: g.module }),
      g.notes.length ? h('div', {}, h('div', { class: 'muted', text: 'Notes' }), g.notes.map((f) => rowOf(f, 'note'))) : null))
      : [h('p', { class: 'muted', text: 'Nothing generated yet. Use the Note-taker.' })];
    const dl = gen.deadlines.length ? h('div', { class: 'ai-genmod' }, h('h3', { text: 'Deadlines saved from notes (in your Calendar)' }), gen.deadlines.map((d) => h('div', { class: 'row ai-gen' },
      h('span', { class: 'grow', text: d.title }), h('span', { class: 'muted', text: (d.course ? d.course + ' · ' : '') + new Date(d.start_ts * 1000).toLocaleString(undefined, d.all_day ? { dateStyle: 'medium' } : { dateStyle: 'medium', timeStyle: 'short' }) }),
      h('button', { class: 'ghost', text: 'Remove', onclick: safe(async () => { gen.deadlines = (await api('/api/ai/deadline/remove', { id: d.id })).deadlines; draw(); }) })))) : null;
    return h('section', { class: 'card ai-card', id: 'ai-gen' }, h('h2', { text: 'Generated files' }), body, dl);
  }

  /* ---------- Prompts */
  function promptsCard() {
    let cur = store('prompt') && S.prompts.some((p) => p.name === store('prompt')) ? store('prompt') : S.prompts[0].name;
    const ta = h('textarea', { rows: 14, spellcheck: 'false', 'aria-label': 'Prompt text' }), info = h('span', { class: 'muted' });
    const load = () => { const p = S.prompts.find((x) => x.name === cur); ta.value = p.text; info.textContent = p.custom ? 'Your version is in use.' : 'Built-in version.'; };
    const pick = sel(S.prompts.map((p) => [p.name, (KIND_LABEL[p.name] || p.name.replace(/_/g, ' '))]), cur, (v) => { cur = v; store('prompt', v); load(); }, 'Prompt');
    const save = h('button', { class: 'primary', text: 'Save' }), reset = h('button', { text: 'Reset to built-in' });
    save.onclick = () => busy(save, async () => { S.prompts = (await aiApi('/api/ai/prompt', { name: cur, text: ta.value })).prompts; load(); toast('Saved. Notes made with the old prompt count as out of date.'); });
    reset.onclick = () => busy(reset, async () => { S.prompts = (await aiApi('/api/ai/prompt', { name: cur, text: null })).prompts; load(); toast('Back to the built-in prompt'); });
    load();
    return h('section', { class: 'card ai-card' }, h('h2', { text: 'Prompts' }),
      h('p', { class: 'muted', text: 'The instructions sent with each file. Keep {{text}}: the course text goes there. Also available: {{module}} {{title}} {{week}} {{part}}. The coursework prompt ends with a deadlines line the app reads; keep it to get deadlines saved.' }),
      h('div', { class: 'row' }, pick, info), ta, h('div', { class: 'row' }, save, reset));
  }

  /* ---------- Ask */
  function askCard() {
    const mods = S.modules, cur = mods.includes(mod) ? mod : mods[0];
    const m = sel(mods, cur, (v) => { mod = v; store('mod', v); loadFiles(v); }, 'Module');
    const list = h('div', { class: 'ai-files' }), chips = h('div', { class: 'row' });
    const subject = h('input', { type: 'search', placeholder: 'Filter by subject or note name', 'aria-label': 'Filter files', oninput: () => showList() });
    const q = h('textarea', { rows: 4, 'aria-label': 'Question', placeholder: 'Ask about the chosen files, e.g. "What does lecture 3 say about parsing?" or "Is anything missing from this extraction?"' });
    const ans = h('div', { class: 'ai-out' }), ask = h('button', { class: 'primary', text: 'Ask' });
    let files = [];
    const matching = () => files.filter((f) => f.toLowerCase().includes(subject.value.trim().toLowerCase()));
    function paintChips() {
      clear(chips);
      [...chosen].forEach((r) => chips.append(h('span', { class: 'badge ai-chip' }, r.split('/').pop(), h('button', { class: 'ghost', 'aria-label': 'Remove ' + r, text: '×', onclick: () => { chosen.delete(r); paintChips(); showList(); } }))));
      if (chosen.size) chips.append(h('button', { class: 'ghost', text: 'Clear', onclick: () => { chosen.clear(); paintChips(); showList(); } }));
    }
    function showList() {
      clear(list);
      const ms = matching();
      ms.forEach((f) => list.append(h('label', { class: 'row' }, h('input', { type: 'checkbox', checked: chosen.has(f), onchange: (e) => { e.target.checked ? chosen.add(f) : chosen.delete(f); paintChips(); } }), f.slice(f.indexOf('/') + 1))));
      if (!ms.length) list.append(h('span', { class: 'muted', text: files.length ? 'No file matches.' : 'No PDFs, text files or notes in this module.' }));
    }
    async function loadFiles(v) { try { files = (await api('/api/ai/files', { module: v })).files; } catch (e) { files = []; } showList(); }
    ask.onclick = () => busy(ask, async () => {
      clear(ans).append(h('span', { class: 'muted', text: 'Asking…' }));
      try {
        const r = await aiApi('/api/ai/ask', { question: q.value, rels: [...chosen] });
        const copy = h('button', { class: 'ghost', text: 'Copy answer', onclick: async () => { try { await navigator.clipboard.writeText(r.answer); toast('Copied', { ms: 1200 }); } catch (e) {} } });
        clear(ans).append(h('pre', { class: 'pvtext ai-answer', text: r.answer }), h('div', { class: 'row' }, h('span', { class: 'muted', text: `${r.in_tokens.toLocaleString()} tokens in, ${r.out_tokens.toLocaleString()} out${r.files.length ? ' · from ' + r.files.join(', ') : ' · no files, just your question'}${r.truncated ? ' · text was cut to fit' : ''}. ${r.usage}` }), copy));
      } catch (e) { clear(ans).append(h('p', { class: 'err', text: e.message })); }
    });
    loadFiles(m.value); paintChips();
    return h('section', { class: 'card ai-card', id: 'ai-ask' }, h('h2', { text: 'Ask' }),
      h('p', { class: 'muted', text: 'Ask about one note, a subject or any files. Filter the list ("parsing", "week 3", a lecture name), tick what is relevant, then ask. Their text (extracted once, reused) is sent with your question.' }),
      h('div', { class: 'row' }, m, subject, h('button', { class: 'ghost', text: 'Tick all shown', onclick: () => { matching().forEach((f) => chosen.add(f)); paintChips(); showList(); } })), list, chips, q, h('div', { class: 'row' }, ask), ans);
  }

  function draw() {
    if (!host) return;
    const keep = $('#main').scrollTop;
    clear(host).append(head(), h('p', { class: 'muted', text: S.usage }), S.error ? setupHelp() : null, h('div', { class: 'ai-grid one' }, notesCard()), genCard(), promptsCard(), askCard());
    $('#main').scrollTop = keep;
  }
  async function refresh(redraw = true) { S = await aiApi('/api/ai/state', {}); if (!S.modules.length) S.modules = ['']; if (redraw) draw(); }

  App.pages.ai = {
    title: 'AI',
    async mount(el_, arg) {
      host = h('div', { class: 'page' }, h('div', { class: 'empty', text: 'Loading…' })); el_.append(host);
      picked = []; plan = null; chosen = new Set(); mod = store('mod') || mod;
      if (arg && arg.startsWith('notes:')) { picked = arg.slice(6).split('|').filter(Boolean); if (picked.length) { mod = modOf(picked[0]); picked.forEach((r) => { if (/\.(pdf|txt|md)$/i.test(r)) chosen.add(r); }); } }
      try { await refresh(); if (picked.length && modOf(picked[0]) !== mod) { mod = modOf(picked[0]); draw(); } } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'The AI page could not load' }), e.message)); }
      return () => { host = null; };
    },
  };
})();
