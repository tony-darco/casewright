"""The file-backed settings layer: config.yaml for settings, .env for secrets, and
the one-time migration out of the old per-user database rows.

``settings_files`` (conftest, autouse) points CONFIG_PATH/ENV_PATH at a tmp dir, so
every test here starts from "no config files at all".
"""

import os
import stat

import pytest
import yaml
from cryptography.fernet import Fernet

from web import db, settings


# --- config.yaml -------------------------------------------------------------------

def test_defaults_when_no_file():
    assert not settings.CONFIG_PATH.exists()
    assert settings.load() == settings.DEFAULTS
    assert settings.section("run")["cleanup_policy"] == "always"


def test_save_writes_the_file_and_roundtrips():
    settings.save("provider", {"chat_model": "mistral-small:22b", "temperature": 0.3})
    assert settings.CONFIG_PATH.exists()
    on_disk = yaml.safe_load(settings.CONFIG_PATH.read_text())
    assert on_disk["provider"]["chat_model"] == "mistral-small:22b"
    assert settings.section("provider")["temperature"] == 0.3
    # untouched fields keep their defaults rather than vanishing
    assert settings.section("provider")["embed_model"] == ""
    assert settings.section("run") == settings.DEFAULTS["run"]


def test_save_is_a_merge_not_a_replace():
    settings.save("run", {"cpu_limit": 2.0})
    settings.save("run", {"memory_limit_mb": 1024})
    run = settings.section("run")
    assert (run["cpu_limit"], run["memory_limit_mb"]) == (2.0, 1024)
    assert run["python_image"] == settings.DEFAULTS["run"]["python_image"]


def test_missing_fields_are_filled_from_defaults():
    settings.CONFIG_PATH.write_text("provider:\n  chat_model: only-this\n")
    provider = settings.section("provider")
    assert provider["chat_model"] == "only-this"
    assert provider["ollama_url"] == ""          # absent from the file
    assert settings.section("meraki")["default_network_id"] == ""   # whole section absent


def test_unknown_sections_and_keys_survive_a_save():
    """A hand-edited file mustn't lose anything the app doesn't recognise."""
    settings.CONFIG_PATH.write_text("mine:\n  note: keep me\nprovider:\n  custom: also me\n")
    settings.save("provider", {"chat_model": "x"})
    on_disk = yaml.safe_load(settings.CONFIG_PATH.read_text())
    assert on_disk["mine"] == {"note": "keep me"}
    assert on_disk["provider"]["custom"] == "also me"
    assert on_disk["provider"]["chat_model"] == "x"


def test_written_file_carries_the_explanatory_header():
    settings.save("run", {"cpu_limit": 2.0})
    assert settings.CONFIG_PATH.read_text().startswith("# casewright configuration.")


def test_malformed_yaml_is_a_clear_error_not_a_silent_default():
    settings.CONFIG_PATH.write_text("provider:\n  chat_model: [unclosed\n")
    with pytest.raises(settings.ConfigError, match="not valid YAML"):
        settings.load()


def test_non_mapping_yaml_is_rejected():
    settings.CONFIG_PATH.write_text("- just\n- a list\n")
    with pytest.raises(settings.ConfigError, match="must be a mapping"):
        settings.load()


def test_ensure_config_file_creates_once():
    assert settings.ensure_config_file() is True
    settings.save("run", {"cpu_limit": 3.0})
    assert settings.ensure_config_file() is False   # already there — must not clobber
    assert settings.section("run")["cpu_limit"] == 3.0


# --- .env --------------------------------------------------------------------------

def test_secret_roundtrip_and_clear():
    assert settings.secret(settings.MERAKI_API_KEY) == ""
    settings.set_secret(settings.MERAKI_API_KEY, "  abc123  ")
    assert settings.secret(settings.MERAKI_API_KEY) == "abc123"
    assert "abc123" in settings.ENV_PATH.read_text()
    settings.clear_secret(settings.MERAKI_API_KEY)
    assert settings.secret(settings.MERAKI_API_KEY) == ""
    assert "abc123" not in settings.ENV_PATH.read_text()


