"""In-memory registry of running test runs (Run feature).

Same mechanism as gen_registry (a background thread with replay + live buffering via
a Job), but keyed by run_id — runs and generations must not share a key space. Reuses
gen_registry.Job so there's one buffering implementation to reason about.

In-memory + single-process. A restart drops running runs; the run row is left
mid-status and its teardown may not have completed.
"""

import threading
import time

from web.services.gen_registry import Job

_GRACE_SECONDS = 120   # keep a finished run's buffer so a late attach still replays it

_lock = threading.Lock()
_jobs = {}  # run_id -> Job


def _prune_locked():
    now = time.monotonic()
    stale = [rid for rid, j in _jobs.items() if j.done_at is not None and now - j.done_at > _GRACE_SECONDS]
    for rid in stale:
        _jobs.pop(rid, None)


def start(run_id, target):
    """Create a Job for ``run_id`` and run ``target(job)`` in a background daemon
    thread; the job is marked finished when the thread exits and kept briefly so a
    just-attaching subscriber still replays it."""
    job = Job(run_id)
    with _lock:
        _prune_locked()
        _jobs[run_id] = job

    def _run():
        try:
            target(job)
        finally:
            job.finish()

    threading.Thread(target=_run, name=f"run-{run_id}", daemon=True).start()
    return job


def get(run_id):
    """The live Job for run_id, or None."""
    with _lock:
        return _jobs.get(run_id)
