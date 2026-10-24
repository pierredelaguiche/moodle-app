'use strict';
/* Core: DOM helper (never innerHTML), api, router, selection controller, menus, palette, toasts, job runner. */
const TOKEN = new URLSearchParams(location.search).get('t') || sessionStorage.getItem('tok') || '';
if (TOKEN) { try { sessionStorage.setItem('tok', TOKEN); } catch (e) {} }
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const App = { pages: {}, S: { badges: null, tree: null, docs: new Map(), cleanup: null }, listeners: [] };

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style') for (const [a, b] of Object.entries(v)) el.style.setProperty(a, b);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  const add = (c) => { if (c === null || c === undefined || c === false) return; if (Array.isArray(c)) c.forEach(add); else el.append(c instanceof Node ? c : document.createTextNode(String(c))); };
  kids.forEach(add);
  return el;
}
/* Element.append that also accepts arrays, null and false (so pages can pass lists of nodes) */
const _append = Element.prototype.append;
Element.prototype.append = function (...kids) { _append.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)); };
const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

const ICONS = {
  home: 'M3 11.5 12 4l9 7.5M5.5 10v9.5h13V10', files: 'M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z',
  ai: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z', tracker: 'M4 19V9m5 10V5m5 14v-7m5 7V8', calendar: 'M4 7.5A1.5 1.5 0 0 1 5.5 6h13A1.5 1.5 0 0 1 20 7.5v11a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5zM4 10.5h16M8.5 3.5v4m7-4v4',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 16.8v.1',
  settings: 'M4 7h9m4 0h3M4 17h3m4 0h9M15 4.5v5M9 14.5v5',
  clock: 'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', bell: 'M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0', edit: 'M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4',
  more: 'M5 12h.01M12 12h.01M19 12h.01', search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4', chev: 'M9 6l6 6-6 6', folder: 'M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z',
  file: 'M7 3h7l5 5v12.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5v-17A.5.5 0 0 1 7.5 3zM14 3v5h5', refresh: 'M20 11a8 8 0 0 0-14-4.5L4 9m0-5v5h5M4 13a8 8 0 0 0 14 4.5l2-2.5m0 5v-5h-5',
  menu: 'M4 7h16M4 12h16M4 17h16', plus: 'M12 5v14M5 12h14', left: 'M15 6l-6 6 6 6', right: 'M9 6l6 6-6 6', x: 'M6 6l12 12M18 6 6 18', save: 'M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6',
  history: 'M3 12a9 9 0 1 0 3-6.7L3 8m0-4v4h4M12 8v4l3 2', sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2.5v2m0 15v2M2.5 12h2m15 0h2M5.3 5.3l1.4 1.4m10.6 10.6 1.4 1.4m0-13.4-1.4 1.4M6.7 17.3l-1.4 1.4',
  download: 'M12 4v11m-4-4 4 4 4-4M5 19h14', eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
};
function icon(name) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.8'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', ICONS[name] || ''); s.append(p);
  return s;
}

