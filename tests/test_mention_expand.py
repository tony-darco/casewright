"""@-mention expansion: the concrete serial travels inline with the mention in the
prompt the LLM sees, so a pinned device's serial is right there next to '@name'."""

from web.services import generate

_DEV = [{"name": "AP-lobby", "serial": "Q2DD-S2Z2-RLKP", "model": "MR42", "mac": "aa:bb"}]


def test_mention_expanded_with_serial_and_model():
    out = generate.expand_mentions("radio test on @AP-lobby please", _DEV)
    assert "@AP-lobby (serial Q2DD-S2Z2-RLKP, model MR42)" in out


def test_plain_prompt_and_unknown_mentions_untouched():
    assert generate.expand_mentions("no mentions here", _DEV) == "no mentions here"
    assert generate.expand_mentions("test @other", _DEV) == "test @other"


def test_expansion_is_idempotent():
    once = generate.expand_mentions("check @AP-lobby", _DEV)
    twice = generate.expand_mentions(once, _DEV)
    assert once == twice and once.count("serial Q2DD-S2Z2-RLKP") == 1


def test_device_without_serial_is_skipped():
    dev = [{"name": "AP-lobby", "serial": "", "model": "MR42"}]
    assert generate.expand_mentions("check @AP-lobby", dev) == "check @AP-lobby"
