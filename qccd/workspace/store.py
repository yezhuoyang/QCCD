"""SQLite persistence for one workspace (`.qccd/workspace.db`).

One database per workspace, WAL mode, every multi-row change in one transaction -- so a
crash leaves either the old state or the new one, never a revision without its event or a
prompt without its delivery row.  The service holds one connection behind one lock; the
workspace is a single-user local tool and the write rate is human speed, so a lock is the
honest amount of concurrency control.

Tables are grouped by the four state categories the design separates:

* design inputs      -- `branches`, `revisions`, `change_sets`, `idempotency`, `artifacts`
* collaboration      -- `notes` (prompts, comments, replies, sketches, all versioned),
                        `context_snapshots`, `work`
* browser/agent view -- `views`, `sessions`, `deliveries`
* generated results  -- `jobs`, `snapshots`, `submissions`, `approvals`

and `events` is the ordered log the browser and agents replay from a cursor.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

__all__ = ["Store", "SCHEMA_VERSION"]

SCHEMA_VERSION = 2
#: what each schema version adds to the one before (applied in order to an older database)
_MIGRATIONS = {
    # 2: general workspaces (2026-09-24) -- a design has a title its person chose; a snapshot and a
    # submission record the board (release id + digest) they were made for
    2: [("branches", "title", "TEXT"), ("snapshots", "task_release", "TEXT"), ("snapshots", "task_digest", "TEXT"),
        ("submissions", "task_digest", "TEXT")],
}

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS branches (
  name TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                 -- main | candidate
  head INTEGER NOT NULL,
  parent TEXT, parent_revision INTEGER,
  status TEXT NOT NULL,               -- open | adopted | discarded
  created_by TEXT, created_at REAL NOT NULL, note TEXT,
  title TEXT                          -- what the person calls the design (any text)
);
CREATE TABLE IF NOT EXISTS revisions (
  branch TEXT NOT NULL, revision INTEGER NOT NULL,
  change_set_id TEXT, state TEXT NOT NULL,
  input_digest TEXT NOT NULL, arch_digest TEXT,
  created_at REAL NOT NULL,
  PRIMARY KEY (branch, revision)
);
CREATE TABLE IF NOT EXISTS change_sets (
  id TEXT PRIMARY KEY, branch TEXT NOT NULL,
  base_revision INTEGER NOT NULL, revision INTEGER,
  request_id TEXT, actor TEXT NOT NULL, origin_prompt_id TEXT,
  summary TEXT, operations TEXT NOT NULL, diff TEXT NOT NULL, touched TEXT NOT NULL,
  diagnostics TEXT NOT NULL, status TEXT NOT NULL,   -- committed | undone
  undo_of TEXT, undone_by TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS cs_branch ON change_sets(branch, revision);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY, request_digest TEXT NOT NULL, response TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  digest TEXT PRIMARY KEY, media_type TEXT NOT NULL, size INTEGER NOT NULL,
  path TEXT NOT NULL, label TEXT, created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  id TEXT NOT NULL, version INTEGER NOT NULL,
  kind TEXT NOT NULL,                 -- prompt | comment | reply | sketch
  thread TEXT,                        -- the prompt/comment a reply belongs to
  status TEXT NOT NULL,               -- draft | sent | posted | resolved | withdrawn
  author TEXT NOT NULL,               -- json actor
  body TEXT NOT NULL,                 -- json payload (text, anchors, intent, ...)
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY (id, version)
);
CREATE INDEX IF NOT EXISTS notes_thread ON notes(thread);
CREATE TABLE IF NOT EXISTS context_snapshots (
  id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS prompt_links (
  prompt_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, actor TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS links_prompt ON prompt_links(prompt_id);
CREATE TABLE IF NOT EXISTS work (
  prompt_id TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT, updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS views (
  id TEXT PRIMARY KEY, branch TEXT NOT NULL, label TEXT,
  follow_agent INTEGER NOT NULL DEFAULT 1, target_session TEXT,
  state TEXT NOT NULL,                -- json: rendered_revision, selection, viewport ...
  created_at REAL NOT NULL, last_seen REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, client TEXT NOT NULL, mode TEXT NOT NULL, label TEXT,
  runtime_ref TEXT, branch TEXT NOT NULL, view_id TEXT,
  capabilities TEXT NOT NULL, status TEXT NOT NULL,
  write_fence INTEGER NOT NULL DEFAULT 0, fence_reason TEXT,
  created_at REAL NOT NULL, last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
  id TEXT PRIMARY KEY, prompt_id TEXT NOT NULL, prompt_version INTEGER NOT NULL,
  session_id TEXT NOT NULL, kind TEXT NOT NULL,       -- deliver | steer
  state TEXT NOT NULL,                -- queued | sending | accepted | uncertain | failed | cancelled
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL,
  external_ref TEXT, last_error TEXT, detail TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE (prompt_id, prompt_version, session_id, kind)
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, snapshot_id TEXT, profile TEXT,
  status TEXT NOT NULL,               -- queued | running | succeeded | failed | cancelled | timeout | internal_error
  progress TEXT, pid INTEGER, owner TEXT,
  idempotency_key TEXT UNIQUE, origin_prompt_id TEXT, actor TEXT NOT NULL,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  result TEXT, error TEXT,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, deadline REAL
);
CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY, branch TEXT NOT NULL, revision INTEGER NOT NULL,
  input_digest TEXT NOT NULL, bundle_digest TEXT, dir TEXT NOT NULL,
  created_at REAL NOT NULL, task_release TEXT, task_digest TEXT
);
CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, job_id TEXT, profile TEXT NOT NULL,
  task_release TEXT NOT NULL, status TEXT NOT NULL, report_digest TEXT,
  origin_prompt_id TEXT, actor TEXT NOT NULL, created_at REAL NOT NULL, task_digest TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, bundle_digest TEXT NOT NULL,
  params_digest TEXT NOT NULL, params TEXT NOT NULL, approved_by TEXT NOT NULL,
  created_at REAL NOT NULL, used_at REAL, publication TEXT
);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, type TEXT NOT NULL,
  branch TEXT, revision INTEGER, payload TEXT NOT NULL
);
"""


