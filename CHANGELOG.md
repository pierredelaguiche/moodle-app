# Changelog

## 0.2.0

### Changed
- **Files is one view.** Moodle on the left, your folders on the right, and an arrow from every file still on Moodle to the folder it will be
  saved in (dashed = suggested, solid = your choice). Smart allocation and its second list are gone; its bulk actions (*Download all new*,
  *Ignore all new*) sit in the page header. Rows carry no badges or chips: the icon colour says the state, with one word for the states that
  need a decision. A *Show* list filters by state; an *Arrows* list switches between every new file and the selection only. Both sides have
  Expand and Collapse.
- **A folder already divided into weeks takes new files into their week**, below the three-file threshold and in any category. A missing week
  folder is created and named like its siblings.
- **PDF text extraction is invisible.** It runs once per version of a file, only when notes, a question or the assistant need it, into the
  app's data folder. Removed: the `read` command, the *Read* card on the AI page, *Read PDFs into text files* and *Extract text* in Files.
  `.formatted txt` folders from earlier versions stay on disk, unused.
- **Study replaces Editor**: module picker; a work area of four sections, each with tabs, arranged by dragging files and tabs onto a section and
  resized with the bars between them; a file creator; and an assistant that reads only what its context list shows.
- **Background jobs.** Actions are queued instead of refused, downloads fetch several files at once, every job can be cancelled, and the
  pop-up log is replaced by one quiet line with a list on demand. Quitting (menu, Ctrl+C, SIGTERM) interrupts a running job at a safe point.
- **Front end, HTTP and backend are separate**: `web/static/`, `web/server.py`, `web/app.py` + `web/jobs.py`.
- **Settings has sections**, opening on *Connections* (Moodle, study folder, AI model, calendar feed).
- One set of design tokens (colours, corners, type scale) for every page.

- **Courses.** *Settings › Courses* lists every course Moodle has for you, followed or not. Follow one that the folder names did not match
  (earlier years, courses without a module code), or add a course you are not enrolled in by its address or number.

- **Year folders.** Module folders can sit inside year folders (`Y1/…`, `Year 2/…`). Once the study folder has one, a course you follow is
  offered a folder in its year (level digit of its code, else its start date), and *Follow all* puts each in its year. Moving the study
  folder up one level (from `…/Y3` to its parent) keeps every record.
- **Process box.** A small "Process #n" box above *Check Moodle* opens into that process's terminal; *All processes* lists the rest.
- The option groups in Settings (Launch to AI) are one scrolling page.

### Fixed
- The icons-only menu overflowed its boxes and hid the labels of the Settings section menu.
- The address of the page was not printed when the first check of Moodle was slow: the job captured the output. It is now printed first.
- `serve` no longer shares a port with a program that already listens on it; it opens the running app or picks a free port.
- The cache of extracted PDF text grew without bound; texts unused for 120 days are pruned at start.

### Removed
- The "another action is still running" error.
- `moodle-app read`, `/api/read`, `/api/ai/read`, `/api/ai/folders`, `/api/pdf/text`, `web/static/alloc.js`, `services/read.py`.

## 0.1.0
First packaged version.
