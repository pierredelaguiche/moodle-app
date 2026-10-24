'use strict';
/* Deadlines, Announcements, Activity log. */
(function () {
  /* ---------------- deadlines */
  let dHost = null, dSel = null, showAll = false, dq = '', dData = null;
  async function dLoad() { dData = await api('/api/deadlines'); if (dHost) dDraw(); }
  const groupOf = (x, now) => { const s0 = secs(startOfDay(new Date(now * 1000))); if (x.overdue) return ['0', 'Overdue']; if (x.due_ts < s0) return ['1', 'Earlier']; if (x.due_ts < s0 + DAY) return ['2', 'Today']; if (x.due_ts < s0 + 2 * DAY) return ['3', 'Tomorrow']; if (x.due_ts < s0 + 7 * DAY) return ['4', 'This week']; return ['5', 'Later']; };
  function dDraw() {
    const d = dData, keep = $('#main').scrollTop, keepSel = dSel ? dSel.ids() : [];
    const items = d.items.filter((x) => (showAll || (!x.done && !x.hidden && !x.gone && x.status === 'open' || x.overdue && !x.hidden)) && (!dq || (x.title + ' ' + x.course).toLowerCase().includes(dq)));
    const groups = {}; items.forEach((x) => { const [k, l] = groupOf(x, d.now); (groups[k] = groups[k] || { l, a: [] }).a.push(x); });
    const byId = Object.fromEntries(d.items.map((x) => [x.id, x]));
    const list = h('div', { class: 'list', 'aria-label': 'Deadlines' });
    Object.keys(groups).sort().forEach((k) => {
      list.append(h('div', { class: 'dayhead' + (k === '0' ? ' bad' : ''), text: groups[k].l }));
      groups[k].a.forEach((x) => {
        const row = h('div', { class: 'item' + (x.done ? ' done' : '') + (x.overdue ? ' overdue' : '') + (x.gone ? ' gone' : ''), dataset: { sel: x.id } },
          h('span', { class: 'tag', text: x.course }),
          h('div', { class: 'grow' }, h('div', { class: 'title' }, x.url ? h('a', { href: x.url, target: '_blank', rel: 'noopener noreferrer', text: x.title }) : x.title), h('div', { class: 'meta', text: fmtDT(x.due_ts) + ' · ' + relTime(x.due_ts, d.now) + (x.cutoff_ts && x.cutoff_ts !== x.due_ts ? ' · cut-off ' + fmtDT(x.cutoff_ts) : '') }), x.note ? h('div', { class: 'meta', text: '✎ ' + x.note }) : null),
          x.status === 'submitted' ? h('span', { class: 'badge b-done', text: 'Submitted' }) : null, x.status === 'graded' ? h('span', { class: 'badge b-done', text: 'Graded' + (x.grade ? ': ' + x.grade : '') }) : null,
          x.overdue ? h('span', { class: 'badge b-overdue', text: 'Overdue' }) : null, x.hidden ? h('span', { class: 'badge', text: 'Hidden' }) : null, x.gone ? h('span', { class: 'badge', text: 'Removed on Moodle' }) : null,
          x.status === 'open' && !x.gone ? h('button', { class: 'ghost donebtn', title: x.done ? 'Mark as not done' : 'Mark as done', text: x.done ? 'Done ✓' : 'Mark done', onclick: (e) => { e.stopPropagation(); setFlag([x.id], 'done', !x.done, true); } }) : null,
          h('button', { class: 'ghost', title: 'Add a note', text: x.note ? 'Edit note' : 'Note', onclick: (e) => { e.stopPropagation(); editNote(x); } }));
        swipeable(row, { onRight: () => setFlag([x.id], 'done', !x.done, true), onLeft: () => setFlag([x.id], 'hidden', !x.hidden, true), rightLabel: x.done ? 'Not done' : 'Done', leftLabel: x.hidden ? 'Unhide' : 'Hide' });
        list.append(row);
      });
    });
    const bar = h('div', { class: 'bar' }, h('div', { class: 'search' }, icon('search'), h('input', { type: 'search', 'data-search': '', placeholder: 'Filter  /', value: dq, oninput: (e) => { dq = e.target.value.trim().toLowerCase(); dDraw(); $('[data-search]').focus(); } })),
      h('label', { class: 'row muted' }, h('input', { type: 'checkbox', checked: showAll, onchange: (e) => { showAll = e.target.checked; dDraw(); } }), 'Show done, hidden and older'), h('span', { class: 'sp' }),
      h('button', { text: 'Copy calendar feed link', title: 'Subscribe in a calendar app that can reach this computer (keep the link private)', onclick: safe(async () => { const l = await api('/api/ics-link'); await navigator.clipboard.writeText(location.origin + l.path); toast('Feed link copied. It only works while Moodle App is running.'); }) }), h('button', { text: 'Calendar', onclick: () => go('calendar') }));
    clear(dHost).append(h('div', { class: 'phead' }, h('h1', { text: 'Deadlines' }), h('span', { class: 'sub', text: items.filter((x) => !x.done && x.status === 'open').length + ' open' })), bar, items.length ? list : h('div', { class: 'empty' }, h('b', { text: 'Nothing due' }), showAll ? 'No deadlines recorded yet.' : 'Everything is done or hidden. Tick “Show done” to see it.'));
    dSel = Selectable(list, { rowSel: '[data-sel]', onChange: (ids) => {
      if (!ids.length) return SelBar.hide(); const xs = ids.map((i) => byId[i]).filter(Boolean), allDone = xs.every((x) => x.done), allHid = xs.every((x) => x.hidden);
      SelBar.show(`${ids.length} selected`, [{ label: allDone ? 'Mark not done' : 'Mark done', cls: 'primary', fn: () => setFlag(ids, 'done', !allDone, true) }, { label: allHid ? 'Unhide' : 'Hide', fn: () => setFlag(ids, 'hidden', !allHid, true) }, { label: 'Clear', fn: () => dSel.clear() }]);
    }, keys: { x: (ids) => setFlag(ids, 'done', !ids.every((i) => byId[i] && byId[i].done), true), h: (ids) => setFlag(ids, 'hidden', !ids.every((i) => byId[i] && byId[i].hidden), true), n: (ids) => byId[ids[0]] && editNote(byId[ids[0]]) } });
    dSel.set(keepSel.filter((i) => byId[i])); $('#main').scrollTop = keep;
  }
  const setFlag = safe(async (ids, flag, on, undo) => {
    const prev = ids.map((i) => dData.items.find((x) => x.id === i)).filter(Boolean).map((x) => [x.id, !!x[flag]]);
    for (const i of ids) await api('/api/deadline', { id: i, [flag]: on });
    await Promise.all([dLoad(), refreshBadges()]);
    if (undo) toast((flag === 'done' ? (on ? 'Marked done' : 'Marked not done') : (on ? 'Hidden' : 'Unhidden')) + (ids.length > 1 ? ` (${ids.length})` : ''), { undo: safe(async () => { for (const [i, v] of prev) await api('/api/deadline', { id: i, [flag]: v }); await Promise.all([dLoad(), refreshBadges()]); }) });
  });
  const editNote = safe(async (x) => { const r = await dialog({ title: 'Note', text: x.title, fields: [{ name: 'note', label: 'Only you see this. It also appears in the calendar feed.', type: 'textarea', value: x.note }], ok: 'Save' }); if (r) { await api('/api/deadline', { id: x.id, note: r.note }); await dLoad(); } });
  App.pages.deadlines = { title: 'Deadlines', async mount(el) {
    dHost = h('div', { class: 'page' }, h('div', { class: 'empty', text: 'Loading…' })); el.append(dHost);
    const l = (w) => { if (w === 'job' && dHost) dLoad().catch(() => {}); }; App.listeners.push(l);
    try { await dLoad(); } catch (e) { clear(dHost).append(h('div', { class: 'empty' }, e.message)); }
    return () => { dHost = null; dSel = null; App.listeners = App.listeners.filter((x) => x !== l); };
  } };

  /* ---------------- announcements */
  let aHost = null, aSel = null, aOnlyUnread = false, aData = null;
  const expanded = new Set();
  async function aLoad() { aData = await api('/api/announcements'); if (aHost) aDraw(); }
  const markRead = safe(async (ids, read = true) => { await api('/api/announcements/read', { ids, read }); await Promise.all([aLoad(), refreshBadges()]); });
  function aDraw() {
    const keep = $('#main').scrollTop, keepSel = aSel ? aSel.ids() : [], items = aData.items.filter((x) => !aOnlyUnread || !x.read), byId = Object.fromEntries(aData.items.map((x) => [String(x.id), x]));
    const list = h('div', { class: 'list' });
    items.forEach((x) => {
      const open = expanded.has(x.id);
      list.append(h('div', { class: 'item', style: { 'align-items': 'flex-start' }, dataset: { sel: String(x.id) } }, 
        h('div', { class: 'grow' }, h('div', { class: 'row' }, !x.read ? h('span', { class: 'badge b-new', text: 'Unread' }) : null, h('span', { class: 'tag', text: x.course }),
          h('button', { class: 'ghost title', 'aria-expanded': String(open), style: { 'text-align': 'left', 'font-weight': x.read ? '500' : '700' }, text: x.subject, onclick: () => { open ? expanded.delete(x.id) : expanded.add(x.id); if (!open && !x.read) markRead([x.id]); else aDraw(); } }), h('span', { class: 'sp' }), h('span', { class: 'meta', text: fmtDate(x.created) + (x.author ? ' · ' + x.author : '') })),
          open ? h('div', { class: 'msg', style: { 'white-space': 'pre-wrap', margin: '8px 0 2px', 'max-height': '24em', overflow: 'auto' }, text: x.text || '(empty)' }) : h('div', { class: 'meta', style: { 'white-space': 'nowrap', overflow: 'hidden', 'text-overflow': 'ellipsis', 'max-width': '70ch' }, text: x.text.replace(/\s+/g, ' ').slice(0, 140) })))); 
    });
    const unread = aData.items.filter((x) => !x.read).length;
    clear(aHost).append(h('div', { class: 'phead' }, h('h1', { text: 'Announcements' }), h('span', { class: 'sub', text: unread + ' unread' }), h('span', { class: 'sp' }), h('button', { text: 'Activity log', title: 'Everything that changed in recent checks', onclick: () => go('history') }), h('button', { text: 'Mark all read', disabled: !unread, onclick: () => markRead(aData.items.filter((x) => !x.read).map((x) => x.id)) })),
      h('div', { class: 'bar' }, h('button', { class: 'chip', 'aria-pressed': String(!aOnlyUnread), text: 'All', onclick: () => { aOnlyUnread = false; aDraw(); } }), h('button', { class: 'chip', 'aria-pressed': String(aOnlyUnread), text: 'Unread', onclick: () => { aOnlyUnread = true; aDraw(); } })),
      items.length ? list : h('div', { class: 'empty' }, h('b', { text: aOnlyUnread ? 'All caught up' : 'No announcements yet' }), aOnlyUnread ? 'Nothing unread.' : 'Check Moodle to fetch the latest.'));
    aSel = Selectable(list, { rowSel: '[data-sel]', onChange: (ids) => { if (!ids.length) return SelBar.hide(); const xs = ids.map((i) => byId[i]).filter(Boolean), anyUnread = xs.some((x) => !x.read);
      SelBar.show(`${ids.length} selected`, [{ label: anyUnread ? 'Mark read' : 'Mark unread', cls: 'primary', fn: () => markRead(xs.map((x) => x.id), anyUnread) }, { label: 'Clear', fn: () => aSel.clear() }]); },
      keys: { r: (ids) => { const xs = ids.map((i) => byId[i]).filter(Boolean); markRead(xs.map((x) => x.id), xs.some((x) => !x.read)); } },
      onOpen: (id) => { const x = byId[id]; if (x) { expanded.has(x.id) ? expanded.delete(x.id) : expanded.add(x.id); if (!x.read) markRead([x.id]); else aDraw(); } } });
    aSel.set(keepSel.filter((i) => byId[i])); $('#main').scrollTop = keep;
  }
  App.pages.announcements = { title: 'Announcements', async mount(el) {
    aHost = h('div', { class: 'page' }, h('div', { class: 'empty', text: 'Loading…' })); el.append(aHost);
    const l = (w) => { if (w === 'job' && aHost) aLoad().catch(() => {}); }; App.listeners.push(l);
    try { await aLoad(); } catch (e) { clear(aHost).append(h('div', { class: 'empty' }, e.message)); }
    return () => { aHost = null; aSel = null; App.listeners = App.listeners.filter((x) => x !== l); };
  } };

  /* ---------------- activity log */
  let hHost = null, hFilter = 'all';
  const CAT = { new_file: 'files', updated_file: 'files', downloaded: 'files', ignored: 'files', closed: 'files', moved: 'files', deadline_new: 'deadlines', deadline_changed: 'deadlines', deadline_status: 'deadlines', deadline_gone: 'deadlines', announcement: 'announcements' };
  const base = (p) => (p || '').split('/').pop();
  function describe(c) {
    const d = c.detail || {};
    switch (c.kind) {
      case 'new_file': return ['New on Moodle', d.name || base(c.ref), d.to ? 'goes to ' + d.to : ''];
      case 'updated_file': return ['Updated on Moodle', d.name || base(c.ref), ''];
      case 'downloaded': return [d.update ? 'Downloaded update' : 'Downloaded', base(d.to), d.to || ''];
      case 'ignored': return ['Ignored', base(c.ref.split(':').pop()), ''];
      case 'closed': return ['Forgot missing file', base(c.ref.split(':').pop()), ''];
      case 'moved': return ['Followed your move', base(d.to), d.to || ''];
      case 'deadline_new': return ['New deadline', d.title, d.due ? 'due ' + fmtDT(d.due) : ''];
      case 'deadline_changed': return ['Deadline moved', d.title, `${d.from ? fmtDT(d.from) : '?'} → ${d.to ? fmtDT(d.to) : '?'}`];
      case 'deadline_status': return ['Deadline status', d.title, `${d.from} → ${d.to}`];
      case 'deadline_gone': return ['Removed on Moodle', c.ref, ''];
      case 'announcement': return ['Announcement', d.subject, ''];
      default: return [c.kind, c.ref, ''];
    }
  }
  async function hDraw() {
    const data = await api('/api/history'); if (!hHost) return;
    const items = data.items.filter((c) => hFilter === 'all' || CAT[c.kind] === hFilter);
    const days = {}; items.forEach((c) => { const k = new Date(c.ts * 1000).toDateString(); (days[k] = days[k] || []).push(c); });
    const body = Object.keys(days).length ? Object.entries(days).map(([k, arr]) => [h('div', { class: 'dayhead', text: new Date(arr[0].ts * 1000).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) }),
      arr.map((c) => { const [what, title, more] = describe(c); return h('div', { class: 'hist' }, h('span', { class: 'muted num', text: fmtTime(c.ts) }), h('span', { class: 'badge', text: what }), h('span', {}, h('span', { text: title }), c.course ? h('span', { class: 'tag', style: { 'margin-left': '8px' }, text: c.course }) : null, more ? h('span', { class: 'muted', text: '  ' + more }) : null)); })]) : h('div', { class: 'empty' }, h('b', { text: 'Nothing recorded' }), 'Changes appear here after Moodle is checked.');
    clear(hHost).append(h('div', { class: 'phead' }, h('h1', { text: 'Activity log' }), h('span', { class: 'sub', text: 'What changed in recent checks and what you did with it.' }), h('span', { class: 'sp' }), h('button', { text: 'Back to announcements', onclick: () => go('announcements') })),
      h('div', { class: 'bar' }, [['all', 'All'], ['files', 'Files'], ['deadlines', 'Deadlines'], ['announcements', 'Announcements']].map(([k, l]) => h('button', { class: 'chip', 'aria-pressed': String(hFilter === k), text: l, onclick: () => { hFilter = k; hDraw(); } }))), body);
  }
  App.pages.history = { title: 'Activity log', async mount(el) {
    hHost = h('div', { class: 'page' }, h('div', { class: 'empty', text: 'Loading…' })); el.append(hHost);
    try { await hDraw(); } catch (e) { clear(hHost).append(h('div', { class: 'empty' }, e.message)); }
    return () => { hHost = null; };
  } };
})();
