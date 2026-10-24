# moodle-app

A local companion for Moodle. It pulls your course files, deadlines and announcements into your own folder tree, keeps a record of
everything in a SQLite database, and, only when you ask, writes study notes with a Claude model. It runs on your computer with
the Python standard library (plus `pymupdf` for reading PDFs). Your Moodle token and course files go nowhere except the model you choose
for notes.

It only ever **reads** from Moodle. Built and tested for Moodle 4.x; see [docs/CAPABILITIES.md](docs/CAPABILITIES.md) for the web service functions it uses.

## Install and start
Requires Python 3.9 or newer.

    git clone <this repository> && cd moodle-app
    python3 -m pip install -e ".[pdf]"      # pymupdf is only needed to read PDFs (notes, text extraction)
    moodle-app serve

`serve` opens the app in your browser. On a first run it opens **Help**, whose quick start does the setup in four steps:

1. **Say where things are**: your Moodle address and the study folder (one folder per module).
2. **Sign in to Moodle**: log in on the page it opens, copy the `moodlemobile://token=…` address Moodle redirects to, paste it. The token is stored in a private file (mode 0600).
3. **Fetch your files**: press *Check Moodle*, then pick files on the Files page.
4. **Optional, AI notes**: use the signed-in `claude` command (Claude Code; no key, uses your plan) or paste an Anthropic API key.

Everything the quick start does can also be done in a terminal (`moodle-app init`, `token`, `courses`, below).
The address of the page, with its access token, is printed when the app starts and stays the same between runs, so you can bookmark it.
Change how the app opens (new tab, new window, or nothing) in *Settings*.

Data lives in a per-user folder (override with `MOODLE_APP_HOME` or `--home`): `~/Library/Application Support/moodle-app` on macOS,
`~/.local/share/moodle-app` on Linux, `%APPDATA%\moodle-app` on Windows. It holds `config.json`, `token`, `moodle_app.db`, caches and the trash.
None of it belongs in a git repository (the provided `.gitignore` covers the common names).

## Using the app
A menu on the left; **Help** (bottom left) has this guide, the quick start and the keyboard and mouse reference.

| Page | What it is for |
|---|---|
| Dashboard | what to do first: counts, the next 7 days, deadlines, term progress, announcements. "Copy as text" and "Mark as seen" |
| Files | **Smart allocation** (new / updated / ignored files per folder, bulk download and ignore) and **Organise** (Moodle tree beside your folders: drag, create folders, split by week, preview, copy PDF text) |
| AI | read PDFs to text, the note-taker, generated files, prompts, ask about notes |
| Tracker | per course a ribbon of weeks filled by reviewed/total; files with notes show *Notes ✓* |
| Calendar | month, week and agenda views of deadlines and your own events |
| Deadlines | grouped by day; tick done, hide, add a note |
| Announcements | read, mark read/unread; the activity log is under *More* |
| Editor | tabs; text files, Markdown preview with maths (KaTeX), PDF and image viewer |
| Settings | theme, how the app opens, folders, sync, hidden folders, model options |

### Files
Only files you tick are downloaded; the rest stay listed as *New*. Files land in `<module>/<Category>/<file>` (Lectures, Workshops, Coursework, …; keywords
are configurable), in `Week NN/` sub-folders once three or more files of a week arrive. A copy you already have anywhere in the module is recognised and not downloaded
again; files you move are followed; updated files keep the old copy in `_old_versions/`. In **Organise**, a not-yet-downloaded file shows in its planned folder as a dashed
"to come" row; dragging a Moodle file onto a folder downloads it there. Right-click for new folder, split by week, extract text, create notes, rename, move, delete
(delete moves one file to the trash). `p` previews a PDF, image or text without saving it.

### Selecting
Click a row, or use the round box that appears on hover. Shift selects a range, Ctrl/Cmd toggles one, dragging over empty space selects what it touches, Ctrl/Cmd+A selects
all, Esc clears. A bar at the bottom shows the actions that fit; right-click shows the same. On touch screens long-press selects. Bulk actions that can be undone offer *Undo*.

### Keys
`Ctrl/Cmd K` jump to a page or file · `g` then `h f i t c d a y s` go to Dashboard, Files, AI, Tracker, Calendar, Deadlines, Announcements, Activity log, Settings · `Shift R` check Moodle ·
`[` collapse the menu · `?` keyboard and mouse help. Files: `d` download, `i` ignore, `u` un-ignore, `p` quick look. Organise: `F2` rename, `m` move to, `t` PDF text, `n` new folder.
Editor: `Ctrl/Cmd S` saves.

