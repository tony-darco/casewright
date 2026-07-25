"""Headless TUI smoke test (Textual Pilot): the workspace mounts, tabs switch, and a
stubbed generation streams tokens into the code view and persists a version.

The pipeline is stubbed the same way the other tests stub it (no Ollama/Docker/Meraki),
so this runs offline.
"""

import asyncio
from unittest import mock

from textual.widgets import TabbedContent, TextArea

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


def test_workspace_mounts_and_streams_generation():
    async def scenario():
        app = CasewrightApp(startup())
        async with app.run_test() as pilot:
            await pilot.pause()
            ws = app.screen
            tabs = ws.query_one("#tabs", TabbedContent)
            assert tabs.active == "tab-prompt"

            tabs.active = "tab-code"
            await pilot.pause()
            assert tabs.active == "tab-code"

            ws.query_one("#composer", TextArea).load_text("test the org endpoint")
            with mock.patch.object(gen_svc, "stream_events", _fake_stream_events):
                ws.action_generate()
                for _ in range(80):
                    await pilot.pause(0.05)
                    if not ws._busy:
                        break

            assert not ws._busy
            assert "assert True" in ws.query_one("#code", TextArea).text
            assert ws.current_test_id is not None
            assert ws._version_count == 1

    asyncio.run(scenario())
