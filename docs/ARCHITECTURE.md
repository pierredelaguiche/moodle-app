# Architecture

## Layers
Each layer may import only from the ones below it. This is what makes the pieces testable alone and replaceable later
(a second source besides Moodle, a different calendar, a container instead of the OS).

```
cli.py            web/server.py (HTTP) -> web/app.py (backend)    entry points, no business logic
                  web/jobs.py (queue)   web/static/ (front end)
services/         sync, files, notes, ics, tree, setup, ai,       one use-case each, take a Ctx
                  tracker, digest, calendar, editor
notes/ llm/       planning + extraction + prompts; providers       study material; LLM calls
moodle/           client, auth, fetch, probe                       the ONLY code that talks to Moodle
db/               connection, migrations, repos                    the ONLY code that writes SQL
domain/           classify, duplicates, weeks, util                pure rules (folder reads only)
config.py, host/  settings + data dir; OS actions (trash)          swappable per platform
```
`services/context.py` (`Ctx`) carries settings, the connection, the Moodle client, a logger and a clock, so tests inject fakes.

## Data flow of a sync
`moodle.fetch` (network, defensive `.get`) -> plain records -> `services/sync.py` (transaction per course) -> `db/repos.py`.
Fetchers return `None` when a function is unavailable or fails, and sync only marks a deadline "gone" for sources that
returned a list, so an API failure never wipes data. Every run is a row in `runs`; every detected difference is a row in `changes`
(this is the History view and the "since last visit" source).

