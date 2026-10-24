-- your own calendar entries (study blocks, exams you add by hand, reminders). Moodle never writes here.
CREATE TABLE events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  title     TEXT NOT NULL,
  start_ts  INTEGER NOT NULL,
  end_ts    INTEGER,
  all_day   INTEGER NOT NULL DEFAULT 0,
  course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
  note      TEXT NOT NULL DEFAULT '',
  created   INTEGER NOT NULL
);
CREATE INDEX idx_events_start ON events(start_ts);
