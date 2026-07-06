"""AI-agnostic model layer.

LangChain already abstracts providers behind ``BaseChatModel`` / ``Embeddings``,
so this module is just the swap seam: a config plus factories that return those
interfaces. Today they build Ollama models; adding OpenAI/Anthropic later is one
extra branch each. Everything downstream (the pipeline, the graph nodes) depends
only on the returned interfaces, never on a concrete provider.

Model names, base URL, and provider are overridable via environment variables so
different local models can be popped in without editing code:

    AUTOTEST_PROVIDER, AUTOTEST_CHAT_MODEL, AUTOTEST_EMBED_MODEL, AUTOTEST_OLLAMA_URL
"""

import os
from dataclasses import dataclass
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama, OllamaEmbeddings

DEFAULT_BASE_URL = "http://192.168.1.17:11434"
DEFAULT_CHAT_MODEL = "mistral-small:22b"   # tool-capable, non-thinking -> reliable structured output
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"

# Chroma store identity. Mirrors src/rag/ingest/embed.py so the pipeline reads
# exactly what the ingest step wrote (same collection, same on-disk location).
COLLECTION_NAME = "meraki_openapi"
PERSIST_DIR = Path("data/chroma")


@dataclass
class ProviderConfig:
    provider: str = os.environ.get("AUTOTEST_PROVIDER", "ollama")
    chat_model: str = os.environ.get("AUTOTEST_CHAT_MODEL", DEFAULT_CHAT_MODEL)
    embed_model: str = os.environ.get("AUTOTEST_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    base_url: str = os.environ.get("AUTOTEST_OLLAMA_URL", DEFAULT_BASE_URL)
    temperature: float = 0.0


def build_chat_model(cfg: ProviderConfig) -> BaseChatModel:
    if cfg.provider == "ollama":
        return ChatOllama(base_url=cfg.base_url, model=cfg.chat_model, temperature=cfg.temperature)
    raise ValueError(f"Unsupported provider: {cfg.provider!r}")


def build_embeddings(cfg: ProviderConfig) -> Embeddings:
    if cfg.provider == "ollama":
        return OllamaEmbeddings(base_url=cfg.base_url, model=cfg.embed_model)
    raise ValueError(f"Unsupported provider: {cfg.provider!r}")


def build_vector_store(embeddings: Embeddings):
    """Chroma store bound to the shared collection/persist dir. Provider-neutral:
    it takes whatever Embeddings the factory produced."""
    from langchain_chroma import Chroma

    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(PERSIST_DIR),
    )
