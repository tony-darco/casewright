"""Shared web dependencies: the Jinja2 environment and the pipeline singleton.

The generation pipeline (``AutoTestLLM``) is expensive to build (chat model +
embeddings + Chroma store) and needs Ollama plus ``AUTOTEST_DATA_DIR``. We build
it *lazily on first use* and cache it, and we swallow build failures so that
every non-generate view still serves and ``/app/generate`` can render a clear
inline error instead of a 500 (handoff: frontend must not hard-depend on a
running model).
"""

from fastapi.templating import Jinja2Templates

from web import config

templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
# Expose the wordmark to every template without threading it through each route.
templates.env.globals["WORDMARK"] = config.WORDMARK


_pipeline = None          # cached AutoTestLLM once built
_pipeline_error = None    # human-readable reason the build failed, if it did


def get_pipeline():
    """Return ``(pipeline, error)``. Exactly one is non-None.

    Built once, lazily. A failed build is cached as an error string so we don't
    retry an expensive, doomed construction on every request.
    """
    global _pipeline, _pipeline_error
    if _pipeline is None and _pipeline_error is None:
        try:
            from rag.pipeline import AutoTestLLM

            _pipeline = AutoTestLLM()
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
            _pipeline_error = str(exc) or exc.__class__.__name__
    return _pipeline, _pipeline_error
