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
| Files | one view, two trees: Moodle beside your folders, with an arrow from every new file to the folder it will be saved in; status filter, bulk download and ignore, drag, create folders, split by week, preview |
| AI | the note-taker, generated files, prompts, ask about notes |
| Tracker | per course a ribbon of weeks filled by reviewed/total; files with notes show *Notes ✓* |
| Calendar | month, week and agenda views of deadlines and your own events |
| Deadlines | grouped by day; tick done, hide, add a note |
| Announcements | read, mark read/unread; the activity log is under *More* |
| Study | pick a module; notes, PDFs and other files side by side in up to four tiles; Markdown with maths (KaTeX); a file creator; an assistant that reads what you hand it |
| Settings | **Connections** (Moodle, study folder, AI model, calendar feed: state and actions), then the options by section, appearance, about |

### Files
Only files you tick are downloaded; the rest stay listed as *New*. Files land in `<module>/<Category>/<file>` (Lectures, Workshops, Coursework, …; keywords
are configurable), in `Week NN/` sub-folders once three or more files of a week arrive. **A folder that is already divided into week folders takes every new file
straight into its week** (any category; a missing week folder is created and named like its siblings, `week_03` next to `week_02`). A copy you already have anywhere in
the module is recognised and not downloaded again; files you move are followed; updated files keep the old copy in `_old_versions/`.

**The arrows.** Each file still on Moodle has an arrow to the folder it will be saved in: dashed for the app's suggestion, solid for a folder you chose, one colour per
destination. Download follows the arrow. Point at a file, a week or a folder to see its arrows; the `+N` on a folder selects the files that will go there. Dragging a Moodle
file onto a folder downloads it there; *Change destination…* only moves the arrow. A downloaded file has no standing arrow (selecting it shows where it is), and the
*Arrows* list switches between every new file and the selection only. On a narrow window the panes stack and the destination is written on the row instead.
The *Show* list filters the Moodle side: to place, new, updated, ignored, deleted here, downloaded, everything. Right-click for new folder, split by week, create notes,
rename, move, delete (delete moves one file to the trash). `p` previews a PDF, image or text without saving it.

### Activity
Long actions (checking Moodle, downloads, writing notes) run in the background and never block the page: start a download, keep selecting, start another. A quiet line under
*Check Moodle* says what is running and how far it is; click it for the list, where each action can be cancelled and its log read. Downloads fetch several files at once
(*Settings > Sync*, default 4). Actions run one after the other; what you start yourself goes before a waiting check of Moodle, and a second check joins the first.
Cancelling stops at the next safe point (between files, courses or notes): files already saved stay, partial ones are removed. Quitting (the *More* menu, Ctrl+C or `kill`)
interrupts whatever runs in the same way.

### Selecting
Click a row, or use the round box that appears on hover. Shift selects a range, Ctrl/Cmd toggles one, dragging over empty space selects what it touches, Ctrl/Cmd+A selects
all, Esc clears. A bar at the bottom shows the actions that fit; right-click shows the same. On touch screens long-press selects. Bulk actions that can be undone offer *Undo*.

### Keys
`Ctrl/Cmd K` jump to a page or file · `g` then `h f i t c d a y s` go to Dashboard, Files, AI, Tracker, Calendar, Deadlines, Announcements, Activity log, Settings · `Shift R` check Moodle ·
`[` collapse the menu · `?` keyboard and mouse help. Files: `d` download, `i` ignore, `u` un-ignore, `p` quick look, `F2` rename, `m` move to, `n` new folder, `g` create notes, `r` mark reviewed.
Study: `Ctrl/Cmd S` saves the file in the focused section, `Ctrl/Cmd Enter` sends to the assistant.

