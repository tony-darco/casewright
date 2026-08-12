"""In-memory registry of running knowledge-base embedding jobs (Knowledge Base
feature).

Same mechanism as gen_registry (a background thread with replay + live buffering via
a Job), but keyed by kb_versions.id instead of tests.id — reuses gen_registry.Job
directly (nothing in it is actually test-specific), but keeps its own _jobs dict so a
test id and a KB version id can never collide in the same process-global registry.

In-memory + single-process. A restart drops running jobs (the version row is left
'embedding', and the screen falls back to that persisted status).
"""

import threading
import time

from web.services.gen_registry import Job

_GRACE_SECONDS = 60

_lock = threading.Lock()
_jobs = {}  # version_id -> Job


def _prune_locked():
    now = time.monotonic()
    stale = [vid for vid, j in _jobs.items() if j.done_at is not None and now - j.done_at > _GRACE_SECONDS]
    for vid in stale:
        _jobs.pop(vid, None)


def start(version_id, target):
    """Create a Job and run ``target(job)`` in a background daemon thread."""
    job = Job(version_id)
    with _lock:
        _prune_locked()
        _jobs[version_id] = job

    def _run():
        try:
            target(job)
        finally:
            job.finish()

    threading.Thread(target=_run, name=f"kb-{version_id}", daemon=True).start()
    return job


def get(version_id):
    """The live Job for version_id, or None."""
    with _lock:
        return _jobs.get(version_id)
