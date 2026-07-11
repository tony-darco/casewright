"""Unit tests for the generation adapter's pure helpers (issues #7 and #9)."""

from web.services import generate


# --- issue #9: clean generated code to pure Python -------------------------------

def test_clean_code_strips_fences_and_prose():
    raw = (
        "Here's your test:\n\n"
        "```python\n"
        "import requests\n\n"
        "def test_orgs():\n"
        "    assert True\n"
        "```\n"
        "Hope this helps!"
    )
    out = generate.clean_code(raw)
    assert out.startswith("import requests")
    assert out.endswith("assert True")
    assert "```" not in out
    assert "Hope this helps" not in out


def test_clean_code_bare_fence():
    out = generate.clean_code("```\nimport os\n```")
    assert out == "import os"


def test_clean_code_no_fence_drops_leading_prose():
    out = generate.clean_code("Sure! Here is the code:\nimport os\nprint(os.getcwd())")
    assert out.splitlines()[0] == "import os"


def test_clean_code_plain_python_passthrough():
    raw = "import requests\n\n\ndef test_x():\n    pass"
    assert generate.clean_code(raw) == raw


def test_clean_code_empty():
    assert generate.clean_code("") == ""
    assert generate.clean_code(None) == ""


# --- issue #9: concrete identifiers injected into the prompt ---------------------

def test_concrete_context_includes_real_identifiers():
    devices = [{"name": "ap1", "serial": "Q2XX-YYYY", "networkId": "L_123"}]
    meta = {"base_url": None, "org_id": "549236"}
    fp = generate._full_prompt("list the org devices", devices, meta)
    assert "Q2XX-YYYY" in fp   # serial (device context)
    assert "549236" in fp      # org id (concrete context)
    assert "L_123" in fp       # network id (concrete context)
    assert "api.meraki.com" in fp  # base url default


# --- issue #7: humanize failures so they surface with a real cause ---------------

def test_humanize_connection_error():
    assert "Ollama" in generate.humanize_error(Exception("Connection refused"))
    assert "Ollama" in generate.humanize_error(Exception("HTTPConnectionPool: Max retries exceeded"))
    assert "Ollama" in generate.humanize_error(Exception("Read timed out"))


def test_humanize_passthrough_for_config_error():
    msg = generate.humanize_error(ValueError("AUTOTEST_DATA_DIR is not set"))
    assert "AUTOTEST_DATA_DIR" in msg
    assert "Ollama" not in msg
