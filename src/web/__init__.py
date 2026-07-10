"""casewright web layer.

FastAPI + Jinja2 + HTMX frontend for the test-generation product (see
docs/FRONTEND_HANDOFF.md). This package is the serving layer only; the
generation engine lives in ``rag`` (rag.pipeline.AutoTestLLM) and is reused,
not reimplemented.
"""
