"""Language validation (rag.graph.validate) + its persistence round-trip.

Toolchain-backed cases (Go/Java) are skipped when the tool isn't installed; the
language-agnostic behavior (Python via ast, the mismatch guard, the signature
heuristic, and the graceful fallback) is always exercised.
"""

import shutil
from unittest import mock

import pytest

from rag.graph import validate
from rag.graph.validate import validate_code

HAS_GOFMT = shutil.which("gofmt") is not None
HAS_JAVAC = shutil.which("javac") is not None


# --- Python: authoritative via ast ----------------------------------------------

def test_python_valid():
    r = validate_code("import requests\ndef test_x():\n    assert True", "py")
    assert r["ok"] is True and r["method"] == "ast"


def test_python_invalid():
    r = validate_code("def test(:\n    pass", "py")
    assert r["ok"] is False and r["method"] == "ast"


def test_empty_is_unknown():
    assert validate_code("", "py")["ok"] is None


# --- the main real failure: another language requested, Python emitted -----------

def test_python_emitted_for_go_is_flagged():
    r = validate_code("import os\ndef test_x():\n    assert True", "go")
    assert r["ok"] is False
    assert "python" in r["detail"].lower()


def test_python_emitted_for_typescript_is_flagged():
    r = validate_code("import os\ndef test_x():\n    assert True", "ts")
    assert r["ok"] is False
    assert "python" in r["detail"].lower()


# --- signature heuristic (no toolchain for TS / C#) -----------------------------

def test_typescript_signature_valid():
    r = validate_code(
        "import { test, expect } from '@jest/globals';\ntest('x', () => { expect(1).toBe(1); });", "ts")
    assert r["ok"] is True and r["method"] == "signature"


def test_csharp_signature_valid():
    r = validate_code(
        "using Xunit;\npublic class T { [Fact] public void A() { Assert.True(true); } }", "csharp")
    assert r["ok"] is True and r["method"] == "signature"


# --- toolchain-backed (skip if the tool is missing) -----------------------------

@pytest.mark.skipif(not HAS_GOFMT, reason="gofmt not installed")
def test_go_valid():
    r = validate_code('package main\nimport "testing"\nfunc TestX(t *testing.T) { _ = 1 }', "go")
    assert r["ok"] is True and r["method"] == "gofmt"


@pytest.mark.skipif(not HAS_GOFMT, reason="gofmt not installed")
def test_go_invalid():
    r = validate_code("package main\nfunc TestX( { }", "go")
    assert r["ok"] is False and r["method"] == "gofmt"


@pytest.mark.skipif(not HAS_JAVAC, reason="javac not installed")
def test_java_unresolved_symbols_still_valid_syntax():
    # references JUnit (not on the classpath) — that's a resolution error, not syntax
    r = validate_code("import org.junit.jupiter.api.Test;\npublic class T {\n  @Test void a() { assert true; }\n}", "java")
    assert r["ok"] is True and r["method"] == "javac"


@pytest.mark.skipif(not HAS_JAVAC, reason="javac not installed")
def test_java_syntax_error():
    r = validate_code("public class T { void a( { } }", "java")
    assert r["ok"] is False and r["method"] == "javac"


# --- graceful fallback when the toolchain is missing ----------------------------

def test_go_falls_back_to_signature_without_gofmt():
    with mock.patch.object(validate.shutil, "which", return_value=None):
        r = validate_code('package main\nimport "testing"\nfunc TestX(t *testing.T) {}', "go")
    assert r["method"] == "signature"        # didn't crash; used the heuristic instead


# --- persistence round-trip (stored with the test, reloaded without re-running) --

def test_validation_persists_and_reloads():
    from web.services import generate as G
    from web.services import tests_store

    val = {"ok": False, "method": "ast", "detail": "looks like Python, not Go",
           "language": "go", "label": "Go"}
    t = tests_store.create_test("T", "p", "f.go", "package main", "go", [], [], val)
    vm = G.view_model_from_test(tests_store.get_test(t["id"]))
    assert vm["validation"]["ok"] is False
    assert vm["validation"]["detail"] == "looks like Python, not Go"


def test_missing_validation_loads_as_none():
    from web.services import generate as G
    from web.services import tests_store

    t = tests_store.create_test("T", "p", "f.py", "code", "py", [], [])
    vm = G.view_model_from_test(tests_store.get_test(t["id"]))
    assert vm["validation"] is None
