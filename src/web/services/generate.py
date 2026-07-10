"""Adapt the RAG pipeline's output into a workspace view model.

Contract (co-decided, handoff G2): the composer posts ``prompt`` plus a JSON
``devices`` array (the @-mention identifiers, G11) and a ``language``. We build a
compact device-context block, run the real pipeline, and hand the template a
plain view model. ``language`` is carried but not yet fed into generation (P2
follow-up).
"""

import json
import re

from web.deps import get_pipeline

# language code -> generated-file extension. Single-select in Settings (G12);
# per-test override is a later concern.
_EXT = {
    "ts": "test.ts",
    "py": "test.py",
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


def _filename(prompt, endpoints, language):
    ext = _EXT.get(language, "test.ts")
    # Prefer a slug from the first retrieved endpoint id (e.g. "GET /organizations"),
    # else from the prompt. Keep it filesystem-safe and short.
    basis = endpoints[0] if endpoints else prompt
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", basis).strip("-").lower()[:32] or "generated"
    return f"{slug}.{ext}"


def build_view_model(prompt, devices, language="ts"):
    """Run the pipeline for ``prompt`` and return a template context dict.

    Never raises: a build/run failure comes back as ``{"error": ...}`` so the
    workspace partial can show an inline message and keep the app usable.
    """
    prompt = (prompt or "").strip()
    pipeline, error = get_pipeline()
    if error is not None:
        return {"error": error, "prompt": prompt}

    full_prompt = prompt
    ctx = _device_context(devices)
    if ctx:
        full_prompt = f"{prompt}\n\n{ctx}"

    try:
        state = pipeline.run(full_prompt)
    except Exception as exc:  # noqa: BLE001 — surfaced inline, not a 500
        return {"error": str(exc) or exc.__class__.__name__, "prompt": prompt}

    code = (state.get("tests") or "").rstrip("\n")
    endpoints = state.get("endpoints") or []
    line_count = code.count("\n") + 1 if code else 0
    return {
        "prompt": prompt,
        "code": code,
        # prebuilt line-number gutter (kept out of the template to avoid escape ambiguity)
        "gutter": "\n".join(str(i) for i in range(1, line_count + 1)),
        "line_count": line_count,
        "file_name": _filename(prompt, endpoints, language),
        "endpoints": endpoints,
        "empty": not code,
    }
