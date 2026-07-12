"""In-memory registry of running generations (#11).

A generation runs in a background thread, decoupled from the client's SSE
connection, so navigating away — or losing the connection entirely — doesn't stop
it. Each Job buffers its stream events; any number of clients can subscribe and
receive a full replay of what's happened so far, then live updates, so a returning
client reattaches seamlessly.

In-memory + single-process: fine for one uvicorn worker. A restart drops running
jobs (the placeholder test row is left ``generating``; the client then falls back to
the persisted row via the stream endpoint).
"""

import threading
import time

# Keep a finished job around this long so a client that attaches just after it ends
# still gets the full replay (esp. instant failures) rather than a "not found".
_GRACE_SECONDS = 60


class Job:
    """A running generation: an append-only event buffer plus a done flag, guarded
    by a Condition so subscribers can wait for new events."""

    def __init__(self, test_id, user_id):
        self.test_id = test_id
        self.user_id = user_id
        self._events = []          # the SSE event dicts, in order
        self._done = False
        self.done_at = None        # monotonic time the job finished (for pruning)
        self._cond = threading.Condition()

    def emit(self, event):
        with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    def finish(self):
        with self._cond:
            self._done = True
            self.done_at = time.monotonic()
            self._cond.notify_all()

    def subscribe(self, timeout=30):
        """Yield every event so far, then live ones, until the job is done. Safe for
        multiple concurrent subscribers (each keeps its own cursor)."""
        i = 0
        while True:
            with self._cond:
                while i >= len(self._events) and not self._done:
                    self._cond.wait(timeout)
                events = self._events[i:]
                i += len(events)
                done_and_drained = self._done and i >= len(self._events)
            for ev in events:
                yield ev
            if done_and_drained:
                return


_lock = threading.Lock()
_jobs = {}  # test_id -> Job


def _prune_locked():
    now = time.monotonic()
    stale = [tid for tid, j in _jobs.items() if j.done_at is not None and now - j.done_at > _GRACE_SECONDS]
    for tid in stale:
        _jobs.pop(tid, None)


def start(test_id, user_id, target):
    """Create a Job and run ``target(job)`` in a background daemon thread. ``target``
    emits events onto the job; the job is marked finished when the thread exits and
    kept for a short grace period so a just-attaching client still gets the replay."""
    job = Job(test_id, user_id)
    with _lock:
        _prune_locked()
        _jobs[test_id] = job

    def _run():
        try:
            target(job)
        finally:
            job.finish()   # retained (not removed) so a late attach still replays it

    threading.Thread(target=_run, name=f"gen-{test_id}", daemon=True).start()
    return job


def get(test_id, user_id=None):
    """The live Job for test_id, or None. If user_id is given, only returns the job
    when it belongs to that user (a user can't attach to another's generation)."""
    with _lock:
        job = _jobs.get(test_id)
    if job is not None and user_id is not None and job.user_id != user_id:
        return None
    return job
