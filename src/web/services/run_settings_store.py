"""Per-user ephemeral-container settings (Settings → Run / Containers).

One row per user in SQLite (db.run_settings), same pattern as provider_store. An
absent row means "use the defaults" — the same defaults the table declares — so the
runner subsystem always has a complete, valid config to work with.
"""

from web import db

DEFAULTS = {
    "python_image": "python:3.12-slim",
    "go_image": "golang:1.22-alpine",
    "script_image": "ubuntu:24.04",
    "timeout_seconds": 120,
    "cpu_limit": 1.0,
    "memory_limit_mb": 512,
    "cleanup_policy": "always",
}


def get_settings(user_id: int) -> dict:
    with db.cursor() as conn:
        r = conn.execute(
            "SELECT python_image, go_image, script_image, timeout_seconds, "
            "cpu_limit, memory_limit_mb, cleanup_policy FROM run_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return dict(r) if r else dict(DEFAULTS)


def save_settings(user_id: int, python_image, go_image, script_image,
                  timeout_seconds, cpu_limit, memory_limit_mb, cleanup_policy) -> None:
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO run_settings (user_id, python_image, go_image, script_image, "
            "timeout_seconds, cpu_limit, memory_limit_mb, cleanup_policy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET python_image = excluded.python_image, "
            "go_image = excluded.go_image, script_image = excluded.script_image, "
            "timeout_seconds = excluded.timeout_seconds, cpu_limit = excluded.cpu_limit, "
            "memory_limit_mb = excluded.memory_limit_mb, cleanup_policy = excluded.cleanup_policy",
            (user_id, python_image.strip(), go_image.strip(), script_image.strip(),
             int(timeout_seconds), float(cpu_limit), int(memory_limit_mb), cleanup_policy.strip()),
        )
