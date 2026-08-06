"""Settings — Meraki integration, model provider, run-container config, and logs.

Mirrors web.routers.settings. Every Meraki/Ollama call blocks on the network, so it
runs on a Textual worker thread and marshals results back with ``call_from_thread``;
the UI never freezes. Knowledge base is its own screen (F3 from the workspace).
"""

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button, Footer, Header, Input, Label, RichLog, Select, Static, TabbedContent,
    TabPane,
)

from web import settings
from web.deps import forget_failed_pipelines
from web.services import (
    logs_store, meraki, ollama_admin, provider_store, run_settings_store, store,
)
from tui.command_screen import CommandScreen

_CLEANUP = [("Always", "always"), ("On success", "on_success"), ("Never", "never")]
_REASONING = [("Backend default", ""), ("On", "1"), ("Off", "0")]


class SettingsScreen(CommandScreen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent(id="settings-tabs"):
            with TabPane("Meraki", id="s-meraki"):
                yield from self._meraki_pane()
            with TabPane("Model provider", id="s-provider"):
                yield from self._provider_pane()
            with TabPane("Run containers", id="s-run"):
                yield from self._run_pane()
            with TabPane("Logs", id="s-logs"):
                yield from self._logs_pane()
        yield Static(f"[dim]Saved to {settings.CONFIG_PATH.name} · secrets in "
                     f"{settings.ENV_PATH.name} — both hand-editable at the repo root.[/dim]",
                     id="settings-files")
        yield self.command_bar()
        yield Footer()

    # --- Meraki ------------------------------------------------------------------
    def _meraki_pane(self):
        with VerticalScroll():
            yield Static("API KEY", classes="eyebrow")
            yield Input(placeholder="Meraki Dashboard API key", password=True, id="apikey")
            with Horizontal(classes="row"):
                yield Button("Save key", id="save-key", variant="primary")
                yield Button("Remove key", id="remove-key")
            yield Static("", id="meraki-status")
            yield Static("ORGANIZATIONS", classes="eyebrow")
            yield Static("", id="orgs")
            with Horizontal(classes="row"):
                yield Input(placeholder="Organization ID", id="orgid")
                yield Button("Add org", id="add-org")
            yield Static("", id="org-status")
            yield Static("DEFAULT EXAMPLE NETWORK", classes="eyebrow")
            with Horizontal(classes="row"):
                yield Select([], id="default-net", allow_blank=True)
                yield Button("Set default", id="set-default")
            yield Static("", id="default-status")

    def _refresh_meraki(self) -> None:
        uid = self.app.uid
        masked = store.masked_meraki_key()
        self.query_one("#meraki-status", Static).update(
            f"Key: {masked} [dim]· stored in .env[/dim]" if masked
            else "[dim]No API key set.[/dim]")
        lines = []
        for org in store.list_orgs(uid):
            nets = org.get("networks", [])
            lines.append(f"• {org.get('name') or org['id']} ({org['id']}) — {len(nets)} network(s)")
        self.query_one("#orgs", Static).update("\n".join(lines) or "[dim]No organizations.[/dim]")
        nets = store.verified_networks(uid)
        sel = self.query_one("#default-net", Select)
        sel.set_options([(f"{n['name']} · {n['orgName']}", n["id"]) for n in nets])
        cur = store.get_default_network_id()
        if cur and cur in [n["id"] for n in nets]:
            sel.value = cur

    @work(thread=True)
    def _save_key(self, key: str) -> None:
        try:
            identity = meraki.validate_key(key)
            store.set_meraki_key(key)
            msg = f"[green]Key saved.[/green] {identity}"
        except meraki.MerakiError as exc:
            msg = f"[red]{exc}[/red]"
        self.app.call_from_thread(self.query_one("#meraki-status", Static).update, msg)
        self.app.call_from_thread(self._refresh_meraki)

    @work(thread=True)
    def _add_org(self, org_id: str) -> None:
        uid = self.app.uid
        if store.org_exists(uid, org_id):
            msg = f"[yellow]Organization {org_id} is already connected.[/yellow]"
        else:
            try:
                key = store.get_meraki_key()
                org = meraki.verify_org(org_id, key)
                networks = meraki.list_networks(org_id, key)
                store.save_org(uid, org)
                store.set_networks_for_org(uid, org["id"], networks)
                msg = f"[green]Added {org.get('name') or org['id']}.[/green]"
            except meraki.MerakiError as exc:
                msg = f"[red]{exc}[/red]"
        self.app.call_from_thread(self.query_one("#org-status", Static).update, msg)
        self.app.call_from_thread(self._refresh_meraki)

    # --- model provider ----------------------------------------------------------
    def _provider_pane(self):
        s = provider_store.get_settings()
        with VerticalScroll():
            yield Static("OLLAMA SERVER", classes="eyebrow")
            with Horizontal(classes="row"):
                yield Input(value=s.get("ollama_url", ""), placeholder="http://localhost:11434",
                            id="ollama-url")
                yield Button("Check", id="check-provider")
            yield Static("", id="provider-check")
            yield Static("MODELS", classes="eyebrow")
            yield Input(value=s.get("chat_model", ""), placeholder="chat model (e.g. qwen3.5)",
                        id="chat-model")
            yield Input(value=s.get("embed_model", ""), placeholder="embedding model (e.g. nomic-embed-text)",
                        id="embed-model")
            with Horizontal(classes="row"):
                yield Input(value=("" if s.get("temperature") is None else str(s["temperature"])),
                            placeholder="temperature 0–2", id="temperature")
                yield Select(_REASONING,
                             value=("" if s.get("reasoning") is None else str(s["reasoning"])),
                             id="reasoning", allow_blank=False)
            yield Button("Save provider", id="save-provider", variant="primary")
            yield Static("", id="provider-status")

    @work(thread=True)
    def _check_provider(self, url: str) -> None:
        try:
            u = ollama_admin.normalize_url(url)
            version = ollama_admin.check_server(u)
            models = ollama_admin.list_models(u)
            msg = f"[green]Ollama {version}[/green] — {len(models)} model(s): {', '.join(models[:8])}"
        except ollama_admin.OllamaError as exc:
            msg = f"[red]{exc}[/red]"
        self.app.call_from_thread(self.query_one("#provider-check", Static).update, msg)

    @work(thread=True)
    def _save_provider(self, url, chat, embed, temp_raw, reasoning_raw) -> None:
        temp = None
        if temp_raw.strip():
            try:
                temp = float(temp_raw)
                if not 0.0 <= temp <= 2.0:
                    raise ValueError
            except ValueError:
                self.app.call_from_thread(self.query_one("#provider-status", Static).update,
                                          "[red]Temperature must be a number between 0 and 2.[/red]")
                return
        try:
            u = ollama_admin.normalize_url(url)
            ollama_admin.check_server(u)
        except ollama_admin.OllamaError as exc:
            self.app.call_from_thread(self.query_one("#provider-status", Static).update,
                                      f"[red]{exc}[/red]")
            return
        reason = None if not reasoning_raw else reasoning_raw == "1"
        provider_store.save_settings("ollama", u, chat, embed, temp, reasoning=reason)
        forget_failed_pipelines()
        self.app.call_from_thread(self.query_one("#provider-status", Static).update,
                                  "[green]Provider settings saved.[/green]")

    # --- run containers ----------------------------------------------------------
    def _run_pane(self):
        r = run_settings_store.get_settings()
        with VerticalScroll():
            yield Static("CONTAINER IMAGES", classes="eyebrow")
            yield Input(value=r["python_image"], id="py-image")
            yield Input(value=r["go_image"], id="go-image")
            yield Input(value=r["script_image"], id="script-image")
            yield Static("LIMITS", classes="eyebrow")
            with Horizontal(classes="row"):
                yield Input(value=str(r["timeout_seconds"]), placeholder="timeout s", id="timeout")
                yield Input(value=str(r["cpu_limit"]), placeholder="cpu", id="cpu")
                yield Input(value=str(r["memory_limit_mb"]), placeholder="mem MB", id="mem")
            yield Select(_CLEANUP, value=r["cleanup_policy"], id="cleanup", allow_blank=False)
            yield Button("Save run settings", id="save-run", variant="primary")
            yield Static("", id="run-settings-status")

    def _save_run_settings(self) -> None:
        try:
            timeout = int(self.query_one("#timeout", Input).value)
            cpu = float(self.query_one("#cpu", Input).value)
            mem = int(self.query_one("#mem", Input).value)
            if timeout <= 0 or cpu <= 0 or mem <= 0:
                raise ValueError
        except ValueError:
            self.query_one("#run-settings-status", Static).update(
                "[red]Timeout, CPU, and memory must be positive numbers.[/red]")
            return
        run_settings_store.save_settings(
            self.query_one("#py-image", Input).value,
            self.query_one("#go-image", Input).value, self.query_one("#script-image", Input).value,
            timeout, cpu, mem, self.query_one("#cleanup", Select).value)
        self.query_one("#run-settings-status", Static).update("[green]Saved.[/green]")

    # --- logs --------------------------------------------------------------------
    def _logs_pane(self):
        with VerticalScroll():
            yield Static("APP LOG", classes="eyebrow")
            yield RichLog(id="app-log", highlight=True, wrap=True, max_lines=500)

    def _load_logs(self) -> None:
        log = self.query_one("#app-log", RichLog)
        log.clear()
        for line in logs_store.app_log_lines():
            log.write(line)
        if not logs_store.app_log_lines():
            log.write("[dim]No log lines yet.[/dim]")

    # --- lifecycle + buttons -----------------------------------------------------
    def on_mount(self) -> None:
        self._refresh_meraki()
        self._load_logs()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "save-key":
            key = self.query_one("#apikey", Input).value.strip()
            if key:
                self._save_key(key)
        elif bid == "remove-key":
            store.clear_meraki_key()
            self._refresh_meraki()
        elif bid == "add-org":
            oid = self.query_one("#orgid", Input).value.strip()
            if oid:
                self._add_org(oid)
        elif bid == "set-default":
            val = self.query_one("#default-net", Select).value
            store.set_default_network_id(val if isinstance(val, str) else "")
            self.query_one("#default-status", Static).update("[green]Default network set.[/green]")
        elif bid == "check-provider":
            self._check_provider(self.query_one("#ollama-url", Input).value)
        elif bid == "save-provider":
            self._save_provider(
                self.query_one("#ollama-url", Input).value,
                self.query_one("#chat-model", Input).value,
                self.query_one("#embed-model", Input).value,
                self.query_one("#temperature", Input).value,
                self.query_one("#reasoning", Select).value or "")
        elif bid == "save-run":
            self._save_run_settings()
