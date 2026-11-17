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

## Read-models
* **Tracker** (`services/tracker.py`): a file's week is `tracker.week_of` (a `Week N` folder on its path, else `week_for`; see *Weeks*), so it
  matches the study folder; `week_keys` uses the same rule. Denominator = files not ignored, numerator = downloaded files in `reviewed`;
  "behind" = an earlier week than the current one (from the course start date) that is not complete.
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
where Finder/Explorer/VS Code/container differences go; `llm/providers.py` is the only place that knows about LLM HTTP APIs and commands (a new provider is a class there plus a branch in `make_provider`).
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

## Chat (services/chat.py)
`Chat` keeps conversations (JSON under `<home>/chat/sessions/`, extra project folders in `<home>/chat/folders.json`). A message starts a thread that runs
`claude -p --output-format stream-json --verbose --include-partial-messages` with the project folder as working directory, the prompt on stdin, `--tools`
and `--allowedTools` from the conversation's mode, `--permission-mode dontAsk`, `--strict-mcp-config`, and `--resume <session>` after the first answer.
`Turn` folds the stream into one assistant message (`parts`: text, streamed by `text_delta` and settled by the `assistant` event; tools, completed by
their `tool_result`), and `result` adds tokens and cost. The page polls `/api/chat/get` while `running`; `v` changes whenever the message does.
Stop sends SIGTERM to the process group; a session the CLI no longer knows is retried once without `--resume`. `rate_limit_event` updates the stored plan
limits, and `limits_problem` refuses a message past `llm.stop_at`. Every `/api/chat/*` call needs `X-Page: chat`, and only `chat.js` names those paths
(unit test). Quitting stops running answers. Tests use `tests/fake_claude.py`, which prints the same event types.

## Weeks
`domain.weeks.week_for` decides a file's week, in order: a week token in the file/activity name (`Week 3`, `week2`, `w02Lab`; any case), a leading
lecture number (`03_Runtime.pdf`; for placing new files, week-folder categories only), a "Week N" in the section name, the section index (often off).
Once a file is in a `Week N` folder, that folder wins (`week_of_path`): the Tracker and the note planner read it first. `moodle-app audit` reports
files whose folder disagrees with `week_for`.

## Ordering
Trees are sorted folders first, then by `tree.natural_key` (case-insensitive, numbers by value: `Week 2` < `Week 10`). The front end uses the same
rule (`natCmp`, `pathCmp` in core.js; `pathCmp` compares path segments so a folder is followed by its own subfolders). Tree rows reserve the
chevron slot on every row (`.chev.ph`, `.pick .tg`) so siblings line up whether or not they have children.

