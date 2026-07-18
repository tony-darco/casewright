"""The device-serial token contract, and its deterministic substitution.

A generated test never writes a device serial. It writes ``{{DEVICE_SERIAL_N}}`` — a
1-based token indexed to the test's hardware rows — and we replace it with a real
serial here. This exists because a serial is not always knowable when the code is
written: a hardware row is either *pinned* to a specific device (serial known now) or
*type-only* ("any wireless AP", claimed only when a run provisions). Told to emit a
literal it doesn't have, the model fabricates one (classically 'MR42-1234567890', a
model name), and the old value-keyed ``code.replace(orig_serial, ...)`` had nothing to
match, so the bogus serial reached the container and 404'd every device call.

A fixed token removes the guesswork from the model and the ambiguity from the swap:
matching is exact rather than dependent on the model reproducing an opaque string
verbatim. Same reasoning as rag.graph.sanitize — when a model's output varies in a way
we can correct deterministically, correct it here instead of adding more prompt text.

Substitution happens twice, by necessity:
  - generation time, for pinned rows, so a saved test carries a real serial and stays
    self-contained (web.services.generate);
  - run time, for type-only rows, once a device is actually claimed
    (web.services.runners.inject).

Stdlib-only, so both callers can share it without pulling in anything heavy.
"""

import re

TOKEN_RE = re.compile(r"\{\{DEVICE_SERIAL_(\d+)\}\}")


def token(index: int) -> str:
    """The token for a 1-based hardware-row index."""
    return "{{DEVICE_SERIAL_%d}}" % index


def substitute(code: str, serial_by_index: dict) -> str:
    """Replace each ``{{DEVICE_SERIAL_N}}`` whose index appears in ``serial_by_index``.

    Indices with no serial yet (a type-only row before its run claims hardware) are
    deliberately left in place for the next substitution pass, rather than blanked —
    an unresolved token is visible and checkable (see ``remaining``), an empty string
    silently builds a malformed URL."""
    if not code:
        return ""
    serials = {int(k): v for k, v in (serial_by_index or {}).items() if v}

    def repl(m):
        return serials.get(int(m.group(1)), m.group(0))

    return TOKEN_RE.sub(repl, code)


def remaining(code: str) -> list:
    """The distinct token indices still present in ``code``, ascending.

    Before substitution this is what the model emitted (empty means it ignored the
    contract and probably invented a serial); after, it's what's left unresolved — at run
    time that must fail loudly rather than execute a request with a literal '{{...}}' in
    the URL."""
    return sorted({int(m.group(1)) for m in TOKEN_RE.finditer(code or "")})
