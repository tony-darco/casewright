# casewright

casewright turns application and feature specifications into working test code. Describe
what you want tested — an endpoint, a feature, an edge case — and it retrieves the right
context from your API spec, then writes runnable tests against it.

It is not a boilerplate generator. Under the hood it's a hybrid agentic RAG pipeline: a
request goes in, gets decomposed and grounded against the ingested spec corpus, and comes
back out as reviewable test code in the language and framework you asked for. Tests run in
a short-lived, resource-capped Docker container against an ephemeral Meraki network that's
torn down afterwards.

casewright is a **terminal application** (a Textual TUI). It runs locally as a single-user
tool — no server, no sign-up — and the whole app is driven from one command bar: type a
test description to generate, or a `/command` to navigate and act.

> Generated code is **untrusted output**: it can be influenced by the spec text it was
> grounded in, so review every test before running it. The container caps the blast radius
> but is not a security boundary — it reaches your Meraki org.

> **casewright is a placeholder name.** The product is early-stage — spec-grounded test
> generation and execution are in; integrations and dashboards are being built out from
> here.

## Install

Prerequisites:

- Python 3.10+
- [Ollama](https://ollama.com) running locally (or reachable) with a chat model and an
  embedding model pulled — casewright talks to it for both retrieval and generation.
- Docker, to run generated tests. Only needed for execution; generation works without it.

```bash
git clone <this-repo>
cd autotest

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

Nothing to set up before the first run — casewright writes its own config on startup and
you can do everything from `/settings`. Configuration lives in two files at the repo root,
and the app reads *and writes* both, so what `/settings` shows and what the files say can
never disagree:

| File | Holds | Notes |
| --- | --- | --- |
| `config.yaml` | every non-secret setting — model provider, knowledge-base storage, run-container images and limits | written with the defaults on first run, commented, safe to hand-edit |
| `.env` | secrets (`MERAKI_API_KEY`) and deployment knobs (database path, Meraki base URL) | kept `0600`, gitignored — see [.env.example](.env.example) |

Edit either by hand or from `/settings`; both paths end up in the same place. A real
exported environment variable wins over `.env`, so a container or CI secret store can
inject the API key without a file.

> The Meraki API key is stored in plaintext in `.env`, the usual arrangement for a local
> single-user tool. It's readable by anything that can read your home directory — use a
> key scoped to the org you're testing against.

The database (`~/.config/casewright/casewright.db`, or `CASEWRIGHT_DB_PATH`) holds records
only: tests, runs, knowledge-base versions, and the cached Meraki org tree. Upgrading from
a build that kept settings in there migrates them into the files on first run.

## Start

```bash
casewright        # installed console script
# or, equivalently:
python -m tui
```

That launches the terminal app. It resolves a single local user on first run (there is no
login) and drops you at the command bar.

From there: **`/settings`** to connect a Meraki org and your model provider, **`/kb`** to
build a knowledge base from an API spec, then describe a test and press Enter. Name a
device serial in the description (`…broadcasting on Q2KD-DEMR-82P7`) to pin the test to
that exact device — `/run` claims that one and no other.

## Commands

Everything happens through the text box at the bottom of the screen.

- **Plain text is a test description.** Type what you want tested and press Enter — it
  generates, streaming the result into the transcript above.
- **`/` starts a command.** Start typing one and a menu of matches appears above the box:
  **↑/↓** move the highlight, **Tab** completes it, **Enter** runs it (even half-typed —
  `/h`↵ runs `/help`), **Esc** closes the menu.

| | Command | Does |
| --- | --- | --- |
| **Go** | `/tests` | list every test |
| | `/new` | start a new test |
| | `/prompt` | the current test's prompt |
| | `/code` | the current test's generated code |
| | `/config` | the run configuration (network + hardware) |
| | `/output` | the current test's latest run output |
| | `/runs` | run history across all tests |
| | `/coverage` | spec-coverage tree |
| | `/kb` | knowledge base |
| | `/settings` | app settings |
| **Do** | `/generate` | (re)generate from the current prompt |
| | `/run` | run the current test |
| | `/repair` | send a failed run back through the pipeline |
| | `/export` | copy the code to the clipboard |
| | `/open <name or #>` | load a test from the library |
| | `/version next \| prev` | step through a test's versions |
| | `/save` | save the code or config you're viewing |
| | `/activate <#>` | activate a knowledge-base version |
| | `/embed` | embed the knowledge-base source |
| | `/delete <#>` | delete an errored knowledge-base version |
| **App** | `/help` | show the full command list |
| | `/back` | return to the workspace |
| | `/quit` | exit (also `Ctrl+Q`) |

`/settings`, `/kb`, `/coverage`, and `/runs` open their own screens; the command bar comes
with you, so you can jump anywhere from anywhere.
