"""A device serial typed straight into the prompt pins the test to that exact device.

"Check the SSID broadcast on Q2KD-DEMR-82P7" names one specific AP — no '@' and no
hardware picker. The serial has to become a pinned hardware row (so a run claims that
unit and no other) and a {{DEVICE_SERIAL_N}} token in the prompt the model sees (so the
real serial never reaches it)."""

from web.services import generate

_SERIAL = "Q2KD-DEMR-82P7"          # a Meraki MR42 access point
_PROMPT = f"Check that the SSID is broadcasting on {_SERIAL}"

_INV = [
    {"name": "AP-lobby", "serial": _SERIAL, "model": "MR42", "mac": "aa:bb"},
    {"name": "cam-1", "serial": "Q2XX-1111-2222", "model": "MV12", "mac": "cc:dd"},
]


# --- finding the serial ----------------------------------------------------------

def test_serial_is_found_anywhere_in_the_prompt():
    assert generate.prompt_serials(_PROMPT) == [_SERIAL]
    assert generate.prompt_serials(f"{_SERIAL.lower()} radio check") == [_SERIAL]
    assert generate.prompt_serials(f"compare {_SERIAL} and {_SERIAL}") == [_SERIAL]


def test_dashed_numbers_are_not_serials():
    """A serial mixes letters and digits; a plain dashed number would otherwise pin
    hardware the run then fails to claim."""
    assert generate.prompt_serials("invoice 1234-5678-9012") == []
    assert generate.prompt_serials("no device here") == []


def test_prompt_serial_counts_as_unresolved():
    assert generate.has_unresolved_mentions(_PROMPT, []) is True
    assert generate.has_unresolved_mentions(_PROMPT, [{"serial": _SERIAL}]) is False


# --- resolving it to a real device ------------------------------------------------

def test_serial_resolves_against_inventory():
    dev = generate.resolve_prompt_mentions(_PROMPT, [], _INV)
    assert len(dev) == 1
    assert dev[0]["serial"] == _SERIAL
    assert dev[0]["model"] == "MR42" and dev[0]["name"] == "AP-lobby"


def test_unknown_serial_still_pins_with_the_model_named_in_the_prompt():
    """Inventory may not know it (no API key, or it's already claimed). The user named
    one exact device, so it pins anyway — provisioning is where that fails loudly — and
    the model named alongside it supplies the hardware type."""
    dev = generate.resolve_prompt_mentions(f"test the MR42 at {_SERIAL}", [], [])
    assert len(dev) == 1
    assert dev[0]["serial"] == _SERIAL and dev[0]["model"] == "MR42"


def test_ambiguous_model_hint_is_not_guessed():
    dev = generate.resolve_prompt_mentions(f"the MR42 and the MX64 near {_SERIAL}", [], [])
    assert dev[0]["serial"] == _SERIAL and dev[0]["model"] == ""


def test_already_resolved_serial_is_not_duplicated():
    sent = [{"name": "AP-lobby", "serial": _SERIAL, "model": "MR42"}]
    assert generate.resolve_prompt_mentions(_PROMPT, sent, _INV) == sent


# --- what the pinned device turns into --------------------------------------------

def test_serial_becomes_a_pinned_wireless_hardware_row():
    dev = generate.resolve_prompt_mentions(_PROMPT, [], _INV)
    rows = generate.pin_mentioned_hardware([{"type": "wireless", "count": 1}], dev)
    assert len(rows) == 1                      # pins the generic AP row, doesn't add to it
    assert rows[0]["serial"] == _SERIAL
    assert rows[0]["type"] == "wireless" and rows[0]["model"] == "MR42"


def test_prompt_serial_is_replaced_by_its_token():
    roster = generate.device_roster(None, generate.resolve_prompt_mentions(_PROMPT, [], _INV))
    out = generate.expand_mentions(_PROMPT, roster)
    assert "{{DEVICE_SERIAL_1}} (model MR42)" in out
    assert _SERIAL not in out                  # the real serial never reaches the model


def test_token_expansion_is_idempotent():
    roster = generate.device_roster(None, generate.resolve_prompt_mentions(_PROMPT, [], _INV))
    once = generate.expand_mentions(_PROMPT, roster)
    assert generate.expand_mentions(once, roster) == once


def test_full_prompt_targets_the_device_without_showing_the_serial():
    dev = generate.resolve_prompt_mentions(_PROMPT, [], _INV)
    fp = generate._full_prompt(_PROMPT, dev, {})
    assert _SERIAL not in fp                   # not inline, and not in the device block
    assert "{{DEVICE_SERIAL_1}}" in fp
    assert "MR42" in fp                        # the model still gets what kind of device


def test_unnamed_device_never_leaks_its_serial_as_a_label():
    """An inventory device with no name of its own carries the serial as its name; the
    device-context block must not echo that back."""
    inv = [{"name": _SERIAL, "serial": _SERIAL, "model": "MR42"}]
    dev = generate.resolve_prompt_mentions(_PROMPT, [], inv)
    assert _SERIAL not in generate._device_context(generate.device_roster(None, dev))
