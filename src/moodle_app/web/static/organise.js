'use strict';
/* Files, one view: Moodle's files on the left, your study folder on the right, and between them an arrow from every file that
   is still on Moodle to the folder it will be saved in (the suggestion). Dashed = the app's suggestion, solid = a folder you
   chose. Download follows the arrow; drag a file onto another folder (or "Change destination…") to send it elsewhere.
   Inside the right tree, drag to move (moves are tracked: a moved file is moved, never "missing"), create folders, rename,
   split a folder into week folders, view and edit any file. */
(function () {
  const F = App.Files;
  const ARROW_MAX = 60;                       // above this many waiting files only the arrows you point at or select are drawn
  const SHOW = [['todo', 'To place'], ['new', 'New'], ['update', 'Updated'], ['ignored', 'Ignored'], ['missing', 'Deleted here'], ['downloaded', 'Downloaded'], ['all', 'Everything']];
  const NS = 'http://www.w3.org/2000/svg';
  let A = null, T = null, host = null, setSub = () => {}, setTools = () => {}, lSel = null, rSel = null, lbody = null, rbody = null, lFilter = 'todo', lq = '', rq = '', drag = null, lock = new Set(),
    nodeL = new Map(), nodeR = new Map(), elR = new Map(), elL = new Map(), openL = new Set(), openR = new Set(), dropEl = null, spring = null, first = true, lookTimer = null, dest = new Map(),
    svg = null, hover = null, raf = 0, flowing = new Set(), ro = null, arrowMode = 'all', focusRel = '';
  const load_ = (k) => { try { const v = sessionStorage.getItem(k); return v === null ? null : new Set(JSON.parse(v)); } catch (e) { return null; } };
  const save_ = (k, s) => { try { sessionStorage.setItem(k, JSON.stringify([...s])); } catch (e) {} };
  const tally = (fs) => fs.reduce((c, f) => { c[f.status] = (c[f.status] || 0) + 1; return c; }, {});
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/"/g, '\\"'));
  const inShow = (f) => (lFilter === 'all' ? true : lFilter === 'todo' ? f.status !== 'downloaded' : f.status === lFilter);
  const under = (folder, dir) => dir === '' || folder === dir || folder.startsWith(dir + '/');
  const NOTE = { update: 'Update', ignored: 'Ignored', missing: 'Deleted here', archived: 'Old version', generated: 'Generated' };

  async function load() {
    const before = A ? new Map(A.files.map((f) => [f.key, f.status])) : null;
    try { [A, T] = await Promise.all([api('/api/alloc'), api('/api/tree')]); } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'Could not read your folders' }), e.message)); return; }
    { const dirs = [...new Set(A.files.filter((f) => f.status === 'new').map((f) => f.folder))].sort(); dest = new Map(dirs.map((d, i) => [d, i % 8])); }
    App.S.tree = T; lock = new Set(A.courses.map((c) => c.folder).filter(Boolean));
    if (first) {
      first = false; const a = load_('oL'), b = load_('oR');
      if (a) openL = a; else { A.courses.forEach((c) => openL.add('c:' + c.id)); for (let y = 0; y < 10; y++) openL.add('y:' + y); A.files.forEach((f) => openL.add(`w:${f.course}:${f.section}`)); }
      if (b) openR = b; else T.root.kids.forEach((d) => { if (d.type === 'dir') openR.add(d.rel); });
    }
    const landed = before ? A.files.filter((f) => f.status === 'downloaded' && f.rel && ['new', 'missing'].includes(before.get(f.key))).map((f) => f.rel) : [];
    draw();
    if (landed.length) land(landed);
  }

  /* ---------- open / close folders without redrawing (so a drag in progress is never interrupted) */
  function setOpen(li, on) {
    const id = li.dataset.open, set = li.dataset.pane === 'l' ? openL : openR;
    li.classList.toggle('open', on); li.setAttribute('aria-expanded', String(on)); on ? set.add(id) : set.delete(id); save_(li.dataset.pane === 'l' ? 'oL' : 'oR', set);
    queueArrows();
  }
  const cIdx = (folder) => (dest.has(folder) ? dest.get(folder) : 0);
  const toggleLi = (li) => setOpen(li, !li.classList.contains('open'));
  const chev = () => h('span', { class: 'chev', 'data-nosel': '', onclick: (e) => { e.stopPropagation(); toggleLi(e.currentTarget.closest('li')); } }, icon('chev'));
  function openTo(rel, pane = 'r') {            // open every folder above `rel` in the right tree
    const parts = rel.split('/'), body = pane === 'r' ? rbody : lbody;
    for (let i = 1; i < parts.length; i++) { const li = $(`li.dir[data-pane="${pane}"][data-open="${esc(parts.slice(0, i).join('/'))}"]`, body); if (li && !li.classList.contains('open')) setOpen(li, true); }
  }

  /* ---------- left: Moodle */
  const where = (f, course) => { if (!f.rel) return ''; const b = course.folder || ''; return f.folder === b ? 'module folder' : b && f.folder.startsWith(b + '/') ? f.folder.slice(b.length + 1) : f.folder; };
  function leftGroup(id, title, sub, files, kids) {
    const searching = !!lq, on = openL.has(id) || searching, t = tally(files), todo = (t.new || 0) + (t.update || 0) + (t.missing || 0);
    nodeL.set(id, { group: true, files });
    const row = h('div', { class: 'frow dir lg', draggable: 'true', 'data-group': '', dataset: { sel: id, gid: id } }, chev(), h('span', { class: 'fic' }, icon('folder')), h('span', { class: 'name', text: title }), sub ? h('span', { class: 'faint sm', text: sub }) : null,
      h('span', { class: 'fmeta', text: todo ? todo + ' to place' : files.length + ' ✓' }));
    elL.set(id, row);
    return h('li', { class: 'dir' + (on ? ' open' : ''), role: 'treeitem', 'aria-expanded': String(on), 'data-gscope': '', dataset: { open: id, pane: 'l' } }, row, h('ul', { role: 'group' }, kids));
  }
  function buildLeft() {
    nodeL = new Map(); elL = new Map();
    const ul = h('ul', { class: 'tree', role: 'tree', 'aria-multiselectable': 'true', 'aria-label': 'Moodle files' });
    /* a focused folder on the right narrows the Moodle side to what belongs there: Y3 shows the year 3 courses, a module
       its course, a folder inside a module only the files planned or saved in it */
    const fc = (c) => !focusRel || under(c.folder || '', focusRel) || under(focusRel, c.folder || '\u0000');
    const ff = (f) => { if (!focusRel) return true; const c = A.courses.find((x) => x.id === f.course); if (!c || !fc(c)) return false; return under(c.folder || '', focusRel) || under(f.folder || '', focusRel); };
    const vis = A.files.filter((f) => ff(f) && inShow(f) && (!lq || (f.name + ' ' + f.sec + ' ' + f.activity).toLowerCase().includes(lq)));
    const yearOf = (c) => { const m = /[A-Z]{3,6}\.?(\d)\d{2}(?!\d)/.exec(((c.short || '') + ' ' + (c.name || '')).toUpperCase()); return m ? +m[1] : 0; };
    const years = [...new Set(A.courses.filter(fc).map(yearOf))].sort((a, b) => (a || 99) - (b || 99));
    const byYear = years.length > 1;                        // one year only: no extra level
    for (const yr of years) {
     const yul = byYear ? [] : null;
     for (const course of A.courses.filter((c) => fc(c) && yearOf(c) === yr)) {
      const cf = vis.filter((f) => f.course === course.id); if (!cf.length) continue;
      const weeks = [...new Set(cf.map((f) => f.section))].sort((a, b) => a - b);
      const node = (leftGroup('c:' + course.id, course.name || course.short, '', cf, weeks.map((w) => {
        const wf = cf.filter((f) => f.section === w).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })), nm = wf[0].sec;
        return leftGroup(`w:${course.id}:${w}`, w === 0 ? 'General' : /^week\s*\d+$/i.test(nm) ? nm : 'Week ' + w, w && nm && !/^week\s*\d+$/i.test(nm) ? nm : '', wf, wf.map((f) => {
          nodeL.set(f.key, f);
          const there = f.status === 'downloaded' || f.status === 'update', isNew = f.status === 'new', to = where(f, course);
          const tip = [f.activity, isNew ? `Will be saved in ${to}` + (f.chosen ? ' (your choice)' : ' (suggested)') : f.rel, F.LABEL[f.status]].filter(Boolean).join('\n');
          const lrow = h('div', { class: 'frow lf st-' + f.status + (isNew ? ' c' + cIdx(f.folder) : ''), draggable: 'true', dataset: { sel: f.key }, title: tip },
            h('span', { class: 'fic s-' + f.status }, icon('file')), h('span', { class: 'name', text: f.name }),
            NOTE[f.status] ? h('span', { class: 'stt s-' + f.status, text: NOTE[f.status] }) : null,
            isNew ? h('span', { class: 'to', text: '→ ' + to }) : null,
            h('span', { class: 'fmeta', text: f.size && !there ? fmtSize(f.size) : '' }),
            h('span', { class: 'acts' }, h('button', { text: 'Preview', title: 'Look inside without saving it (p)', onclick: (e) => { e.stopPropagation(); lookAt('l', { kind: 'moodle', f }); } })));
          elL.set(f.key, lrow);
          return h('li', { class: 'file st-' + f.status, role: 'treeitem' }, lrow);
        }));
      })));
      if (yul) yul.push(node); else ul.append(node);
     }
     if (yul && yul.length) { const files = vis.filter((f) => yearOf(A.courses.find((c) => c.id === f.course) || {}) === yr); ul.append(leftGroup('y:' + yr, yr ? 'Year ' + yr : 'Other courses', '', files, yul)); }
    }
    if (!ul.children.length) ul.append(h('li', { class: 'empty' }, h('b', { text: lq ? 'Nothing matches' : lFilter === 'todo' ? 'Everything is downloaded' : 'Nothing here' }),
      lFilter !== 'all' && !lq ? [h('span', { text: lFilter === 'todo' ? 'Nothing waits for a decision. ' : 'No Moodle file is in this state. ' }), h('button', { text: 'Show all Moodle files', onclick: () => setShow('all') })] : null));
    return ul;
  }

  /* ---------- right: your folders */
  const match = (n) => !rq || n.name.toLowerCase().includes(rq) || (n.type === 'dir' && n.kids.some(match));
  const waiting = (n) => (n.type === 'file' ? (n.status === 'new' ? 1 : 0) : n.kids.reduce((s, k) => s + waiting(k), 0));
  const firstDest = (n) => { if (dest.has(n.rel) && n.kids.some((k) => k.type === 'file' && k.status === 'new')) return n.rel; for (const k of n.kids) if (k.type === 'dir') { const d = firstDest(k); if (d !== null) return d; } return null; };
  function buildNode(n) {
    if (n.type === 'file') {
      if (n.status === 'new' || n.status === 'ignored' || !match(n)) return null;       // files still on Moodle are not here yet: the arrow says where they will go
      const id = 'p:' + n.rel; nodeR.set(id, n);
      const tip = [n.moodle, n.week].filter(Boolean).join(' · ');
      const row = h('div', { class: 'frow rf st-' + n.status, draggable: n.status === 'missing' ? null : 'true', dataset: { sel: id, parent: F.parent(n.rel) }, title: [n.rel, tip].filter(Boolean).join('\n') },
        h('span', { class: 'fic s-' + n.status }, icon('file')), h('span', { class: 'name', text: n.name }),
        NOTE[n.status] ? h('span', { class: 'stt s-' + n.status, text: NOTE[n.status], title: n.status === 'missing' ? 'Moodle has it, but it is not in this folder any more' : null }) : null,
        n.reviewed ? h('span', { class: 'rv', title: 'Reviewed', text: '✓' }) : null,
        h('span', { class: 'fmeta', text: n.size ? fmtSize(n.size) : '' }));
      elR.set(id, row);
      return h('li', { class: 'file st-' + n.status, role: 'treeitem' }, row);
    }
    if (!match(n)) return null;
    const isRoot = n.rel === '', id = 'p:' + n.rel, locked = lock.has(n.rel), w = waiting(n), inc = n.kids.filter((k) => k.type === 'file' && k.status === 'new').length, kids = n.kids.map(buildNode).filter(Boolean);
    const on = isRoot || openR.has(n.rel) || !!rq;
    if (!isRoot) nodeR.set(id, n);
    const mark = (count, kind, colour) => (count ? h('button', { class: `inc ${kind} c${cIdx(colour)}`, 'data-nosel': '', text: '+' + count, title: `${plural(count, 'Moodle file')} will be saved ${kind === 'total' ? 'under this folder' : 'in this folder'}. Click to select ${count === 1 ? 'it' : 'them'} on the left.`,
      onclick: (e) => { e.stopPropagation(); selectIncoming(n.rel, kind === 'total'); } }) : null);
    const row = h('div', { class: 'frow dir' + (isRoot ? ' rootrow' : ''), draggable: isRoot || locked ? null : 'true', dataset: isRoot ? { dir: '' } : { sel: id, dir: n.rel, parent: F.parent(n.rel) }, title: n.rel || 'Your study folder' },
      isRoot ? h('span', { class: 'chev ph' }) : chev(), h('span', { class: 'fic' }, icon('folder')), h('span', { class: 'name', text: isRoot ? 'Study folder' : n.name }),
      locked ? h('span', { class: 'lockd', title: 'Module folder: Moodle files are matched to it, so it stays where it is', 'aria-label': 'module folder' }, icon('lock')) : null,
      n.virtual && !isRoot ? h('span', { class: 'faint sm', text: 'will be created' }) : null,
      isRoot || n.rel === focusRel ? null : h('span', { class: 'acts inl focusbtn' }, h('button', { text: 'Focus', 'data-nosel': '', title: 'Show only this folder on this side', onclick: (e) => { e.stopPropagation(); setFocusDir(n.rel); } })),
      mark(inc, w === inc || isRoot ? 'direct solo' : 'direct', n.rel), isRoot || w === inc ? null : mark(w, 'total', firstDest(n) || n.rel),
      h('span', { class: 'fmeta', text: kids.length || isRoot || n.virtual ? '' : 'empty' }));
    elR.set(isRoot ? 'root' : id, row);
    return h('li', { class: 'dir' + (on ? ' open' : '') + (isRoot ? ' root' : '') + (n.virtual ? ' virtual' : ''), role: 'treeitem', 'aria-expanded': String(on), dataset: { open: n.rel, pane: 'r', dir: n.rel } }, row, h('ul', { role: 'group' }, kids));
  }
  function buildRight() {
    nodeR = new Map(); elR = new Map();
    let top = T.root;
    if (focusRel) { let n = T.root; for (const part of focusRel.split('/')) n = n && n.kids.find((k) => k.type === 'dir' && k.name === part); if (n) { top = n; openR.add(n.rel); } else focusRel = ''; }
    return h('ul', { class: 'tree', role: 'tree', 'aria-multiselectable': 'true', 'aria-label': focusRel ? 'Folder ' + focusRel : 'Your study folder' }, buildNode(top));
  }

  /* ---------- drawing */
  /* Expand / Collapse, by level. The first press of either goes all the way (expand stops at MAX_LEVELS); pressing the
     other one afterwards steps one level at a time, from where the tree is: Collapse, Expand, Expand opens level 1, then 2. */
  const MAX_LEVELS = 8, step = { l: { depth: 0, last: '' }, r: { depth: 0, last: '' } };
  function levelOf(li) { let n = 0; for (let p = li.parentElement.closest('li.dir'); p; p = p.parentElement.closest('li.dir')) if (!p.classList.contains('root')) n++; return n; }
  function stepTree(side, dir) {
    const body = side === 'l' ? lbody : rbody, st = step[side]; if (!body) return;
    const lis = $$('li.dir:not(.root)', body), deepest = Math.min(MAX_LEVELS, lis.reduce((m, li) => Math.max(m, levelOf(li) + 1), 0));
    if (dir > 0) st.depth = st.last === 'collapse' || st.last === 'expand-step' ? Math.min(deepest, st.depth + 1) : deepest;
    else st.depth = st.last === 'expand' || st.last === 'collapse-step' ? Math.max(0, Math.min(st.depth, deepest) - 1) : 0;
    st.last = dir > 0 ? (st.last === 'collapse' || st.last === 'expand-step' ? 'expand-step' : 'expand') : (st.last === 'expand' || st.last === 'collapse-step' ? 'collapse-step' : 'collapse');
    lis.forEach((li) => setOpen(li, levelOf(li) < st.depth));
  }
  function setFocusDir(rel) { focusRel = rel || ''; try { sessionStorage.setItem('ffocus', focusRel); } catch (e) {} if (rSel) rSel.clear(); draw(); }
  function setShow(k) { lFilter = k; try { sessionStorage.setItem('fshow', k); } catch (e) {} draw(); }
  function pane(cls, title, tools, body) { return h('section', { class: 'pane ' + cls }, h('header', {}, h('h2', { text: title }), tools), body); }
  function draw() {
    if (!host) return;
    const keepL = lSel ? lSel.ids() : [], keepR = rSel ? rSel.ids() : [], sl = lbody ? lbody.scrollTop : 0, sr = rbody ? rbody.scrollTop : 0;
    if (lSel) lSel.destroy(); if (rSel) rSel.destroy();
    const c = tally(A.files), todo = (c.new || 0) + (c.update || 0) + (c.ignored || 0) + (c.missing || 0);
    let nfiles = 0; (function w(n) { if (n.type === 'file') { if (n.status !== 'new' && n.status !== 'ignored') nfiles++; } else n.kids.forEach(w); })(T.root);
    setSub(`${plural(nfiles, 'file')} in your folders · ${c.new || 0} new on Moodle` + (c.update ? ` · ${c.update} updated` : '') + (c.missing ? ` · ${c.missing} deleted here` : ''));
    const newKeys = A.files.filter((f) => f.status === 'new').map((f) => f.key);
    setTools([
      h('select', { class: 'opt', id: 'arrowmode', 'aria-label': 'Arrows', title: 'Which arrows to draw. Files already downloaded never keep an arrow: theirs appears only while you select or point at them.', onchange: (e) => { arrowMode = e.target.value; try { localStorage.setItem('farrows', arrowMode); } catch (x) {} queueArrows(); } },
        [['all', 'Arrows: every new file'], ['picked', 'Arrows: selection only']].map(([k, l]) => h('option', { value: k, text: l, selected: arrowMode === k }))),
      h('button', { text: 'Ignore all new', disabled: !newKeys.length, title: 'Stop offering every new file (they stay listed under Ignored)', onclick: () => F.act.ignore(newKeys) }),
      h('button', { class: 'primary', id: 'dlall', text: `Download all new${newKeys.length ? ' (' + newKeys.length + ')' : ''}`, disabled: !newKeys.length, title: 'Every new file goes where its arrow points', onclick: () => F.act.download(newKeys) })]);
    const ltree = buildLeft(), rtree = buildRight();
    lbody = h('div', { class: 'pbody' }, ltree); rbody = h('div', { class: 'pbody' }, rtree);
    const count = (k) => (k === 'todo' ? todo : k === 'all' ? A.files.length : c[k] || 0);
    const show = h('select', { class: 'show', 'aria-label': 'Show Moodle files', title: 'Which Moodle files to list', onchange: (e) => setShow(e.target.value) },
      SHOW.map(([k, l]) => h('option', { value: k, text: `${l} (${count(k)})`, selected: lFilter === k })));
    const sbox = (val, on, label) => h('div', { class: 'search sm' }, icon('search'), h('input', { type: 'search', 'data-search': '', placeholder: 'Filter', value: val, 'aria-label': label, oninput: on }));
    const lsearch = sbox(lq, (e) => { lq = e.target.value.trim().toLowerCase(); redrawKeepFocus(e.target); }, 'Filter Moodle files');
    const rsearch = sbox(rq, (e) => { rq = e.target.value.trim().toLowerCase(); redrawKeepFocus(e.target); }, 'Filter your folders');
    const rtools = h('div', { class: 'ptools' },
      h('button', { class: 'ghost', text: 'Expand', title: 'Open everything (up to 8 levels); after Collapse, one level per press', onclick: () => stepTree('r', 1) }), h('button', { class: 'ghost', text: 'Collapse', title: 'Close everything; after Expand, one level per press', onclick: () => stepTree('r', -1) }), rsearch);
    svg = document.createElementNS(NS, 'svg'); svg.setAttribute('class', 'arrows'); svg.setAttribute('aria-hidden', 'true');
    const rtitle = focusRel ? F.base(focusRel) : 'Your folders';
    if (focusRel) rtools.prepend(h('button', { id: 'focusup', text: '↑ ' + (F.parent(focusRel) ? F.base(F.parent(focusRel)) : 'Study folder'), title: 'Focus on the folder above', onclick: () => setFocusDir(F.parent(focusRel)) }));
    if (focusRel) rtools.prepend(h('button', { id: 'unfocus', text: 'Show all folders', title: 'Leave ' + focusRel + ' and show the whole study folder again', onclick: () => setFocusDir('') }));
    clear(host).append(pane('left', focusRel ? 'Moodle · ' + F.base(focusRel) : 'Moodle', h('div', { class: 'ptools' }, show,
      h('button', { class: 'ghost', text: 'Expand', title: 'Open everything; after Collapse, one level per press', onclick: () => stepTree('l', 1) }), h('button', { class: 'ghost', text: 'Collapse', title: 'Close everything; after Expand, one level per press', onclick: () => stepTree('l', -1) }), lsearch), lbody), pane('right', rtitle, rtools, rbody), svg,
      h('p', { class: 'hint', text: 'An arrow shows where each new file will be saved: dashed is the suggestion, solid is a folder you chose. Download follows the arrow; drag a file onto another folder to send it there instead. Right-click for more. Press p for a quick look, / to filter.' }));
    lSel = Selectable(ltree, { onChange: (ids) => onSel('l', ids), onOpen: (id) => openL_(id), onContext: (e, ids) => F.menu(e, buildL(ids)), scroller: lbody, bandOnRows: false, keys: F.keymap(buildL) });
    const rkeys = F.keymap(buildR); rkeys.Backspace = rkeys.Delete;
    rSel = Selectable(rtree, { onChange: (ids) => onSel('r', ids), onOpen: (id) => openR_(id), onContext: (e, ids) => F.menu(e, buildR(ids)), scroller: rbody, bandOnRows: false, keys: rkeys });
    rbody.addEventListener('contextmenu', (e) => { if (e.target.closest('[data-sel]')) return; e.preventDefault(); F.menu(e, [{ label: 'New folder', on: true, fn: () => doNew(parentFor()) }, { label: 'New file here…', on: true, fn: () => doNewFile(parentFor()) }]); });
    lbody.addEventListener('scroll', queueArrows, { passive: true }); rbody.addEventListener('scroll', queueArrows, { passive: true });
    wireDrag();
    lSel.set(keepL.filter((i) => nodeL.has(i))); rSel.set(keepR.filter((i) => nodeR.has(i)));
    lbody.scrollTop = sl; rbody.scrollTop = sr;
    if (!keepL.length && !keepR.length) SelBar.hide();
    queueArrows();
  }
  function redrawKeepFocus(input) { const pos = input.selectionStart, pane_ = input.closest('.pane').classList.contains('left') ? 'left' : 'right'; draw(); const i = $('.pane.' + pane_ + ' [data-search]', host); if (i) { i.focus(); i.setSelectionRange(pos, pos); } }

  /* ---------- the arrows: from each file still on Moodle to the folder it will be saved in.
     A row hidden in a closed group starts at the group; a folder hidden in a closed folder ends at the nearest folder you can see.
     Ends that are scrolled out of sight stop at the edge of the pane. Drawn again whenever anything moves. */
  function queueArrows() { if (!raf) raf = requestAnimationFrame(drawArrows); }
  function seen(el) { while (el && el.offsetParent === null) { const li = el.closest('li'), up = li && li.parentElement.closest('li.dir'); el = up ? up.firstElementChild : null; } return el; }
  function targetRow(folder) { for (let rel = folder; ; rel = F.parent(rel)) { const el = elR.get(rel ? 'p:' + rel : 'root'); if (el && el.offsetParent !== null) return el; if (!rel) return null; } }
  const svgEl = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  function drawArrows() {
    raf = 0;
    if (!svg || !host || !lbody || !rbody || !A) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const hb = host.getBoundingClientRect(), lb = lbody.getBoundingClientRect(), rb = rbody.getBoundingClientRect();
    const wide = lb.right <= rb.left + 1;                     // side by side; stacked panes show the destination as text instead
    host.classList.toggle('stacked', !wide);
    if (!wide) return;
    svg.setAttribute('viewBox', `0 0 ${hb.width} ${hb.height}`); svg.setAttribute('width', hb.width); svg.setAttribute('height', hb.height);
    const picked = new Set(lSel ? leftFiles(lSel.ids()).map((f) => f.key) : []);
    const news = A.files.filter((f) => f.status === 'new' && elL.has(f.key));
    /* a file that is already here has no standing arrow; while you select it or point at it, one shows where it is */
    const here = A.files.filter((f) => (f.status === 'downloaded' || f.status === 'update') && f.rel && elL.has(f.key) && (picked.has(f.key) || (!!hover && !!hover.one && hover.keys.has(f.key))));
    const strong = (f) => picked.has(f.key) || flowing.has(f.key) || (!!hover && (hover.keys ? hover.keys.has(f.key) : under(f.folder, hover.dir)));
    const all = arrowMode === 'all' && news.length <= ARROW_MAX, lines = new Map();
    for (const f of [...news, ...here]) {
      const isHere = f.status !== 'new', on = isHere || strong(f); if (!on && !all) continue;
      const a = seen(elL.get(f.key)), t = targetRow(f.folder); if (!a || !t) continue;
      const id = (a.dataset.sel || '') + '\n' + (t.dataset.dir || '') + (isHere ? '\nh' : ''), old = lines.get(id);
      if (old) { old.on = old.on || on; old.flow = old.flow || flowing.has(f.key); old.mine = old.mine && f.chosen; old.n++; continue; }
      lines.set(id, { a, t, on, flow: flowing.has(f.key), mine: !!f.chosen, here: isHere, c: cIdx(f.folder), n: 1 });
    }
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    let drawn = 0;
    for (const ln of lines.values()) {
      const ar = ln.a.getBoundingClientRect(), ic = ln.t.querySelector('.selbox') || ln.t.querySelector('.fic') || ln.t, tr = ic.getBoundingClientRect();
      const ya = ar.top + ar.height / 2, yt = tr.top + tr.height / 2, outA = ya < lb.top + 2 || ya > lb.bottom - 2, outT = yt < rb.top + 2 || yt > rb.bottom - 2;
      if (outA && !ln.on) continue;                           // quiet arrows only for rows you can see
      const x1 = lb.right - hb.left + 1, y1 = clamp(ya, lb.top + 8, lb.bottom - 8) - hb.top;
      const x2 = Math.max(tr.left - 6, rb.left + 6) - hb.left, y2 = clamp(yt, rb.top + 8, rb.bottom - 8) - hb.top, k = Math.max(28, (x2 - x1) * 0.5);
      const g = svgEl('g', { class: (ln.here ? 'ar here' : `ar c${ln.c}`) + (ln.on ? ' on' : '') + (ln.flow ? ' flow' : '') + (ln.mine ? ' mine' : '') + (outT || outA ? ' off' : '') });
      g.append(svgEl('path', { class: 'ln', d: `M${x1} ${y1} C${x1 + k} ${y1} ${x2 - k} ${y2} ${x2 - 6} ${y2}` }), svgEl('circle', { class: 'tail', cx: x1, cy: y1, r: 2.5 }),
        outT ? svgEl('circle', { class: 'head', cx: x2 - 3, cy: y2, r: 3 }) : svgEl('path', { class: 'head', d: `M${x2 - 8} ${y2 - 4.5} L${x2} ${y2} L${x2 - 8} ${y2 + 4.5} Z` }));
      svg.append(g); drawn++;
    }
    host.dataset.arrows = drawn;
  }
  function setHover(hv) { const same = (!hv && !hover) || (hv && hover && hv.id === hover.id); if (same) return; hover = hv; queueArrows(); }
  function onHover(e) {
    const r = e.target.closest && e.target.closest('.frow'); if (!r || !host.contains(r)) return setHover(null);
    if (r.classList.contains('lf')) return setHover({ id: 'f:' + r.dataset.sel, one: true, keys: new Set([r.dataset.sel]) });
    if (r.classList.contains('lg')) { const g = nodeL.get(r.dataset.sel); return setHover(g ? { id: 'g:' + r.dataset.sel, keys: new Set(g.files.map((f) => f.key)) } : null); }
    if (r.classList.contains('dir') && r.dataset.dir !== undefined && r.dataset.dir !== '') return setHover({ id: 'd:' + r.dataset.dir, dir: r.dataset.dir });
    setHover(null);
  }
  /* files that have just been downloaded: open their folder and let the row glow once */
  function land(rels) {
    rels.slice(0, 12).forEach((rel) => openTo(rel));
    const rows = rels.map((rel) => elR.get('p:' + rel)).filter(Boolean); if (!rows.length) return;
    rows.forEach((r) => r.classList.add('arrive')); rows[0].scrollIntoView({ block: 'nearest' });
    setTimeout(() => rows.forEach((r) => r.classList.remove('arrive')), 1800);
  }
  function selectIncoming(dir, deep) {
    const ks = A.files.filter((f) => f.status === 'new' && (deep ? under(f.folder, dir) : f.folder === dir)).map((f) => f.key); if (!ks.length) return;
    if (ks.some((k) => !elL.has(k))) { lq = ''; lFilter = 'todo'; draw(); }
    ks.forEach((k) => { const row = elL.get(k); for (let li = row && row.closest('li.dir'); li; li = li.parentElement.closest('li.dir')) if (!li.classList.contains('open')) setOpen(li, true); });
    lSel.set(ks.filter((k) => elL.has(k))); const r0 = elL.get(ks[0]); if (r0) r0.scrollIntoView({ block: 'nearest' });
  }

  /* ---------- selection helpers */
  const leftFiles = (ids) => { const out = new Map(); ids.forEach((i) => { const n = nodeL.get(i); if (n && !n.group) out.set(n.key, n); }); return [...out.values()]; };
  const rightNodes = (ids) => ids.map((i) => nodeR.get(i)).filter(Boolean);
  const movable = (n) => n.status !== 'missing' && !(n.type === 'dir' && lock.has(n.rel));
  function parentFor() { const ns = rightNodes(rSel ? rSel.ids() : []); if (ns.length === 1) return ns[0].type === 'dir' ? ns[0].rel : F.parent(ns[0].rel); return ''; }
  function openL_(id) { const n = nodeL.get(id); if (!n) return; if (n.group) { const li = $(`li[data-open="${esc(id)}"]`, lbody); if (li) toggleLi(li); } else if ((n.status === 'downloaded' || n.status === 'update') && n.rel) reveal(n.rel); }
  function openR_(id) { const n = nodeR.get(id); if (!n) return; if (n.type === 'dir') { const li = elR.get(id).closest('li'); toggleLi(li); } else if (n.status !== 'missing') F.openFile(n.rel); }
  function showMoved(moved) {                      // open the folders the moved items are in, select them, scroll to the first
    const ids = moved.map((m) => 'p:' + m.to).filter((i) => nodeR.has(i)); if (!ids.length) return;
    moved.forEach((m) => openTo(m.to));
    rSel.set(ids); const el = elR.get(ids[0]); if (el) el.scrollIntoView({ block: 'nearest' });
  }
  function reveal(rel) { openTo(rel); rSel.set(['p:' + rel]); const el = elR.get('p:' + rel); if (el) el.scrollIntoView({ block: 'center' }); }

  let crossing = false;
  function onSel(side, ids) {
    if (crossing) return;                                   // the other pane is being cleared because this one now has the selection
    if (ids.length) { const other = side === 'l' ? rSel : lSel; if (other && other.size()) { crossing = true; other.clear(); crossing = false; } }
    hilite(side === 'l' ? leftFiles(ids) : []);
    if (!ids.length) { if (!(lSel && lSel.size()) && !(rSel && rSel.size())) SelBar.hide(); return; }
    if (side === 'l') F.bar(`${plural(leftFiles(ids).length, 'Moodle file')} selected`, buildL(ids), () => lSel.clear());
    else F.bar(`${plural(rightNodes(ids).length, 'item')} selected`, buildR(ids), () => rSel.clear());
    if (F.look.isOpen()) { clearTimeout(lookTimer); lookTimer = setTimeout(() => lookIds(side, ids), 120); }
  }
  const keys = (fs) => fs.map((f) => f.key);

  /* ---------- what you pick on the left lights up the folder it will land in (and its arrow) */
  function hilite(fs) {
    if (!rbody) return;
    $$('.hl', rbody).forEach((e) => e.classList.remove('hl'));
    let firstRow = null;
    fs.filter((f) => f.status === 'new').forEach((f) => {
      openTo(f.folder);
      const dr = elR.get(f.folder ? 'p:' + f.folder : 'root'); if (dr) { dr.classList.add('hl'); firstRow = firstRow || dr; }
    });
    if (firstRow) firstRow.scrollIntoView({ block: 'nearest' });
    queueArrows();
  }

  /* ---------- left actions */
  async function placeInto(fs) {
    const ks = keys(fs.filter((f) => ['new', 'ignored', 'missing'].includes(f.status)));
    const dir = await F.pickFolder({ title: `Download ${plural(ks.length, 'file')} to…`, text: 'They are saved in this folder. The choice is remembered for these files.', ok: 'Download here', start: parentFor() });
    if (dir !== null) F.act.place(ks, dir);
  }
  async function changeDest(fs) {
    const ks = keys(fs), dir = await F.pickFolder({ title: `Where should ${plural(ks.length, 'file')} go?`, text: 'Nothing is downloaded yet: the arrow will point to this folder.', ok: 'Use this folder', start: fs.length ? fs[0].folder : '' });
    if (dir !== null) F.act.retarget(ks, dir);
  }
  async function moveOnDisk(fs) {
    const rels = fs.map((f) => f.rel), dir = await F.pickFolder({ title: `Move ${plural(rels.length, 'file')} to…`, ok: 'Move here', start: F.parent(rels[0]) });
    if (dir !== null) { const r = await F.moveTo(rels, dir); if (r && r.moved.length) showMoved(r.moved); }
  }
  /* the bar, the right-click menu and the keys all come from this list */
  function buildL(ids) {
    const fs = leftFiles(ids), on = fs.filter((f) => (f.status === 'downloaded' || f.status === 'update') && f.rel);
    return [...F.moodleActions(fs, { place: placeInto, change: changeDest }),
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
  function buildR(ids) {
    const ns = rightNodes(ids), mv = ns.filter(movable), one = ns.length === 1 ? ns[0] : null, isFile = !!one && one.type === 'file' && one.status !== 'missing', isDir = !!one && one.type === 'dir';
    const rv = ns.filter((n) => n.type === 'file' && n.key && n.status !== 'missing'), notable = ns.filter((n) => n.status !== 'missing' && (n.type === 'dir' || /\.(pdf|txt|md)$/i.test(n.name)) && n.rel);
    return [
      { label: 'Open', cls: 'primary', hint: 'Enter', on: isFile, fn: () => F.openFile(one.rel) },
      { label: 'Open with default program', menuOnly: true, on: isFile, fn: safe(async () => { await api('/api/open', { rel: one.rel }); }) },
      { label: 'Preview', key: 'p', on: isFile, title: 'Quick look (p)', fn: () => lookIds('r', ids) },
      { label: isDir ? 'New folder inside' : 'New folder', key: 'n', sep: true, on: true, menuOnly: true, fn: () => doNew(parentFor()) },
      { label: 'New file here…', menuOnly: true, on: true, fn: () => doNewFile(parentFor()) },
      { label: 'Split by week', menuOnly: true, on: isDir, title: 'Move the files in this folder into one folder per week', fn: () => F.subdivide(one.rel) },
      { label: 'Create notes…', key: 'g', sep: true, menuOnly: true, on: !!notable.length, title: 'Opens the AI page with these files selected (g)', fn: () => go('ai', 'notes:' + notable.map((n) => n.rel).join('|')) },
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
    if (d.from === 'local') { const r = await F.moveTo(d.rels, rel); if (r && r.moved.length) showMoved(r.moved); return; }
    const place = d.files.filter((f) => ['new', 'ignored', 'missing'].includes(f.status)), there = d.files.filter((f) => (f.status === 'downloaded' || f.status === 'update') && f.rel);
    if (there.length) { const r = await F.moveTo(there.map((f) => f.rel), rel); if (r && r.moved.length) showMoved(r.moved); }
    if (place.length) F.act.place(keys(place), rel);
  }

  F.view = {
    async mount(el, o) {
      host = h('div', { class: 'org' }); el.append(host); setSub = o.sub; setTools = o.tools; first = true; A = null; hover = null; flowing = new Set();
      try { const k = sessionStorage.getItem('fshow'); if (SHOW.some((s) => s[0] === k)) lFilter = k; arrowMode = localStorage.getItem('farrows') === 'picked' ? 'picked' : 'all'; focusRel = sessionStorage.getItem('ffocus') || ''; } catch (e) {}
      host.addEventListener('mouseover', onHover);
      host.addEventListener('mouseleave', () => setHover(null));
      const l = (what) => { if ((what === 'job' || what === 'progress') && host && host.isConnected) load(); };      // also as files land, one by one
      App.listeners.push(l); F.reload = load;
      F.flow = (ks) => { ks.forEach((k) => flowing.add(k)); queueArrows(); return () => { ks.forEach((k) => flowing.delete(k)); queueArrows(); }; };   // a download has started: its arrows move until it ends
      addEventListener('resize', queueArrows);
      if (window.ResizeObserver) { ro = new ResizeObserver(queueArrows); ro.observe(host); }
      await load();
      return () => {
        clearTimeout(lookTimer); F.look.close(); App.listeners = App.listeners.filter((x) => x !== l); removeEventListener('dragend', endDrag, true); removeEventListener('resize', queueArrows);
        if (ro) { ro.disconnect(); ro = null; } if (raf) { cancelAnimationFrame(raf); raf = 0; }
        if (lSel) lSel.destroy(); if (rSel) rSel.destroy(); F.flow = null; lSel = rSel = lbody = rbody = host = svg = null; endDrag();
      };
    },
  };
})();
