'use strict';
/* Chat: Claude Code working in a folder you pick, like the chat panel of an editor. Projects (module folders, the study folder,
   folders you add) on the left with their conversations; the conversation in the middle; what Claude may do and the model above
   the message box. Every call says it comes from this page (X-Page: chat); the server refuses /api/chat/* from anywhere else. */
(function () {
  const chatApi = (op, body) => api('/api/chat/' + op, body || {}, { 'X-Page': 'chat' });
  const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem('chat.' + k); localStorage.setItem('chat.' + k, v); } catch (e) {} return null; };
  let host = null, st = null, project = '', chats = [], conv = null, timer = null, shownV = -1, draft = '';

  const projOf = (id) => (st && st.projects.find((p) => p.id === id)) || null;
  const ago = (t) => { const s = Date.now() / 1000 - t; return s < 60 ? 'just now' : s < 3600 ? Math.floor(s / 60) + ' min ago' : s < 86400 ? Math.floor(s / 3600) + ' h ago' : fmtDate(t); };

  async function loadList() { chats = (await chatApi('list', { project })).chats; drawSide(); }
  async function openChat(id) {
    stopPoll(); conv = id ? await chatApi('get', { id }) : null; shownV = -1; ls('last.' + project, id || '');
    drawSide(); drawMain(); if (conv && conv.running) poll();
  }
  async function newChat() {
    const last = conv || {};
    conv = await chatApi('create', { project, model: last.model || ls('model') || undefined, mode: last.mode || ls('mode') || 'ask' });
    shownV = -1; ls('last.' + project, conv.id); await loadList(); drawMain();
    const box = $('.ch-ask textarea', host); if (box) box.focus();
  }
  function stopPoll() { clearTimeout(timer); timer = null; }
  function poll() {
    stopPoll();
    timer = setTimeout(async () => {
      if (!host || !conv) return;
      try { const c = await chatApi('get', { id: conv.id }); if (!conv || c.id !== conv.id) return; conv = c; if (c.v !== shownV) drawThread(); if (!c.running) { drawAsk(); loadList(); return; } } catch (e) { /* keep trying */ }
      poll();
    }, 350);
  }

  const send = safe(async () => {
    const box = $('.ch-ask textarea', host); const text = box ? box.value.trim() : ''; if (!text) return;
    if (!conv) await newChat();
    conv = await chatApi('send', { id: conv.id, text }); draft = '';
    const b = $('.ch-ask textarea', host); if (b) b.value = '';
    drawThread(true); drawAsk(); loadList(); poll();
  });
  const stop = safe(async () => { if (conv) { await chatApi('stop', { id: conv.id }); } });

  /* ---------- left: projects and their conversations */
  async function addFolder() {
    const r = await dialog({ title: 'Add a project folder', text: 'Any folder on this computer, for example one of your coding projects. Claude works inside it: it can read the files there, and edit them or run commands only if you allow that in a conversation.', ok: 'Add', fields: [{ name: 'path', label: 'Full path of the folder', value: '' }] });
    if (!r || !r.path.trim()) return;
    try {
      const res = await chatApi('folder/add', { path: r.path }); st.projects = res.projects; project = res.added; ls('project', project);
      if (host && !$('.ch-side', host)) clear(host).append(h('aside', { class: 'ch-side', 'aria-label': 'Projects and conversations' }), h('section', { class: 'ch-main' }));
      await loadList(); openChat(null);
    }
    catch (e) { toast(e.message, { err: true }); }
  }
  async function removeFolder(p) {
    if (!(await dialog({ title: 'Remove this project?', text: 'The folder and its files stay where they are; it is only taken off this list. Its conversations stay saved.', path: p.path, ok: 'Remove' }))) return;
    st.projects = (await chatApi('folder/remove', { path: p.path })).projects;
    if (project === p.id) { project = (st.projects[0] || {}).id || ''; ls('project', project); }
    await loadList(); openChat(null);
  }
  async function delChat(c) {
    if (!(await dialog({ title: 'Delete this conversation?', text: c.title || 'New chat', ok: 'Delete', danger: true }))) return;
    await chatApi('delete', { id: c.id }); if (conv && conv.id === c.id) conv = null; await loadList(); drawMain();
  }
  function drawSide() {
    const side = host && $('.ch-side', host); if (!side) return;
    const groups = [['module', 'Modules'], ['root', 'Study folder'], ['folder', 'Your folders']];
    const pick = h('select', { 'aria-label': 'Project', onchange: async (e) => { project = e.target.value; ls('project', project); await loadList(); const last = ls('last.' + project); openChat(chats.some((c) => c.id === last) ? last : null); } },
      groups.map(([k, label]) => { const ps = st.projects.filter((p) => p.kind === k); return ps.length ? h('optgroup', { label }, ps.map((p) => h('option', { value: p.id, text: p.name + (p.missing ? ' (missing)' : ''), selected: p.id === project }))) : null; }));
    const p = projOf(project);
    const list = h('div', { class: 'ch-list', role: 'list', 'aria-label': 'Conversations' },
      chats.length ? chats.map((c) => h('div', { class: 'ch-item' + (conv && conv.id === c.id ? ' on' : ''), role: 'listitem', tabindex: '0', title: c.title,
        onclick: () => openChat(c.id), onkeydown: (e) => { if (e.key === 'Enter') openChat(c.id); }, oncontextmenu: (e) => { e.preventDefault(); ctxMenu(e.clientX, e.clientY, [{ label: 'Delete conversation', danger: true, fn: () => delChat(c) }]); } },
        h('span', { class: 'name', text: c.title || 'New chat' }), c.running ? h('span', { class: 'spin', title: 'Answering' }) : h('span', { class: 'faint when', text: ago(c.updated) }),
        h('button', { class: 'ghost x', 'aria-label': 'Delete ' + (c.title || 'conversation'), title: 'Delete', text: '×', onclick: (e) => { e.stopPropagation(); delChat(c); } })))
        : h('div', { class: 'muted ch-none', text: 'No conversations in this project yet.' }));
    clear(side).append(
      h('div', { class: 'ch-pick' }, pick,
        h('div', { class: 'row' }, h('button', { class: 'ghost', text: '+ Add folder', title: 'Add a folder from anywhere on this computer as a project', onclick: addFolder }), h('span', { class: 'sp' }),
          p && p.kind === 'folder' ? h('button', { class: 'ghost', text: 'Remove', title: 'Take this folder off the list (nothing is deleted)', onclick: () => removeFolder(p) }) : null),
        h('div', { class: 'ch-path', title: p ? p.path : '', text: p ? (p.path.length > 44 ? '…' + p.path.slice(-43) : p.path) : '' })),
      h('button', { class: 'primary', id: 'newchat', text: 'New chat', disabled: !project || !st.cli, onclick: safe(newChat) }), list);
  }

  /* ---------- middle: the conversation */
  function part(p) {
    if (p.type === 'text') { const d = h('div', { class: 'md' }); d.append(App.markdown ? App.markdown(p.text || '') : h('p', { text: p.text })); return d; }
    const det = h('details', { class: 'ch-tool' + (p.error ? ' bad' : '') }, h('summary', {}, h('span', { class: 'tn', text: p.name }), h('span', { class: 'ts', text: p.summary.replace(new RegExp('^' + p.name + ':? ?'), '') }), p.result === undefined ? h('span', { class: 'spin' }) : null));
    if (p.result !== undefined) det.append(h('pre', { text: p.result || '(no output)' }));
    return det;
  }
  function message(m, last) {
    if (m.role === 'user') return h('div', { class: 'msg me', text: m.text });
    const box = h('div', { class: 'msg ai ch-ai' + (m.error && !m.stopped ? ' err' : '') });
    if (m.note) box.append(h('div', { class: 'faint ch-note', text: m.note }));
    (m.parts || []).forEach((p) => box.append(part(p)));
    if (last && conv.running && !(m.parts || []).length) box.append(h('div', { class: 'msg wait' }, h('span', { class: 'spin' }), 'Claude is working in ' + ((projOf(conv.project) || {}).name || 'the folder') + '…'));
    if (m.error) box.append(h('div', { class: m.stopped ? 'faint' : 'ch-err', text: m.stopped ? 'Stopped.' : m.error }));
    else if (m.stopped) box.append(h('div', { class: 'faint', text: 'Stopped.' }));
    const bits = [m.model, m.tokens ? `${(m.tokens.in || 0).toLocaleString()} in / ${(m.tokens.out || 0).toLocaleString()} out` : '', typeof m.cost === 'number' && m.cost > 0 ? '$' + m.cost.toFixed(4) : ''].filter(Boolean);
    const txt = (m.parts || []).filter((p) => p.type === 'text').map((p) => p.text).join('\n\n');
    if (!(last && conv.running)) box.append(h('div', { class: 'macts' }, txt ? h('button', { class: 'ghost', text: 'Copy', onclick: () => { navigator.clipboard.writeText(txt).then(() => toast('Copied', { ms: 1200 })); } }) : null, h('span', { class: 'faint minfo', text: bits.join(' · ') })));
    return box;
  }
  function drawThread(force) {
    const th = host && $('.ch-thread', host); if (!th) return;
    const atEnd = force || th.scrollHeight - th.scrollTop - th.clientHeight < 60;
    const open = new Set($$('details[open]', th).map((d) => d.dataset.k));
    clear(th);
    if (!conv || !conv.messages.length) {
      const p = projOf(project);
      th.append(h('div', { class: 'ch-hello' }, h('b', { text: p ? 'Chat about ' + p.name : 'Pick a project' }),
        h('p', { class: 'muted', text: 'Claude runs in this folder with your Claude Code sign-in, like the chat in an editor: it can look through the files there to answer. Choose below whether it may also edit files or run commands. Each conversation is remembered, and the next message continues it.' }),
        !st.cli ? h('p', { class: 'ch-err', text: 'The `claude` command (Claude Code) was not found. Install it and sign in by running `claude` once in Terminal, or set llm.executable in config.json to its full path.' }) : null));
    } else conv.messages.forEach((m, i) => { const el = message(m, i === conv.messages.length - 1); $$('details', el).forEach((d, j) => { d.dataset.k = i + ':' + j; if (open.has(d.dataset.k)) d.open = true; }); th.append(el); });
    shownV = conv ? conv.v : -1;
    const t = $('.ch-bar h1', host); if (t) t.textContent = conv && conv.title ? conv.title : 'Chat';
    if (atEnd) th.scrollTop = th.scrollHeight;
  }
  function drawAsk() {
    const el = host && $('.ch-ask', host); if (!el) return;
    const running = !!(conv && conv.running), old = $('textarea', el), val = old ? old.value : draft;
    const box = h('textarea', { rows: 3, placeholder: st.cli ? 'Ask Claude about this project…  (Enter sends, Shift+Enter: new line)' : 'Claude Code is not available', 'aria-label': 'Message to Claude', disabled: !st.cli || !project });
    box.value = val; box.addEventListener('input', () => { draft = box.value; });
    box.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!running) send(); } });
    const cur = conv || { model: ls('model') || st.model, mode: ls('mode') || 'ask' };
    const set = safe(async (k, v) => { ls(k, v); if (conv) conv = await chatApi('update', { id: conv.id, [k]: v }); drawAsk(); });
    const models = [...new Set([...st.models, cur.model])];
    const modeSel = h('select', { 'aria-label': 'What Claude may do', disabled: running, onchange: (e) => set('mode', e.target.value), title: 'What Claude may do in the folder. Anything else is refused: a background run cannot ask you.' }, st.modes.map((m) => h('option', { value: m.id, text: m.label, selected: m.id === cur.mode })));
    const modelSel = h('select', { 'aria-label': 'Model', disabled: running, onchange: (e) => set('model', e.target.value) }, models.map((m) => h('option', { value: m, text: m, selected: m === cur.model })));
    const p = projOf(project);
    clear(el).append(
      h('div', { class: 'ch-opts' }, modeSel, modelSel, h('span', { class: 'sp' }), h('span', { class: 'faint ch-where', title: p ? p.path : '', text: p ? 'Runs `claude` in ' + p.name : '' })),
      cur.mode === 'run' ? h('div', { class: 'ch-warn', text: 'Commands run without asking first. Use this only in a folder you have a copy of (git).' }) : null,
      h('div', { class: 'row ch-send' }, box, running ? h('button', { class: 'danger', text: 'Stop', onclick: stop }) : h('button', { class: 'primary', text: 'Send', disabled: !st.cli || !project, onclick: send })));
    if (document.activeElement === document.body || (old && document.activeElement === old)) box.focus();
  }
  function drawMain() {
    const main = host && $('.ch-main', host); if (!main) return;
    const p = projOf(project);
    clear(main).append(
      h('div', { class: 'ch-bar' }, h('h1', { text: conv && conv.title ? conv.title : 'Chat' }), h('span', { class: 'faint', text: p ? p.name : '' }), h('span', { class: 'sp' }),
        conv ? h('button', { class: 'ghost', text: 'New chat', onclick: safe(newChat) }) : null),
      h('div', { class: 'ch-thread', 'aria-live': 'polite' }), h('div', { class: 'ch-ask' }));
    drawThread(true); drawAsk();
  }

  App.pages.chat = { title: 'Chat', async mount(el, arg) {
    host = h('div', { class: 'chat' }, h('aside', { class: 'ch-side', 'aria-label': 'Projects and conversations' }), h('section', { class: 'ch-main' }));
    el.append(host); el.style.overflow = 'hidden';
    st = await chatApi('state');
    const want = arg ? decodeURIComponent(arg) : null;
    project = (projOf(ls('project')) || st.projects[0] || {}).id || '';
    if (!st.projects.length) { clear(host).append(h('div', { class: 'empty', style: { margin: 'auto' } }, h('b', { text: 'No project yet' }), 'Set the study folder under Settings, or ', h('button', { text: 'add a folder', onclick: addFolder }))); return () => { host = null; }; }
    await loadList();
    const last = want || ls('last.' + project);
    await openChat(chats.some((c) => c.id === last) ? last : null);
    return () => { stopPoll(); host = null; el.style.overflow = ''; };
  } };
})();
