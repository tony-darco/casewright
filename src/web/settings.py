"""File-backed application settings — the source of truth for everything a user can change.

Two files at the repo root, both readable and hand-editable:

  * ``config.yaml`` — non-secret settings: model provider, knowledge-base storage,
    run-container images and limits, the default example network. Written on first
    run with every option and its default spelled out.
  * ``.env`` — secrets (today just ``MERAKI_API_KEY``) plus the bootstrap variables
    ``web.config`` reads. Kept 0600, and gitignored.

Settings are read from these files and Settings writes back to them, so the UI and
the files can never disagree — there is no third copy in the database.

Secrets read the real environment first, falling back to ``.env``, so a container or
CI secret store can inject a key without a file. Writes update both the file and this
process's environment, so a save takes effect without a restart.

Settings are global, not per-user: casewright is a single-user local tool (see
tui.identity). Per-user *data* — tests, runs, knowledge-base versions, the cached
Meraki org tree — stays in SQLite (web.db).
"""

import os
from pathlib import Path

import yaml
from dotenv import dotenv_values, set_key, unset_key

from web import config

REPO_ROOT = config.WEB_DIR.parents[1]

# Module-level rather than computed per call so tests can point them at a tmp dir
# instead of the developer's real files.
CONFIG_PATH = REPO_ROOT / "config.yaml"
ENV_PATH = REPO_ROOT / ".env"

# The name of the one secret the Settings UI manages. Bootstrap secrets are read
# straight from the environment by web.config and are not editable in the app.
MERAKI_API_KEY = "MERAKI_API_KEY"

# Every setting, its section, and its default. This is the schema: load() fills in
# anything the file omits, so a hand-truncated config.yaml still yields a complete,
# valid config. Section order here is the order they're written to the file.
DEFAULTS = {
    # Blank/None means "use the backend default" — see rag.provider.ProviderConfig,
    # which stays authoritative. provider_store.overrides() only maps the fields that
    # are actually set, so an untouched section leaves the pipeline on its defaults.
    "provider": {
        "provider": "ollama",
        "ollama_url": "",
        "chat_model": "",
        "embed_model": "",
        "temperature": None,
        "reasoning": None,
    },
    "knowledge_base": {
        "storage_kind": "local",   # 'local' | 'remote'
        "storage_url": "",
    },
    "meraki": {
        "default_network_id": "",
    },
    "run": {
        "python_image": "python:3.12-slim",
        "go_image": "golang:1.22-alpine",
        "script_image": "ubuntu:24.04",
        "timeout_seconds": 120,
        "cpu_limit": 1.0,
        "memory_limit_mb": 512,
        "cleanup_policy": "always",   # 'always' | 'on_success' | 'never'
    },
}

# Re-emitted on every write: yaml.safe_dump can't preserve comments, so the guidance
# lives here rather than inline in the file where a save would silently eat it.
_HEADER = """\
# casewright configuration.
#
# Written by the app and safe to hand-edit — Settings reads and writes this exact
# file. Secrets do NOT live here; the Meraki API key is in .env.
#
# provider        model provider. Blank fields fall back to the built-in defaults
#                 (chat qwen3.5:latest, embed nomic-embed-text:latest, Ollama at
#                 http://localhost:11434). temperature: 0-2. reasoning: true/false
#                 to force model "thinking" on or off, null to leave it to the backend.
# knowledge_base  where the vector store lives. 'local' keeps it inside the app;
#                 'remote' needs storage_url pointing at a Chroma server.
# meraki          default_network_id is the example network baked into generated
#                 tests. Runs provision their own ephemeral network regardless.
# run             the ephemeral containers generated tests execute in.
#                 cleanup_policy: always | on_success | never.

"""


class ConfigError(RuntimeError):
    """config.yaml exists but could not be parsed."""


