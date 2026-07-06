from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os

from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma

from rag.ingest.split import paths_data, write_one

ollama_emb = OllamaEmbeddings(
    base_url = "http://192.168.1.17:11434",
    model= "nomic-embed-text:latest"
)

vector_collective_name = "meraki_openapi"
PERSIST_DIR = os.getenv("AUTOTEST_DATA_DIR", "data/chroma")

vector_store = Chroma(
    collection_name=vector_collective_name,
    embedding_function=ollama_emb,
    persist_directory=str(PERSIST_DIR),
)

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(write_one, paths_data.items())

    documents = [doc for docs in results for doc in docs]
    if documents:
        vector_store.add_documents(documents)

