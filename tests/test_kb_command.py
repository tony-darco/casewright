"""Parsing the arguments to /kb.

The knowledge base is driven entirely from the command bar, so this grammar is the
whole surface for creating one. It's a pure function over the text after ``/kb`` —
no Textual, no database.
"""

from tui.commands import parse_kb


# --- listing ---------------------------------------------------------------------

def test_bare_kb_lists():
    assert parse_kb("").action == "list"
    assert parse_kb("   ").action == "list"


# --- --new -----------------------------------------------------------------------

def test_new_with_a_file_path():
    req = parse_kb("--new ~/specs/meraki.json --split custom")
    assert (req.action, req.source, req.split) == ("new", "~/specs/meraki.json", "custom")
    assert req.error == "" and req.name == ""


def test_new_with_a_url_and_langchain_split():
    req = parse_kb("--new https://example.com/spec.json --split langchain")
    assert req.source == "https://example.com/spec.json" and req.split == "langchain"


def test_flags_may_come_in_any_order():
    a = parse_kb("--new spec.json --split custom")
    b = parse_kb("--split custom --new spec.json")
    assert (a.source, a.split) == (b.source, b.split)


def test_name_is_optional_and_may_be_quoted():
    req = parse_kb('--new spec.json --split custom --name "Meraki v1.53"')
    assert req.name == "Meraki v1.53" and req.source == "spec.json"


def test_split_is_required_because_the_two_corpora_differ():
    """Guessing a split method would quietly hand someone a knowledge base that
    retrieves badly, so it has to be stated."""
    req = parse_kb("--new spec.json")
    assert req.action == "list" and "--split custom" in req.error


def test_unknown_split_is_rejected():
    assert "custom" in parse_kb("--new spec.json --split magic").error


def test_new_without_a_source():
    assert "URL or file path" in parse_kb("--new").error
    assert "URL or file path" in parse_kb("--new --split custom").error


# --- --storage --------------------------------------------------------------------

def test_storage_remote_and_local():
    assert parse_kb("--storage http://chroma:8000").storage == "http://chroma:8000"
    assert parse_kb("--storage local").storage == "local"
    assert parse_kb("--storage local").action == "storage"


def test_storage_without_a_value():
    assert "Chroma URL" in parse_kb("--storage").error


# --- misuse -----------------------------------------------------------------------

def test_unknown_flag_reports_usage():
    req = parse_kb("--frobnicate x")
    assert "'--frobnicate'" in req.error and "/kb --new" in req.error


def test_new_and_storage_are_separate_operations():
    assert "one of --new or --storage" in parse_kb("--new a.json --storage local").error


def test_modifiers_alone_do_nothing():
    assert "--new" in parse_kb("--split custom").error


def test_unbalanced_quote_is_reported_not_raised():
    assert parse_kb('--new spec.json --name "unclosed').error != ""
