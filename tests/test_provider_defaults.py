"""ProviderConfig runs with no environment at all — every field defaults, so a
fresh checkout or a container needs no .env. Env vars only override."""

import os
from unittest import mock

from rag.provider import DEFAULT_PERSIST_DIR, ProviderConfig


def _no_env():
    """Environment with every AUTOTEST_* var stripped."""
    return mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                        if not k.startswith("AUTOTEST_")}, clear=True)


def test_persist_dir_defaults_inside_the_app_without_env():
    with _no_env():
        cfg = ProviderConfig()
    # resolved against REPO_ROOT -> lives inside the app/container, not on the host
    assert cfg.persist_dir.endswith(DEFAULT_PERSIST_DIR)
    assert os.path.isabs(cfg.persist_dir)


def test_data_dir_env_still_overrides_the_default():
    with _no_env():
        os.environ["AUTOTEST_DATA_DIR"] = "/somewhere/else"
        assert ProviderConfig().persist_dir == "/somewhere/else"


def test_remote_chroma_url_skips_local_path_resolution():
    with _no_env():
        os.environ["AUTOTEST_CHROMA_URL"] = "http://chroma:8000"
        cfg = ProviderConfig()
    assert cfg.chroma_url == "http://chroma:8000"


def test_no_env_still_yields_working_model_defaults():
    with _no_env():
        cfg = ProviderConfig()
    assert cfg.provider == "ollama"
    assert cfg.chat_model and cfg.embed_model
    assert cfg.reasoning is False
