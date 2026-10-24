'use strict';
/* Files page shell: a toggle between "Smart allocation" (alloc.js) and "Organise" (organise.js), plus the helpers both use:
   folder picker, moves with Undo, new folder, rename, subdivide by week, PDF text for AI chats. */
(function () {
  const F = App.Files = { views: {}, reload: null };
  F.LABEL = { new: 'New', update: 'Update', downloaded: 'Downloaded', adopted: 'Matched', local: 'Local only', ignored: 'Ignored', missing: 'Deleted here', archived: 'Old version', generated: 'Generated' };
  F.parent = (rel) => (rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '');
  F.base = (rel) => rel.slice(rel.lastIndexOf('/') + 1);
  F.TEXT = new Set(['md', 'markdown', 'txt', 'py', 'tex', 'bib', 'c', 'h', 'cpp', 'hpp', 'java', 'js', 'ts', 'json', 'csv', 'sh', 'sql', 'html', 'css', 'yml', 'yaml', 'toml', 'ini', 'r', 'rs', 'go', 'm', 'hs', 'log']);
  F.RAW = new Set(['pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp']);
  F.ext = (name) => (name.includes('.') ? name.split('.').pop().toLowerCase() : '');
  F.inEditor = (name) => F.TEXT.has(F.ext(name)) || F.RAW.has(F.ext(name));
  F.changed = async () => { await refreshBadges(); if (F.reload) await F.reload(); };

  /* open a file: the editor for text, PDFs and images; the computer's own program for everything else */
  F.openFile = safe(async (rel) => {
    if (F.inEditor(F.base(rel))) return go('edit', rel);
    await api('/api/open', { rel }); toast('Opened with your default program', { ms: 1800 });
  });

  /* ---------- actions on Moodle files */
  F.act = {
    download(keys) { if (keys.length) return Jobs.run(api('/api/download', { keys }), `Downloading ${keys.length} file(s)…`); },
    ignore: safe(async (keys) => { if (!keys.length) return; await api('/api/ignore', { keys }); toast(`Ignored ${keys.length} file(s)`, { undo: () => F.act.unignore(keys) }); await F.changed(); }),
    unignore: (keys) => keys.length ? Jobs.run(api('/api/unignore', { keys }), 'Restoring…') : null,
    forget: safe(async (keys) => { if (!keys.length) return; await api('/api/close', { keys }); toast(`Forgot ${keys.length} file(s): they will not be offered again`); await F.changed(); }),
    place(keys, dir) { if (keys.length) return Jobs.run(api('/api/place', { keys, dir }), `Downloading ${keys.length} file(s) into ${dir ? F.base(dir) : 'the study folder'}…`); },
    retarget: safe(async (keys, dir) => { const r = await api('/api/retarget', { keys, dir }); toast(`${r.changed} file(s) will be saved in ${dir ? F.base(dir) : 'the study folder'}`); await F.changed(); }),
  };

  /* ---------- folder picker */
  F.pickFolder = ({ title = 'Choose a folder', text = '', ok = 'Choose', start = '', hide = [] }) => new Promise(async (resolve) => {
    const dirs = [];
    const loadDirs = async () => { const t = await api('/api/tree'); dirs.length = 0; (function w(n, d) { if (n.type !== 'dir') return; dirs.push({ rel: n.rel, name: n.name || 'Study folder', depth: d, virtual: !!n.virtual }); n.kids.forEach((k) => w(k, d + 1)); })(t.root, 0); };
    try { await loadDirs(); } catch (e) { toast(e.message, { err: true }); return resolve(null); }
    let cur = start, q = '';
    const off = (d) => hide.some((x) => d.rel === x || d.rel.startsWith(x + '/'));
    const list = h('ul', { class: 'pick', role: 'listbox', 'aria-label': 'Folders' }), where = h('div', { class: 'path' });
    const draw = () => {
      clear(list);
      dirs.filter((d) => !q || (d.rel + '/' + d.name).toLowerCase().includes(q)).forEach((d) => list.append(h('li', { role: 'option', 'aria-selected': String(d.rel === cur), 'aria-disabled': String(off(d)), class: off(d) ? 'off' : '', dataset: { dir: d.rel },
        onclick: () => { if (!off(d)) { cur = d.rel; draw(); } }, ondblclick: () => { if (!off(d)) { cur = d.rel; dlg.close('yes'); } }, style: { 'padding-left': 8 + (q ? 0 : d.depth * 16) + 'px' } },
        icon('folder'), h('span', { text: q ? d.rel || d.name : d.name }), d.virtual ? h('span', { class: 'faint', text: 'planned' }) : null)));
      where.textContent = 'Destination: ' + (cur || 'Study folder');
    };
    const search = h('input', { type: 'search', placeholder: 'Filter folders', 'aria-label': 'Filter folders', oninput: (e) => { q = e.target.value.trim().toLowerCase(); draw(); } });
    const mk = safe(async () => {
      const f = await dialog({ title: 'New folder', text: 'Inside ' + (cur || 'the study folder'), fields: [{ name: 'name', label: 'Name' }], ok: 'Create' });
      if (!f || !f.name.trim()) return;
      const r = await api('/api/fs/mkdir', { rel: (cur ? cur + '/' : '') + f.name.trim() });
      await loadDirs(); cur = r.rel; draw(); const on = $('li[aria-selected="true"]', list); if (on) on.scrollIntoView({ block: 'nearest' });
    });
    const dlg = h('dialog', { class: 'picker' }, h('h3', { text: title }), text ? h('p', { text }) : null, search, list, where,
      h('div', { class: 'btns' }, h('button', { text: 'New folder…', onclick: mk }), h('span', { class: 'sp' }), h('button', { text: 'Cancel', onclick: () => dlg.close('no') }), h('button', { class: 'primary', text: ok, onclick: () => dlg.close('yes') })));
    dlg.addEventListener('close', () => { const ans = dlg.returnValue === 'yes' ? cur : null; dlg.remove(); resolve(ans); });
    document.body.append(dlg); draw(); dlg.showModal(); search.focus();
  });

  /* ---------- moves, folders, rename, subdivide (all with Undo) */
  const undoer = (res, what) => async () => {
    try { await api('/api/fs/undo', { moved: res.moved, created: res.created }); toast(`Undone: ${what}`, { ms: 2200 }); } catch (e) { toast(e.message, { err: true }); }
    await F.changed();
  };
  F.moveTo = async (rels, dir) => {
    let res; try { res = await api('/api/fs/move', { rels, dir }); } catch (e) { toast(e.message, { err: true }); return null; }
    const bad = res.skipped.filter((s) => !s.noop), n = res.moved.length, where = dir ? F.base(dir) : 'the study folder';
    if (n) toast(`Moved ${n} item${n > 1 ? 's' : ''} to ${where}` + (bad.length ? `. ${bad.length} skipped: ${bad[0].reason}` : ''), { undo: undoer(res, 'move'), ms: 8000 });
    else if (bad.length) toast(bad[0].reason, { err: true }); else toast('Already in that folder', { ms: 1800 });
    await F.changed(); return res;
  };
  F.newFolder = safe(async (parent) => {
    const f = await dialog({ title: 'New folder', text: 'Inside ' + (parent || 'the study folder') + '. Use a/b to make two levels.', fields: [{ name: 'name', label: 'Name' }], ok: 'Create' });
    if (!f || !f.name.trim()) return null;
    const r = await api('/api/fs/mkdir', { rel: (parent ? parent + '/' : '') + f.name.trim() });
    toast(`Created ${F.base(r.rel)}`, { ms: 2000 }); await F.changed(); return r.rel;
  });
  F.rename = safe(async (rel) => {
    const f = await dialog({ title: 'Rename', text: rel, fields: [{ name: 'name', label: 'New name', value: F.base(rel) }], ok: 'Rename' });
    if (!f || !f.name.trim() || f.name.trim() === F.base(rel)) return null;
    const res = await api('/api/fs/rename', { rel, name: f.name.trim() });
    toast(`Renamed to ${f.name.trim()}`, { undo: undoer(res, 'rename') }); await F.changed(); return res.moved[0] ? res.moved[0].to : null;
  });
  F.subdivide = safe(async (rel) => {
    const plan = await api('/api/fs/subdivide', { rel, dry: true });
    if (!plan.count) return toast('Nothing to split: no file here names a week (“Week 3”, “Lecture 3”) and none comes from a Moodle week.', { err: true });
    const lines = plan.weeks.map((w) => `${w.dir}${w.exists ? '' : '  (new folder)'}: ${w.files.length} file${w.files.length > 1 ? 's' : ''}`);
    if (plan.left.length) lines.push('', `${plan.left.length} file${plan.left.length > 1 ? 's stay' : ' stays'} here: ${plan.left.slice(0, 3).map((f) => f.name).join(', ')}${plan.left.length > 3 ? ', …' : ''}`);
    if (plan.waiting) lines.push(`${plan.waiting} file${plan.waiting > 1 ? 's' : ''} still to download will go to the week folders as well.`);
    const ok = await dialog({ title: `Split “${F.base(rel)}” into week folders?`, text: 'Files are moved, not copied, and stay tracked as the same Moodle files. You can undo right after.', path: lines.join('\n'), ok: 'Split by week' });
    if (!ok) return;
    const res = await api('/api/fs/subdivide', { rel });
    toast(`Moved ${res.moved.length} files into ${plan.weeks.length} week folder${plan.weeks.length > 1 ? 's' : ''}` + (res.skipped.length ? `; ${res.skipped.length} skipped` : ''), { undo: undoer(res, 'split'), ms: 9000 });
    await F.changed();
  });

  /* ---------- PDF -> text, for pasting into an AI chat (a temporary .txt is written outside your folders) */
  F.pdfText = async (rels) => {
    const stats = h('div', { class: 'muted', text: 'Reading…' }), ta = h('textarea', { readonly: true, rows: 14, 'aria-label': 'Text of the PDF', spellcheck: 'false' });
    const copy = h('button', { class: 'primary', text: 'Copy to clipboard', disabled: true }), keep = h('button', { text: 'Save .txt next to the PDF', disabled: true });
    let full = '', truncated = false;
    const dlg = h('dialog', { class: 'wide' }, h('h3', { text: rels.length > 1 ? `Text of ${rels.length} PDFs` : 'Text of ' + F.base(rels[0]) }), stats, ta,
      h('div', { class: 'btns' }, keep, h('span', { class: 'sp' }), h('button', { text: 'Close', onclick: () => dlg.close() }), copy));
    dlg.addEventListener('close', () => dlg.remove()); document.body.append(dlg); dlg.showModal();
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(full); } catch (e) { ta.select(); document.execCommand('copy'); }
      toast(`Copied ${(ta.dataset.words || '')} words`, { ms: 1800 });
    });
    keep.addEventListener('click', safe(async () => {
      const r = await api('/api/file/new', { rel: rels[0].replace(/\.pdf$/i, '') + '.txt', text: full }); toast('Saved ' + F.base(r.rel)); keep.disabled = true; await F.changed();
    }));
    try {
      const r = await api('/api/pdf/text', { rels });
      full = r.text; truncated = r.truncated; ta.value = full; ta.dataset.words = r.words;
      const errs = r.files.filter((f) => f.error);
      clear(stats).append(h('span', { text: `${r.words.toLocaleString()} words · ${r.chars.toLocaleString()} characters · about ${r.tokens.toLocaleString()} tokens` }),
        truncated ? h('div', { class: 'err', text: 'Very long: only the first part is shown here and copied.' }) : null,
        errs.length ? h('div', { class: 'err', text: errs.map((f) => `${f.label}: ${f.error}`).join(' · ') }) : null,
        r.files.length && r.files.every((f) => f.cached) ? h('div', { class: 'faint', text: 'Already extracted earlier: reused, nothing was read again.' }) : null,
        h('div', { class: 'faint', text: 'A temporary copy was written to ' + r.tmp + ' and is removed after a day.' }));
      copy.disabled = !full; keep.disabled = rels.length !== 1 || truncated || !full; copy.focus();
    } catch (e) { clear(stats).append(h('div', { class: 'err', text: e.message })); }
  };

  /* ---------- what you can do with Moodle files, defined once for both views.
     Each action: { label, key (shortcut), cls, title, on (applies to this selection), sep (a divider before it), fn }. */
  F.redownload = async (fs) => {                         // each file goes back to the folder it had
    const by = new Map(); fs.forEach((f) => { if (!by.has(f.folder)) by.set(f.folder, []); by.get(f.folder).push(f.key); });
    for (const [dir, ks] of by) await F.act.place(ks, dir);
  };
  F.moodleActions = (fs, o = {}) => {
    const of = (...st) => fs.filter((f) => st.includes(f.status)), k = (a) => a.map((f) => f.key), n = (a) => (a.length ? ' ' + a.length : '');
    const dl = of('new', 'update'), nw = of('new'), ig = of('ignored'), ms = of('missing'), pl = of('new', 'ignored', 'missing');
    return [
      { label: 'Download' + n(dl), key: 'd', cls: 'primary', on: !!dl.length, fn: () => F.act.download(k(dl)) },
      { label: 'Download to…', on: !!pl.length, title: 'Pick a folder, then download', fn: () => o.place(pl) },
      o.change ? { label: 'Change folder…', on: !!nw.length, title: 'Choose the folder without downloading yet', fn: () => o.change(nw) } : null,
      { label: 'Ignore' + n(nw), key: 'i', sep: true, on: !!nw.length, fn: () => F.act.ignore(k(nw)) },
      { label: 'Un-ignore' + n(ig), key: 'u', on: !!ig.length, fn: () => F.act.unignore(k(ig)) },
      { label: 'Download again' + n(ms), on: !!ms.length, fn: () => F.redownload(ms) },
      { label: 'Forget' + n(ms), on: !!ms.length, title: 'Stop offering these', fn: () => F.act.forget(k(ms)) },
    ].filter(Boolean);
  };
  F.bar = (info, actions, onClear) => SelBar.show(info, [...actions.filter((a) => a.on && !a.menuOnly).map((a) => ({ label: a.label, cls: a.cls, title: a.title || a.key, fn: a.fn })), { label: 'Clear', fn: onClear }]);
  F.menu = (e, actions) => ctxMenu(e.clientX, e.clientY, actions.flatMap((a) => (a.sep ? ['sep', a] : [a])).map((a) => (a === 'sep' ? a : { label: a.label, key: a.key || a.hint, disabled: !a.on, danger: a.cls === 'danger', fn: a.fn })));
  F.keymap = (build) => { const out = {}; for (const a of build([])) if (a.key) out[a.key] = (ids) => { const x = build(ids).find((y) => y.key === a.key); if (x && x.on) x.fn(); }; return out; };

  /* ---------- quick look: a full-window preview that saves nothing. Moodle files are fetched, shown and dropped (the server keeps no copy).
     it = { kind: 'moodle', f } or { kind: 'local', n }; o = { step(d), place(files) } */
  F.look = (() => {
    let el = null, seq = 0, url = null, cur = {};
    const dropUrl = () => { if (url) { URL.revokeObjectURL(url); url = null; } };
    function keys(e) {
      if (!el || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || document.querySelector('dialog[open]')) return;
      const k = e.key;
      if (k === 'Escape' || k === ' ' || k === 'p') close();
      else if ((k === 'ArrowUp' || k === 'ArrowLeft') && cur.step) cur.step(-1);
      else if ((k === 'ArrowDown' || k === 'ArrowRight') && cur.step) cur.step(1);
      else return;
      e.preventDefault(); e.stopPropagation();
    }
    function close() { seq++; dropUrl(); if (el) { el.remove(); el = null; } removeEventListener('keydown', keys, true); }
    async function show(it, o) {
      cur = o || cur;
      const my = ++seq, f = it.f, n = it.n, name = f ? f.name : n.name, ext = F.ext(name);
      const there = f ? (f.status === 'downloaded' || f.status === 'update') && !!f.rel : true, rel = f ? f.rel : n.rel;
      dropUrl();
      const body = h('div', { class: 'pvbody' }, h('div', { class: 'empty', text: 'Loading…' }));
      const note = there ? h('span', { class: 'tag', text: 'On this computer' }) : h('span', { class: 'tag plan', text: 'Preview only · nothing is saved' });
      const btns = h('div', { class: 'ptools' },
        !there && cur.place ? h('button', { class: 'primary', text: 'Download to…', onclick: () => cur.place([f]) }) : null,
        there && F.inEditor(name) ? h('button', { text: F.TEXT.has(ext) ? 'Edit' : 'Open in editor', onclick: () => go('edit', rel) }) : null,
        there && !F.inEditor(name) ? h('button', { text: 'Open with default program', onclick: () => F.openFile(rel) }) : null,
        h('button', { class: 'ghost', text: 'Close', title: 'Close the preview', onclick: close }));
      const fresh = !el;
      const box = h('div', { class: 'qlook' + (fresh ? ' enter' : ''), 'data-nosel': '', onclick: (e) => { if (e.target === box) close(); } },
        h('section', { class: 'qpanel', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Preview of ' + name }, h('header', {}, h('h2', { text: name, title: name }), note, h('span', { class: 'sp' }), btns), body,
          h('footer', {}, cur.step ? h('span', { text: '↑ ↓ other file' }) : null, h('span', { text: 'Space or Esc close' }))));
      if (el) el.replaceWith(box); else { document.body.append(box); addEventListener('keydown', keys, true); }
      el = box;
      const say = (t, err) => { if (my === seq) clear(body).append(h('div', { class: 'empty' }, h('b', { text: err ? 'No preview' : '' }), t)); };
      try {
        let blob = null, text = null;
        if (F.RAW.has(ext) || (F.TEXT.has(ext) && !there)) {
          const r = await fetch(there ? '/api/raw?rel=' + encodeURIComponent(rel) : '/api/preview?key=' + encodeURIComponent(f.key), { headers: { 'X-Token': TOKEN } });
          if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || r.statusText); }
          if (F.RAW.has(ext)) blob = await r.blob(); else text = await r.text();
        } else if (F.TEXT.has(ext)) { const r = await api('/api/file?rel=' + encodeURIComponent(rel)); text = r.text; if (text === undefined) throw new Error(r.note || 'This file cannot be shown as text'); }
        else throw new Error(`${ext ? '.' + ext : 'This type of'} files have no preview here.` + (there ? '' : ' Download it to open it with your own program.'));
        if (my !== seq) return;
        if (blob) { url = URL.createObjectURL(blob); clear(body).append(ext === 'pdf' ? h('iframe', { src: url, title: name }) : h('img', { src: url, alt: name })); }
        else clear(body).append(h('pre', { class: 'pvtext', text: text.length > 200000 ? text.slice(0, 200000) + '\n… (shortened)' : text }));
      } catch (e) { say(e.message, true); }
    }
    addEventListener('pagehide', dropUrl);
    return { show, close, isOpen: () => !!el };
  })();

  /* ---------- the page */
  App.pages.files = {
    title: 'Files',
    async mount(el, arg) {
      let view = arg === 'organise' || arg === 'allocate' ? arg : null;
      if (view) { try { localStorage.setItem('filesview', view); } catch (e) {} } else { try { view = localStorage.getItem('filesview'); } catch (e) {} }
      if (view !== 'organise') view = 'allocate';
      const sub = h('span', { class: 'sub' }), body = h('div', { class: 'fbody' });
      const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Files view' },
        [['allocate', 'Smart allocation', 'New, ignored and deleted files, by the folder they are assigned to'], ['organise', 'Organise', 'Moodle files and your folders side by side: drag, drop, create folders']]
          .map(([k, l, t]) => h('button', { text: l, title: t, 'aria-pressed': String(view === k), onclick: () => { if (view !== k) go('files', k); } })));
      const host = h('div', { class: 'page fpage ' + view }, h('div', { class: 'fhead' }, h('h1', { text: 'Files' }), sub, h('span', { class: 'sp' }), seg), body);
      el.append(host); if (view === 'organise' && matchMedia('(min-width: 1100px)').matches) el.style.overflow = 'hidden';
      const done = await F.views[view].mount(body, { sub: (t) => { sub.textContent = t; } });
      return () => { el.style.overflow = ''; F.reload = null; F.look.close(); SelBar.hide(); if (done) done(); };
    },
  };
})();
