"""Background generation (#11): the in-memory registry and the worker that persists.

Uses a simulated pipeline (mocked stream_events), so no model backend is needed.
"""

import threading
import time
from unittest import mock

from web import db
from web.services import gen_registry, generate, tests_store


# --- Job: replay + live + multi-subscriber ---------------------------------------

def test_subscribe_replays_then_follows_live():
    job = gen_registry.Job(test_id=1, user_id=1)
    job.emit({"type": "stage", "label": "a"})
    job.emit({"type": "token", "text": "x"})

    seen = []
    def consume():
        for ev in job.subscribe(timeout=2):
            seen.append(ev)
    t = threading.Thread(target=consume)
    t.start()

    time.sleep(0.05)                       # subscriber has replayed the first two
    job.emit({"type": "token", "text": "y"})
    job.emit({"type": "done"})
    job.finish()
    t.join(timeout=2)

    assert [e.get("text") or e.get("label") or e["type"] for e in seen] == ["a", "x", "y", "done"]


def test_multiple_subscribers_each_get_everything():
    job = gen_registry.Job(2, 1)
    results = [[], []]
    def consume(i):
        for ev in job.subscribe(timeout=2):
            results[i].append(ev["type"])
    threads = [threading.Thread(target=consume, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    for k in range(3):
        job.emit({"type": "token", "text": str(k)})
    job.emit({"type": "done"})
    job.finish()
    for t in threads:
        t.join(timeout=2)
    assert results[0] == results[1] == ["token", "token", "token", "done"]


def test_registry_retains_finished_job_then_prunes_stale():
    ran = threading.Event()
    def target(job):
        job.emit({"type": "stage", "label": "go"})
        ran.set()
    gen_registry.start(test_id=999, user_id=7, target=target)
    assert ran.wait(2)
    time.sleep(0.05)
    job = gen_registry.get(999)
    assert job is not None and job.done_at is not None   # retained after finishing (late attach can replay)
    # a late subscriber still gets the full replay and returns immediately
    assert [e["type"] for e in job.subscribe(timeout=2)] == ["stage"]
    # a stale finished job is pruned when the next generation starts
    job.done_at -= (gen_registry._GRACE_SECONDS + 1)
    gen_registry.start(test_id=1000, user_id=7, target=lambda j: None)
    time.sleep(0.05)
    assert gen_registry.get(999) is None


def test_get_is_user_scoped():
    job = gen_registry.Job(3, user_id=42)
    with mock.patch.dict(gen_registry._jobs, {3: job}, clear=False):
        assert gen_registry.get(3, user_id=42) is job
        assert gen_registry.get(3, user_id=99) is None    # not this user's job


# --- worker: persists on success, drops placeholder on failure -------------------

def _fake_stream(vm_final):
    def _gen(prompt, dev, language, meta, prov):
        yield {"type": "stage", "node": "generate", "label": "Writing the test…"}
        yield {"type": "token", "text": "import requests\n"}
        yield {"type": "final", "vm": vm_final}
    return _gen


class _StubJob:
    def __init__(self):
        self.events = []
    def emit(self, ev):
        self.events.append(ev)


def test_worker_persists_finished_test():
    from web.routers import app_view
    db.init()
    u = db.create_user("bgok", "hash")
    t = tests_store.create_generating(u["id"], "My test", "list orgs", "py", [])
    vm = generate._workspace_vm("list orgs", "import requests\n", "orgs.test.py",
                                ["GET /organizations"], "py",
                                {"ok": True, "method": "ast", "detail": "ok", "language": "python", "label": "Python"})
    vm["_log"] = []
    job = _StubJob()
    with mock.patch.object(app_view.generate, "stream_events", _fake_stream(vm)):
        app_view._run_generation(job, u["id"], t["id"], "My test", "list orgs",
                                 [], "[]", "py", {}, None)

    row = tests_store.get_test(u["id"], t["id"])
    assert row["status"] == "done"
    assert row["code"] == "import requests\n"
    assert row["validation_json"]                      # persisted
    done = job.events[-1]
    assert done["type"] == "done" and done["status"] == "done"
    assert done["item_html"] and str(t["id"]) in done["item_html"]


def test_worker_drops_placeholder_on_error():
    from web.routers import app_view
    db.init()
    u = db.create_user("bgerr", "hash")
    t = tests_store.create_generating(u["id"], "Bad", "x", "py", [])
    err_vm = {"error": "Couldn't reach the model backend (Ollama).", "prompt": "x", "_log": []}
    job = _StubJob()
    with mock.patch.object(app_view.generate, "stream_events", _fake_stream(err_vm)):
        app_view._run_generation(job, u["id"], t["id"], "Bad", "x", [], "[]", "py", {}, None)

    assert tests_store.get_test(u["id"], t["id"]) is None   # placeholder removed
    done = job.events[-1]
    assert done["status"] == "error" and done["item_html"] == ""
