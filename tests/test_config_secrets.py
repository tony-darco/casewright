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
