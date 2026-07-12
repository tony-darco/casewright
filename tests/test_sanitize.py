"""Unit tests for the deterministic code sanitizer (rag.graph.sanitize).

Generalizes the old web-layer clean_code tests (Python) and adds the multi-language
behavior the sanitize node now supports.
"""

from rag.graph.languages import resolve
from rag.graph.sanitize import sanitize_code


# --- Python: the behavior inherited from the old clean_code ----------------------

def test_strips_fences_and_prose():
    raw = (
        "Here's your test:\n\n"
        "```python\n"
        "import requests\n\n"
        "def test_orgs():\n"
        "    assert True\n"
        "```\n"
        "Hope this helps!"
    )
    out = sanitize_code(raw, "python")
    assert out.startswith("import requests")
    assert out.endswith("assert True")
    assert "```" not in out
    assert "Hope this helps" not in out


def test_bare_fence():
    assert sanitize_code("```\nimport os\n```", "python") == "import os"


def test_no_fence_drops_leading_prose():
    out = sanitize_code("Sure! Here is the code:\nimport os\nprint(os.getcwd())", "python")
    assert out.splitlines()[0] == "import os"


def test_plain_python_passthrough():
    raw = "import requests\n\n\ndef test_x():\n    pass"
    assert sanitize_code(raw, "python") == raw


def test_empty():
    assert sanitize_code("", "python") == ""
    assert sanitize_code(None, "python") == ""


# --- language awareness ----------------------------------------------------------

def test_prefers_fence_matching_the_target_language():
    # Model returned an explanatory python snippet AND the real typescript answer;
    # for a TS target we keep the TS fence, not the python one.
    raw = (
        "First, conceptually in Python:\n"
        "```python\nimport requests\n```\n"
        "Now the actual test:\n"
        "```ts\nimport { test, expect } from '@jest/globals';\ntest('ok', () => {});\n```\n"
    )
    out = sanitize_code(raw, "ts")
    assert out.startswith("import { test")
    assert "requests" not in out


def test_falls_back_to_any_fence_when_no_tag_matches():
    # No fence tagged for Go, but there is a fenced block — keep it rather than drop all.
    raw = "```\npackage main\n\nfunc TestX(t *testing.T) {}\n```"
    out = sanitize_code(raw, "go")
    assert out.startswith("package main")


def test_typescript_no_fence_drops_leading_prose():
    raw = "Here you go:\nimport fetch from 'node-fetch';\nconst x = 1;"
    out = sanitize_code(raw, "ts")
    assert out.splitlines()[0].startswith("import fetch")


def test_csharp_fence_tag_with_hash():
    raw = "```csharp\nusing System.Net.Http;\npublic class T {}\n```"
    out = sanitize_code(raw, "csharp")
    assert out.startswith("using System.Net.Http;")


def test_unknown_language_falls_back_to_python():
    assert resolve("klingon").name == "python"
    out = sanitize_code("```python\nimport os\n```", "klingon")
    assert out == "import os"