## Database (SQLite, `schema_version` + forward-only `db/migrations/NNNN_name.sql`, each in a transaction)
| Table | Holds |
|---|---|
| `settings` | JSON key/value (site release, ICS key, ...) |
| `courses`, `sections`, `activities` | what Moodle says exists; `courses.tracked` = matched to a local folder |
| `files` | every file Moodle lists + what happened to it (`pending/downloaded/ignored`, `adopted`, `rel_path`, version signature, pending update) |
| `deadlines` | stable id `course:cmid:kind`; your `hidden/done/note` are never overwritten by a sync; `seq` bumps on a moved date; `gone` |
| `announcements` | news-forum discussions (HTML stored, shown as text), `read` flag |
| `runs`, `changes` | history |
| `notes_manifest` | which generated files the tool owns and the hashes that protect your edits |
| `reviewed` | files you ticked as studied (the Tracker's numerator) |
| `events` | your own calendar entries (migration 0002); Moodle never writes here |
| `file_targets` | where you want a Moodle file not yet downloaded (migration 0003); removed after download or when Moodle drops the file |
| `todos` | schema only, no UI yet |
Paths in the database are relative to `dest_root` with `/`, so moving the study folder or switching OS does not break records.
Foreign keys are on; deleting a course cascades.

## Web layer: front end, HTTP, backend
Three parts, each replaceable without touching the others:

| Part | Where | Knows about |
|---|---|---|
| Front end | `web/static/` (`index.html`, `style.css`, plain scripts: `core.js` helpers/router/selection/jobs, one file per page) | `/api/*` JSON only |
| HTTP layer | `web/server.py` (`Handler`, `make_server`, `run`) | requests, the access rules, routing to `App` |
| Backend | `web/app.py` (`App`: plain methods returning plain data), `web/jobs.py`, and the services | no HTTP |

There is no build step and no dependency. The page builds DOM with a helper that only sets `textContent`, never `innerHTML`, so announcement text or file
names cannot inject markup; the server also sends a strict Content-Security-Policy (scripts and styles from itself only, no inline script, `blob:` only for
PDF/image previews). `/static/<name>.js|css` is whitelisted by pattern and carries no secrets; every `/api/*` call needs the per-run token and a local Host.

## Jobs: background work, order, cancel, quit
Every long action is a `Job` submitted to `Jobs` (`web/jobs.py`) through `App.start`; the request returns `{job: id}` at once.
* **Queue, not refusal.** Jobs run one at a time (they share one database connection); a new one waits instead of failing. The slow part of a
  download runs in parallel inside its job: `FileService.download` fetches with a thread pool (`sync.parallel_downloads`) and takes the database lock
  only to put each fetched file in place, so the page keeps reading while files are in transit.
* **Order.** `USER` work (downloads, notes) is taken before `BACKGROUND` work (checks of Moodle) that is still waiting; a second check joins the one
  already waiting or running (`coalesce`).
* **Cancel.** `Jobs.cancel(id)` drops a waiting job at once and flags a running one; services call `ctx.check()` at safe points (between courses in a
  sync, between files in a download, between notes), which raises `services.context.Cancelled`. It is a `BaseException`, so the `except Exception`
  blocks that turn failures into log lines do not swallow it; transactions roll back and run records are closed as not ok.
* **Quit.** `/api/quit`, Ctrl+C and SIGTERM all end in `App.stop`: nothing new starts, waiting jobs are dropped, the running one gets a few seconds to reach
  a safe point, previews are removed.
* **Output.** While jobs run, `sys.stdout` is a router that sends what a job's thread prints to that job's log and everything else to the terminal.
* **API.** `GET /api/jobs` (the list with state, progress and last line), `GET /api/job?id=&since=` (a log), `POST /api/job/cancel`. The page polls
  while anything is active (`Jobs` in core.js), shows one line in the menu, and tells pages when a job ends (`'job'`) or files land (`'progress'`).

* **Tracker** (`services/tracker.py`): week = Moodle section; denominator = files not ignored, numerator = downloaded files in `reviewed`;
  "behind" = an earlier week than the current one (from the course start date) that is not complete. Section N is assumed to be week N.
* **Digest** (`services/digest.py`): read-model only; "since" is the time stored by `digest --seen` / "Mark as seen" (default 7 days).
* **Calendar** (`services/calendar.py`): merges deadlines and your events for a date range (max 400 days); event CRUD with validation.
* **Editor** (`services/editor.py`): see the README paragraph; `Conflict` -> HTTP 409.

## Behaviour that must not change (each has tests in `tests/unit/test_files.py`)
1. Only ticked files download; unticked stay `pending`; ignored stay recorded as `ignored`, even when Moodle changes them.
2. Every Moodle file is recorded under a stable key (`course:cmid:filepath+filename`, independent of Moodle's revision numbers).
3. A copy anywhere in the module folder is matched before a file counts as new (same stem+size > same name > same size >= 10 KB);
   small same-size files are compared by hash after download; files already claimed by another record are skipped.
4. A tracked file you moved inside the module folder is followed, not re-downloaded.
5. Layout `<Module>/<Category>/<file>`; first keyword hit over (file name, activity name, section name); numbered names -> Lectures.
6. `Week NN/` folders form at the threshold; a category folder that already has a week folder takes every new file into its week (any category, the folder is created when missing and named like its siblings); adopted files never move.
7. Updated files archive the old copy in `_old_versions/`.
8. The web interface rules: localhost, per-run token, Host check, paths confined to `dest_root`, delete = one regular file to the trash.


## Seams for later
`moodle/fetch.py` is the source boundary (a second university or another LMS would add a sibling module producing the same records);
`services/ics.py` is a pure function over rows (a CalDAV/Outlook sink would be another consumer of `deadlines`); `host/bridge.py` is
where Finder/Explorer/VS Code/container differences go; `llm/providers.py` is the only place that knows about LLM HTTP APIs.
These are conventions, not yet formal interfaces; formalise them when the second implementation arrives.

## Files editor (schema v3)
`services/layout.py` (`Layout`) owns folder work: mkdir, rename, rmdir, move, undo, subdivide (plan then execute) and the allocation view. Moves
rewrite `files.rel_path`, `notes_manifest.rel`, `file_targets.folder_rel` and pending destinations, and log a `moved` change, so sync sees no
missing file. Module folders are protected. `Ctx.urls` (memory only) holds Moodle download links; `App.preview` fetches into `<home>/tmp/preview/`,
returns the bytes and deletes the file in `finally`; `clean_previews` runs at start and exit.
Endpoints: GET `/api/alloc`, `/api/preview`; POST `/api/place`, `/api/retarget`, `/api/fs/{mkdir,move,rename,rmdir,undo,subdivide}`, `/api/open` (document suffixes only).

## Start-up
`web.server.run` refuses a port something already answers on (it opens the running app, or falls back to a free port), starts listening, prints the address, and then queues the first check as a normal `refresh` job. `App.refresh` holds the lock but gives it up while a Moodle request is in flight (only when no transaction is open), so reads (`/api/badges`, `/api/tree`, ...) are answered from the database during the check. `/api/badges.syncing` drives the sidebar spinner.

## Front end: selection and the Files view
`Selectable` (core.js) is the only selection code: rows carry `data-sel`, it draws the one round `.selbox` per row itself (always visible; rows contain no other checkbox, Deadlines use a *Mark done* button), supports heading rows (`data-group` + `data-gscope`, derived state), keyboard, rubber band, touch, and adds *Select all* to the bar. A selection only ever holds rows that exist in the DOM. `files.js` holds the page shell and the shared helpers: `F.moodleActions` (one list of actions feeding the bar, the right-click menu and the shortcut keys, see `F.bar`, `F.menu`, `F.keymap`), the folder picker and `F.look` (the quick-look overlay). `organise.js` is the one view: the Moodle tree, the folder tree and the arrows.

**Arrows.** `drawArrows` (organise.js) draws into one SVG laid over both panes, from `/api/alloc` data only: for every file with status `new`, a curve from its row to the row of `file.folder` (dashed = suggested, solid = `file.chosen`), coloured by the destination's index (`dest`, the same class `cN` on the file row and on the folder's `+N` count). A row hidden in a closed group starts at the group row, a folder hidden in a closed folder ends at the nearest visible ancestor (`seen`, `targetRow`), ends scrolled out of sight are clamped to the pane edge, and arrows with the same two ends are drawn once. It runs on a `requestAnimationFrame` after anything that moves rows: draw, scroll of either pane, open/close, resize, hover, selection. Above `ARROW_MAX` waiting files, or with *Arrows: selection only*, only hovered and selected files get one. Files already here get an arrow only while selected or pointed at. `F.flow(keys)` marks arrows of a download in progress (animated dashes); after the job, `land` opens the folder and lets the new rows glow once. Stacked panes (narrow window) draw nothing and show the destination as text (`.to`), which is also what a screen reader gets on a wide window.

## Weeks
`domain.weeks.week_for` decides a file's week: a "Week N" in the Moodle section name, then a week token in the file/activity name
(`week2`, `w02Lab`), then a leading lecture number (`03_Runtime.pdf`, week-folder categories only), then Moodle's section index (often
off by one or more). `moodle-app audit` reports files whose folder disagrees.

