"""SQLite storage for users (and, from Step 2, per-user data).

One file, WAL mode, on a path that in Docker points at a mounted volume so it
survives container restarts (config.DB_PATH). Parameterized queries only. This is
the seam that a future Postgres backend would replace.
"""

import secrets
import sqlite3
from contextlib import contextmanager

from web import config


def new_slug() -> str:
    """An opaque, URL-safe token for a test's shareable-but-owner-locked page URL
    (/app/t/{slug}). Unguessable so the URL can't be enumerated; access is still
    owner-scoped in every query."""
    return secrets.token_urlsafe(12)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name    TEXT NOT NULL DEFAULT '',
    last_name     TEXT NOT NULL DEFAULT '',
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email         TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-user Meraki data (isolation: one row per user, always filtered by user_id).
-- api_key_enc is Fernet-encrypted (never plaintext); orgs_json holds the connected
-- orgs -> networks -> devices tree for that user.
CREATE TABLE IF NOT EXISTS meraki_data (
    user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    api_key_enc        TEXT,
    orgs_json          TEXT NOT NULL DEFAULT '[]',
    default_network_id TEXT NOT NULL DEFAULT ''
);

-- Per-user model-provider settings (Settings → Model provider). Blank/NULL text
-- fields mean "use the backend default" (the ProviderConfig defaults keep working
-- untouched — see web.services.provider_store). reasoning is nullable for the same
-- reason temperature is: NULL = unset = keep the backend default.
CREATE TABLE IF NOT EXISTS provider_settings (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL DEFAULT 'ollama',
    ollama_url  TEXT NOT NULL DEFAULT '',
    chat_model  TEXT NOT NULL DEFAULT '',
    embed_model TEXT NOT NULL DEFAULT '',
    temperature REAL,
    reasoning   INTEGER
);

-- Persisted generated tests (per user). Relational identifiers + a JSON column for
-- the flexible/document-ish bits (grounded endpoints, @device refs) — SQLite serves
-- the "document" role here, no second datastore needed.
CREATE TABLE IF NOT EXISTS tests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slug           TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_tests_user ON tests(user_id, created_at DESC);

