'use strict';
/* Organise: Moodle's files on the left, your study folder on the right. Drag from left to right to download into a folder,
   drag inside the right tree to move (moves are tracked: a moved file is moved, never "missing"). Create folders, rename,
   split a folder into week folders, view and edit any file, copy a PDF's text for an AI chat. */
(function () {
  const F = App.Files;
  let A = null, T = null, host = null, setSub = () => {}, lSel = null, rSel = null, lbody = null, rbody = null, lFilter = 'todo', lq = '', rq = '', drag = null, lock = new Set(),
    nodeL = new Map(), nodeR = new Map(), elR = new Map(), openL = new Set(), openR = new Set(), dropEl = null, spring = null, first = true, lookTimer = null, elL = new Map(), elG = new Map(), dest = new Map();
  const load_ = (k) => { try { const v = sessionStorage.getItem(k); return v === null ? null : new Set(JSON.parse(v)); } catch (e) { return null; } };
  const save_ = (k, s) => { try { sessionStorage.setItem(k, JSON.stringify([...s])); } catch (e) {} };
  const tally = (fs) => fs.reduce((c, f) => { c[f.status] = (c[f.status] || 0) + 1; return c; }, {});
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/"/g, '\\"'));

  async function load() {
    try { [A, T] = await Promise.all([api('/api/alloc'), api('/api/tree')]); } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'Could not read your folders' }), e.message)); return; }
    { const dirs = [...new Set(A.files.filter((f) => f.status === 'new').map((f) => f.folder))].sort(); dest = new Map(dirs.map((d, i) => [d, i % 8])); }
    App.S.tree = T; lock = new Set(A.courses.map((c) => c.folder).filter(Boolean));
    if (first) {
      first = false; const a = load_('oL'), b = load_('oR');
      if (a) openL = a; else { A.courses.forEach((c) => openL.add('c:' + c.id)); A.files.forEach((f) => openL.add(`w:${f.course}:${f.section}`)); }
      if (b) openR = b; else T.root.kids.forEach((d) => { if (d.type === 'dir') openR.add(d.rel); });
    }
    draw();
  }

  /* ---------- open / close folders without redrawing (so a drag in progress is never interrupted) */
  function setOpen(li, on) {
    const id = li.dataset.open, set = li.dataset.pane === 'l' ? openL : openR;
    li.classList.toggle('open', on); li.setAttribute('aria-expanded', String(on)); on ? set.add(id) : set.delete(id); save_(li.dataset.pane === 'l' ? 'oL' : 'oR', set);
  }
  const cIdx = (folder) => (dest.has(folder) ? dest.get(folder) : 0);
  const toggleLi = (li) => setOpen(li, !li.classList.contains('open'));
  const chev = () => h('span', { class: 'chev', 'data-nosel': '', onclick: (e) => { e.stopPropagation(); toggleLi(e.currentTarget.closest('li')); } }, icon('chev'));

  /* ---------- left: Moodle */
  const where = (f, course) => { if (!f.rel) return ''; const b = course.folder || ''; return f.folder === b ? 'module folder' : b && f.folder.startsWith(b + '/') ? f.folder.slice(b.length + 1) : f.folder; };
  function leftGroup(id, title, sub, files, kids) {
    const searching = !!lq, on = openL.has(id) || searching, t = tally(files), todo = (t.new || 0) + (t.update || 0) + (t.ignored || 0) + (t.missing || 0);
    nodeL.set(id, { group: true, files });
    const row = h('div', { class: 'frow dir lg', draggable: 'true', 'data-group': '', dataset: { sel: id } }, chev(), h('span', { class: 'fic' }, icon('folder')), h('span', { class: 'name', text: title }), sub ? h('span', { class: 'faint sm', text: sub }) : null,
      h('span', { class: 'counts' }, ['new', 'update', 'ignored', 'missing'].filter((k) => t[k]).map((k) => h('span', { class: 'b-' + k, text: t[k] + ' ' + (F.LABEL[k] || k).toLowerCase() }))),
      h('span', { class: 'fmeta', text: todo ? '' : files.length + ' ✓' }));
    return h('li', { class: 'dir' + (on ? ' open' : ''), role: 'treeitem', 'aria-expanded': String(on), 'data-gscope': '', dataset: { open: id, pane: 'l' } }, row, h('ul', { role: 'group' }, kids));
  }
  function buildLeft() {
    nodeL = new Map(); elL = new Map();
    const ul = h('ul', { class: 'tree', role: 'tree', 'aria-multiselectable': 'true', 'aria-label': 'Moodle files' });
    const vis = A.files.filter((f) => (lFilter === 'all' || f.status !== 'downloaded') && (!lq || (f.name + ' ' + f.sec + ' ' + f.activity).toLowerCase().includes(lq)));
    for (const course of A.courses) {
      const cf = vis.filter((f) => f.course === course.id); if (!cf.length) continue;
      const weeks = [...new Set(cf.map((f) => f.section))].sort((a, b) => a - b);
      ul.append(leftGroup('c:' + course.id, course.name || course.short, '', cf, weeks.map((w) => {
        const wf = cf.filter((f) => f.section === w).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })), nm = wf[0].sec;
        return leftGroup(`w:${course.id}:${w}`, w === 0 ? 'General' : /^week\s*\d+$/i.test(nm) ? nm : 'Week ' + w, w && nm && !/^week\s*\d+$/i.test(nm) ? nm : '', wf, wf.map((f) => {
          nodeL.set(f.key, f);
          const there = f.status === 'downloaded' || f.status === 'update';
          const lrow = h('div', { class: 'frow lf st-' + f.status, draggable: 'true', dataset: { sel: f.key }, title: [f.activity, f.rel].filter(Boolean).join('\n') },
            h('span', { class: 'fic' }, icon('file')), h('span', { class: 'name', text: f.name }), f.chosen ? h('span', { class: 'tag', text: 'your folder' }) : null,
            f.status === 'new' ? h('span', { class: 'dest c' + cIdx(f.folder), text: 'will go to ' + where(f, course), title: 'Planned folder, shown in the same colour on the right. Download puts it there; drag it onto another folder to choose.' })
              : h('span', { class: 'fmeta wkl', text: f.status === 'downloaded' ? '→ ' + where(f, course) : '' }),
            h('span', { class: 'badge b-' + f.status, text: F.LABEL[f.status] || f.status }), f.size && !there ? h('span', { class: 'fmeta', text: fmtSize(f.size) }) : null,
            h('span', { class: 'acts' }, h('button', { text: 'Preview', title: 'Look inside without saving it (p)', onclick: (e) => { e.stopPropagation(); lookAt('l', { kind: 'moodle', f }); } })));
          elL.set(f.key, lrow);
          return h('li', { class: 'file st-' + f.status, role: 'treeitem' }, lrow);
        }));
      })));
    }
    if (!ul.children.length) ul.append(h('li', { class: 'empty' }, h('b', { text: lq ? 'Nothing matches' : 'Everything is downloaded' }), lFilter === 'todo' && !lq ? [h('span', { text: 'Nothing waits for a decision. ' }), h('button', { text: 'Show all Moodle files', onclick: () => { lFilter = 'all'; draw(); } })] : null));
    return ul;
  }

  /* ---------- right: your folders */
  const match = (n) => !rq || n.name.toLowerCase().includes(rq) || (n.type === 'dir' && n.kids.some(match));
  const waiting = (n) => (n.type === 'file' ? (n.status === 'new' ? 1 : 0) : n.kids.reduce((s, k) => s + waiting(k), 0));
  /* a file that is still on Moodle, shown where it will land, in the colour of its chip on the left */
  function ghost(n) {
    const folder = F.parent(n.rel);
    const row = h('div', { class: 'frow gh c' + cIdx(folder), draggable: 'true', dataset: { key: n.key, parent: folder }, title: 'Still on Moodle: it will be saved here when you download it. Click to find it on the left; drag it onto another folder to change where it goes.' },
      h('span', { class: 'fic' }, icon('file')), h('span', { class: 'name', text: n.name }), h('span', { class: 'tag plan', text: 'to come' }), h('span', { class: 'fmeta', text: n.size ? fmtSize(n.size) : '' }));
    elG.set(n.key, row);
    return h('li', { class: 'file ghost', role: 'treeitem' }, row);
  }
  function buildNode(n) {
    if (n.type === 'file') {
      if (n.status === 'new') return match(n) ? ghost(n) : null;
      if (n.status === 'ignored' || !match(n)) return null;
      const id = 'p:' + n.rel; nodeR.set(id, n);
      const tip = [n.moodle, n.week].filter(Boolean).join(' · ');
      const row = h('div', { class: 'frow rf st-' + n.status, draggable: n.status === 'missing' ? null : 'true', dataset: { sel: id, parent: F.parent(n.rel) }, title: tip || n.rel },
        h('span', { class: 'fic' }, icon('file')), h('span', { class: 'name', text: n.name }),
        n.key && n.week ? h('span', { class: 'tag', text: n.week.replace(/^Week\s*/i, 'W'), title: 'From Moodle: ' + tip }) : null,
        n.status === 'update' ? h('span', { class: 'badge b-update', text: 'Update' }) : n.status === 'missing' ? h('span', { class: 'badge b-missing', text: 'Deleted here', title: 'Moodle has it, but it is not in this folder any more' }) : ['generated', 'archived'].includes(n.status) ? h('span', { class: 'badge b-' + n.status, text: F.LABEL[n.status] }) : null,
        n.reviewed ? h('span', { class: 'rv', title: 'Reviewed', text: '✓' }) : null,
        h('span', { class: 'fmeta', text: n.size ? fmtSize(n.size) : '' }),
        null);
      elR.set(id, row);
      return h('li', { class: 'file st-' + n.status, role: 'treeitem' }, row);
    }
    if (!match(n)) return null;
    const isRoot = n.rel === '', id = 'p:' + n.rel, locked = lock.has(n.rel), w = waiting(n), inc = n.kids.filter((k) => k.type === 'file' && k.status === 'new').length, kids = n.kids.map(buildNode).filter(Boolean);
    const on = isRoot || openR.has(n.rel) || !!rq;
    if (!isRoot) nodeR.set(id, n);
    const row = h('div', { class: 'frow dir' + (isRoot ? ' rootrow' : ''), draggable: isRoot || locked ? null : 'true', dataset: isRoot ? { dir: '' } : { sel: id, dir: n.rel, parent: F.parent(n.rel) }, title: n.rel || 'Your study folder' },
      isRoot ? h('span', { class: 'chev ph' }) : chev(), h('span', { class: 'fic' }, icon('folder')), h('span', { class: 'name', text: isRoot ? 'Study folder' : n.name }),
      locked ? h('span', { class: 'tag', text: 'module', title: 'Moodle files are matched to this folder, so it stays where it is' }) : null,
      n.virtual && !isRoot ? h('span', { class: 'tag plan', text: 'planned', title: 'Not created yet: ' + plural(w, 'file') + ' will be saved here' }) : null,
      inc ? h('span', { class: 'dest c' + cIdx(n.rel), text: inc + ' to come', title: 'Moodle files that will be saved in this folder' }) : w && !n.virtual ? h('span', { class: 'counts' }, h('span', { class: 'b-new', text: w + ' to come' })) : null,
      h('span', { class: 'fmeta', text: kids.length ? '' : isRoot ? '' : 'empty' }),
      null);
    elR.set(isRoot ? 'root' : id, row);
    return h('li', { class: 'dir' + (on ? ' open' : '') + (isRoot ? ' root' : ''), role: 'treeitem', 'aria-expanded': String(on), dataset: { open: n.rel, pane: 'r', dir: n.rel } }, row, h('ul', { role: 'group' }, kids));
  }
  function buildRight() {
    nodeR = new Map(); elR = new Map(); elG = new Map();
    return h('ul', { class: 'tree', role: 'tree', 'aria-multiselectable': 'true', 'aria-label': 'Your study folder' }, buildNode(T.root));
  }

  /* ---------- drawing */
  function pane(cls, title, tools, body) { return h('section', { class: 'pane ' + cls }, h('header', {}, h('h2', { text: title }), tools), body); }
  function draw() {
    if (!host) return;
    const keepL = lSel ? lSel.ids() : [], keepR = rSel ? rSel.ids() : [], sl = lbody ? lbody.scrollTop : 0, sr = rbody ? rbody.scrollTop : 0;
    if (lSel) lSel.destroy(); if (rSel) rSel.destroy();
    const c = tally(A.files), todo = (c.new || 0) + (c.update || 0) + (c.ignored || 0) + (c.missing || 0);
    let nfiles = 0; (function w(n) { if (n.type === 'file') { if (n.status !== 'new' && n.status !== 'ignored') nfiles++; } else n.kids.forEach(w); })(T.root);
    setSub(`${plural(nfiles, 'file')} in your folders · ${todo} Moodle file${todo === 1 ? '' : 's'} to place`);
    const ltree = buildLeft(), rtree = buildRight();
    lbody = h('div', { class: 'pbody' }, ltree); rbody = h('div', { class: 'pbody' }, rtree);
    const lfilter = h('div', { class: 'seg sm' }, [['todo', 'To place'], ['all', 'All']].map(([k, l]) => h('button', { text: l, 'aria-pressed': String(lFilter === k), onclick: () => { lFilter = k; draw(); } })));
    const sbox = (val, on) => h('div', { class: 'search sm' }, icon('search'), h('input', { type: 'search', 'data-search': '', placeholder: 'Filter', value: val, 'aria-label': 'Filter', oninput: on }));
    const lsearch = sbox(lq, (e) => { lq = e.target.value.trim().toLowerCase(); redrawKeepFocus(e.target); });
    const rsearch = sbox(rq, (e) => { rq = e.target.value.trim().toLowerCase(); redrawKeepFocus(e.target); });
    const rtools = h('div', { class: 'ptools' },
      h('button', { class: 'ghost', text: 'Expand', onclick: () => { $$('li.dir', rbody).forEach((li) => setOpen(li, true)); } }), h('button', { class: 'ghost', text: 'Collapse', onclick: () => { $$('li.dir:not(.root)', rbody).forEach((li) => setOpen(li, false)); } }), rsearch);
    clear(host).append(pane('left', 'Moodle', h('div', { class: 'ptools' }, lfilter, lsearch), lbody), pane('right', 'Your folders', rtools, rbody),
      h('p', { class: 'hint', text: 'Press p for a quick look inside a file without saving it. Files still to come are shown where they will land, in the colour of their chip on the left. Download sends a file there; drag it onto another folder to choose where it goes instead. Right-click a file or folder for more (new folder, split by week, extract text, create notes, rename, move, delete). Click a row to select it; click the round box, hold Ctrl/Cmd or Shift, or drag a box to select several.' }));
    lSel = Selectable(ltree, { onChange: (ids) => onSel('l', ids), onOpen: (id) => openL_(id), onContext: (e, ids) => F.menu(e, buildL(ids)), scroller: lbody, bandOnRows: false, keys: F.keymap(buildL) });
    const rkeys = F.keymap(buildR); rkeys.Backspace = rkeys.Delete;
    rSel = Selectable(rtree, { onChange: (ids) => onSel('r', ids), onOpen: (id) => openR_(id), onContext: (e, ids) => F.menu(e, buildR(ids)), scroller: rbody, bandOnRows: false, keys: rkeys });
    rbody.addEventListener('contextmenu', (e) => { if (e.target.closest('[data-sel]')) return; e.preventDefault(); F.menu(e, [{ label: 'New folder', on: true, fn: () => doNew(parentFor()) }, { label: 'New file here…', on: true, fn: () => doNewFile(parentFor()) }]); });
    wireDrag();
    lSel.set(keepL.filter((i) => nodeL.has(i))); rSel.set(keepR.filter((i) => nodeR.has(i)));
    lbody.scrollTop = sl; rbody.scrollTop = sr;
    if (!keepL.length && !keepR.length) SelBar.hide();
  }
  function redrawKeepFocus(input) { const pos = input.selectionStart, pane_ = input.closest('.pane').classList.contains('left') ? 'left' : 'right'; draw(); const i = $('.pane.' + pane_ + ' [data-search]', host); if (i) { i.focus(); i.setSelectionRange(pos, pos); } }

  /* ---------- selection helpers */
  const leftFiles = (ids) => { const out = new Map(); ids.forEach((i) => { const n = nodeL.get(i); if (n && !n.group) out.set(n.key, n); }); return [...out.values()]; };
  const rightNodes = (ids) => ids.map((i) => nodeR.get(i)).filter(Boolean);
  const movable = (n) => n.status !== 'missing' && !(n.type === 'dir' && lock.has(n.rel));
  function parentFor() { const ns = rightNodes(rSel ? rSel.ids() : []); if (ns.length === 1) return ns[0].type === 'dir' ? ns[0].rel : F.parent(ns[0].rel); return ''; }
  function openL_(id) { const n = nodeL.get(id); if (!n) return; if (n.group) { const li = $(`li[data-open="${esc(id)}"]`, lbody); if (li) toggleLi(li); } else if ((n.status === 'downloaded' || n.status === 'update') && n.rel) reveal(n.rel); }
  function openR_(id) { const n = nodeR.get(id); if (!n) return; if (n.type === 'dir') { const li = elR.get(id).closest('li'); toggleLi(li); } else if (n.status !== 'missing') F.openFile(n.rel); }
  function showMoved(moved) {                      // open the folders the moved items are in, select them, scroll to the first
    const ids = moved.map((m) => 'p:' + m.to).filter((i) => nodeR.has(i)); if (!ids.length) return;
    moved.forEach((m) => { const parts = m.to.split('/'); for (let i = 1; i < parts.length; i++) { const li = $(`li.dir[data-pane="r"][data-open="${esc(parts.slice(0, i).join('/'))}"]`, rbody); if (li) setOpen(li, true); } });
    rSel.set(ids); const el = elR.get(ids[0]); if (el) el.scrollIntoView({ block: 'nearest' });
  }
  function reveal(rel) {
    const parts = rel.split('/'); for (let i = 1; i < parts.length; i++) { const li = $(`li.dir[data-pane="r"][data-open="${esc(parts.slice(0, i).join('/'))}"]`, rbody); if (li) setOpen(li, true); }
    rSel.set(['p:' + rel]); const el = elR.get('p:' + rel); if (el) el.scrollIntoView({ block: 'center' });
  }

  let crossing = false;
  function onSel(side, ids) {
    if (crossing) return;                                   // the other pane is being cleared because this one now has the selection
    if (ids.length) { const other = side === 'l' ? rSel : lSel; if (other && other.size()) { crossing = true; other.clear(); crossing = false; } }
    if (side === 'l') hilite(leftFiles(ids));
    if (!ids.length) { if (!(lSel && lSel.size()) && !(rSel && rSel.size())) SelBar.hide(); return; }
    if (side === 'l') F.bar(`${plural(leftFiles(ids).length, 'Moodle file')} selected`, buildL(ids), () => lSel.clear());
    else F.bar(`${plural(rightNodes(ids).length, 'item')} selected`, buildR(ids), () => rSel.clear());
    if (F.look.isOpen()) { clearTimeout(lookTimer); lookTimer = setTimeout(() => lookIds(side, ids), 120); }
  }
  const keys = (fs) => fs.map((f) => f.key);

  /* ---------- link the two sides: what you pick on the left lights up where it will land, and the other way round */
  function hilite(fs) {
    $$('.hl', rbody).forEach((e) => e.classList.remove('hl'));
    let first = null;
    fs.filter((f) => f.status === 'new').forEach((f) => {
      const g = elG.get(f.key); if (!g) return; g.classList.add('hl'); first = first || g;
      for (let li = g.closest('li.dir'); li; li = li.parentElement.closest('li.dir')) if (!li.classList.contains('open')) setOpen(li, true);
      const dr = elR.get(g.dataset.parent ? 'p:' + g.dataset.parent : 'root'); if (dr) dr.classList.add('hl');
    });
    if (first) first.scrollIntoView({ block: 'nearest' });
  }
  function link(k) { $$('.link', host).forEach((x) => x.classList.remove('link')); if (k) [elL.get(k), elG.get(k)].forEach((x) => x && x.classList.add('link')); }
  function findOnLeft(key) {
    const row = elL.get(key); if (!row) return;
    for (let li = row.closest('li.dir'); li; li = li.parentElement.closest('li.dir')) if (!li.classList.contains('open')) setOpen(li, true);
    lSel.set([key]); row.scrollIntoView({ block: 'center' });
  }

  /* ---------- left actions */
  async function placeInto(fs) {
    const ks = keys(fs.filter((f) => ['new', 'ignored', 'missing'].includes(f.status)));
    const dir = await F.pickFolder({ title: `Download ${plural(ks.length, 'file')} to…`, text: 'They are saved in this folder. The choice is remembered for these files.', ok: 'Download here', start: parentFor() });
    if (dir !== null) F.act.place(ks, dir);
  }
  async function moveOnDisk(fs) {
    const rels = fs.map((f) => f.rel), dir = await F.pickFolder({ title: `Move ${plural(rels.length, 'file')} to…`, ok: 'Move here', start: F.parent(rels[0]) });
    if (dir !== null) { const r = await F.moveTo(rels, dir); if (r && r.moved.length) showMoved(r.moved); }
  }
  /* the bar, the right-click menu and the keys all come from this list */
  function buildL(ids) {
    const fs = leftFiles(ids), on = fs.filter((f) => (f.status === 'downloaded' || f.status === 'update') && f.rel);
    return [...F.moodleActions(fs, { place: placeInto }),
      { label: 'Preview', key: 'p', sep: true, on: fs.length === 1, title: 'Quick look without saving (p)', fn: () => lookIds('l', ids) },
      { label: on.length ? `Move ${on.length} to…` : 'Move to…', on: !!on.length, fn: () => moveOnDisk(on) },
      { label: 'Show in your folders', on: on.length === 1, fn: () => reveal(on[0].rel) }];
  }

  /* ---------- right actions */
  const doNew = async (parent) => { const rel = await F.newFolder(parent); if (rel) { const par = rel.includes('/') ? F.parent(rel) : ''; const li = $(`li.dir[data-pane="r"][data-open="${esc(par)}"]`, rbody); if (li) setOpen(li, true); setTimeout(() => reveal(rel), 0); } };
  const doNewFile = safe(async (parent) => {
    const f = await dialog({ title: 'New file', text: 'Name it with .md, .txt, .py or .tex. Folders in the path are created if needed.', fields: [{ name: 'rel', label: 'Path', value: (parent ? parent + '/' : '') + 'notes.md' }], ok: 'Create' });
    if (f && f.rel.trim()) { const r = await api('/api/file/new', { rel: f.rel.trim(), text: '' }); go('edit', r.rel); }
  });
  async function doMove(ids) {
    const ns = rightNodes(ids).filter(movable); if (!ns.length) return;
    const dir = await F.pickFolder({ title: `Move ${plural(ns.length, 'item')} to…`, ok: 'Move here', start: F.parent(ns[0].rel), hide: ns.filter((n) => n.type === 'dir').map((n) => n.rel) });
    if (dir === null) return; const r = await F.moveTo(ns.map((n) => n.rel), dir); if (r && r.moved.length) showMoved(r.moved);
  }
  async function doRename(ids) { const ns = rightNodes(ids).filter(movable); if (ns.length !== 1) return; const to = await F.rename(ns[0].rel); if (to) setTimeout(() => reveal(to), 0); }
  const doDelete = safe(async (ids) => {
    const ns = rightNodes(ids).filter(movable), files = ns.filter((n) => n.type === 'file'), dirs = ns.filter((n) => n.type === 'dir'); if (!ns.length) return;
    const lines = [...files, ...dirs].slice(0, 12).map((n) => n.rel + (n.type === 'dir' ? '/' : '')); if (ns.length > 12) lines.push(`… and ${ns.length - 12} more`);
    const ok = await dialog({ title: `Delete ${plural(ns.length, 'item')}?`, text: (files.length ? 'Files go to the Trash and can be restored. ' : '') + (dirs.length ? 'Folders are removed only if they are empty.' : ''), path: lines.join('\n'), ok: 'Delete', danger: true });
    if (!ok) return; let done = 0; const fail = [];
    for (const n of files) { try { await api('/api/delete', { rel: n.rel }); done++; } catch (e) { fail.push(e.message); } }
    for (const n of dirs) { try { await api('/api/fs/rmdir', { rel: n.rel }); done++; } catch (e) { fail.push(n.name + ': ' + e.message); } }
    rSel.clear(); toast(`${done} deleted` + (fail.length ? `. ${fail.length} not deleted: ${fail[0]}` : ''), fail.length ? { err: true } : {}); await F.changed();
  });
  const doReview = safe(async (ids) => {
    const rv = rightNodes(ids).filter((n) => n.type === 'file' && n.key && n.status !== 'missing'); if (!rv.length) return;
    const on = !rv.every((n) => n.reviewed); await api('/api/reviewed', { keys: rv.map((n) => n.key), on });
    toast(on ? `Marked ${rv.length} as reviewed` : `Unmarked ${rv.length}`, { undo: safe(async () => { await api('/api/reviewed', { keys: rv.map((n) => n.key), on: !on }); await F.changed(); }) }); await F.changed();
  });
  const doPdf = (ids) => { const p = rightNodes(ids).filter((n) => n.type === 'file' && n.name.toLowerCase().endsWith('.pdf') && n.status !== 'missing'); if (p.length) F.pdfText(p.map((n) => n.rel)); };
  function buildR(ids) {
    const ns = rightNodes(ids), mv = ns.filter(movable), one = ns.length === 1 ? ns[0] : null, isFile = !!one && one.type === 'file' && one.status !== 'missing', isDir = !!one && one.type === 'dir';
    const pdfs = ns.filter((n) => n.type === 'file' && n.name.toLowerCase().endsWith('.pdf') && n.status !== 'missing'), rv = ns.filter((n) => n.type === 'file' && n.key && n.status !== 'missing'), notable = ns.filter((n) => n.status !== 'missing' && (n.type === 'dir' || /\.(pdf|txt|md)$/i.test(n.name)) && n.rel);
    return [
      { label: 'Open', cls: 'primary', hint: 'Enter', on: isFile, fn: () => F.openFile(one.rel) },
      { label: 'Open with default program', menuOnly: true, on: isFile, fn: safe(async () => { await api('/api/open', { rel: one.rel }); }) },
      { label: 'Preview', key: 'p', on: isFile, title: 'Quick look (p)', fn: () => lookIds('r', ids) },
      { label: isDir ? 'New folder inside' : 'New folder', key: 'n', sep: true, on: true, menuOnly: true, fn: () => doNew(parentFor()) },
      { label: 'New file here…', menuOnly: true, on: true, fn: () => doNewFile(parentFor()) },
      { label: 'Split by week', menuOnly: true, on: isDir, title: 'Move the files in this folder into one folder per week', fn: () => F.subdivide(one.rel) },
      { label: 'Read PDFs into text files', menuOnly: true, on: isDir && !!T.read_ready, fn: () => Jobs.run(api('/api/read', { rel: one.rel }), 'Reading PDFs…') },
      { label: pdfs.length > 1 ? `Extract text of ${pdfs.length} PDFs` : 'Extract text', key: 't', sep: true, menuOnly: true, on: !!pdfs.length, title: 'Only extract the text (to copy for an AI chat). Reuses text already extracted. (t)', fn: () => doPdf(ids) },
      { label: 'Create notes…', key: 'g', menuOnly: true, on: !!notable.length, title: 'Opens the AI page with these files selected (g)', fn: () => go('ai', 'notes:' + notable.map((n) => n.rel).join('|')) },
      { label: (rv.length && rv.every((n) => n.reviewed) ? 'Unmark reviewed' : 'Mark reviewed') + (rv.length ? ' ' + rv.length : ''), key: 'r', menuOnly: true, on: !!rv.length, fn: () => doReview(ids) },
      { label: 'Rename', key: 'F2', sep: true, menuOnly: true, on: !!one && movable(one), fn: () => doRename(ids) },
      { label: mv.length ? `Move ${mv.length} to…` : 'Move to…', key: 'm', menuOnly: true, on: !!mv.length, fn: () => doMove(ids) },
      { label: mv.length ? `Delete ${mv.length}` : 'Delete', key: 'Delete', cls: 'danger', menuOnly: true, on: !!mv.length, title: 'Delete', fn: () => doDelete(ids) },
    ];
  }

  /* ---------- quick look (the viewer itself lives in files.js) */
  const lookAt = (side, it) => F.look.show(it, { step: (d) => (side === 'l' ? lSel : rSel).step(d), place: placeInto });
  function lookIds(side, ids) {
    if (side === 'l') { const fs = leftFiles(ids); if (fs.length === 1) lookAt('l', { kind: 'moodle', f: fs[0] }); }
    else { const ns = rightNodes(ids); if (ns.length === 1 && ns[0].type === 'file' && ns[0].status !== 'missing') lookAt('r', { kind: 'local', n: ns[0] }); }
  }

  /* ---------- drag and drop (mouse; on touch use "Move to…" / "Download to…") */
  const locked_ = (n) => n.type === 'dir' && lock.has(n.rel);
  function wireDrag() {
    const begin = (e, d) => {
      drag = d; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', d.label);
      const badge = h('div', { class: 'dragbadge', text: d.label }); document.body.append(badge); e.dataTransfer.setDragImage(badge, -10, -6); setTimeout(() => badge.remove(), 0);
      document.body.classList.add('is-dragging');
    };
    const start = (side) => (e) => {
      const gh = e.target.closest && e.target.closest('.gh');
      if (gh) { const k = gh.dataset.key, ks = lSel.has(k) ? leftFiles(lSel.ids()).filter((f) => f.status === 'new').map((f) => f.key) : [k]; return begin(e, { from: 'plan', keys: ks, label: plural(ks.length, 'planned file') }); }
      const row = e.target.closest && e.target.closest('[data-sel]'); if (!row || row.getAttribute('draggable') !== 'true') { e.preventDefault(); return; }
      const S = side === 'l' ? lSel : rSel; if (!row.classList.contains('selected')) S.pickRow(row);
      const ids = S.ids();
      if (side === 'l') { const fs = leftFiles(ids); if (!fs.length) { e.preventDefault(); return; } begin(e, { from: 'moodle', files: fs, label: plural(fs.length, 'file') }); }
      else { const ns = rightNodes(ids).filter((n) => n.status !== 'missing' && !locked_(n)); if (!ns.length) { e.preventDefault(); return; } begin(e, { from: 'local', rels: ns.map((n) => n.rel), label: ns.length === 1 ? ns[0].name : plural(ns.length, 'item') }); }
    };
    lbody.addEventListener('dragstart', start('l')); rbody.addEventListener('dragstart', start('r'));
    rbody.addEventListener('dragover', onOver); rbody.addEventListener('drop', onDrop);
    rbody.addEventListener('dragleave', (e) => { if (!rbody.contains(e.relatedTarget)) clearDrop(); });
    addEventListener('dragend', endDrag, true);
  }
  function target(e) {
    const row = e.target.closest && e.target.closest('.frow'); let rel = '', el = elR.get('root');
    if (row && row.classList.contains('dir') && row.dataset.dir !== undefined) { rel = row.dataset.dir; el = row; }
    else if (row && row.dataset.parent !== undefined) { rel = row.dataset.parent; el = rel ? elR.get('p:' + rel) : elR.get('root'); }
    return { rel, el };
  }
  function canDrop(rel) {
    if (!drag) return false;
    if (drag.from === 'moodle') return true;
    if (drag.from === 'plan') return drag.keys.some((k) => { const f = nodeL.get(k); return f && f.folder !== rel; });
    return !drag.rels.some((r) => rel === r || rel.startsWith(r + '/')) && drag.rels.some((r) => F.parent(r) !== rel);
  }
  function clearDrop() { if (dropEl) dropEl.classList.remove('drop'); dropEl = null; clearTimeout(spring); spring = null; }
  function onOver(e) {
    if (!drag) return;
    const t = target(e);
    if (!t.el || !canDrop(t.rel)) { clearDrop(); e.dataTransfer.dropEffect = 'none'; return; }
    e.preventDefault(); e.dataTransfer.dropEffect = 'move';
    if (dropEl !== t.el) {
      clearDrop(); dropEl = t.el; dropEl.classList.add('drop');
      const li = dropEl.closest('li.dir'); if (li && !li.classList.contains('open')) spring = setTimeout(() => { if (dropEl === t.el) setOpen(li, true); }, 700);   // open a folder you hover over
    }
    const b = rbody.getBoundingClientRect(); if (e.clientY > b.bottom - 44) rbody.scrollTop += 16; else if (e.clientY < b.top + 44) rbody.scrollTop -= 16;
  }
  function endDrag() { drag = null; clearDrop(); document.body.classList.remove('is-dragging'); $$('.dragbadge').forEach((x) => x.remove()); }
  async function onDrop(e) {
    if (!drag) return; const t = target(e); if (!canDrop(t.rel)) return endDrag();
    e.preventDefault(); const d = drag, rel = t.rel; endDrag();
    if (d.from === 'plan') return F.act.retarget(d.keys, rel);
    if (d.from === 'local') { const r = await F.moveTo(d.rels, rel); if (r && r.moved.length) showMoved(r.moved); return; }
    const place = d.files.filter((f) => ['new', 'ignored', 'missing'].includes(f.status)), there = d.files.filter((f) => (f.status === 'downloaded' || f.status === 'update') && f.rel);
    if (there.length) { const r = await F.moveTo(there.map((f) => f.rel), rel); if (r && r.moved.length) showMoved(r.moved); }
    if (place.length) F.act.place(keys(place), rel);
  }

  F.views.organise = {
    async mount(el, o) {
      host = h('div', { class: 'org' }); el.append(host); setSub = o.sub; first = true;
      host.addEventListener('mouseover', (e) => { const r = e.target.closest && e.target.closest('.gh, .frow.lf'); link(r ? r.dataset.key || r.dataset.sel : null); });
      host.addEventListener('mouseleave', () => link(null));
      host.addEventListener('click', (e) => { const g = e.target.closest && e.target.closest('.gh'); if (g && !e.target.closest('button')) findOnLeft(g.dataset.key); });
      const l = (what) => { if (what === 'job' && host && host.isConnected) load(); };
      App.listeners.push(l); F.reload = load;
      await load();
      return () => { clearTimeout(lookTimer); F.look.close(); App.listeners = App.listeners.filter((x) => x !== l); removeEventListener('dragend', endDrag, true); if (lSel) lSel.destroy(); if (rSel) rSel.destroy(); lSel = rSel = lbody = rbody = host = null; endDrag(); };
    },
  };
})();
