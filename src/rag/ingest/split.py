import json
from pathlib import Path
import requests
import os
from concurrent.futures import ThreadPoolExecutor

from langchain_core.documents import Document

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")

OUT_DIR = Path(os.getenv("AUTOTEST_SPECS_OUT_DIR", "data/specs/path_docs"))
OUT_DIR.mkdir(parents=True, exist_ok=True)


SPEC_URL = "https://raw.githubusercontent.com/meraki/openapi/refs/heads/master/openapi/spec3.json"
# AUTOTEST_SPEC_SOURCE: "local" (default) reads the pinned snapshot -> a reproducible
# corpus that matches graph.json / tests.yaml. "live" fetches the latest spec from
# GitHub and falls back to the snapshot on any error.
SPEC_SOURCE = os.getenv("AUTOTEST_SPEC_SOURCE", "local").strip().lower()
LOCAL_SPEC = Path(os.getenv("AUTOTEST_SPECS_DIR", "data/specs")) / "meraki_open_api_spec.json"


def _load_spec() -> dict:
    if SPEC_SOURCE == "live":
        try:
            return requests.get(SPEC_URL, timeout=30).json()
        except Exception:
            pass  # fall back to the pinned snapshot
    return json.loads(LOCAL_SPEC.read_text())


json_data: dict = _load_spec()
paths_data: dict = json_data.get("paths", {})

def write_one(item):
    path_name, path_doc = item
    path_part = path_name.replace("/{", "").replace("}", "").replace("/", "_")
    input_texts = []
    for method, operation in path_doc.items():
        if method.lower() not in HTTP_METHODS:
            continue  # skip path-level "parameters" and vendor extensions
        out_path = OUT_DIR / f"{method}{path_part}.json"
        if not out_path.exists():
            out_path.write_text(json.dumps(operation, indent=2), encoding="utf-8")

        input_texts.append(Document(
            page_content=json.dumps(operation, indent=2),
            metadata={
                "source": "https://raw.githubusercontent.com/meraki/openapi/refs/heads/master/oenapi/spec3.json",
                "method": method.upper(),
                "path": path_name,
                "endpoint_id": f"{method.upper()} {path_name}",
            },
        ))
    
    return input_texts



if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=8) as pool:
        pool.map(write_one, paths_data.items())