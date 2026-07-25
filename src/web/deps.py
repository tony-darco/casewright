"""Shared dependency: the lazily-built, cached generation pipeline.

The generation pipeline (``AutoTestLLM``) is expensive to build (chat model +
embeddings + Chroma store) and needs Ollama plus ``AUTOTEST_DATA_DIR``. We build
it *lazily on first use* and cache it — one instance per distinct set of
per-user provider overrides (Settings → Model provider), so users on the same
settings share a pipeline and the no-overrides default stays a singleton. We
swallow build failures so that every non-generate view still works and a
generation can surface a clear inline error instead of crashing (the front-end
must not hard-depend on a running model).
"""

_pipelines = {}   # override-key -> (pipeline, error); exactly one is non-None per entry


def _key(overrides):
    return tuple(sorted((overrides or {}).items()))


def get_pipeline(overrides=None):
    """Return ``(pipeline, error)`` for the given ProviderConfig attribute
    overrides (the user's provider settings; ``None``/``{}`` = env defaults).
    Exactly one is non-None.

    Built once per distinct override set, lazily. A failed build is cached as an
    error string so we don't retry an expensive, doomed construction on every
    request; ``forget_failed_pipelines`` clears those after settings change.
    """
    key = _key(overrides)
    if key not in _pipelines:
        try:
            from rag.pipeline import AutoTestLLM
            from rag.provider import ProviderConfig

            cfg = ProviderConfig()
            for name, value in (overrides or {}).items():
                setattr(cfg, name, value)
            _pipelines[key] = (AutoTestLLM(cfg), None)
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
            _pipelines[key] = (None, str(exc) or exc.__class__.__name__)
    return _pipelines[key]


def forget_failed_pipelines():
    """Drop cached build *failures* so the next generate retries — called after a
    user saves provider settings (the new settings may fix a doomed build)."""
    for key in [k for k, (pipeline, _) in _pipelines.items() if pipeline is None]:
        del _pipelines[key]
