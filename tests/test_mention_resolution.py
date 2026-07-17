"""Server-side @-mention resolution: a plain-text '@MR42' the composer didn't turn
into a chip still resolves to a real device (serial) from inventory, so the model
isn't left guessing a serial from the model name."""

from web.services import generate

_INV = [
    {"name": "AP-lobby", "serial": "Q2DD-S2Z2-RLKP", "model": "MR42", "mac": "aa:bb"},
    {"name": "cam-1", "serial": "Q2XX-1111-2222", "model": "MV12", "mac": "cc:dd"},
]


def test_unique_mention_by_model_resolves_to_the_device():
    dev = generate.resolve_prompt_mentions("test the @MR42 access point", [], _INV)
    assert len(dev) == 1
    assert dev[0]["serial"] == "Q2DD-S2Z2-RLKP" and dev[0]["model"] == "MR42"


def test_mention_by_name_or_serial_resolves():
    assert generate.resolve_prompt_mentions("check @AP-lobby", [], _INV)[0]["serial"] == "Q2DD-S2Z2-RLKP"
    assert generate.resolve_prompt_mentions("check @Q2XX-1111-2222", [], _INV)[0]["model"] == "MV12"


def test_ambiguous_model_is_left_alone():
    inv = _INV + [{"name": "AP-2", "serial": "Q2DD-9999-0000", "model": "MR42", "mac": ""}]
    # two MR42s: we can't know which one, so nothing is attached
    assert generate.resolve_prompt_mentions("test @MR42", [], inv) == []


def test_already_sent_device_is_not_duplicated():
    sent = [{"name": "MR42", "serial": "Q2DD-S2Z2-RLKP", "model": "MR42"}]
    out = generate.resolve_prompt_mentions("test @MR42", sent, _INV)
    assert len(out) == 1 and out[0]["serial"] == "Q2DD-S2Z2-RLKP"


def test_has_unresolved_mentions():
    assert generate.has_unresolved_mentions("test @MR42", []) is True
    assert generate.has_unresolved_mentions("test @MR42", [{"name": "MR42"}]) is False
    assert generate.has_unresolved_mentions("no mentions here", []) is False