## Text extraction
`notes.extract.extract_info(path)` is the only PDF reader and it is an internal step: no command, page or endpoint asks for it directly. It stores text in `<home>/cache/text/` keyed by (path, size, mtime) (configured by `Ctx`), so notes, questions and the Study assistant share one extraction and no PDF is converted (or sent) twice. A cached text is marked as used when read; `prune_cache` (run when the server starts) removes texts unused for `CACHE_KEEP_DAYS`. Text bundles written by early versions (`.formatted txt/`, `formatted txt/`) are left on disk, never listed and never used as a source. `App.note_plan/note_run` (server) build a plan for the picked files only (`planner.build_plan(only=...)`) and run it through `services.notes.run_notes`.

## The AI page
`services/ai.py` holds every LLM use (state, prompts, notes plan/job, ask, and `chat` for the Study assistant). `web/server.py` exposes them under `/api/ai/*`; the paths in `Handler.LLM_PATHS` need the header `X-Page: ai` (sent only by `ai.js`) or `X-Page: study` (sent only by the assistant in `editor.js`, for `/api/ai/chat`). Settings may call `/api/ai/check` alone (`LLM_EXTRA`): the *Test connection* button, which sends no course text. A unit test checks that no other script names these paths. The database lock is held for database work only, never during a model call.

