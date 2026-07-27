"""The workspace, styled after the Claude Code terminal: a launch banner, a single
scrolling transcript that fills the body, and the command bar at the bottom.

There is no sidebar and there are no tabs — everything is driven by the command bar.
Plain text is a test description (it generates); slash commands navigate and act, and
their output is written into the transcript. Generation and runs stream through the
same background-job registries the web layer used (via tui.streaming.pump).
"""

import os

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.widgets import RichLog, Static

from web import config
from web.services import app_flow, generate, tests_store
from tui import generation, run_flow
from tui.command_screen import CommandScreen
from tui.commands import Command
from tui.streaming import pump

_STATUS_GLYPH = {"done": "✓", "generating": "◴", "error": "✕"}
_HW_TYPES = {"type:wireless": "Any wireless AP",
             "type:security_appliance": "Any security appliance",
             "type:camera": "Any camera"}


class WorkspaceScreen(CommandScreen):
    def __init__(self):
        super().__init__()
        self.current_test_id = None
        self._prompt = ""
        self._code_buffer = ""
        self._vm = {}
        self._version_no = 0
        self._version_count = 0
        self._readonly = False
        self._busy = False
        self._language = "py"

    # --- layout ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(self._banner(), id="banner")
        yield RichLog(id="transcript", highlight=True, markup=True, wrap=True)
        yield self.command_bar()

    def _banner(self) -> str:
        return (f"[b]✳ {config.WORDMARK}[/b]\n"
                f"[dim]local test generation · grounded in your Meraki API spec[/dim]\n"
                f"[dim]{os.getcwd()}[/dim]")

    def on_mount(self) -> None:
        self._say("[dim]Describe a test to generate it, or /help for commands.[/dim]")

    # --- transcript helpers ------------------------------------------------------
    def _log(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    def _echo(self, text: str) -> None:
        """Echo what the user typed, Claude-Code style."""
        self._log().write(f"[dim]>[/dim] {text}")

    def _say(self, markup: str) -> None:
        self._log().write(markup)

    def _bullet(self, markup: str) -> None:
        self._log().write(f"[b]●[/b] {markup}")

    # --- plain text: describe a test → generate ----------------------------------
    def on_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._echo(text)
        self._start_generation(text, regen=False)

    # --- commands ----------------------------------------------------------------
    def on_command(self, cmd: Command) -> bool:
        name = cmd.name
        if name == "generate":
            self._echo("/generate")
            if self._prompt:
                self._start_generation(self._prompt, regen=True)
            else:
                self._say("[yellow]Describe what you want to test first.[/yellow]")
        elif name == "run":
            self._echo("/run"); self._run()
        elif name == "repair":
            self._echo("/repair"); self._repair()
        elif name == "export":
            self._export()
        elif name == "open":
            self._open_by_ref(cmd.args)
        elif name == "version":
            self._version(cmd.args)
        elif name == "save":
            self._say("[dim]Nothing to save here — /code shows the current test.[/dim]")
        else:
            return False
        return True

    # --- navigation views (called by App.goto) -----------------------------------
    def goto_view(self, target: str, arg: str = "") -> None:
        if target == "new":
            self._new_test()
        elif target == "tests":
            self._list_tests()
        elif target == "code":
            self._show_code()
        elif target == "config":
            self._show_config()
        elif target == "output":
            self._show_output()
        elif target == "prompt":
            self._say("[dim]Type a test description below, then Enter.[/dim]")
        self.query_one("#command").focus()

    def _new_test(self) -> None:
        self.current_test_id = None
        self._prompt = ""
        self._code_buffer = ""
        self._vm = {}
        self._version_count = 0
        self._readonly = False
        self._log().clear()
        self._say("[dim]New test — describe what you want to test.[/dim]")

    def _list_tests(self) -> None:
        tests = tests_store.list_tests(self.app.uid)
        if not tests:
            self._say("[dim]No tests yet. Describe one to generate.[/dim]")
            return
        self._bullet(f"{len(tests)} test(s):")
        for i, t in enumerate(tests, start=1):
            glyph = _STATUS_GLYPH.get(t["status"], "·")
            self._say(f"  [dim]{i}.[/dim] {glyph} {t['name']}")
        self._say("[dim]/open <number or name> to load one.[/dim]")

    # --- loading a test ----------------------------------------------------------
    def _load_vm(self, test_id: int, version_no: int = None) -> bool:
        uid = self.app.uid
        test = tests_store.get_test(uid, test_id)
        if not test:
            return False
        self.current_test_id = test_id
        self._prompt = test.get("prompt", "") or self._prompt
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
        self._vm = vm
        self._code_buffer = vm.get("code", "")
        self._language = vm.get("language", "py")
        return True

    def load_test(self, test_id: int, version_no: int = None, show_code: bool = True) -> None:
        if not self._load_vm(test_id, version_no):
            return
        vm = self._vm
        vlabel = (f"v{self._version_no + 1} of {self._version_count}"
                  if self._version_count else "unsaved")
        ro = " · read-only" if self._readonly else ""
        eps = ", ".join(vm.get("endpoints", []) or []) or "(none)"
        self._bullet(f"[b]{vm.get('name') or 'test'}[/b]  [dim]· {vlabel}{ro}[/dim]")
        self._say(f"[dim]{vm.get('file_name', '')} · {vm.get('line_count', 0)} lines "
                  f"· grounded in: {eps}[/dim]")
        if show_code and self._code_buffer:
            self._show_code()

    # --- views over the loaded test ----------------------------------------------
    def _need_test(self) -> bool:
        if self.current_test_id is None:
            self._say("[yellow]No test loaded. Describe one, or /open a saved test.[/yellow]")
            return False
        return True

    def _show_code(self) -> None:
        if not self._need_test():
            return
        code = self._code_buffer or "[dim](no code yet)[/dim]"
        self._say(f"[dim]── {self._vm.get('file_name', 'code')} ─────────────[/dim]")
        for line in code.splitlines() or [""]:
            self._log().write(Text("  " + line, style="grey70"))

    def _show_config(self) -> None:
        if not self._need_test():
            return
        vm = self._vm
        source = "build from scratch (agent)" if vm.get("run_source") == "scratch" else "clone an example network"
        net = vm.get("source_network_id") or vm.get("default_network_id") or "(default)"
        pinned = [r.get("serial") for r in vm.get("hardware", []) if r.get("serial")]
        types = [_HW_TYPES.get(f"type:{r.get('type')}", r.get("type"))
                 for r in vm.get("hardware", []) if not r.get("serial")]
        hw = ", ".join(pinned + types) or "(any claimable)"
        self._bullet("Run config")
        self._say(f"  [dim]source:[/dim] {source}")
        self._say(f"  [dim]network:[/dim] {net}")
        self._say(f"  [dim]hardware:[/dim] {hw}")

    def _show_output(self) -> None:
        if not self._need_test():
            return
        vm = self._vm
        run = vm.get("run")
        if not run:
            self._say("[dim]No runs yet. /run to start.[/dim]")
            return
        self._bullet(f"Last run — status: {run['status']}"
                     + (f" — {run['error_message']}" if run.get("error_message") else ""))
        for e in vm.get("logs", []):
            self._say(f"  [dim]\\[{e['stage']}][/dim] {e['message']}")

    def _open_by_ref(self, ref: str) -> None:
        tests = tests_store.list_tests(self.app.uid)
        if not ref:
            self._say("[yellow]Usage: /open <number or name>[/yellow]")
            return
        chosen = None
        if ref.isdigit():
            i = int(ref) - 1
            if 0 <= i < len(tests):
                chosen = tests[i]
        else:
            low = ref.lower()
            chosen = next((t for t in tests if low in t["name"].lower()), None)
        if chosen:
            self._echo(f"/open {ref}")
            self.load_test(chosen["id"])
        else:
            self._say(f"[yellow]No test matches “{ref}”.[/yellow]")

    def _version(self, arg: str) -> None:
        if self.current_test_id is None:
            self._say("[yellow]No test loaded.[/yellow]")
            return
        arg = arg.lower()
        if arg in ("next", "fwd", "forward") and self._version_no < self._version_count - 1:
            self.load_test(self.current_test_id, self._version_no + 1)
        elif arg in ("prev", "back", "previous") and self._version_no > 0:
            self.load_test(self.current_test_id, self._version_no - 1)
        else:
            self._say("[dim]Usage: /version next | prev[/dim]")

    # --- generation --------------------------------------------------------------
    def _start_generation(self, prompt: str, regen: bool) -> None:
        if self._busy:
            self._say("[yellow]A generation is already running…[/yellow]")
            return
        self._prompt = prompt
        regen_of = self.current_test_id if (regen and self.current_test_id and not self._readonly) else None
        self._busy = True
        self._code_buffer = ""
        self._say(f"[dim]Generating ({self._language})…[/dim]")
        job, test_id, name = generation.start_generate(
            self.app.uid, prompt, self._language, None, regen_of)
        self.current_test_id = test_id
        self._subscribe(job, self._on_gen_event)

    def _on_gen_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "stage":
            label = ev.get("label")
            if label:
                self._say(f"[dim]· {label}[/dim]")
        elif kind == "token":
            self._code_buffer += ev.get("text", "")
        elif kind == "error":
            self._say(f"[red]{ev.get('message', 'Error')}[/red]")
        elif kind == "done":
            self._busy = False
            if ev.get("status") == "done":
                self.load_test(ev["test_id"])
                self._say("[dim]/run to run it · /config to review the run setup[/dim]")
            else:
                self._say("[red]Generation failed.[/red]")

    # --- run + repair ------------------------------------------------------------
    def _run(self) -> None:
        if not self._need_test():
            return
        source = "scratch" if self._vm.get("run_source") == "scratch" else "example"
        net = self._vm.get("source_network_id") or ""
        run, error, job = run_flow.start_run(
            self.app.uid, self.current_test_id, source, net,
            version_no=self._version_no if self._readonly else None)
        if error:
            self._say(f"[red]{error}[/red]")
            return
        if job is None:
            self._say(f"[yellow]{run['error_message'] if run else 'Could not start run.'}[/yellow]")
            return
        self._say("[dim]Running…[/dim]")
        self._subscribe(job, self._on_run_event)

    def _on_run_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "log":
            self._say(f"  [dim]\\[{ev.get('stage', '')}][/dim] {ev.get('message', '')}")
        elif kind == "status":
            err = ev.get("error")
            self._say(f"[dim]status: {ev.get('status')}"
                      + (f" — {err}" if err else "") + "[/dim]")
        elif kind == "done":
            if self.current_test_id:
                self._load_vm(self.current_test_id,
                              self._version_no if self._readonly else None)
            self._bullet("Run finished.")

    def _repair(self) -> None:
        if not self._need_test() or self._busy:
            return
        job, error = generation.start_repair(self.app.uid, self.current_test_id)
        if error:
            self._say(f"[red]{error}[/red]")
            return
        self._busy = True
        self._code_buffer = ""
        self._say("[dim]Repairing…[/dim]")
        self._subscribe(job, self._on_gen_event)

    # --- export ------------------------------------------------------------------
    def _export(self) -> None:
        if not self._need_test():
            return
        test = tests_store.get_test(self.app.uid, self.current_test_id)
        if test:
            self.app.copy_to_clipboard(test.get("code", ""))
            self._say("[dim]Code copied to clipboard.[/dim]")

    # --- streaming ---------------------------------------------------------------
    @work(thread=True, exclusive=False)
    def _subscribe(self, job, on_event) -> None:
        pump(self.app, job, on_event)
