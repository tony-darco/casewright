"""End-to-end eval: does casewright do its job on an AP prompt?

Each case pairs a prompt with its ground-truth endpoints and a reference script. The
runner pushes the prompt through the real generation pipeline and scores three things:

1. **endpoints** — did it ground on the right ones (precision/recall, recall-gated),
2. **code** — an LLM judge vs the reference + intent,
3. **runs first try** — offline (does it compile/parse) and, opt-in, live (Docker + Meraki).

The pieces are split so the harness itself is testable offline: :mod:`eval.e2e.runner`
takes injectable ``generate`` / ``judge`` callables, so a stub replaces Ollama in tests.
"""
