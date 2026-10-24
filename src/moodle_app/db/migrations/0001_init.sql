-- moodle-app schema v1. Times are Unix seconds (UTC). Paths are relative to dest_root with '/' separators.

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL                       -- JSON
);

-- one row per Moodle course the user is enrolled in; `tracked` = matched to a local module folder and not excluded
CREATE TABLE courses (
  id          INTEGER PRIMARY KEY,          -- Moodle course id
  shortname   TEXT NOT NULL DEFAULT '',
  fullname    TEXT NOT NULL DEFAULT '',
  startdate   INTEGER NOT NULL DEFAULT 0,
  enddate     INTEGER NOT NULL DEFAULT 0,
  hidden      INTEGER NOT NULL DEFAULT 0,
  progress    INTEGER,                      -- Moodle's own completion progress, if the course uses it
  folder_rel  TEXT,                         -- module folder under dest_root, NULL when none matches
  tracked     INTEGER NOT NULL DEFAULT 0,
  first_seen  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL
);

CREATE TABLE sections (
  course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  section_no INTEGER NOT NULL,
  name      TEXT NOT NULL DEFAULT '',
  visible   INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (course_id, section_no)
);

-- every activity/resource in a course (a file, folder, assignment, page, url, forum, ...)
CREATE TABLE activities (
  cmid       INTEGER PRIMARY KEY,           -- course module id
  course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  section_no INTEGER NOT NULL DEFAULT 0,
  modname    TEXT NOT NULL DEFAULT '',
  instance   INTEGER,
  name       TEXT NOT NULL DEFAULT '',
  url        TEXT NOT NULL DEFAULT '',
  visible    INTEGER NOT NULL DEFAULT 1,
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL
);
CREATE INDEX idx_activities_course ON activities(course_id);

-- every file Moodle lists, plus what we did with it. Replaces the old .moodle_sync_state.json
CREATE TABLE files (
  key          TEXT PRIMARY KEY,            -- course:cmid:filepath+filename, stable across Moodle's revision bumps
  course_id    INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  cmid         INTEGER,
  section_no   INTEGER NOT NULL DEFAULT 0,
  sec_name     TEXT NOT NULL DEFAULT '',
  moodle_name  TEXT NOT NULL DEFAULT '',    -- activity name
  filename     TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL CHECK (status IN ('pending','downloaded','ignored')),
  adopted      INTEGER NOT NULL DEFAULT 0,  -- 1 = we found a copy you already had instead of downloading
  rel_path     TEXT NOT NULL DEFAULT '',    -- where it is (downloaded/adopted) or will go (pending)
  size         INTEGER NOT NULL DEFAULT 0,
  sig_modified INTEGER NOT NULL DEFAULT 0,  -- Moodle's timemodified of the copy we have
  sig_size     INTEGER NOT NULL DEFAULT 0,
  upd_modified INTEGER,                     -- set when Moodle has a newer version than the one we have
  upd_size     INTEGER,
  first_seen   INTEGER NOT NULL,
  last_seen    INTEGER NOT NULL
);
CREATE INDEX idx_files_course ON files(course_id, status);
CREATE INDEX idx_files_path ON files(rel_path);

CREATE TABLE deadlines (
  id          TEXT PRIMARY KEY,             -- stable: course:cmid:kind  (or event:<id>)
  course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  cmid        INTEGER,
  kind        TEXT NOT NULL,                -- assign | quiz_close | event
  title       TEXT NOT NULL,
  due_ts      INTEGER NOT NULL,
  cutoff_ts   INTEGER,
  allow_from_ts INTEGER,
  url         TEXT NOT NULL DEFAULT '',
  source      TEXT NOT NULL,                -- assign | quiz | calendar | manual
  status      TEXT NOT NULL DEFAULT 'open', -- open | submitted | graded   ('overdue' is derived: open and past due)
  grade_text  TEXT NOT NULL DEFAULT '',
  hidden      INTEGER NOT NULL DEFAULT 0,   -- you hid it
  done        INTEGER NOT NULL DEFAULT 0,   -- you ticked it
  note        TEXT NOT NULL DEFAULT '',
  seq         INTEGER NOT NULL DEFAULT 0,   -- bumped when the due date changes (ICS SEQUENCE)
  gone        INTEGER NOT NULL DEFAULT 0,   -- no longer returned by Moodle (kept for ICS CANCELLED)
  first_seen  INTEGER NOT NULL,
  updated     INTEGER NOT NULL
);
CREATE INDEX idx_deadlines_due ON deadlines(due_ts);

CREATE TABLE announcements (
  id          INTEGER PRIMARY KEY,          -- Moodle discussion id
  course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  forum_id    INTEGER,
  subject     TEXT NOT NULL DEFAULT '',
  message     TEXT NOT NULL DEFAULT '',     -- HTML as Moodle sends it
  author      TEXT NOT NULL DEFAULT '',
  created     INTEGER NOT NULL DEFAULT 0,
  modified    INTEGER NOT NULL DEFAULT 0,
  pinned      INTEGER NOT NULL DEFAULT 0,
  read        INTEGER NOT NULL DEFAULT 0,
  first_seen  INTEGER NOT NULL
);
CREATE INDEX idx_ann_course ON announcements(course_id, created);

-- one row per sync/notes/import run, and the changes it found (the History view)
CREATE TABLE runs (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  kind     TEXT NOT NULL,                   -- sync | notes | import
  started  INTEGER NOT NULL,
  finished INTEGER,
  ok       INTEGER,
  error    TEXT,
  summary  TEXT                             -- JSON
);
CREATE TABLE changes (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  ts        INTEGER NOT NULL,
  kind      TEXT NOT NULL,                  -- new_file | updated_file | downloaded | ignored | closed | moved | deadline_new | deadline_changed | announcement | ...
  course_id INTEGER,
  ref       TEXT NOT NULL DEFAULT '',
  detail    TEXT NOT NULL DEFAULT ''        -- JSON
);
CREATE INDEX idx_changes_ts ON changes(ts);

-- what the user has studied (the Tracker's "reviewed" signal); scope = file key or 'course:week'
CREATE TABLE reviewed (
  scope  TEXT NOT NULL,                     -- file | week
  ref    TEXT NOT NULL,
  ts     INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  PRIMARY KEY (scope, ref)
);

CREATE TABLE todos (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
  text      TEXT NOT NULL,
  done      INTEGER NOT NULL DEFAULT 0,
  due_ts    INTEGER,
  url       TEXT NOT NULL DEFAULT '',
  created   INTEGER NOT NULL
);

-- files written by `notes` and what they were made from
CREATE TABLE notes_manifest (
  rel          TEXT PRIMARY KEY,            -- output path relative to dest_root
  input_hash   TEXT NOT NULL,
  version      TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  generated    TEXT NOT NULL
);
