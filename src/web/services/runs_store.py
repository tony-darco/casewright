"""Persistence for test runs (SQLite `runs` table).

A run is one invocation of a test's code (Run feature). Each run carries an
8-digit ``run_code`` used to name its ephemeral Meraki network; the code is random
with insert-time collision-retry.

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


def create_run(test_id, source, example_network_id="", version_no=0) -> dict:
    """Create a queued run with a unique 8-digit code. Retries on the rare code
    collision (UNIQUE constraint) before giving up. ``version_no`` records which code
    version this run executes, so its output can be browsed under that version."""
    for _ in range(_MAX_CODE_ATTEMPTS):
        code = _new_code()
        try:
            with db.cursor() as conn:
                cur = conn.execute(
                    "INSERT INTO runs (run_code, test_id, source, example_network_id, version_no) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (code, test_id, source, example_network_id, version_no),
                )
                return {"id": cur.lastrowid, "run_code": code, "status": "queued"}
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("could not allocate a unique run code")


def get_run(run_id):
    with db.cursor() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs_for_test(test_id) -> list:
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT id, run_code, status, source, error_message, created_at, finished_at "
            "FROM runs WHERE test_id = ? ORDER BY created_at DESC, id DESC",
            (test_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def version_runs(test_id, version_no) -> list:
    """Runs of one code version, oldest-first — the browsable outputs the Output tab
    pages through for that version."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT id, run_code, status, error_message, created_at, finished_at "
            "FROM runs WHERE test_id = ? AND version_no = ? "
            "ORDER BY created_at ASC, id ASC",
            (test_id, version_no),
        ).fetchall()
    return [dict(r) for r in rows]


def output_nav(test_id, version_no, current_run_id) -> dict:
    """Output-navigation context for one code version: the version's runs plus the
    position of ``current_run_id`` within them, so the Output tab can render
    "run i of N" with prev/next/latest links. ``current_run_id`` None (no run yet)
    yields an empty nav."""
    runs = version_runs(test_id, version_no)
    ids = [r["id"] for r in runs]
    idx = ids.index(current_run_id) if current_run_id in ids else (len(ids) - 1)
    return {
        "version_runs": runs,
        "run_index": idx,                                    # 0-based, -1 when no runs
        "run_total": len(runs),
        "prev_run_id": ids[idx - 1] if idx > 0 else None,
        "next_run_id": ids[idx + 1] if 0 <= idx < len(ids) - 1 else None,
        "latest_run_id": ids[-1] if ids else None,
    }


def latest_status_by_test() -> dict:
    """test_id -> the status of that test's most recent *finished* run, for the
    coverage tree. Only terminal statuses count: an in-flight run says nothing about
    the endpoint yet, and letting it win would flip a passing test back to never-run.
    A bare column alongside MAX() comes from the matching row (a SQLite guarantee)."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT test_id, status, MAX(id) FROM runs "
            "WHERE status IN ('success', 'error', 'failed') GROUP BY test_id"
        ).fetchall()
    return {r["test_id"]: r["status"] for r in rows}


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
