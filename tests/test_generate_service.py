"""Unit tests for the generation adapter's pure helpers (issues #7 and #9)."""

from web.services import generate

# Code sanitization moved into the graph (rag.graph.sanitize); see tests/test_sanitize.py.


# --- issue #9: runtime identifiers come from the environment, not baked-in literals ----

def test_concrete_context_directs_ids_and_serial_to_env():
    """Org id, network id, AND device serial are supplied via environment variables —
    all are per-run (fresh network, freshly-claimed device), so none is a literal. The
    device's model still appears (useful context); its serial does not."""
    devices = [{"name": "ap1", "serial": "Q2XX-YYYY", "model": "MR16", "orgId": "549236"}]
    meta = {"base_url": None, "org_id": "549236", "network_ids": ["L_123"]}
    fp = generate._full_prompt("list the org devices", devices, meta)
    assert "MERAKI_ORG_ID" in fp        # org id read from the environment
    assert "MERAKI_NETWORK_ID" in fp    # network id read from the environment
    assert "MERAKI_DEVICE_SERIAL" in fp  # serial read from the environment
    assert "MR16" in fp                 # device model is still useful context
    assert "api.meraki.com" in fp       # base url default (a literal constant)
    # the actual ids/serial must NOT be baked into the prompt as literals
    assert "549236" not in fp and "L_123" not in fp and "Q2XX-YYYY" not in fp


def test_concrete_context_env_vars_present_without_meta():
    """Even with no meta, the code is told to read the identifiers from the environment."""
    fp = generate._full_prompt("list the org devices", [], {})
    assert all(v in fp for v in
               ("MERAKI_ORG_ID", "MERAKI_NETWORK_ID", "MERAKI_API_KEY", "MERAKI_DEVICE_SERIAL"))


# --- issue #7: humanize failures so they surface with a real cause ---------------

def test_humanize_connection_error():
    assert "Ollama" in generate.humanize_error(Exception("Connection refused"))
    assert "Ollama" in generate.humanize_error(Exception("HTTPConnectionPool: Max retries exceeded"))
    assert "Ollama" in generate.humanize_error(Exception("Read timed out"))


def test_humanize_passthrough_for_config_error():
    msg = generate.humanize_error(ValueError("AUTOTEST_DATA_DIR is not set"))
    assert "AUTOTEST_DATA_DIR" in msg
    assert "Ollama" not in msg


# --- @-mentions become pinned hardware rows ---------------------------------------
# The model only guesses hardware *types*; an @-mention names the actual device the
# test is written against, so it should arrive in the Config tab already pinned.

def _mention(serial, model, name):
    return {"serial": serial, "model": model, "name": name}


def test_mentioned_device_is_pinned_and_absorbs_the_generic_requirement():
    hardware = generate.pin_mentioned_hardware(
        [{"type": "wireless", "count": 1, "reason": "wireless test"}],
        [_mention("Q2DD-S2Z2-RLKP", "MR16", "AP1")],
    )
    assert hardware == [{"type": "wireless", "count": 1, "serial": "Q2DD-S2Z2-RLKP",
                         "model": "MR16", "name": "AP1", "reason": "referenced in the prompt"}]


def test_generic_requirement_expands_to_one_row_per_device():
    """The Config tab has no count field — 2 APs is two rows, not one row of 2."""
    hardware = generate.pin_mentioned_hardware([{"type": "wireless", "count": 2, "reason": "r"}], [])
    assert hardware == [{"type": "wireless", "count": 1, "reason": "r"},
                        {"type": "wireless", "count": 1, "reason": "r"}]


def test_mention_reduces_but_does_not_erase_a_larger_requirement():
    """Two APs needed, one named: pin that one and still ask for one more."""
    hardware = generate.pin_mentioned_hardware(
        [{"type": "wireless", "count": 2, "reason": "r"}],
        [_mention("Q2-A", "MR16", "AP1")],
    )
    assert [h.get("serial") for h in hardware] == ["Q2-A", None]
    assert all(h["count"] == 1 for h in hardware)


def test_mentions_covering_the_whole_requirement_leave_no_generic_row():
    hardware = generate.pin_mentioned_hardware(
        [{"type": "wireless", "count": 2, "reason": "r"}],
        [_mention("Q2-A", "MR16", "AP1"), _mention("Q2-B", "MR33", "AP2")],
    )
    assert [h["serial"] for h in hardware] == ["Q2-A", "Q2-B"]


def test_mention_of_a_type_the_model_did_not_ask_for_is_still_pinned():
    """The user named a camera; the test clearly needs it even if the model missed it."""
    hardware = generate.pin_mentioned_hardware(
        [{"type": "wireless", "count": 1, "reason": "r"}],
        [_mention("Q2-CAM", "MV12N", "Camera1")],
    )
    assert [h["type"] for h in hardware] == ["camera", "wireless"]
    assert hardware[0]["serial"] == "Q2-CAM"


def test_duplicate_mentions_pin_once():
    hardware = generate.pin_mentioned_hardware(
        [], [_mention("Q2-A", "MR16", "AP1"), _mention("Q2-A", "MR16", "AP1")])
    assert len(hardware) == 1


def test_no_hardware_and_no_mentions_is_empty():
    assert generate.pin_mentioned_hardware(None, None) == []
