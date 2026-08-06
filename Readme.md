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

No configuration file is needed — the model provider, vector store, and Meraki integration
are all configured at runtime from `/settings`. For development, set:

```bash
export CASEWRIGHT_DEV=1   # skip the production secret check, use dev-only defaults
```

In production one secret is required, because the app needs it before it can read its own
database (it encrypts the stored Meraki API key at rest):

```bash
export CASEWRIGHT_ENC_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export CASEWRIGHT_DEV=0
```

See [.env.example](.env.example) for optional overrides (database path, spec directory,
Meraki base URL, and the Ollama/Chroma host allowlist).

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
- **The menu only offers what works here.** A line above the box names where you are —
  `home`, `test · <name>`, `knowledge base`, `runs` — and the suggestions are scoped to
  it. No `/run` before a test is loaded, no `/activate` outside the knowledge base.
  **`/help`** fills the same menu with everything available in that place.
- **↑/↓ recall what you typed before**, when the menu is closed.

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
| | `/kb` | knowledge bases (see below) |
| | `/settings` | app settings |
| **Do** | `/generate` | regenerate from the current prompt |
| | `/run` | run the current test |
| | `/repair` | send a failed run back through the pipeline |
| | `/edit --code` | edit the generated code (saves a new version) |
| | `/edit --prompt` | edit the description it was generated from |
| | `/export` | copy the code to the clipboard |
| | `/open <name or #>` | load a test from the library |
| | `/version next \| prev` | step through a test's versions |
| | `/save` | save what you're editing (Settings) |
| **Knowledge base** | `/kb` | list your knowledge bases |
| | `/kb --new <url\|path> --split custom` | embed a new one from a URL or file |
| | `/kb --new … --split langchain` | generic recursive chunking instead |
| | `/kb --new … --name "My spec"` | label it (else the filename/URL is used) |
| | `/kb --storage <chroma-url>` | keep vectors on a remote Chroma server |
| | `/kb --storage local` | keep them inside the app |
| | `/activate <#>` | make a version the one generation retrieves against |
| | `/delete <#>` | delete an errored version |
| **App** | `/help` | everything available where you are |
| | `/back` | return to the workspace |
| | `/quit` | exit (also `Ctrl+Q`) |

`/settings`, `/kb`, `/coverage`, and `/runs` open their own screens; the command bar comes
with you, so you can jump anywhere from anywhere — `/kb --new …` works without visiting
the knowledge base first.

**Opening a test** summarises it rather than dumping the file: the prompt it came from,
its last few runs, and the head of the code. `/code` prints all of it, `/prompt` the whole
prompt, `/output` the full run log.

**`/edit`** opens the text in place — **ctrl+s** saves, **esc** discards. Saving code
snapshots a new version, so the previous one stays reachable via `/version prev`; saving
a prompt leaves the code alone, since rewriting the prompt is how you set up the next
`/generate`. (Syntax highlighting needs tree-sitter — `pip install 'textual[syntax]'`.)