-- Per-test generation logs (issue #8): pipeline stages + errors, tied to a test id
-- and scoped to a user. Deleted with the test/user via cascade. The app-wide log is
-- an in-memory ring buffer (web.services.logs_store), not persisted here.
CREATE TABLE IF NOT EXISTS test_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    test_id    INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL DEFAULT '',
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_test_logs_test ON test_logs(test_id, id);

-- Version history for a test (#12): a snapshot (prompt + code + metadata) is appended
-- on every successful generation, so regenerating preserves the prior prompt and code.
-- version_no is 0-based within a test. The tests row mirrors the latest version.
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

-- Knowledge-base embedding runs (Knowledge Base feature): one row per version, per
-- user isolation like tests. Re-embedding creates a new row/collection rather than
-- overwriting a prior version.
CREATE TABLE IF NOT EXISTS kb_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL DEFAULT '',
    source_kind     TEXT NOT NULL DEFAULT '',   -- 'upload' | 'link'
    source_label    TEXT NOT NULL DEFAULT '',   -- filename or URL
    split_method    TEXT NOT NULL DEFAULT '',   -- 'langchain' | 'custom'
    collection_name TEXT NOT NULL DEFAULT '',
    doc_count       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'embedding',  -- 'embedding' | 'done' | 'error'
    error_message   TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_kb_versions_user ON kb_versions(user_id, created_at DESC);

-- Per-user active knowledge-base version pointer (mirrors provider_settings' shape).
-- storage_kind/storage_url pick where the vector store itself lives: 'local' (inside
-- the application itself — ProviderConfig.persist_dir, default data/chroma) or
-- 'remote' (a Chroma server URL, wherever it happens to be hosted).
CREATE TABLE IF NOT EXISTS kb_settings (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
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
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id, created_at DESC);

-- Durable per-run logs (mirrors test_logs' role): the live push is SSE + an
-- in-memory queue (web.services.run_events); this table is the persistent/replay copy.
CREATE TABLE IF NOT EXISTS run_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL DEFAULT '',
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_run_logs_run ON run_logs(run_id, id);

-- Per-user ephemeral-container settings (Settings → Run / Containers). One row per
-- user, same pattern as provider_settings.
CREATE TABLE IF NOT EXISTS run_settings (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
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
        conn.execute("PRAGMA foreign_keys=OFF")  # migrations below may transiently break FKs
        conn.executescript(_SCHEMA)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "username" not in cols:
            _migrate_to_username(conn, cols)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        # add-column migrations for DBs already on the username schema
        for name, ddl in (("email", "TEXT NOT NULL DEFAULT '' COLLATE NOCASE"),
                          ("token_version", "INTEGER NOT NULL DEFAULT 0")):
            if name not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        # unique email, but allow many blanks (migrated rows) via a partial index
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email <> ''")
        # add-column migrations for the tests table (validation result, generation status)
        test_cols = {row[1] for row in conn.execute("PRAGMA table_info(tests)").fetchall()}
        if "validation_json" not in test_cols:
            conn.execute("ALTER TABLE tests ADD COLUMN validation_json TEXT NOT NULL DEFAULT ''")
        if "status" not in test_cols:
            conn.execute("ALTER TABLE tests ADD COLUMN status TEXT NOT NULL DEFAULT 'done'")
        # add-column migration for provider_settings (model "thinking" toggle)
        prov_cols = {row[1] for row in conn.execute("PRAGMA table_info(provider_settings)").fetchall()}
        if "reasoning" not in prov_cols:
            conn.execute("ALTER TABLE provider_settings ADD COLUMN reasoning INTEGER")
        # add-column migrations for kb_settings (storage location, Knowledge Base feature)
        kb_settings_cols = {row[1] for row in conn.execute("PRAGMA table_info(kb_settings)").fetchall()}
        for name, ddl in (("storage_kind", "TEXT NOT NULL DEFAULT 'local'"),
                          ("storage_url", "TEXT NOT NULL DEFAULT ''")):
            if name not in kb_settings_cols:
                conn.execute(f"ALTER TABLE kb_settings ADD COLUMN {name} {ddl}")
        # add-column migrations for the tests table (Run feature: hardware + run config)
        for name, ddl in (("hardware_json", "TEXT NOT NULL DEFAULT '[]'"),
                          ("run_source", "TEXT NOT NULL DEFAULT 'example'"),
                          ("source_network_id", "TEXT NOT NULL DEFAULT ''"),
                          ("gen_meta_json", "TEXT NOT NULL DEFAULT '{}'"),
                          ("dep_endpoints_json", "TEXT NOT NULL DEFAULT '[]'")):
            if name not in test_cols:
                conn.execute(f"ALTER TABLE tests ADD COLUMN {name} {ddl}")
        # tests gains an opaque URL slug; backfill existing rows so every test is
        # reachable at /app/t/{slug}. The unique index is created here (after the
        # column exists), not in _SCHEMA, so it doesn't run against a pre-slug table
        # (same idiom as idx_users_email above). Partial index so blank rows don't
        # collide before the backfill fills them in.
        if "slug" not in test_cols:
            conn.execute("ALTER TABLE tests ADD COLUMN slug TEXT NOT NULL DEFAULT ''")
        for (tid,) in conn.execute("SELECT id FROM tests WHERE slug = ''").fetchall():
            conn.execute("UPDATE tests SET slug = ? WHERE id = ?", (new_slug(), tid))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tests_slug ON tests(slug) WHERE slug <> ''")
        # runs gains the code version it executed (versioned output: each run is an
        # output tagged to the code version it ran, so the Output tab is version-scoped)
        run_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "version_no" not in run_cols:
            conn.execute("ALTER TABLE runs ADD COLUMN version_no INTEGER NOT NULL DEFAULT 0")
            # Backfill: assign each existing run to the code version that was live when it
            # ran (the newest version created at or before the run), so historical output
            # stays visible under the right version instead of collapsing onto v0. Runs
            # predating any version fall back to 0.
            conn.execute(
                "UPDATE runs SET version_no = COALESCE(("
                "  SELECT v.version_no FROM test_versions v"
                "  WHERE v.test_id = runs.test_id AND v.created_at <= runs.created_at"
                "  ORDER BY v.created_at DESC, v.version_no DESC LIMIT 1), 0)"
            )
        # meraki_data gains a default example network for the Run feature
        meraki_cols = {row[1] for row in conn.execute("PRAGMA table_info(meraki_data)").fetchall()}
        if "default_network_id" not in meraki_cols:
            conn.execute("ALTER TABLE meraki_data ADD COLUMN default_network_id TEXT NOT NULL DEFAULT ''")
        _repair_meraki_data_fk(conn)
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_to_username(conn, old_cols: set) -> None:
    """One-time migration from the old email/name schema to username/first_name/last_name."""
    # legacy_alter_table=ON stops SQLite from rewriting other tables' REFERENCES clauses
    # (e.g. meraki_data's FK) to point at the renamed table below.
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("ALTER TABLE users RENAME TO users_old")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    conn.executescript(_SCHEMA)
    old_rows = conn.execute("SELECT * FROM users_old").fetchall()
    for row in old_rows:
        row = dict(row)
        name = (row.get("name") or "").strip()
        first_name, _, last_name = name.partition(" ")
        username = (row.get("email") or "").split("@")[0] or f"user{row['id']}"
        try:
            conn.execute(
                "INSERT INTO users (id, first_name, last_name, username, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], first_name, last_name, username, row["password_hash"], row["created_at"]),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "INSERT INTO users (id, first_name, last_name, username, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], first_name, last_name, f"{username}{row['id']}", row["password_hash"], row["created_at"]),
            )
    conn.execute("DROP TABLE users_old")


def _repair_meraki_data_fk(conn) -> None:
    """Fix meraki_data if an earlier migration left its FK pointing at a table
    (e.g. users_old) that no longer exists — recreate it against users."""
    fk_rows = conn.execute("PRAGMA foreign_key_list(meraki_data)").fetchall()
    if not fk_rows or fk_rows[0]["table"] == "users":
        return
    conn.execute("ALTER TABLE meraki_data RENAME TO meraki_data_old")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meraki_data (user_id, api_key_enc, orgs_json, default_network_id) "
        "SELECT user_id, api_key_enc, orgs_json, default_network_id FROM meraki_data_old"
    )
    conn.execute("DROP TABLE meraki_data_old")


# --- users -----------------------------------------------------------------------

def create_user(username: str, password_hash: str, first_name: str = "",
                last_name: str = "", email: str = "") -> dict:
    with cursor() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, first_name, last_name, email) "
            "VALUES (?, ?, ?, ?, ?)",
            (username.strip(), password_hash, first_name.strip(), last_name.strip(), email.strip()),
        )
        return {
            "id": cur.lastrowid, "username": username.strip(), "email": email.strip(),
            "first_name": first_name.strip(), "last_name": last_name.strip(), "token_version": 0,
        }


def get_user_by_username(username: str):
    with cursor() as conn:
        row = conn.execute(
            "SELECT id, username, email, password_hash, first_name, last_name, token_version "
            "FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str):
    email = email.strip()
    if not email:
        return None
    with cursor() as conn:
        row = conn.execute(
            "SELECT id, username, email FROM users WHERE email = ? AND email <> ''", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int):
    with cursor() as conn:
        row = conn.execute(
            "SELECT id, username, email, first_name, last_name, token_version "
            "FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def update_profile(user_id: int, first_name: str, last_name: str, username: str, email: str) -> None:
    """Update the editable profile fields. May raise sqlite3.IntegrityError on a
    duplicate username/email — the caller checks first and also catches this."""
    with cursor() as conn:
        conn.execute(
            "UPDATE users SET first_name = ?, last_name = ?, username = ?, email = ? WHERE id = ?",
            (first_name.strip(), last_name.strip(), username.strip(), email.strip(), user_id),
        )


def update_password(user_id: int, password_hash: str) -> int:
    """Set a new password hash and bump token_version (invalidates existing JWTs).
    Returns the new token_version so the caller can re-issue the current session."""
    with cursor() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE id = ?",
            (password_hash, user_id),
        )
        row = conn.execute("SELECT token_version FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["token_version"] if row else 0


def get_password_hash(user_id: int) -> str:
    with cursor() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["password_hash"] if row else ""
