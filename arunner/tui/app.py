"""FR-62 Textual VIEW LAYER -- the interactive `arunner tui` app.

This module is the ONLY place Textual is imported, and it is imported LAZILY
(from ``cli.cmd_tui``) only when ``arunner tui`` actually runs. So a bare
``arunner`` install with no `[tui]` extra never imports Textual, and the
engine/ticker/monitor path stays dependency-free (NFR-3).

Everything the views render comes from the strictly read-only
``arunner.tui.data`` layer (which reuses the FR-59 monitor render path). The app
holds exactly ONE write path -- the explicit, confirm-prompted KILL action
(``DATA.write_kill_control``) that drops a CANCEL/STOP control file; every other
view advances nothing, locks nothing, drops no control file -- it is a viewer
over externalized disk state (NFR-9), the same property the FR-59 monitor holds.

Views (read-only except the gated kill):
  1. Overview       -- ALL run-dirs newest-first, each with live counts + a
                       reconciled HEALTH flag (instr-051); ``k`` stops a run.
  2. Run view       -- the live status table (FR-59 renderer, reconciled);
                       ``k`` cancels the selected entry.
  3. Entry view     -- one entry's full record (reconciled) + heartbeat history.
  4. Log/HB tail    -- follow that entry's heartbeat.ndjson (and the journal).
Every view supports ``c`` to copy its displayed content to the clipboard.
"""
from __future__ import annotations

from pathlib import Path

# Textual is the optional `[tui]` extra. Importing app.py REQUIRES it; the
# engine path never imports this module (cli.cmd_tui imports it lazily and
# prints a clean install hint if Textual is absent).
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from arunner.tui import data as DATA

# The display refresh cadence (seconds). Like the FR-59 monitor: this refreshes
# the DISPLAY -- the freshness header makes clear that lifecycle/counts are only
# as fresh as the last engine tick, while ACTIVITY/HB-AGE are live.
REFRESH_SECONDS = 2.0


class ConfirmScreen(ModalScreen):
    """A tiny yes/no modal. The TUI's ONE write (the kill action) is gated
    behind this: ``on_confirm`` (a zero-arg callable returning a status string)
    runs only on an explicit ``y``; ``n``/``escape`` cancels without writing."""

    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n", "no", "No"),
                Binding("escape", "no", "Cancel")]

    def __init__(self, prompt: str, on_confirm) -> None:
        super().__init__()
        self._prompt = prompt
        self._on_confirm = on_confirm

    def compose(self) -> ComposeResult:
        yield Static(self._prompt + "\n\n[y] confirm    [n] cancel",
                     id="confirm-body")

    def action_yes(self) -> None:
        msg = None
        try:
            msg = self._on_confirm()
        except Exception as exc:                       # never crash the TUI
            msg = "action failed: %s" % exc
        self.app.pop_screen()
        if msg:
            _notify(self.app, msg)

    def action_no(self) -> None:
        self.app.pop_screen()


def _notify(app, message: str) -> None:
    """Best-effort status toast; degrade silently if the Textual build lacks
    ``notify`` (older/newer API)."""
    try:
        app.notify(message)
    except Exception:
        pass


class _CopyMixin:
    """A ``c`` keybind that copies the focused view's displayed text to the
    system clipboard (the one read-side convenience write -- the paste buffer,
    never the run-dir). Subclasses define ``_copy_text()``."""

    def action_copy(self) -> None:
        ok, info = DATA.copy_to_clipboard(self._copy_text())
        _notify(self.app, ("copied to clipboard via %s" % info) if ok
                else ("copy unavailable: %s" % info))

    def _copy_text(self) -> str:
        return ""


