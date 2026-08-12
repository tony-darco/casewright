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
from tui.commands import Command, parse_edit
from tui.markup import esc
from tui.screens.editor import EditorScreen
from tui.streaming import pump

_STATUS_GLYPH = {"done": "✓", "generating": "◴", "error": "✕"}
_RUN_GLYPH = {"success": "[green]✓[/green]", "failed": "[red]✕[/red]",
              "error": "[red]![/red]", "running": "[yellow]◴[/yellow]"}
_OPEN_RUNS = 3        # recent runs summarised when a test is opened
_OPEN_CODE_LINES = 12  # head of the code shown there; /code prints the whole file
_OPEN_PROMPT_CHARS = 160   # prompts run to paragraphs; /prompt shows the rest
_HW_TYPES = {"type:wireless": "Any wireless AP",
             "type:security_appliance": "Any security appliance",
             "type:camera": "Any camera"}


class WorkspaceScreen(CommandScreen):
    PLACE = "home"

    @property
    def place(self) -> str:
        """'home' until a test is loaded — that's what decides whether /run, /edit and
        the other test commands are offered at all."""
        return "test" if self.current_test_id is not None else "home"

    def place_label(self) -> str:
        if self.current_test_id is None:
            return "home"
        name = self._test_name() or f"test {self.current_test_id}"
        vlabel = (f" · v{self._version_no + 1}/{self._version_count}"
                  if self._version_count > 1 else "")
        return f"test · {name[:38]}{vlabel}" + (" · read-only" if self._readonly else "")

    def __init__(self, args: str = ""):
        super().__init__(args)
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

    def _dismiss_banner(self) -> None:
        """The banner is an arrival card — wordmark, tagline, cwd. Once you've typed
        anything you know where you are, and it's just taking a fifth of the screen."""
        banner = self.query("#banner")
        if banner and banner.first().display:
            banner.first().display = False

    # --- transcript helpers ------------------------------------------------------
    def _log(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    def _echo(self, text: str) -> None:
        """Echo what the user typed, Claude-Code style."""
        self._dismiss_banner()
        self._log().write(f"[dim]>[/dim] {esc(text)}")

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
        elif name == "edit":
            self._echo(f"/edit {cmd.args}".rstrip()); self._edit(cmd.args)
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
        self._dismiss_banner()
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
            self._show_prompt()
        self.query_one("#command").focus()

    def _new_test(self) -> None:
        self.current_test_id = None
        self._prompt = ""
        self._code_buffer = ""
        self._vm = {}
        self._version_count = 0
        self._readonly = False
        self._log().clear()
        self.refresh_place()
        self._say("[dim]New test — describe what you want to test.[/dim]")

    def _list_tests(self) -> None:
        tests = tests_store.list_tests()
        if not tests:
            self._say("[dim]No tests yet. Describe one to generate.[/dim]")
            return
        self._bullet(f"{len(tests)} test(s):")
        for i, t in enumerate(tests, start=1):
            glyph = _STATUS_GLYPH.get(t["status"], "·")
            self._say(f"  [dim]{i}.[/dim] {glyph} {esc(t['name'])}")
        self._say("[dim]/open <number or name> to load one.[/dim]")

    # --- loading a test ----------------------------------------------------------
    def _load_vm(self, test_id: int, version_no: int = None) -> bool:
        test = tests_store.get_test(test_id)
        if not test:
            return False
        self.current_test_id = test_id
        self._prompt = test.get("prompt", "") or self._prompt
        versions = tests_store.list_versions(test_id)
        self._version_count = len(versions)
        if version_no is None or version_no >= self._version_count - 1:
            vm = generate.view_model_from_test(test)
            self._version_no = max(self._version_count - 1, 0)
            self._readonly = False
        else:
            version = tests_store.get_version(test_id, version_no)
            vm = generate.view_model_from_version(version, test_id, test["name"], self._version_count)
            self._version_no = version_no
            self._readonly = True
        vm.update(app_flow.run_context(test, self._version_no if self._readonly else None))
        self._vm = vm
        self._code_buffer = vm.get("code", "")
        self._language = vm.get("language", "py")
        return True

    def load_test(self, test_id: int, version_no: int = None, show_code: bool = True) -> None:
        """Summarise a test: what it was asked for, how it last ran, and the top of the
        code. The whole file is a /code away — printing 167 lines on every open buries
        the two things you actually came to check."""
        if not self._load_vm(test_id, version_no):
            return
        vm = self._vm
        vlabel = (f"v{self._version_no + 1} of {self._version_count}"
                  if self._version_count else "unsaved")
        ro = " · read-only" if self._readonly else ""
        eps = ", ".join(vm.get("endpoints", []) or []) or "(none)"
        self._bullet(f"[b]{esc(self._test_name() or 'test')}[/b]  [dim]· {vlabel}{ro}[/dim]")
        self._say(f"[dim]{esc(vm.get('file_name', ''))} · {vm.get('line_count', 0)} lines "
                  f"· grounded in: {esc(eps)}[/dim]")
        self.refresh_place()
        if show_code:
            self._show_prompt_summary()
            self._show_recent_runs()
            self._show_code_head()

    def _show_prompt(self) -> None:
        """The whole prompt, wrapped. The summary on open clips it; this is where you
        come to read a long one."""
        if not self._need_test():
            return
        prompt = " ".join((self._prompt or "").split())
        self._say(f"  [dim]prompt[/dim]  {esc(prompt) if prompt else '[dim](none)[/dim]'}")
        self._say("[dim]/edit --prompt to change it[/dim]")

    def _show_prompt_summary(self) -> None:
        """One line. A generated test's prompt can be several paragraphs, and the point
        of the summary is to fit runs and code on the same screen."""
        prompt = " ".join((self._prompt or "").split())
        if not prompt:
            return
        clipped = prompt if len(prompt) <= _OPEN_PROMPT_CHARS else (
            prompt[:_OPEN_PROMPT_CHARS].rstrip() + "…")
        self._say("")
        self._say(f"  [dim]prompt[/dim]  {esc(clipped)}")
        if len(prompt) > _OPEN_PROMPT_CHARS:
            self._say("          [dim]/prompt for all of it[/dim]")

    def _show_recent_runs(self) -> None:
        """The last few runs of the version being viewed, newest first."""
        runs = self._vm.get("version_runs") or []
        self._say("")
        if not runs:
            self._say("  [dim]runs[/dim]    [dim]none yet — /run to start one[/dim]")
            return
        recent = list(reversed(runs))[:_OPEN_RUNS]
        for i, r in enumerate(recent):
            label = "runs" if i == 0 else "    "
            glyph = _RUN_GLYPH.get(r["status"], "[dim]·[/dim]")
            when = (r.get("finished_at") or r.get("created_at") or "")[:16]
            err = f"  [dim]{esc(r.get('error_message', '')[:44])}[/dim]" if r.get("error_message") else ""
            self._say(f"  [dim]{label}[/dim]    {glyph} {r['status']:<8}[dim]{when}[/dim]{err}")
        if len(runs) > _OPEN_RUNS:
            self._say(f"          [dim]+{len(runs) - _OPEN_RUNS} older · /output to browse[/dim]")

    def _show_code_head(self) -> None:
        lines = (self._code_buffer or "").splitlines()
        self._say("")
        if not lines:
            self._say("  [dim]code[/dim]    [dim](none yet)[/dim]")
            return
        self._say(f"  [dim]code[/dim]    [dim]{esc(self._vm.get('file_name', ''))}[/dim]")
        for line in lines[:_OPEN_CODE_LINES]:
            self._log().write(Text("          " + line, style="grey70"))
        if len(lines) > _OPEN_CODE_LINES:
            self._say(f"          [dim]… {len(lines) - _OPEN_CODE_LINES} more lines · /code for all[/dim]")

    # --- views over the loaded test ----------------------------------------------
    def _test_name(self) -> str:
        """The loaded test's name. It lives under ``t`` on the view model, not at the
        top level — reading ``vm["name"]`` is why the header used to just say 'test'."""
        return (self._vm.get("t") or {}).get("name", "")

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
        self._say(f"  [dim]network:[/dim] {esc(net)}")
        self._say(f"  [dim]hardware:[/dim] {esc(hw)}")

    def _show_output(self) -> None:
        if not self._need_test():
            return
        vm = self._vm
        run = vm.get("run")
        if not run:
            self._say("[dim]No runs yet. /run to start.[/dim]")
            return
        self._bullet(f"Last run — status: {run['status']}"
                     + (f" — {esc(run['error_message'])}" if run.get("error_message") else ""))
        for e in vm.get("logs", []):
            self._say(f"  [dim]\\[{e['stage']}][/dim] {esc(e['message'])}")

    def _open_by_ref(self, ref: str) -> None:
        tests = tests_store.list_tests()
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
            self._say(f"[yellow]No test matches “{esc(ref)}”.[/yellow]")

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
        job, test_id, name, devices = generation.start_generate(
            prompt, self._language, None, regen_of)
        for d in devices:
            self._bullet(f"Targeting [b]{esc(d.get('model') or 'device')}[/b] {esc(d['serial'])}"
                         f" [dim](from your prompt — the run claims this one)[/dim]")
        self.current_test_id = test_id
        self.refresh_place()
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
            self._say(f"[red]{esc(ev.get('message', 'Error'))}[/red]")
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
            self.current_test_id, source, net,
            version_no=self._version_no if self._readonly else None)
        if error:
            self._say(f"[red]{esc(error)}[/red]")
            return
        if job is None:
            self._say(f"[yellow]{esc(run['error_message']) if run else 'Could not start run.'}[/yellow]")
            return
        self._say("[dim]Running…[/dim]")
        self._subscribe(job, self._on_run_event)

    def _on_run_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "log":
            self._say(f"  [dim]\\[{ev.get('stage', '')}][/dim] {esc(ev.get('message', ''))}")
        elif kind == "status":
            err = ev.get("error")
            self._say(f"[dim]status: {ev.get('status')}"
                      + (f" — {esc(err)}" if err else "") + "[/dim]")
        elif kind == "done":
            if self.current_test_id:
                self._load_vm(self.current_test_id,
                              self._version_no if self._readonly else None)
            self._bullet("Run finished.")

    def _repair(self) -> None:
        if not self._need_test() or self._busy:
            return
        job, error = generation.start_repair(self.current_test_id)
        if error:
            self._say(f"[red]{esc(error)}[/red]")
            return
        self._busy = True
        self._code_buffer = ""
        self._say("[dim]Repairing…[/dim]")
        self._subscribe(job, self._on_gen_event)

    # --- edit --------------------------------------------------------------------
    def _edit(self, args: str) -> None:
        if not self._need_test():
            return
        target, error = parse_edit(args)
        if error:
            self._say(f"[yellow]{esc(error)}[/yellow]")
            return
        if self._readonly:
            self._say("[yellow]This is an older version — /version next to reach the "
                      "editable one.[/yellow]")
            return
        if target == "code":
            self.app.push_screen(
                EditorScreen(self._vm.get("file_name") or "code", self._code_buffer,
                             "ctrl+s save · esc discard"),
                self._save_code)
        else:
            self.app.push_screen(
                EditorScreen("prompt", self._prompt, "ctrl+s save · esc discard"),
                self._save_prompt)

    def _save_code(self, text) -> None:
        """``None`` means the editor was cancelled — leave the test alone."""
        if text is None or text == self._code_buffer:
            self._say("[dim]No changes.[/dim]")
            return
        tests_store.update_code(self.current_test_id, text)
        tests_store.add_version(self.current_test_id, self._prompt,
                                self._vm.get("file_name") or "", text, self._language,
                                self._vm.get("endpoints") or [], self._vm.get("validation"))
        self.load_test(self.current_test_id, show_code=False)
        self._say(f"[green]Saved {text.count(chr(10)) + 1} lines as v{self._version_count}."
                  "[/green] [dim]/version prev to compare.[/dim]")

    def _save_prompt(self, text) -> None:
        if text is None or text.strip() == (self._prompt or "").strip():
            self._say("[dim]No changes.[/dim]")
            return
        self._prompt = text.strip()
        tests_store.set_prompt(self.current_test_id, self._prompt)
        self.load_test(self.current_test_id, show_code=False)
        self._say("[green]Prompt saved.[/green] [dim]/generate to rebuild the code from "
                  "it.[/dim]")

    # --- export ------------------------------------------------------------------
    def _export(self) -> None:
        if not self._need_test():
            return
        test = tests_store.get_test(self.current_test_id)
        if test:
            self.app.copy_to_clipboard(test.get("code", ""))
            self._say("[dim]Code copied to clipboard.[/dim]")

    # --- streaming ---------------------------------------------------------------
    @work(thread=True, exclusive=False)
    def _subscribe(self, job, on_event) -> None:
        pump(self.app, job, on_event)
