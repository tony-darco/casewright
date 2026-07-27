"""Evaluation harnesses for casewright.

- ``eval.promptfoo`` — the retrieval sweep (endpoint precision/recall over 138 cases).
- ``eval.e2e``       — end-to-end: generate real code, score endpoints, LLM-judge the
  code, and check it runs (offline compile + opt-in live execution).
"""
