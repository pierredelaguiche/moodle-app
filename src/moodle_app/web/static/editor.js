'use strict';
/* Editor: tabs, text editing with safe save (conflict check, backups on the server), markdown preview, PDF and image viewing. */
(function () {
  let host = null, active = null, saving = false;
  const docs = App.S.docs;

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
    const d = { rel: r.rel, name: r.name, kind: r.kind, text: r.text || '', orig: r.text || '', sha: r.sha, eol: r.eol || 'lf', lang: r.lang, dirty: false, editable: r.editable, note: r.note, preview: r.lang === 'markdown', wrap: false, savedAt: null, url: null, conflict: false };
    if (r.kind === 'pdf' || r.kind === 'image') { const resp = await fetch('/api/raw?rel=' + encodeURIComponent(rel), { headers: { 'X-Token': TOKEN } }); if (!resp.ok) throw new Error('Could not load the preview'); d.url = URL.createObjectURL(await resp.blob()); }
    docs.set(rel, d); return d;
  }
  function closeDoc(rel) {
    const d = docs.get(rel); if (!d) return;
    const done = () => { if (d.url) URL.revokeObjectURL(d.url); const keys = [...docs.keys()], i = keys.indexOf(rel); docs.delete(rel); const next = keys[i + 1] || keys[i - 1]; go('edit', next || ''); };
    if (d.dirty) dialog({ title: 'Discard unsaved changes?', text: d.rel, ok: 'Discard', danger: true }).then((ok) => ok && done()); else done();
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
    drawChrome();
  });
  const revert = (d) => { d.text = d.orig; d.dirty = false; draw(); };
  async function reload(d) { const r = await api('/api/file?rel=' + encodeURIComponent(d.rel)); d.text = d.orig = r.text || ''; d.sha = r.sha; d.eol = r.eol; d.dirty = false; d.conflict = false; draw(); }
  async function checkDisk() {
    const d = active && docs.get(active); if (!d || d.kind !== 'text' || saving) return;
    try { const r = await api('/api/file?rel=' + encodeURIComponent(d.rel)); if (r.sha && r.sha !== d.sha) { if (!d.dirty) { d.text = d.orig = r.text; d.sha = r.sha; d.eol = r.eol; toast('Reloaded: the file changed on disk', { ms: 2500 }); draw(); } else { d.conflict = true; drawChrome(); } } } catch (e) { /* file gone or moved; ignore */ }
  }

  /* ---------- drawing */
  let ta = null, prev = null, statusEl = null, tabsEl = null, toolEl = null;
  function drawChrome() {
    if (!host) return; const d = active && docs.get(active);
    clear(tabsEl).append([...docs.values()].map((x) => h('div', { class: 'edtab', role: 'tab', 'aria-selected': String(x.rel === active), title: x.rel, onclick: () => go('edit', x.rel), onauxclick: (e) => { if (e.button === 1) closeDoc(x.rel); } }, x.dirty ? h('span', { class: 'dot', title: 'Unsaved' }) : null, h('span', { text: x.name }), h('button', { class: 'x', 'aria-label': 'Close ' + x.name, text: '×', onclick: (e) => { e.stopPropagation(); closeDoc(x.rel); } }))),
      h('button', { class: 'ghost icon', 'aria-label': 'New file', title: 'New file', onclick: newFile }, icon('plus')));
    clear(toolEl);
    if (d) {
      toolEl.append(h('span', { class: 'muted', style: { 'font-size': '12.5px' }, text: d.rel.split('/').join('  ›  ') }), h('span', { class: 'sp' }),
        d.conflict ? h('span', { class: 'badge b-warn', text: 'Changed on disk' }) : null, d.conflict ? h('button', { text: 'Reload from disk', onclick: () => reload(d) }) : null,
        d.kind === 'text' && d.lang === 'markdown' ? h('button', { 'aria-pressed': String(d.preview), class: 'chip', text: 'Preview', onclick: () => { d.preview = !d.preview; draw(); } }) : null,
        d.kind === 'text' ? h('button', { 'aria-pressed': String(d.wrap), class: 'chip', text: 'Wrap', onclick: () => { d.wrap = !d.wrap; draw(); } }) : null,
        d.url ? h('button', { text: 'Open in new tab', onclick: () => window.open(d.url, '_blank', 'noopener') }) : null,
        d.editable ? h('button', { text: 'Revert', disabled: !d.dirty, onclick: () => revert(d) }) : null, d.editable ? h('button', { class: 'primary', disabled: !d.dirty, text: 'Save', title: 'Ctrl/Cmd S', onclick: () => save(d) }) : null);
    }
    status();
  }
  function status() {
    if (!statusEl) return; const d = active && docs.get(active); clear(statusEl); if (!d) return;
    if (d.kind !== 'text') { statusEl.append(h('span', { text: fmtSize(0) === '' ? '' : (d.kind === 'pdf' ? 'PDF' : d.kind === 'image' ? 'Image' : 'Read only') })); return; }
    let ln = 1, col = 1; if (ta) { const p = ta.selectionStart, before = d.text.slice(0, p); ln = before.split('\n').length; col = p - before.lastIndexOf('\n'); }
    const words = (d.text.match(/\S+/g) || []).length;
    statusEl.append(h('span', { text: `Ln ${ln}, Col ${col}` }), h('span', { text: `${words} words · ${d.text.length} chars` }), h('span', { text: d.lang + ' · ' + d.eol.toUpperCase() }), h('span', { class: 'sp' }), h('span', { text: d.dirty ? 'Unsaved changes' : d.savedAt ? 'Saved ' + d.savedAt.toLocaleTimeString() : 'Up to date' }));
  }
  function draw() {
    if (!host) return; const d = active && docs.get(active); drawChrome(); const body = clear($('.edbody', host)); body.classList.remove('split'); ta = prev = null;
    if (!d) {
      body.append(h('div', { class: 'empty', style: { margin: 'auto' } }, h('b', { text: docs.size ? 'Pick a tab' : 'No file open' }), 'Open a file from Files (double-click or Enter), press ', h('kbd', { text: '⌘K' }), ' to jump to one, or ', h('button', { text: 'create a new file', onclick: newFile }), '.'));
      return;
    }
    if (d.kind === 'pdf') return body.append(h('iframe', { src: d.url, title: d.name }));
    if (d.kind === 'image') return body.append(h('img', { src: d.url, alt: d.name }));
    if (d.kind !== 'text') return body.append(h('div', { class: 'empty', style: { margin: 'auto' } }, h('b', { text: 'This file cannot be edited here' }), d.note || (d.kind === 'toolarge' ? 'It is larger than 2 MB.' : 'Only text files open in the editor.')));
    ta = h('textarea', { spellcheck: 'false', 'aria-label': 'Editing ' + d.name, wrap: d.wrap ? 'soft' : 'off', style: { 'white-space': d.wrap ? 'pre-wrap' : 'pre' } }); ta.value = d.text;
    ta.addEventListener('input', () => { d.text = ta.value; const was = d.dirty; d.dirty = d.text !== d.orig; if (was !== d.dirty) drawChrome(); else status(); if (prev) renderPrev(d); });
    ta.addEventListener('keyup', status); ta.addEventListener('click', status);
    ta.addEventListener('keydown', (e) => onKey(e, d));
    body.append(ta);
    if (d.lang === 'markdown' && d.preview) { body.classList.add('split'); prev = h('div', { class: 'md', 'aria-label': 'Preview' }); body.append(prev); renderPrev(d);
      let lock = false; ta.addEventListener('scroll', () => { if (lock) return; lock = true; prev.scrollTop = (ta.scrollTop / Math.max(1, ta.scrollHeight - ta.clientHeight)) * (prev.scrollHeight - prev.clientHeight); requestAnimationFrame(() => lock = false); }); }
    if (d.pos) { ta.selectionStart = ta.selectionEnd = d.pos; ta.scrollTop = d.scroll || 0; } if (!/^(ontouchstart)/.test('')) ta.focus({ preventScroll: true });
  }
  let pt = null; function renderPrev(d) { clearTimeout(pt); pt = setTimeout(() => { if (prev) { clear(prev).append(markdown(d.text)); } }, 120); }
  function onKey(e, d) {
    const k = e.key; if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 's') { e.preventDefault(); save(d); return; }
    d.pos = ta.selectionStart; d.scroll = ta.scrollTop;
    const edit = (start, end, text, selS, selE) => { ta.focus(); ta.setSelectionRange(start, end); if (!document.execCommand || !document.execCommand('insertText', false, text)) { ta.setRangeText(text, start, end, 'end'); ta.dispatchEvent(new Event('input')); } if (selS !== undefined) ta.setSelectionRange(selS, selE); };
    if (k === 'Tab') {
      e.preventDefault(); const s = ta.selectionStart, en = ta.selectionEnd, v = ta.value;
      if (s === en && !e.shiftKey) return edit(s, en, '    ');
      const ls = v.lastIndexOf('\n', s - 1) + 1, block = v.slice(ls, en), lines = block.split('\n');
      const nb = lines.map((l) => e.shiftKey ? l.replace(/^( {1,4}|\t)/, '') : '    ' + l).join('\n'); edit(ls, en, nb, ls, ls + nb.length);
    } else if (k === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.isComposing) {
      const s = ta.selectionStart, v = ta.value, ls = v.lastIndexOf('\n', s - 1) + 1, cur = v.slice(ls, s), ind = /^\s*/.exec(cur)[0]; let extra = '';
      const li = /^(\s*)([-*+]|\d+\.)\s(\[[ x]\]\s)?/.exec(cur);
      if (d.lang === 'markdown' && li) { if (cur.trim() === li[0].trim()) { e.preventDefault(); return edit(ls, s, '', ls, ls); } e.preventDefault(); const mark = /\d/.test(li[2]) ? (parseInt(li[2], 10) + 1) + '.' : li[2]; return edit(s, ta.selectionEnd, '\n' + li[1] + mark + ' ' + (li[3] ? '[ ] ' : '')); }
      if (/[:{(\[]\s*$/.test(cur)) extra = '    '; if (ind || extra) { e.preventDefault(); edit(s, ta.selectionEnd, '\n' + ind + extra); }
    }
  }
  const newFile = safe(async () => {
    const cur = active ? active.split('/').slice(0, -1).join('/') : '';
    const f = await dialog({ title: 'New file', text: 'Path inside your study folder. Folders are created if needed.', fields: [{ name: 'rel', label: 'Path', value: (cur ? cur + '/' : '') + 'notes.md' }], ok: 'Create' });
    if (f && f.rel.trim()) { const r = await api('/api/file/new', { rel: f.rel.trim(), text: '' }); go('edit', r.rel); }
  });

  App.pages.edit = { title: 'Editor', async mount(el, arg) {
    host = h('div', { class: 'ed' }, h('div', { class: 'edtabs', role: 'tablist' }), h('div', { class: 'edtool' }), h('div', { class: 'edbody' }), h('div', { class: 'edbar' }));
    el.append(host); tabsEl = $('.edtabs', host); toolEl = $('.edtool', host); statusEl = $('.edbar', host); el.style.overflow = 'hidden';
    const rel = arg ? decodeURIComponent(arg) : null;
    try { if (rel) { await openDoc(rel); active = rel; } else if (!active || !docs.has(active)) active = [...docs.keys()][0] || null; } catch (e) { toast(e.message, { err: true }); active = [...docs.keys()][0] || null; if (rel) history.replaceState(null, '', '#/edit'); }
    draw();
    const onFocus = () => { if (document.visibilityState === 'visible') checkDisk(); }; document.addEventListener('visibilitychange', onFocus); addEventListener('focus', onFocus);
    return () => { const d = active && docs.get(active); if (d && ta) { d.pos = ta.selectionStart; d.scroll = ta.scrollTop; } host = null; ta = prev = null; el.style.overflow = ''; document.removeEventListener('visibilitychange', onFocus); removeEventListener('focus', onFocus); };
  } };
})();
