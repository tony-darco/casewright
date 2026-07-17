"""Run orchestration (Run feature).

Sequences one test run: provision an ephemeral network + hardware -> inject this
run's identifiers into the code -> execute it in a container -> record the outcome,
always tearing the network/hardware down in a ``finally``. Runs in a background
thread (via run_registry), emitting log/status events onto the job for the SSE
stream and persisting logs to run_logs.

Concurrency: an MVP single-run-at-a-time guard (one uvicorn worker). ``begin`` is the
atomic gate — the route rejects a second run while one is active rather than queuing.
"""

import json
import logging
import threading

from web.services import (
    generate, network_provision, run_logs_store, run_settings_store, runs_store,
)
from web.services.network_provision import ProvisionError
from web.services.runners import registry
from web.services.runners.base import DockerUnavailable
from web.services.runners.inject import inject_run_values

logger = logging.getLogger("web.run_orchestrator")

_guard = threading.Lock()
_active_run_id = None


def begin(run_id) -> bool:
    """Atomically claim the single active-run slot for ``run_id``. Returns False if a
    run is already in progress (the caller should reject rather than queue)."""
    global _active_run_id
    with _guard:
        if _active_run_id is not None:
            return False
        _active_run_id = run_id
        return True


def _release():
    global _active_run_id
    with _guard:
        _active_run_id = None


def is_busy() -> bool:
    with _guard:
        return _active_run_id is not None


def _loads(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (ValueError, TypeError):
        return default


def start_run(job, uid, test, run_id, run_code, org_id, source, example_network_id, key):
    """Background worker: the full provision -> inject -> run -> teardown lifecycle for
    one run. Emits {type: log|status|done} events onto ``job`` and persists logs."""
    def on_log(entry):
        run_logs_store.record(run_id, [entry])
        job.emit({"type": "log", **entry})

    def status(new_status, error=None):
        runs_store.update_status(run_id, new_status, error)
        job.emit({"type": "status", "status": new_status, "error": error or ""})

    result = None
    try:
        status("provisioning")
        # hardware rows pinned to a serial (from the prompt's @-mentions, or chosen in
        # the Config tab) are claimed exactly, so the test runs on the device it names
        hardware = _loads(test.get("hardware_json"), [])
        result = network_provision.provision(uid, run_code, org_id, hardware, source,
                                             example_network_id, key, on_log)
        runs_store.set_network(run_id, result.network_id, result.org_id)
        runs_store.set_claimed_devices(run_id, result.claimed_devices)

        status("running")
        # New code reads org/network/key from the environment (so the fresh per-run
        # network is used with no id baked in); inject only swaps device serials and, for
        # legacy literal-based tests, any baked-in network id -> this run's network.
        code = inject_run_values(
            test.get("code", ""), _loads(test.get("gen_meta_json"), {}),
            _loads(test.get("devices_json"), []), result.claimed_devices, result.network_id)
        env = {"MERAKI_API_KEY": key, "MERAKI_ORG_ID": result.org_id,
               "MERAKI_NETWORK_ID": result.network_id}
        runner = registry.get_runner(test.get("language") or "py")
        outcome = runner.run(code, env, run_settings_store.get_settings(uid), on_log)
        status("success" if outcome.ok else "failed")
    except ProvisionError as exc:
        on_log({"stage": "provision", "level": "error", "message": str(exc)})
        status("error", str(exc))
    except NotImplementedError as exc:
        on_log({"stage": "run", "level": "error", "message": str(exc)})
        status("error", str(exc))
    except DockerUnavailable as exc:
        on_log({"stage": "run", "level": "error", "message": str(exc)})
        status("error", str(exc))
    except Exception as exc:  # noqa: BLE001 — reported as the run's error, never a 500
        msg = generate.humanize_error(exc)
        logger.exception("run %s failed", run_id)
        on_log({"stage": "run", "level": "error", "message": msg})
        status("error", msg)
    finally:
        if result and result.network_id:
            network_provision.teardown(org_id, result.network_id, result.claimed_devices, key, on_log)
        _release()
        job.emit({"type": "done"})
