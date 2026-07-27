"""Headless TUI smoke test (Textual Pilot): the workspace mounts and a stubbed
generation streams into the transcript and persists a version.

The pipeline is stubbed the same way the other tests stub it (no Ollama/Docker/Meraki),
so this runs offline.
"""

import asyncio
from unittest import mock

from web.services import generate as gen_svc
from tui.bootstrap import startup
from tui.app import CasewrightApp


def _fake_stream_events(prompt, devices, language="py", meta=None, overrides=None,
                        repair=None, endpoints=None, hardware=None):
    yield {"type": "stage", "node": "generate", "label": "Writing the test…"}
    for ch in "def test_ok():\n    assert True\n":
        yield {"type": "token", "text": ch}
    yield {"type": "final", "vm": gen_svc._workspace_vm(
        prompt, "def test_ok():\n    assert True\n", "ok.test.py",
        ["GET /organizations"], "py", None)}


def test_plain_text_generates_into_transcript():
    async def scenario():
        app = CasewrightApp(startup())
        async with app.run_test() as pilot:
            await pilot.pause()
            ws = app.screen

            # Plain text is a test description → it generates.
            with mock.patch.object(gen_svc, "stream_events", _fake_stream_events):
                ws.on_text("test the org endpoint")
                for _ in range(80):
                    await pilot.pause(0.05)
                    if not ws._busy:
                        break

            assert not ws._busy
            assert "assert True" in ws._code_buffer
            assert ws.current_test_id is not None
            assert ws._version_count == 1

    asyncio.run(scenario())
