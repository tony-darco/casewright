"""Legacy at-rest decryption for the stored Meraki API key.

Superseded by web.settings: the key now lives in plaintext in a 0600 ``.env``, the
normal shape for a single-user local tool. This module survives only so the one-time
migration (``web.settings.migrate_from_db``) can read a key an older build encrypted
into SQLite. Nothing encrypts anything any more, and nothing here creates a key —
with no key to be found there is nothing to migrate, which is the fresh-install case.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

from web import config


def _load_key():
    env = os.environ.get("CASEWRIGHT_ENC_KEY", "").strip()
    if env:
        return env.encode()
    path = config._config_dir() / "enc.key"
    return path.read_bytes() if path.exists() else None


def decrypt(token: str) -> str:
    """The plaintext behind ``token``, or "" if there's no key or it doesn't fit."""
    key = _load_key()
    if not key:
        return ""
    try:
        return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
