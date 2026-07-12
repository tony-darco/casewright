"""Adapt the RAG pipeline's output into a workspace view model.

Contract (co-decided, handoff G2): the composer posts ``prompt`` plus a JSON
``devices`` array (the @-mention identifiers, G11) and a ``language``. We build a
compact device-context block, run the real pipeline, and hand the template a
plain view model.

Three concerns beyond the happy path live here:
- failures are surfaced, never silent — build/run errors come back as ``{"error": ...}``
  with a human-readable cause (issue #7);
- generated code is cleaned to pure Python before it reaches the UI (issue #9);
- every generation records a per-test log (pipeline stages + errors) returned on the
  view model as ``_log`` for the caller to persist (issue #8).
"""

import json
import logging
import re

from web import config
from web.deps import get_pipeline

logger = logging.getLogger("web.generate")

# language code -> generated-file extension. Single-select in Settings (G12);
# per-test override is a later concern. Python is the default target (issue #9).
_EXT = {
    "py": "test.py",
    "ts": "test.ts",
    "java": "Test.java",
    "go": "_test.go",
    "csharp": "Tests.cs",
}


def parse_devices(raw):
    """The composer posts mentioned devices as a JSON string; be forgiving."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _device_context(devices):
    if not devices:
        return ""
    lines = ["Referenced devices (resolve by serial):"]
    for d in devices:
        if not isinstance(d, dict):
            continue
        lines.append(
            "- {name}: serial={serial} model={model} mac={mac}".format(
                name=d.get("name", "?"),
                serial=d.get("serial", "?"),
                model=d.get("model", "?"),
                mac=d.get("mac", "?"),
            )
        )
    return "\n".join(lines)


def _concrete_context(devices, meta):
    """Real identifiers the generated test should use literally, instead of
    placeholders (issue #9): base URL, org ID, and each referenced device's network
    ID + serial. Values come from the user's verified Meraki data / @device mentions."""
    meta = meta or {}
    lines = ["Concrete values — use these literally, do not invent placeholders:"]
    lines.append(f"- base URL: {meta.get('base_url') or config.MERAKI_BASE_URL}")
    if meta.get("org_id"):
        lines.append(f"- organization ID: {meta['org_id']}")
    net_ids = []
    for d in devices or []:
        if isinstance(d, dict) and d.get("networkId") and d["networkId"] not in net_ids:
            net_ids.append(d["networkId"])
    for nid in net_ids:
        lines.append(f"- network ID: {nid}")
    lines.append("- API key: read from the MERAKI_API_KEY environment variable")
    return "\n".join(lines)


def _full_prompt(prompt, devices, meta):
    parts = [prompt]
    ctx = _device_context(devices)
    if ctx:
        parts.append(ctx)
    parts.append(_concrete_context(devices, meta))
    return "\n\n".join(parts)


# The generated code is stripped of fences/prose deterministically inside the graph
# (rag.graph.sanitize, the `sanitize` node), so this adapter trusts state["tests"].

# httpx/requests/ollama connection failures stringify inconsistently; match on the
# common shapes so the user gets a cause they can act on, not a stack-trace fragment.
_CONN_HINTS = (
    "connect", "connection", "refused", "max retries", "newconnectionerror",
    "timed out", "timeout", "unreachable", "failed to establish", "name resolution",
    "httpx", "httpcore",
)


def humanize_error(exc):
    """Turn a low-level build/run exception into a message worth showing (issue #7)."""
    raw = str(exc).strip() or exc.__class__.__name__
    low = raw.lower()
    if any(h in low for h in _CONN_HINTS):
        return (
            "Couldn't reach the model backend (Ollama). Make sure it's running and "
            f"reachable, then try again. ({raw[:200]})"
        )
    return raw


class _GenLog:
    """Accumulates per-test log entries during one generation (issue #8).

    Pure collector — deliberately NOT mirrored into the app-wide logger: these entries
    include the user's prompt, and the app-wide log is visible to any signed-in user.
    Operational failures still reach the app log via the ``logger.exception`` calls
    below (which carry no prompt text)."""

    def __init__(self):
        self.entries = []

    def add(self, stage, message, level="info"):
        self.entries.append({"stage": stage, "level": level, "message": message})


def _filename(prompt, endpoints, language):
    ext = _EXT.get(language, "test.py")
    # Prefer a slug from the first retrieved endpoint id (e.g. "GET /organizations"),
    # else from the prompt. Keep it filesystem-safe and short.
    basis = endpoints[0] if endpoints else prompt
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", basis).strip("-").lower()[:32] or "generated"
    return f"{slug}.{ext}"


def build_view_model(prompt, devices, language="py", meta=None):
    """Run the pipeline for ``prompt`` and return a template context dict.

    Never raises: a build/run failure comes back as ``{"error": ...}`` (with the cause
    humanized) so the workspace partial can show an inline message and keep the app
    usable. The returned ``_log`` carries the per-test log for the caller to persist.
    """
    prompt = (prompt or "").strip()
    log = _GenLog()
    log.add("start", f"prompt={prompt!r}, devices={len(devices or [])}")

    pipeline, error = get_pipeline()
    if error is not None:
        log.add("error", f"pipeline unavailable: {error}", "error")
        return {"error": error, "prompt": prompt, "_log": log.entries}

    try:
        state = pipeline.run(_full_prompt(prompt, devices, meta), language=language)
    except Exception as exc:  # noqa: BLE001 — surfaced inline, not a 500
        msg = humanize_error(exc)
        log.add("error", msg, "error")
        logger.exception("generation failed")
        return {"error": msg, "prompt": prompt, "_log": log.entries}

    endpoints = state.get("endpoints") or []
    log.add("retrieve", f"grounded in {len(endpoints)} endpoint(s): {', '.join(endpoints) or '(none)'}")
    code = (state.get("tests") or "").rstrip("\n")   # already sanitized by the graph
    log.add("generate", f"generated {code.count(chr(10)) + 1 if code else 0} line(s)"
            if code else "no code generated (retriever found nothing to ground)",
            "info" if code else "error")

    vm = _workspace_vm(prompt, code, _filename(prompt, endpoints, language), endpoints)
    vm["_log"] = log.entries
    return vm


def _workspace_vm(prompt, code, file_name, endpoints):
    line_count = code.count("\n") + 1 if code else 0
    return {
        "prompt": prompt,
        "code": code,
        # prebuilt line-number gutter (kept out of the template to avoid escape ambiguity)
        "gutter": "\n".join(str(i) for i in range(1, line_count + 1)),
        "line_count": line_count,
        "file_name": file_name,
        "endpoints": endpoints,
        "empty": not code,
    }


def view_model_from_test(test):
    """Rebuild the workspace view model from a stored test row (for loading it back)."""
    endpoints = parse_devices(test.get("endpoints_json"))  # same forgiving JSON parse
    vm = _workspace_vm(test.get("prompt", ""), test.get("code") or "",
                       test.get("file_name") or "", endpoints)
    vm["t"] = {"id": test["id"], "name": test["name"]}
    return vm


# Graph node -> the status line the user sees while that stage runs (live generation).
STAGE_LABELS = {
    "generate_queries": "Expanding your prompt into search queries…",
    "retrieve": "Searching the API spec…",
    "rerank": "Ranking the most relevant endpoints…",
    "grade": "Checking relevance…",
    "rewrite": "Refining the search…",
    "finalize": "Selecting endpoints…",
    "dependencies": "Resolving call-order dependencies…",
    "generate": "Writing the test…",
    "sanitize": "Cleaning up the generated code…",
}


def stream_events(prompt, devices, language="py", meta=None):
    """Yield streaming events for the SSE endpoint:

        {"type": "stage", "node": ..., "label": ...}   -- progress
        {"type": "token", "text": ...}                 -- generated code, char by char
        {"type": "final", "vm": <workspace view model>}

    A build/run failure (e.g. Ollama unreachable) comes back as a final vm with an
    ``error`` so the UI surfaces it instead of failing silently (see issue #7). The
    final vm carries ``_log`` (the per-test log) for the caller to persist (issue #8).
    """
    prompt = (prompt or "").strip()
    log = _GenLog()
    log.add("start", f"prompt={prompt!r}, devices={len(devices or [])}")

    pipeline, error = get_pipeline()
    if error is not None:
        log.add("error", f"pipeline unavailable: {error}", "error")
        yield {"type": "final", "vm": {"error": error, "prompt": prompt, "_log": log.entries}}
        return

    final = {}
    try:
        for kind, payload in pipeline.stream_run(_full_prompt(prompt, devices, meta), language=language):
            if kind == "stage":
                log.add(payload, STAGE_LABELS.get(payload, payload))  # pipeline dedups the generate stage
                label = STAGE_LABELS.get(payload)
                if label:
                    yield {"type": "stage", "node": payload, "label": label}
            elif kind == "token":
                yield {"type": "token", "text": payload}
            elif kind == "final":
                final = payload or {}
    except Exception as exc:  # surfaced inline, not a 500
        msg = humanize_error(exc)
        log.add("error", msg, "error")
        logger.exception("streaming generation failed")
        yield {"type": "error", "message": msg}
        yield {"type": "final", "vm": {"error": msg, "prompt": prompt, "_log": log.entries}}
        return

    endpoints = final.get("endpoints") or []
    log.add("retrieve", f"grounded in {len(endpoints)} endpoint(s): {', '.join(endpoints) or '(none)'}")
    code = (final.get("tests") or "").rstrip("\n")   # already sanitized by the graph
    log.add("generate", f"generated {code.count(chr(10)) + 1 if code else 0} line(s)"
            if code else "no code generated (retriever found nothing to ground)",
            "info" if code else "error")

    vm = _workspace_vm(prompt, code, _filename(prompt, endpoints, language), endpoints)
    vm["_log"] = log.entries
    yield {"type": "final", "vm": vm}
