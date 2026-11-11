'use strict';
/* Help: a quick start that does the first-run setup, then the usage guide, then keyboard and mouse. */
(function () {
  const KEYS = [
    ['Ctrl/Cmd K', 'Jump to a page or file'],
    ['g then h f i t c d a y s', 'Go to Dashboard, Files, AI, Tracker, Calendar, Deadlines, Announcements, Activity log, Settings'],
    ['Shift R', 'Check Moodle'], ['[', 'Collapse or expand the menu'], ['?', 'Open this guide at the keyboard and mouse section'],
    ['Click · Ctrl/Cmd click · Shift click', 'Select one · toggle one · select a range'],
    ['Drag on empty space', 'Select everything the box touches (add with Ctrl/Cmd or Shift)'],
    ['Arrows · Shift+arrows · Space · Ctrl/Cmd A · Esc', 'Move · extend · toggle · select all · clear'],
    ['Files: d i u p', 'Download · ignore · un-ignore · preview without saving'],
    ['Your folders: drag · F2 · m · n · Delete', 'Move · rename · move to… · new folder · delete'],
    ['Shift+click twice', 'Moves the end of the range; earlier selections stay'],
    ['Right click', 'Actions for the selection (new folder, create notes, rename, move, delete…)'],
    ['Touch: long press · swipe', 'Select · swipe a deadline right for done, left to hide'],
    ['Study: Ctrl/Cmd S', 'Save the file in the focused section (refused if the file changed on disk meanwhile)'],
    ['Study: Ctrl/Cmd Enter', 'Send the message to the assistant'],
  ];
  const P = (...c) => h('p', {}, ...c);
  const step = (n, ok, title, ...body) => h('section', { class: 'card gd-step' + (ok ? ' done' : '') },
    h('h3', {}, h('span', { class: 'gd-n', text: ok ? '✓' : String(n) }), title), ...body);
  const code = (t) => h('code', { text: t });
  const field = (label, input, hint) => h('label', { class: 'set-row' }, h('span', { class: 'set-label', text: label }), input, hint ? h('span', { class: 'muted set-help', text: hint }) : null);
  const busy = async (btn, fn) => { btn.disabled = true; try { await fn(); } catch (e) { toast(e.message, { err: true }); } finally { btn.disabled = false; } };

  App.pages.help = {
    title: 'Help',
    async mount(el, arg) {
      const host = h('div', { class: 'page gd' }); el.append(host);
      let st = await api('/api/setup');
      let loginUrl = null;

      async function draw() {
        const site = h('input', { type: 'text', value: st.site_url, placeholder: 'https://moodle.your-university.edu', 'aria-label': 'Moodle address' });
        const root = h('input', { type: 'text', value: st.dest_root, placeholder: '/Users/you/Documents/University', 'aria-label': 'Study folder' });
        const savePlaces = h('button', { class: 'primary', text: 'Save', onclick: () => busy(savePlaces, async () => { st = await api('/api/setup/places', { site_url: site.value, dest_root: root.value }); toast('Saved'); draw(); }) });
        const pasted = h('textarea', { rows: 2, placeholder: 'moodlemobile://token=…', 'aria-label': 'Address Moodle sent you to' });
        const open = h('button', { text: loginUrl ? 'Open the sign-in again' : 'Open Moodle sign-in', disabled: !st.site, onclick: () => busy(open, async () => { loginUrl = (await api('/api/setup/begin', {})).url; window.open(loginUrl, '_blank', 'noopener'); draw(); }) });
        const connect = h('button', { class: 'primary', text: 'Connect', disabled: !loginUrl, onclick: () => busy(connect, async () => { const r = await api('/api/setup/token', { pasted: pasted.value }); st = r; loginUrl = null; toast('Connected' + (r.user ? ' as ' + r.user : '')); draw(); }) });
        const key = h('input', { type: 'password', autocomplete: 'off', placeholder: 'sk-ant-…', 'aria-label': 'Anthropic API key' });
        const saveKey = h('button', { text: 'Save key', onclick: () => busy(saveKey, async () => { st = await api('/api/setup/key', { key: key.value }); toast('Key saved'); draw(); }) });
        const dropKey = h('button', { class: 'ghost', text: 'Remove key', onclick: () => busy(dropKey, async () => { st = await api('/api/setup/key', { key: '' }); toast('Key removed'); draw(); }) });

        clear(host).append(
          h('h1', { text: 'Help' }),
          h('nav', { class: 'gd-toc muted' }, ...[['start', 'Quick start'], ['files', 'Files'], ['study', 'Study'], ['planner', 'Tracker, calendar, deadlines'], ['ai', 'AI notes'], ['keys', 'Keyboard and mouse'], ['trouble', 'If something goes wrong']].map(([id, t]) => h('a', { href: '#/help/' + id, text: t }))),

          h('h2', { id: 'start', text: 'Quick start' }),
          P('Everything stays on your computer. The app only reads from Moodle; it never changes anything there. Three steps get you going; the fourth is optional.'),
          step(1, st.site && st.root_ok, 'Say where things are',
            field('Moodle address', site, 'The address you type to log in to Moodle, without anything after the site name.'),
            field('Study folder', root, 'A folder on this computer that will hold one folder per module. It must already exist.'),
            h('div', { class: 'row' }, savePlaces, !st.root_ok && st.dest_root ? h('span', { class: 'err', text: 'That folder does not exist.' }) : null)),
          step(2, st.token && st.connected, 'Sign in to Moodle',
            P('Moodle hands the app a personal token instead of your password. It is stored in a private file and used only to read your courses.'),
            h('ol', { class: 'gd-ol' },
              h('li', {}, 'Press ', h('b', { text: 'Open Moodle sign-in' }), ' and log in to Moodle in the tab that opens.'),
              h('li', {}, 'Moodle then tries to open an address starting ', code('moodlemobile://token='), '. Your browser cannot open it, and that is expected. Copy that address: if the page shows an "Open in app" or "Go" link, right-click it and copy the link address. Otherwise open the browser developer tools (Network tab, tick "Preserve log") before logging in, click the ', code('launch.php'), ' request and copy the ', code('location'), ' response header.'),
              h('li', {}, 'Paste it below and press ', h('b', { text: 'Connect' }), '.')),
            h('div', { class: 'row' }, open), pasted, h('div', { class: 'row' }, connect, st.token ? h('span', { class: 'muted', text: 'A token is saved. Repeat these steps to replace it.' }) : null)),
          step(3, false, 'Fetch your files',
            P('Press ', h('b', { text: 'Check Moodle' }), ' (bottom left, or Shift R). The app asks Moodle for your courses, files, deadlines and announcements. Nothing is downloaded yet.'),
            P('Open ', h('a', { href: '#/files', text: 'Files' }), ', tick the files you want and press ', h('b', { text: 'Download' }), '. See the Files section below for how the folders are chosen.'),
            h('div', { class: 'row' }, h('button', { text: 'Check Moodle now', disabled: !st.connected, onclick: () => checkMoodle() }), h('a', { class: 'btnlink', href: '#/files', text: 'Go to Files' }))),
          step(4, st.claude_login || st.api_key, 'Optional: turn on AI notes',
            P('Notes are written by a Claude model, only when you ask on the ', h('a', { href: '#/ai', text: 'AI' }), ' page. Choose one:'),
            h('ul', {}, h('li', {}, h('b', { text: 'Your Claude subscription (no key). ' }), 'Install Claude Code, run ', code('claude'), ' once in a terminal and sign in. The app finds it and uses your plan, with its limits shown on the AI page. ', st.claude_login ? h('b', { text: 'Found on this computer.' }) : h('span', { class: 'muted', text: 'Not found on this computer yet.' })),
              h('li', {}, h('b', { text: 'An Anthropic API key. ' }), 'Create one in the Anthropic Console. It is billed per use by Anthropic and, when present, is used before the subscription. Paste it here or set ', code('ANTHROPIC_API_KEY'), '.')),
            h('div', { class: 'row' }, key, saveKey, st.api_key ? dropKey : null), st.api_key ? h('p', { class: 'muted', text: 'A key is in use. Press Check connection on the AI page to test it.' }) : null),

          h('h2', { id: 'files', text: 'Files: downloading from Moodle' }),
          P('Moodle files are only listed until you pick them: nothing is downloaded automatically. Downloads run in the background, several files at once: keep working, start more, or cancel one from the activity line under Check Moodle.'),
          P('One view, two trees: Moodle files by course and week on the left, your folders on the right. An arrow runs from every file still on Moodle to the folder it will be saved in. A dashed arrow is the suggestion; a solid one is a folder you chose. Point at a file, a week or a folder to see its arrows; the +N on a folder selects the files that will go there.'),
          P('Download follows the arrow. To send a file elsewhere, drag it onto a folder (downloads there) or right-click and choose Change destination (only moves the arrow). A folder that is already divided into week folders receives new files straight in their week. The Show list at the top of the Moodle side filters by state: to place, new, updated, ignored, deleted here, downloaded.'),
          P('Files land in ', code('<module>/<Category>/'), ' (Lectures, Workshops, Coursework…), in ', code('Week NN'), ' sub-folders once three or more files of a week arrive. A copy you already have anywhere in the module is recognised and not downloaded again; files you move are followed; updated files keep the old copy in ', code('_old_versions'), '. Ignored files stop appearing as New.'),

          h('h2', { id: 'organise', text: 'Organising and previewing' }),
          P('Right-click a file or folder in your folders for new folder, split into weeks, create notes, rename, move and delete. Delete moves the file to a trash folder. Press ', h('kbd', { text: 'p' }), ' to preview a PDF, image or text without saving it.'),

          h('h2', { id: 'study', text: 'Study: notes, PDFs and the assistant' }),
          P('Pick a module on the left. The work area has four sections, two rows of two, each with its own tabs; a section with nothing in it takes no room. A click opens a file as a tab where you are. Drag a file from the list, or a tab, onto the section you want (the four targets show while you drag), or Alt-click to give a file a section of its own. Drag the bars between sections to resize; right-click a tab for the same moves without dragging.'),
          P('New file creates a note, text, Python or LaTeX file in the module. The Assistant on the right reads only what its context line shows: the files on screen (with unsaved edits), files you add, a selection, or text you paste. Its answers can be copied, inserted at the cursor, used to replace the text (Revert undoes it until you save) or saved as a new note.'),

          h('h2', { id: 'planner', text: 'Tracker, calendar and deadlines' }),
          P('Tracker shows each course as a ribbon of weeks; tick files as reviewed. Deadlines come from assignments, quizzes and Moodle calendar events; tick them done or hide them. Calendar shows deadlines and your own events (double-click a day to add one). Deadlines found in coursework notes are added to the Calendar for you.'),

          h('h2', { id: 'ai', text: 'AI notes' }),
          P('Two places can reach a model, and only when you press the button: the AI page and the Study assistant. On the AI page, the ', h('b', { text: 'Note-taker' }), ' shows a plan with a token estimate first; untick what you do not want, then Create. Scope can be one module or everything in the study folder. Files you edited are never overwritten. ', h('b', { text: 'Ask' }), ' answers a question about the notes or files you tick. ', h('b', { text: 'Prompts' }), ' edits what the note-taker sends. Check your institution\'s rules on third-party AI before sending course material to a remote model.'),
          P('PDFs are turned into text once, behind the scenes, only when notes or a question need them; that text is kept in the app\'s data folder, not in your study folder. Notes go under ', code('<module>/Notes'), '. Use Check generated files on the AI page to see them.'),

          h('h2', { id: 'keys', text: 'Keyboard and mouse' }),
          P('Selection works the same on every list: click a row, or use the round box that appears on hover. Shift selects a range, Ctrl/Cmd toggles one, dragging over empty space selects what it touches.'),
          h('div', { class: 'gd-keys' }, KEYS.map(([k, v]) => h('div', { class: 'row' }, h('kbd', { text: k }), h('span', { class: 'muted', text: v })))),

          h('h2', { id: 'trouble', text: 'If something goes wrong' }),
          h('ul', {}, h('li', { text: 'Moodle says the token is invalid or expired: repeat step 2 above.' }),
            h('li', { text: 'No files show up: check that the study folder has a folder whose name contains the module code; unmatched courses are listed on the Files page.' }),
            h('li', { text: 'AI says it cannot connect: use Check connection on the AI page; it tells you whether the claude command and a key were found.' }),
            h('li', { text: 'The page does not open: the address (with its access token) is printed in the terminal each time you start the app; bookmark it. Change how it opens in Settings.' })),
          P('Settings holds the theme and the options you are likely to change.'));
        if (arg) { const t = document.getElementById(arg); if (t) t.scrollIntoView(); }
      }
      draw();
      return () => {};
    },
  };
})();
