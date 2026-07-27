"""Spec-coverage tree — the pinned Meraki OpenAPI spec with this user's tests overlaid.

Data comes straight from web.services.coverage (tree_view / endpoint_detail); this
screen just renders the tree and an endpoint-detail panel.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Static, Tree

from web.services import coverage
from tui.command_screen import CommandScreen

_STATE_GLYPH = {"none": "·", "never": "○", "covered": "○", "passed": "✓", "failed": "✕"}


class CoverageScreen(CommandScreen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="cov-body"):
            yield Tree("Meraki API spec", id="cov-tree")
            with VerticalScroll(id="cov-detail"):
                yield Static("Select an endpoint to see the tests that cover it.", id="cov-detail-body")
        yield self.command_bar()
        yield Footer()

    def on_mount(self) -> None:
        view = coverage.tree_view(self.app.uid)
        tree = self.query_one("#cov-tree", Tree)
        tree.root.set_label(f"Meraki API spec — {view['covered']}/{view['total']} endpoints covered")
        tree.root.expand()
        for g in view["groups"]:
            gnode = tree.root.add(f"{g['name']}  ({g['covered']}/{g['total']})")
            for s in g["subgroups"]:
                snode = gnode.add(f"{s['name']}  ({s['covered']}/{s['total']})")
                for leaf in s["leaves"]:
                    glyph = _STATE_GLYPH.get(leaf["state"], "·")
                    snode.add_leaf(f"{glyph} {leaf['id']}", data=leaf["id"])

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        ep = event.node.data
        if not ep:
            return
        detail = coverage.endpoint_detail(self.app.uid, ep)
        body = self.query_one("#cov-detail-body", Static)
        if detail is None:
            body.update("Unknown endpoint.")
            return
        lines = [f"[b]{detail['endpoint']}[/b]", detail["summary"] or "", "",
                 f"State: {detail['state']}", ""]
        if detail["tests"]:
            lines.append("Tests using this endpoint:")
            lines += [f"  • {t['name']}" for t in detail["tests"]]
        else:
            lines.append("[dim]No tests cover this endpoint yet.[/dim]")
        body.update("\n".join(lines))
