"""Runner subsystem: literal-substitution injection, registry resolution, and the
Docker lifecycle (with the SDK mocked — no real containers)."""

from unittest import mock

import pytest

from web.services.runners import base, registry
from web.services.runners.inject import inject_run_values


# --- inject (serials always; network id only as a legacy-literal fallback) -----------

def test_inject_swaps_legacy_network_literal_and_serials():
    """A legacy test baked a network id in; the swap keeps its writes on the ephemeral
    network. New code has no literal and instead reads MERAKI_NETWORK_ID from the env."""
    code = 'NET = "L_old_1"\nSERIAL = "Q2AA-BBBB-CCCC"\n'
    gen_meta = {"network_ids": ["L_old_1"]}
    orig = [{"serial": "Q2AA-BBBB-CCCC", "model": "MR33"}]
    claimed = [{"serial": "Q2ZZ-YYYY-XXXX", "hardwareType": "wireless"}]
    out = inject_run_values(code, gen_meta, orig, claimed, "L_new_9")
    assert "L_new_9" in out and "L_old_1" not in out
    assert "Q2ZZ-YYYY-XXXX" in out and "Q2AA-BBBB-CCCC" not in out


def test_inject_pairs_by_hardware_type():
    code = "a=AP1; b=CAM1"
    orig = [{"serial": "AP1", "model": "MR44"}, {"serial": "CAM1", "model": "MV12"}]
    claimed = [{"serial": "NEWCAM", "hardwareType": "camera"},
               {"serial": "NEWAP", "hardwareType": "wireless"}]
    out = inject_run_values(code, {}, orig, claimed, "")
    assert "NEWAP" in out and "NEWCAM" in out


def test_inject_noop_without_metadata():
    assert inject_run_values("x=1", {}, [], [], "L_new") == "x=1"


# --- inject: serial tokens (the current contract) -----------------------------------

def test_inject_resolves_token_from_claimed_device():
    """A type-only hardware row has no serial until a run claims one, so its token
    survives generation and is resolved here."""
    code = 'SERIAL = "{{DEVICE_SERIAL_1}}"'
    claimed = [{"serial": "Q2NEW-AAAA", "hardwareType": "wireless", "row": 1}]
    out = inject_run_values(code, {}, [], claimed, "L_new")
    assert out == 'SERIAL = "Q2NEW-AAAA"'


def test_inject_maps_tokens_by_hardware_row_not_claim_order():
    """Claims happen pinned-first, which is NOT hardware-row order — so the recorded row
    is what makes {{DEVICE_SERIAL_N}} land on the right device."""
    code = 'a="{{DEVICE_SERIAL_1}}"; b="{{DEVICE_SERIAL_2}}"'
    claimed = [{"serial": "PINNED", "hardwareType": "camera", "row": 2},
               {"serial": "GENERIC", "hardwareType": "wireless", "row": 1}]
    out = inject_run_values(code, {}, [], claimed, "")
    assert out == 'a="GENERIC"; b="PINNED"'


def test_inject_falls_back_to_position_for_devices_without_row():
    """Runs claimed before the row field existed still resolve, by list position."""
    code = '"{{DEVICE_SERIAL_1}}"'
    out = inject_run_values(code, {}, [], [{"serial": "OLD", "hardwareType": "wireless"}], "")
    assert out == '"OLD"'


def test_inject_leaves_token_unresolved_when_no_device_claimed():
    """Nothing to resolve it with: the token stays so the orchestrator can fail loudly
    instead of sending a literal '{{...}}' in a URL."""
    code = '"{{DEVICE_SERIAL_2}}"'
    out = inject_run_values(code, {}, [], [{"serial": "A", "hardwareType": "wireless", "row": 1}], "")
    assert "{{DEVICE_SERIAL_2}}" in out


# --- registry ----------------------------------------------------------------

def test_registry_resolves_python_and_go():
    assert registry.get_runner("py").name == "python"
    assert registry.get_runner("go").name == "go"
    assert registry.get_runner("script").name == "script"


def test_registry_rejects_unimplemented():
    for lang in ("ts", "java", "csharp"):
        with pytest.raises(NotImplementedError):
            registry.get_runner(lang)


# --- Docker lifecycle (mocked) ----------------------------------------------

def _fake_container(exit_code=0, logs=(b"line one\n", b"line two\n")):
    c = mock.Mock()
    c.logs.return_value = iter(logs)
    c.wait.return_value = {"StatusCode": exit_code}
    return c


def test_python_runner_success_streams_logs_and_reports_ok():
    container = _fake_container(exit_code=0)
    client = mock.Mock()
    client.containers.run.return_value = container
    seen = []
    with mock.patch.object(base, "_client", return_value=client):
        res = registry.get_runner("py").run(
            "def test_x():\n    assert True\n", {"MERAKI_API_KEY": "k"},
            {"python_image": "python:3.12-slim", "timeout_seconds": 30, "cpu_limit": 1.0,
             "memory_limit_mb": 256, "cleanup_policy": "always"},
            on_log=lambda e: seen.append(e["message"]))
    assert res.ok is True and res.exit_code == 0
    assert "line one" in seen and "line two" in seen
    container.remove.assert_called_once()  # cleanup_policy 'always'
    # resource caps forwarded
    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["mem_limit"] == "256m" and kwargs["nano_cpus"] == 1_000_000_000
    assert kwargs["environment"] == {"MERAKI_API_KEY": "k"}


def test_runner_reports_test_failure_as_nonzero():
    container = _fake_container(exit_code=1)
    client = mock.Mock()
    client.containers.run.return_value = container
    with mock.patch.object(base, "_client", return_value=client):
        res = registry.get_runner("py").run("x", {}, {"cleanup_policy": "on_success"}, None)
    assert res.ok is False and res.exit_code == 1
    container.remove.assert_not_called()  # on_success + failure -> kept for debugging


def test_docker_unavailable_surfaces():
    with mock.patch.object(base, "_client", side_effect=base.DockerUnavailable("no daemon")):
        with pytest.raises(base.DockerUnavailable):
            registry.get_runner("py").run("x", {}, {}, None)
