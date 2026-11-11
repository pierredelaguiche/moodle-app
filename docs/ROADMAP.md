# Status and ideas

## Working
Layered package with SQLite migrations; files (one two-tree view with suggestion arrows, tracked moves, week folders incl. folders already divided
by weeks, duplicates); deadlines with submission status and moved-date detection; announcements; activity log; calendar and iCalendar export; tracker;
Study (four arrangeable sections with tabs for notes, PDFs and text, safe writes, Markdown/maths, file creator, an assistant with explicit context); background jobs (queued, parallel downloads, cancel, clean quit); AI page (note-taker with an
editable plan, prompts, ask, deadlines from coursework notes; PDF text extracted behind the scenes); settings with a Connections view and an in-app
quick start; CI on three systems. Tests use a mock Moodle and mock model servers, no network.

## Ideas, roughly in order
0. **Normalise names across Y1-Y3** (asked for): one naming style for week folders (`week_01`, `Week 1`, `Week 01`), file names (`Lecture01.pdf`,
   `Lecture 1.pdf`, `lecture-01.pdf`) and category folders, chosen in Settings (separator: space, underscore, hyphen; numbers padded to two
   digits or not; CamelCase or words). Shown as a plan first (old name -> new name, per module), applied as tracked moves with Undo, so records
   follow. Building blocks exist: `weeks.week_name` already follows the style of sibling folders, `Layout.subdivide` moves with a plan.
1. Tracker: per-course to-dos, a `reviewed` rule beyond manual ticks, a week offset when section N is not week N.
2. Calendar: recurring events, timetable import.
3. Files: remembered allocation rules by pattern, touch drag and drop, arrows between stacked panes.
4. Study: open in an external editor, LaTeX compile, search across notes, the assistant proposing edits as a diff.
5. Notes: LaTeX output, more prompt presets.
6. Packaging: CI (lint, tests, secret scan), pipx/PyPI, Docker, Windows path tests.
7. Connectors as formal interfaces once a second one exists: another LMS behind `moodle/fetch.py`, a CalDAV/Outlook calendar sink, a local model preset.