/* ---------- formatting */
const DAY = 86400;
const pad = (n) => String(n).padStart(2, '0');
const fmtSize = (n) => { n = n || 0; const u = ['B', 'KB', 'MB', 'GB']; let i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + ' ' + u[i]; };
const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '';
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
const fmtDT = (ts) => new Date(ts * 1000).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const secs = (d) => Math.floor(d.getTime() / 1000);
const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const mondayOf = (d) => { const x = startOfDay(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
const toLocalInput = (ts, dateOnly) => { const d = new Date(ts * 1000); const ymd = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; return dateOnly ? ymd : `${ymd}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const fromLocalInput = (v) => v ? Math.floor(new Date(v.length === 10 ? v + 'T00:00' : v).getTime() / 1000) : null;
function relTime(ts, now) {
  const d = ts - now, a = Math.abs(d), s = d < 0 ? 'ago' : 'left';
  if (a < 3600) return `${Math.max(1, Math.round(a / 60))} min ${s}`;
  if (a < DAY) return `${Math.round(a / 3600)} h ${s}`;
  return `${Math.round(a / DAY)} d ${s}`;
}
function ago(ts) { if (!ts) return 'never'; const a = Date.now() / 1000 - ts; if (a < 60) return 'just now'; if (a < 3600) return `${Math.round(a / 60)} min ago`; if (a < DAY) return `${Math.round(a / 3600)} h ago`; return `${Math.round(a / DAY)} d ago`; }

/* ---------- api */
async function api(path, body, headers) {
  const opt = { headers: { 'X-Token': TOKEN, ...(headers || {}) } };
  if (body !== undefined) { opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.error || r.statusText); e.status = r.status; e.data = j; throw e; }
  return j;
}
const safe = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, { err: true }); } };

/* ---------- toasts & dialogs */
function toast(msg, o = {}) {
  const box = $('#toasts');
  const t = h('div', { class: 'toast' + (o.err ? ' err' : ''), role: 'status' }, h('span', { text: msg }));
  if (o.undo) t.append(h('button', { text: 'Undo', onclick: () => { t.remove(); o.undo(); } }));
  box.append(t);
  setTimeout(() => t.remove(), o.ms || (o.undo ? 7000 : o.err ? 7000 : 3500));
}
function dialog({ title, text, path, ok = 'OK', cancel = 'Cancel', danger = false, fields = null }) {
  return new Promise((resolve) => {
    const inputs = {};
    const dlg = h('dialog', {}, h('h3', { text: title }), text && h('p', { text }), path && h('div', { class: 'path', text: path }),
      fields && fields.map((f, n) => { const id = 'df' + n + Math.random().toString(36).slice(2, 6); const inp = f.type === 'textarea' ? h('textarea', { rows: 3, id }) : h('input', { type: f.type || 'text', id }); inp.value = f.value || ''; inputs[f.name] = inp; return [h('label', { text: f.label, for: id }), inp]; }),
      h('div', { class: 'btns' }, h('button', { text: cancel, onclick: () => dlg.close('no') }), h('button', { class: danger ? 'danger' : 'primary', text: ok, onclick: () => dlg.close('yes') })));
    dlg.addEventListener('close', () => { const res = dlg.returnValue === 'yes' ? (fields ? Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.value])) : true) : null; dlg.remove(); resolve(res); });
    dlg.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && !e.isComposing) { e.preventDefault(); dlg.close('yes'); } });
    document.body.append(dlg); dlg.showModal();
    const first = Object.values(inputs)[0]; (first || dlg.querySelector('button')).focus();
  });
}

/* ---------- context menu */
function ctxMenu(x, y, items) {
  closeCtx();
  const m = h('div', { id: 'ctx', role: 'menu' });
  items.forEach((it) => {
    if (it === 'sep') return m.append(h('hr'));
    m.append(h('button', { role: 'menuitem', disabled: it.disabled, class: it.danger ? 'danger' : '', onclick: () => { closeCtx(); it.fn(); } }, h('span', { text: it.label }), it.key && h('span', { class: 'k', text: it.key })));
  });
  document.body.append(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 6)) + 'px'; m.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 6)) + 'px';
  const f = m.querySelector('button:not(:disabled)'); if (f) f.focus();
  m.addEventListener('keydown', (e) => { const bs = $$('button:not(:disabled)', m), i = bs.indexOf(document.activeElement); if (e.key === 'ArrowDown') { bs[(i + 1) % bs.length].focus(); e.preventDefault(); } else if (e.key === 'ArrowUp') { bs[(i - 1 + bs.length) % bs.length].focus(); e.preventDefault(); } else if (e.key === 'Escape') closeCtx(); });
}
function closeCtx() { const m = $('#ctx'); if (m) m.remove(); }
addEventListener('pointerdown', (e) => { if (!e.target.closest('#ctx')) closeCtx(); }, true);
addEventListener('blur', closeCtx);

/* ---------- floating selection bar */
const SelBar = {
  show(info, buttons) {
    const bar = $('#selbar'); clear(bar);
    bar.append(h('span', { class: 'info', text: info }), buttons.filter(Boolean).map((b) => h('button', { class: b.cls || '', disabled: b.disabled, title: b.title, text: b.label, onclick: b.fn })));
    bar.classList.add('on');
  },
  hide() { $('#selbar').classList.remove('on'); },
  /* the selection controller adds "Select all" once a bar is up, so every list offers it the same way */
  extend(n, total, fn) {
    const bar = $('#selbar'); if (!bar.classList.contains('on') || !n || n >= total || $('.selall', bar)) return;
    const b = h('button', { class: 'selall', text: `Select all ${total}`, onclick: fn }), last = bar.lastElementChild;
    last && last.textContent === 'Clear' ? last.before(b) : bar.append(b);
  },
};

/* ---------- selection controller, one for every list.
   Rows carry data-sel="<id>". Click selects one row; Ctrl/Cmd+click or the round box toggles; Shift+click (or Shift on the box) extends a range
   from the anchor and a second Shift+click only moves the end of that range; drag a box on empty space; Space/arrows/Home/End/Ctrl+A/Esc on the
   keyboard; long-press or tap on touch. The round box on each row appears on hover and on every row once something is selected.
   A row with data-group is a heading: its box and click stand for the rows inside its [data-gscope] container (checked, half, or unchecked). */
function Selectable(root, o) {
  o = o || {};
  const rowSel = o.rowSel || '[data-sel]', sel = new Set(), winL = [];
  let anchor = null, focus = null, base = new Set(), swallow = false, band = null, start = null, bandBase = null, lp = null;   // base: what was selected before the current Shift range began
  root.tabIndex = 0;
  const all = () => $$(rowSel, root), isGroup = (r) => r.hasAttribute('data-group'), idOf = (r) => r.dataset.sel;
  const shown = (r) => r.offsetParent !== null, leaves = () => all().filter((r) => !isGroup(r)), rows = () => all().filter(shown);
  const members = (r) => $$(rowSel, r.closest('[data-gscope]') || r.parentElement).filter((x) => !isGroup(x)).map(idOf);
  const rowOf = (t) => { const r = t.closest && t.closest(rowSel); return r && root.contains(r) ? r : null; };
  const interactive = 'button,a,input,select,textarea,[data-nosel]';
  const rangeIds = (a, b) => { const ids = leaves().filter(shown).map(idOf); let i = ids.indexOf(a), j = ids.indexOf(b); if (i < 0) i = j; if (i < 0 || j < 0) return []; return ids.slice(Math.min(i, j), Math.max(i, j) + 1); };
  const commit = () => { base = new Set(sel); };
  const put = (ids) => ids.forEach((i) => sel.add(i)), drop = (ids) => ids.forEach((i) => sel.delete(i));

  function apply() {
    for (const r of all()) {
      const id = idOf(r); let on, part = false;
      if (isGroup(r)) { const m = members(r), n = m.filter((i) => sel.has(i)).length; on = !!m.length && n === m.length; part = n > 0 && !on; } else on = sel.has(id);
      r.classList.toggle('selected', on); r.classList.toggle('partial', part); r.classList.toggle('focused', id === focus); r.setAttribute('aria-selected', String(on));
      if (!r.querySelector(':scope > .selbox')) r.prepend(h('span', { class: 'selbox', 'aria-hidden': 'true' }));
    }
    root.classList.toggle('multi', sel.size > 0);
  }
  function change() { apply(); o.onChange && o.onChange(api.ids()); SelBar.extend(sel.size, leaves().length, () => api.all()); }

  const api = {
    ids: () => leaves().map(idOf).filter((i) => sel.has(i)),
    has: (i) => sel.has(i), size: () => sel.size, focusId: () => focus,
    set(ids) { const have = new Set(leaves().map(idOf)); sel.clear(); put(ids.filter((i) => have.has(i))); commit(); change(); },
    add(ids) { put(ids); commit(); change(); },
    remove(ids) { drop(ids); commit(); change(); },
    clear() { sel.clear(); commit(); change(); },
    all() { api.set(leaves().filter(shown).map(idOf)); root.focus({ preventScroll: true }); },
    refresh() { const have = new Set(leaves().map(idOf)); for (const i of [...sel]) if (!have.has(i)) sel.delete(i); if (focus && !all().some((r) => idOf(r) === focus)) focus = null; commit(); change(); },
    pickRow(r) { pick(r, {}); },
    /* move the selection to the previous/next visible row (used by the quick look) */
    step(d) { const rs = leaves().filter(shown); if (!rs.length) return; const ids = rs.map(idOf), cur = api.ids().find((i) => ids.includes(i)), i = cur ? ids.indexOf(cur) : -1;
      const r = rs[Math.max(0, Math.min(rs.length - 1, i < 0 ? 0 : i + d))]; anchor = focus = idOf(r); api.set([anchor]); r.scrollIntoView({ block: 'nearest' }); },
    destroy() { winL.forEach(([t, f]) => removeEventListener(t, f)); winL.length = 0; clearTimeout(lp); if (band) { band.remove(); band = null; } },
  };

  /* one place decides what a click on a row means */
  function pick(r, how) {
    const group = isGroup(r), ids = group ? members(r) : [idOf(r)];
    if (how.range && anchor && !group) { sel.clear(); put([...base]); put(rangeIds(anchor, ids[0])); }
    else if (how.toggle || (how.range && group)) { const on = !ids.every((i) => sel.has(i)); (on ? put : drop)(ids); anchor = ids[0]; commit(); }
    else { sel.clear(); put(ids); anchor = ids[0]; commit(); }
    focus = idOf(r); change();
  }
  const coarse = () => matchMedia('(hover: none) and (pointer: coarse)').matches;

  root.addEventListener('click', (e) => {
    if (swallow) { swallow = false; return; }
    const r = rowOf(e.target), box = e.target.closest('.selbox');
    if (!r) { if (!e.target.closest(interactive) && !e.shiftKey && !e.ctrlKey && !e.metaKey && o.clearOnBlank !== false && sel.size) api.clear(); return; }
    if (!box && e.target.closest(interactive)) return;
    pick(r, { range: e.shiftKey, toggle: (!!box && !e.shiftKey) || e.ctrlKey || e.metaKey || (sel.size > 0 && o.tapToggle !== false && coarse()) });
    root.focus({ preventScroll: true });
  });
  root.addEventListener('dblclick', (e) => { const r = rowOf(e.target); if (r && !e.target.closest('button,a,input,.selbox')) o.onOpen && o.onOpen(idOf(r), r); });
  root.addEventListener('contextmenu', (e) => {
    const r = rowOf(e.target); if (!r || !o.onContext) return;
    e.preventDefault(); const ids = isGroup(r) ? members(r) : [idOf(r)];
    if (!ids.every((i) => sel.has(i))) { sel.clear(); put(ids); anchor = ids[0]; focus = idOf(r); commit(); change(); }
    o.onContext(e, api.ids(), r);
  });

  /* rubber band (mouse) and long-press (touch, pen) */
  root.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    if (e.pointerType !== 'mouse') {
      const r = rowOf(e.target); if (!r || e.target.closest('button,a,input')) return;
      const x = e.clientX, y = e.clientY;
      lp = setTimeout(() => { lp = null; pick(r, { toggle: true }); if (navigator.vibrate) navigator.vibrate(12); swallow = true; setTimeout(() => { swallow = false; }, 400); }, 450);
      const cancel = (ev) => { if (ev.type === 'pointermove' && Math.hypot(ev.clientX - x, ev.clientY - y) < 8) return; clearTimeout(lp); lp = null; root.removeEventListener('pointermove', cancel); root.removeEventListener('pointerup', cancel); };
      root.addEventListener('pointermove', cancel); root.addEventListener('pointerup', cancel);
      return;
    }
    if (e.target.closest('button,a,input,select,textarea,.chev,.selbox')) return;
    if (e.target.closest('[draggable="true"]') || (o.bandOnRows === false && rowOf(e.target))) return;       // a draggable thing starts a drag, not a box
    start = { x: e.clientX, y: e.clientY }; bandBase = (e.ctrlKey || e.metaKey || e.shiftKey) ? new Set(sel) : new Set();
  });
  const onWin = (type, fn) => { addEventListener(type, fn); winL.push([type, fn]); };
  onWin('pointermove', (e) => {
    if (!start) return;
    if (!band) { if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < 6) return; band = h('div', { class: 'band' }); document.body.append(band); getSelection().removeAllRanges(); }
    const x1 = Math.min(start.x, e.clientX), y1 = Math.min(start.y, e.clientY), x2 = Math.max(start.x, e.clientX), y2 = Math.max(start.y, e.clientY);
    Object.assign(band.style, { left: x1 + 'px', top: y1 + 'px', width: x2 - x1 + 'px', height: y2 - y1 + 'px' });
    const sc = o.scroller || root.closest('#main'); if (sc) { const b = sc.getBoundingClientRect(); if (e.clientY > b.bottom - 36) sc.scrollTop += 14; else if (e.clientY < b.top + 36) sc.scrollTop -= 14; }
    sel.clear(); put([...bandBase]);
    for (const r of leaves()) { if (!shown(r)) continue; const b = r.getBoundingClientRect(); if (b.right >= x1 && b.left <= x2 && b.bottom >= y1 && b.top <= y2) sel.add(idOf(r)); }
    change();
  });
  onWin('pointerup', () => { if (band) { band.remove(); band = null; swallow = true; setTimeout(() => { swallow = false; }, 0); commit(); } start = null; });
  root.addEventListener('dragstart', () => { start = null; if (band) { band.remove(); band = null; } });

  root.addEventListener('keydown', (e) => {
    if (e.target !== root && e.target.closest('input,textarea,select,button')) return;
    const rs = rows(); if (!rs.length) return;
    const ids = rs.map(idOf); let i = ids.indexOf(focus);
    const go_ = (n) => {
      i = Math.max(0, Math.min(ids.length - 1, (i < 0 ? (n > 0 ? -1 : ids.length) : i) + n)); const r = rs[i]; focus = idOf(r);
      if (e.ctrlKey || e.metaKey) change();                                                          // only the focus ring moves
      else if (e.shiftKey && !isGroup(r)) { if (!anchor) { anchor = focus; commit(); } sel.clear(); put([...base]); put(rangeIds(anchor, focus)); change(); }
      else pick(r, {});
      r.scrollIntoView({ block: 'nearest' }); e.preventDefault();
    };
    if (e.key === 'ArrowDown') go_(1); else if (e.key === 'ArrowUp') go_(-1);
    else if (e.key === 'Home') { i = -1; go_(1); } else if (e.key === 'End') { i = ids.length; go_(-1); }
    else if (e.key === ' ' && focus) { const r = rs[i]; if (r) pick(r, { toggle: true }); e.preventDefault(); }
    else if ((e.key === 'a' || e.key === 'A') && (e.ctrlKey || e.metaKey)) { api.all(); e.preventDefault(); }
    else if (e.key === 'Escape' && sel.size) { api.clear(); e.stopPropagation(); }
    else if (e.key === 'Enter' && focus && rs[i]) { o.onOpen && o.onOpen(focus, rs[i]); e.preventDefault(); }
    else if (o.keys && o.keys[e.key] && !e.ctrlKey && !e.metaKey && !e.altKey) { const t = api.ids(); if (t.length) { o.keys[e.key](t, e); e.preventDefault(); } }
  });
  /* Esc clears the selection wherever the focus is (after pressing a button in the bar, say), unless a dialog, menu or quick look has its own use for it */
  onWin('keydown', (e) => {
    if (e.key !== 'Escape' || !sel.size || e.defaultPrevented || !root.isConnected || e.target.closest('input,textarea,select,[contenteditable]') || document.querySelector('dialog[open], #ctx, #palette, .qlook')) return;
    api.clear();
  });
  apply();
  return api;
}


/* swipe (touch/pen only): right = onRight, left = onLeft. Mouse drags are left to the rubber band. */
function swipeable(el, { onRight, onLeft, rightLabel = 'Done', leftLabel = 'Hide' }) {
  let x0 = null, dx = 0, id = null;
  const hint = h('div', { class: 'swipe-hint' }); el.prepend(hint);
  el.addEventListener('pointerdown', (e) => { if (e.pointerType === 'mouse' || e.target.closest('button,a,input,textarea')) return; x0 = e.clientX; dx = 0; id = e.pointerId; });
  el.addEventListener('pointermove', (e) => {
    if (x0 === null || e.pointerId !== id) return; dx = e.clientX - x0; if (Math.abs(dx) < 10) return;
    el.classList.add('dragging'); el.style.transform = `translateX(${dx}px)`;
    hint.textContent = dx > 0 ? rightLabel : leftLabel; hint.style.justifyContent = dx > 0 ? 'flex-start' : 'flex-end';
    hint.style.background = dx > 0 ? 'var(--ok-bg)' : 'var(--warn-bg)'; hint.style.color = dx > 0 ? 'var(--ok)' : 'var(--warn)';
  });
  const end = () => { if (x0 === null) return; el.classList.remove('dragging'); el.style.transform = ''; hint.textContent = ''; hint.style.background = ''; if (dx > 90 && onRight) onRight(); else if (dx < -90 && onLeft) onLeft(); x0 = null; dx = 0; };
  el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
}

/* ---------- jobs (refresh/download/read) */
const Jobs = {
  poll: null,
  async run(startPromise, label) {
    let j; try { j = await startPromise; } catch (e) { toast(e.message, { err: true }); return; }
    if (!j.job) return;
    return Jobs.follow(label);
  },
  async follow(label, quiet) {
    const box = $('#joblog'); box.hidden = !!quiet; const pre = $('pre', box); clear(pre); $('.jt', box).textContent = label || 'Working…'; $('.spin', box).hidden = false; let pos = 0;
    clearInterval(Jobs.poll);
    await new Promise((resolve) => {
      Jobs.poll = setInterval(async () => {
        try {
          const s = await api('/api/job?since=' + pos);
          s.lines.forEach((l) => pre.append(h('div', { class: l.startsWith('!') ? 'err' : '', text: l }))); pos = s.next; pre.scrollTop = pre.scrollHeight;
          if (!s.running) { clearInterval(Jobs.poll); $('.spin', box).hidden = true; $('.jt', box).textContent = s.error ? 'Finished with an error' : 'Done'; if (!s.error) setTimeout(() => { if (!Jobs.poll) box.hidden = true; }, quiet ? 0 : 2500); resolve(); }
        } catch (e) { clearInterval(Jobs.poll); resolve(); }
      }, 600);
    });
    await refreshBadges(); App.listeners.forEach((f) => f('job'));
  },
};

/* ---------- badges & sync button */
async function refreshBadges() {
  try { App.S.badges = await api('/api/badges'); } catch (e) { return; }
  const b = App.S.badges;
  const set = (id, n, cls) => { const el = $('#pill-' + id); if (!el) return; el.textContent = n; el.hidden = !n; el.className = 'pill ' + (cls || ''); };
  set('files', b.new_files + b.updates, 'info'); set('deadlines', b.overdue || b.due_soon, b.overdue ? 'hot' : 'info'); set('announcements', b.unread, 'info');
  const sy = $('#sync').parentElement; sy.classList.toggle('busy', !!b.syncing);
  const w = $('#syncwhen'); w.textContent = b.syncing ? 'Checking Moodle…' : b.scan_error ? 'Last check failed: ' + b.scan_error : (b.scanned_at ? 'Checked ' + ago(b.scanned_at) : 'Not checked yet'); w.parentElement.classList.toggle('err', !!b.scan_error);
}

/* ---------- router */
const NAV = [['', 'Dashboard', 'home'], ['files', 'Files', 'files'], ['ai', 'AI', 'ai'], ['tracker', 'Tracker', 'tracker'], ['calendar', 'Calendar', 'calendar'], ['deadlines', 'Deadlines', 'clock'], ['announcements', 'Announcements', 'bell'], ['edit', 'Editor', 'edit'], ['settings', 'Settings', 'settings']];
function route() { const hsh = decodeURIComponent(location.hash.replace(/^#\/?/, '')); const i = hsh.indexOf('/'); return i < 0 ? { name: hsh, arg: '' } : { name: hsh.slice(0, i), arg: hsh.slice(i + 1) }; }
function go(name, arg) { location.hash = '#/' + name + (arg ? '/' + encodeURIComponent(arg) : ''); }
async function render() {
  closeCtx(); SelBar.hide();
  if (App.S.cleanup) { try { App.S.cleanup(); } catch (e) {} App.S.cleanup = null; }
  const r = route(); const page = App.pages[r.name] || App.pages[''];
  $$('.navitem[data-nav]').forEach((a) => a.toggleAttribute('aria-current', a.dataset.nav === (App.pages[r.name] ? r.name : '')) );
  $$('.navitem[data-nav]').forEach((a) => { if (a.hasAttribute('aria-current')) a.setAttribute('aria-current', 'page'); });
  const main = clear($('#main')); main.scrollTop = 0; $('#app').classList.remove('menuopen');
  document.title = (page.title || 'Moodle App') + ' · Moodle App';
  try { App.S.cleanup = (await page.mount(main, r.arg)) || null; } catch (e) { main.append(h('div', { class: 'page' }, h('div', { class: 'empty' }, h('b', { text: 'This page could not load' }), e.message))); }
}
addEventListener('hashchange', render);

/* ---------- command palette */
function fuzzy(q, s) { q = q.toLowerCase(); s = s.toLowerCase(); let i = 0, score = 0, last = -2; for (let k = 0; k < s.length && i < q.length; k++) if (s[k] === q[i]) { score += (k === last + 1 ? 3 : 1) + (k === 0 || ' /._-'.includes(s[k - 1]) ? 2 : 0); last = k; i++; } return i === q.length ? score : -1; }
async function openPalette() {
  if ($('#palette')) return;
  if (!App.S.tree) { try { App.S.tree = await api('/api/tree'); } catch (e) {} }
  const files = []; (function w(n) { if (n.type === 'file') files.push(n); else (n.kids || []).forEach(w); })(App.S.tree ? App.S.tree.root : { type: 'dir', kids: [] });
  const items = [...NAV.filter((n) => n[0] !== 'edit').map(([k, t]) => ({ label: 'Go to ' + t, hint: 'g ' + ({ '': 'h', files: 'f', ai: 'i', tracker: 't', calendar: 'c', deadlines: 'd', announcements: 'a', settings: 's' }[k] || ''), fn: () => go(k) })),
    { label: 'Go to Activity log', hint: 'g y', fn: () => go('history') }, { label: 'Check Moodle now', hint: 'Shift R', fn: checkMoodle }, { label: 'Open the help', hint: '?', fn: () => help() }, { label: 'Keyboard and mouse', fn: () => help('keys') },
    ...files.filter((f) => f.status !== 'new' && f.status !== 'ignored' && f.status !== 'missing').map((f) => ({ label: f.name, hint: f.rel.split('/').slice(0, -1).join(' / '), fn: () => go('edit', f.rel), file: true }))];
  let act = 0, shown = [];
  const input = h('input', { type: 'text', placeholder: 'Jump to a page or open a file…', 'aria-label': 'Command palette' }), ul = h('ul');
  const close = () => $('#palette') && $('#palette').remove();
  const draw = () => { const q = input.value.trim(); shown = (q ? items.map((it) => ({ it, s: fuzzy(q, it.label + ' ' + (it.hint || '')) })).filter((x) => x.s >= 0).sort((a, b) => b.s - a.s).map((x) => x.it) : items.filter((i) => !i.file)).slice(0, 40); act = Math.min(act, Math.max(0, shown.length - 1)); clear(ul);
    shown.forEach((it, i) => ul.append(h('li', { class: i === act ? 'act' : '', onclick: () => { close(); it.fn(); } }, h('span', { text: it.label }), h('span', { class: 'k', text: it.hint || '' })))); if (!shown.length) ul.append(h('li', { class: 'faint', text: 'No match' })); const a = $('li.act', ul); if (a) a.scrollIntoView({ block: 'nearest' }); };
  input.addEventListener('input', () => { act = 0; draw(); });
  input.addEventListener('keydown', (e) => { if (e.key === 'ArrowDown') { act = Math.min(shown.length - 1, act + 1); draw(); e.preventDefault(); } else if (e.key === 'ArrowUp') { act = Math.max(0, act - 1); draw(); e.preventDefault(); } else if (e.key === 'Enter' && shown[act]) { const f = shown[act].fn; close(); f(); } else if (e.key === 'Escape') close(); });
  const p = h('div', { id: 'palette', onclick: (e) => { if (e.target === p) close(); } }, h('div', { class: 'box' }, input, ul)); document.body.append(p); draw(); input.focus();
}

/* ---------- global actions */
const checkMoodle = () => Jobs.run(api('/api/refresh', {}), 'Checking Moodle…');
function applyTheme() { const t = localStorage.getItem('theme'); if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
const getTheme = () => { try { return localStorage.getItem('theme') || 'system'; } catch (e) { return 'system'; } };
function setTheme(t) { try { if (t === 'system') localStorage.removeItem('theme'); else localStorage.setItem('theme', t); } catch (e) {} applyTheme(); }
function help(section) { go('help', typeof section === 'string' ? section : ''); }
function moreMenu(ev) {
  const r = ev.currentTarget.getBoundingClientRect();
  ctxMenu(r.right + 6, r.top - 120, [{ label: 'Activity log', fn: () => go('history') }, { label: 'Help and keyboard shortcuts', key: '?', fn: () => help('keys') }, { label: 'Collapse menu', key: '[', fn: toggleSide }, 'sep',
    { label: 'Stop Moodle App', danger: true, fn: async () => { if (await dialog({ title: 'Stop Moodle App?', text: 'The page stops working until you start it again from the terminal.', ok: 'Stop', danger: true })) { try { await api('/api/quit', {}); } catch (e) {} document.body.textContent = 'Stopped. You can close this tab.'; } } }]);
}
function toggleSide() { const a = $('#app'); if (matchMedia('(max-width: 860px)').matches) a.classList.toggle('menuopen'); else { a.classList.toggle('collapsed'); try { localStorage.setItem('side', a.classList.contains('collapsed') ? 'c' : 'o'); } catch (e) {} } }

let gPending = false, gTimer = null;
addEventListener('keydown', (e) => {
  const typing = e.target.closest && e.target.closest('input,textarea,select,[contenteditable]');
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey || $('dialog[open]')) return;
  if (gPending) { gPending = false; clearTimeout(gTimer); const m = { h: '', f: 'files', i: 'ai', t: 'tracker', c: 'calendar', d: 'deadlines', a: 'announcements', y: 'history', e: 'edit', s: 'settings' }; if (e.key in m) { go(m[e.key]); e.preventDefault(); } return; }
  if (e.key === 'g') { gPending = true; gTimer = setTimeout(() => gPending = false, 1200); }
  else if (e.key === '?') { help('keys'); e.preventDefault(); } else if (e.key === 'R') { checkMoodle(); e.preventDefault(); } else if (e.key === '[') toggleSide();
  else if (e.key === '/') { const s = $('[data-search]'); if (s) { s.focus(); e.preventDefault(); } }
});
addEventListener('beforeunload', (e) => { if ([...App.S.docs.values()].some((d) => d.dirty)) { e.preventDefault(); e.returnValue = ''; } });

/* ---------- boot */
async function firstRun() {          // a brand-new install opens the quick start once
  try {
    if (localStorage.getItem('guide.seen')) return;
    const st = await api('/api/setup');
    if (!st.complete) { localStorage.setItem('guide.seen', '1'); if (route().name !== 'help') go('help'); }
    else localStorage.setItem('guide.seen', '1');
  } catch (e) {}
}
function boot() {
  applyTheme();
  const app = $('#app'); try { if (localStorage.getItem('side') === 'c') app.classList.add('collapsed'); } catch (e) {}
  const nav = $('#nav');
  NAV.forEach(([k, t, ic]) => nav.append(h('a', { class: 'navitem', href: '#/' + k, 'data-nav': k, title: t }, icon(ic), h('span', { class: 't', text: t }), ['files', 'deadlines', 'announcements'].includes(k) && h('span', { class: 'pill', id: 'pill-' + k, hidden: true }))));
  $('#sync').addEventListener('click', checkMoodle);
  $('#more').addEventListener('click', moreMenu);
  $('#helpbtn').addEventListener('click', () => help());
  $('#palbtn').addEventListener('click', openPalette);
  $('#menubtn').addEventListener('click', toggleSide);
  $('#jclose').addEventListener('click', () => { $('#joblog').hidden = true; });
  render();
  firstRun();
  refreshBadges().then(() => { if (App.S.badges && App.S.badges.syncing) Jobs.follow('Checking Moodle…', true); }); setInterval(refreshBadges, 30000);
}
