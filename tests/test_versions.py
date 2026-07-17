"""Test version history (#12): snapshots on (re)generation + read-only viewing."""

from unittest import mock

from web import db
from web.routers import app_view
from web.services import generate, tests_store


def _fake_stream(code, prompt="p"):
    vm = generate._workspace_vm(prompt, code, "f.test.py", ["GET /x"], "py", None)
    vm["_log"] = []

    def _gen(prompt_, dev, language, meta, prov, repair=None, endpoints=None):
        yield {"type": "token", "text": code}
        yield {"type": "final", "vm": vm}
    return _gen


class _StubJob:
    def __init__(self):
        self.events = []
    def emit(self, ev):
        self.events.append(ev)


# --- store --------------------------------------------------------------------

def test_add_list_get_versions():
    db.init()
    u = db.create_user("verstore", "h")
    t = tests_store.create_generating(u["id"], "T", "p0", "py", [])
    assert tests_store.add_version(t["id"], "p0", "f.py", "code0", "py", [], None) == 0
    assert tests_store.add_version(t["id"], "p1", "f.py", "code1", "py", [], None) == 1
    vs = tests_store.list_versions(u["id"], t["id"])
    assert [v["version_no"] for v in vs] == [0, 1]
    assert tests_store.get_version(u["id"], t["id"], 0)["code"] == "code0"
    assert tests_store.get_version(u["id"], t["id"], 1)["prompt"] == "p1"


def test_versions_user_scoped():
    db.init()
    owner, other = db.create_user("vo", "h"), db.create_user("vp", "h")
    t = tests_store.create_generating(owner["id"], "T", "p", "py", [])
    tests_store.add_version(t["id"], "p", "f", "c", "py", [], None)
    assert tests_store.list_versions(other["id"], t["id"]) == []
    assert tests_store.get_version(other["id"], t["id"], 0) is None


def test_restart_generation_updates_prompt_and_status():
    db.init()
    u = db.create_user("vrestart", "h")
    t = tests_store.create_generating(u["id"], "T", "old prompt", "py", [])
    tests_store.finish_test(u["id"], t["id"], "f", "c", [], None, "done")
    r = tests_store.restart_generation(u["id"], t["id"], "new prompt", "ts")
    assert r["id"] == t["id"]
    row = tests_store.get_test(u["id"], t["id"])
    assert row["status"] == "generating" and row["prompt"] == "new prompt" and row["language"] == "ts"


# --- read-only view model -----------------------------------------------------

def test_view_model_from_version_readonly_flags():
    v0 = {"version_no": 0, "prompt": "p", "code": "c", "file_name": "f", "language": "py",
          "endpoints_json": "[]", "validation_json": ""}
    vm0 = generate.view_model_from_version(v0, 5, "T", 2)
    assert vm0["is_latest"] is False and vm0["readonly"] is True and vm0["version_count"] == 2
    v1 = dict(v0, version_no=1)
    vm1 = generate.view_model_from_version(v1, 5, "T", 2)
    assert vm1["is_latest"] is True and vm1["readonly"] is False


# --- worker appends a version per (re)generation ------------------------------

def test_regenerate_snapshots_each_version():
    db.init()
    u = db.create_user("verworker", "h")
    t = tests_store.create_generating(u["id"], "T", "v0 prompt", "py", [])
    tid = t["id"]

    # first generation -> version 0
    job = _StubJob()
    with mock.patch.object(app_view.generate, "stream_events", _fake_stream("code v0", "v0 prompt")):
        app_view._run_generation(job, u["id"], tid, "T", "v0 prompt", [], "[]", "py", {}, None)
    # regenerate into the same test -> version 1
    tests_store.restart_generation(u["id"], tid, "v1 prompt", "py")
    job2 = _StubJob()
    with mock.patch.object(app_view.generate, "stream_events", _fake_stream("code v1", "v1 prompt")):
        app_view._run_generation(job2, u["id"], tid, "T", "v1 prompt", [], "[]", "py", {}, None)

    vs = tests_store.list_versions(u["id"], tid)
    assert len(vs) == 2
    assert tests_store.get_version(u["id"], tid, 0)["code"] == "code v0"
    assert tests_store.get_version(u["id"], tid, 1)["code"] == "code v1"
    # the tests row mirrors the latest
    assert tests_store.get_test(u["id"], tid)["code"] == "code v1"
    # the rendered panel shows the version bar now that there are 2 versions
    assert "version-bar" in job2.events[-1]["panel_html"]
