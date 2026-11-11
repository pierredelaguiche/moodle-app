'use strict';
/* Study: pick a module and lay its notes, PDFs and other files out in four sections (two rows of two). Each section holds a
   stack of tabs; drag a file or a tab onto a section to put it there, drag the bars between sections to resize. Create files,
   and ask the assistant about what is on screen. Text editing keeps the safe save (conflict check, backups on the server); Markdown is shown
   typeset. The assistant receives only what its context list shows: the open files, files you add, and text you hand it. */
(function () {
  const F = App.Files, docs = App.S.docs, MAX_TABS = 8, SEC = ['Top left', 'Top right', 'Bottom left', 'Bottom right'];
  const studyApi = (path, body) => api(path, body, { 'X-Page': 'study' });          // the only LLM call of this page: /api/ai/chat
  let host = null, saving = false, focus = 0, mod = '', tree = null, fq = '', refs = [], dragRel = null;
  let secs = SEC.map(() => ({ tabs: [], on: 0 })), split = { row: 50, c0: 50, c1: 50 };      // the four sections; `on` = the tab that shows
  let chat = [], pins = [], useOpen = true, aiOpen = true, busy = false, aiTo = '';
  const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem('study.' + k); localStorage.setItem('study.' + k, v); } catch (e) {} return null; };

  /* ---------- small markdown renderer that builds DOM nodes (nothing from the file is ever parsed as HTML) */
  /* maths: $x$, $$x$$, GitHub's $`x`$, and a code span that is only $x$ (what models often write) are typeset with KaTeX */
  function math(tex, display) {
    const box = h(display ? 'div' : 'span', { class: display ? 'math-block' : 'math' });
    try { if (!window.katex) throw new Error('no katex'); window.katex.render(tex, box, { displayMode: display, throwOnError: false, trust: false, output: 'htmlAndMathml' }); }
    catch (e) { box.textContent = (display ? '$$' : '$') + tex + (display ? '$$' : '$'); box.classList.add('math-raw'); }
    return box;
  }
  const stripDollars = (t) => { t = t.trim(); return t.length > 1 && t[0] === '$' && t[t.length - 1] === '$' ? t.slice(1, -1).trim() : t; };
  function inline(text) {
    const out = []; const re = /(\$`[^`\n]+`\$)|(\$\$[^\n]+?\$\$)|(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\s][^*]*\*)|(\b_[^_\s][^_]*_\b)|(\[[^\]]+\]\(https?:\/\/[^)\s]+\))|((?<!\\)\$(?=\S)[^$\n]*?[^\s$\\]\$(?!\d))/g; let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index)); const t = m[0];
      if (m[1]) out.push(math(stripDollars(t.slice(2, -2)), false)); else if (m[2]) out.push(math(t.slice(2, -2).trim(), true));
      else if (m[3]) { const c = t.slice(1, -1), mm = /^\$([^$]+)\$$/.exec(c.trim()); out.push(mm ? math(mm[1].trim(), false) : h('code', { text: c })); }
      else if (m[4] || m[5]) out.push(h('strong', {}, inline(t.slice(2, -2)))); else if (m[6] || m[7]) out.push(h('em', {}, inline(t.slice(1, -1))));
      else if (m[8]) { const mm = /^\[([^\]]+)\]\((.+)\)$/.exec(t); out.push(h('a', { href: mm[2], target: '_blank', rel: 'noopener noreferrer', text: mm[1] })); } else out.push(math(t.slice(1, -1).trim(), false));
      last = m.index + t.length;
    }
    if (last < text.length) out.push(text.slice(last)); return out;
  }
  function markdown(src) {
    const root = h('div'), lines = src.replace(/\r\n/g, '\n').split('\n'); let i = 0;
    while (i < lines.length) {
      const ln = lines[i];
      if (/^```(math|latex|tex)\s*$/i.test(ln)) { const buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); i++; root.append(math(buf.join('\n'), true)); continue; }
      if (/^```/.test(ln)) { const buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); i++; root.append(h('pre', {}, h('code', { text: buf.join('\n') }))); continue; }
      if (/^\s*\$\$/.test(ln)) {
        const one = /^\s*\$\$(.+?)\$\$\s*$/.exec(ln);
        if (one) { root.append(math(one[1].trim(), true)); i++; continue; }
        const first = ln.replace(/^\s*\$\$/, ''), buf = [first]; let j = i + 1, closed = false;
        while (j < lines.length && j - i < 200) { const e = lines[j]; if (/\$\$\s*$/.test(e)) { buf.push(e.replace(/\$\$\s*$/, '')); closed = true; break; } buf.push(e); j++; }
        if (closed) { root.append(math(buf.join('\n').trim(), true)); i = j + 1; continue; }
      }
      let m;
      if ((m = /^(#{1,6})\s+(.*)$/.exec(ln))) { root.append(h('h' + m[1].length, {}, inline(m[2]))); i++; continue; }
      if (/^\s*([-*_])\1\1+\s*$/.test(ln)) { root.append(h('hr')); i++; continue; }
      if (/^>\s?/.test(ln)) { const buf = []; while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, '')); root.append(h('blockquote', {}, inline(buf.join(' ')))); continue; }
      if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()); const head = cells(ln); i += 2; const body = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body.push(cells(lines[i++]));
        root.append(h('table', {}, h('thead', {}, h('tr', {}, head.map((c) => h('th', {}, inline(c))))), h('tbody', {}, body.map((r) => h('tr', {}, r.map((c) => h('td', {}, inline(c)))))))); continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(ln)) {
        const ordered = /^\s*\d/.test(ln), list = h(ordered ? 'ol' : 'ul'); const stack = [{ ind: (/^\s*/.exec(ln)[0].length), el: list }];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          const mm = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i]), ind = mm[1].length; let txt = mm[3];
          while (stack.length > 1 && ind < stack[stack.length - 1].ind) stack.pop();
          if (ind > stack[stack.length - 1].ind) { const sub = h(/\d/.test(mm[2]) ? 'ol' : 'ul'); const parent = stack[stack.length - 1].el.lastElementChild; (parent || stack[stack.length - 1].el).append(sub); stack.push({ ind, el: sub }); }
          const task = /^\[( |x|X)\]\s+(.*)$/.exec(txt); const li = h('li', {}); if (task) { li.append(h('input', { type: 'checkbox', disabled: true, checked: task[1] !== ' ' }), ' '); txt = task[2]; } li.append(inline(txt)); stack[stack.length - 1].el.append(li); i++;
        }
        root.append(list); continue;
      }
      if (!ln.trim()) { i++; continue; }
      const buf = []; while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*\$\$|>|\s*([-*+]|\d+[.)])\s)/.test(lines[i])) buf.push(lines[i++]); root.append(h('p', {}, inline(buf.join(' '))));
    }
    return root;
  }

  /* ---------- documents */
  async function openDoc(rel) {
    if (docs.has(rel)) return docs.get(rel);
    const r = await api('/api/file?rel=' + encodeURIComponent(rel));
    const d = { rel: r.rel, name: r.name, kind: r.kind, text: r.text || '', orig: r.text || '', sha: r.sha, eol: r.eol || 'lf', lang: r.lang, dirty: false, editable: r.editable, note: r.note, mode: null, wrap: r.lang === 'markdown', savedAt: null, url: null, conflict: false };
    if (r.kind === 'pdf' || r.kind === 'image') { const resp = await fetch('/api/raw?rel=' + encodeURIComponent(rel), { headers: { 'X-Token': TOKEN } }); if (!resp.ok) throw new Error('Could not load the preview'); d.url = URL.createObjectURL(await resp.blob()); }
    docs.set(rel, d); return d;
  }
  /* ---------- the workspace: four sections, each a stack of tabs. A file is open in one section at a time; empty sections take no room. */
  const openRels = () => secs.flatMap((s) => s.tabs);
  const shownRel = (i) => secs[i].tabs[secs[i].on] || null;
  const shown = () => secs.map((s, i) => shownRel(i)).filter(Boolean);
  const used = () => secs.filter((s) => s.tabs.length).length;
  const cur = () => docs.get(shownRel(focus)) || null;
  const keep = () => { try { sessionStorage.setItem('study.secs', JSON.stringify({ secs, focus, split })); } catch (e) {} };
  const setUrl = () => history.replaceState(null, '', '#/edit' + (shownRel(focus) ? '/' + encodeURIComponent(shownRel(focus)) : ''));
  function dropDoc(rel) { const d = docs.get(rel); if (d && !openRels().includes(rel)) { if (d.url) URL.revokeObjectURL(d.url); docs.delete(rel); } }
  function tidy() {                               // `on` and `focus` always point at something that exists
    secs.forEach((s) => { s.tabs = s.tabs.filter((t) => docs.has(t)); s.on = Math.max(0, Math.min(s.on, s.tabs.length - 1)); });
    if (!secs[focus].tabs.length) { const i = secs.findIndex((s) => s.tabs.length); focus = i < 0 ? 0 : i; }
  }
  async function closeTab(si, rel, quiet) {
    const d = docs.get(rel), s = secs[si]; if (!s.tabs.includes(rel)) return false;
    if (d && d.dirty && !(await dialog({ title: 'Discard unsaved changes?', text: d.rel, ok: 'Discard', danger: true }))) return false;
    const at = s.tabs.indexOf(rel); if (at < 0) return false;
    s.tabs.splice(at, 1); if (s.on >= at && s.on > 0) s.on--;
    dropDoc(rel); if (!quiet) { tidy(); keep(); setUrl(); draw(); }
    return true;
  }
  /* open a file. Default: as a tab of the focused section (or just show it where it already is).
     `side`: in a section of its own while one is free. `at`: in that section, moving it there if it is open elsewhere. */
  async function show(rel, side, at) {
    let d; try { d = await openDoc(rel); } catch (e) { toast(e.message, { err: true }); return; }
    const where = secs.findIndex((s) => s.tabs.includes(d.rel));
    let si = at;
    if (si === undefined) {
      if (side && openRels().some((t) => t !== d.rel)) {
        if (where >= 0 && secs[where].tabs.length === 1) si = where;                       // already has a section to itself
        else { si = secs.findIndex((s) => !s.tabs.length); if (si < 0) { si = where >= 0 ? where : focus; toast('All four sections are in use: it is a tab here. Drag it to rearrange.', { ms: 3000 }); } }
      } else si = where >= 0 ? where : focus;
    }
    const s = secs[si];
    if (!s.tabs.includes(d.rel) && s.tabs.length >= MAX_TABS) { toast(`A section holds at most ${MAX_TABS} files: close one first`, { err: true }); dropDoc(d.rel); return; }
    if (where >= 0 && where !== si) { const o = secs[where], k = o.tabs.indexOf(d.rel); o.tabs.splice(k, 1); if (o.on >= k && o.on > 0) o.on--; }
    if (!s.tabs.includes(d.rel)) s.tabs.push(d.rel);
    s.on = s.tabs.indexOf(d.rel); focus = si;
    const m = modOf(d.rel); if (m && m !== mod) { mod = m; ls('mod', mod); }
    keep(); setUrl(); draw();
  }
  /* arranging: drag a file from the list, or a tab, onto one of the four sections; or use the menu on a tab */
  function startDrag(e, rel) { dragRel = rel; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', F.base(rel)); setTimeout(() => { if (host && dragRel) host.classList.add('arranging'); }, 0); }
  function endDrag() { dragRel = null; if (host) { host.classList.remove('arranging'); $$('.st-drop .zone.over', host).forEach((z) => z.classList.remove('over')); } }
  function dropZones() {
    return h('div', { class: 'st-drop', 'aria-hidden': 'true' }, SEC.map((name, i) => {
      const z = h('div', { class: 'zone', dataset: { zone: i } }, h('b', { text: name }), h('span', { text: secs[i].tabs.length ? `${secs[i].tabs.length} open here` : 'empty' }));
      z.addEventListener('dragover', (e) => { if (!dragRel) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; z.classList.add('over'); });
      z.addEventListener('dragleave', () => z.classList.remove('over'));
      z.addEventListener('drop', (e) => { e.preventDefault(); const rel = dragRel; endDrag(); if (rel) show(rel, false, i); });
      return z;
    }));
  }
  function arrangeMenu(e, rel, si) {              // the same moves without dragging (keyboard, touch)
    e.preventDefault(); e.stopPropagation();
    const here = si !== undefined && si >= 0, s = here ? secs[si] : null, r = e.currentTarget.getBoundingClientRect(), x = e.clientX || r.left, y = e.clientY || r.bottom;
    ctxMenu(x, y, [...SEC.map((name, i) => ({ label: (here ? 'Move to ' : 'Open in ') + name.toLowerCase(), disabled: here && i === si, fn: () => show(rel, false, i) })),
      ...(here ? ['sep', { label: 'Close', fn: () => closeTab(si, rel) },
        { label: 'Close the others here', disabled: s.tabs.length < 2, fn: async () => { for (const t of s.tabs.filter((t) => t !== rel)) if (!(await closeTab(si, t, true))) break; tidy(); keep(); setUrl(); draw(); } }] : [])]);
  }
  function splitter(kind, ri) {                   // the bar between two sections (v) or between the two rows (h)
    const key = kind === 'v' ? 'c' + ri : 'row';
    const el = h('div', { class: 'st-split ' + kind, role: 'separator', 'aria-orientation': kind === 'v' ? 'vertical' : 'horizontal', title: 'Drag to resize. Double-click: equal halves.' });
    el.addEventListener('dblclick', () => { split[key] = 50; keep(); draw(); });
    el.addEventListener('pointerdown', (e) => {
      e.preventDefault(); el.setPointerCapture(e.pointerId); host.classList.add('resizing');
      const box = (kind === 'v' ? el.parentElement : $('.st-tiles', host)).getBoundingClientRect(), a = el.previousElementSibling, b = el.nextElementSibling;
      const move = (ev) => { const part = kind === 'v' ? (ev.clientX - box.left) / box.width : (ev.clientY - box.top) / box.height, v = Math.round(Math.max(18, Math.min(82, part * 100))); split[key] = v; a.style.flexGrow = v; b.style.flexGrow = 100 - v; };
      const up = () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up); if (host) host.classList.remove('resizing'); keep(); };
      el.addEventListener('pointermove', move); el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
    });
    return el;
  }
  const save = safe(async (d, force) => {
    if (saving || !d || !d.editable) return; saving = true;
    try {
      const r = await api('/api/file/save', { rel: d.rel, text: d.text, sha: d.sha, eol: d.eol, force: !!force });
      d.sha = r.sha; d.orig = d.text; d.dirty = false; d.conflict = false; d.savedAt = new Date(); toast(r.changed ? 'Saved' : 'No changes to save', { ms: 1500 });
    } catch (e) {
      if (e.status === 409) { saving = false; const ok = await dialog({ title: 'This file changed on disk', text: 'Another program (or another tab) saved it after you opened it. Overwrite keeps your version and stores the other one in the backups folder. Reload discards your edits.', ok: 'Overwrite', cancel: 'Keep editing' }); if (ok) { await save(d, true); } return; }
      throw e;
    } finally { saving = false; }
    chrome();
  });
  const revert = (d) => { d.text = d.orig; d.dirty = false; draw(); };
  async function reload(d) { const r = await api('/api/file?rel=' + encodeURIComponent(d.rel)); d.text = d.orig = r.text || ''; d.sha = r.sha; d.eol = r.eol; d.dirty = false; d.conflict = false; draw(); }
  async function checkDisk() {
    if (saving) return; let redraw = false;
    for (const rel of openRels()) {
      const d = docs.get(rel); if (!d || d.kind !== 'text') continue;
      try { const r = await api('/api/file?rel=' + encodeURIComponent(d.rel)); if (r.sha && r.sha !== d.sha) { if (!d.dirty) { d.text = d.orig = r.text; d.sha = r.sha; d.eol = r.eol; redraw = true; } else d.conflict = true; } } catch (e) { /* file gone or moved; ignore */ }
    }
    if (redraw) { toast('Reloaded: a file changed on disk', { ms: 2500 }); draw(); } else chrome();
  }

  /* ---------- the module and its files (left) */
  const YEAR = /^(y|yr|year)[\s_-]*0*\d{1,2}$/i;                   // a year folder holds module folders (the server's domain/years.py)
  const modules = () => (tree ? tree.root.kids.filter((k) => k.type === 'dir' && !k.virtual).flatMap((k) => (YEAR.test(k.name) ? k.kids.filter((x) => x.type === 'dir' && !x.virtual).map((x) => x.rel) : [k.name])) : []);
  const modOf = (rel) => modules().filter((m) => rel.startsWith(m + '/')).sort((a, b) => b.length - a.length)[0] || '';
  const inMod = (rel) => { const m = modOf(rel); return m ? rel.slice(m.length + 1) : rel; };
  const dirIn = (rel) => inMod(rel).split('/').slice(0, -1).join('/');
  function moduleFiles() {
    let m = tree ? tree.root : null; for (const part of (mod ? mod.split('/') : ['\u0000'])) m = m && m.kids.find((k) => k.type === 'dir' && k.name === part); const out = [];
    (function w(n) { if (n.type === 'file') { if (!['new', 'ignored', 'missing', 'archived'].includes(n.status) && F.inEditor(n.name)) out.push(n); } else n.kids.forEach(w); })(m || { type: 'dir', kids: [] });
    return out;
  }
  const folderOf = (rel) => dirIn(rel) || 'Module folder';
  function drawSide() {
    const side = $('.st-side', host); if (!side) return;
    const files = moduleFiles().filter((f) => !fq || f.rel.toLowerCase().includes(fq)), groups = new Map();
    files.forEach((f) => { const g = folderOf(f.rel); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(f); });
    const list = h('div', { class: 'st-files', role: 'list', 'aria-label': 'Files of ' + mod });
    [...groups.keys()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).forEach((g) => {
      list.append(h('div', { class: 'st-folder', text: g }), groups.get(g).map((f) => h('div', { class: 'st-file' + (openRels().includes(f.rel) ? ' open' : ''), role: 'listitem', draggable: 'true', title: f.rel + '\nClick: open as a tab · Alt-click: in a section of its own · Drag: onto the section you want', dataset: { rel: f.rel },
        onclick: (e) => show(f.rel, e.altKey || e.metaKey || e.ctrlKey), ondragstart: (e) => startDrag(e, f.rel), oncontextmenu: (e) => arrangeMenu(e, f.rel) },
        h('span', { class: 'fic s-' + (F.ext(f.name) === 'pdf' ? 'update' : f.status === 'generated' ? 'new' : 'local') }, icon('file')), h('span', { class: 'name', text: f.name }),
        h('button', { class: 'ghost side', title: 'Open in a section of its own', 'aria-label': 'Open ' + f.name + ' beside', text: '⧉', onclick: (e) => { e.stopPropagation(); show(f.rel, true); } }))));
    });
    if (!files.length) list.append(h('div', { class: 'muted st-none', text: !modules().length ? 'No module folder yet. Set the study folder under Settings.' : fq ? 'Nothing matches.' : 'No notes, PDFs or text files in this module yet.' }));
    const pick = h('select', { 'aria-label': 'Module', onchange: (e) => { mod = e.target.value; ls('mod', mod); fq = ''; drawSide(); } }, modules().map((m) => h('option', { value: m, text: m, selected: m === mod })));
    const search = h('input', { type: 'search', placeholder: 'Filter files', 'aria-label': 'Filter files', value: fq, oninput: (e) => { fq = e.target.value.trim().toLowerCase(); const pos = e.target.selectionStart; drawSide(); const i = $('.st-side input[type=search]', host); if (i) { i.focus(); i.setSelectionRange(pos, pos); } } });
    clear(side).append(h('div', { class: 'st-pick' }, pick, h('button', { class: 'primary', id: 'newfile', text: 'New file', title: 'Create a note or another text file in this module', disabled: !mod, onclick: () => newFile() })), search, list);
  }

  /* ---------- tiles (middle) */
  const modeOf = (d) => (d.lang !== 'markdown' ? 'edit' : d.mode || (used() === 1 ? 'both' : d.text.trim() ? 'preview' : 'edit'));
  function chrome() {                           // refresh what can change without rebuilding the tiles: dirty marks, buttons, the status line
    if (!host) return;
    refs.forEach((r, i) => {
      const d = docs.get(shownRel(i)); if (!d || !r) return;
      r.el.classList.toggle('focus', i === focus); r.dots.forEach((dot, rel) => { const x = docs.get(rel); dot.hidden = !(x && x.dirty); });
      if (r.save) r.save.disabled = !d.dirty; if (r.rev) r.rev.hidden = !d.dirty; r.warn.hidden = !d.conflict; r.rel.hidden = !d.conflict;
    });
    status(); ctxLine();
  }
  function status() {
    const el = host && $('.edbar', host); if (!el) return; const d = cur(); clear(el); if (!d) return;
    if (d.kind !== 'text') { el.append(h('span', { text: d.kind === 'pdf' ? 'PDF' : d.kind === 'image' ? 'Image' : 'Read only' }), h('span', { text: d.rel })); return; }
    const ta = refs[focus] && refs[focus].ta; let ln = 1, col = 1; if (ta) { const p = ta.selectionStart, before = d.text.slice(0, p); ln = before.split('\n').length; col = p - before.lastIndexOf('\n'); }
    const words = (d.text.match(/\S+/g) || []).length;
    el.append(h('span', { text: d.rel.split('/').join(' › ') }), ta ? h('span', { text: `Ln ${ln}, Col ${col}` }) : null, h('span', { text: `${words} words` }), h('span', { class: 'sp' }), h('span', { text: d.dirty ? 'Unsaved changes' : d.savedAt ? 'Saved ' + d.savedAt.toLocaleTimeString() : 'Up to date' }));
  }
  function setFocus(i) { if (i === focus || !secs[i].tabs.length) return; focus = i; keep(); setUrl(); chrome(); }
  function section(i) {                           // one of the four: its tabs, the tools of the tab that shows, and that file
    const s = secs[i], rel = s.tabs[s.on], d = docs.get(rel), r = { ta: null, prev: null, dots: new Map() }; refs[i] = r;
    const mode = modeOf(d), body = h('div', { class: 'edbody' + (mode === 'both' ? ' split' : '') });
    r.warn = h('span', { class: 'stt s-update', text: 'Changed on disk', hidden: !d.conflict }); r.rel = h('button', { text: 'Reload', hidden: !d.conflict, onclick: () => reload(d) });
    r.rev = d.editable ? h('button', { class: 'ghost', text: 'Revert', hidden: !d.dirty, title: 'Back to the saved text', onclick: () => revert(d) }) : null;
    r.save = d.editable ? h('button', { class: 'primary', text: 'Save', title: 'Ctrl/Cmd S', disabled: !d.dirty, onclick: () => save(d) }) : null;
    const modes = d.kind === 'text' && d.lang === 'markdown' ? h('div', { class: 'seg sm', role: 'group', 'aria-label': 'View of ' + d.name }, [['preview', 'Read'], ['edit', 'Edit'], ['both', 'Both']].map(([k, l]) => h('button', { text: l, 'aria-pressed': String(mode === k), onclick: () => { d.mode = k; focus = i; draw(); } }))) : null;
    const tabs = h('div', { class: 'sec-tabs', role: 'tablist', 'aria-label': SEC[i] }, s.tabs.map((t) => {
      const td = docs.get(t), dot = h('span', { class: 'dot', title: 'Unsaved', hidden: !td.dirty }); r.dots.set(t, dot);
      return h('div', { class: 'sec-tab' + (t === rel ? ' on' : ''), role: 'tab', 'aria-selected': String(t === rel), draggable: 'true', title: td.rel + '\nDrag onto another section to move it · right-click for more', dataset: { rel: t },
        onclick: () => { if (t !== rel || focus !== i) { s.on = s.tabs.indexOf(t); focus = i; keep(); setUrl(); draw(); } }, ondragstart: (e) => startDrag(e, t), oncontextmenu: (e) => arrangeMenu(e, t, i) },
        dot, h('span', { class: 'name', text: td.name }), h('button', { class: 'ghost x', 'aria-label': 'Close ' + td.name, title: 'Close', text: '×', onclick: (e) => { e.stopPropagation(); closeTab(i, t); } }));
    }));
    r.tab = h('header', { class: 'edtab', title: SEC[i] }, tabs, h('div', { class: 'sec-tools' }, r.warn, r.rel, modes, r.rev, r.save,
      d.url ? h('button', { class: 'ghost', text: 'New tab', title: 'Open in a browser tab', onclick: () => window.open(d.url, '_blank', 'noopener') }) : null,
      h('button', { class: 'ghost icon arr', 'aria-label': 'Arrange ' + d.name, title: 'Move to another section', onclick: (e) => arrangeMenu(e, rel, i) }, icon('more'))));
    if (d.kind === 'pdf') body.append(h('iframe', { src: d.url, title: d.name }));
    else if (d.kind === 'image') body.append(h('img', { src: d.url, alt: d.name }));
    else if (d.kind !== 'text') body.append(h('div', { class: 'empty', style: { margin: 'auto' } }, h('b', { text: 'This file cannot be shown here' }), d.note || (d.kind === 'toolarge' ? 'It is larger than 2 MB.' : 'Only text files, PDFs and images open here.')));
    else {
      if (mode !== 'preview') {
        const ta = r.ta = h('textarea', { spellcheck: 'false', 'aria-label': 'Editing ' + d.name, wrap: d.wrap ? 'soft' : 'off', style: { 'white-space': d.wrap ? 'pre-wrap' : 'pre' } }); ta.value = d.text;
        ta.addEventListener('input', () => { d.text = ta.value; const was = d.dirty; d.dirty = d.text !== d.orig; if (was !== d.dirty) chrome(); else status(); if (r.prev) renderPrev(d, r); });
        ta.addEventListener('keyup', status); ta.addEventListener('click', status); ta.addEventListener('keydown', (e) => onKey(e, d, ta));
        ta.addEventListener('blur', () => { d.pos = ta.selectionStart; d.end = ta.selectionEnd; d.scroll = ta.scrollTop; });
        body.append(ta);
        if (d.pos) { ta.selectionStart = ta.selectionEnd = d.pos; ta.scrollTop = d.scroll || 0; }
      }
      if (mode !== 'edit') {
        r.prev = h('div', { class: 'md', 'aria-label': 'Preview of ' + d.name }); body.append(r.prev); clear(r.prev).append(markdown(d.text));
        if (r.ta) { let lock = false; r.ta.addEventListener('scroll', () => { if (lock) return; lock = true; r.prev.scrollTop = (r.ta.scrollTop / Math.max(1, r.ta.scrollHeight - r.ta.clientHeight)) * (r.prev.scrollHeight - r.prev.clientHeight); requestAnimationFrame(() => lock = false); }); }
      }
    }
    r.el = h('article', { class: 'tile' + (i === focus ? ' focus' : ''), dataset: { i }, 'aria-label': SEC[i] + ': ' + d.name }, r.tab, body);
    r.el.addEventListener('focusin', () => setFocus(i)); r.el.addEventListener('mousedown', () => setFocus(i));
    return r.el;
  }
  function draw() {
    if (!host) return; refs = []; tidy();
    const grid = clear($('.st-tiles', host)), n = used(); grid.dataset.n = n;
    if (!n) grid.append(h('div', { class: 'empty', style: { margin: 'auto' } }, h('b', { text: 'Nothing open' }), 'Pick a file on the left. There are four sections: drag a file onto the one you want, or Alt-click (⧉) to give it a section of its own. ', h('button', { text: 'Create a new file', onclick: () => newFile() })));
    else {
      const rows = [[0, 1], [2, 3]].map((ids) => ids.filter((i) => secs[i].tabs.length));
      const els = rows.map((ids, ri) => {
        if (!ids.length) return null;
        const row = h('div', { class: 'st-row', style: { 'flex-grow': rows[0].length && rows[1].length ? (ri ? 100 - split.row : split.row) : 1 } });
        ids.forEach((i, k) => { if (k) row.append(splitter('v', ri)); const el = section(i); el.style.flexGrow = ids.length === 2 ? (k ? 100 - split['c' + ri] : split['c' + ri]) : 1; row.append(el); });
        return row;
      });
      if (els[0]) grid.append(els[0]); if (els[0] && els[1]) grid.append(splitter('h')); if (els[1]) grid.append(els[1]);
    }
    grid.append(dropZones());
    drawSide(); chrome();
    const ta = refs[focus] && refs[focus].ta; if (ta && !document.querySelector('dialog[open]') && !(document.activeElement && document.activeElement.closest('.st-ai, .st-side'))) ta.focus({ preventScroll: true });
  }
  const pts = new WeakMap(); function renderPrev(d, r) { clearTimeout(pts.get(r)); pts.set(r, setTimeout(() => { if (r.prev) clear(r.prev).append(markdown(d.text)); }, 120)); }
  function onKey(e, d, ta) {
    const k = e.key; if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 's') { e.preventDefault(); save(d); return; }
    d.pos = ta.selectionStart; d.scroll = ta.scrollTop;
    const edit = (start, end, text, selS, selE) => { ta.focus(); ta.setSelectionRange(start, end); if (!document.execCommand || !document.execCommand('insertText', false, text)) { ta.setRangeText(text, start, end, 'end'); ta.dispatchEvent(new Event('input')); } if (selS !== undefined) ta.setSelectionRange(selS, selE); };
    if (k === 'Tab') {
      e.preventDefault(); const s = ta.selectionStart, en = ta.selectionEnd, v = ta.value;
      if (s === en && !e.shiftKey) return edit(s, en, '    ');
      const ls_ = v.lastIndexOf('\n', s - 1) + 1, block = v.slice(ls_, en), lines = block.split('\n');
      const nb = lines.map((l) => e.shiftKey ? l.replace(/^( {1,4}|\t)/, '') : '    ' + l).join('\n'); edit(ls_, en, nb, ls_, ls_ + nb.length);
    } else if (k === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.isComposing) {
      const s = ta.selectionStart, v = ta.value, ls_ = v.lastIndexOf('\n', s - 1) + 1, line = v.slice(ls_, s), ind = /^\s*/.exec(line)[0]; let extra = '';
      const li = /^(\s*)([-*+]|\d+\.)\s(\[[ x]\]\s)?/.exec(line);
      if (d.lang === 'markdown' && li) { if (line.trim() === li[0].trim()) { e.preventDefault(); return edit(ls_, s, '', ls_, ls_); } e.preventDefault(); const mark = /\d/.test(li[2]) ? (parseInt(li[2], 10) + 1) + '.' : li[2]; return edit(s, ta.selectionEnd, '\n' + li[1] + mark + ' ' + (li[3] ? '[ ] ' : '')); }
      if (/[:{(\[]\s*$/.test(line)) extra = '    '; if (ind || extra) { e.preventDefault(); edit(s, ta.selectionEnd, '\n' + ind + extra); }
    }
  }

  /* ---------- file creator */
  const KINDS = [['md', 'Note (Markdown)', (t) => `# ${t}\n\n`], ['txt', 'Plain text', () => ''], ['py', 'Python', (t) => `"""${t}"""\n\n\ndef main():\n    pass\n\n\nif __name__ == "__main__":\n    main()\n`], ['tex', 'LaTeX', (t) => `\\documentclass{article}\n\\title{${t}}\n\\begin{document}\n\\maketitle\n\n\\end{document}\n`]];
  function newFile(preset) {
    return new Promise((resolve) => {
      if (!mod) { toast('Pick a module first', { err: true }); return resolve(null); }
      const dirs = new Set(['Notes']); moduleFiles().forEach((f) => { const g = dirIn(f.rel); if (g) dirs.add(g); });
      const c = cur(), here = c && c.rel.startsWith(mod + '/') ? dirIn(c.rel) : '';
      const name = h('input', { type: 'text', id: 'nf-name', value: (preset && preset.name) || '', placeholder: 'Week 3 summary' });
      const kind = h('select', { id: 'nf-kind' }, KINDS.map(([k, l]) => h('option', { value: k, text: `${l} (.${k})` })));
      const dir = h('select', { id: 'nf-dir' }, [...dirs].sort().map((x) => h('option', { value: x, text: x, selected: x === (here || 'Notes') })), h('option', { value: '', text: 'Module folder' }));
      const dlg = h('dialog', { class: 'creator' }, h('h3', { text: preset ? 'Save as a new note' : 'New file' }), h('p', { text: 'In ' + mod + '. The folder is created if it does not exist yet.' }),
        h('label', { for: 'nf-name', text: 'Name' }), name, h('label', { for: 'nf-kind', text: 'Type' }), kind, h('label', { for: 'nf-dir', text: 'Folder' }), dir,
        h('div', { class: 'btns' }, h('button', { text: 'Cancel', onclick: () => dlg.close('no') }), h('button', { class: 'primary', text: 'Create', onclick: () => dlg.close('yes') })));
      dlg.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); dlg.close('yes'); } });
      dlg.addEventListener('close', safe(async () => {
        const ok = dlg.returnValue === 'yes', title = name.value.trim().replace(/\.(md|txt|py|tex)$/i, ''); dlg.remove();
        if (!ok) return resolve(null); if (!title) { toast('Give the file a name', { err: true }); return resolve(null); }
        const k = KINDS.find((x) => x[0] === kind.value), rel = [mod, dir.value, title + '.' + k[0]].filter(Boolean).join('/');
        const r = await api('/api/file/new', { rel, text: preset && preset.text !== undefined ? preset.text : k[2](title) });
        tree = await api('/api/tree'); App.S.tree = tree; await show(r.rel, !!preset); resolve(r.rel);
      }));
      document.body.append(dlg); dlg.showModal(); name.focus();
    });
  }

  /* ---------- the assistant (right). It is sent exactly what the context list shows, and only when you press Send. */
  const openCtx = () => (useOpen ? shown().map((t) => docs.get(t)).filter((d) => d && (d.kind === 'text' || d.kind === 'pdf')) : []);      // what you can see, not every tab
  function ctxLine() {
    const box = host && $('.st-ctx', host); if (!box) return;
    const open = openCtx();
    clear(box).append(
      h('label', { class: 'ctx-open', title: 'Send the text of the files you can see in the sections (as it is now, saved or not) with each message. Tabs behind them are not sent.' }, h('input', { type: 'checkbox', checked: useOpen, onchange: (e) => { useOpen = e.target.checked; ctxLine(); } }), h('span', { text: `On screen (${shown().length})` })),
      open.map((d) => h('span', { class: 'chip ctx auto', title: d.rel, text: d.name })),
      pins.map((p, i) => h('span', { class: 'chip ctx', title: p.rel || p.text.slice(0, 400) }, h('span', { text: p.label }), h('button', { class: 'ghost', 'aria-label': 'Remove ' + p.label, text: '×', onclick: () => { pins.splice(i, 1); ctxLine(); } }))),
      !open.length && !pins.length ? h('span', { class: 'faint', text: 'No context: the assistant only sees your message.' }) : null);
  }
  function addSelection() {
    const r = refs[focus], d = cur(); let text = '';
    if (r && r.ta && r.ta.selectionEnd > r.ta.selectionStart) text = r.ta.value.slice(r.ta.selectionStart, r.ta.selectionEnd);
    else if (d && d.kind === 'text' && d.end > d.pos) text = d.text.slice(d.pos, d.end);
    else text = String(window.getSelection ? window.getSelection() : '').trim();
    if (!text.trim()) return toast('Select some text in a section first', { err: true });
    pins.push({ label: `Selection${d ? ' from ' + d.name : ''} (${text.trim().split(/\s+/).length} words)`, text }); ctxLine();
  }
  async function addText() {
    const f = await dialog({ title: 'Give the assistant some text', text: 'An exercise, a paragraph from a book, an error message… It is sent with your next messages until you remove it.', fields: [{ name: 'text', label: 'Text', type: 'textarea' }], ok: 'Add' });
    if (f && f.text.trim()) { pins.push({ label: `Pasted text (${f.text.trim().split(/\s+/).length} words)`, text: f.text }); ctxLine(); }
  }
  function addFile() {
    const files = moduleFiles().filter((f) => (F.TEXT.has(F.ext(f.name)) || F.ext(f.name) === 'pdf') && !pins.some((p) => p.rel === f.rel)); if (!files.length) return toast('No more files to add in this module', { err: true });
    const pick = h('select', { id: 'cf-file', size: Math.min(10, Math.max(4, files.length)) }, files.map((f) => h('option', { value: f.rel, text: inMod(f.rel).split('/').join(' / ') })));
    const dlg = h('dialog', {}, h('h3', { text: 'Add a file to the context' }), h('p', { text: 'Its text is sent with your messages. PDFs are read once and the text is reused.' }), h('label', { for: 'cf-file', text: 'File in ' + mod }), pick,
      h('div', { class: 'btns' }, h('button', { text: 'Cancel', onclick: () => dlg.close('no') }), h('button', { class: 'primary', text: 'Add', onclick: () => dlg.close('yes') })));
    pick.addEventListener('dblclick', () => dlg.close('yes'));
    dlg.addEventListener('close', () => { const rel = dlg.returnValue === 'yes' ? pick.value : ''; dlg.remove(); if (rel) { pins.push({ label: F.base(rel), rel }); ctxLine(); } });
    document.body.append(dlg); dlg.showModal(); pick.focus();
  }
  function insert(text, replace) {
    const d = cur(); if (!d || d.kind !== 'text' || !d.editable) return toast('Click into a text tile first', { err: true });
    if (replace) d.text = text; else { const at = typeof d.pos === 'number' ? Math.min(d.pos, d.text.length) : d.text.length; d.text = d.text.slice(0, at) + (at && d.text[at - 1] !== '\n' ? '\n' : '') + text + '\n' + d.text.slice(at); }
    d.dirty = d.text !== d.orig; if (d.lang === 'markdown' && modeOf(d) === 'preview') d.mode = 'both'; draw();
    toast(replace ? `Replaced the text of ${d.name}. Not saved yet: Revert undoes it.` : `Inserted into ${d.name}. Not saved yet.`, { ms: 4500 });
  }
  function drawThread() {
    const box = host && $('.st-thread', host); if (!box) return;
    clear(box);
    if (!chat.length) box.append(h('div', { class: 'muted st-hello' }, h('b', { text: 'Ask about what is open' }), 'Tidy or extend your notes, get an explanation, hints for an exercise, or a quick quiz. Answers can be inserted into the file you are editing or saved as a new note.'));
    chat.forEach((m) => {
      if (m.role === 'user') return box.append(h('div', { class: 'msg me' }, h('div', { class: 'mtext', text: m.text }), m.ctx && m.ctx.length ? h('div', { class: 'mctx', text: 'with ' + m.ctx.join(', ') }) : null));
      const d = cur(), can = !!(d && d.kind === 'text' && d.editable), body = h('div', { class: 'md' }, markdown(m.text));
      box.append(h('div', { class: 'msg ai' + (m.err ? ' err' : '') }, body, m.err ? null : h('div', { class: 'macts' },
        h('button', { class: 'ghost', text: 'Copy', onclick: async () => { try { await navigator.clipboard.writeText(m.text); toast('Copied', { ms: 1500 }); } catch (e) { toast('Could not copy', { err: true }); } } }),
        h('button', { class: 'ghost', text: 'Insert', disabled: !can, title: can ? 'Insert at the cursor of ' + d.name : 'Click into a text tile first', onclick: () => insert(m.text, false) }),
        h('button', { class: 'ghost', text: 'Replace file', disabled: !can, title: can ? 'Replace the whole text of ' + d.name + ' (you can Revert before saving)' : 'Click into a text tile first', onclick: async () => { if (await dialog({ title: `Replace the text of ${d.name}?`, text: 'Nothing is saved until you press Save, and Revert brings the old text back.', ok: 'Replace' })) insert(m.text, true); } }),
        h('button', { class: 'ghost', text: 'Save as note', title: 'Create a new note with this answer', onclick: () => newFile({ text: m.text.endsWith('\n') ? m.text : m.text + '\n' }) }),
        m.info ? h('span', { class: 'faint minfo', text: m.info }) : null)));
    });
    if (busy) box.append(h('div', { class: 'msg ai wait' }, h('span', { class: 'spin' }), ' Thinking…'));
    box.scrollTop = box.scrollHeight;
  }
  const send = async (text) => {
    text = (text || '').trim(); if (!text || busy) return;
    const open = openCtx(), snippets = [], rels = [];
    open.forEach((d) => { if (d.kind === 'text') snippets.push({ label: 'FILE: ' + d.name + (d.dirty ? ' (with unsaved edits)' : ''), text: d.text }); else rels.push(d.rel); });
    pins.forEach((p) => { if (p.rel) { if (!open.some((d) => d.rel === p.rel)) rels.push(p.rel); } else snippets.push({ label: p.label, text: p.text }); });
    const messages = [...chat.filter((m) => !m.err).map((m) => ({ role: m.role, text: m.text })), { role: 'user', text }];
    chat.push({ role: 'user', text, ctx: [...open.map((d) => d.name), ...pins.map((p) => p.label)] }); busy = true; drawThread();
    const box = $('.st-ask textarea', host); if (box) box.value = '';
    try {
      const r = await studyApi('/api/ai/chat', { messages, rels, snippets });
      aiTo = r.to || aiTo; chat.push({ role: 'assistant', text: r.answer, info: `${r.in_tokens.toLocaleString()} in · ${r.out_tokens.toLocaleString()} out` + (r.truncated ? ' · context was cut to fit' : '') });
    } catch (e) { chat.push({ role: 'assistant', err: true, text: e.message + (/key|login|claude|provider|model/i.test(e.message) ? '\n\nConnect a model under Settings › Connections.' : '') }); }
    busy = false; drawThread(); const to = $('.st-to', host); if (to && aiTo) to.textContent = 'Sends to ' + aiTo;
  };
  function drawAi() {
    const el = host && $('.st-ai', host); if (!el) return;
    host.classList.toggle('noai', !aiOpen); $('#aitoggle', host).setAttribute('aria-pressed', String(aiOpen));
    if (!aiOpen) return void clear(el);
    const box = h('textarea', { rows: 3, placeholder: 'Ask about the open files…  (Ctrl/Cmd Enter sends)', 'aria-label': 'Message to the assistant' });
    box.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(box.value); } });
    const quick = [['Tidy', 'Tidy the open notes: fix structure, headings and wording, keep every fact. Return the complete revised Markdown.'], ['Explain', 'Explain the main ideas of the attached material simply, with one small example each.'], ['Quiz me', 'Ask me five short questions on the attached material, one at a time. Wait for my answer before the next.'], ['Hints', 'Help me with the attached exercise: give hints step by step, not the full solution.']];
    clear(el).append(
      h('header', {}, h('h2', { text: 'Assistant' }), h('span', { class: 'faint st-to', text: aiTo ? 'Sends to ' + aiTo : 'Nothing is sent until you press Send' }), h('span', { class: 'sp' }), h('button', { class: 'ghost', text: 'Clear', title: 'Start a new conversation', onclick: () => { chat = []; drawThread(); } })),
      h('div', { class: 'st-ctx', 'aria-label': 'Context sent with each message' }),
      h('div', { class: 'st-add' }, h('button', { text: '+ File', title: 'Add a file of this module', onclick: addFile }), h('button', { text: '+ Selection', title: 'Add the text selected in the focused tile', onclick: addSelection }), h('button', { text: '+ Text', title: 'Paste text for the assistant to read', onclick: addText })),
      h('div', { class: 'st-thread', 'aria-live': 'polite' }),
      h('div', { class: 'st-quick' }, quick.map(([l, t]) => h('button', { class: 'chip', text: l, title: t, onclick: () => send(t) }))),
      h('div', { class: 'st-ask' }, box, h('button', { class: 'primary', text: 'Send', onclick: () => send(box.value) })));
    ctxLine(); drawThread();
  }

  App.pages.edit = { title: 'Study', async mount(el, arg) {
    host = h('div', { class: 'study' },
      h('aside', { class: 'st-side', 'aria-label': 'Module files' }),
      h('section', { class: 'st-main' },
        h('div', { class: 'st-bar' }, h('h1', { text: 'Study' }), h('span', { class: 'sp' }),
          h('button', { class: 'chip', id: 'aitoggle', text: 'Assistant', title: 'Show or hide the assistant', 'aria-pressed': String(aiOpen), onclick: () => { aiOpen = !aiOpen; ls('ai', aiOpen ? '1' : '0'); drawAi(); } })),
        h('div', { class: 'st-tiles', 'aria-label': 'Open files, in four sections' }), h('div', { class: 'edbar' })),
      h('aside', { class: 'st-ai', 'aria-label': 'Assistant' }));
    el.append(host); el.style.overflow = 'hidden';
    aiOpen = ls('ai') !== '0';
    try { tree = App.S.tree = await api('/api/tree'); } catch (e) { tree = App.S.tree; }
    const rel = arg ? decodeURIComponent(arg) : null;
    if (!openRels().length) {
      try {
        const s = JSON.parse(sessionStorage.getItem('study.secs') || 'null');
        if (s && Array.isArray(s.secs)) {
          for (let i = 0; i < SEC.length; i++) for (const t of ((s.secs[i] || {}).tabs || []).slice(0, MAX_TABS)) { try { await openDoc(t); if (!openRels().includes(t)) secs[i].tabs.push(t); } catch (e) { /* gone */ } }
          secs.forEach((x, i) => { x.on = (s.secs[i] || {}).on || 0; }); focus = Math.max(0, Math.min(3, s.focus || 0)); if (s.split) split = { ...split, ...s.split };
        }
      } catch (e) {}
    }
    host.addEventListener('dragend', endDrag);
    const ms = modules(); mod = (rel && modOf(rel)) || (ms.includes(mod) ? mod : null) || (ms.includes(ls('mod')) ? ls('mod') : null) || ms[0] || '';
    drawAi();
    if (rel && !['study'].includes(rel)) await show(rel, false); else draw();
    if (rel && !docs.has(rel)) history.replaceState(null, '', '#/edit');
    const onFocus = () => { if (document.visibilityState === 'visible') checkDisk(); }; document.addEventListener('visibilitychange', onFocus); addEventListener('focus', onFocus);
    const onSave = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's' && host && !e.target.closest('textarea')) { const d = cur(); if (d && d.editable) { e.preventDefault(); save(d); } } };
    addEventListener('keydown', onSave);
    return () => { refs.forEach((r, i) => { const d = docs.get(shownRel(i)); if (d && r && r.ta) { d.pos = r.ta.selectionStart; d.scroll = r.ta.scrollTop; } }); host = null; refs = []; el.style.overflow = ''; document.removeEventListener('visibilitychange', onFocus); removeEventListener('focus', onFocus); removeEventListener('keydown', onSave); };
  } };
  App.pages.study = App.pages.edit;
})();
