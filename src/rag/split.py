import json
from pathlib import Path
import requests
from concurrent.futures import ThreadPoolExecutor

from langchain_core.documents import Document

OUT_DIR = Path("data/specs/path_docs")
OUT_DIR.mkdir(parents=True, exist_ok=True)


try:
    json_data: dict = requests.get("https://raw.githubusercontent.com/meraki/openapi/refs/heads/master/oenapi/spec3.json").json()
except Exception:
    with open(Path("data/specs/reduced_open_api_spec.json"), 'r+') as api_spe_file:
        json_data = json.load(api_spe_file)

paths_data: dict = json_data.get("paths", {})

def write_one(item):
    path_name, path_doc = item
    path_part = path_name.replace("/{", "").replace("}", "").replace("/", "_")
    input_texts = []
    for method, operation in path_doc.items():
        out_path = OUT_DIR / f"{method}{path_part}.json"
        if not out_path.exists():
            out_path.write_text(json.dumps(operation, indent=2), encoding="utf-8")

        input_texts.append(Document(
            page_content=json.dumps(operation, indent=2), metadata={"source": "https://raw.githubusercontent.com/meraki/openapi/refs/heads/master/oenapi/spec3.json"}
        ))
    
    return input_texts



if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=8) as pool:
        pool.map(write_one, paths_data.items())