Plan: `AI.plan` returns editable jobs keyed `kind|source_rel` (module `*` = all); `notes_job(select=[{module,key}])` runs only those. Coursework notes end with a `<!-- deadlines: [...] -->` line that `AI.save_deadlines` turns into calendar events (note `AI: <rel>`, deduplicated). `tracker.notes_by_source` links notes to files via their `source:` front matter.


## Setup and settings
`services/setup.py` is the first-run flow used by the Help page: save the Moodle address and study folder, hand out the sign-in link, accept the pasted `moodlemobile://token=` address (verified with one `core_webservice_get_site_info` call before it is saved), and save or remove an API key. `serve` starts without a token or study folder; sync and file actions report "not set up" until the flow is done. `config.FIELDS` describes the options the Settings page may edit; `config.update` validates, merges into `config.json` (unknown keys kept), and applies the change to the running app. The theme is a browser-side preference.

## Study
`editor.js` is the Study page (route `#/edit/<rel>`, kept so existing links work). The work area is four sections, `secs[0..3]` = top left, top right, bottom left,
bottom right, each `{tabs: [rel], on}`; a file is open in one section at a time, and the shared document store (`App.S.docs`) holds one document per file. `draw`
lays the non-empty sections out as up to two flex rows with `splitter` bars between them (`split.row`, `split.c0`, `split.c1` are percentages); `show(rel, side, at)`
is the only way a file enters or changes section (click, Alt-click, drop on a zone of `dropZones`, the `arrangeMenu`). Sections, focus and splits are kept in
`sessionStorage`. The assistant posts to `/api/ai/chat` with the conversation, the files on screen (`shown()`; text files as `snippets` with their unsaved text, PDFs as
`rels`) and what was added by hand. `AI.chat` reads files through the shared text cache, caps the context at `notes.max_input_tokens`, sends the last `CHAT_TURNS` turns,
and returns the answer with what was actually used. Applying an answer only changes the text in the section; saving goes through the same conflict-checked save as typing.

## Settings and connections
`App.connections` (server) is the read-model behind *Settings > Connections*: one entry per connector (`moodle` source, `folder` storage, `model`, `calendar` feed) with `state` (`ok`, `warn`, `todo`, `off`), a summary and detail lines; never the token. The actions reuse existing endpoints (`/api/setup/*`, `/api/refresh`, `/api/ai/check`, `/calendar.ics`). A second source or calendar sink would add an entry here and a sibling module at the seams above.

## Styles
`style.css` starts with the tokens: colours (light, dark), the corner scale (`--r-sm`, `--r`, `--r-lg`, `--r-pill`) and the type scale (`--fs-2xs` to `--fs-xl`). Rules below use only those: no literal colour, radius or font size outside `:root`.

## Courses
A course is tracked when `match_folder` finds a top-level folder starting with its module code, or when it is in the `course_folders` setting (database, `{course id: folder name}`), written by `App.add_course` (*Settings > Courses*, `/api/courses`, `/api/courses/add`, `/api/courses/remove`). `sync_courses` reads the enrolled courses, then asks for added courses that are not among them by id (`core_course_get_courses_by_field`); if that call fails the existing rows are kept. `domain/years.py` is the year level: `module_dirs` lists module folders directly in the study folder and inside year folders, `year_of` reads a course's year, `year_folder` names a year folder like its siblings. `courses.folder_rel` may therefore be `Y1/Name`; everything that lists modules (`AI.modules`, Study's `modules()`, the notes command) goes through the same rule. `sync.follow_root` remembers the study folder's path and, when it moves to a folder above or below, rewrites `files.rel_path`, `courses.folder_rel`, `notes_manifest.rel`, `file_targets.folder_rel` and the course map with the path in between.
