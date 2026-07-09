"""AI-agnostic model layer.

LangChain already abstracts providers behind ``BaseChatModel`` / ``Embeddings``,
so this module is just the swap seam: a config plus factories that return those
interfaces. Today they build Ollama models; adding OpenAI/Anthropic later is one
extra branch each. Everything downstream (the pipeline, the graph nodes) depends
only on the returned interfaces, never on a concrete provider.

Model names, base URL, and provider are overridable via environment variables so
different local models can be popped in without editing code:

    AUTOTEST_PROVIDER, AUTOTEST_CHAT_MODEL, AUTOTEST_EMBED_MODEL, AUTOTEST_OLLAMA_URL,
    AUTOTEST_DATA_DIR

AUTOTEST_DATA_DIR (the Chroma store directory) is required and has no default; the
rest fall back to sensible defaults.

Both ingest (rag.ingest.embed) and retrieval (rag.pipeline) build their vector store
from one ProviderConfig, so they can never diverge on store location or embedding
space (a divergence would silently return garbage with no error).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama, OllamaEmbeddings

from rag import REPO_ROOT


DEFAULT_CHAT_MODEL = "qwen3.5:latest"   # tool-capable, non-thinking -> reliable structured output
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"
DEFAULT_COLLECTION = "meraki_openapi"


@dataclass
class ProviderConfig:
    # default_factory so the env is read when ProviderConfig() is instantiated
    provider: str = field(default_factory=lambda: os.environ.get("AUTOTEST_PROVIDER", "ollama"))
    chat_model: str = field(default_factory=lambda: os.environ.get("AUTOTEST_CHAT_MODEL", DEFAULT_CHAT_MODEL))
    embed_model: str = field(default_factory=lambda: os.environ.get("AUTOTEST_EMBED_MODEL", DEFAULT_EMBED_MODEL))
    base_url: str = field(default_factory=lambda: os.environ.get("AUTOTEST_OLLAMA_URL"))


    # AUTOTEST_DATA_DIR is the single source of truth; no default (fail fast).
    persist_dir: str = field(default_factory=lambda: os.environ.get("AUTOTEST_DATA_DIR"))
    collection_name: str = DEFAULT_COLLECTION
    temperature: float = 0.0

    # Disable model "thinking" by default. 
    # AUTOTEST_CHAT_REASONING=1 to re-enable
    reasoning: bool = field(default_factory=lambda: os.environ.get("AUTOTEST_CHAT_REASONING", "").strip().lower() in ("1", "true", "yes", "on"))

    def __post_init__(self):
        if not self.persist_dir:
            raise ValueError(
                "AUTOTEST_DATA_DIR is not set. Point it at the Chroma store directory "
                "(e.g. add it to .env, then `set -a; source .env; set +a`), or pass "
                "ProviderConfig(persist_dir=...)."
            )
        # Resolve a relative persist_dir against the repo root (see rag/__init__.py)
        # so the store location never depends on the process CWD -- promptfoo runs
        # providers from another directory.
        persist = Path(self.persist_dir)
        if not persist.is_absolute():
            persist = REPO_ROOT / persist
        self.persist_dir = str(persist)


def build_chat_model(cfg: ProviderConfig) -> BaseChatModel:
    if cfg.provider == "ollama":
        return ChatOllama(base_url=cfg.base_url, model=cfg.chat_model, temperature=cfg.temperature, reasoning=cfg.reasoning)
    raise ValueError(f"Unsupported provider: {cfg.provider!r}")


def build_embeddings(cfg: ProviderConfig) -> Embeddings:
    if cfg.provider == "ollama":
        return OllamaEmbeddings(base_url=cfg.base_url, model=cfg.embed_model)
    raise ValueError(f"Unsupported provider: {cfg.provider!r}")


def build_vector_store(cfg: ProviderConfig, embeddings=None):
    """Chroma store bound to cfg's collection + persist dir. Both ingest and
    retrieval build it from one ProviderConfig, so they cannot diverge on store
    location or embedding space. Reuses `embeddings` if given, else builds them
    from the same cfg (guaranteeing ingest and query share an embedding model)."""
    from langchain_chroma import Chroma

    if embeddings is None:
        embeddings = build_embeddings(cfg)
    return Chroma(
        collection_name=cfg.collection_name,
        embedding_function=embeddings,
        persist_directory=str(cfg.persist_dir),
    )
