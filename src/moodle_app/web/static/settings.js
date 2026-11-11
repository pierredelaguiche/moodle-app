'use strict';
/* Settings: a menu on the left, one section at a time on the right.
   Connections = what the app is plugged into (Moodle, the study folder, the AI model, the calendar feed), each with its state
   and the actions that fit. The option groups are the important, safe fields of config.json: the server lists them (with
   checks) and leaves everything else in the file as it is. Appearance is kept in this browser. */
(function () {
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const STATE = { ok: ['check', 'ok'], warn: ['warn', 'warn'], todo: ['warn', 'todo'], off: ['plug', 'off'] };
  App.pages.settings = {
    title: 'Settings',
    async mount(el, arg) {
      const host = h('div', { class: 'page set-page' }, h('div', { class: 'empty', text: 'Loading…' }));
      el.append(host);
      let data, conn, edits = {}, section = arg || 'connections';
      const isGroup = (s) => !!data && data.fields.some((f) => f.group === s);
      const toGroup = (g) => { const el = document.getElementById('set-' + g); if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' }); };
      const pick = async (s) => { if (s === section) return; if (isGroup(s) && isGroup(section)) { section = s; history.replaceState(null, '', '#/settings/' + encodeURIComponent(s)); $$('.set-nav .navitem', host).forEach((b) => { if (b.dataset.k === s) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); }); $('.phead .sub', host).textContent = s; return toGroup(s); }
        if (Object.keys(edits).length && !(await dialog({ title: 'Leave this section?', text: 'Unsaved changes will be lost.', ok: 'Leave', danger: true }))) return; edits = {}; section = s; history.replaceState(null, '', '#/settings/' + encodeURIComponent(s)); draw(); };
      const read = (f, inp) => f.kind === 'bool' ? inp.checked : f.kind === 'list' ? inp.value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean) : inp.value;

      /* ---------- Connections */
      const reloadConn = async () => { conn = await api('/api/connections'); draw(); };
      const copy = async (text) => { try { await navigator.clipboard.writeText(text); toast('Copied', { ms: 1500 }); } catch (e) { toast('Could not copy: select the address and copy it by hand', { err: true }); } };
      function actions(c) {
        if (c.id === 'moodle') return [
          h('button', { class: c.state === 'todo' ? 'primary' : '', text: c.state === 'todo' ? 'Set up Moodle…' : 'Sign in again…', title: 'Opens the quick start: address, sign-in, token', onclick: () => go('help', 'start') }),
          h('button', { text: 'Check Moodle now', disabled: c.state === 'todo', onclick: async () => { await checkMoodle(); reloadConn(); } }),
          h('button', { text: 'Courses…', disabled: c.state === 'todo', title: 'Which courses are followed; add more', onclick: () => pick('courses') })];
        if (c.id === 'folder') return [h('button', { class: c.state === 'todo' ? 'primary' : '', text: c.state === 'todo' ? 'Choose the folder…' : 'Change…', onclick: () => pick('Folders') })];
        if (c.id === 'model') {
          const test = h('button', { text: 'Test connection', title: 'One tiny call: proves the login or key works. No course text is sent.' });
          test.onclick = async () => { test.disabled = true; try { const r = await api('/api/ai/check', {}, { 'X-Page': 'settings' }); toast('Connected: ' + r.to); } catch (e) { toast(e.message, { err: true }); } test.disabled = false; reloadConn(); };
          const key = c.api_key
            ? h('button', { class: 'danger', text: 'Remove API key', onclick: safe(async () => { if (await dialog({ title: 'Remove the API key?', text: 'The key file is deleted from the data folder. The signed-in claude command is used instead, if there is one.', ok: 'Remove', danger: true })) { await api('/api/setup/key', { key: '' }); toast('Key removed'); reloadConn(); } }) })
            : h('button', { text: 'Add API key…', onclick: safe(async () => { const f = await dialog({ title: 'Anthropic API key', text: 'Stored in a private file in the data folder (owner-only), never in the repository or the config file.', fields: [{ name: 'key', label: 'Key', type: 'password' }], ok: 'Save key' }); if (f && f.key.trim()) { await api('/api/setup/key', { key: f.key.trim() }); toast('Key saved'); reloadConn(); } }) });
          return [test, key, h('button', { text: 'Model options', onclick: () => pick('AI') })];
        }
        if (c.id === 'calendar') { const url = location.origin + c.path; return [h('button', { text: 'Copy feed address', onclick: () => copy(url) }), h('a', { class: 'btnlink', href: c.path, download: 'deadlines.ics', text: 'Download .ics' }), h('button', { text: 'Open Calendar', onclick: () => go('calendar') })]; }
        return [];
      }
      function connections() {
        const notes = {
          model: (c) => [c.claude_login ? 'Claude command found on this computer.' : 'Claude command not found.', c.api_key ? 'An API key is stored.' : 'No API key stored.'].join(' '),
          calendar: () => 'Apple Calendar: subscribe under “On My Mac” (iCloud subscriptions are fetched by Apple’s servers, which cannot reach this computer), or import the .ics file.',
        };
        return [h('p', { class: 'muted', text: 'What the app is plugged into. Only Moodle and the study folder are needed; the model and the calendar feed are optional.' }),
          conn.items.map((c) => { const [ic, cls] = STATE[c.state] || STATE.off; return h('section', { class: 'card conn s-' + cls, dataset: { id: c.id } },
            h('div', { class: 'conn-ic' }, icon(ic)),
            h('div', { class: 'conn-main' },
              h('div', { class: 'conn-head' }, h('h2', { text: c.name }), h('span', { class: 'conn-role', text: c.role }), h('span', { class: 'sp' }), h('span', { class: 'conn-state', text: c.summary })),
              c.detail.map((d) => h('div', { class: 'conn-line', text: d })), notes[c.id] ? h('div', { class: 'conn-line faint', text: notes[c.id](c) }) : null,
              h('div', { class: 'row conn-acts' }, actions(c)))); }),
          h('p', { class: 'muted sm', text: `Data folder: ${conn.home}` })];
      }

      /* ---------- Courses: every course Moodle lists for you, which ones are followed, and adding the rest */
      let courses = null;
      const loadCourses = async () => { courses = await api('/api/courses'); draw(); };
      const addCourse = safe(async (c) => {
        const f = await dialog({ title: c ? 'Follow ' + c.name : 'Add a course', text: c ? 'The course gets its own folder in your study folder (made if it does not exist). Its files then show up on the Files page.' : 'Paste the address of the course page on Moodle (…/course/view.php?id=123) or just its number. It works for any course Moodle lets you open, enrolled or not.',
          fields: [...(c ? [] : [{ name: 'ref', label: 'Course address or number' }]), { name: 'folder', label: c ? 'Folder (Y1/Name puts it in a year folder)' : 'Folder (empty: the course name; Y1/Name puts it in a year folder)', value: c ? c.suggest : '' }], ok: 'Follow' });
        if (!f || (!c && !f.ref.trim())) return;
        const r = await api('/api/courses/add', { ref: c ? String(c.id) : f.ref.trim(), folder: f.folder.trim() });
        toast(`Following ${r.name || 'course ' + r.id} in “${r.folder}”. Checking Moodle…`); Jobs.follow('Checking Moodle…', true); await loadCourses();
      });
      const dropCourse = safe(async (c) => {
        if (!(await dialog({ title: 'Stop following ' + c.name + '?', text: 'Its folder and files stay where they are. New files are no longer looked for.', ok: 'Stop following', danger: true }))) return;
        await api('/api/courses/remove', { id: c.id }); toast('No longer following ' + c.name); await loadCourses();
      });
      const followAll = safe(async (list) => {
        const lines = list.slice(0, 14).map((c) => `${c.suggest || c.name}`); if (list.length > 14) lines.push(`… and ${list.length - 14} more`);
        if (!(await dialog({ title: `Follow ${list.length} course${list.length > 1 ? 's' : ''}?`, text: 'Each gets the folder below (made if missing). Nothing is downloaded: their files appear on the Files page as new.', path: lines.join('\n'), ok: 'Follow all' }))) return;
        const r = await api('/api/courses/add-all', {});
        toast(`Following ${r.added.length} more` + (r.failed.length ? `. ${r.failed.length} could not be added: ${r.failed[0].name}: ${r.failed[0].error}` : '. Checking Moodle…'), r.failed.length ? { err: true } : {}); Jobs.follow(); await loadCourses();
      });
      function coursesView() {
        if (!courses) { loadCourses().catch((e) => toast(e.message, { err: true })); return [h('div', { class: 'empty', text: 'Loading…' })]; }
        const on = courses.courses.filter((c) => c.tracked), off = courses.courses.filter((c) => !c.tracked);
        const row = (c) => h('div', { class: 'row crs', dataset: { id: c.id } }, h('div', { class: 'grow' }, h('div', { class: 'crs-name', text: c.name }),
          h('div', { class: 'conn-line', text: c.tracked ? `Folder: ${c.folder}` + (c.added ? ' · added by you' : ' · matched by its module code') : 'Not followed: no folder in your study folder starts with its module code' })),
          c.tracked ? (c.added ? h('button', { text: 'Stop following', onclick: () => dropCourse(c) }) : null) : h('button', { class: 'primary', text: 'Follow…', onclick: () => addCourse(c) }));
        return [h('p', { class: 'muted', text: 'A course is followed when a folder starts with its module code (CSCI.323 …), in your study folder or inside a year folder (Y1, Y2, Y3). ' + (courses.years ? 'Your study folder is divided by years, so a course you follow is offered a folder in its year (from the level digit of its code, else from when it started).' : 'To sort by year, make folders named Y1, Y2, Y3 in the study folder (or follow a course into Y1/Name): courses are then offered a folder in their year.') + ' A course you are not enrolled in can be added by its address.' }),
          h('div', { class: 'row conn-acts' }, h('button', { class: 'primary', id: 'addcourse', text: 'Add a course by address…', disabled: !courses.connected, onclick: () => addCourse(null) }), off.length ? h('button', { id: 'followall', text: courses.years ? `Follow all ${off.length} in their year folders` : `Follow all ${off.length}`, disabled: !courses.connected, onclick: () => followAll(off) }) : null, h('button', { text: 'Refresh the list', disabled: !courses.connected, onclick: async () => { await checkMoodle(); loadCourses(); } })),
          h('section', { class: 'card set-card' }, h('h2', { text: `Followed (${on.length})` }), on.length ? on.map(row) : h('div', { class: 'muted', text: 'None yet.' })),
          h('section', { class: 'card set-card' }, h('h2', { text: `Other courses on Moodle (${off.length})` }), off.length ? off.map(row) : h('div', { class: 'muted', text: 'Every course Moodle lists for you is followed.' }))];
      }

      /* ---------- option groups (config.json) */
      function options(groups) {
        edits = {};
        const save = h('button', { class: 'primary', text: 'Save changes', disabled: true }), msg = h('span', { class: 'muted' });
        const dirty = () => { const n = Object.keys(edits).length; save.disabled = !n; msg.textContent = n ? `${n} unsaved` : ''; };
        save.onclick = async () => {
          save.disabled = true;
          try { const r = await api('/api/settings', { changes: edits }); data = r; toast(r.restart.length ? 'Saved. Restart the app for: ' + r.restart.map((k) => data.fields.find((f) => f.key === k).label).join(', ') : 'Saved'); edits = {}; conn = await api('/api/connections'); draw(); }
          catch (e) { toast(e.message, { err: true }); dirty(); }
        };
        return [...groups.map((group) => h('section', { class: 'card set-card', id: 'set-' + group, dataset: { group } }, h('h2', { text: group }), data.fields.filter((f) => f.group === group).map((f) => {
          let inp;
          if (f.kind === 'choice') inp = h('select', {}, f.options.map(([v, t]) => h('option', { value: v, text: t, selected: v === (f.value || '') })));
          else if (f.kind === 'bool') inp = h('input', { type: 'checkbox', checked: !!f.value });
          else if (f.kind === 'list') inp = h('textarea', { rows: 2, value: (f.value || []).join('\n') });
          else inp = h('input', { type: f.kind === 'int' || f.kind === 'float' ? 'number' : 'text', value: f.value ?? '', step: f.kind === 'float' ? '0.05' : '1', min: f.min ?? undefined, max: f.max ?? undefined });
          inp.setAttribute('aria-label', f.label);
          inp.addEventListener('input', () => { const v = read(f, inp); if (same(v, f.kind === 'list' ? f.value || [] : f.value ?? (f.kind === 'bool' ? false : ''))) delete edits[f.key]; else edits[f.key] = v; dirty(); });
          return h('label', { class: 'set-row' }, h('span', { class: 'set-label' }, f.label, f.restart ? h('span', { class: 'stt', title: 'Takes effect the next time the app starts', text: 'restart' }) : null),
            inp, f.help ? h('span', { class: 'muted set-help', text: f.help }) : null);
        }))), h('div', { class: 'row set-save' }, save, msg)];
      }
      const appearance = () => [h('section', { class: 'card set-card' },
        h('label', { class: 'set-row' }, h('span', { class: 'set-label', text: 'Theme' }),
          h('select', { 'aria-label': 'Theme', onchange: (e) => setTheme(e.target.value) }, [['system', 'Follow the system'], ['light', 'Light'], ['dark', 'Dark']].map(([v, t]) => h('option', { value: v, text: t, selected: v === getTheme() }))),
          h('span', { class: 'muted set-help', text: 'Applies at once. Kept in this browser only.' })),
        h('label', { class: 'set-row' }, h('span', { class: 'set-label', text: 'Menu' }),
          h('select', { 'aria-label': 'Menu', onchange: (e) => { if ((e.target.value === 'c') !== $('#app').classList.contains('collapsed')) toggleSide(); } }, [['o', 'Icons and names'], ['c', 'Icons only']].map(([v, t]) => h('option', { value: v, text: t, selected: (v === 'c') === $('#app').classList.contains('collapsed') }))),
          h('span', { class: 'muted set-help', text: 'The [ key switches it too.' })))];
      const about = () => [h('section', { class: 'card set-card' },
        h('div', { class: 'conn-line', text: `Settings file: ${data.config_path}` }), h('div', { class: 'conn-line', text: `Data folder: ${data.home}` }),
        h('div', { class: 'conn-line muted', text: 'Options not shown here (course keywords, advanced model options) stay in the settings file and are never touched by this page.' }),
        h('div', { class: 'row conn-acts' }, h('button', { text: 'Activity log', onclick: () => go('history') }), h('button', { text: 'Help and shortcuts', onclick: () => help('keys') }),
          h('button', { class: 'danger', text: 'Stop Moodle App', onclick: stopApp })))];

      function draw() {
        const groups = [...new Set(data.fields.map((f) => f.group))];
        const menu = [['connections', 'Connections', 'plug'], ['courses', 'Courses', 'study'], ...groups.map((g) => [g, g, 'settings']), ['appearance', 'Appearance', 'sun'], ['about', 'About', 'help']];
        if (!menu.some((m) => m[0] === section)) section = 'connections';
        const title = menu.find((m) => m[0] === section)[1];
        const todo = conn.items.filter((c) => c.state === 'todo' || c.state === 'warn').length;
        clear(host).append(h('div', { class: 'phead' }, h('h1', { text: 'Settings' }), h('span', { class: 'sub', text: title })),
          h('div', { class: 'set-grid' },
            h('nav', { class: 'set-nav', 'aria-label': 'Settings sections' }, menu.map(([k, t, ic]) => h('button', { class: 'navitem', 'aria-current': k === section ? 'page' : null, dataset: { k }, onclick: () => pick(k) }, icon(ic), h('span', { class: 't', text: t }), k === 'connections' && todo ? h('span', { class: 'pill hot', text: todo }) : null))),
            h('div', { class: 'set-body' }, section === 'connections' ? connections() : section === 'courses' ? coursesView() : section === 'appearance' ? appearance() : section === 'about' ? about() : options(groups))));
        if (isGroup(section)) requestAnimationFrame(() => { const el = document.getElementById('set-' + section); if (el) el.scrollIntoView({ block: 'start' }); });
      }
      try { [data, conn] = await Promise.all([api('/api/settings'), api('/api/connections')]); draw(); } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'Settings could not load' }), e.message)); }
      return () => {};
    },
  };
})();
