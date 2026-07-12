"""Deterministic post-generation language validation (the graph's ``validate`` node).

The generator is tuned for Python; asking for another language can yield code that
isn't actually that language — most often Python emitted for a non-Python request —
or code with syntax errors. This step checks the sanitized code against the selected
language and reports a status the UI can surface. It *warns*; it never blocks
generation.

Strategy (no third-party deps):
  - python  -> ast.parse (authoritative, stdlib)
  - go      -> ``gofmt -e`` (parses, reports syntax errors) when gofmt is on PATH
  - java    -> ``javac`` when on PATH, read syntax-only: missing-symbol / missing-import
               errors are expected for a standalone snippet and are ignored; only real
               syntax errors count as invalid
  - ts, c#  -> language-signature heuristic (no toolchain available here)
  - all     -> mismatch guard: code that cleanly ast.parse()s while the target is NOT
               python is flagged as "looks like Python"

Never raises: a missing toolchain, a timeout, or any unexpected error falls back to
the signature heuristic, so validation can't break generation.

Return shape: ``{"ok": bool|None, "method": str, "detail": str, "language": str}``
where ``ok`` is True (valid), False (invalid / wrong language), or None (couldn't
determine); ``method`` is ast|gofmt|javac|signature|none.
"""

import ast
import re
import shutil
import subprocess

from rag.graph.languages import resolve

_TIMEOUT = 8  # seconds for any toolchain subprocess

# Hallmark tokens per language, for the no-toolchain heuristic + mismatch detection.
_SIGNATURES = {
    "python": [r"\bdef\s+\w+\s*\(", r"^\s*import\s+\w", r"\bassert\b", r"\bself\b", r':\s*$'],
    "typescript": [r"\bimport\b", r"\bexport\b", r"\bconst\b", r"\bfunction\b", r"=>",
                   r"\bdescribe\(", r"\b(test|it)\(", r"\binterface\b", r";\s*$"],
    "java": [r"\bpublic\s+(final\s+|abstract\s+)*class\b", r"\bimport\s+[\w.]+;", r"@Test\b",
             r"\bvoid\b", r"\bSystem\.out\b", r";\s*$"],
    "go": [r"\bpackage\s+\w+", r"\bfunc\b", r"\bimport\s*\(", r":=", r"\bt\s+\*testing\.T\b"],
    "csharp": [r"\busing\s+[\w.]+;", r"\bnamespace\b", r"\bpublic\s+class\b", r"\[\s*Fact\b",
               r"\[\s*Test\b", r"\bvoid\b", r"\bConsole\."],
}


def _result(ok, method, detail, lang):
    return {"ok": ok, "method": method, "detail": detail, "language": lang.name, "label": lang.label}


def _parses_as_python(code):
    try:
        ast.parse(code)
        return True
    except (SyntaxError, ValueError):
        return False


def _sig_hits(code, lang_name):
    pats = _SIGNATURES.get(lang_name, [])
    return sum(1 for p in pats if re.search(p, code, re.MULTILINE))


def _validate_signature(code, lang, note=None):
    """No toolchain: score the code against each language's hallmark tokens."""
    target = _sig_hits(code, lang.name)
    others = {n: _sig_hits(code, n) for n in _SIGNATURES if n != lang.name}
    best_other, best_hits = max(others.items(), key=lambda kv: kv[1], default=(None, 0))
    suffix = f" ({note})" if note else ""
    if target >= 2 and target >= best_hits:
        return _result(True, "signature", f"matches {lang.label} conventions{suffix}", lang)
    if best_other and best_hits >= 2 and best_hits > target:
        other_label = resolve(best_other).label
        return _result(False, "signature", f"looks like {other_label}, not {lang.label}{suffix}", lang)
    return _result(None, "signature", f"couldn't confidently validate as {lang.label}{suffix}", lang)


def _validate_go(code, lang):
    gofmt = shutil.which("gofmt")
    if not gofmt:
        return _validate_signature(code, lang, note="gofmt not installed")
    try:
        proc = subprocess.run([gofmt, "-e"], input=code, capture_output=True,
                              text=True, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — validation must never crash generation
        return _validate_signature(code, lang, note=f"gofmt failed: {exc}")
    if proc.returncode == 0:
        return _result(True, "gofmt", "parses as valid Go", lang)
    first = (proc.stderr.strip().splitlines() or ["syntax error"])[0]
    return _result(False, "gofmt", f"Go syntax error: {first}", lang)


# javac messages that mean a *syntax* problem (vs unresolved symbols, which are
# expected when a standalone snippet references JUnit etc.).
_JAVA_SYNTAX_MARKERS = (
    "expected", "illegal start", "not a statement", "reached end of file while parsing",
    "class, interface, enum, or record expected", "unclosed", "malformed", "invalid",
)
_JAVA_RESOLUTION_MARKERS = ("cannot find symbol", "does not exist", "cannot access")


def _validate_java(code, lang):
    javac = shutil.which("javac")
    if not javac:
        return _validate_signature(code, lang, note="javac not installed")
    import os
    import tempfile
    m = re.search(r"\bpublic\s+(?:final\s+|abstract\s+)*(?:class|interface|enum)\s+([A-Za-z_]\w*)", code)
    classname = m.group(1) if m else "Generated"
    try:
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, f"{classname}.java")
            with open(src, "w") as fh:
                fh.write(code)
            proc = subprocess.run([javac, "-proc:none", "-d", d, src],
                                  capture_output=True, text=True, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return _validate_signature(code, lang, note=f"javac failed: {exc}")
    if proc.returncode == 0:
        return _result(True, "javac", "compiles as valid Java", lang)
    errors = [ln for ln in proc.stderr.splitlines() if ": error:" in ln]
    low = "\n".join(errors).lower()
    if any(mark in low for mark in _JAVA_SYNTAX_MARKERS):
        first = next((e.split(": error:", 1)[1].strip() for e in errors if "error:" in e), "syntax error")
        return _result(False, "javac", f"Java syntax error: {first}", lang)
    # Only unresolved symbols / missing imports — expected for a standalone test snippet.
    return _result(True, "javac", "valid Java syntax (unresolved symbols expected for a snippet)", lang)


def validate_code(code, language="python"):
    """Validate ``code`` against ``language``; see module docstring for the contract."""
    code = (code or "").strip()
    lang = resolve(language)
    if not code:
        return _result(None, "none", "no code to validate", lang)

    if lang.name == "python":
        try:
            ast.parse(code)
            return _result(True, "ast", "parses as valid Python", lang)
        except SyntaxError as exc:
            return _result(False, "ast", f"Python syntax error: {exc.msg} (line {exc.lineno})", lang)

    # Non-Python target: the most common failure is the model emitting Python.
    if _parses_as_python(code):
        return _result(False, "ast", f"looks like Python, not {lang.label}", lang)

    if lang.name == "go":
        return _validate_go(code, lang)
    if lang.name == "java":
        return _validate_java(code, lang)
    return _validate_signature(code, lang)  # typescript, csharp (no toolchain here)
