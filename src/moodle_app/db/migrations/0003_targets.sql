-- schema v3: where you asked a Moodle file to go before it is downloaded (kept across syncs)

CREATE TABLE file_targets (
  key        TEXT PRIMARY KEY,
  folder_rel TEXT NOT NULL
);
