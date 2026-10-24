'use strict';
/* Settings: the important, safe options of config.json. The server lists the fields (with checks); everything else in the file
   is left as it is. Changes apply at once unless the field says it needs a restart. */
(function () {
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  App.pages.settings = {
    title: 'Settings',
    async mount(el) {
      const host = h('div', { class: 'page set-page' }, h('div', { class: 'empty', text: 'Loading…' }));
      el.append(host);
      let data, edits = {};
      const read = (f, inp) => f.kind === 'bool' ? inp.checked : f.kind === 'list' ? inp.value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean) : inp.value;
      function draw() {
        edits = {};
        const save = h('button', { class: 'primary', text: 'Save changes', disabled: true });
        const msg = h('span', { class: 'muted' });
        const dirty = () => { save.disabled = !Object.keys(edits).length; msg.textContent = Object.keys(edits).length ? `${Object.keys(edits).length} unsaved` : ''; };
        const groups = [...new Set(data.fields.map((f) => f.group))];
        clear(host).append(
          h('h1', { text: 'Settings' }),
          h('p', { class: 'muted', text: 'The options you are likely to change. Anything else (course keywords, advanced model options) stays in the file and is never touched by this page.' }),
          h('section', { class: 'card set-card' }, h('h2', { text: 'Appearance' }),
            h('label', { class: 'set-row' }, h('span', { class: 'set-label', text: 'Theme' }),
              h('select', { 'aria-label': 'Theme', onchange: (e) => setTheme(e.target.value) }, [['system', 'Follow the system'], ['light', 'Light'], ['dark', 'Dark']].map(([v, t]) => h('option', { value: v, text: t, selected: v === getTheme() }))),
              h('span', { class: 'muted set-help', text: 'Applies at once. Kept in this browser only.' }))),
          ...groups.map((g) => h('section', { class: 'card set-card' }, h('h2', { text: g }),
            data.fields.filter((f) => f.group === g).map((f) => {
              let inp;
              if (f.kind === 'choice') inp = h('select', {}, f.options.map(([v, t]) => h('option', { value: v, text: t, selected: v === (f.value || '') })));
              else if (f.kind === 'bool') inp = h('input', { type: 'checkbox', checked: !!f.value });
              else if (f.kind === 'list') inp = h('textarea', { rows: 2, value: (f.value || []).join('\n') });
              else inp = h('input', { type: f.kind === 'int' || f.kind === 'float' ? 'number' : 'text', value: f.value ?? '', step: f.kind === 'float' ? '0.05' : '1', min: f.min ?? undefined, max: f.max ?? undefined });
              inp.setAttribute('aria-label', f.label);
              inp.addEventListener('input', () => { const v = read(f, inp); if (same(v, f.kind === 'list' ? f.value || [] : f.value ?? (f.kind === 'bool' ? false : ''))) delete edits[f.key]; else edits[f.key] = v; dirty(); });
              return h('label', { class: 'set-row' }, h('span', { class: 'set-label' }, f.label, f.restart ? h('span', { class: 'badge', title: 'Takes effect the next time the app starts', text: 'restart' }) : null),
                inp, f.help ? h('span', { class: 'muted set-help', text: f.help }) : null);
            }))),
          h('div', { class: 'row' }, save, msg),
          h('p', { class: 'muted', text: `File: ${data.config_path} · data folder: ${data.home}` }));
        save.onclick = async () => {
          save.disabled = true;
          try {
            const r = await api('/api/settings', { changes: edits });
            data = r;
            toast(r.restart.length ? 'Saved. Restart the app for: ' + r.restart.map((k) => data.fields.find((f) => f.key === k).label).join(', ') : 'Saved');
            draw();
          } catch (e) { toast(e.message, { err: true }); dirty(); }
        };
      }
      try { data = await api('/api/settings'); draw(); } catch (e) { clear(host).append(h('div', { class: 'empty' }, h('b', { text: 'Settings could not load' }), e.message)); }
      return () => {};
    },
  };
})();
