"""Knowledge base: a list of versions, driven entirely from the command bar.

The screen shows what exists and nothing else — versions, and where their vectors
live. Everything that *changes* something is a command, so there is no form to fill
in and no field left holding a half-typed URL:

    /kb                                     this list
    /kb --new <url|path> --split custom     embed a new version
    /kb --storage <chroma-url> | local      where vectors are kept
    /activate <n> · /delete <n>             by row number, or the highlighted row

Ingestion runs in a daemon thread via ``kb_registry``; this screen subscribes to the
job for live stage updates.
"""

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from web.services import kb_ingest, kb_registry, kb_store, provider_store, url_fetch
from tui.command_screen import CommandScreen
from tui.commands import Command, KbRequest, parse_kb
from tui.markup import esc
from tui.streaming import pump

# status -> (glyph, colour). Colour is the only decoration, btop-style: the row reads
# at a glance and nothing competes with it.
_STATUS = {"done": ("●", "green"), "embedding": ("◴", "yellow"), "error": ("✕", "red")}
_SPLIT_LABEL = {"custom": "OpenAPI", "langchain": "LangChain"}


class KnowledgeBaseScreen(CommandScreen):
    PLACE = "kb"
    PLACE_LABEL = "knowledge base"
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, args: str = ""):
        super().__init__(args)
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="kb-main"):
            table = DataTable(id="kb-versions", cursor_type="row", zebra_stripes=False)
            table.border_title = "knowledge bases"
            yield table
            storage = Static("", id="kb-storage")
            storage.border_title = "vector store"
            yield storage
            yield Static("", id="kb-status")
        yield self.command_bar()
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#kb-versions", DataTable).add_columns(
            " ", "#", "name", "docs", "split", "source")
        self.refresh_view()
        if self.args:                      # arrived as "/kb --new …" from another screen
            self.run_args(self.args)

    # --- rendering ---------------------------------------------------------------
    def refresh_view(self) -> None:
        self._refresh_versions()
        self._refresh_storage()

    def _refresh_versions(self) -> None:
        table = self.query_one("#kb-versions", DataTable)
        row = table.cursor_row
        table.clear()
        versions = kb_store.list_versions()
        for i, v in enumerate(versions, start=1):
            glyph, colour = _STATUS.get(v["status"], ("·", "white"))
            active = v.get("is_active")
            table.add_row(
                f"[{colour}]{glyph}[/]",
                f"[b]{i}[/b]" if active else f"[dim]{i}[/dim]",
                f"[b]{esc(v['name'])}[/b] ★" if active else esc(v["name"]),
                f"{v['doc_count']:,}" if v["doc_count"] else "[dim]—[/dim]",
                f"[dim]{_SPLIT_LABEL.get(v['split_method'], v['split_method'])}[/dim]",
                f"[dim]{esc(v['error_message'] or v['source_label'])}[/dim]",
            )
        if versions:
            table.move_cursor(row=min(row, len(versions) - 1))
        else:
            self._status("No knowledge bases yet. [b]/kb --new <url|path> --split custom[/b] "
                         "to build one.")

    def _refresh_storage(self) -> None:
        st = kb_store.get_storage()
        if st["storage_kind"] == "remote" and st["storage_url"]:
            body = f"[b]remote[/b]  {esc(st['storage_url'])}"
        else:
            from rag.provider import ProviderConfig
            body = f"[b]local[/b]  [dim]{esc(ProviderConfig().persist_dir)}[/dim]"
        self.query_one("#kb-storage", Static).update(body)

    def _status(self, markup: str) -> None:
        self.query_one("#kb-status", Static).update(markup)

    # --- /kb arguments -----------------------------------------------------------
    def run_args(self, args: str) -> None:
        """Act on the text after ``/kb``. Called on arrival and on every later ``/kb``."""
        req = parse_kb(args)
        if req.error:
            self._status(f"[red]{esc(req.error)}[/red]")
        elif req.action == "new":
            self._start_new(req)
        elif req.action == "storage":
            self._set_storage(req.storage)
        else:
            self.refresh_view()
            self._status("")

    def _set_storage(self, target: str) -> None:
        if target.lower() == "local":
            kb_store.set_storage("local", "")
            self._status("[green]Vectors are kept inside the app.[/green]")
        else:
            url = target if target.startswith(("http://", "https://")) else "http://" + target
            kb_store.set_storage("remote", url)
            self._status(f"[green]New vectors go to {esc(url)}.[/green] "
                         "[dim]Existing versions stay where they were embedded.[/dim]")
        self._refresh_storage()

    # --- ingest ------------------------------------------------------------------
    def _start_new(self, req: KbRequest) -> None:
        if self._busy:
            self._status("[yellow]An embedding is already running…[/yellow]")
            return
        self._busy = True
        self._status(f"Fetching [b]{esc(req.source)}[/b]…")
        self._fetch_and_ingest(req)

    @work(thread=True)
    def _fetch_and_ingest(self, req: KbRequest) -> None:
        """Read the source off the network or the filesystem, then hand it to the worker.

        Which one it is comes from the value itself rather than a flag: an http(s)
        scheme is unambiguous, and anything else is a path."""
        is_url = req.source.startswith(("http://", "https://"))
        try:
            if is_url:
                content = url_fetch.fetch_spec_url(req.source)
                label = req.source
            else:
                path = Path(req.source).expanduser()
                content = path.read_bytes()
                label = path.name
        except (url_fetch.FetchError, OSError) as exc:
            self._busy = False
            self.app.call_from_thread(self._status, f"[red]{esc(exc)}[/red]")
            return
        self.app.call_from_thread(self._begin_ingest, req, content,
                                  "url" if is_url else "file", label)

    def _begin_ingest(self, req: KbRequest, content: bytes, kind: str, label: str) -> None:
        v = kb_store.create_embedding(req.name or label, kind, req.split, label)
        prov = provider_store.overrides()
        job = kb_registry.start(v["id"], lambda job: kb_ingest.run_ingest(
            job, v["id"], content, req.split, label, prov))
        self._refresh_versions()
        self._status(f"Embedding [b]{esc(v['name'])}[/b]…")
        self._subscribe(job)

    @work(thread=True)
    def _subscribe(self, job) -> None:
        pump(self.app, job, self._on_kb_event)

    def _on_kb_event(self, ev: dict) -> None:
        if ev.get("type") == "stage":
            self._status(f"Embedding… [dim]({esc(ev.get('stage', ''))})[/dim]")
        elif ev.get("type") == "done":
            self._busy = False
            ok = ev.get("status") == "done"
            self._status(f"[green]Embedded {ev.get('doc_count', 0):,} documents.[/green] "
                         "[dim]/activate <n> to generate against it.[/dim]" if ok
                         else f"[red]{esc(ev.get('message', 'Ingest failed.'))}[/red]")
            self._refresh_versions()

    # --- activate / delete -------------------------------------------------------
    def _version_id(self, ref: str = ""):
        """The version an ``/activate``/``/delete`` refers to — by 1-based number, else
        the highlighted row."""
        versions = kb_store.list_versions()
        if ref.strip().isdigit():
            i = int(ref) - 1
            return versions[i]["id"] if 0 <= i < len(versions) else None
        row = self.query_one("#kb-versions", DataTable).cursor_row
        return versions[row]["id"] if 0 <= row < len(versions) else None

    def action_activate(self, ref: str = "") -> None:
        vid = self._version_id(ref)
        if vid is None:
            self._status("[yellow]No version selected — /activate <number>.[/yellow]")
            return
        if kb_store.set_active(vid):
            self._status("[green]Activated — generation now retrieves against it.[/green]")
        else:
            self._status("[red]Can't activate that one (still embedding, or it errored).[/red]")
        self._refresh_versions()

    def action_delete(self, ref: str = "") -> None:
        vid = self._version_id(ref)
        if vid is None:
            self._status("[yellow]No version selected — /delete <number>.[/yellow]")
            return
        if kb_store.delete_version(vid):
            self._status("[green]Deleted.[/green]")
        else:
            self._status("[yellow]Only errored versions can be deleted — a finished one "
                         "owns embedded vectors.[/yellow]")
        self._refresh_versions()

    # --- command routing ---------------------------------------------------------
    def on_command(self, cmd: Command) -> bool:
        if cmd.name == "activate":
            self.action_activate(cmd.args)
        elif cmd.name == "delete":
            self.action_delete(cmd.args)
        else:
            return False
        return True

    def on_text(self, text: str) -> None:
        self._status("[dim]Knowledge bases are managed with commands — "
                     "[b]/kb --new <url|path> --split custom[/b].[/dim]")
