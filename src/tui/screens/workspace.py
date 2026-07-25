"""The core loop: library + composer + tabbed workspace (Prompt / Code / Config / Output).

Mirrors the web product app (app_view.py / app.html). Generation and runs stream through
the same background-job registries the web layer used; this screen subscribes to a job on
a worker thread (tui.streaming.pump) and renders the events.
"""

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button, Footer, Header, Label, ListItem, ListView, RadioButton, RadioSet,
    RichLog, Select, SelectionList, Static, TabbedContent, TabPane, TextArea,
)

from web.services import app_flow, generate, store, tests_store
from tui import generation, run_flow
from tui.streaming import pump

_LANGUAGES = [
    ("Python / pytest", "py"), ("TypeScript / Jest", "ts"), ("Java / JUnit5", "java"),
    ("Go", "go"), ("C# / xUnit", "csharp"),
]
_TA_LANG = {"py": "python", "ts": "javascript", "java": "java", "go": "go", "csharp": "csharp"}
_HW_TYPES = [("Any wireless AP", "type:wireless"),
             ("Any security appliance", "type:security_appliance"),
             ("Any camera", "type:camera")]
_STATUS_GLYPH = {"done": "✓", "generating": "◴", "error": "✕"}


class WorkspaceScreen(Screen):
    BINDINGS = [
        ("ctrl+n", "new_test", "New"),
        ("ctrl+g", "generate", "Generate"),
        ("ctrl+r", "run", "Run"),
        ("ctrl+f", "repair", "Repair"),
        ("ctrl+e", "export", "Export"),
        ("ctrl+s", "save_code", "Save code"),
        ("f2", "open_settings", "Settings"),
        ("f3", "open_kb", "Knowledge"),
        ("f4", "open_coverage", "Coverage"),
        ("f5", "open_runs", "Runs"),
    ]

    def __init__(self):
        super().__init__()
        self.current_test_id = None
        self._code_buffer = ""
        self._version_no = 0
        self._version_count = 0
        self._readonly = False
        self._busy = False

    # --- layout ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with VerticalScroll(id="sidebar"):
                yield Static("LIBRARY", classes="eyebrow")
                yield Button("+ New test", id="new", variant="default")
                yield ListView(id="library")
            with Vertical(id="main"):
                yield Static("What do you want to test?", id="topbar")
                with TabbedContent(id="tabs"):
                    with TabPane("Prompt", id="tab-prompt"):
                        yield TextArea(id="composer")
                        with Horizontal(id="composer-bar"):
                            yield Select(_LANGUAGES, value="py", id="language", allow_blank=False)
                            yield Button("Generate", id="generate", variant="primary")
                        yield Static("", id="status")
                    with TabPane("Code", id="tab-code"):
                        yield TextArea("", id="code", language="python", read_only=True)
                        yield Static("", id="code-info")
                    with TabPane("Test config", id="tab-config"):
                        yield Static("Build the run network by", classes="eyebrow")
                        with RadioSet(id="run-source"):
                            yield RadioButton("Clone an example network", value=True, id="src-example")
                            yield RadioButton("Build from scratch (agent)", id="src-scratch")
                        yield Static("Example network", classes="eyebrow")
                        yield Select([], id="network", allow_blank=True)
                        yield Static("Hardware the run will claim", classes="eyebrow")
                        yield SelectionList(id="hardware")
                        with Horizontal(id="config-bar"):
                            yield Button("Save config", id="save-config")
                            yield Button("Run", id="run", variant="primary")
                        yield Static("", id="config-status")
                    with TabPane("Output", id="tab-output"):
                        yield RichLog(id="output", highlight=True, markup=True, wrap=True)
                        with Horizontal(id="output-bar"):
                            yield Button("Run", id="run2", variant="primary")
                            yield Button("Repair", id="repair")
                        yield Static("", id="run-status")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_library()

    # --- library -----------------------------------------------------------------
    def refresh_library(self) -> None:
        lv = self.query_one("#library", ListView)
        lv.clear()
        for t in tests_store.list_tests(self.app.uid):
            glyph = _STATUS_GLYPH.get(t["status"], "·")
            lv.append(ListItem(Label(f"{glyph} {t['name']}"), name=str(t["id"])))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is not None and event.item.name:
            self.load_test(int(event.item.name))

    # --- loading a test ----------------------------------------------------------
    def load_test(self, test_id: int, version_no: int = None) -> None:
        uid = self.app.uid
        test = tests_store.get_test(uid, test_id)
        if not test:
            return
        self.current_test_id = test_id
        versions = tests_store.list_versions(uid, test_id)
        self._version_count = len(versions)
        if version_no is None or version_no >= self._version_count - 1:
            vm = generate.view_model_from_test(test)
            self._version_no = max(self._version_count - 1, 0)
            self._readonly = False
        else:
            version = tests_store.get_version(uid, test_id, version_no)
            vm = generate.view_model_from_version(version, test_id, test["name"], self._version_count)
            self._version_no = version_no
            self._readonly = True
        vm.update(app_flow.run_context(uid, test, self._version_no if self._readonly else None))
        self._render_vm(vm, test)

    def _render_vm(self, vm: dict, test: dict) -> None:
        self.query_one("#composer", TextArea).load_text(vm.get("prompt", ""))
        lang = vm.get("language", "py")
        try:
            self.query_one("#language", Select).value = lang
        except Exception:
            pass
        self._code_buffer = vm.get("code", "")
        code = self.query_one("#code", TextArea)
        code.language = _TA_LANG.get(lang, "python")
        code.read_only = True
        code.load_text(self._code_buffer)
        code.read_only = self._readonly
        ro = " · read-only (older version)" if self._readonly else ""
        vlabel = f"v{self._version_no + 1} of {self._version_count}" if self._version_count else "unsaved"
        self.query_one("#topbar", Static).update(
            f"{test['name']}  ·  {vlabel}{ro}  ·  ← / → versions")
        eps = ", ".join(vm.get("endpoints", []) or []) or "(none)"
        self.query_one("#code-info", Static).update(
            f"{vm.get('file_name', '')} · {vm.get('line_count', 0)} lines · grounded in: {eps}")
        self._render_config(vm)
        self._render_output(vm)

    def _render_config(self, vm: dict) -> None:
        src = self.query_one("#run-source", RadioSet)
        src.query_one("#src-scratch", RadioButton).value = vm.get("run_source") == "scratch"
        src.query_one("#src-example", RadioButton).value = vm.get("run_source") != "scratch"
        net = self.query_one("#network", Select)
        opts = [(n.get("name") or n["id"], n["id"]) for n in vm.get("networks", [])]
        net.set_options(opts)
        chosen = vm.get("source_network_id") or vm.get("default_network_id") or ""
        if chosen and chosen in [o[1] for o in opts]:
            net.value = chosen
        hw = self.query_one("#hardware", SelectionList)
        hw.clear_options()
        pinned = {r.get("serial") for r in vm.get("hardware", []) if r.get("serial")}
        types_needed = [r.get("type") for r in vm.get("hardware", []) if not r.get("serial")]
        for d in vm.get("claimable", []):
            hw.add_option((d["label"], f"serial:{d['serial']}", d["serial"] in pinned))
        for label, value in _HW_TYPES:
            initial = value.split(":", 1)[1] in types_needed
            hw.add_option((label, value, initial))
        err = vm.get("claimable_error")
        self.query_one("#config-status", Static).update(err or "")

    def _render_output(self, vm: dict) -> None:
        log = self.query_one("#output", RichLog)
        log.clear()
        run = vm.get("run")
        if not run:
            log.write("[dim]No runs yet. Configure hardware, then Run.[/dim]")
            self.query_one("#run-status", Static).update("")
            return
        for e in vm.get("logs", []):
            log.write(f"[dim]\\[{e['stage']}][/dim] {e['message']}")
        self.query_one("#run-status", Static).update(
            f"status: {run['status']}" + (f" — {run['error_message']}" if run.get("error_message") else ""))

    # --- generation --------------------------------------------------------------
    def action_new_test(self) -> None:
        self.current_test_id = None
        self._code_buffer = ""
        self._version_count = 0
        self._readonly = False
        self.query_one("#composer", TextArea).load_text("")
        self.query_one("#code", TextArea).load_text("")
        self.query_one("#topbar", Static).update("What do you want to test?")
        self.query_one("#status", Static).update("")
        self.query_one("#tabs", TabbedContent).active = "tab-prompt"
        self.query_one("#composer", TextArea).focus()

    def action_generate(self) -> None:
        if self._busy:
            self.query_one("#status", Static).update("A generation is already running…")
            return
        prompt = self.query_one("#composer", TextArea).text.strip()
        if not prompt:
            self.query_one("#status", Static).update("Type what you want to test first.")
            return
        language = self.query_one("#language", Select).value or "py"
        hardware_rows = list(self.query_one("#hardware", SelectionList).selected)
        regen_of = self.current_test_id if (self.current_test_id and not self._readonly) else None

        self._busy = True
        self._code_buffer = ""
        self.query_one("#code", TextArea).load_text("")
        self.query_one("#tabs", TabbedContent).active = "tab-code"
        self.query_one("#status", Static).update("Starting…")

        job, test_id, name = generation.start_generate(
            self.app.uid, prompt, language, hardware_rows, regen_of)
        self.current_test_id = test_id
        self.refresh_library()
        self._subscribe(job, self._on_gen_event)

    def _on_gen_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "stage":
            self.query_one("#status", Static).update(ev.get("label", ""))
        elif kind == "token":
            self._code_buffer += ev.get("text", "")
            self.query_one("#code", TextArea).load_text(self._code_buffer)
        elif kind == "error":
            self.query_one("#status", Static).update(f"[red]{ev.get('message', 'Error')}[/red]")
        elif kind == "done":
            self._busy = False
            ok = ev.get("status") == "done"
            self.query_one("#status", Static).update("Done." if ok else "[red]Generation failed.[/red]")
            self.refresh_library()
            if ok:
                self.load_test(ev["test_id"])

    # --- code editing ------------------------------------------------------------
    def action_save_code(self) -> None:
        if self.current_test_id is None or self._readonly:
            return
        code = self.query_one("#code", TextArea).text
        if tests_store.update_code(self.app.uid, self.current_test_id, code):
            self.query_one("#code-info", Static).update("[green]Saved.[/green]")

    # --- version navigation ------------------------------------------------------
    def key_left(self) -> None:
        if self.current_test_id and self._version_no > 0:
            self.load_test(self.current_test_id, self._version_no - 1)

    def key_right(self) -> None:
        if self.current_test_id and self._version_no < self._version_count - 1:
            self.load_test(self.current_test_id, self._version_no + 1)

    # --- config + run ------------------------------------------------------------
    def action_save_config(self) -> None:
        if self.current_test_id is None:
            return
        rows = list(self.query_one("#hardware", SelectionList).selected)
        hardware = app_flow.parse_hardware_rows(self.app.uid, rows)
        tests_store.update_hardware(self.app.uid, self.current_test_id, hardware)
        source = "scratch" if self.query_one("#src-scratch", RadioButton).value else "example"
        net = self.query_one("#network", Select).value
        tests_store.set_run_config(self.app.uid, self.current_test_id, source,
                                   net if isinstance(net, str) else "")
        self.query_one("#config-status", Static).update("[green]Saved.[/green]")

    def action_run(self) -> None:
        if self.current_test_id is None:
            return
        self.action_save_config()
        source = "scratch" if self.query_one("#src-scratch", RadioButton).value else "example"
        net = self.query_one("#network", Select).value
        run, error, job = run_flow.start_run(
            self.app.uid, self.current_test_id, source,
            net if isinstance(net, str) else "",
            version_no=self._version_no if self._readonly else None)
        self.query_one("#tabs", TabbedContent).active = "tab-output"
        log = self.query_one("#output", RichLog)
        log.clear()
        if error:
            self.query_one("#run-status", Static).update(f"[red]{error}[/red]")
            return
        if job is None:
            self.query_one("#run-status", Static).update(
                f"[yellow]{run['error_message'] if run else 'Could not start run.'}[/yellow]")
            return
        self.query_one("#run-status", Static).update("Running…")
        self._subscribe(job, self._on_run_event)

    def _on_run_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "log":
            self.query_one("#output", RichLog).write(
                f"[dim]\\[{ev.get('stage', '')}][/dim] {ev.get('message', '')}")
        elif kind == "status":
            err = ev.get("error")
            self.query_one("#run-status", Static).update(
                f"status: {ev.get('status')}" + (f" — {err}" if err else ""))
        elif kind == "done":
            if self.current_test_id:
                self.load_test(self.current_test_id,
                               self._version_no if self._readonly else None)

    def action_repair(self) -> None:
        if self.current_test_id is None or self._busy:
            return
        job, error = generation.start_repair(self.app.uid, self.current_test_id)
        if error:
            self.query_one("#run-status", Static).update(f"[red]{error}[/red]")
            return
        self._busy = True
        self._code_buffer = ""
        self.query_one("#code", TextArea).load_text("")
        self.query_one("#tabs", TabbedContent).active = "tab-code"
        self.query_one("#status", Static).update("Repairing…")
        self.refresh_library()
        self._subscribe(job, self._on_gen_event)

    # --- export ------------------------------------------------------------------
    def action_export(self) -> None:
        if self.current_test_id is None:
            return
        test = tests_store.get_test(self.app.uid, self.current_test_id)
        if not test:
            return
        self.app.copy_to_clipboard(test.get("code", ""))
        self.query_one("#code-info", Static).update("[green]Code copied to clipboard.[/green]")

    # --- screen navigation -------------------------------------------------------
    def action_open_settings(self) -> None:
        from tui.screens.settings import SettingsScreen
        self.app.push_screen(SettingsScreen())

    def action_open_kb(self) -> None:
        from tui.screens.knowledgebase import KnowledgeBaseScreen
        self.app.push_screen(KnowledgeBaseScreen())

    def action_open_coverage(self) -> None:
        from tui.screens.coverage import CoverageScreen
        self.app.push_screen(CoverageScreen())

    def action_open_runs(self) -> None:
        from tui.screens.runs import RunsScreen
        self.app.push_screen(RunsScreen())

    # --- buttons -----------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "new": self.action_new_test, "generate": self.action_generate,
            "save-config": self.action_save_config, "run": self.action_run,
            "run2": self.action_run, "repair": self.action_repair,
        }
        handler = actions.get(event.button.id)
        if handler:
            handler()

    # --- streaming ---------------------------------------------------------------
    @work(thread=True, exclusive=False)
    def _subscribe(self, job, on_event) -> None:
        pump(self.app, job, on_event)