## Notes per week
`planner.build_plan` groups lecture and workshop-summary sources by week (`week_of`: week in the path, then in the file name, then a leading lecture
number, then `week_hint`, which `services.notes.week_hints` derives from Moodle's section for downloaded files). One job per (kind, week) reads every file
of that week, joined with `=== File: name ===` headers; its key is `kind|week:N`, its output `Notes/Lectures/Week NN.md` or
`Notes/Workshops/Week NN - summary.md`, and its front matter carries `week:` and a `sources:` list (read by the Tracker and the study guide).
Files without a week, code stubs and coursework notes keep one job per file (`kind|<path>`).

**Per category (group "term").** `build_plan(..., group="term")` turns the weekly groups of lectures and summaries into one `category_notes` job each
(`_term_job`): key `term_key(kind)` = `lecture_notes|term`, output `Notes/Lectures/Lectures - all weeks.md`, `Job.base` = the weekly kind. Its source
(`Job.via`) is the weekly notes when every group has one written from the files as they are now (the manifest's `input_hash`; a note you edited
still counts), otherwise the course files. Either way the pieces go through `guide_batches`, with `category_notes_merge` when there is more than
one request, and the plan's note says which ("from 9 weekly notes", "from 34 files: 3 parts + merge"). The hash is over the course text, so weekly
notes appearing later do not make a term note out of date. `sources:` lists every course file; the Tracker prefers a week's own note when both
exist, and `guide_inputs` skips `* - all weeks.md`. `notes.group` is the default; the AI page and `notes --group` choose per run.

## Text extraction
`notes.extract.extract_info(path)` is the only PDF reader and it is an internal step: no command, page or endpoint asks for it directly. It stores text in `<home>/cache/text/` keyed by (path, size, mtime) (configured by `Ctx`), so notes, questions and the Study assistant share one extraction and no PDF is converted (or sent) twice. A cached text is marked as used when read; `prune_cache` (run when the server starts) removes texts unused for `CACHE_KEEP_DAYS`. Text bundles written by early versions (`.formatted txt/`, `formatted txt/`) are left on disk, never listed and never used as a source. `App.note_plan/note_run` (server) build a plan for the picked files only (`planner.build_plan(only=...)`) and run it through `services.notes.run_notes`.

## The AI page
`services/ai.py` holds every LLM use (state, prompts, notes plan/job, ask, and `chat` for the Study assistant). `web/server.py` exposes them under `/api/ai/*`; `Handler.LLM_ALLOW` lists every path that can reach a model and the pages that may call it, by their `X-Page` header: `ai` (`ai.js`) all of them; `study` (`editor.js`) `chat`, `plan` and `notes` (the assistant and its quick menu); `settings` only `check` (the *Test* button, no course text). The server refuses any other pair, and a unit test checks both that table and that no script names a path its page may not call. The database lock is held for database work only, never during a model call.

**A model per task.** `llm.providers.task_config(llm, task)` lays `llm.tasks.<task>` over the top-level `llm` keys (an entry that names a provider
brings its own model, address, command and key; one without changes only the keys it has), and `make_provider(..., task=)` builds from that. Tasks:
`notes`, `guide` (`planner.task_of(kind)` maps a note kind to one of these two), `audio_script`, `chat` (the Study assistant), `ask`. `AI.provider(task)`
is the only way services get a provider; `planner.execute` and `describe` take one provider or a function kind -> provider, so one run can use two
models. Providers: `claude`, `claude_cli`, `openai_compat`, and `cli` (`CLIProvider`: any command, prompt on stdin or as an argument, no shell, empty
working folder, token counts estimated). Every provider has `remote` and `where`; `AI.describe(provider)` is the `target` (`model`, `local`, `where`)
that plans, answers and `/api/ai/target` carry. `/api/ai/target` reads config and calls no model, so it is not an LLM path.

**The confirm.** Nothing is sent before a confirm that names the model and says where the text goes: the *Create* dialog lists the plan's targets,
and chat-style sends (the Study assistant, *Ask*) go through `confirmSend` in core.js, asked once per model and destination in a tab. `modelLine` is
the one formatter (`haiku, sent to Anthropic`).

**The model table.** `config.model_scopes()` yields the default and the tasks; each has five `FIELDS` marked `table` (provider, model, server address,
command, local). They are validated and saved by `config.update` like every option (empty = unset, a half-filled row is refused), but shown by
`modelTable` in settings.js from `App.model_rows`, not as an option group. *Test* calls `/api/ai/check` with the task.

Usage is data, not a sentence: `services/usage.summary` returns `{five_hour, seven_day, overage, seen, app_tokens_7d}`, every AI reply carries it as `usage`, and `usageChip` in `ai.js` is the only code that shows it (a confirm adds a window only above 60%). The terminal keeps one sentence (`usage.cli_line`).

UI text: `tests/unit/test_ui_text.py` fails on a string literal over 140 characters in a page script and on an intro paragraph over 110 (one line); `guide.js`, the Help page, is exempt. Longer explanations go into a `title` tooltip or Help.

The page (`ai.js`): one *Create* panel (Make: notes or study guide; for notes the group, week and kinds), *Chat with the course* (`/api/ai/chat`
with the ticked files as `rels`: the Study assistant's backend, task `chat`), and two panels opened from the header: *Library* (`/api/ai/generated`,
notes and study guides apart, deadlines from notes) and *Prompts*. `/api/ai/ask` and the `ask` task are still served but no page uses them.

Plan: `AI.plan` returns editable jobs keyed `kind|source_rel` (module `*` = all); `notes_job(select=[{module,key}])` runs only those. Coursework notes end with a `<!-- deadlines: [...] -->` line that `AI.save_deadlines` turns into calendar events (note `AI: <rel>`, deduplicated). `tracker.notes_by_source` links notes to files via their `source:` front matter.

## Setup and settings
`services/setup.py` is the first-run flow used by the Help page: save the Moodle address and study folder, hand out the sign-in link, accept the pasted `moodlemobile://token=` address (verified with one `core_webservice_get_site_info` call before it is saved), and save or remove an API key. `serve` starts without a token or study folder; sync and file actions report "not set up" until the flow is done. `config.FIELDS` describes the options the Settings page may edit; `config.update` validates, merges into `config.json` (unknown keys kept), and applies the change to the running app. The theme is a browser-side preference.

## Study
`editor.js` is the Study page (route `#/edit/<rel>`, kept so existing links work). The work area is four sections, `secs[0..3]` = top left, top right, bottom left,
bottom right, each `{tabs: [rel], on}`; a file is open in one section at a time, and the shared document store (`App.S.docs`) holds one document per file. `draw`
lays the non-empty sections out as up to two flex rows with `splitter` bars between them (`split.row`, `split.c0`, `split.c1` are percentages); `show(rel, side, at)`
is the only way a file enters or changes section (click, Alt-click, drop on a zone of `dropZones`, the `arrangeMenu`). Sections, focus and splits are kept in
`sessionStorage`. The quick menu above the assistant (`quickMenu`) asks `/api/ai/week?rel=` about the focused file (module, week by `planner.week_of`,
category from the notes' lecture/workshop folders; no model) and offers one job each: that week's notes, its category for the whole term, the study
guide. `quickJob` plans it (`/api/ai/plan`), confirms (model, where the text goes, files written, tokens), runs `/api/ai/notes` with that plan's keys as a job
and opens the result. *+ Module notes* is a pin `{module}`; `AI.chat(modules=)` adds every note of `AI.module_notes(module)` within the same budget.
*Open in AI tab* links to `#/ai/create?module=&make=&group=&week=&kinds=`, which `ai.js` (`prefill`) turns into a filled Create panel. The assistant posts to `/api/ai/chat` with the conversation, the files on screen (`shown()`; text files as `snippets` with their unsaved text, PDFs as
`rels`) and what was added by hand. `AI.chat` reads files through the shared text cache, caps the context at `notes.max_input_tokens`, sends the last `CHAT_TURNS` turns,
and returns the answer with what was actually used. Applying an answer only changes the text in the section; saving goes through the same conflict-checked save as typing.

## Settings and connections
`App.connections` (server) is the read-model behind *Settings > Connections*: one entry per connector (`moodle` source, `folder` storage, `model`, `calendar` feed) with `state` (`ok`, `warn`, `todo`, `off`), a summary and detail lines; never the token. The actions reuse existing endpoints (`/api/setup/*`, `/api/refresh`, `/api/ai/check`, `/calendar.ics`). A second source or calendar sink would add an entry here and a sibling module at the seams above.

## Styles
`style.css` starts with the tokens: colours (light, dark), the corner scale (`--r-sm`, `--r`, `--r-lg`, `--r-pill`) and the type scale (`--fs-2xs` to `--fs-xl`). Rules below use only those: no literal colour, radius or font size outside `:root`.

## Courses
A course is tracked when `match_folder` finds a top-level folder starting with its module code, or when it is in the `course_folders` setting (database, `{course id: folder name}`), written by `App.add_course` (*Settings > Courses*, `/api/courses`, `/api/courses/add`, `/api/courses/remove`). `sync_courses` reads the enrolled courses, then asks for added courses that are not among them by id (`core_course_get_courses_by_field`); if that call fails the existing rows are kept. `domain/years.py` is the year level: `module_dirs` lists module folders directly in the study folder and inside year folders, `year_of` reads a course's year, `year_folder` names a year folder like its siblings. `courses.folder_rel` may therefore be `Y1/Name`; everything that lists modules (`AI.modules`, Study's `modules()`, the notes command) goes through the same rule. `sync.follow_root` remembers the study folder's path and, when it moves to a folder above or below, rewrites `files.rel_path`, `courses.folder_rel`, `notes_manifest.rel`, `file_targets.folder_rel` and the course map with the path in between.
