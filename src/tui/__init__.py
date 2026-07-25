"""casewright TUI — a Textual front-end over the services layer.

Uses only ``web.services`` (and ``web.db`` / ``web.config`` for startup), never the
RAG pipeline or any web-serving code directly. See docs: the TUI replaces the web
serving layer while reusing the exact same background-job machinery
(``gen_registry`` / ``run_registry`` / ``kb_registry``).
"""
