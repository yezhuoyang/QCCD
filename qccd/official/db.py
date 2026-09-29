"""One small database layer for SQLite and PostgreSQL.

SQL is written once with `?` placeholders and translated for psycopg (`%s`).  Only the
features both engines share are used; the two engine-specific points are the claim of a
queued job (`claim_sql`) and auto-increment keys (avoided: ids are random text).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

__all__ = ["Database", "SCHEMA", "COLUMNS"]

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS releases (
         id TEXT PRIMARY KEY, digest TEXT NOT NULL, path TEXT NOT NULL, active INTEGER NOT NULL,
         evaluator_policy TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS uploaders (
         id TEXT PRIMARY KEY, name TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE,
         quota_per_day INTEGER NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,
         created_at DOUBLE PRECISION NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS submissions (
         id TEXT PRIMARY KEY, uploader TEXT NOT NULL, task TEXT NOT NULL, task_digest TEXT NOT NULL,
         bundle_digest TEXT NOT NULL, archive_sha256 TEXT NOT NULL, visibility TEXT NOT NULL,
         display_name TEXT NOT NULL, status TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
         UNIQUE (uploader, task, bundle_digest))""",
    """CREATE TABLE IF NOT EXISTS jobs (
         id TEXT PRIMARY KEY, submission TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
         locked_by TEXT, locked_at DOUBLE PRECISION, heartbeat DOUBLE PRECISION, error TEXT,
         created_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION)""",
    """CREATE TABLE IF NOT EXISTS reports (
         id TEXT PRIMARY KEY, submission TEXT NOT NULL, evaluator_version TEXT NOT NULL,
         evaluator_policy TEXT NOT NULL, report_digest TEXT NOT NULL, report TEXT NOT NULL,
         eligible INTEGER NOT NULL, rank_value DOUBLE PRECISION, created_at DOUBLE PRECISION NOT NULL,
         superseded_by TEXT)""",
    # a workspace signing in with a qccd.academy account (accounts.py): the workspace holds the
    # poll secret, the person approves the code on the site, the workspace collects its key once
    """CREATE TABLE IF NOT EXISTS links (
         code TEXT PRIMARY KEY, poll_sha256 TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL,
         account_id TEXT, account_name TEXT, key_id TEXT, ip TEXT,
         created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
         approved_at DOUBLE PRECISION, delivered_at DOUBLE PRECISION)""",
]

#: columns added after a table was first deployed: (table, column, type).  An existing database
#: gains them on start; a new one is made with them.
COLUMNS = [
    # an uploader is a maintainer's token (kind 'maintainer') or a person's key from signing in
    # with their site account (kind 'account'), which names the account it submits for
    ("uploaders", "kind", "TEXT"), ("uploaders", "account_id", "TEXT"), ("uploaders", "account_name", "TEXT"),
    ("uploaders", "label", "TEXT"), ("uploaders", "last_used_at", "DOUBLE PRECISION"),
    # who a submission is credited to on the leaderboard
    ("submissions", "account_id", "TEXT"), ("submissions", "credit", "TEXT"),
]


class Database:
    def __init__(self, url: str):
        self.url = url
        self.lock = threading.RLock()
        if url.startswith("sqlite:///"):
            path = Path(url[len("sqlite:///"):])
            path.parent.mkdir(parents=True, exist_ok=True)
            self.kind = "sqlite"
            self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
        elif url.startswith("postgresql://") or url.startswith("postgres://"):
            import psycopg  # the deployment image installs it; tests use SQLite
            from psycopg.rows import dict_row
            self.kind = "postgres"
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        else:
            raise ValueError("database URL must be sqlite:///path or postgresql://...")
        for stmt in SCHEMA:
            self.execute(stmt)
        for table, column, kind in COLUMNS:
            self.ensure_column(table, column, kind)

    def ensure_column(self, table: str, column: str, kind: str) -> None:
        if self.kind == "postgres":
            self.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {kind}")
            return
        have = {r["name"] for r in self.all(f"PRAGMA table_info({table})")}
        if column not in have:
            self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def execute(self, sql: str, args=()):
        with self.lock:
            cur = self.conn.execute(self._sql(sql), tuple(args))
            return cur

    def one(self, sql: str, args=()):
        with self.lock:
            r = self.conn.execute(self._sql(sql), tuple(args)).fetchone()
            return dict(r) if r is not None else None

    def all(self, sql: str, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(self._sql(sql), tuple(args)).fetchall()]

    @contextmanager
    def tx(self):
        with self.lock:
            if self.kind == "sqlite":
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    yield self
                    self.conn.execute("COMMIT")
                except BaseException:
                    self.conn.execute("ROLLBACK")
                    raise
            else:
                with self.conn.transaction():
                    yield self

    def claim_sql(self) -> str:
        """Select one queued job for update, skipping rows another worker holds."""
        if self.kind == "postgres":
            return ("SELECT id, submission, attempts FROM jobs WHERE status='queued' ORDER BY created_at "
                    "LIMIT 1 FOR UPDATE SKIP LOCKED")
        return "SELECT id, submission, attempts FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