class Store:
    """A locked SQLite connection with small JSON helpers."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False,
                                  isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(_DDL)
            cur = self.get_meta("store_schema")
            if cur is None:
                self.set_meta("store_schema", str(SCHEMA_VERSION))
            elif int(cur) < SCHEMA_VERSION:
                self._migrate(int(cur))
            elif int(cur) != SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.path} has store schema {cur}; this build speaks {SCHEMA_VERSION} (a newer QCCD made it)")

    def _migrate(self, cur: int) -> None:
        """Bring an older database up to this schema, in one transaction; a column that is already
        there (a migration interrupted before its version was recorded) is left alone."""
        with self.tx() as db:
            for v in range(cur + 1, SCHEMA_VERSION + 1):
                for table, column, kind in _MIGRATIONS.get(v, []):
                    have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
                    if column not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
            db.execute("INSERT INTO meta(key, value) VALUES('store_schema', ?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))

    # -- transactions ------------------------------------------------------------

    def tx(self):
        """`with store.tx() as db:` -- BEGIN IMMEDIATE ... COMMIT, under the lock."""
        return _Tx(self)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # -- helpers -----------------------------------------------------------------

    def one(self, sql: str, args: Iterable = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.db.execute(sql, tuple(args)).fetchone()

    def all(self, sql: str, args: Iterable = ()) -> list:
        with self.lock:
            return self.db.execute(sql, tuple(args)).fetchall()

    def get_meta(self, key: str) -> str | None:
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


class _Tx:
    def __init__(self, store: Store):
        self.store = store

    def __enter__(self) -> sqlite3.Connection:
        self.store.lock.acquire()
        try:
            self.store.db.execute("BEGIN IMMEDIATE")
        except Exception:
            self.store.lock.release()
            raise
        return self.store.db

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self.store.db.execute("COMMIT")
            else:
                self.store.db.execute("ROLLBACK")
        finally:
            self.store.lock.release()
        return False


def dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def loads(s: str | None) -> Any:
    return None if s is None else json.loads(s)