### Study
Choose a module on the left; its notes, PDFs, images and text files are listed by folder. The work area has **four sections** (two rows of two), each holding a stack of
tabs; a section with nothing in it takes no room, so one file fills the area, two share it, and so on. A click opens a file as a tab of the section you are in;
**drag a file from the list, or a tab, onto the section you want** (the four targets appear while you drag); the ⧉ button or Alt-click gives a file a section of its own.
Right-click a tab or a file for the same moves without dragging. Drag the bars between sections to resize them (double-click: equal halves). The arrangement is kept
while the browser tab is open. Markdown opens typeset (*Read*), with *Edit* and *Both*. **New file** creates a note or another text file in the module (name, type, folder).
The **Assistant** panel on the right answers about what its context list shows and nothing else: the files on screen (including unsaved edits; tabs behind them are
not sent), files you add, a selection, or pasted text. Answers can be copied, inserted at the cursor, used to replace the file's text (not saved until you press Save;
*Revert* undoes it) or saved as a new note. It says where the text is sent before you send anything.

Editing is careful. A save is refused if the file changed on disk after you opened it (you can overwrite or keep editing); the previous version is copied to `<data dir>/backups/` (last 10 per file);
writes are atomic; only UTF-8 text up to 2 MB opens; symbolic links are never followed. Files made by the note-taker can be edited and are then left alone.

### Deadlines and the calendar
A sync reads assignments (due, cut-off, your submission and grade status, extensions), quiz close times and calendar events, and logs moved dates. Tick or hide each deadline; your ticks survive syncs.
The calendar feed link (Calendar page) is a URL on the running app. **Apple Calendar** subscriptions kept in iCloud are fetched by Apple's servers, which cannot reach `localhost`: subscribe under
*On My Mac*, or import a file made with `moodle-app ics --out deadlines.ics` (re-importing updates events, no duplicates).

## AI notes
Two places can reach a model, and the server refuses model requests from anywhere else: the **AI** page and the **Study** assistant. Nothing is sent until you press
Create, Ask or Send. PDFs are turned into text behind the scenes, once per version of a file, only when notes, a question or the assistant need it; that text is kept in the
app's data folder (`cache/text/`, pruned when unused for 120 days), never in your study folder.
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
| `probe` | read-only check of what your token can do (`probe_report.json`, field names only) |

## Settings
*Settings* opens on **Connections**: Moodle (source), the study folder (storage), the AI model and the calendar feed, each with its state and the actions that fit
(sign in again, test the model, add or remove an API key, copy the feed address). The Moodle token is never shown.
**Courses** lists every course Moodle has for you. A course is followed when a folder in the study folder starts with its module code; any other
(earlier years, no code) can be followed from the list, and a course you are not enrolled in can be added by its address (`…/course/view.php?id=123`) if Moodle lets you open it.
Each followed course has one folder, directly inside the study folder or inside a **year folder** (`Y1`, `Year 2`, …). Once the study folder has a year folder,
a course you follow is offered a folder in its year, taken from the level digit of its module code (CSCI.323 is year 3) or else from when it started; *Follow all* does this
for every course at once. To nest an existing setup, set the study folder to the folder that holds `Y1`, `Y2`, `Y3`: existing records follow.
`config.json` in the data folder holds the options; most are editable on the *Settings* page by section, which checks values and keeps any other keys untouched. Fields marked *restart* apply
the next time the app starts. Course-name keywords (`keywords`) and advanced model options are edited in the file.

## Security
The web interface binds to 127.0.0.1, needs a random access token, checks the Host header, resolves every path and requires it to be inside the study folder; delete moves exactly one regular file
to the trash and never follows symlinks. Announcement bodies (HTML from teachers) are reduced to plain text. The calendar feed has its own secret key in its URL.
The Moodle token is stored with mode 0600 and used only for read functions. Treat the data folder like a password store. Report security problems privately.

## Development
    python3 -m pip install -e ".[test]" && python3 -m pytest      # mock Moodle and mock model servers, no network
CI (`.github/workflows/ci.yml`) runs the unit tests on Linux, macOS and Windows and the browser tests on Linux. Changes are listed in [CHANGELOG.md](CHANGELOG.md).
Browser tests need Playwright (`pip install playwright && playwright install chromium`) and are skipped without it. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layers and
schema and [docs/ROADMAP.md](docs/ROADMAP.md) for ideas. Maths in the preview uses [KaTeX](https://katex.org) (MIT), bundled under `web/static/katex`.

## Licence
MIT, see [LICENSE](LICENSE).
