'use strict';
/* Calendar: month / week / agenda over deadlines and your own events. Click a day for details, drag your own events to move them. */
(function () {
  let host = null, mode = 'month', cursor = startOfDay(new Date()), picked = null, data = null, showHidden = false, panel = null, cleanupKeys = null;
  const HR0 = 6, HR1 = 23, HH = 44;
  const range = () => {
    if (mode === 'month') { const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1), s = mondayOf(first), last = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0); const rows = Math.ceil(((mondayOf(last) - s) / 86400000 + 7) / 7); return { s, e: addDays(s, rows * 7), rows }; }
    if (mode === 'week') { const s = mondayOf(cursor); return { s, e: addDays(s, 7), rows: 1 }; }
    return { s: startOfDay(cursor), e: addDays(startOfDay(cursor), 30), rows: 1 };
  };
  const title = () => { const r = range(); if (mode === 'month') return cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }); const e = addDays(r.e, -1); return r.s.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) + ' – ' + e.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); };
  async function load() { const r = range(); data = await api(`/api/calendar?from=${secs(r.s)}&to=${secs(r.e)}&hidden=${showHidden ? 1 : 0}`); if (host) draw(); }
  const step = (n) => { if (mode === 'month') cursor = new Date(cursor.getFullYear(), cursor.getMonth() + n, 1); else cursor = addDays(cursor, n * (mode === 'week' ? 7 : 30)); load(); };
  const itemsOn = (day) => { const a = secs(day), b = secs(addDays(day, 1)); return data.items.filter((i) => i.start < b && (i.end ? (i.all_day ? i.end >= a : i.end > a) : i.start >= a)); };
  const cls = (i) => 'ev ' + (i.type === 'event' ? 'own' : 'dl') + (i.overdue ? ' overdue' : '') + (i.done ? ' done' : '') + (i.hidden ? ' hidden' : '');
  const label = (i) => (i.all_day ? '' : fmtTime(i.start) + ' ') + i.title;
  const termWeek = (monday) => { if (!data.term_start) return ''; const w = Math.floor((monday - mondayOf(new Date(data.term_start * 1000))) / (7 * 86400000)) + 1; return w >= 1 && w <= 30 ? 'W' + w : ''; };

  function chip(i) {
    const b = h('button', { class: cls(i), title: `${i.title}${i.course ? ' · ' + i.course : ''}`, draggable: i.type === 'event' ? 'true' : null, onclick: (e) => { e.stopPropagation(); picked = new Date(i.start * 1000); openPanel(startOfDay(picked), i.id); } }, label(i));
    if (i.type === 'event') b.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', String(i.event_id)); e.dataTransfer.effectAllowed = 'move'; });
    return b;
  }
  const moveEvent = safe(async (id, day) => {
    const ev = data.items.find((i) => i.event_id === id); if (!ev) return;
    const shift = secs(day) - secs(startOfDay(new Date(ev.start * 1000)));
    if (!shift) return;
    await api('/api/events/update', { id, start: ev.start + shift, end: ev.end ? ev.end + shift : null });
    toast('Moved “' + ev.title + '”', { undo: safe(async () => { await api('/api/events/update', { id, start: ev.start, end: ev.end }); await load(); }) }); await load();
  });
  function dayCell(day, monthRef) {
    const its = itemsOn(day), isToday = sameDay(day, new Date());
    const c = h('div', { class: 'day' + (isToday ? ' today' : '') + (monthRef !== null && day.getMonth() !== monthRef ? ' out' : '') + (picked && sameDay(picked, day) ? ' sel' : ''), role: 'gridcell', tabindex: '-1', 'aria-label': day.toDateString() + ', ' + its.length + ' items', onclick: () => { picked = day; openPanel(day); }, ondblclick: () => editEvent(null, day) },
      h('span', { class: 'dn', text: day.getDate() === 1 ? day.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : day.getDate(), style: day.getDate() === 1 ? { width: 'auto', padding: '0 6px', 'border-radius': '11px' } : {} }), its.slice(0, 3).map(chip), its.length > 3 ? h('button', { class: 'more', text: `+${its.length - 3} more`, onclick: (e) => { e.stopPropagation(); picked = day; openPanel(day); } }) : null);
    c.addEventListener('dragover', (e) => { e.preventDefault(); c.classList.add('dropok'); }); c.addEventListener('dragleave', () => c.classList.remove('dropok'));
    c.addEventListener('drop', (e) => { e.preventDefault(); c.classList.remove('dropok'); moveEvent(Number(e.dataTransfer.getData('text/plain')), day); });
    return c;
  }
  function monthView() {
    const r = range(), g = h('div', { class: 'cal', role: 'grid', 'aria-label': title() }), dow = h('div', { class: 'dow' }, h('div'), ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => h('div', { text: d })));
    const weeks = h('div', { class: 'weeks', style: { '--rows': r.rows } });
    for (let w = 0; w < r.rows; w++) { const mon = addDays(r.s, w * 7); weeks.append(h('div', { class: 'wrow', role: 'row' }, h('div', { class: 'wn', text: termWeek(mon), title: 'Week of the term' }), [0, 1, 2, 3, 4, 5, 6].map((d) => dayCell(addDays(mon, d), cursor.getMonth())))); }
    g.append(dow, weeks); return g;
  }
  function weekView() {
    const r = range(), days = [0, 1, 2, 3, 4, 5, 6].map((d) => addDays(r.s, d)), g = h('div', { class: 'cal wk7' });
    g.append(h('div', { class: 'thead' }, h('div', { text: termWeek(r.s) }), days.map((d) => h('div', { class: sameDay(d, new Date()) ? 'today' : '', onclick: () => { picked = d; openPanel(d); } }, d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }), itemsOn(d).filter((i) => i.all_day).map(chip)))));
    const hours = h('div', { class: 'hours' }); for (let k = HR0; k < HR1; k++) hours.append(h('div', { text: pad(k) + ':00' }));
    const grid = h('div', { class: 'tgrid' }, hours);
    days.forEach((d) => {
      const col = h('div', { class: 'tcol', onclick: (e) => { if (e.target !== col) return; const y = e.offsetY / HH * 60; const m = Math.round(y / 30) * 30; const t = new Date(d); t.setHours(HR0, m, 0, 0); editEvent(null, t); } });
      const its = itemsOn(d).filter((i) => !i.all_day).sort((a, b) => a.start - b.start), lanes = [];
      its.forEach((i) => { const end = Math.max(i.end || 0, i.start + 1800); let l = lanes.findIndex((e) => e <= i.start); if (l < 0) { l = lanes.length; lanes.push(0); } lanes[l] = end; i._l = l; i._e = end; });
      its.forEach((i) => { const a = new Date(i.start * 1000), top = Math.max(0, ((a.getHours() + a.getMinutes() / 60) - HR0) * HH), hgt = Math.max(20, (i._e - i.start) / 3600 * HH); const n = Math.max(1, lanes.length);
        const b = h('div', { class: cls(i).replace('ev ', 'tev '), style: { top: Math.min(top, (HR1 - HR0) * HH - 20) + 'px', height: hgt + 'px', left: (i._l / n * 100) + '%', width: (100 / n) + '%' }, title: i.title + (i.course ? ' · ' + i.course : ''), onclick: (e) => { e.stopPropagation(); openPanel(d, i.id); } }, fmtTime(i.start) + ' ' + i.title); if (i.type === 'event') { b.draggable = true; b.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', String(i.event_id)); }); } col.append(b); });
      col.addEventListener('dragover', (e) => e.preventDefault()); col.addEventListener('drop', (e) => { e.preventDefault(); moveEvent(Number(e.dataTransfer.getData('text/plain')), d); });
      if (sameDay(d, new Date())) { const n = new Date(); const y = ((n.getHours() + n.getMinutes() / 60) - HR0) * HH; if (y >= 0 && y <= (HR1 - HR0) * HH) col.append(h('div', { class: 'nowline', style: { top: y + 'px' } })); }
      grid.append(col);
    });
    g.append(grid); return g;
  }
  function agendaView() {
    const r = range(), wrap = h('div', { class: 'list' }); let any = false;
    for (let i = 0; i < 30; i++) { const d = addDays(r.s, i), its = itemsOn(d); if (!its.length) continue; any = true;
      wrap.append(h('div', { class: 'dayhead' + (sameDay(d, new Date()) ? '' : ''), text: (sameDay(d, new Date()) ? 'Today · ' : '') + d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) }),
        its.map((x) => h('div', { class: 'item' + (x.done ? ' done' : '') + (x.overdue ? ' overdue' : ''), onclick: () => openPanel(d, x.id) }, h('span', { class: 'num muted', text: x.all_day ? 'all day' : fmtTime(x.start), style: { width: '56px' } }), x.course ? h('span', { class: 'tag', text: x.course }) : null, h('div', { class: 'grow' }, h('div', { class: 'title', text: x.title }), x.note ? h('div', { class: 'meta', text: x.note }) : null), h('span', { class: 'badge ' + (x.type === 'event' ? 'b-new' : 'b-warn'), text: x.type === 'event' ? 'Yours' : 'Deadline' })))); }
    return any ? wrap : h('div', { class: 'empty' }, h('b', { text: 'Nothing in the next 30 days' }), 'Add an event, or check Moodle for deadlines.');
  }
  function draw() {
    const keep = $('#main').scrollTop;
    const view = mode === 'month' ? monthView() : mode === 'week' ? weekView() : agendaView();
    clear(host).append(h('div', { class: 'phead' }, h('h1', { text: 'Calendar' }), h('span', { class: 'sub', text: title() }), h('span', { class: 'sp' }), h('button', { class: 'primary', text: 'Add event', onclick: () => editEvent(null, picked || new Date()) })),
      h('div', { class: 'bar' }, h('button', { class: 'icon', 'aria-label': 'Previous', onclick: () => step(-1) }, icon('left')), h('button', { text: 'Today', onclick: () => { cursor = startOfDay(new Date()); picked = cursor; load(); } }), h('button', { class: 'icon', 'aria-label': 'Next', onclick: () => step(1) }, icon('right')),
        h('div', { class: 'seg', role: 'group', 'aria-label': 'View' }, [['month', 'Month'], ['week', 'Week'], ['agenda', 'Agenda']].map(([k, l]) => h('button', { 'aria-pressed': String(mode === k), text: l, onclick: () => { mode = k; draw.scrolled = false; load(); } }))), h('span', { class: 'sp' }),
        h('label', { class: 'row muted' }, h('input', { type: 'checkbox', checked: showHidden, onchange: (e) => { showHidden = e.target.checked; load(); } }), 'Show hidden'), h('span', { class: 'muted', text: '← → to move, t for today' })), view);
    $('#main').scrollTop = keep;
    if (mode === 'week' && !draw.scrolled) { draw.scrolled = true; const g = $('.tgrid', host), m = $('#main'); if (g) { const now = new Date(), inWeek = data.items && range().s <= now && now < range().e; const hr = inWeek ? Math.max(HR0, now.getHours() - 2) : 8; m.scrollTop = g.getBoundingClientRect().top - m.getBoundingClientRect().top + m.scrollTop - 40 + (hr - HR0) * HH; } }
    if (panel) openPanel(panel.day, panel.focus, true);
  }
  function openPanel(day, focusId, keepOpen) {
    closePanel(); panel = { day, focus: focusId }; picked = day; if (host) host.classList.add('hasside');
    const its = itemsOn(day);
    const el = h('aside', { class: 'side', role: 'dialog', 'aria-label': day.toDateString() }, h('h3', {}, day.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }), h('span', { class: 'sp' }), h('button', { class: 'ghost icon', 'aria-label': 'Close', onclick: () => { closePanel(); panel = null; } }, icon('x'))),
      h('button', { class: 'primary', text: 'Add event', onclick: () => editEvent(null, day), style: { 'margin-bottom': '10px' } }),
      its.length ? its.map((i) => h('div', { class: 'card', style: { margin: '8px 0', 'border-color': focusId === i.id ? 'var(--accent)' : '' } },
        h('div', { class: 'row' }, h('b', { text: i.title }), h('span', { class: 'sp' }), h('span', { class: 'badge ' + (i.type === 'event' ? 'b-new' : i.overdue ? 'b-overdue' : 'b-warn'), text: i.type === 'event' ? 'Yours' : i.done ? 'Done' : i.overdue ? 'Overdue' : 'Deadline' })),
        h('div', { class: 'meta muted', text: (i.all_day ? 'All day' : fmtTime(i.start) + (i.end ? ' – ' + fmtTime(i.end) : '')) + (i.course ? ' · ' + i.course : '') }), i.note ? h('div', { text: i.note, style: { 'margin-top': '4px' } }) : null,
        h('div', { class: 'row', style: { 'margin-top': '8px' } }, i.type === 'event' ? [h('button', { text: 'Edit', onclick: () => editEvent(i) }), h('button', { class: 'danger', text: 'Delete', onclick: () => delEvent(i) })]
          : [i.url ? h('a', { href: i.url, target: '_blank', rel: 'noopener noreferrer', text: 'Open in Moodle' }) : null, i.status === 'open' ? h('button', { text: i.done ? 'Mark not done' : 'Mark done', onclick: safe(async () => { await api('/api/deadline', { id: i.id, done: !i.done }); await Promise.all([load(), refreshBadges()]); }) }) : null,
            h('button', { text: i.hidden ? 'Unhide' : 'Hide', onclick: safe(async () => { await api('/api/deadline', { id: i.id, hidden: !i.hidden }); await Promise.all([load(), refreshBadges()]); }) })]))) : h('div', { class: 'empty', text: 'Nothing on this day.' }));
    document.body.append(el);
  }
  function closePanel() { $$('.side').forEach((e) => e.remove()); if (host) host.classList.remove('hasside'); }
  const delEvent = safe(async (i) => { if (!await dialog({ title: 'Delete event?', text: i.title, ok: 'Delete', danger: true })) return; await api('/api/events/delete', { id: i.event_id }); toast('Event deleted'); await load(); });

  const editEvent = safe(async (ev, day) => {
    const courses = data.courses || [], allDay0 = ev ? ev.all_day : false;
    const start0 = ev ? ev.start : (() => { const d = new Date(day); if (d.getHours() === 0 && d.getMinutes() === 0) d.setHours(9, 0, 0, 0); return secs(d); })();
    const end0 = ev ? ev.end : start0 + 3600;
    const f = {
      title: h('input', { type: 'text', value: ev ? ev.title : '', placeholder: 'e.g. Study CSCI.323 week 3', 'aria-label': 'Title' }),
      allday: h('input', { type: 'checkbox', checked: allDay0 }), start: h('input', { type: allDay0 ? 'date' : 'datetime-local', value: toLocalInput(start0, allDay0) }), end: h('input', { type: allDay0 ? 'date' : 'datetime-local', value: end0 ? toLocalInput(end0, allDay0) : '' }),
      course: h('select', {}, h('option', { value: '', text: 'No course' }), courses.map((c) => h('option', { value: c.id, text: c.short, selected: ev && ev.course_id === c.id }))), note: h('textarea', { rows: 3 }),
    };
    f.note.value = ev ? ev.note : '';
    f.allday.addEventListener('change', () => { const on = f.allday.checked; const s = fromLocalInput(f.start.value), e = fromLocalInput(f.end.value); f.start.type = on ? 'date' : 'datetime-local'; f.end.type = on ? 'date' : 'datetime-local'; f.start.value = s ? toLocalInput(s, on) : ''; f.end.value = e ? toLocalInput(e, on) : ''; });
    let nid = 0; const lab = (t, inp) => { inp.id = 'ev' + (++nid); return [h('label', { text: t, for: inp.id }), inp]; };
    const dlg = h('dialog', {}, h('h3', { text: ev ? 'Edit event' : 'Add event' }), lab('Title', f.title), h('label', { class: 'row' }, f.allday, 'All day'), lab('Starts', f.start), lab('Ends (optional)', f.end), lab('Course (optional)', f.course), lab('Note', f.note),
      h('div', { class: 'btns' }, h('button', { text: 'Cancel', onclick: () => dlg.close('no') }), h('button', { class: 'primary', text: ev ? 'Save' : 'Add', onclick: () => dlg.close('yes') })));
    const ok = await new Promise((res) => { dlg.addEventListener('close', () => { res(dlg.returnValue === 'yes'); dlg.remove(); }); document.body.append(dlg); dlg.showModal(); f.title.focus(); });
    if (!ok) return;
    const body = { title: f.title.value, start: fromLocalInput(f.start.value), end: fromLocalInput(f.end.value), all_day: f.allday.checked, course_id: f.course.value ? Number(f.course.value) : null, note: f.note.value };
    if (ev) await api('/api/events/update', { id: ev.event_id, ...body }); else await api('/api/events', body);
    toast(ev ? 'Event saved' : 'Event added'); await load();
  });

  App.pages.calendar = { title: 'Calendar', async mount(el) {
    host = h('div', { class: 'page', style: { 'max-width': '1400px' } }, h('div', { class: 'empty', text: 'Loading…' })); el.append(host); draw.scrolled = false;
    const l = (w) => { if (w === 'job' && host) load().catch(() => {}); }; App.listeners.push(l);
    const key = (e) => { if (e.target.closest('input,textarea,select') || e.ctrlKey || e.metaKey || e.altKey || $('dialog[open]')) return; if (e.key === 'ArrowLeft') { step(-1); e.preventDefault(); } else if (e.key === 'ArrowRight') { step(1); e.preventDefault(); } else if (e.key === 't') { cursor = startOfDay(new Date()); picked = cursor; load(); } else if (e.key === 'm') { mode = 'month'; load(); } else if (e.key === 'w') { mode = 'week'; load(); } else if (e.key === 'a') { mode = 'agenda'; load(); } else if (e.key === 'n') { editEvent(null, picked || new Date()); } else if (e.key === 'Escape') { closePanel(); panel = null; } };
    addEventListener('keydown', key);
    let sx = null; const ps = (e) => { if (e.pointerType !== 'mouse') sx = e.clientX; }, pe = (e) => { if (sx !== null && e.pointerType !== 'mouse') { const dx = e.clientX - sx; if (Math.abs(dx) > 90) step(dx < 0 ? 1 : -1); } sx = null; }; host.addEventListener('pointerdown', ps); host.addEventListener('pointerup', pe);
    try { await load(); } catch (e) { clear(host).append(h('div', { class: 'empty' }, e.message)); }
    return () => { host = null; panel = null; closePanel(); removeEventListener('keydown', key); App.listeners = App.listeners.filter((x) => x !== l); };
  } };
})();
