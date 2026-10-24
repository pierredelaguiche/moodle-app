# Architecture

## Layers
Each layer may import only from the ones below it. This is what makes the pieces testable alone and replaceable later
(a second source besides Moodle, a different calendar, a container instead of the OS).

```
cli.py            web/server.py + web/static/index.html          entry points, no business logic
services/         sync, files, notes, ics, tree, read, setup,     one use-case each, take a Ctx
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

## Web layer
`web/server.py` is a thin HTTP adapter over the services (JSON API); the page is static files in `web/static/` (`index.html`, `style.css`,
and plain scripts: `core.js` helpers/router/selection, one file per page). There is no build step and no dependency. The page builds DOM
with a helper that only sets `textContent`, never `innerHTML`, so announcement text or file names cannot inject markup; the server also sends a
strict Content-Security-Policy (scripts and styles from itself only, no inline script, `blob:` only for PDF/image previews).
`/static/<name>.js|css` is whitelisted by pattern and carries no secrets; every `/api/*` call needs the per-run token and a local Host.

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
6. `Week NN/` folders form at the threshold, existing week folders are reused and receive new files immediately, adopted files never move.
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
returns the bytes and deletes the file in `finally`; `clean_previews` runs at start and exit. `read.pdf_text` writes `<home>/tmp/<name>.txt`.
Endpoints: GET `/api/alloc`, `/api/preview`; POST `/api/place`, `/api/retarget`, `/api/fs/{mkdir,move,rename,rmdir,undo,subdivide}`, `/api/open` (document suffixes only), `/api/pdf/text`.

## Start-up
`web.server.run` starts listening, then runs the first check as a normal `refresh` job. `App.refresh` holds the lock but gives it up while a Moodle request is in flight (only when no transaction is open), so reads (`/api/badges`, `/api/tree`, ...) are answered from the database during the check. `/api/badges.syncing` drives the sidebar spinner.

## Front end: selection and Files views
`Selectable` (core.js) is the only selection code: rows carry `data-sel`, it draws the one round `.selbox` per row itself (always visible; rows contain no other checkbox, Deadlines use a *Mark done* button), supports heading rows (`data-group` + `data-gscope`, derived state), keyboard, rubber band, touch, and adds *Select all* to the bar. A selection only ever holds rows that exist in the DOM. `files.js` holds what both Files views share: `F.moodleActions` (one list of actions feeding the bar, the right-click menu and the shortcut keys, see `F.bar`, `F.menu`, `F.keymap`) and `F.look` (the quick-look overlay). `alloc.js` (Smart allocation) and `organise.js` (side by side) only build their rows and add their own actions. In Organise, a destination folder gets a colour index (`dest`), used for the chip on the Moodle row and the dashed *to come* row on the folder side.


## Weeks
`domain.weeks.week_for` decides a file's week: a "Week N" in the Moodle section name, then a week token in the file/activity name
(`week2`, `w02Lab`), then a leading lecture number (`03_Runtime.pdf`, week-folder categories only), then Moodle's section index (often
off by one or more). `moodle-app audit` reports files whose folder disagrees.

## Text extraction
`notes.extract.extract_info(path)` is the only PDF reader. It stores text in `<home>/cache/text/` keyed by (path, size, mtime) (configured by `Ctx`), so `read`, *Extract text*, and notes share one extraction. `App.note_plan/note_run` (server) build a plan for the picked files only (`planner.build_plan(only=...)`) and run it through `services.notes.run_notes`.

## The AI page
`services/ai.py` holds every LLM use (state, prompts, notes plan/job, ask). `web/server.py` exposes them under `/api/ai/*`; the paths in `Handler.LLM_PATHS` need the header `X-Page: ai`, which only `ai.js` sends (`aiApi`). The database lock is held for database work only, never during a model call.

Texts: `services/read.py` writes per course to `.formatted txt` (`course_of`, `out_dir_for`, `adopt_legacy`); the walker hides dot-folders. Plan: `AI.plan` returns editable jobs keyed `kind|source_rel` (module `*` = all); `notes_job(select=[{module,key}])` runs only those. Coursework notes end with a `<!-- deadlines: [...] -->` line that `AI.save_deadlines` turns into calendar events (note `AI: <rel>`, deduplicated). `tracker.notes_by_source` links notes to files via their `source:` front matter.


## Setup and settings
`services/setup.py` is the first-run flow used by the Help page: save the Moodle address and study folder, hand out the sign-in link, accept the pasted `moodlemobile://token=` address (verified with one `core_webservice_get_site_info` call before it is saved), and save or remove an API key. `serve` starts without a token or study folder; sync and file actions report "not set up" until the flow is done. `config.FIELDS` describes the options the Settings page may edit; `config.update` validates, merges into `config.json` (unknown keys kept), and applies the change to the running app. The theme is a browser-side preference.
