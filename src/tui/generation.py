"""The background generation worker.

Drives ``generate.stream_events``, emits its events onto the job's buffer, then
persists the finished result via ``app_flow.persist_generation_result``. The TUI
subscribes to the job and renders the events itself.
"""

import json

from web.services import (
    app_flow, generate, gen_registry, kb_store, provider_store, run_logs_store,
    runs_store, tests_store,
)


def _default_name(prompt: str) -> str:
    p = " ".join((prompt or "").split())
    return (p[:60] + "…") if len(p) > 60 else (p or "Untitled test")


def build_overrides() -> dict:
    """The configured provider overrides plus the active knowledge-base collection —
    assembled fresh before every generation."""
    prov = provider_store.overrides()
    active_kb = kb_store.get_active()   # only ever a 'done' version
    if active_kb:
        prov = {**prov, "collection_name": active_kb["collection_name"]}
        storage = kb_store.get_storage()
        if storage["storage_kind"] == "remote" and storage["storage_url"]:
            prov = {**prov, "chroma_url": storage["storage_url"]}
    return prov


def run_generation(job, test_id, name, prompt, dev, devices_raw, language, meta, prov,
                   repair=None, endpoints=None, keep_hardware=None, is_new=True, hardware=None):
    """Run the pipeline, emit stream events onto ``job``, persist the result.

    Emits ``stage`` / ``token`` / ``error`` events straight through, then a final
    ``{"type": "done", "status": ..., "test_id": ...}`` once persistence completes."""
    for ev in generate.stream_events(prompt, dev, language, meta, prov, repair, endpoints,
                                     hardware=hardware):
        if ev["type"] != "final":
            job.emit(ev)                       # stage / token / error passthrough
            continue
        vm = ev["vm"]
        vm["devices"] = devices_raw            # echoed so a later regenerate reuses grounding
        vm["_gen_meta"] = meta                 # persist_generation_result reads this
        if repair:
            vm["hardware"] = keep_hardware or []
        status = app_flow.persist_generation_result(test_id, vm, name, language, is_new, repair)
        job.emit({"type": "done", "status": status, "test_id": test_id})


def start_generate(prompt, language, hardware_rows=None, regen_of=None):
    """Kick off a generation in the background.

    Returns ``(job, test_id, name, dev)`` — ``dev`` being the devices the prompt named,
    so the caller can show what the test is being pinned to. ``regen_of`` regenerates
    into an existing test as a new version, otherwise a fresh placeholder test is
    created."""
    prompt = (prompt or "").strip()
    dev = []
    # Resolve the devices named in the prompt as plain text — a bare "@MR42", or a serial
    # typed straight in ("Q2KD-DEMR-82P7") — against the org's claimable inventory. The
    # lookup is best-effort: a serial pins even when inventory can't be reached.
    if generate.has_unresolved_mentions(prompt, dev):
        inventory, _ = app_flow.claimable_devices()
        dev = generate.resolve_prompt_mentions(prompt, dev, inventory)
    devices_raw = json.dumps(dev)
    picked_hardware = app_flow.parse_hardware_rows(hardware_rows or [])
    name = _default_name(prompt)
    meta = app_flow.gen_meta(dev)
    prov = build_overrides()

    t = tests_store.restart_generation(regen_of, prompt, language) if regen_of else None
    is_new = t is None
    if is_new:
        t = tests_store.create_generating(name, prompt, language, dev)
    test_id, name = t["id"], t["name"]

    job = gen_registry.start(test_id, lambda job: run_generation(
        job, test_id, name, prompt, dev, devices_raw, language, meta, prov,
        is_new=is_new, hardware=picked_hardware))
    return job, test_id, name, dev


def start_repair(test_id):
    """Send a failed run back through the pipeline to fix the code. Returns
    ``(job, error)``; exactly one is non-None."""
    test = tests_store.get_test(test_id)
    if not test:
        return None, "Test not found."
    runs = runs_store.list_runs_for_test(test_id)
    run = runs_store.get_run(runs[0]["id"]) if runs else None
    if not run or run["status"] not in ("failed", "error"):
        return None, "There's no failed run to learn from. Run the test first."

    repair = generate.repair_context(test, run, run_logs_store.logs_for_run(run["id"]))
    dev = generate.parse_devices(test.get("devices_json"))
    endpoints = generate.parse_devices(test.get("endpoints_json"))
    keep_hardware = generate.parse_devices(test.get("hardware_json"))
    prompt, language = test.get("prompt", ""), test.get("language") or "py"
    prov = build_overrides()

    t = tests_store.restart_generation(test_id, prompt, language)
    if t is None:
        return None, "Test not found."
    name = t["name"]
    meta = app_flow.gen_meta(dev)

    job = gen_registry.start(test_id, lambda job: run_generation(
        job, test_id, name, prompt, dev, test.get("devices_json") or "[]", language,
        meta, prov, repair=repair, endpoints=endpoints, keep_hardware=keep_hardware,
        is_new=False))
    return job, None
