# Changelog

## Unreleased

### Added
- **Study: quick menu** above the assistant, for the file in focus: *Notes for week N*, *<Category>, all weeks*, *Study guide* (plan, confirm with the
  model and the files it writes, background job, then the note opens beside), *+ Module notes* as context, and *Open in AI tab* with the Create
  panel filled in (`#/ai/create?…`).
- **A model per task.** *Settings › Connections › AI model* is a table: a default, and notes, study guide, audio script, chat and ask each on their
  own provider and model if you want (`llm.tasks.<task>` in `config.json`), with *Test* per row. `llm-check --task` does the same in a terminal.
- **`cli` provider**: any command that takes a prompt and prints the answer (`opencode run`, `ollama run`, `llm`, a script), with `{model}`,
  `{system}` and `{prompt}` placeholders. Also new: `local`, `key_env`, `env`, `timeout` options.
- **Every send names its model.** Confirms say which model is used and whether the text stays on this computer or is sent, and to whom. The
  Study assistant and *Ask* now ask once per model before the first message.
- **Chat page.** Claude Code working in a folder you pick, like the chat in a code editor: a module folder, the study folder, or any folder
  you add. Answers stream, tool calls (files read, searched, edited) are shown and can be opened, conversations continue (`--resume`) and are
  saved per project. Per conversation: the model and what Claude may do (read; read and edit; edit and run commands). `g k` opens it.
- **Study: resizable side panels.** Drag the inner edge of the file list or the assistant; the width is kept, double-click restores it.

### Changed
- Model endpoints are allowed per page (`LLM_ALLOW`): the Study page may now plan and write notes, but no longer call `check` or `ask`.
- **AI page rebuilt around one *Create* panel**: pick a module, make *Notes* or a *Study guide*; for notes choose per week or per category, the
  week and the kinds. *Ask* is now *Chat with the course* (a conversation, same assistant as in Study). *Library* and *Prompts* open from the
  header. *Check connection* is gone: each model has *Test* in Settings.
- **Notes per category (whole term)**: one note per category (`Notes/Lectures/Lectures - all weeks.md`), from the weekly notes when they are up
  to date, else from the course files in parts with a merge. `notes --group term`, or `notes.group` as the default. New prompts
  `category_notes` and `category_notes_merge`.
- **Less text.** Plan usage is one chip in the AI header (`Claude · 5h 12% · 7d 40%`; the tooltip has the time checked, extra usage and
  the app's 7-day tokens) instead of a sentence in three places; a confirm mentions usage only when a window is above 60%. Card intros are one
  line, long explanations moved to tooltips or Help, and a test keeps UI strings short.
- `services/usage.line()` is now `summary()` (data); `/api/ai/*` replies carry `usage` as that object, no longer as text.
- **Notes are written per week.** Lecture notes and workshop summaries are one note per week (`Notes/Lectures/Week 03.md`), made from every
  file of that week; the week comes from the folder, the file name, or Moodle's section. Files with no week, workshop code stubs and coursework
  notes stay one per file. Prompts `lecture_notes` and `workshop_summary` are version 2. Existing per-file notes are left as they are.
- The Tracker reads the `sources:` list of a weekly note, so every file it covers shows *Notes ✓*.
- **Collapsing the menu no longer moves its icons**: the column narrows smoothly, the labels fade, and every icon keeps its place.

### Fixed
- Chat page: with a local model as the default AI model, new conversations passed its name to Claude Code (`claude --model
  LFM2-…`), which refused it. Conversations now use a Claude model only: a non-Claude name (also one
  saved in an older conversation) is replaced by sonnet, or by the default model when that is a Claude one.
- **Tracker weeks follow the study folder.** A file's week is its `Week N` folder, else the week its name gives, then Moodle's section; it was
  Moodle's section only, so files a lecturer put in the wrong week were counted there. *Mark whole week reviewed* uses the same rule.
- Week numbers in file names are read in any case (`Week 3 slides.pdf`, not only `week3`), so such files are also placed in the right week folder.
- Folders are sorted in natural order (`Week 2` before `Week 10`) everywhere, and tree rows line up: files under their sibling folders' icons,
  folders without subfolders in *Download to…* / *Move to…*, files in the Study list under their folder heading.
- Study: the per-file "open beside" button used the class of the slide-over panel and, on hover, became a 380 px pale strip over the right
  tiles (with a stray ⧉ in the assistant).
- Study: section headers wrapped to two lines in some tiles and not others, so text started at different heights; a tab's close button
  could be cut off. Headers are now one line everywhere; the tabs scroll, and narrow sections compact their tools.
- Study: *Both* view stacks the text and the preview in a narrow section instead of squeezing two columns.

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
