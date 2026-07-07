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

Both ingest (rag.ingest.embed) and retrieval (rag.pipeline) build their vector store
from one ProviderConfig, so they can never diverge on store location or embedding
space (a divergence would silently return garbage with no error).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama, OllamaEmbeddings


DEFAULT_CHAT_MODEL = "qwen3.5:latest"   # tool-capable, non-thinking -> reliable structured output
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"
DEFAULT_COLLECTION = "meraki_openapi"

# Resolve the store path from this file (like depgraph.py), not the process CWD, so
# ingest and retrieval hit the same on-disk store no matter where they're invoked
# from (e.g. promptfoo runs providers from the config dir).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSIST_DIR = str(_REPO_ROOT / "data" / "chroma")


@dataclass
class ProviderConfig:
    provider: str = os.environ.get("AUTOTEST_PROVIDER", "ollama")
    chat_model: str = os.environ.get("AUTOTEST_CHAT_MODEL", DEFAULT_CHAT_MODEL)
    embed_model: str = os.environ.get("AUTOTEST_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    base_url: str = os.environ.get("AUTOTEST_OLLAMA_URL")
    # store identity -- shared by ingest and retrieval so they never diverge
    persist_dir: str = os.environ.get("AUTOTEST_DATA_DIR") or DEFAULT_PERSIST_DIR
    collection_name: str = DEFAULT_COLLECTION
    temperature: float = 0.0


def build_chat_model(cfg: ProviderConfig) -> BaseChatModel:
    if cfg.provider == "ollama":
        return ChatOllama(base_url=cfg.base_url, model=cfg.chat_model, temperature=cfg.temperature)
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
