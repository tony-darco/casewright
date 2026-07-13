# casewright

casewright turns application and feature specifications into working test code. Describe
what you want tested — an endpoint, a feature, an edge case — and it retrieves the right
context from your API spec, then writes runnable tests against it.

It is not a boilerplate generator. Under the hood it's a hybrid agentic RAG pipeline: a
request goes in, gets decomposed and grounded against the ingested spec corpus, and comes
back out as reviewable test code in the language and framework you asked for.

> **casewright is a placeholder name.** The product is early-stage — the MVP focuses on
> spec-grounded test generation; execution, integrations, and dashboards are being built
> out from here.

Generated code is treated as **untrusted output**: every test is meant for human review,
and — once execution lands — for running in a sandbox, not silently in a developer or CI
workflow. See [Prompt injection in generated code](#prompt-injection-in-generated-code)
below.

## Usage

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) running locally (or reachable) with a chat model and an
  embedding model pulled — casewright talks to it for both retrieval and generation.

### Setup

```bash
git clone <this-repo>
cd autotest

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Create a `.env` file in the repo root with at least:

```bash
AUTOTEST_DATA_DIR=data/chroma        # where the vector store lives
AUTOTEST_PROVIDER=ollama
AUTOTEST_OLLAMA_URL=http://localhost:11434
AUTOTEST_CHAT_MODEL=<a chat model you've pulled>
AUTOTEST_EMBED_MODEL=<an embedding model you've pulled>

JWT_SECRET=<a real secret>
CASEWRIGHT_ENC_KEY=<a real Fernet key>   # encrypts the stored Meraki API key at rest
```

For local development only, set `CASEWRIGHT_DEV=1` to skip the production secret checks
and run on generated dev-only defaults.

### Build the knowledge base

casewright generates tests against an OpenAPI spec corpus, split into per-endpoint chunks
and embedded into a local Chroma vector store:

```bash
python -m rag.ingest.split    # fetch/snapshot the spec, split it into per-endpoint docs
python -m rag.ingest.embed    # embed those docs into the Chroma store at AUTOTEST_DATA_DIR
```

The pinned snapshot (`AUTOTEST_SPEC_SOURCE=local`, the default) is reproducible; set it to
`live` to pull the latest spec instead.

### Run it

```bash
uvicorn web.main:app --reload
```

Visit `http://localhost:8000`, sign up, and:

1. Connect a Meraki organization in **Settings → Meraki integration** (org ID, verified
   against the Meraki Dashboard API) so its networks and devices become available to
   reference in a prompt with `@device-name`.
2. Describe a test on the composer screen and pick a target language (Python/pytest,
   TypeScript/Jest, Java/JUnit 5, Go, or C#/xUnit).
3. Review the generated code in the workspace — Prompt, Code, and Output tabs — export it
   (copy or download), or refine the prompt and regenerate a new version. Every test keeps
   its full version history.

## How It Works

```text
prompt (+ @device context)
  -> RAG-Fusion query expansion
  -> vector retrieval, fused across query variants
  -> LLM rerank
  -> CRAG relevance grading (rewrite + re-retrieve on low confidence)
  -> call-order dependency resolution
  -> test generation, targeted at the requested language/framework
  -> deterministic sanitize (strip fences/prose down to pure source)
  -> validation (does it parse/compile as that language?)
```

Retrieval nodes degrade gracefully on a bad model response (e.g. fall back to the
unranked pool) but never on a genuine backend outage — that surfaces as an error rather
than a silent empty result, so a down model backend never looks like "nothing found."

## What You Get

- A **composer → workspace** flow: describe a test, watch it stream in, review the result.
- A per-user **test library** with full version history — regenerate a test and its prior
  prompt/code stay reachable, not overwritten.
- **Five target languages**: Python (pytest), TypeScript (Jest), Java (JUnit 5), Go
  (`testing`), and C# (xUnit) — each with its own generation and sanitization rules.
- **Meraki integration**: connect organizations, verify networks, and reference real
  devices in a prompt via `@device-name` mentions.
- **Export**: copy generated code to the clipboard or download it as a file.
- A **Settings** area covering account, Meraki integration, output language, model
  provider (Ollama endpoint/models/temperature), and logs (app-wide + per-test
  generation stages and errors).
- Placeholder **Runs** and **Coverage** dashboards, ready for real data as those land.

## Repository Map

| Path | Purpose |
| --- | --- |
| `src/web/` | FastAPI app: routers, Jinja2 templates, static assets, auth, per-user services |
| `src/rag/` | The generation pipeline — `pipeline.py` (the LangGraph), `graph/` (prompts, language registry, sanitize, validate, dependency graph), `provider.py` (model/vector-store wiring), `ingest/` (spec split + embed) |
| `src/eval/` | Evaluation harness for the retrieval/generation pipeline (promptfoo, graph-based, and sampling evals) |
| `data/` | The spec corpus and local Chroma vector store |
| `fixtures/` | Fixed inputs used by tests and evals |
| `tests/` | Unit tests for the web layer and the pipeline |
| `docs/` | Frontend/UX build handoff and design mockups |

## Prompt injection in generated code

Generated test code may be influenced by untrusted specification text — summaries or
descriptions pulled from the retrieved corpus. A compromised or poisoned source could
attempt to steer the model into producing harmful or unexpected test code, such as shell
commands or other side effects. Treat generated code as untrusted output that requires
human review before execution, and, once execution lands, run it in a sandbox rather than
directly in a developer or CI workflow.

## Status

Early development. Spec-grounded test generation is the current focus; test execution,
Jira/feature-tracking integration, and richer dashboards are on the roadmap next.
