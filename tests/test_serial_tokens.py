"""The device-serial token contract (web.services.serial_tokens).

A generated test writes {{DEVICE_SERIAL_N}} instead of a serial, because a serial isn't
always knowable when the code is written (a type-only hardware row is claimed per run).
Substitution is exact, so it can't silently miss the way the old value-keyed
`code.replace(orig_serial, ...)` did when the model invented a serial.
"""

from web.services import serial_tokens as st


def test_token_is_one_based():
    assert st.token(1) == "{{DEVICE_SERIAL_1}}"
    assert st.token(2) == "{{DEVICE_SERIAL_2}}"


def test_substitute_replaces_every_occurrence():
    code = 'A = "{{DEVICE_SERIAL_1}}"\nurl = f"/devices/{{DEVICE_SERIAL_1}}/wireless"'
    out = st.substitute(code, {1: "Q2AA-BBBB"})
    assert "{{DEVICE_SERIAL_1}}" not in out and out.count("Q2AA-BBBB") == 2


def test_substitute_leaves_unknown_indices_intact():
    """A token with no serial yet (type-only row) survives to the next pass rather than
    being blanked — an empty string would silently build a malformed URL."""
    code = '"{{DEVICE_SERIAL_1}}" "{{DEVICE_SERIAL_2}}"'
    out = st.substitute(code, {1: "Q2-A"})
    assert "Q2-A" in out and "{{DEVICE_SERIAL_2}}" in out
    assert st.remaining(out) == [2]


def test_multi_device_indices_map_independently():
    code = '"{{DEVICE_SERIAL_1}}" and "{{DEVICE_SERIAL_2}}"'
    out = st.substitute(code, {1: "Q2-AP", 2: "Q2-CAM"})
    assert out == '"Q2-AP" and "Q2-CAM"'


def test_remaining_is_sorted_and_deduped():
    code = "{{DEVICE_SERIAL_3}} {{DEVICE_SERIAL_1}} {{DEVICE_SERIAL_3}}"
    assert st.remaining(code) == [1, 3]


def test_empty_input():
    assert st.substitute("", {1: "X"}) == "" and st.remaining("") == []
    assert st.remaining("no tokens here") == []


def test_blank_serial_does_not_resolve_a_token():
    """A claimed device with an empty serial must not erase the token."""
    assert st.substitute("{{DEVICE_SERIAL_1}}", {1: ""}) == "{{DEVICE_SERIAL_1}}"
