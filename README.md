# moodle-app

A local companion for Moodle. It pulls your course files, deadlines and announcements into your own folder tree, keeps a record of
everything in a SQLite database, and, only when you ask, writes study notes with a Claude model. It runs on your computer with
the Python standard library (plus `pymupdf` for reading PDFs). Your Moodle token and course files go nowhere except the model you choose
for notes.

It only ever **reads** from Moodle. Built and tested for Moodle 4.x; see [docs/CAPABILITIES.md](docs/CAPABILITIES.md) for the web service functions it uses.

## Install and start
Requires Python 3.9 or newer ([python.org](https://www.python.org/downloads/); on Windows tick *Add python.exe to PATH* in the installer).

1. **Download** this repository: `git clone <this repository>`, or *Code > Download ZIP* on GitHub and unzip it.
2. **Start** it with the launcher, which is the same on every system:

   | System | Double-click | Or from a terminal, in the project folder |
   |---|---|---|
   | macOS | `start.command` | `python3 start.py` |
   | Windows | `start.bat` | `py start.py` (PowerShell or cmd) |
   | Linux | `start.command` (make it executable first) | `python3 start.py` |

   The first run creates a private `.venv` inside the folder and installs the app into it (`pymupdf` for reading PDFs is included, and skipped
   if it cannot be installed). Later runs start at once, and the launcher installs again by itself when `pyproject.toml` changes.
   It runs `python -m moodle_app serve`, so it does not need the `moodle-app` command on your PATH (on Windows it often is not).
   Extra arguments go to `serve`, e.g. `py start.py --port 9000 --open none`.

   On macOS, if double-clicking `start.command` is blocked, right-click it and choose *Open* once (or run `chmod +x start.command`, which a ZIP download needs).

To install it yourself instead: `python3 -m pip install -e ".[pdf]"` and then `moodle-app serve`.

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
| AI | create notes (per week or per category) and study guides, chat with the course, the library of what was generated, prompts |
| Tracker | per course a ribbon of weeks filled by reviewed/total; a file's week is the one its folder gives it (see *Files*); files with notes show *Notes ✓* |
| Calendar | month, week and agenda views of deadlines and your own events |
| Deadlines | grouped by day; tick done, hide, add a note |
| Announcements | read, mark read/unread; the activity log is under *More* |
| Study | pick a module; notes, PDFs and other files side by side in up to four tiles; Markdown with maths (KaTeX); a file creator; an assistant that reads what you hand it |
| Chat | Claude Code working in a folder you pick (a module, the study folder, or any project folder you add), like the chat in a code editor |
| Settings | **Connections** (Moodle, study folder, AI model, calendar feed: state and actions), then the options by section, appearance, about |

### Files
Only files you tick are downloaded; the rest stay listed as *New*. Files land in `<module>/<Category>/<file>` (Lectures, Workshops, Coursework, …; keywords
are configurable), in `Week NN/` sub-folders once three or more files of a week arrive. A folder already divided into week folders takes every new file straight
into its week (a missing one is created, named like its siblings). A copy you already have in the module is not downloaded again; files you move are followed;
updated files keep the old copy in `_old_versions/`.

**Which week?** A week in the file or activity name (`Week 3`, `w03Lab`) wins over Moodle's section, since lecturers often upload into the wrong one; then a
leading lecture number (`03_…`), the section name, the section number. Once a file sits in a `Week N` folder, that folder decides, also for the Tracker and notes.

**The arrows.** Each file still on Moodle has an arrow to the folder it will be saved in (dashed: suggested, solid: your choice, one colour per destination).
Download follows the arrow. Drag a Moodle file onto a folder to download it there; *Change destination…* only moves the arrow; the `+N` on a folder selects what will
go there. *Arrows* shows every new file or the selection only; on a narrow window the destination is written on the row instead.
*Show* filters the Moodle side. Right-click for new folder, split by week, create notes, rename, move, delete (one file, to the trash). `p` previews without saving.
Folders are listed in natural order (`Week 2` before `Week 10`), in the trees and in *Download to…* / *Move to…*.

### Activity
Long actions (checking Moodle, downloads, notes) run in the background; the page stays usable. The line under *Check Moodle* shows what runs; click it to cancel or
read a log. Actions queue (yours before a waiting check of Moodle); downloads fetch several files at once (*Settings > Sync*, default 4). Cancelling or quitting stops at
the next safe point: saved files stay, partial ones are removed.

### Selecting
Click a row, or use the round box that appears on hover. Shift selects a range, Ctrl/Cmd toggles one, dragging over empty space selects what it touches, Ctrl/Cmd+A selects
all, Esc clears. A bar at the bottom shows the actions that fit; right-click shows the same. On touch screens long-press selects. Bulk actions that can be undone offer *Undo*.

### Keys
`Ctrl/Cmd K` jump to a page or file · `g` then `h f i t c d a y k s` go to Dashboard, Files, AI, Tracker, Calendar, Deadlines, Announcements, Activity log, Chat, Settings · `Shift R` check Moodle ·
`[` collapse the menu · `?` keyboard and mouse help. Files: `d` download, `i` ignore, `u` un-ignore, `p` quick look, `F2` rename, `m` move to, `n` new folder, `g` create notes, `r` mark reviewed.
Study: `Ctrl/Cmd S` saves the file in the focused section, `Ctrl/Cmd Enter` sends to the assistant.

### Study
Pick a module; its files are listed by folder. The work area has **four sections** (two rows of two), each a stack of tabs; empty sections take no room.
Click opens a file in the current section; drag a file or tab onto another section (or Alt-click / ⧉ for a section of its own; right-click does the same without
dragging). Drag the bars to resize (double-click resets). Markdown opens typeset, with *Edit* and *Both*. **New file** creates a note or text file in the module.
Above the assistant, a **quick menu** fits what is open: *Notes for week 3*, *Lectures, all weeks*, *Study guide* (each shows the plan, the model and
where the text goes before anything is sent, then opens what it wrote), *+ Module notes* (every note of the module as context, cut to fit) and *Open in AI tab*.
The **Assistant** answers only about what its context list shows: the files on screen (with unsaved edits), files you add, a selection or pasted text. Answers can be
copied, inserted, used to replace the text (not saved until you press Save) or saved as a note. Its header names the model and where the text
goes, and the first message to a model asks once.

Editing is careful. A save is refused if the file changed on disk after you opened it (you can overwrite or keep editing); the previous version is copied to `<data dir>/backups/` (last 10 per file);
writes are atomic; only UTF-8 text up to 2 MB opens; symbolic links are never followed. Files made by the note-taker can be edited and are then left alone.

### Deadlines and the calendar
A sync reads assignments (due, cut-off, your submission and grade status, extensions), quiz close times and calendar events, and logs moved dates. Tick or hide each deadline; your ticks survive syncs.
The calendar feed link (Calendar page) is a URL on the running app. **Apple Calendar** subscriptions kept in iCloud are fetched by Apple's servers, which cannot reach `localhost`: subscribe under
*On My Mac*, or import a file made with `moodle-app ics --out deadlines.ics` (re-importing updates events, no duplicates).

### Chat
Pick a **project**: a module folder, the study folder, or any folder you add (not your home folder or the disk root). Each message runs the signed-in `claude`
command (Claude Code) inside that folder; the answer streams, and every file it read is a line you can open. Conversations continue (`--resume`) and are saved in
`<data dir>/chat/`. Per conversation choose the model and what Claude may do: *Read files* (default), *Read and edit files*, or *Edit files and run commands*
(no confirmation: use it on a folder you have a copy of, e.g. a git repository). Anything else is refused. *Stop* keeps what arrived. Needs Claude Code; an API key
alone does not run it.

## AI notes
Two places can reach a model, and the server refuses model requests from anywhere else: the **AI** page and the **Study** assistant. Nothing is sent until you press
Create, Ask or Send. PDFs are turned into text behind the scenes, once per version of a file, only when notes, a question or the assistant need it; that text is kept in the
app's data folder (`cache/text/`, pruned when unused for 120 days), never in your study folder.
- **Create** makes *Notes* or a *Study guide* for a module (or every module), always through an editable plan with a token estimate.
  - *Per week*: lecture notes and workshop summaries one per week (`Notes/Lectures/Week 03.md`, `Notes/Workshops/Week 03 - summary.md`; the week
    as in *Files*). A file with no week gets a note of its own.
  - *Per category (whole term)*: one note per category (`Notes/Lectures/Lectures - all weeks.md`). It is written from the weekly notes when every
    week has an up-to-date one (cheap), otherwise from the course files, in parts with a merge when they are long. The plan says which.
  - Workshop code stubs and coursework notes (requirements, marking, deadlines) stay one per file.
  Files you edited are never overwritten. Deadlines found in coursework notes are added to the Calendar once each.
- **Chat with the course** is the Study assistant on the AI page: tick notes or files as context and ask.
- **Library** (top right) lists notes and study guides per module and the deadlines saved from notes (*Remove* takes one out of the Calendar).
- **Prompts** (top right) can be edited (saved in `<data dir>/prompts/`, *Reset* returns the built-in).

Notes are written under `<module>/Notes/`. With the signed-in `claude` command, the usage windows Claude reports (5-hour, 7-day) are one chip in the AI header (details in its tooltip), and the app refuses to start a run past
`llm.stop_at` (default 80%) and stops a running batch when Claude says a limit is reached. With an API key (`ANTHROPIC_API_KEY`, `LLM_API_KEY`, or a key saved from Help), calls are billed per use by the provider and the key is used before the signed-in command.
`model` is `haiku` by default (small and cheap), or `sonnet`, `opus`, or a full model id.
Check your institution's rules on third-party AI before sending course material to a remote model.

### Models: any provider, one per task
*Settings › Connections › AI model* is a table: a default, and a row per task (notes, study guide, audio script, chat, ask) that uses the default
until you choose otherwise. Each row has *Test* (one tiny call, no course text). The same in `config.json`:

    "llm": {"provider": "auto", "model": "haiku",
            "tasks": {"guide": {"model": "sonnet"},
                      "audio_script": {"provider": "openai_compat", "base_url": "http://localhost:11434/v1", "model": "qwen3:14b"},
                      "chat": {"provider": "cli", "command": ["opencode", "run", "{prompt}"]}}}

| Provider | Uses | Options |
|---|---|---|
| `auto` (default) | an Anthropic API key if there is one, else the signed-in `claude` command | `model`, `stop_at` |
| `claude_cli` | the signed-in `claude` command (Claude Code; your plan, with its limits) | `model`, `executable`, `stop_at` |
| `claude` | the Anthropic API with a key | `model`, `key_file`, `key_env`, `base_url` |
| `openai_compat` | any OpenAI-style server: Ollama, LM Studio, llama.cpp, vLLM, hosted APIs | `base_url`, `model`, `key_env` or `key_file`, `local` |
| `cli` | any command that takes a prompt and prints the answer | `command`, `model`, `local`, `env`, `timeout` |

- **`cli` command**: a list of arguments (or one line in Settings). `{model}`, `{system}` and `{prompt}` are replaced inside any argument; without
  `{prompt}` the prompt goes to stdin, without `{system}` the instructions are put in front of the prompt. No shell runs it, and it starts in an
  empty temporary folder. Examples: `["ollama", "run", "{model}"]`, `["llm", "-m", "{model}", "-s", "{system}"]`. `opencode run "<prompt>"` is
  the form its documentation gives; it uses the model set in opencode itself (see `opencode run --help` for a model flag and add it with `{model}`).
  In Settings the command is one line: quotes group words, and Windows paths (`C:\Tools\llm.exe`) are kept as typed.
- **Where the text goes**: every confirm names the model and says "stays on this computer" or "sent to …". A `localhost` server counts as
  local. Set `"local": true` when the address cannot tell (a container name) or when a command answers on this computer; the app cannot know
  what a command does with the text, so without it a command counts as sending.
- **Keys** never go in `config.json`: `key_env` names an environment variable, `key_file` a file in the data folder.
- A task entry that names a provider uses only its own `model`, `base_url`, `command` and key; one without a provider (`"guide"` above) changes
  just those keys of the default. Plan limits are tracked for the `claude` command only; a plain command reports no tokens, so they are estimated.

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
| `llm-check [--task T]` | test the model connection, the default or a task's (and where it looked for a key) |
| `notes MODULE [--what lectures,summaries,code,coursework,guide,all] [--group week\|term] [--week N] [--dry-run] [--yes] [--force]` | study material from the terminal, with the same token guard |
| `probe` | read-only check of what your token can do (`probe_report.json`, field names only) |

## Settings
*Settings* opens on **Connections**: Moodle (source), the study folder (storage), the AI model and the calendar feed, each with its state and the actions that fit
(sign in again, choose and test a model per task, add or remove an API key, copy the feed address). The Moodle token is never shown.
**Courses** lists every course Moodle has for you. A course is followed when a folder in the study folder starts with its module code; any other
(earlier years, no code) can be followed from the list, and a course you are not enrolled in can be added by its address (`…/course/view.php?id=123`) if Moodle lets you open it.
Each followed course has one folder, directly inside the study folder or inside a **year folder** (`Y1`, `Year 2`, …). Once the study folder has a year folder,
a course you follow is offered a folder in its year, taken from the level digit of its module code (CSCI.323 is year 3) or else from when it started; *Follow all* does this
for every course at once. To nest an existing setup, set the study folder to the folder that holds `Y1`, `Y2`, `Y3`: existing records follow.
`config.json` in the data folder holds the options; most are editable on the *Settings* page by section, which checks values and keeps any other keys untouched. Fields marked *restart* apply
the next time the app starts. Course-name keywords (`keywords`) and the less common model options (`env`, `timeout`, `key_env`) are edited in the file.

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
