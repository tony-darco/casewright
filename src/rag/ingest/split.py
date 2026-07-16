import json
from pathlib import Path
import requests
import os
import sys
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


def _load_spec():
    """Return (spec_dict, source_str). source_str is the URL or local path the
    spec actually came from, so Document provenance reflects reality."""
    if SPEC_SOURCE == "live":
        try:
            resp = requests.get(SPEC_URL, timeout=30)
            resp.raise_for_status()
            return resp.json(), SPEC_URL
        except Exception as e:
            print(f"WARNING: live spec fetch failed ({e}); using pinned snapshot "
                  f"{LOCAL_SPEC}", file=sys.stderr)
    return json.loads(LOCAL_SPEC.read_text()), str(LOCAL_SPEC)


json_data, SPEC_ACTUAL_SOURCE = _load_spec()
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
                "source": SPEC_ACTUAL_SOURCE,
                "method": method.upper(),
                "path": path_name,
                "endpoint_id": f"{method.upper()} {path_name}",
            },
        ))
    
    return input_texts


def split_openapi_custom(spec: dict, source_label: str) -> list:
    """Pure custom split (Knowledge Base feature): one Document per HTTP method/
    operation, mirroring write_one's chunking exactly, but taking an already-parsed
    OpenAPI dict and a caller-supplied source label instead of the module's own
    hardcoded spec load — no OUT_DIR file writes, no dependency on SPEC_SOURCE/
    LOCAL_SPEC/SPEC_URL. Assumes an OpenAPI-shaped document (paths -> methods ->
    operations); a non-OpenAPI document will simply yield no documents."""
    docs = []
    for path_name, path_doc in (spec.get("paths") or {}).items():
        for method, operation in (path_doc or {}).items():
            if method.lower() not in HTTP_METHODS:
                continue  # skip path-level "parameters" and vendor extensions
            docs.append(Document(
                page_content=json.dumps(operation, indent=2),
                metadata={
                    "source": source_label,
                    "method": method.upper(),
                    "path": path_name,
                    "endpoint_id": f"{method.upper()} {path_name}",
                },
            ))
    return docs


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=8) as pool:
        pool.map(write_one, paths_data.items())