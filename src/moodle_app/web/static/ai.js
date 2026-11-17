'use strict';
/* AI: the page that reaches the LLM (with the Study assistant). One Create panel (notes per week or per category for the
   whole term, or a study guide; always an editable plan first), Chat with the course (the same backend as the Study
   assistant, with files you tick as context), and two panels opened from the header: the Library (what was generated,
   deadlines saved from notes) and the Prompts. PDFs are turned into text behind the scenes, once, only when needed.
   Every call that may send text to the model goes through aiApi() below, which marks it with X-Page: ai; the server
   refuses those endpoints without it. */
(function () {
  const aiApi = (path, body) => api(path, body, { 'X-Page': 'ai' });
  const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  const ALL = '*';
  const KINDS = [['lectures', 'Lectures'], ['summaries', 'Workshops'], ['code', 'Code'], ['coursework', 'Coursework']];
  const GROUPS = [['week', 'Per week'], ['term', 'Per category (whole term)']];
  const KIND_LABEL = { lecture_notes: 'Lectures', workshop_summary: 'Workshops', workshop_code: 'Workshop code', coursework_notes: 'Coursework', category_notes: 'Whole term', category_notes_merge: 'Whole term: merge', study_guide: 'Study guide', study_guide_merge: 'Study guide: merge' };
  const store = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem('ai.' + k)); localStorage.setItem('ai.' + k, JSON.stringify(v)); } catch (e) {} return null; };

  let host = null, S = null, picked = [], mod = '', plan = null, ticked = new Set(), skipped = new Set(), openJobs = new Set(), shutDirs = new Set(), lib = null;
  let make = store('make') === 'guide' ? 'guide' : 'notes', group = null, week = '';
  const on = Object.assign({ lectures: true, summaries: true, code: false, coursework: false }, store('kinds') || {});
  const panels = Object.assign({ library: false, prompts: false }, store('panels') || {});
  const talk = { msgs: [], chosen: new Set(), draft: '', busy: false };          // Chat with the course: kept while the page is open

  const sel = (opts, value, onchange, label) => h('select', { 'aria-label': label, onchange: (e) => onchange(e.target.value) },
    opts.map((o) => { const [v, t] = Array.isArray(o) ? o : [o, o]; return h('option', { value: v, text: t, selected: v === value }); }));
  /** A segmented choice: one button per option, the current one pressed. */
  const seg = (opts, value, onchange, label) => h('div', { class: 'seg', role: 'group', 'aria-label': label },
    opts.map(([v, t]) => h('button', { text: t, 'aria-pressed': String(v === value), onclick: () => { if (v !== value) onchange(v); } })));
  const busy = async (btn, fn) => { btn.disabled = true; try { await fn(); } catch (e) { toast(e.message, { err: true }); } finally { btn.disabled = false; } };
  const kb = (n) => (n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : (n / 1048576).toFixed(1) + ' MB');
  const when = (t) => new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  const modOf = (rel) => (S ? S.modules : []).filter((m) => rel === m || rel.startsWith(m + '/')).sort((a, b) => b.length - a.length)[0] || rel.split('/')[0];
  const scopeLabel = (m) => (m === ALL ? `Everything in ${S.root}` : m);

  /* ---------- usage: shown once, as the chip in the header */
  const HIGH = 0.6;                                              // above this share of a plan window, the chip warns and confirms mention it
  const windows = (u) => [['5h', u && u.five_hour], ['7d', u && u.seven_day]].filter(([, w]) => w);
  const pct = (w) => Math.round(w.util * 100) + '%';
  /**
   * The one place plan usage is shown. Short text, the rest in the tooltip, so no card or dialog repeats it.
   * @param {{five_hour: ?{util: number}, seven_day: ?{util: number}, overage: ?string, seen: ?number, app_tokens_7d: number}} u `usage.summary` from the server
   * @returns {HTMLElement} `Claude · 5h 12% · 7d 40%`, or the app's own 7-day token count when the provider reports no plan windows
   */
  function usageChip(u) {
    const wins = windows(u), sent = ((u && u.app_tokens_7d) || 0).toLocaleString() + ' tokens';
    const tip = [u && u.seen ? 'Checked ' + new Date(u.seen * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : 'Plan usage not checked yet',
      u && u.overage ? 'Extra usage ' + (u.overage === 'allowed' ? 'on' : 'off') : null, sent + ' sent by this app in 7 days'].filter(Boolean).join('\n');
    return h('span', { class: 'badge' + (wins.some(([, w]) => w.util > HIGH) ? ' b-update' : ''), id: 'ai-usage', title: tip, text: wins.length ? ['Claude', ...wins.map(([n, w]) => n + ' ' + pct(w))].join(' · ') : sent + ' · 7d' });
  }
  /** Usage worth a sentence in a confirm: only a window above HIGH. @returns {string} '' or ' 5h window at 72%.' */
  const usageHigh = (u) => windows(u).filter(([, w]) => w.util > HIGH).map(([n, w]) => ` ${n} window at ${pct(w)}.`).join('');
  /** Every reply that carries `usage` ends here: the chip is replaced in place, nothing else shows the numbers. */
  function setUsage(u) { if (!u) return; S.usage = u; const c = $('#ai-usage'); if (c) c.replaceWith(usageChip(u)); }

  /* ---------- header: usage, the model, and the two panels */
  function head() {
    const toggle = (k, text, title) => h('button', { text, title, 'aria-pressed': String(panels[k]), dataset: { panel: k }, onclick: safe(async () => {
      panels[k] = !panels[k]; store('panels', panels);
      if (k === 'library' && panels.library) lib = await api('/api/ai/generated', {});
      draw(); if (panels[k]) $(k === 'library' ? '#ai-lib' : '#ai-prompts').scrollIntoView({ behavior: 'smooth' });
    }) });
    return h('div', { class: 'phead' }, h('h1', { text: 'AI' }), usageChip(S.usage), h('span', { class: 'sub', id: 'ai-model', text: S.error ? 'Not connected' : modelLine(S.targets.notes) }), h('span', { class: 'sp' }),
      toggle('library', 'Library', 'Notes, study guides and the deadlines saved from notes'), toggle('prompts', 'Prompts', 'The instructions sent with each file'));
  }
  function setupHelp() {
    return h('section', { class: 'card ai-card ai-warn' }, h('h2', { text: 'Connect a model first' }), h('p', { text: S.error }),
      h('p', { class: 'muted' }, 'Run `claude` once in Terminal to sign in, or choose a model under ', h('a', { href: '#/settings', text: 'Settings › Connections' }), '.'));
  }

  /* ---------- Create: what to make, then an editable plan, then one confirm */
  const excludeBody = () => { const o = {}; skipped.forEach((k) => { const [m, f] = k.split('\0'); (o[m] = o[m] || []).push(f); }); return o; };
  const sumPlan = () => plan ? plan.jobs.filter((j) => ticked.has(j.module + '\0' + j.key)) : [];
  function createCard() {
    const mods = S.modules, cur = mod === ALL || mods.includes(mod) ? mod : mods[0];
    const reset = () => { plan = null; draw(); };
    const m = sel([...mods.map((x) => [x, x]), [ALL, scopeLabel(ALL) + ' (all modules)']], cur, (v) => { mod = v; store('mod', v); if (v === ALL) picked = []; reset(); }, 'Module');
    const term = group === 'term';
    const wk = h('input', { type: 'text', size: 3, placeholder: 'all', value: term ? '' : week, disabled: term, 'aria-label': 'Only this week', title: term ? 'A whole-term note covers every week' : 'Leave empty for every week', oninput: (e) => { week = e.target.value.trim(); } });
    const out = h('div', { class: 'ai-out' });
    const what = () => (make === 'guide' ? 'course' : KINDS.filter(([k]) => on[k]).map(([k]) => k).join(',') || 'lectures');
    const body = (select) => ({ module: m.value, what: what(), group: make === 'guide' ? 'week' : group, week: make === 'notes' && !term && week ? +week : null, rels: m.value === ALL || make === 'guide' ? [] : picked, select, exclude: excludeBody() });
    const pv = h('button', { text: 'Show plan' }), create = h('button', { class: 'primary', text: 'Create', disabled: true });
    const total = h('span', { class: 'muted' });
    function paint() {
      clear(out);
      if (!plan) return;
      setUsage(plan.usage);                                       // a plan may have asked Claude for fresh numbers
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
        const all = [...(j.files || []), ...(j.excluded || [])].sort((x, y) => x.localeCompare(y, undefined, { numeric: true }));
        const jid = id;
        const many = all.length > 1, isOpen = openJobs.has(jid);
        list.append(h('div', { class: 'row ai-job' }, h('input', { type: 'checkbox', 'aria-label': 'Write ' + j.title, checked: ticked.has(id), onchange: (e) => { e.target.checked ? ticked.add(id) : ticked.delete(id); upd(); } }),
          many ? h('button', { class: 'ghost', 'aria-expanded': String(isOpen), title: 'Choose which files go into this note', text: isOpen ? '▾' : '▸', onclick: () => { isOpen ? openJobs.delete(jid) : openJobs.add(jid); paint(); } }) : null,
          h('span', { class: 'badge', text: KIND_LABEL[j.kind] || j.kind }), h('span', { class: 'grow', title: all.join('\n'), text: (/^Week \d+ /.test(j.title) ? j.title : j.title + (j.week ? ` (week ${j.week})` : '')) + (j.via && j.note ? ' · ' + j.note : many ? ` · ${j.files.length} of ${all.length} files` : '') + (j.files.length ? '' : ' · ' + (j.note || '')) }),
          j.status !== 'new' && j.files.length ? h('span', { class: 'badge b-update', title: j.note || 'Written before; the source changed or you forced it', text: j.status === 'refresh' ? 'Out of date' : 'Replace' }) : null,
          h('span', { class: 'muted num', text: '~' + j.in_tokens.toLocaleString() + ' tokens' })));
        if (many && isOpen) list.append(fileTree(j, all));
      });
      out.append(bar, list);
      upd();
    }
    /* the files of one note, by folder: untick a file or a whole folder to leave it out; folders collapse */
    function fileTree(j, all) {
      const box = h('div', { class: 'ai-files ai-tree' }), key = (f) => j.module + '\0' + f;
      const dirs = new Map();
      all.forEach((f) => { const i = f.lastIndexOf('/'), d = i < 0 ? '' : f.slice(0, i); (dirs.get(d) || dirs.set(d, []).get(d)).push(f); });
      const change = (files, keep) => { files.forEach((f) => (keep ? skipped.delete(key(f)) : skipped.add(key(f)))); replan(); };
      const names = new Set([...dirs.keys()].filter(Boolean));          // folders with only sub-folders get a heading as well
      dirs.forEach((_, d) => { const p = d.split('/'); for (let i = 1; i < p.length; i++) names.add(p.slice(0, i).join('/')); });
      const under = (x, top) => x !== top && x.startsWith(top + '/');
      const flip = (d, alt) => {
        const inner = [...names].filter((x) => under(x, d)), k = (x) => j.module + '\0' + x;
        if (shutDirs.has(k(d))) { shutDirs.delete(k(d)); if (alt) inner.forEach((x) => shutDirs.delete(k(x))); } else { shutDirs.add(k(d)); inner.forEach((x) => shutDirs.add(k(x))); }   // closing a folder closes the ones inside it
        paint();
      };
      const order = ['', ...[...names].sort(pathCmp)];
      order.forEach((d) => {
        const files = d === '' ? (dirs.get('') || []) : all.filter((f) => f === d || f.startsWith(d + '/')), own = dirs.get(d) || [];
        if (d && [...shutDirs].some((x) => x.startsWith(j.module + '\0') && under(d, x.slice(j.module.length + 1)))) return;       // inside a closed folder
        if (!files.length) return;
        const kept = files.filter((f) => !skipped.has(key(f))).length, closed = shutDirs.has(j.module + '\0' + d), depth = d ? d.split('/').length - 1 : 0;
        const cb = h('input', { type: 'checkbox', 'aria-label': 'Folder ' + (d || '(module root)'), checked: kept === files.length, onchange: (e) => change(files, e.target.checked) });
        cb.indeterminate = kept > 0 && kept < files.length;
        if (d) box.append(h('div', { class: 'row', style: { 'margin-left': depth * 1.2 + 'em' } }, h('button', { class: 'ghost', 'aria-expanded': String(!closed), text: closed ? '▸' : '▾', title: closed ? 'Show what is inside (Alt: every level)' : 'Hide everything inside', onclick: (e) => flip(d, e.altKey) }), cb, h('b', { text: d.split('/').pop(), title: d }), h('span', { class: 'muted', text: `${kept}/${files.length}` })));
        if (!closed) own.forEach((f) => box.append(h('label', { class: 'row', style: { 'margin-left': (d ? depth + 1 : 0) * 1.2 + 'em' } },
          h('input', { type: 'checkbox', checked: !skipped.has(key(f)), onchange: (e) => change([f], e.target.checked) }), f.slice(f.lastIndexOf('/') + 1))));
      });
      return box;
    }
    async function replan() {
      const was = new Set(ticked);
      try {
        plan = await aiApi('/api/ai/plan', body());
        if (plan.error) throw new Error(plan.error);
        ticked = new Set(plan.jobs.filter((j) => was.has(j.module + '\0' + j.key) && j.files.length).map((j) => j.module + '\0' + j.key));
        paint();
      } catch (e) { toast(e.message, { err: true }); }
    }
    function upd() {
      const j = sumPlan(), t = j.reduce((a, x) => a + x.in_tokens + x.out_tokens, 0);
      total.textContent = `${j.length} of ${plan.jobs.length} ticked · about ${t.toLocaleString()} tokens`;
      create.textContent = j.length ? `Create ${plural(j.length, make === 'guide' ? 'study guide' : 'note')}` : 'Create';
      create.disabled = !j.length || !!plan.limit_problem;
    }
    pv.onclick = () => busy(pv, async () => {
      pv.textContent = 'Planning…';
      try {
        skipped.clear(); openJobs.clear(); shutDirs.clear();
        plan = await aiApi('/api/ai/plan', body());
        if (plan.error) { const msg = plan.error; plan = null; throw new Error(msg); }
        ticked = new Set(plan.jobs.filter((j) => j.status === 'new').map((j) => j.module + '\0' + j.key));
        if (!ticked.size) plan.jobs.forEach((j) => ticked.add(j.module + '\0' + j.key));
        paint();
      } finally { pv.textContent = 'Show plan'; }
    });
    create.onclick = () => busy(create, async () => {
      const j = sumPlan(); if (!j.length) return;
      const t = j.reduce((a, x) => a + x.in_tokens, 0), to = j.reduce((a, x) => a + x.out_tokens, 0);
      const used = plan.targets.filter((x) => j.some((y) => y.task === x.task));
      const ok = await dialog({ title: create.textContent + '?', text: `Model: ${(used.length ? used : plan.targets).map(modelLine).join('; ')}. About ${t.toLocaleString()} tokens in, up to ${to.toLocaleString()} out.${usageHigh(plan.usage)}`, ok: 'Create' });
      if (!ok) return;
      await Jobs.run(aiApi('/api/ai/notes', body(j.map((x) => ({ module: x.module, key: x.key })))));
      plan = null; clear(out); await refresh(false); if (panels.library) lib = await api('/api/ai/generated', {}); draw();
    });
    const notesOpts = make !== 'notes' ? null : [
      h('div', { class: 'row' }, h('span', { class: 'muted', text: 'Group' }), seg(GROUPS, group, (v) => { group = v; store('group', v); reset(); }, 'Group'), h('label', { class: 'row muted' }, 'Week', wk)),
      h('div', { class: 'row ai-kinds' }, h('span', { class: 'muted', text: 'Kinds' }), KINDS.map(([k, l]) => h('label', { class: 'row' },
        h('input', { type: 'checkbox', checked: on[k], onchange: (e) => { on[k] = e.target.checked; store('kinds', on); plan = null; clear(out); create.disabled = true; } }), l))),
      picked.length && cur !== ALL ? h('div', { class: 'row' }, h('span', { class: 'badge', text: plural(picked.length, 'selected item') + ' from Files' }), h('span', { class: 'muted', text: picked.map((r) => r.split('/').pop()).slice(0, 4).join(', ') + (picked.length > 4 ? '…' : '') }),
        h('button', { class: 'ghost', text: 'Use the whole module', onclick: () => { picked = []; reset(); } })) : null];
    paint();
    return h('section', { class: 'card ai-card', id: 'ai-create' }, h('h2', { text: 'Create' }),
      h('p', { class: 'muted', text: 'Writes under <module>/Notes. Nothing is sent until you press Create.', title: 'Files you edited are never overwritten. Deadlines found in coursework notes go to your Calendar.' }),
      h('div', { class: 'row' }, h('span', { class: 'muted', text: 'Module' }), m, h('span', { class: 'muted', text: 'Make' }), seg([['notes', 'Notes'], ['guide', 'Study guide']], make, (v) => { make = v; store('make', v); reset(); }, 'Make')),
      notesOpts, h('div', { class: 'row' }, pv, create), out);
  }

  /* ---------- Chat with the course: the Study assistant's backend (/api/ai/chat), with the files you tick as context */
  function chatCard() {
    const mods = S.modules, cur = mods.includes(mod) ? mod : mods[0];
    const m = sel(mods, cur, (v) => { mod = v; store('mod', v); loadFiles(v); }, 'Module of the files');
    const list = h('div', { class: 'ai-files' }), chips = h('div', { class: 'row' }), thread = h('div', { class: 'ai-thread', 'aria-live': 'polite' });
    const subject = h('input', { type: 'search', placeholder: 'Filter by subject or note name', 'aria-label': 'Filter files', oninput: () => showList() });
    const q = h('textarea', { rows: 3, 'aria-label': 'Message', value: talk.draft, placeholder: 'Ask about the ticked files…  (Ctrl/Cmd Enter sends)', oninput: (e) => { talk.draft = e.target.value; } });
    const send = h('button', { class: 'primary', text: 'Send' });
    let files = [];
    const matching = () => files.filter((f) => f.toLowerCase().includes(subject.value.trim().toLowerCase()));
    function paintChips() {
      clear(chips);
      [...talk.chosen].forEach((r) => chips.append(h('span', { class: 'badge ai-chip' }, r.split('/').pop(), h('button', { class: 'ghost', 'aria-label': 'Remove ' + r, text: '×', onclick: () => { talk.chosen.delete(r); paintChips(); showList(); } }))));
      if (talk.chosen.size) chips.append(h('button', { class: 'ghost', text: 'Clear', onclick: () => { talk.chosen.clear(); paintChips(); showList(); } }));
    }
    function showList() {
      clear(list);
      const ms = matching();
      ms.forEach((f) => list.append(h('label', { class: 'row' }, h('input', { type: 'checkbox', checked: talk.chosen.has(f), onchange: (e) => { e.target.checked ? talk.chosen.add(f) : talk.chosen.delete(f); paintChips(); } }), f.slice(f.indexOf('/') + 1))));
      if (!ms.length) list.append(h('span', { class: 'muted', text: files.length ? 'No file matches.' : 'No PDFs, text files or notes in this module.' }));
    }
    async function loadFiles(v) { try { files = (await api('/api/ai/files', { module: v })).files; } catch (e) { files = []; } showList(); }
    function paintThread() {
      clear(thread);
      talk.msgs.forEach((x) => thread.append(h('div', { class: 'ai-msg ' + (x.role === 'user' ? 'me' : x.err ? 'ai err' : 'ai') }, h('pre', { class: 'ai-answer', text: x.text }),
        x.info ? h('div', { class: 'row' }, h('span', { class: 'muted', text: x.info }), h('button', { class: 'ghost', text: 'Copy', onclick: async () => { try { await navigator.clipboard.writeText(x.text); toast('Copied', { ms: 1200 }); } catch (e) {} } })) : null)));
      if (talk.busy) thread.append(h('div', { class: 'ai-msg ai wait' }, h('span', { class: 'spin' }), ' Thinking…'));
      thread.scrollTop = thread.scrollHeight;
    }
    const ask = async () => {
      const text = q.value.trim(); if (!text || talk.busy) return;
      if (!(await confirmSend(await api('/api/ai/target', { task: 'chat' })))) return;
      const messages = [...talk.msgs.filter((x) => !x.err).map((x) => ({ role: x.role, text: x.text })), { role: 'user', text }];
      talk.msgs.push({ role: 'user', text }); talk.busy = true; talk.draft = q.value = ''; send.disabled = true; paintThread();
      try {
        const r = await aiApi('/api/ai/chat', { messages, rels: [...talk.chosen] });
        talk.msgs.push({ role: 'assistant', text: r.answer, info: `${r.in_tokens.toLocaleString()} in · ${r.out_tokens.toLocaleString()} out${r.context.length ? ' · from ' + r.context.map((c) => c.replace(/^FILE: /, '')).join(', ') : ' · no files'}${r.truncated ? ' · text was cut to fit' : ''}` });
        setUsage(r.usage);
      } catch (e) { talk.msgs.push({ role: 'assistant', err: true, text: e.message }); }
      talk.busy = false; send.disabled = false; paintThread();
    };
    send.onclick = ask;
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ask(); } });
    loadFiles(m.value); paintChips(); paintThread();
    return h('section', { class: 'card ai-card', id: 'ai-chat' }, h('h2', { text: 'Chat with the course' }),
      h('p', { class: 'muted', text: 'Tick files as context, then ask. Their text is sent with each message.' }),
      h('div', { class: 'row' }, m, subject, h('button', { class: 'ghost', text: 'Tick all shown', onclick: () => { matching().forEach((f) => talk.chosen.add(f)); paintChips(); showList(); } })), list, chips, thread, q,
      h('div', { class: 'row' }, send, h('button', { class: 'ghost', text: 'New chat', title: 'Forget this conversation', onclick: () => { talk.msgs = []; paintThread(); } })));
  }

  /* ---------- Library: what was generated, and the deadlines saved from notes */
  function libraryCard() {
    if (!panels.library || !lib) return null;
    const rowOf = (f) => h('div', { class: 'row ai-gen' }, h('a', { href: '#/edit/' + encodeURIComponent(f.rel), class: 'grow', text: f.name, title: f.rel }),
      f.state ? h('span', { class: 'badge' + (f.state === 'yours' ? ' b-update' : ''), text: f.state === 'yours' ? 'Edited / yours' : 'Generated' }) : null,
      h('span', { class: 'muted num', text: kb(f.size) + ' · ' + when(f.mtime) }),
      h('button', { class: 'ghost', text: 'Chat about it', onclick: () => { talk.chosen.add(f.rel); mod = modOf(f.rel); draw(); $('#ai-chat').scrollIntoView({ behavior: 'smooth' }); } }));
    const part = (title, files) => (files.length ? h('div', {}, h('div', { class: 'muted', text: title }), files.map(rowOf)) : null);
    const body = lib.modules.length ? lib.modules.map((g) => h('div', { class: 'ai-genmod' }, h('h3', { text: g.module }),
      part('Notes', g.notes.filter((f) => f.kind !== 'guide')), part('Study guides', g.notes.filter((f) => f.kind === 'guide'))))
      : [h('p', { class: 'muted', text: 'Nothing generated yet.' })];
    const dl = lib.deadlines.length ? h('div', { class: 'ai-genmod' }, h('h3', { text: 'Deadlines from notes (in your Calendar)' }), lib.deadlines.map((d) => h('div', { class: 'row ai-gen' },
      h('span', { class: 'grow', text: d.title }), h('span', { class: 'muted', text: (d.course ? d.course + ' · ' : '') + new Date(d.start_ts * 1000).toLocaleString(undefined, d.all_day ? { dateStyle: 'medium' } : { dateStyle: 'medium', timeStyle: 'short' }) }),
      h('button', { class: 'ghost', text: 'Remove', onclick: safe(async () => { lib.deadlines = (await api('/api/ai/deadline/remove', { id: d.id })).deadlines; draw(); }) })))) : null;
    return h('section', { class: 'card ai-card', id: 'ai-lib' }, h('h2', { text: 'Library' }), body, dl);
  }

  /* ---------- Prompts */
  function promptsCard() {
    if (!panels.prompts) return null;
    let cur = store('prompt') && S.prompts.some((p) => p.name === store('prompt')) ? store('prompt') : S.prompts[0].name;
    const ta = h('textarea', { rows: 14, spellcheck: 'false', 'aria-label': 'Prompt text' }), info = h('span', { class: 'muted' });
    const load = () => { const p = S.prompts.find((x) => x.name === cur); ta.value = p.text; info.textContent = p.custom ? 'Your version is in use.' : 'Built-in version.'; };
    const pick = sel(S.prompts.map((p) => [p.name, (KIND_LABEL[p.name] || p.name.replace(/_/g, ' '))]), cur, (v) => { cur = v; store('prompt', v); load(); }, 'Prompt');
    const save = h('button', { class: 'primary', text: 'Save' }), reset = h('button', { text: 'Reset to built-in' });
    save.onclick = () => busy(save, async () => { S.prompts = (await aiApi('/api/ai/prompt', { name: cur, text: ta.value })).prompts; load(); toast('Saved. Notes made with the old prompt count as out of date.'); });
    reset.onclick = () => busy(reset, async () => { S.prompts = (await aiApi('/api/ai/prompt', { name: cur, text: null })).prompts; load(); toast('Back to the built-in prompt'); });
    load();
    return h('section', { class: 'card ai-card', id: 'ai-prompts' }, h('h2', { text: 'Prompts' }),
      h('p', { class: 'muted', text: 'The instructions sent with each file. Keep {{text}}: the course text goes there.', title: 'Also: {{module}} {{title}} {{week}} {{part}}. The coursework prompt must keep its deadlines line.' }),
      h('div', { class: 'row' }, pick, info), ta, h('div', { class: 'row' }, save, reset));
  }

  function draw() {
    if (!host) return;
    const keep = $('#main').scrollTop;
    clear(host).append(head(), S.error ? setupHelp() : null, createCard(), chatCard(), libraryCard(), promptsCard());
    $('#main').scrollTop = keep;
  }
  async function refresh(redraw = true) { S = await aiApi('/api/ai/state', {}); if (!S.modules.length) S.modules = ['']; if (!group) group = GROUPS.some(([g]) => g === store('group')) ? store('group') : S.group; if (redraw) draw(); }

  /**
   * Fill the Create panel from a link: `#/ai/create?module=…&make=notes|guide&group=week|term&week=3&kinds=lectures,summaries`
   * (the Study quick menu's "Open in AI tab"). Unknown values are ignored; nothing is planned or sent.
   * @param {string} q the part after `create?`
   */
  function prefill(q) {
    const p = new URLSearchParams(q);
    if (p.get('module')) mod = p.get('module');
    if (['notes', 'guide'].includes(p.get('make'))) make = p.get('make');
    if (GROUPS.some(([g]) => g === p.get('group'))) group = p.get('group');
    week = /^\d{1,2}$/.test(p.get('week') || '') ? p.get('week') : '';
    if (p.get('kinds')) { const ks = p.get('kinds').split(','); KINDS.forEach(([k]) => { on[k] = ks.includes(k); }); }
  }

  App.pages.ai = {
    title: 'AI',
    async mount(el_, arg) {
      host = h('div', { class: 'page' }, h('div', { class: 'empty', text: 'Loading…' })); el_.append(host);
      picked = []; plan = null; mod = store('mod') || mod;
      if (arg && arg.startsWith('notes:')) { picked = arg.slice(6).split('|').filter(Boolean); if (picked.length) { make = 'notes'; mod = modOf(picked[0]); picked.forEach((r) => { if (/\.(pdf|txt|md)$/i.test(r)) talk.chosen.add(r); }); } }
      try { await refresh(false); if (picked.length) mod = modOf(picked[0]); if (arg && arg.startsWith('create?')) prefill(arg.slice(7)); if (panels.library) lib = await api('/api/ai/generated', {}); draw(); } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'The AI page could not load' }), e.message)); }
      return () => { host = null; };
    },
  };
})();
