"""SQLite storage for a single-user local tool.

One file, WAL mode, on a path that in Docker points at a mounted volume so it
survives container restarts (config.DB_PATH). Parameterized queries only.

There is no user concept. casewright is a local TUI used by one person, so
nothing is owned: a test is a test, a knowledge base is a knowledge base, and the
thing worth tracking about either is its *version*, not its owner. The four
settings tables (meraki_data, provider_settings, kb_settings, run_settings) are
single-row by construction — ``CHECK (id = 1)`` — rather than one row per user.
"""

import sqlite3
from contextlib import contextmanager

from web import config

_SCHEMA = """
-- Meraki integration. api_key_enc is Fernet-encrypted (never plaintext);
-- orgs_json holds the connected orgs -> networks -> devices tree.
CREATE TABLE IF NOT EXISTS meraki_data (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    api_key_enc        TEXT,
    orgs_json          TEXT NOT NULL DEFAULT '[]',
    default_network_id TEXT NOT NULL DEFAULT ''
);

-- Model-provider settings (Settings -> Model provider). Blank/NULL text fields
-- mean "use the backend default" (the ProviderConfig defaults keep working
-- untouched — see web.services.provider_store). reasoning is nullable for the
-- same reason temperature is: NULL = unset = keep the backend default.
CREATE TABLE IF NOT EXISTS provider_settings (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    provider    TEXT NOT NULL DEFAULT 'ollama',
    ollama_url  TEXT NOT NULL DEFAULT '',
    chat_model  TEXT NOT NULL DEFAULT '',
    embed_model TEXT NOT NULL DEFAULT '',
    temperature REAL,
    reasoning   INTEGER
);

-- Persisted generated tests. Relational identifiers + JSON columns for the
-- flexible/document-ish bits (grounded endpoints, device refs) — SQLite serves
-- the "document" role here, no second datastore needed.
CREATE TABLE IF NOT EXISTS tests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    file_name      TEXT NOT NULL DEFAULT '',
    code           TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT '',
    endpoints_json TEXT NOT NULL DEFAULT '[]',
    devices_json   TEXT NOT NULL DEFAULT '[]',
    validation_json TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'done',
    hardware_json  TEXT NOT NULL DEFAULT '[]',
    run_source     TEXT NOT NULL DEFAULT 'example',
    source_network_id TEXT NOT NULL DEFAULT '',
    gen_meta_json  TEXT NOT NULL DEFAULT '{}',
    dep_endpoints_json TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tests_created ON tests(created_at DESC);

-- Per-test generation logs: pipeline stages + errors, tied to a test id and
-- deleted with it. The app-wide log is an in-memory ring buffer
-- (web.services.logs_store), not persisted here.
CREATE TABLE IF NOT EXISTS test_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id    INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL DEFAULT '',
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_test_logs_test ON test_logs(test_id, id);

-- Version history for a test: a snapshot (prompt + code + metadata) is appended
-- on every successful generation, so regenerating preserves the prior prompt and
-- code. version_no is 0-based within a test. The tests row mirrors the latest.
CREATE TABLE IF NOT EXISTS test_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id         INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    version_no      INTEGER NOT NULL,
    prompt          TEXT NOT NULL DEFAULT '',
    file_name       TEXT NOT NULL DEFAULT '',
    code            TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL DEFAULT '',
    endpoints_json  TEXT NOT NULL DEFAULT '[]',
    validation_json TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_versions ON test_versions(test_id, version_no);

-- Knowledge-base embedding runs: one row per version. Re-embedding creates a new
-- row (and a new Chroma collection) rather than overwriting a prior version, so
-- you can switch back to the corpus a test was originally grounded against.
CREATE TABLE IF NOT EXISTS kb_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL DEFAULT '',
    source_kind     TEXT NOT NULL DEFAULT '',   -- 'file' | 'url'
    source_label    TEXT NOT NULL DEFAULT '',   -- filename or URL
    split_method    TEXT NOT NULL DEFAULT '',   -- 'langchain' | 'custom'
    collection_name TEXT NOT NULL DEFAULT '',
    doc_count       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'embedding',  -- 'embedding' | 'done' | 'error'
    error_message   TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_kb_versions_created ON kb_versions(created_at DESC);

-- Which knowledge-base version generation retrieves against, and where the
-- vector store itself lives: 'local' (inside the application —
-- ProviderConfig.persist_dir, default data/chroma) or 'remote' (a Chroma server).
CREATE TABLE IF NOT EXISTS kb_settings (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    active_version_id INTEGER REFERENCES kb_versions(id) ON DELETE SET NULL,
    storage_kind      TEXT NOT NULL DEFAULT 'local',   -- 'local' | 'remote'
    storage_url       TEXT NOT NULL DEFAULT ''
);

-- One row per invocation of a test's code (Run feature). A test can be run many
-- times; each run gets its own ephemeral Meraki network + claimed hardware,
-- identified by an 8-digit run_code used to name the network (``run-{run_code}``).
CREATE TABLE IF NOT EXISTS runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_code             TEXT NOT NULL UNIQUE,
    test_id              INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    status               TEXT NOT NULL DEFAULT 'queued',   -- queued|provisioning|running|success|error|failed
    source               TEXT NOT NULL DEFAULT 'example',  -- example|scratch
    version_no           INTEGER NOT NULL DEFAULT 0,        -- the code version this run executed
    example_network_id   TEXT NOT NULL DEFAULT '',
    org_id               TEXT NOT NULL DEFAULT '',
    network_id           TEXT NOT NULL DEFAULT '',         -- the ephemeral network, once provisioned
    claimed_devices_json TEXT NOT NULL DEFAULT '[]',       -- [{serial, model, hardwareType, row}]
    error_message        TEXT NOT NULL DEFAULT '',
    started_at           TEXT,
    finished_at          TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_test ON runs(test_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);

-- Durable per-run logs (mirrors test_logs' role): the live push is an in-memory
-- queue (web.services.run_registry); this table is the persistent/replay copy.
CREATE TABLE IF NOT EXISTS run_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL DEFAULT '',
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_run_logs_run ON run_logs(run_id, id);

-- Ephemeral-container settings (Settings -> Run / Containers).
CREATE TABLE IF NOT EXISTS run_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    python_image    TEXT NOT NULL DEFAULT 'python:3.12-slim',
    go_image        TEXT NOT NULL DEFAULT 'golang:1.22-alpine',
    script_image    TEXT NOT NULL DEFAULT 'ubuntu:24.04',
    timeout_seconds INTEGER NOT NULL DEFAULT 120,
    cpu_limit       REAL NOT NULL DEFAULT 1.0,
    memory_limit_mb INTEGER NOT NULL DEFAULT 512,
    cleanup_policy  TEXT NOT NULL DEFAULT 'always'         -- always|on_success|never
);
"""


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def cursor():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    """Create tables if missing. Called once at app startup."""
    with cursor() as conn:
        conn.executescript(_SCHEMA)