def test_env_file_is_owner_only():
    settings.set_secret(settings.MERAKI_API_KEY, "abc123")
    mode = stat.S_IMODE(settings.ENV_PATH.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_saving_a_secret_takes_effect_without_a_restart():
    """The value must be live in this process too — .env is only read at startup."""
    settings.set_secret(settings.MERAKI_API_KEY, "live-key")
    assert os.environ[settings.MERAKI_API_KEY] == "live-key"
    settings.clear_secret(settings.MERAKI_API_KEY)
    assert settings.MERAKI_API_KEY not in os.environ


def test_real_environment_beats_the_file(monkeypatch):
    settings.set_secret(settings.MERAKI_API_KEY, "from-file")
    monkeypatch.setenv(settings.MERAKI_API_KEY, "from-env")
    assert settings.secret(settings.MERAKI_API_KEY) == "from-env"


def test_set_secret_preserves_other_lines():
    settings.ENV_PATH.write_text("# a comment\nCASEWRIGHT_DB_PATH=/data/x.db\n")
    settings.set_secret(settings.MERAKI_API_KEY, "abc123")
    text = settings.ENV_PATH.read_text()
    assert "# a comment" in text and "CASEWRIGHT_DB_PATH=/data/x.db" in text


# --- migration ---------------------------------------------------------------------

@pytest.fixture
def legacy_user():
    """A user whose settings live in the old per-user tables."""
    db.init()
    uid = db.create_user(f"legacy_{os.urandom(4).hex()}", "x")["id"]
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO provider_settings (user_id, provider, ollama_url, chat_model, "
            "embed_model, temperature, reasoning) VALUES (?, 'ollama', 'http://box:11434', "
            "'mistral-small:22b', 'nomic-embed-text:latest', 0.4, 1)", (uid,))
        conn.execute(
            "INSERT INTO run_settings (user_id, python_image, go_image, script_image, "
            "timeout_seconds, cpu_limit, memory_limit_mb, cleanup_policy) "
            "VALUES (?, 'python:3.11', 'golang:1.21', 'debian:12', 300, 2.0, 2048, 'never')", (uid,))
        conn.execute(
            "INSERT INTO kb_settings (user_id, storage_kind, storage_url) "
            "VALUES (?, 'remote', 'http://chroma:8000')", (uid,))
    return uid


def test_migration_carries_settings_into_the_file(legacy_user):
    assert settings.migrate_from_db(legacy_user) is True

    provider = settings.section("provider")
    assert provider["ollama_url"] == "http://box:11434"
    assert provider["chat_model"] == "mistral-small:22b"
    assert provider["temperature"] == 0.4
    assert provider["reasoning"] is True          # stored as 1, must come back a bool

    run = settings.section("run")
    assert (run["python_image"], run["timeout_seconds"], run["cleanup_policy"]) == \
        ("python:3.11", 300, "never")

    assert settings.section("knowledge_base") == {"storage_kind": "remote",
                                                  "storage_url": "http://chroma:8000"}


def test_migration_decrypts_the_stored_api_key(legacy_user, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("CASEWRIGHT_ENC_KEY", key.decode())
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO meraki_data (user_id, api_key_enc, default_network_id) VALUES (?, ?, ?)",
            (legacy_user, Fernet(key).encrypt(b"secret-meraki-key").decode(), "N_42"))

    settings.migrate_from_db(legacy_user)

    assert settings.secret(settings.MERAKI_API_KEY) == "secret-meraki-key"
    assert settings.section("meraki")["default_network_id"] == "N_42"


def test_migration_runs_only_once(legacy_user):
    assert settings.migrate_from_db(legacy_user) is True
    settings.save("provider", {"chat_model": "chosen-later"})
    assert settings.migrate_from_db(legacy_user) is False   # config.yaml exists -> skip
    assert settings.section("provider")["chat_model"] == "chosen-later"


def test_migration_of_a_fresh_install_yields_defaults():
    db.init()
    uid = db.create_user(f"fresh_{os.urandom(4).hex()}", "x")["id"]
    assert settings.migrate_from_db(uid) is True     # nothing to carry over, but writes the file
    assert settings.load() == settings.DEFAULTS
    assert settings.secret(settings.MERAKI_API_KEY) == ""