### The editor is careful
A save is refused if the file changed on disk after you opened it (you can overwrite or keep editing); the previous version is copied to `<data dir>/backups/` (last 10 per file);
writes are atomic; only UTF-8 text up to 2 MB opens; symbolic links are never followed. Files made by the note-taker can be edited and are then left alone.

### Deadlines and the calendar
A sync reads assignments (due, cut-off, your submission and grade status, extensions), quiz close times and calendar events, and logs moved dates. Tick or hide each deadline; your ticks survive syncs.
The calendar feed link (Calendar page) is a URL on the running app. **Apple Calendar** subscriptions kept in iCloud are fetched by Apple's servers, which cannot reach `localhost`: subscribe under
*On My Mac*, or import a file made with `moodle-app ics --out deadlines.ics` (re-importing updates events, no duplicates).

## AI notes
The AI page is the only part that can reach a model, and the server refuses model requests from any other page.
- **Read** turns PDFs into text files (no model). Texts live in a hidden `<course>/.formatted txt/` per course; any folder can be read.
- **Note-taker** shows an editable plan with a token estimate first. Scope: one module or everything in the study folder. Kinds: lecture notes, workshop summaries and code stubs, coursework notes (requirements, marking, deadlines), a study guide per course.
  Files you edited are never overwritten. Deadlines found in coursework notes are added to the Calendar once each; remove them under *Check generated files*.
- **Prompts** can be edited (saved in `<data dir>/prompts/`, *Reset* returns the built-in).
- **Ask** answers a question about the notes or files you tick, optionally limited to a module.

Notes are written under `<module>/Notes/`. With the signed-in `claude` command, the usage windows Claude reports (5-hour, 7-day) are shown, and the app refuses to start a run past
`llm.stop_at` (default 80%) and stops a running batch when Claude says a limit is reached. With an API key (`ANTHROPIC_API_KEY`, `LLM_API_KEY`, or a key saved from Help), calls are billed per use by the provider and the key is used before the signed-in command.
`model` is `haiku` by default (small and cheap), or `sonnet`, `opus`, or a full model id. Other providers: `{"provider": "openai_compat", "model": "...", "base_url": "http://localhost:11434/v1"}` (a localhost URL keeps text on your machine).
Check your institution's rules on third-party AI before sending course material to a remote model.

## Commands
| Command | What it does |
|---|---|
| `serve [--port N] [--open tab\|window\|none]` | the web interface |
| `init`, `token`, `courses`, `status` | terminal setup and inspection |
| `sync [--only files,deadlines,announcements] [--course X] [--if-due]` | ask Moodle what changed and record it |
| `pending`, `download --all` | list / download new files |
| `deadlines [--all]`, `announcements [--unread --full]`, `history`, `digest` | read what the last sync found |
| `ics [--out FILE]` | deadlines as an iCalendar file (stable UIDs) |
| `audit [--fix]` | files in the wrong week or category folder |
| `llm-check` | test the model connection (and where it looked for a key) |
| `notes MODULE [--what lectures,summaries,code,coursework,guide,all] [--week N] [--dry-run] [--yes] [--force]` | study material from the terminal, with the same token guard |
| `read FOLDER` | PDFs to text (per course by default) |
| `probe` | read-only check of what your token can do (`probe_report.json`, field names only) |

## Settings
`config.json` in the data folder; most options are editable on the *Settings* page, which checks values and keeps any other keys untouched. Fields marked *restart* apply
the next time the app starts. Course-name keywords (`keywords`) and advanced model options are edited in the file.

## Security
The web interface binds to 127.0.0.1, needs a random access token, checks the Host header, resolves every path and requires it to be inside the study folder; delete moves exactly one regular file
to the trash and never follows symlinks. Announcement bodies (HTML from teachers) are reduced to plain text. The calendar feed has its own secret key in its URL.
The Moodle token is stored with mode 0600 and used only for read functions. Treat the data folder like a password store. Report security problems privately.

## Development
    python3 -m pip install -e ".[test]" && python3 -m pytest      # mock Moodle and mock model servers, no network
Browser tests need Playwright (`pip install playwright && playwright install chromium`) and are skipped without it. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layers and
schema and [docs/ROADMAP.md](docs/ROADMAP.md) for ideas. Maths in the preview uses [KaTeX](https://katex.org) (MIT), bundled under `web/static/katex`.

## Licence
MIT, see [LICENSE](LICENSE).
