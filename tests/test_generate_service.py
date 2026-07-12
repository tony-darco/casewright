"""Unit tests for the generation adapter's pure helpers (issues #7 and #9)."""

from web.services import generate

# Code sanitization moved into the graph (rag.graph.sanitize); see tests/test_sanitize.py.


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
