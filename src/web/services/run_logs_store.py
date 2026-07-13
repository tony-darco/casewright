"""Durable per-run logs (SQLite `run_logs` table).

The live log push during a run is SSE + an in-memory queue (web.services.run_events);
this module is the persistent/replay copy so a user who reloads mid-run (or opens a
finished run) still sees the full log. Mirrors the per-test half of logs_store.
"""

from web import db


def record(run_id, entries) -> None:
    """Persist run log entries (``[{stage, level, message}, ...]``)."""
    if not entries:
        return
    rows = [
        (run_id, e.get("stage", ""), e.get("level", "info"), e.get("message", ""))
        for e in entries
    ]
    with db.cursor() as conn:
        conn.executemany(
            "INSERT INTO run_logs (run_id, stage, level, message) VALUES (?, ?, ?, ?)",
            rows,
        )


def logs_for_run(run_id) -> list:
    """This run's log entries in order: ``[{stage, level, message, created_at}]``."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT stage, level, message, created_at FROM run_logs "
            "WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]