class RunPickerScreen(_CopyMixin, Screen):
    """View 1 (the in-flight OVERVIEW, instr-051): every run-dir under the
    runs-root, newest-first, each with its live counts + reconciled HEALTH flag
    (RUNNING / STALE-TICK / HUNG? / DONE / DEAD). Enter opens one; ``k`` stops a
    selected run (confirm-prompted STOP); ``c`` copies the overview."""

    BINDINGS = [Binding("r", "refresh", "Refresh"),
                Binding("k", "kill", "Stop run"),
                Binding("c", "copy", "Copy"),
                Binding("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("In-flight overview (Enter=open, k=stop, c=copy):  %s"
                    % self.app.runs_root, id="picker-title")
        yield ListView(id="run-list")
        yield Footer()

    def on_mount(self) -> None:
        self._reload()
        self.set_interval(REFRESH_SECONDS, self._reload)        # live overview

    def action_refresh(self) -> None:
        self._reload()

    def _reload(self) -> None:
        lv = self.query_one("#run-list", ListView)
        idx = lv.index                                          # keep selection
        lv.clear()
        self._rows = DATA.list_runs(self.app.runs_root)
        if not self._rows:
            lv.append(ListItem(Label("(no runs under %s)" % self.app.runs_root)))
            return
        for run in self._rows:
            item = ListItem(Label(DATA.format_picker_row(run)))
            item.run_dir = run["run_dir"]                       # carry the path
            lv.append(item)
        if idx is not None and 0 <= idx < len(self._rows):
            lv.index = idx

    def _copy_text(self) -> str:
        return "\n".join(DATA.format_picker_row(r)
                         for r in DATA.list_runs(self.app.runs_root))

    def action_kill(self) -> None:
        lv = self.query_one("#run-list", ListView)
        item = lv.highlighted_child
        run_dir = getattr(item, "run_dir", None) if item else None
        if run_dir is None:
            return

        def _do():
            p = DATA.write_kill_control(run_dir, verb="STOP")
            return "wrote STOP to %s (halts on its next tick)" % run_dir.name

        self.app.push_screen(ConfirmScreen(
            "STOP run %s? It halts cleanly on its next tick (FR-10)."
            % Path(run_dir).name, _do))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        run_dir = getattr(event.item, "run_dir", None)
        if run_dir is not None:
            self.app.push_screen(RunViewScreen(run_dir))


class RunViewScreen(_CopyMixin, Screen):
    """View 2: the live status table (FR-59 renderer, reconciled per instr-051) +
    an entry list to drill into. ``k`` CANCELs the selected entry
    (confirm-prompted, frees its slot); ``c`` copies the table. Refreshes on an
    interval; read-only except the explicit kill."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("k", "kill", "Cancel entry"),
        Binding("c", "copy", "Copy"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, run_dir) -> None:
        super().__init__()
        self.run_dir = Path(run_dir)
        self._last_good = "(waiting for run state...)"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._last_good, id="run-table")
        yield Label("Entries (Enter=drill in, k=cancel):", id="entries-title")
        yield ListView(id="entry-list")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.run_dir.name
        self._refresh_table()
        self._reload_entries()
        self.set_interval(REFRESH_SECONDS, self._refresh_table)

    def action_refresh(self) -> None:
        self._refresh_table()
        self._reload_entries()

    def _refresh_table(self) -> None:
        text, _terminal, ok = DATA.run_view_frame(self.run_dir,
                                                  interval=REFRESH_SECONDS)
        if ok:
            self._last_good = text
        self.query_one("#run-table", Static).update(self._last_good)

    def _reload_entries(self) -> None:
        lv = self.query_one("#entry-list", ListView)
        lv.clear()
        for name in DATA.entry_names(self.run_dir):
            item = ListItem(Label(name))
            item.entry_name = name
            lv.append(item)

    def _copy_text(self) -> str:
        return self._last_good

    def action_kill(self) -> None:
        lv = self.query_one("#entry-list", ListView)
        item = lv.highlighted_child
        name = getattr(item, "entry_name", None) if item else None
        if name is None:
            return

        def _do():
            DATA.write_kill_control(self.run_dir, run_name=name, verb="CANCEL")
            return "wrote CANCEL for %s (frees its slot next tick)" % name

        self.app.push_screen(ConfirmScreen(
            "CANCEL (abandon) entry %s in %s? It is marked abandoned and its "
            "pool slot frees on the next tick (FR-39)." % (name, self.run_dir.name),
            _do))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = getattr(event.item, "entry_name", None)
        if name is not None:
            self.app.push_screen(EntryViewScreen(self.run_dir, name))


class EntryViewScreen(_CopyMixin, Screen):
    """View 3: one entry's full record (reconciled state, instr-051) + its
    heartbeat history + results. ``c`` copies the rendered detail."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("t", "tail", "Tail log"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "copy", "Copy"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, run_dir, entry_name) -> None:
        super().__init__()
        self.run_dir = Path(run_dir)
        self.entry_name = entry_name
        self._rendered = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("", id="entry-detail")
            yield Static("", id="entry-history")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "%s / %s" % (self.run_dir.name, self.entry_name)
        self._refresh()
        self.set_interval(REFRESH_SECONDS, self._refresh)

    def action_refresh(self) -> None:
        self._refresh()

    def action_tail(self) -> None:
        self.app.push_screen(TailScreen(self.run_dir, self.entry_name))

    def _copy_text(self) -> str:
        return self._rendered

    def _refresh(self) -> None:
        detail = DATA.entry_detail(self.run_dir, self.entry_name)
        history = DATA.heartbeat_history(self.run_dir, self.entry_name)
        detail_text = DATA.format_entry_detail(detail)
        history_text = "heartbeat history:\n" + DATA.format_history(history)
        self._rendered = detail_text + "\n\n" + history_text
        self.query_one("#entry-detail", Static).update(detail_text)
        self.query_one("#entry-history", Static).update(history_text)


class TailScreen(_CopyMixin, Screen):
    """View 4: follow this entry's heartbeat.ndjson and the run journal live.
    ``c`` copies the current tails."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("c", "copy", "Copy"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, run_dir, entry_name) -> None:
        super().__init__()
        self.run_dir = Path(run_dir)
        self.entry_name = entry_name
        self._rendered = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Label("heartbeat.ndjson (%s):" % self.entry_name)
            yield Static("", id="hb-tail")
            yield Label("journal.ndjson (run):")
            yield Static("", id="journal-tail")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "tail %s / %s" % (self.run_dir.name, self.entry_name)
        self._refresh()
        self.set_interval(REFRESH_SECONDS, self._refresh)

    def _copy_text(self) -> str:
        return self._rendered

    def _refresh(self) -> None:
        hb = DATA.heartbeat_history(self.run_dir, self.entry_name, limit=40)
        jr = DATA.journal_tail(self.run_dir, limit=40)
        hb_text = DATA.format_history(hb, 40)
        jr_text = DATA.format_history(jr, 40)
        self._rendered = ("heartbeat.ndjson:\n" + hb_text
                          + "\n\njournal.ndjson:\n" + jr_text)
        self.query_one("#hb-tail", Static).update(hb_text)
        self.query_one("#journal-tail", Static).update(jr_text)


class ArunnerTUI(App):
    """The `arunner tui` app. Read-only except the explicit, confirm-prompted
    kill action. Opens the in-flight OVERVIEW, or jumps straight to the run view
    when launched with a run-dir argument."""

    TITLE = "arunner tui"
    CSS = "#run-table { height: auto; } #picker-title, #entries-title { padding: 0 1; }"

    def __init__(self, runs_root=None, run_dir=None) -> None:
        super().__init__()
        self.runs_root = (Path(runs_root) if runs_root is not None
                          else DATA.default_runs_root())
        self._initial_run_dir = Path(run_dir) if run_dir is not None else None

    def on_mount(self) -> None:
        if self._initial_run_dir is not None:
            self.push_screen(RunViewScreen(self._initial_run_dir))
        else:
            self.push_screen(RunPickerScreen())


def run(runs_root=None, run_dir=None) -> int:
    """Launch the TUI. Returns a process exit code (0)."""
    ArunnerTUI(runs_root=runs_root, run_dir=run_dir).run()
    return 0
