"""Fail-closed secret validation: outside DEV_MODE the app must refuse to start when
the at-rest encryption key (for the stored Meraki key) is missing. In DEV_MODE the
local-file fallback is tolerated. (JWT session secrets were removed with the web
serving layer — the TUI is a local single-user tool with no login.)"""

import pytest

from web import config


def test_rejects_missing_enc_key(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.delenv("CASEWRIGHT_ENC_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CASEWRIGHT_ENC_KEY"):
        config.validate_startup_secrets()


def test_passes_with_enc_key(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", False)
    monkeypatch.setenv("CASEWRIGHT_ENC_KEY", "a-real-enc-key")
    config.validate_startup_secrets()  # no raise


def test_dev_mode_tolerates_missing_enc_key(monkeypatch):
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.delenv("CASEWRIGHT_ENC_KEY", raising=False)
    config.validate_startup_secrets()  # no raise
