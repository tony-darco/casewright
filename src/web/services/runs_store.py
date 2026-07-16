"""Per-user persistence for test runs (SQLite `runs` table).

A run is one invocation of a test's code (Run feature). Every call is scoped to
user_id — a user only ever sees or controls their own runs. Each run carries an
8-digit ``run_code`` used to name its ephemeral Meraki network; the code is random
with insert-time collision-retry (same idiom as the username-collision retry in
web.db).

Status lifecycle: queued -> provisioning -> running -> {success | error | failed},
where ``error`` means our own infrastructure failed (Meraki API, no inventory,
Docker) and ``failed`` means the container ran to completion but the test's own
assertions failed.
"""

import json
import random
import sqlite3

from web import db

_MAX_CODE_ATTEMPTS = 8


def _new_code() -> str:
    return f"{random.randint(0, 99_999_999):08d}"


def create_run(user_id, test_id, source, example_network_id="") -> dict:
    """Create a queued run with a unique 8-digit code. Retries on the rare code
    collision (UNIQUE constraint) before giving up."""
    for _ in range(_MAX_CODE_ATTEMPTS):
        code = _new_code()
        try:
            with db.cursor() as conn:
                cur = conn.execute(
                    "INSERT INTO runs (run_code, user_id, test_id, source, example_network_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (code, user_id, test_id, source, example_network_id),
                )
                return {"id": cur.lastrowid, "run_code": code, "status": "queued"}
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("could not allocate a unique run code")


def get_run(user_id, run_id):
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def list_runs_for_test(user_id, test_id) -> list:
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT id, run_code, status, source, error_message, created_at, finished_at "
            "FROM runs WHERE user_id = ? AND test_id = ? ORDER BY created_at DESC, id DESC",
            (user_id, test_id),
        ).fetchall()
    return [dict(r) for r in rows]


def update_status(run_id, status, error_message=None) -> None:
    """Advance a run's status. Stamps started_at on entering 'running' and
    finished_at on any terminal status."""
    sets = ["status = ?"]
    params = [status]
    if error_message is not None:
        sets.append("error_message = ?")
        params.append(error_message)
    if status == "running":
        sets.append("started_at = datetime('now')")
    if status in ("success", "error", "failed"):
        sets.append("finished_at = datetime('now')")
    params.append(run_id)
    with db.cursor() as conn:
        conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", params)


def set_network(run_id, network_id, org_id) -> None:
    with db.cursor() as conn:
        conn.execute(
            "UPDATE runs SET network_id = ?, org_id = ? WHERE id = ?",
            (network_id, org_id, run_id),
        )


def set_claimed_devices(run_id, claimed_devices) -> None:
    with db.cursor() as conn:
        conn.execute(
            "UPDATE runs SET claimed_devices_json = ? WHERE id = ?",
            (json.dumps(claimed_devices or []), run_id),
        )
