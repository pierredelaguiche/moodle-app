# Status and ideas

## Working
Layered package with SQLite migrations; files (smart allocation and organise views, tracked moves, week folders, duplicates);
deadlines with submission status and moved-date detection; announcements; activity log; calendar and iCalendar export; tracker;
text editor with safe writes and Markdown/maths preview; AI page (read, note-taker with an editable plan, prompts, ask, deadlines from
coursework notes); settings and in-app quick start. Tests use a mock Moodle and mock model servers, no network.

## Ideas, roughly in order
1. Tracker: per-course to-dos, a `reviewed` rule beyond manual ticks, a week offset when section N is not week N.
2. Calendar: recurring events, timetable import.
3. Files: remembered allocation rules by pattern, touch drag and drop.
4. Editor: open in an external editor, LaTeX compile, search across notes.
5. Notes: LaTeX output, more prompt presets.
6. Packaging: CI (lint, tests, secret scan), pipx/PyPI, Docker, Windows path tests.
7. A second source (another LMS) behind `moodle/fetch.py`.
