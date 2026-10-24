'use strict';
/* Smart allocation: every Moodle file that wants a decision (new, updated, ignored, deleted here), grouped by the folder it is
   assigned to. Select rows (click, Ctrl/Cmd, Shift, drag a box) and act on all of them: download, ignore, change folder, ... */
(function () {
  const F = App.Files;
  const FILTERS = [['todo', 'To do'], ['new', 'New'], ['update', 'Updated'], ['ignored', 'Ignored'], ['missing', 'Deleted here'], ['all', 'Everything']];
  let data = null, filter = 'todo', query = '', sel = null, host = null, setSub = () => {}, closed = new Set(), byKey = new Map();
  try { closed = new Set(JSON.parse(sessionStorage.getItem('aclosed') || '[]')); } catch (e) {}
  const saveClosed = () => { try { sessionStorage.setItem('aclosed', JSON.stringify([...closed])); } catch (e) {} };

  const inFilter = (f) => (filter === 'all' ? true : filter === 'todo' ? f.status !== 'downloaded' : f.status === filter)
    && (!query || (f.name + ' ' + f.activity + ' ' + f.sec + ' ' + f.folder).toLowerCase().includes(query));
  const tally = (fs) => fs.reduce((c, f) => { c[f.status] = (c[f.status] || 0) + 1; return c; }, {});
  const weekLabel = (f) => (f.sec && !/^week\s*\d+$/i.test(f.sec) ? f.sec : f.section ? 'Week ' + f.section : 'General');
  const shortFolder = (course, folder) => {
    const base = course.folder || '';
    if (folder === base) return { head: '', tail: 'Module folder' };
    const rel = base && folder.startsWith(base + '/') ? folder.slice(base.length + 1) : folder;
    const i = rel.lastIndexOf('/'); return { head: i < 0 ? '' : rel.slice(0, i + 1), tail: i < 0 ? rel : rel.slice(i + 1) };
  };

  async function load() {
    try { data = await api('/api/alloc'); } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'Could not read your files' }), e.message)); return; }
    byKey = new Map(data.files.map((f) => [f.key, f]));
    draw();
  }

  const statusBadge = (f) => h('span', { class: 'badge b-' + f.status, text: F.LABEL[f.status] || f.status });
  function rowActions(f) {
    const b = (label, fn, title) => h('button', { text: label, title, onclick: (e) => { e.stopPropagation(); fn(); } });
    if (f.status === 'new' || f.status === 'update') return [b('Download', () => F.act.download([f.key])), f.status === 'new' ? b('Ignore', () => F.act.ignore([f.key])) : null];
    if (f.status === 'ignored') return [b('Un-ignore', () => F.act.unignore([f.key]))];
    if (f.status === 'missing') return [b('Download again', () => F.redownload([f])), b('Forget', () => F.act.forget([f.key]), 'Stop offering this file')];
    return [];
  }
  function row(f) {
    return h('div', { class: 'frow arow st-' + f.status, dataset: { sel: f.key }, title: [f.activity, f.rel].filter(Boolean).join('\n') },
      h('span', { class: 'fic' }, icon('file')), h('span', { class: 'name', text: f.name }),
      f.chosen ? h('span', { class: 'tag', text: 'your folder', title: 'You chose where this goes' }) : null,
      h('span', { class: 'fmeta wkl', text: weekLabel(f) }), statusBadge(f), h('span', { class: 'fmeta', text: f.size ? fmtSize(f.size) : '' }),
      h('span', { class: 'acts' }, rowActions(f)));
  }

  function draw() {
    if (!host) return;
    const keep = sel ? sel.ids() : [], y = $('#main').scrollTop;
    if (sel) sel.destroy();
    const all = data.files, c = tally(all), vis = all.filter(inFilter);
    setSub(`${c.new || 0} new · ${c.update || 0} updated · ${c.ignored || 0} ignored · ${c.missing || 0} deleted here`);
    const count = (k) => (k === 'todo' ? (c.new || 0) + (c.update || 0) + (c.ignored || 0) + (c.missing || 0) : k === 'all' ? all.length : c[k] || 0);
    const bar = h('div', { class: 'bar' }, h('div', { class: 'search' }, icon('search'), h('input', { type: 'search', 'data-search': '', placeholder: 'Filter by name or week  /', value: query, 'aria-label': 'Filter files', oninput: (e) => { query = e.target.value.trim().toLowerCase(); draw(); const s = $('[data-search]'); s.focus(); s.setSelectionRange(s.value.length, s.value.length); } })),
      FILTERS.map(([k, l]) => h('button', { class: 'chip', 'aria-pressed': String(filter === k), text: l + (count(k) ? ' ' + count(k) : ''), onclick: () => { filter = k; draw(); } })),
      h('span', { class: 'sp' }),
      h('button', { text: `Ignore all new${c.new ? ' (' + c.new + ')' : ''}`, disabled: !c.new, onclick: () => F.act.ignore(all.filter((f) => f.status === 'new').map((f) => f.key)) }),
      h('button', { class: 'primary', text: `Download all new${c.new ? ' (' + c.new + ')' : ''}`, disabled: !c.new, onclick: () => F.act.download(all.filter((f) => f.status === 'new').map((f) => f.key)) }));
    const root = h('div', { class: 'alloc', role: 'listbox', 'aria-multiselectable': 'true', 'aria-label': 'Moodle files by folder' });
    for (const course of data.courses) {
      const mine = vis.filter((f) => f.course === course.id);
      if (!mine.length) continue;
      const folders = new Map();
      mine.forEach((f) => { if (!folders.has(f.folder)) folders.set(f.folder, []); folders.get(f.folder).push(f); });
      const sec = h('section', { class: 'acourse' }, h('h2', {}, course.name || course.short, h('span', { class: 'n', text: mine.length + ' file' + (mine.length > 1 ? 's' : '') })));
      [...folders.keys()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).forEach((folder) => {
        const fs = folders.get(folder).sort((a, b) => (a.section - b.section) || a.name.localeCompare(b.name, undefined, { numeric: true }));
        const gid = course.id + '|' + folder, shut = closed.has(gid), t = tally(fs), p = shortFolder(course, folder);
        const todo = fs.filter((f) => f.status === 'new' || f.status === 'update');
        const g = h('div', { class: 'agroup' + (shut ? ' shut' : ''), role: 'group', 'data-gscope': '' });
        const head = h('div', { class: 'ghead', 'data-group': '', dataset: { sel: 'g:' + gid }, title: 'Click to select every file in this folder' },
          h('span', { class: 'chev', title: 'Show or hide the files', onclick: (e) => { e.stopPropagation(); const now = !g.classList.contains('shut'); g.classList.toggle('shut', now); now ? closed.add(gid) : closed.delete(gid); saveClosed(); } }, icon('chev')),
          h('span', { class: 'fic' }, icon('folder')), h('span', { class: 'gpath' }, p.head ? h('span', { class: 'faint', text: p.head }) : null, h('b', { text: p.tail })),
          fs[0].folder_exists ? null : h('span', { class: 'tag', text: 'will be created' }),
          h('span', { class: 'counts' }, ['new', 'update', 'ignored', 'missing'].filter((k) => t[k]).map((k) => h('span', { class: 'b-' + k, text: t[k] + ' ' + (F.LABEL[k] || k).toLowerCase() }))),
          h('span', { class: 'sp' }),
          todo.length ? h('button', { text: `Download ${todo.length}`, onclick: () => F.act.download(todo.map((f) => f.key)) }) : null,
          fs.some((f) => f.status === 'new') ? h('button', { text: 'Change folder…', title: 'Choose another folder for the new files here', onclick: () => chooseFolder(fs.filter((f) => f.status === 'new').map((f) => f.key), false) }) : null);
        g.append(head);
        fs.forEach((f) => g.append(row(f)));
        sec.append(g);
      });
      root.append(sec);
    }
    if (!vis.length) root.append(h('div', { class: 'empty' }, h('b', { text: query ? 'Nothing matches' : filter === 'todo' ? 'Everything is where it should be' : 'Nothing here' }),
      query ? 'Clear the filter to see everything.' : filter === 'todo' ? 'No new, updated, ignored or deleted files. Run “Check Moodle” to look for more.' : 'Try another filter.',
      filter === 'todo' && !query && all.length ? [' ', h('button', { text: 'Show everything', onclick: () => { filter = 'all'; draw(); } })] : null));
    clear(host).append(bar, root);
    sel = Selectable(root, { onChange: onSel, onOpen: (id) => { const f = byKey.get(id); if (f && (f.status === 'downloaded' || f.status === 'update')) go('files', 'organise'); }, onContext: (e, ids) => F.menu(e, build(ids)), keys: F.keymap(build) });
    sel.set(keep);
    $('#main').scrollTop = y;
  }

  const filesOf = (ids) => ids.map((i) => byKey.get(i)).filter(Boolean);
  const keysOf = (fs) => fs.map((f) => f.key);

  async function chooseFolder(keys, download) {
    const fs = filesOf(keys), start = fs.length ? fs[0].folder : '';
    const dir = await F.pickFolder({ title: download ? `Download ${fs.length} file${fs.length > 1 ? 's' : ''} to…` : `Where should ${fs.length} file${fs.length > 1 ? 's' : ''} go?`,
      text: download ? 'They are downloaded into this folder. The choice is remembered for these files.' : 'The files stay on Moodle until you download them; they will be saved here.', ok: download ? 'Download here' : 'Use this folder', start });
    if (dir === null) return;
    download ? F.act.place(keys, dir) : F.act.retarget(keys, dir);
  }
  const lookAt = (fs, quiet) => { if (fs.length === 1) F.look.show({ kind: 'moodle', f: fs[0] }, { step: (d) => sel.step(d), place: (pl) => chooseFolder(keysOf(pl), true) }); else if (!quiet) toast('Select one file to preview it', { ms: 1800 }); };
  /* every list of actions (bar, right-click menu, keys) comes from here */
  function build(ids) {
    const fs = filesOf(ids);
    return [...F.moodleActions(fs, { place: (pl) => chooseFolder(keysOf(pl), true), change: (nw) => chooseFolder(keysOf(nw), false) }),
      { label: 'Preview', key: 'p', sep: true, on: fs.length === 1, title: 'Quick look without saving (p)', fn: () => lookAt(fs) }];
  }
  let lookTimer = null;
  function onSel(ids) {
    if (!ids.length) return SelBar.hide();
    F.bar(`${ids.length} selected`, build(ids), () => sel.clear());
    if (F.look.isOpen()) { clearTimeout(lookTimer); lookTimer = setTimeout(() => lookAt(filesOf(ids), true), 120); }
  }

  F.views.allocate = {
    async mount(el, o) {
      host = h('div', { class: 'allochost' }); el.append(host); setSub = o.sub;
      const l = (what) => { if (what === 'job' && host && host.isConnected) load(); };
      App.listeners.push(l); F.reload = load;
      await load();
      return () => { clearTimeout(lookTimer); F.look.close(); App.listeners = App.listeners.filter((x) => x !== l); if (sel) sel.destroy(); host = null; sel = null; };
    },
  };
})();
