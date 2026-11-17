# Status and ideas

## Working
Layered package with SQLite migrations; files (one two-tree view with suggestion arrows, tracked moves, week folders incl. folders already divided
by weeks, duplicates); deadlines with submission status and moved-date detection; announcements; activity log; calendar and iCalendar export; tracker;
Study (four arrangeable sections with tabs for notes, PDFs and text, safe writes, Markdown/maths, file creator, an assistant with explicit context); background jobs (queued, parallel downloads, cancel, clean quit); AI page (note-taker with an
editable plan, prompts, ask, deadlines from coursework notes; PDF text extracted behind the scenes); settings with a Connections view and an in-app
quick start; CI on three systems. Tests use a mock Moodle and mock model servers, no network.

## Next (designed, not started): see [DESIGN_AI.md](DESIGN_AI.md)
1. **Say it once**: cut repeated and long UI text app-wide (plan usage becomes one chip on the AI tab).
2. **A model per task**: e.g. Claude for notes, a local model (Ollama, LM Studio) or a harness (`opencode run`) for audio scripts or chat.
3. **AI tab**: one *Create* panel; notes per week or per category (whole term); study guide as a first-class choice; *Chat with the course* replaces *Ask*.
4. **Study assistant**: a quick menu (this week's notes, term notes, study guide, audio guide) above the chat.
5. **Audio guides**: a model rewrites the source as a fuller spoken read-along (numbers and maths in words), then local TTS speaks it
   (Kokoro container, or in-process fallback). Speed, language and accent are user settings; default English, `af_heart`.
   Runs in the background with a time estimate and a progress bar.
6. **Chat engines**: Claude Code (agent in your folders, also inside the Docker setup via the official Claude Code container feature) or a plain
   model chat with any provider, chosen in Settings.

## Ideas, roughly in order
0. **Normalise names across Y1-Y3** (asked for): one naming style for week folders (`week_01`, `Week 1`, `Week 01`), file names (`Lecture01.pdf`,
   `Lecture 1.pdf`, `lecture-01.pdf`) and category folders, chosen in Settings (separator: space, underscore, hyphen; numbers padded to two
   digits or not; CamelCase or words). Shown as a plan first (old name -> new name, per module), applied as tracked moves with Undo, so records
   follow. Building blocks exist: `weeks.week_name` already follows the style of sibling folders, `Layout.subdivide` moves with a plan.
1. Tracker: per-course to-dos, a `reviewed` rule beyond manual ticks. (Weeks already follow the study folder, not the Moodle section.)
2. Calendar: recurring events, timetable import.
3. Files: remembered allocation rules by pattern, touch drag and drop, arrows between stacked panes.
4. Study: open in an external editor, LaTeX compile, search across notes, the assistant proposing edits as a diff.
5. Notes: LaTeX output, more prompt presets.
6. Packaging: CI (lint, tests, secret scan), pipx/PyPI, Windows path tests. Docker: see B below.
7. Connectors as formal interfaces once a second one exists: another LMS behind `moodle/fetch.py`, a CalDAV/Outlook calendar sink, a local model preset.

## Later, bigger plans (not started)
- **Easy install**: Docker (`compose.yaml`, bound to 127.0.0.1, folders mounted), pipx/PyPI, a double-click build per OS.
- **Faster backend**: profile first (most time is likely network, PDF text and model calls); cache the folder tree, parallel Moodle calls,
  SQLite indexes. Rewrite a part in another language only if profiling shows a CPU-bound hot path.
- **Comments**: a docstring per module and public function (contract and *why*), JSDoc on shared JS helpers; `ruff` (`D` rules) and ESLint in CI.
- **Accessibility**: WCAG 2.2 AA (keyboard trees, focus, contrast, reduced motion); axe in the browser tests; an accessibility statement.
- **"ISO acceptance"**: software is not ISO-certified; universities ask for a security overview, a data-flow statement and the accessibility statement.
- **Website, data local**: host the front end (`web/static/` has no build step) and keep the app on your machine; a browser-only version is blocked by
  Moodle's CORS, folder access (Chromium only) and the local `claude` CLI. GitHub Pages via the Student Pack; offers and verification expire, so own the domain.
- **Before going public**: rename (*Moodle* is a trademark; the official app is "Moodle app"), check the university's rules on automated access.
- **If hosted**: privacy notice, Impressum, terms, "not affiliated" note, cookie consent only for non-essential storage. Monetise with a plain
  Buy Me a Coffee link; ads need consent, clash with the privacy promise and with GitHub Pages' terms.
