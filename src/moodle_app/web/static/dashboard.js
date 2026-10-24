'use strict';
/* Dashboard = the digest: what to do first, what is due, where you are behind, what changed. */
(function () {
  const greet = () => { const hr = new Date().getHours(); return hr < 5 ? 'Late night' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening'; };
  let host = null;
  async function draw() {
    const t0 = secs(startOfDay(new Date()));
    const [d, tr, cal] = await Promise.all([api('/api/digest'), api('/api/tracker'), api(`/api/calendar?from=${t0}&to=${t0 + 7 * DAY}`)]);
    if (!host) return;
    const stat = (n, label, go_, bad) => h('button', { class: 'stat' + (bad && n ? ' bad' : ''), onclick: () => go(go_) }, h('b', { text: n }), h('span', { text: label }));
    const dl = (x) => h('div', { class: 'item' + (x.when.includes('overdue') ? ' overdue' : '') }, h('span', { class: 'tag', text: x.course }), h('div', { class: 'grow' }, h('div', { class: 'title' }, x.url ? h('a', { href: x.url, target: '_blank', rel: 'noopener noreferrer', text: x.title }) : x.title), h('div', { class: 'meta', text: fmtDT(x.due_ts) + ' · ' + x.when })));
    const list = (title, arr, kind) => arr.length ? [h('h2', {}, title, h('span', { class: 'n', text: arr.length })), h('div', { class: 'list' }, arr.map(dl))] : null;
    const days = []; for (let i = 0; i < 7; i++) { const day = addDays(new Date(t0 * 1000), i), a = secs(day), b = a + DAY; const its = cal.items.filter((x) => x.start >= a && x.start < b);
      days.push(h('button', { class: 'd7' + (i === 0 ? ' today' : ''), onclick: () => go('calendar') }, h('b', { text: day.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' }) }), its.slice(0, 2).map((x) => h('div', { class: 'c', text: (x.done ? '✓ ' : '') + x.title })), its.length > 2 ? h('div', { class: 'c faint', text: `+${its.length - 2} more` }) : null)); }
    const a = d.announcements;
    const text = d.text;
    clear(host).append(
      h('div', { class: 'phead' }, h('h1', { text: greet() + '.' }), h('span', { class: 'sub', text: new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) + (d.last_sync ? ' · checked ' + ago(d.last_sync) : '') }), h('span', { class: 'sp' }),
        h('button', { text: 'Copy as text', onclick: async () => { try { await navigator.clipboard.writeText(text); toast('Copied the digest'); } catch (e) { toast('Copy is not allowed here', { err: true }); } } }),
        h('button', { text: 'Mark as seen', title: 'Next time, “new” means after now', onclick: safe(async () => { await api('/api/digest/seen', {}); toast('Marked as seen'); }) })),
      d.focus.length ? [h('h2', { text: 'Do first' }), h('div', { class: 'focus' }, d.focus.map((f) => h('button', { class: 'f ' + f.level, onclick: () => go(f.go) }, h('span', { class: 'grow', text: f.text }), icon('right'))))] : h('div', { class: 'card', text: 'Nothing urgent. Deadlines are clear and everything is reviewed up to this week.' }),
      h('div', { class: 'stats' }, stat(d.overdue.length, 'overdue', 'deadlines', true), stat(d.soon.length, 'due in 7 days', 'deadlines'), stat(d.files.new + d.files.updated, 'new or updated files', 'files'), stat(a.unread, 'unread announcements', 'announcements'), stat(d.behind.length, 'weeks behind', 'tracker', true)),
      h('h2', { text: 'Next 7 days' }), h('div', { class: 'days7' }, days),
      h('div', { class: 'grid2' },
        h('div', {}, list('Overdue', d.overdue), list('Due in the next 7 days', d.soon), list('Coming up', d.later), !d.overdue.length && !d.soon.length && !d.later.length ? h('div', { class: 'empty' }, h('b', { text: 'No open deadlines' }), 'Check Moodle to look for new ones.') : null,
          d.moved.length ? [h('h2', { text: 'Moved since you last looked' }), h('div', { class: 'list' }, d.moved.map((m) => h('div', { class: 'item' }, h('div', { class: 'grow' }, h('div', { class: 'title', text: m.title }), h('div', { class: 'meta', text: (m.from ? fmtDT(m.from) : '?') + '  →  ' + (m.to ? fmtDT(m.to) : '?') })))))] : null),
        h('div', {}, h('h2', { text: 'Where you are in the term' }), tr.courses.length ? tr.courses.map((c) => h('div', { class: 'course card', style: { 'margin-bottom': '10px' } }, h('div', { class: 'ch' }, h('b', { text: c.short }), h('span', { class: 'muted', text: c.current_week && c.phase === 'running' ? 'week ' + c.current_week : c.phase === 'over' ? 'term over' : '' }), h('span', { class: 'sp' }), h('span', { class: 'num', text: c.pct + '%' })), ribbon(c, () => go('tracker'), true), c.behind ? h('div', { class: 'meta', style: { 'margin-top': '6px', color: 'var(--bad)' }, text: c.behind + ' week' + (c.behind > 1 ? 's' : '') + ' behind' }) : null)) : h('div', { class: 'empty' }, 'No tracked courses yet.'),
          a.items.length ? [h('h2', {}, 'Announcements', h('span', { class: 'n', text: a.unread + ' unread' })), a.items.slice(0, 4).map((x) => h('button', { class: 'card', style: { display: 'block', width: '100%', 'text-align': 'left', margin: '5px 0' }, onclick: () => go('announcements') }, h('b', { text: x.subject }), h('div', { class: 'meta muted', text: x.course + ' · ' + fmtDate(x.created) }), h('div', { class: 'muted', text: x.text.slice(0, 140) })))] : null)));
  }
  App.pages[''] = {
    title: 'Dashboard',
    async mount(el) {
      host = h('div', { class: 'page' }, h('div', { class: 'empty', text: 'Loading…' })); el.append(host);
      const l = (w) => { if (w === 'job' && host) draw().catch(() => {}); }; App.listeners.push(l);
      try { await draw(); } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'Could not build the digest' }), e.message)); }
      return () => { host = null; App.listeners = App.listeners.filter((x) => x !== l); };
    },
  };
})();
