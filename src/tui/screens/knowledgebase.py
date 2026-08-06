"""Knowledge base — ingest an API spec (URL or file), embed it with live progress,
activate/delete versions, choose local vs. remote storage.

Mirrors the KB routes in web.routers.settings. Ingestion runs in a daemon thread via
``kb_registry`` (same as the web layer); this screen subscribes to the job for stage
updates.
"""

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button, Footer, Header, Input, Label, ListItem, ListView, Select, Static,
)

from web.services import kb_ingest, kb_registry, kb_store, provider_store, url_fetch
from tui.command_screen import CommandScreen
from tui.commands import Command
from tui.streaming import pump

_SOURCE = [("From URL", "link"), ("From file", "upload")]
_SPLIT = [("LangChain split", "langchain"), ("Custom OpenAPI split", "custom")]
_STORAGE = [("Local (inside the app)", "local"), ("Remote Chroma server", "remote")]


class KnowledgeBaseScreen(CommandScreen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self):
        super().__init__()
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="kb-main"):
            yield Static("STORAGE", classes="eyebrow")
            with Horizontal(classes="row"):
                yield Select(_STORAGE, id="kb-storage-kind", allow_blank=False)
                yield Input(placeholder="http://chroma-host:8000 (remote only)", id="kb-storage-url")
                yield Button("Save storage", id="kb-save-storage")
            yield Static("", id="kb-storage-status")

            yield Static("ADD A KNOWLEDGE BASE", classes="eyebrow")
            with Horizontal(classes="row"):
                yield Select(_SOURCE, value="link", id="kb-source", allow_blank=False)
                yield Select(_SPLIT, value="langchain", id="kb-split", allow_blank=False)
            yield Input(placeholder="URL to an OpenAPI spec, or a local file path", id="kb-input")
            yield Button("Start embedding", id="kb-start", variant="primary")
            yield Static("", id="kb-status")

            yield Static("VERSIONS  (/activate · /delete — by number, e.g. /activate 2)",
                         classes="eyebrow")
            yield ListView(id="kb-versions")

        yield self.command_bar()
        yield Footer()

    def on_mount(self) -> None:
        st = kb_store.get_storage()
        self.query_one("#kb-storage-kind", Select).value = st["storage_kind"]
        self.query_one("#kb-storage-url", Input).value = st["storage_url"]
        self._refresh_versions()

    def _refresh_versions(self) -> None:
        lv = self.query_one("#kb-versions", ListView)
        lv.clear()
        for i, v in enumerate(kb_store.list_versions(self.app.uid), start=1):
            flag = " ★active" if v.get("is_active") else ""
            extra = f" — {v['error_message']}" if v["status"] == "error" and v["error_message"] else ""
            label = f"{i}. [{v['status']}] {v['name']} · {v['doc_count']} docs{flag}{extra}"
            lv.append(ListItem(Label(label), name=str(v["id"])))

    def _selected_version_id(self, ref: str = ""):
        """The version id for an ``/activate``/``/delete`` — by 1-based number, else the highlighted row."""
        if ref.strip().isdigit():
            versions = kb_store.list_versions(self.app.uid)
            i = int(ref) - 1
            return versions[i]["id"] if 0 <= i < len(versions) else None
        item = self.query_one("#kb-versions", ListView).highlighted_child
        return int(item.name) if item and item.name else None

    # --- storage -----------------------------------------------------------------
    def _save_storage(self) -> None:
        kind = self.query_one("#kb-storage-kind", Select).value
        url = self.query_one("#kb-storage-url", Input).value.strip()
        if kind == "remote":
            if url and not url.startswith(("http://", "https://")):
                url = "http://" + url
            if not url:
                self.query_one("#kb-storage-status", Static).update(
                    "[red]Enter a Chroma server URL for remote storage.[/red]")
                return
        else:
            url = ""
        kb_store.set_storage(kind, url)
        self.query_one("#kb-storage-status", Static).update("[green]Storage saved.[/green]")

    # --- ingest ------------------------------------------------------------------
    def _start(self) -> None:
        if self._busy:
            return
        source = self.query_one("#kb-source", Select).value
        split = self.query_one("#kb-split", Select).value
        value = self.query_one("#kb-input", Input).value.strip()
        if not value:
            self.query_one("#kb-status", Static).update("[red]Enter a URL or file path.[/red]")
            return
        self._busy = True
        self.query_one("#kb-status", Static).update("Fetching source…")
        self._fetch_and_ingest(source, split, value)

    @work(thread=True)
    def _fetch_and_ingest(self, source: str, split: str, value: str) -> None:
        try:
            if source == "link":
                content = url_fetch.fetch_spec_url(value)
                label = value
            else:
                content = Path(value).expanduser().read_bytes()
                label = Path(value).name
        except (url_fetch.FetchError, OSError) as exc:
            self._busy = False
            self.app.call_from_thread(self.query_one("#kb-status", Static).update,
                                      f"[red]{exc}[/red]")
            return
        self.app.call_from_thread(self._begin_ingest, content, split, label)

    def _begin_ingest(self, content: bytes, split: str, label: str) -> None:
        uid = self.app.uid
        v = kb_store.create_embedding(uid, label, "link", split, label)
        prov = provider_store.overrides()
        job = kb_registry.start(v["id"], uid, lambda job: kb_ingest.run_ingest(
            job, uid, v["id"], content, split, label, prov))
        self._refresh_versions()
        self.query_one("#kb-status", Static).update("Embedding…")
        self._subscribe(job)

    @work(thread=True)
    def _subscribe(self, job) -> None:
        pump(self.app, job, self._on_kb_event)

    def _on_kb_event(self, ev: dict) -> None:
        if ev.get("type") == "stage":
            self.query_one("#kb-status", Static).update(f"Embedding… ({ev.get('stage', '')})")
        elif ev.get("type") == "done":
            self._busy = False
            ok = ev.get("status") == "done"
            self.query_one("#kb-status", Static).update(
                f"[green]Embedded {ev.get('doc_count', 0)} docs.[/green]" if ok
                else f"[red]{ev.get('message', 'Ingest failed.')}[/red]")
            self._refresh_versions()

    # --- activate / delete -------------------------------------------------------
    def action_activate(self, ref: str = "") -> None:
        vid = self._selected_version_id(ref)
        if vid is None:
            self.query_one("#kb-status", Static).update("[yellow]No version selected.[/yellow]")
            return
        if kb_store.set_active(self.app.uid, vid):
            self.query_one("#kb-status", Static).update("[green]Activated.[/green]")
        else:
            self.query_one("#kb-status", Static).update(
                "[red]Can't activate (not found, or still embedding).[/red]")
        self._refresh_versions()

    def action_delete(self, ref: str = "") -> None:
        vid = self._selected_version_id(ref)
        if vid is None:
            self.query_one("#kb-status", Static).update("[yellow]No version selected.[/yellow]")
            return
        kb_store.delete_version(self.app.uid, vid)
        self._refresh_versions()

    # --- command routing ---------------------------------------------------------
    def on_command(self, cmd: Command) -> bool:
        if cmd.name == "activate":
            self.action_activate(cmd.args)
        elif cmd.name == "delete":
            self.action_delete(cmd.args)
        elif cmd.name == "embed":
            self._start()
        else:
            return False
        return True

    def on_text(self, text: str) -> None:
        """Plain text becomes the source URL/path to embed."""
        self.query_one("#kb-input", Input).value = text.strip()
        self.query_one("#kb-status", Static).update("Source set — /embed to start.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "kb-save-storage":
            self._save_storage()
        elif event.button.id == "kb-start":
            self._start()
