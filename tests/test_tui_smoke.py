"""Headless TUI smoke test (Textual Pilot): the workspace mounts and a stubbed
generation streams into the transcript and persists a version.

The pipeline is stubbed the same way the other tests stub it (no Ollama/Docker/Meraki),
so this runs offline.
"""

import asyncio
import json
from unittest import mock

from web.services import generate as gen_svc, tests_store
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


def test_serial_typed_into_the_prompt_targets_that_device():
    """A serial in the description is picked up by the workspace itself — no '@', no
    picker — and the test is generated against that one device."""
    async def scenario():
        app = CasewrightApp(startup())
        async with app.run_test() as pilot:
            await pilot.pause()
            ws = app.screen

            with mock.patch.object(gen_svc, "stream_events", _fake_stream_events):
                ws.on_text("Verify the SSID is broadcasting on Q2KD-DEMR-82P7, an MR42")
                for _ in range(80):
                    await pilot.pause(0.05)
                    if not ws._busy:
                        break

            test = tests_store.get_test(app.uid, ws.current_test_id)
            devices = json.loads(test["devices_json"])
            assert [(d["serial"], d["model"]) for d in devices] == [("Q2KD-DEMR-82P7", "MR42")]

    asyncio.run(scenario())


def test_settings_screen_reads_and_writes_the_config_file():
    """The Settings screen is populated from config.yaml and saves straight back to
    it — no database round trip in between."""
    from textual.widgets import Input

    from web import settings
    from web.services import run_settings_store
    from tui.screens.settings import SettingsScreen

    run_settings_store.save_settings("python:3.11-slim", "golang:1.22-alpine",
                                     "ubuntu:24.04", 90, 1.0, 256, "never")

    async def scenario():
        app = CasewrightApp(startup())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(SettingsScreen())
            await pilot.pause()
            screen = app.screen

            # populated from the file, not from defaults
            assert screen.query_one("#py-image", Input).value == "python:3.11-slim"
            assert screen.query_one("#timeout", Input).value == "90"

            screen.query_one("#py-image", Input).value = "python:3.13-slim"
            screen._save_run_settings()

    asyncio.run(scenario())

    assert settings.section("run")["python_image"] == "python:3.13-slim"
    on_disk = settings.CONFIG_PATH.read_text()
    assert "python:3.13-slim" in on_disk and "cleanup_policy: never" in on_disk
