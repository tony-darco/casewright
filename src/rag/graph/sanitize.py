"""Deterministic post-generation sanitizer (the graph's ``sanitize`` node).

The generate prompt asks for raw source only, but models still wrap answers in
Markdown fences or bracket them with prose ("Here's your test: … Hope this
helps!"). Rather than fight that non-determinism with more prompting, we strip it
here, deterministically:

  1. If the output has fenced code blocks, keep only their bodies — preferring
     fences tagged for the target language, else any fence. Everything outside the
     fences (the prose) is dropped.
  2. With no fence, drop any leading prose before the first line that looks like
     real code for the target language.
  3. Otherwise return the text unchanged (already pure source).

Stdlib-only so it stays cheap to import and trivially testable.
"""

import re

from rag.graph.languages import resolve

# ```lang\n … ``` — the language tag is optional and may contain +, #, - (c++, c#).
_FENCE_RE = re.compile(r"```[ \t]*([\w+#-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)


def sanitize_code(text: str, language="python") -> str:
    """Return only the runnable ``language`` source found in ``text``."""
    if not text:
        return ""
    lang = resolve(language)

    blocks = _FENCE_RE.findall(text)
    if blocks:
        keep = {lang.name, *lang.aliases}
        matching = [body for tag, body in blocks if tag.lower() in keep]
        chosen = matching or [body for _, body in blocks]
        return "\n\n".join(body.strip("\n") for body in chosen).strip()

    lines = text.strip("\n").splitlines()
    for i, line in enumerate(lines):
        if lang.code_start.match(line):
            return "\n".join(lines[i:]).strip()
    return text.strip()