def load() -> dict:
    """Every section, with defaults filled in for anything the file leaves out.

    Sections the file adds that we don't know about are preserved, so a save can't
    silently drop them.
    """
    raw = {}
    if CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{CONFIG_PATH} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{CONFIG_PATH} must be a mapping of sections, not {type(raw).__name__}.")
    merged = {name: dict(fields) for name, fields in DEFAULTS.items()}
    for name, fields in raw.items():
        if isinstance(fields, dict):
            merged.setdefault(name, {}).update(fields)
    return merged


def section(name: str) -> dict:
    """One section's settings, defaults filled in."""
    return load()[name]


def save(name: str, values: dict) -> None:
    """Merge ``values`` into section ``name`` and rewrite config.yaml."""
    data = load()
    data[name] = {**data.get(name, {}), **values}
    _write(data)


def _write(data: dict) -> None:
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-replace: a crash mid-write can't leave a truncated live config.
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(_HEADER + body, encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def ensure_config_file() -> bool:
    """Write config.yaml with the defaults if it isn't there yet. True if created."""
    if CONFIG_PATH.exists():
        return False
    _write(load())
    return True


# --- secrets (.env) ----------------------------------------------------------------

def secret(name: str) -> str:
    """A secret's value: the real environment first, then ``.env``."""
    value = os.environ.get(name)
    if value is None and ENV_PATH.exists():
        value = dotenv_values(ENV_PATH).get(name)
    return (value or "").strip()


def set_secret(name: str, value: str) -> None:
    """Persist a secret to ``.env`` (creating it 0600) and to this process's env."""
    value = value.strip()
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    set_key(str(ENV_PATH), name, value)
    _owner_only(ENV_PATH)
    os.environ[name] = value


def clear_secret(name: str) -> None:
    if ENV_PATH.exists():
        unset_key(str(ENV_PATH), name)
        _owner_only(ENV_PATH)
    os.environ.pop(name, None)


def _owner_only(path: Path) -> None:
    """0600 — the file holds plaintext secrets. Best-effort: a filesystem that
    doesn't do POSIX modes (a Windows share, some mounts) isn't a reason to fail
    the save."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# --- one-time migration ------------------------------------------------------------

def migrate_from_db() -> bool:
    """Move settings out of SQLite and into the files, once.

    Earlier builds kept these in the settings tables, with the Meraki key encrypted
    at rest. A missing config.yaml is the marker for "not migrated yet": we seed it
    from the database rather than from the defaults, so an existing install keeps its
    configured provider, containers, and API key. Returns True if it migrated.

    Those tables are single-row now (id = 1), so there is one set of settings to
    carry over. The old rows are left in place — read-only from here on, and cheap
    insurance if this needs unpicking.
    """
    if CONFIG_PATH.exists():
        return False

    from web import db
    from web.services import crypto

    data = load()
    api_key_enc = None
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT provider, ollama_url, chat_model, embed_model, temperature, reasoning "
            "FROM provider_settings WHERE id = 1").fetchone()
        if row:
            fields = dict(row)
            fields["reasoning"] = None if fields["reasoning"] is None else bool(fields["reasoning"])
            data["provider"].update(fields)

        row = conn.execute(
            "SELECT python_image, go_image, script_image, timeout_seconds, cpu_limit, "
            "memory_limit_mb, cleanup_policy FROM run_settings WHERE id = 1").fetchone()
        if row:
            data["run"].update(dict(row))

        row = conn.execute(
            "SELECT storage_kind, storage_url FROM kb_settings WHERE id = 1").fetchone()
        if row:
            data["knowledge_base"].update(dict(row))

        row = conn.execute(
            "SELECT api_key_enc, default_network_id FROM meraki_data WHERE id = 1").fetchone()
        if row:
            data["meraki"]["default_network_id"] = row["default_network_id"] or ""
            api_key_enc = row["api_key_enc"]

    _write(data)
    if api_key_enc:
        key = crypto.decrypt(api_key_enc)
        if key:
            set_secret(MERAKI_API_KEY, key)
    return True
