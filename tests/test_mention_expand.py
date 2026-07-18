"""@-mention expansion: the device's serial TOKEN travels inline with the mention in the
prompt the LLM sees, so '@name' carries the {{DEVICE_SERIAL_N}} to write. The real serial
is never inlined — the model must never see one, or it copies it instead of the token."""

from web.services import generate

_DEV = [{"name": "AP-lobby", "serial": "Q2DD-S2Z2-RLKP", "model": "MR42", "mac": "aa:bb"}]
_ROSTER = generate.device_roster(None, _DEV)


def test_mention_expanded_with_token_and_model():
    out = generate.expand_mentions("radio test on @AP-lobby please", _ROSTER)
    assert "@AP-lobby ({{DEVICE_SERIAL_1}}, model MR42)" in out
    assert "Q2DD-S2Z2-RLKP" not in out   # the serial itself never reaches the model


def test_plain_prompt_and_unknown_mentions_untouched():
    assert generate.expand_mentions("no mentions here", _ROSTER) == "no mentions here"
    assert generate.expand_mentions("test @other", _ROSTER) == "test @other"


def test_expansion_is_idempotent():
    once = generate.expand_mentions("check @AP-lobby", _ROSTER)
    twice = generate.expand_mentions(once, _ROSTER)
    assert once == twice and once.count("{{DEVICE_SERIAL_1}}") == 1


def test_device_without_serial_is_skipped():
    """A roster row with no name can't be matched to an @-mention, so the prompt is left
    alone (an unnamed type-only row is still tokenized in the device-context block)."""
    roster = [{"type": "wireless", "count": 1}]
    assert generate.expand_mentions("check @AP-lobby", roster) == "check @AP-lobby"


def test_second_device_gets_second_token():
    devs = [{"name": "AP1", "serial": "Q2-A", "model": "MR42"},
            {"name": "CAM1", "serial": "Q2-B", "model": "MV12"}]
    out = generate.expand_mentions("@AP1 and @CAM1", generate.device_roster(None, devs))
    assert "@AP1 ({{DEVICE_SERIAL_1}}" in out and "@CAM1 ({{DEVICE_SERIAL_2}}" in out
