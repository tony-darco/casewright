"""SQLite storage for users (and, from Step 2, per-user data).

One file, WAL mode, on a path that in Docker points at a mounted volume so it
survives container restarts (config.DB_PATH). Parameterized queries only. This is
the seam that a future Postgres backend would replace.
"""

import sqlite3
from contextlib import contextmanager

from web import config

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
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    api_key_enc TEXT,
    orgs_json   TEXT NOT NULL DEFAULT '[]'
);

-- Per-user model-provider settings (Settings → Model provider). Blank/NULL fields
-- mean "use the backend default" (the .env / ProviderConfig defaults keep working
-- untouched — see web.services.provider_store).
CREATE TABLE IF NOT EXISTS provider_settings (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL DEFAULT 'ollama',
    ollama_url  TEXT NOT NULL DEFAULT '',
    chat_model  TEXT NOT NULL DEFAULT '',
    embed_model TEXT NOT NULL DEFAULT '',
    temperature REAL
);

-- Persisted generated tests (per user). Relational identifiers + a JSON column for
-- the flexible/document-ish bits (grounded endpoints, @device refs) — SQLite serves
-- the "document" role here, no second datastore needed.
CREATE TABLE IF NOT EXISTS tests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name           TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    file_name      TEXT NOT NULL DEFAULT '',
    code           TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT '',
    endpoints_json TEXT NOT NULL DEFAULT '[]',
    devices_json   TEXT NOT NULL DEFAULT '[]',
    validation_json TEXT NOT NULL DEFAULT '',
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
        # add-column migration: per-test language-validation result (JSON), added later
        test_cols = {row[1] for row in conn.execute("PRAGMA table_info(tests)").fetchall()}
        if "validation_json" not in test_cols:
            conn.execute("ALTER TABLE tests ADD COLUMN validation_json TEXT NOT NULL DEFAULT ''")
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
        "INSERT INTO meraki_data (user_id, api_key_enc, orgs_json) "
        "SELECT user_id, api_key_enc, orgs_json FROM meraki_data_old"
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
