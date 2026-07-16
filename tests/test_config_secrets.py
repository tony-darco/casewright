"""Fail-closed secret validation (issue #10): the app must refuse to start with a
missing/sentinel JWT secret or a missing encryption key, unless DEV_MODE is set."""

import pytest

from web import config


def test_rejects_dev_sentinel_outside_dev_mode(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(config, "JWT_SECRET", config.JWT_DEV_SENTINEL)
    monkeypatch.setenv("CASEWRIGHT_ENC_KEY", "a-real-enc-key")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        config.validate_startup_secrets()


def test_rejects_empty_jwt_secret(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(config, "JWT_SECRET", "   ")
    monkeypatch.setenv("CASEWRIGHT_ENC_KEY", "a-real-enc-key")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        config.validate_startup_secrets()


def test_rejects_missing_enc_key(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(config, "JWT_SECRET", "a-real-strong-secret")
    monkeypatch.delenv("CASEWRIGHT_ENC_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CASEWRIGHT_ENC_KEY"):
        config.validate_startup_secrets()


def test_passes_with_real_secrets(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setattr(config, "JWT_SECRET", "a-real-strong-secret")
    monkeypatch.setenv("CASEWRIGHT_ENC_KEY", "a-real-enc-key")
    config.validate_startup_secrets()  # no raise


def test_dev_mode_tolerates_insecure_fallbacks(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "JWT_SECRET", config.JWT_DEV_SENTINEL)
    monkeypatch.delenv("CASEWRIGHT_ENC_KEY", raising=False)
    config.validate_startup_secrets()  # no raise


# --- dev JWT secret: generated + persisted, never the source-controlled sentinel ---

def test_dev_jwt_secret_is_generated_persisted_and_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    first = config._dev_jwt_secret()

    assert first != config.JWT_DEV_SENTINEL   # not the value that ships in the repo
    assert len(first) >= 32

    key = tmp_path / "casewright" / "jwt.key"
    assert key.exists(), "secret must persist outside the repo"
    assert oct(key.stat().st_mode)[-3:] == "600", "secret file must not be world/group readable"

    # stable across restarts, or every reload would invalidate live sessions
    assert config._dev_jwt_secret() == first


def test_dev_jwt_secret_differs_per_machine(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "a"))
    a = config._dev_jwt_secret()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "b"))
    b = config._dev_jwt_secret()
    assert a != b
