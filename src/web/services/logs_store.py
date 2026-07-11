"""Logging for the Logs page (issue #8).

Two sources, one module:

- **Per-test logs** — pipeline stages + errors for a single generation, persisted in
  ``test_logs`` (SQLite) tied to a test id and scoped to a user. A user only ever sees
  their own test logs.
- **App-wide log** — recent server log records, kept in a bounded in-memory ring
  buffer (a logging handler) rather than the DB: it's the whole application log, high
  volume, and not per-user. Bounded size doubles as retention.
"""

import logging
from collections import deque

from web import db

# --- per-test logs (persisted, per-user) -----------------------------------------


def record(user_id, test_id, entries) -> None:
    """Persist a generation's log entries (``[{stage, level, message}, ...]``)."""
    if not entries:
        return
    rows = [
        (user_id, test_id, e.get("stage", ""), e.get("level", "info"), e.get("message", ""))
        for e in entries
    ]
    with db.cursor() as conn:
        conn.executemany(
            "INSERT INTO test_logs (user_id, test_id, stage, level, message) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def tests_with_logs(user_id) -> list:
    """This user's tests that have logs, newest test first, each with its ordered
    entries: ``[{id, name, created_at, entries: [{stage, level, message, created_at}]}]``."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT l.test_id AS test_id, t.name AS name, t.created_at AS test_created_at, "
            "l.stage AS stage, l.level AS level, l.message AS message, l.created_at AS created_at "
            "FROM test_logs l JOIN tests t ON t.id = l.test_id "
            "WHERE l.user_id = ? ORDER BY t.created_at DESC, t.id DESC, l.id ASC",
            (user_id,),
        ).fetchall()

    grouped = {}
    for r in rows:
        g = grouped.get(r["test_id"])
        if g is None:
            g = grouped[r["test_id"]] = {
                "id": r["test_id"], "name": r["name"],
                "created_at": r["test_created_at"], "entries": [],
            }
        g["entries"].append({
            "stage": r["stage"], "level": r["level"],
            "message": r["message"], "created_at": r["created_at"],
        })
    return list(grouped.values())


# --- app-wide log (in-memory ring buffer) ----------------------------------------

_APP_LOG_CAPACITY = 500


class _RingBufferHandler(logging.Handler):
    """Keeps the most recent ``capacity`` formatted log records in memory."""

    def __init__(self, capacity=_APP_LOG_CAPACITY):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        try:
            self.buffer.append({
                "level": record.levelname,
                "name": record.name,
                "message": self.format(record),
                "time": self.formatter.formatTime(record) if self.formatter else "",
            })
        except Exception:  # never let logging raise
            self.handleError(record)


_handler = None


def install_app_log(level=logging.INFO) -> None:
    """Attach the ring-buffer handler to the root logger (once, at startup)."""
    global _handler
    if _handler is not None:
        return
    _handler = _RingBufferHandler()
    _handler.setLevel(level)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(_handler)
    if root.level > level or root.level == logging.NOTSET:
        root.setLevel(level)


def app_log_lines() -> list:
    """Recent app-wide log records, newest last (chronological)."""
    return list(_handler.buffer) if _handler is not None else []
