"""
migrate_pbis.py
One-time migration: add epics, pbis tables and pbi_id column to existing DB.
Safe to re-run — all operations are IF NOT EXISTS / column-check guarded.
"""
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

# Add pbi_id column to tasks first (index creation below depends on it)
cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
if "pbi_id" not in cols:
    conn.execute("ALTER TABLE tasks ADD COLUMN pbi_id TEXT")
    conn.commit()
    print("Added pbi_id column to tasks")
else:
    print("pbi_id column already exists")

conn.executescript("""
CREATE TABLE IF NOT EXISTS epics (
    id         TEXT PRIMARY KEY,
    project    TEXT NOT NULL,
    name       TEXT NOT NULL,
    color      TEXT DEFAULT '#6366f1',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS pbis (
    id                   TEXT PRIMARY KEY,
    epic_id              TEXT REFERENCES epics(id),
    project              TEXT NOT NULL,
    title                TEXT NOT NULL,
    description          TEXT DEFAULT '',
    acceptance_criteria  TEXT DEFAULT '',
    affected_files       TEXT DEFAULT '[]',
    status               TEXT DEFAULT 'active',
    created_at           TEXT
);

CREATE INDEX IF NOT EXISTS idx_pbis_epic    ON pbis(epic_id);
CREATE INDEX IF NOT EXISTS idx_pbis_project ON pbis(project);
CREATE INDEX IF NOT EXISTS idx_pbis_status  ON pbis(status);
CREATE INDEX IF NOT EXISTS idx_pbi_id       ON tasks(pbi_id);
""")

conn.commit()
conn.close()
print("Migration complete.")
