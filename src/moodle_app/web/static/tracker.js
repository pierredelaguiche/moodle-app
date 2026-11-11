'use strict';
/* Tracker: per course a ribbon of weeks (filled by reviewed/total); click a week to see and tick its files. */
function ribbon(c, onPick, compact, picked) {
  const wk = c.weeks.filter((w) => w.no !== 0);
  const r = h('div', { class: 'ribbon', role: 'list', 'aria-label': 'Weeks of ' + c.short });
  wk.forEach((w) => {
    r.append(h('button', { class: 'wk ' + w.state + (picked === w.no ? ' sel' : ''), role: 'listitem', title: `${w.name}: ${w.reviewed} of ${w.total} reviewed · ${w.state}`, onclick: () => onPick(w) },
      h('div', { class: 'fill', style: { height: (w.state === 'done' ? 100 : w.pct) + '%' } }), h('span', { class: 'lab', text: compact ? w.no : 'W' + w.no }), compact ? null : h('span', { class: 'frac', text: w.total ? `${w.reviewed}/${w.total}` : '–' })));
  });
  if (c.current_week && c.phase === 'running') {
    const idx = wk.findIndex((w) => w.no === c.current_week);
    if (idx >= 0) { const tag = h('span', { class: 'today-tag', text: 'now' }); tag.style.left = ((idx + 0.5) / wk.length * 100) + '%'; r.append(tag); r.style.marginTop = '18px'; }
  }
  return r;
}
(function () {
  let host = null, picked = {}, sels = {};
  const STATE = { done: 'Done', current: 'This week', behind: 'Behind', upcoming: 'Ahead', open: 'Open', general: 'General' };
  async function draw() {
    const tr = await api('/api/tracker'); if (!host) return;
    const keep = $('#main').scrollTop;
    clear(host).append(h('div', { class: 'phead' }, h('h1', { text: 'Tracker' }), h('span', { class: 'sub', text: 'A week counts as done when every file in it is downloaded and marked reviewed.' })),
      tr.courses.length ? tr.courses.map(course) : h('div', { class: 'empty' }, h('b', { text: 'No courses to track' }), 'Check Moodle first, then make sure each course matches a folder (moodle-app courses).'));
    $('#main').scrollTop = keep;
  }
  function course(c) {
    const pw = picked[c.id] != null ? picked[c.id] : (c.weeks.find((w) => w.state === 'current') || c.weeks.find((w) => w.state === 'behind') || c.weeks.find((w) => w.no !== 0) || {}).no;
    const w = c.weeks.find((x) => x.no === pw);
    const box = h('div', { class: 'course' }, h('div', { class: 'ch' }, h('h2', { text: c.short }), h('span', { class: 'muted', text: c.name }), h('span', { class: 'sp' }),
      c.current_week && c.phase === 'running' ? h('span', { class: 'badge b-soon', text: 'Week ' + c.current_week }) : h('span', { class: 'badge', text: c.phase === 'over' ? 'Term over' : c.phase === 'before' ? 'Not started' : 'No dates' }),
      c.behind ? h('span', { class: 'badge b-behind', text: c.behind + ' behind' }) : null, h('span', { class: 'num', text: `${c.reviewed}/${c.total} · ${c.pct}%` })),
      h('div', { class: 'bigbar', style: { 'margin-bottom': '12px' } }, h('i', { style: { width: c.pct + '%' } })),
      ribbon(c, (x) => { picked[c.id] = x.no; draw(); }, false, pw),
      c.next_deadline ? h('div', { class: 'meta muted', style: { margin: '8px 0 0' }, text: 'Next deadline: ' + c.next_deadline.title + ' · ' + fmtDT(c.next_deadline.due_ts) }) : null);
    if (w) box.append(week(c, w));
    return box;
  }
  function week(c, w) {
    const files = w.files, list = h('div', { class: 'list', style: { 'margin-top': '10px' } });
    const rv = (ids, on) => api('/api/reviewed', { keys: ids, on }).then(draw).catch((e) => toast(e.message, { err: true }));
    const rows = files.map((f) => h('div', { class: 'item', dataset: { sel: f.key } }, h('div', { class: 'grow' }, f.status === 'new' ? h('span', { class: 'title', text: f.name }) : h('a', { class: 'title', href: '#/edit/' + encodeURIComponent(f.rel), text: f.name }), h('div', { class: 'meta', text: f.moodle })),
      f.status === 'new' ? h('span', { class: 'badge b-new', text: 'Not downloaded' }) : f.status === 'update' ? h('span', { class: 'badge b-update', text: 'Update' }) : null,
      f.note ? h('a', { class: 'badge b-done', href: '#/edit/' + encodeURIComponent(f.note), title: 'Open the note made from this file', text: 'Notes ✓' }) : f.status !== 'new' && /\.(pdf|txt|md)$/i.test(f.name) ? h('button', { class: 'ghost', title: 'Opens the AI page with this file ready for the note-taker', text: 'Make notes', onclick: () => go('ai', 'notes:' + f.rel) }) : null,
      f.status === 'new' ? h('button', { text: 'Download', onclick: () => Jobs.run(api('/api/download', { keys: [f.key] })) }) : h('button', { class: f.reviewed ? 'b-done' : '', 'aria-pressed': String(f.reviewed), text: f.reviewed ? '✓ Reviewed' : 'Mark reviewed', onclick: () => rv([f.key], !f.reviewed) })));
    list.append(rows);
    const head = h('div', { class: 'bar', style: { 'margin-top': '14px' } }, h('h2', { style: { margin: 0 }, text: w.name }), h('span', { class: 'badge b-' + (w.state === 'done' ? 'done' : w.state === 'behind' ? 'behind' : w.state === 'current' ? 'soon' : ''), text: STATE[w.state] }), h('span', { class: 'muted num', text: `${w.reviewed} of ${w.total} reviewed${w.notes ? ' · ' + w.notes + ' with notes' : ''}` }), h('span', { class: 'sp' }),
      w.files.some((f) => f.status === 'new') ? h('button', { text: 'Download new', onclick: () => Jobs.run(api('/api/download', { keys: w.files.filter((f) => f.status === 'new').map((f) => f.key) })) }) : null,
      h('button', { class: 'primary', disabled: !w.files.some((f) => f.status !== 'new'), text: w.state === 'done' ? 'Unmark week' : 'Mark whole week reviewed', onclick: () => api('/api/reviewed', { week: { course: c.id, section: w.no }, on: w.state !== 'done' }).then(draw).catch((e) => toast(e.message, { err: true })) }));
    const wrap = h('div', {}, head, files.length ? list : h('div', { class: 'empty', text: 'No files in this week.' }));
    const s = Selectable(list, { rowSel: '[data-sel]', clearOnBlank: false, onChange: (ids) => {
      if (!ids.length) return SelBar.hide();
      const byKey = Object.fromEntries(files.map((f) => [f.key, f])), chosen = ids.map((i) => byKey[i]).filter(Boolean), rvs = chosen.filter((f) => f.status !== 'new'), allOn = rvs.length && rvs.every((f) => f.reviewed);
      SelBar.show(`${ids.length} selected`, [rvs.length ? { label: (allOn ? 'Unmark reviewed ' : 'Mark reviewed ') + rvs.length, cls: 'primary', fn: () => rv(rvs.map((f) => f.key), !allOn) } : null, chosen.some((f) => f.status === 'new') ? { label: 'Download', fn: () => Jobs.run(api('/api/download', { keys: chosen.filter((f) => f.status === 'new').map((f) => f.key) })) } : null, { label: 'Clear', fn: () => s.clear() }].filter(Boolean));
    }, keys: { r: (ids) => { const f = files.filter((x) => ids.includes(x.key) && x.status !== 'new'); if (f.length) rv(f.map((x) => x.key), !f.every((x) => x.reviewed)); } } });
    return wrap;
  }
  App.pages.tracker = {
    title: 'Tracker',
    async mount(el) {
      host = h('div', { class: 'page' }, h('div', { class: 'empty', text: 'Loading…' })); el.append(host);
      const l = (w) => { if (w === 'job' && host) draw().catch(() => {}); }; App.listeners.push(l);
      try { await draw(); } catch (e) { clear(host).append(h('div', { class: 'empty' }, e.message)); }
      return () => { host = null; App.listeners = App.listeners.filter((x) => x !== l); };
    },
  };
})();
