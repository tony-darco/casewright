"""Symmetric encryption for secrets at rest (the Meraki API key).

Uses Fernet (AES-128-CBC + HMAC). The key-encryption-key (KEK) comes from
``CASEWRIGHT_ENC_KEY`` — set it from a Docker secret in production, kept separate
from the database volume so a leaked DB can't be decrypted. For local dev, if the
env var is unset we generate one and persist it to a 0600 file outside the repo
(``~/.config/casewright/enc.key``) so it's stable across restarts.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

from web import config

_fernet = None


def _load_key() -> bytes:
    env = os.environ.get("CASEWRIGHT_ENC_KEY", "").strip()
    if env:
        return env.encode()
    path = config._config_dir() / "enc.key"
    if path.exists():
        return path.read_bytes()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    return _f().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    try:
        return _f().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
