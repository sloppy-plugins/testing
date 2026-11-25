#!/usr/bin/env python3
"""Independent Sloppy testing adapter using the public sloppy-sdk."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import queue
import signal
import sys
import tempfile
import threading
import time

if __package__:
    from .catalog import CATALOG, default_answer
else:
    from catalog import CATALOG, default_answer

PLUGIN_ID = "testing"
APPLICATION = "sloppy-testing"
ACTIVE = {"pending", "submitted"}
PACKAGE = Path(__file__).resolve().parent

if __name__ == "__main__":
    environment = PACKAGE / ".venv"
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if interpreter.is_file() and Path(sys.prefix).resolve() != environment.resolve():
        os.execv(str(interpreter), [str(interpreter), str(PACKAGE / "testing.py"), *sys.argv[1:]])

try:
    from sloppy_sdk import SloppyError, SloppySession, connection_path
    from rich.text import Text
    from textual import on
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Grid, Horizontal, Vertical
    from textual.widgets import Button, Footer, OptionList, Static, TextArea
    from textual.widgets.option_list import Option
except ModuleNotFoundError as error:
    if error.name not in {"textual", "rich", "sloppy_sdk"}:
        raise
    print(
        f"sloppy testing: missing Python dependency {error.name!r}. "
        "For this checkout, run make deps. For an installed plugin, run make installed-deps "
        "from the testing checkout to repair its environment; see README.md.",
        file=sys.stderr,
    )
    sys.exit(1)

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class PluginError(Exception):
    pass


def read_json(path):
    try:
        return parse_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PluginError(f"Cannot read JSON from {path}: {error}") from error


def parse_json(text):
    def reject(value):
        raise ValueError(f"Non-finite JSON value: {value}")

    return json.loads(text, parse_constant=reject)


class History:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.directory / "history.lock").open("a+b")
        try:
            if os.name == "nt":
                if self.lock.seek(0, os.SEEK_END) == 0:
                    self.lock.write(b"\0")
                    self.lock.flush()
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.lock.close()
            raise PluginError("Another testing plugin is using this history directory") from error
        self.file = self.directory / "history.json"
        try:
            self.data = (
                read_json(self.file)
                if self.file.exists()
                else {
                    "version": 1,
                    "resume": None,
                    "records": {},
                }
            )
            if (
                not isinstance(self.data, dict)
                or self.data.get("version") != 1
                or not isinstance(self.data.get("records"), dict)
            ):
                raise PluginError("Invalid testing history format")
            self.save()
        except (KeyError, TypeError, ValueError, OSError, PluginError):
            self.lock.close()
            raise

    def save(self):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, delete=False
            ) as file:
                temporary = Path(file.name)
                json.dump(self.data, file, indent=2, ensure_ascii=False, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.file)
        except (OSError, ValueError) as error:
            raise PluginError(f"Cannot persist testing history: {error}") from error
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def remember(self, item, service_instance):
        record = dict(deepcopy(item), service_instance=service_instance)
        if self.data["records"].get(item["id"]) != record:
            self.data["records"][item["id"]] = record
            self.save()

    def close(self):
        if os.name == "nt":
            self.lock.seek(0)
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
        self.lock.close()


class Backend:
    def __init__(self, connection_file, history, descriptor=None):
        self.history = history
        self.descriptor = descriptor or read_json(PACKAGE / "plugin.json")["plugin"]
        self.session = SloppySession(
            plugin_id=PLUGIN_ID,
            application=APPLICATION,
            descriptor=self.descriptor,
            connection_file=connection_file,
            resume=history.data.get("resume"),
        )
        self.online = False
        self.closed = False
        self.status = "Connecting to Sloppy..."
        self.actions = None

    def start_actions(self, connection_file):
        if __package__:
            from .actions import TestingActions
        else:
            from actions import TestingActions

        self.actions = TestingActions(self.session, connection_file, self.descriptor)

    def connect(self):
        deadline = time.monotonic() + 3
        while not self.session.online:
            if self.closed:
                raise PluginError("Testing session is closed")
            if self.session.ready.done():
                self.session.ready.result()
            if time.monotonic() >= deadline:
                raise PluginError(self.session.last_error or "Sloppy is not connected")
            time.sleep(0.05)
        credentials = self.session.resume_credentials
        if self.history.data.get("resume") != credentials:
            self.history.data["resume"] = credentials
            self.history.save()
        self.online = True
        self.status = "Connected to Sloppy"
        return self.session.instance_id

    def result(self, future):
        try:
            return future.result(timeout=3)
        except TimeoutError as error:
            raise PluginError(
                "Sloppy did not confirm the operation within 3 seconds; "
                "it may still be pending. Refresh before trying again."
            ) from error

    def owned(self, item):
        origin = item.get("origin", {})
        return (
            origin.get("plugin") == PLUGIN_ID
            and origin.get("application") == APPLICATION
            and item.get("session_id") == self.session.session_id
        )

    def refresh(self, stopping=None):
        instance = self.connect()
        items = self.result(self.session.list())
        seen = set()
        for item in items:
            if not self.owned(item):
                continue
            if stopping is not None and stopping.is_set():
                return
            seen.add(item["id"])
            self.history.remember(item, instance)
            if item["state"] == "submitted":
                # This plugin is the originating application. Accept only
                # the validated first proposal, then publish native completion.
                try:
                    item = self.complete(item)
                except SloppyError as error:
                    if error.status != 409:
                        raise
                    item = self.result(self.session.get(item["id"]))
                self.history.remember(item, instance)
        for id, item in list(self.history.data["records"].items()):
            if item["state"] in ACTIVE and (item["service_instance"] != instance or id not in seen):
                item = dict(
                    item,
                    state="unavailable",
                    testing_note="The service restarted or no longer retains this bubble. It was not replayed.",
                    updated_at_ms=int(time.time() * 1000),
                )
                self.history.remember(item, item["service_instance"])

    def current(self, id):
        instance = self.connect()
        known = self.history.data["records"].get(id)
        if known is None:
            raise PluginError(
                "This bubble is not recorded in this testing session. Refresh the bubble list and check its ID."
            )
        if known["service_instance"] != instance:
            raise PluginError(
                "This bubble belongs to an earlier Sloppy service session. It is no longer available; send a new bubble instead."
            )
        item = self.result(self.session.get(id))
        if not self.owned(item):
            raise PluginError("This notification does not belong to this testing session")
        if item["state"] not in ACTIVE:
            self.history.remember(item, instance)
            raise PluginError(f"This bubble is already {item['state']}")
        return item

    def send(self, payload):
        instance = self.connect()
        if not isinstance(payload, dict):
            raise PluginError("A notification must be a JSON object")
        item = self.result(self.session.send(payload).created)
        instance = self.connect()
        self.history.remember(item, instance)
        return item

    def complete(self, item):
        fresh = self.result(self.session.get(item["id"]))
        if fresh["state"] != "submitted":
            return fresh
        answer = fresh["resolution"]["answer"]
        # Never execute the displayed command, apply a diff or access a resource.
        self.result(
            self.session.acknowledge(
                item["id"],
                "accepted",
                "Accepted by the testing application; no operation executed.",
            )
        )
        return self.result(self.session.resolve(item["id"], answer))

    def resolve(self, id, answer):
        item = self.current(id)
        if not item["requires_response"]:
            return self.dismiss(id, "Completed informational bubble in the testing TUI")
        if item["state"] != "pending":
            raise PluginError(
                "Sloppy already submitted an answer; its first proposal will complete"
            )
        if not isinstance(answer, dict):
            raise PluginError("An answer must be a JSON object")
        item = self.result(self.session.answer(id, answer))
        self.history.remember(item, self.session.instance_id)
        item = self.complete(item)
        self.history.remember(item, self.session.instance_id)
        return item

    def dismiss(self, id, reason="Dismissed from the testing TUI"):
        self.current(id)
        item = self.result(self.session.dismiss(id, reason))
        self.history.remember(item, self.session.instance_id)
        return item

    def close(self):
        self.closed = True
        if self.actions is not None:
            self.actions.close()
        self.session.close()

    def snapshot(self):
        items = sorted(
            self.history.data["records"].values(), key=lambda item: item["created_at_ms"]
        )
        return dict(
            pending=deepcopy([item for item in items if item["state"] in ACTIVE]),
            resolved=deepcopy([item for item in reversed(items) if item["state"] not in ACTIVE]),
            online=self.online,
            status=self.status
            + (" · " + self.actions.status() if self.actions is not None else ""),
        )


class Worker(threading.Thread):
    def __init__(self, backend):
        super().__init__(name="sloppy-testing-service", daemon=True)
        self.backend = backend
        self.commands = queue.Queue(maxsize=32)
        self.events = queue.Queue(maxsize=64)
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()
        self.backend.close()

    def dispatch(self, operation, *args):
        try:
            self.commands.put_nowait((operation, args))
        except queue.Full as error:
            raise PluginError(
                "The service command queue is full; wait for pending requests"
            ) from error

    def emit(self, event):
        while not self.stopping.is_set():
            try:
                self.events.put(event, timeout=0.1)
                return
            except queue.Full:
                continue

    def run(self):
        next_poll, delay = 0, 0.5
        while not self.stopping.is_set():
            try:
                operation, args = self.commands.get(timeout=0.1)
            except queue.Empty:
                operation = None
            if operation:
                try:
                    item = getattr(self.backend, operation)(*args)
                    self.emit(
                        ("message", f"{operation.capitalize()}: {item['title']} ({item['state']})")
                    )
                except (PluginError, SloppyError, TimeoutError, OSError, ValueError) as error:
                    self.emit(("message", f"{operation.capitalize()} failed: {error}"))
                next_poll = 0
            if time.monotonic() >= next_poll:
                try:
                    self.backend.refresh(self.stopping)
                    delay = 0.5
                except (PluginError, SloppyError, TimeoutError, OSError, ValueError) as error:
                    self.backend.online = False
                    self.backend.status = str(error)
                    delay = min(delay * 2, 30)
                self.emit(("snapshot", self.backend.snapshot()))
                next_poll = time.monotonic() + delay


def display_text(value):
    return "".join(char if char.isprintable() else " " for char in str(value))


class TUI(App):
    TITLE = "Sloppy bubble testing"
    HORIZONTAL_BREAKPOINTS = [(0, "-too-narrow"), (90, "-wide-enough")]
    VERTICAL_BREAKPOINTS = [(0, "-too-short"), (20, "-tall-enough")]
    CSS = """
    Screen { background: $surface; }
    #connection, #status { height: auto; max-height: 2; padding: 0 1; }
    #connection { background: $primary-background; text-style: bold; }
    #status { color: $warning; }
    #size-warning { display: none; height: 1fr; padding: 1; }
    #workbench { height: 1fr; }
    Screen.-too-narrow #size-warning, Screen.-too-short #size-warning { display: block; }
    Screen.-too-narrow #workbench, Screen.-too-short #workbench { display: none; }
    #composer-pane, #records { width: 1fr; }
    #composer-pane, #pending-pane, #resolved-pane {
        border: round $primary; padding: 0 1;
    }
    #pending-pane, #resolved-pane { height: 1fr; }
    #composer-title, #editor-help { height: auto; max-height: 2; }
    #catalog, #json, #pending, #resolved { height: 1fr; }
    #json { border: none; }
    #compose-controls { grid-size: 3 2; grid-rows: 3 3; height: 6; }
    .controls { height: 3; }
    Button { min-width: 8; width: 1fr; margin: 0; }
    .detail { height: auto; max-height: 2; color: $text-muted; }
    """
    BINDINGS = [
        Binding("s,S", "send", "Send", priority=True),
        Binding("e,E", "edit", "Edit", priority=True),
        Binding("i,I", "import", "Import", show=False, priority=True),
        Binding("w,W", "export", "Export", show=False, priority=True),
        Binding("r,R", "resolve", "Resolve", priority=True),
        Binding("a,A", "answer", "Custom answer", show=False, priority=True),
        Binding("d,D", "reset_or_dismiss", "Reset/dismiss", priority=True),
        Binding("tab", "next_pane", "Panes", priority=True),
        Binding("shift+tab", "previous_pane", show=False, priority=True),
        Binding("ctrl+1", "focus_pane(0)", show=False, priority=True),
        Binding("ctrl+2", "focus_pane(1)", show=False, priority=True),
        Binding("ctrl+3", "focus_pane(2)", show=False, priority=True),
        Binding("ctrl+s", "save", "Save JSON", priority=True),
        Binding("escape,ctrl+g", "back", "Back/cancel", priority=True),
        Binding("q,Q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", show=False),
    ]

    def __init__(self, worker, catalog=None):
        super().__init__()
        self.worker = worker
        self.catalog = catalog if catalog is not None else CATALOG
        self.composer = None
        self.selected_preset = None
        self.edit_target = None
        self.snapshot = dict(
            pending=[], resolved=[], online=False, status="Connecting to Sloppy..."
        )
        self._worker_started = False

    def compose(self) -> ComposeResult:
        yield Static(
            "Sloppy bubble testing | Connecting to Sloppy...", id="connection", markup=False
        )
        yield Static("Resize the terminal to at least 90 columns x 20 rows.", id="size-warning")
        with Horizontal(id="workbench"):
            with Vertical(id="composer-pane"):
                yield Static(
                    "Choose an example, then send defaults or edit its JSON.",
                    id="composer-title",
                    markup=False,
                )
                yield OptionList(
                    *(
                        Option(Text(display_text(item["name"])), id=item["id"])
                        for item in self.catalog
                    ),
                    id="catalog",
                )
                yield TextArea(
                    language="json",
                    read_only=True,
                    show_line_numbers=True,
                    soft_wrap=False,
                    tab_behavior="indent",
                    id="json",
                )
                yield Static("", id="editor-help", markup=False)
                with Grid(id="compose-controls"):
                    yield Button("Send", id="send", variant="primary")
                    yield Button("Edit JSON", id="edit")
                    yield Button("Reset", id="reset")
                    yield Button("Import", id="import")
                    yield Button("Export", id="export")
                    yield Button("Catalog", id="back")
                with Horizontal(id="editor-controls", classes="controls"):
                    yield Button("Save", id="save", variant="primary")
                    yield Button("Cancel", id="cancel")
            with Vertical(id="records"):
                with Vertical(id="pending-pane"):
                    yield OptionList(id="pending")
                    yield Static(
                        "No bubbles yet.", id="pending-detail", classes="detail", markup=False
                    )
                    with Horizontal(classes="controls"):
                        yield Button("Resolve", id="resolve", variant="success")
                        yield Button("Answer...", id="answer")
                        yield Button("Dismiss", id="dismiss", variant="error")
                with Vertical(id="resolved-pane"):
                    yield OptionList(id="resolved")
                    yield Static(
                        "No bubbles yet.", id="resolved-detail", classes="detail", markup=False
                    )
                    with Horizontal(classes="controls"):
                        yield Button("View record", id="inspect")
        yield Static(
            "Send multiple bubbles; replies from Sloppy are accepted automatically.",
            id="status",
            markup=False,
        )
        yield Footer()

    def on_mount(self):
        self.query_one("#composer-pane").border_title = "Bubble catalog / Composer"
        self.query_one("#pending-pane").border_title = "Pending (0)"
        self.query_one("#resolved-pane").border_title = "Resolved / history (0)"
        self.show_composer()
        self.worker.start()
        self._worker_started = True
        self.set_interval(0.1, self.poll_worker)
        self.query_one("#catalog").focus()

    def on_unmount(self):
        self.stop_worker()

    def stop_worker(self):
        if self._worker_started:
            self.worker.stop()
            self.worker.join(timeout=3)
            if self.worker.is_alive():
                raise PluginError(
                    "Service worker did not stop; history lock retained until process exits"
                )
            self._worker_started = False

    def poll_worker(self):
        while True:
            try:
                kind, value = self.worker.events.get_nowait()
            except queue.Empty:
                break
            if kind == "snapshot":
                self.apply_snapshot(value)
            else:
                self.message(value)

    def message(self, value):
        self.query_one("#status", Static).update(display_text(value))

    def selected_record(self, name):
        index = self.query_one("#" + name, OptionList).highlighted
        items = self.snapshot[name]
        return items[index] if index is not None and index < len(items) else None

    def apply_snapshot(self, value):
        previous = {name: self.selected_record(name) for name in ("pending", "resolved")}
        old = self.snapshot
        self.snapshot = value
        self.query_one("#connection", Static).update(
            "Sloppy bubble testing | " + display_text(value["status"])
        )
        for name in ("pending", "resolved"):
            listing = self.query_one("#" + name, OptionList)

            def rows(items):
                return [(item["id"], item["state"], item["title"]) for item in items]

            if rows(old[name]) != rows(value[name]):
                listing.clear_options()
                listing.add_options(
                    Option(
                        Text(
                            display_text(
                                f"{'dismissed' if item['state'] == 'cancelled' else item['state']} "
                                f"{item['id'][:8]}: {item['title']}"
                            )
                        ),
                        id=item["id"],
                    )
                    for item in value[name]
                )
                listing.highlighted = (
                    next(
                        (
                            index
                            for index, item in enumerate(value[name])
                            if previous[name] and item["id"] == previous[name]["id"]
                        ),
                        0,
                    )
                    if value[name]
                    else None
                )
            title = "Pending" if name == "pending" else "Resolved / history"
            self.query_one("#" + name + "-pane").border_title = f"{title} ({len(value[name])})"
            self.update_detail(name)
        self.update_buttons()

    def update_detail(self, name):
        item = self.selected_record(name)
        detail = "No bubbles yet."
        if item:
            detail = item.get("testing_note") or json.dumps(
                item.get("resolution") or item.get("cancellation") or item.get("bubble"),
                ensure_ascii=False,
            )
        self.query_one("#" + name + "-detail", Static).update(display_text(detail))

    @on(OptionList.OptionHighlighted)
    def record_highlighted(self, event):
        if event.option_list.id in {"pending", "resolved"}:
            self.update_detail(event.option_list.id)
            self.update_buttons()

    @on(OptionList.OptionSelected, "#catalog")
    def open_preset(self, event):
        if self.edit_target is None:
            self.selected_preset = self.catalog[event.option_index]
            self.composer = deepcopy(self.selected_preset["notification"])
            self.show_composer()
            self.query_one("#json").focus()

    @on(OptionList.OptionSelected, "#pending")
    def open_pending(self):
        self.action_answer()

    @on(OptionList.OptionSelected, "#resolved")
    def open_history(self):
        self.action_inspect()

    def show_composer(self):
        composing = self.composer is not None
        self.query_one("#catalog").display = not composing
        editor = self.query_one("#json", TextArea)
        editor.display = composing
        editor.read_only = True
        editor.show_cursor = False
        if composing:
            editor.load_text(json.dumps(self.composer, indent=2, ensure_ascii=False))
        self.query_one("#composer-title", Static).update(
            display_text(self.selected_preset["name"])
            if composing
            else "Choose an example, then send defaults or edit its JSON."
        )
        self.query_one("#editor-help", Static).update(
            "Read-only preview. E edits; Escape returns to catalog."
        )
        self.query_one("#editor-help").display = composing
        self.query_one("#compose-controls").display = True
        self.query_one("#editor-controls").display = False
        self.update_buttons()

    def update_buttons(self):
        editing = self.edit_target is not None
        for name in ("send", "edit", "import", "back"):
            self.query_one("#" + name, Button).disabled = editing
        for name in ("reset", "export"):
            self.query_one("#" + name, Button).disabled = editing or self.composer is None
        item = self.selected_record("pending")
        for name in ("resolve", "dismiss"):
            self.query_one("#" + name, Button).disabled = editing or item is None
        self.query_one("#answer", Button).disabled = (
            editing or item is None or not item["requires_response"]
        )
        self.query_one("#inspect", Button).disabled = (
            editing or self.selected_record("resolved") is None
        )

    def begin_edit(self, value, target):
        if self.edit_target is not None:
            return
        self.edit_target = target
        self.query_one("#catalog").display = False
        editor = self.query_one("#json", TextArea)
        editor.display = True
        editor.read_only = target[0] == "inspect"
        editor.show_cursor = True
        editor.load_text(json.dumps(value, indent=2, ensure_ascii=False))
        labels = {
            "notification": "Edit notification JSON",
            "answer": "Edit answer JSON",
            "import": "Import path (JSON string)",
            "export": "Export path (JSON string)",
            "inspect": "History record (read-only)",
        }
        self.query_one("#composer-title", Static).update(labels[target[0]])
        self.query_one("#editor-help", Static).update(
            "Ctrl-S saves; Ctrl-G / Escape closes. Tab indents JSON."
        )
        self.query_one("#editor-help").display = True
        self.query_one("#compose-controls").display = False
        self.query_one("#editor-controls").display = True
        self.query_one("#save").display = target[0] != "inspect"
        self.query_one("#cancel", Button).label = "Close" if target[0] == "inspect" else "Cancel"
        self.update_buttons()
        editor.focus()

    def pane(self):
        widget = self.focused
        while widget is not None:
            if widget.id in {"pending-pane", "resolved-pane"}:
                return 1 if widget.id == "pending-pane" else 2
            widget = widget.parent
        return 0

    def check_action(self, action, parameters):
        if self.edit_target is not None:
            return action in {"save", "back", "focus_pane"}
        if action in {"resolve", "answer"}:
            return self.pane() == 1
        return action != "save"

    def action_focus_pane(self, pane):
        self.query_one(
            (
                "#json" if self.composer is not None or self.edit_target else "#catalog",
                "#pending",
                "#resolved",
            )[pane]
        ).focus()

    def action_next_pane(self):
        self.action_focus_pane((self.pane() + 1) % 3)

    def action_previous_pane(self):
        self.action_focus_pane((self.pane() - 1) % 3)

    def dispatch(self, operation, *args):
        try:
            self.worker.dispatch(operation, *args)
        except PluginError as error:
            self.message(str(error))
            return False
        self.message(f"{operation.capitalize()} queued; you can keep composing.")
        return True

    def action_send(self):
        index = self.query_one("#catalog", OptionList).highlighted
        if self.edit_target is None and self.pane() == 0 and index is not None:
            self.dispatch(
                "send",
                deepcopy(
                    self.composer
                    if self.composer is not None
                    else self.catalog[index]["notification"]
                ),
            )

    def action_edit(self):
        if self.edit_target is None and self.pane() == 0:
            if self.composer is None:
                index = self.query_one("#catalog", OptionList).highlighted
                if index is None:
                    return
                self.selected_preset = self.catalog[index]
                self.composer = deepcopy(self.selected_preset["notification"])
            self.begin_edit(self.composer, ("notification", None))

    def action_import(self):
        self.begin_edit(str(Path.cwd() / "bubble.json"), ("import", None))

    def action_export(self):
        if self.composer is not None:
            self.begin_edit(str(Path.cwd() / "bubble.json"), ("export", deepcopy(self.composer)))

    def action_reset_or_dismiss(self):
        if self.pane() == 1:
            self.action_dismiss()
        elif self.pane() == 0 and self.composer is not None and self.edit_target is None:
            self.composer = deepcopy(self.selected_preset["notification"])
            self.show_composer()

    def action_resolve(self):
        item = self.selected_record("pending")
        if item and self.edit_target is None:
            if item["requires_response"]:
                self.dispatch("resolve", item["id"], default_answer(item))
            else:
                self.dispatch("dismiss", item["id"], "Completed in testing TUI")

    def action_answer(self):
        item = self.selected_record("pending")
        if item and item["requires_response"]:
            self.begin_edit(default_answer(item), ("answer", item["id"]))

    def action_dismiss(self):
        item = self.selected_record("pending")
        if item and self.edit_target is None:
            self.dispatch("dismiss", item["id"])

    def action_inspect(self):
        item = self.selected_record("resolved")
        if item:
            self.begin_edit(item, ("inspect", None))

    def action_back(self):
        if self.edit_target is not None:
            self.edit_target = None
        else:
            self.composer = None
        self.show_composer()
        self.action_focus_pane(0)

    def action_save(self):
        if self.edit_target is None or self.edit_target[0] == "inspect":
            return
        try:
            value = parse_json(self.query_one("#json", TextArea).text)
            target, id = self.edit_target
            if target in {"notification", "answer"} and not isinstance(value, dict):
                raise ValueError("Notification or answer must be a JSON object")
            if target == "notification":
                self.composer = value
            elif target == "answer":
                if not self.dispatch("resolve", id, value):
                    return
            elif target in {"import", "export"}:
                if not isinstance(value, str):
                    raise ValueError("Enter the path as a JSON string")
                path = Path(value).expanduser()
                if target == "import":
                    payload = read_json(path)
                    if not isinstance(payload, dict):
                        raise ValueError("Notification file must contain a JSON object")
                    self.composer = payload
                    self.selected_preset = dict(name=path.name, notification=deepcopy(payload))
                else:
                    export_json(path, id)
            self.edit_target = None
            self.show_composer()
            self.action_focus_pane(0)
            self.message("Submitting answer..." if target == "answer" else "JSON saved.")
        except (ValueError, PluginError, OSError) as error:
            self.message(str(error))

    @on(Button.Pressed)
    def button_pressed(self, event):
        actions = {
            "send": self.action_send,
            "edit": self.action_edit,
            "reset": self.action_reset_or_dismiss,
            "import": self.action_import,
            "export": self.action_export,
            "back": self.action_back,
            "save": self.action_save,
            "cancel": self.action_back,
            "resolve": self.action_resolve,
            "answer": self.action_answer,
            "dismiss": self.action_dismiss,
            "inspect": self.action_inspect,
        }
        actions[event.button.id]()


def state_directory():
    if os.environ.get("SLOPPY_PLUGIN_DATA_DIR") and not os.environ.get("SLOPPY_TESTING_STATE_DIR"):
        return Path(os.environ["SLOPPY_PLUGIN_DATA_DIR"])
    version = read_json(PACKAGE / "plugin.json")["plugin"]["version"]
    if os.name == "nt" and not os.environ.get("SLOPPY_TESTING_STATE_DIR"):
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            raise PluginError("LOCALAPPDATA is not set; pass --state-dir")
        return Path(root) / "SloppyTesting" / version
    directory = Path(
        os.environ.get(
            "SLOPPY_TESTING_STATE_DIR",
            Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "sloppy-testing",
        )
    )
    return directory if os.environ.get("SLOPPY_TESTING_STATE_DIR") else directory / version


def export_json(path, value):
    # Exclusive creation prevents overwriting a plugin author's source files.
    with Path(path).open("x", encoding="utf-8") as file:
        json.dump(value, file, indent=2, ensure_ascii=False, allow_nan=False)
        file.write("\n")


def load_catalog(path):
    items = deepcopy(CATALOG)
    if path:
        additions = read_json(path)
        if not isinstance(additions, list):
            raise PluginError("Custom catalog must be a list of {id, name, notification} objects")
        ids = {item["id"] for item in items}
        for item in additions:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
                or item["id"] in ids
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or not isinstance(item.get("notification"), dict)
            ):
                raise PluginError("Invalid custom preset or duplicate ID in catalog")
            ids.add(item["id"])
            items.append(item)
    return items


def main():
    parser = argparse.ArgumentParser(
        description="Compose Sloppy bubbles in an independent testing TUI."
    )
    parser.add_argument("--connection-file", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--catalog", type=Path, help="Append a plugin author's preset catalog JSON")
    parser.add_argument(
        "--layout-manifest",
        type=Path,
        help="Preview another plugin's layouts under the testing identity",
    )
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument(
        "--list", action="store_true", help="List available presets without a terminal or service"
    )
    operations.add_argument(
        "--actions",
        action="store_true",
        help="Run the Actions provider without the TUI until interrupted",
    )
    operations.add_argument(
        "--send", metavar="PRESET", help="Send a preset and print the created record as JSON"
    )
    operations.add_argument("--payload", type=Path, help="Send a custom notification JSON file")
    operations.add_argument(
        "--sync",
        action="store_true",
        help="Accept app proposals and print pending/history JSON once",
    )
    operations.add_argument(
        "--resolve", metavar="ID", help="Resolve a pending record with a sample or --answer JSON"
    )
    operations.add_argument(
        "--dismiss", metavar="ID", help="Dismiss a record owned by this testing history"
    )
    operations.add_argument(
        "--export", metavar="PRESET", help="Export a default notification for plugin development"
    )
    parser.add_argument(
        "--output", type=Path, help="New JSON file path for --export (never overwritten)"
    )
    parser.add_argument("--answer", help="Answer object JSON for --resolve")
    args = parser.parse_args()
    catalog = load_catalog(args.catalog)

    def payload(id):
        for item in catalog:
            if item["id"] == id:
                return deepcopy(item["notification"])
        raise PluginError(f"Unknown preset: {id}")

    if args.answer is not None and not args.resolve:
        parser.error("--answer requires --resolve")
    if args.list:
        for item in catalog:
            print(f"{item['id']:<20} {item['name']}")
        return
    if args.export:
        if args.output is None:
            parser.error("--export requires --output")
        export_json(args.output, payload(args.export))
        print(f"Exported {args.export} to {args.output}")
        return
    if args.output is not None:
        parser.error("--output requires --export")
    if not any([args.send, args.payload, args.sync, args.resolve, args.dismiss, args.actions]):
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise PluginError(
                "The TUI needs an interactive terminal. Use --list, --send or --sync for automation."
            )
    descriptor = read_json(PACKAGE / "plugin.json")["plugin"]
    if args.layout_manifest:
        source = read_json(args.layout_manifest)
        if not isinstance(source, dict) or not isinstance(source.get("plugin"), dict):
            raise PluginError("Layout manifest must contain a plugin descriptor")
        descriptor["layouts"] = source["plugin"].get("layouts", {})
    args.connection_file = args.connection_file or connection_path()
    args.state_dir = args.state_dir or state_directory()
    history = History(args.state_dir)
    backend = None
    try:
        backend = Backend(args.connection_file, history, descriptor)
        if not any([args.send, args.payload, args.sync, args.resolve, args.dismiss]):
            backend.start_actions(args.connection_file)
        if args.send or args.payload:
            item = backend.send(payload(args.send) if args.send else read_json(args.payload))
            print(json.dumps(item))
        elif args.sync:
            backend.refresh()
            print(json.dumps(backend.snapshot()))
        elif args.resolve:
            item = backend.current(args.resolve)
            answer = (
                parse_json(args.answer)
                if args.answer
                else (default_answer(item) if item["requires_response"] else {})
            )
            print(json.dumps(backend.resolve(args.resolve, answer)))
        elif args.dismiss:
            print(json.dumps(backend.dismiss(args.dismiss)))
        elif args.actions:
            try:
                while True:
                    try:
                        backend.refresh()
                    except (PluginError, SloppyError, TimeoutError, OSError) as error:
                        backend.status = str(error)
                    time.sleep(0.25)
            except KeyboardInterrupt:
                pass
        else:
            app = TUI(Worker(backend), catalog)
            try:
                app.run()
            finally:
                app.stop_worker()
    finally:
        if backend is not None:
            backend.close()
        history.close()


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        main()
    except (PluginError, SloppyError, TimeoutError, OSError, ValueError) as error:
        print(f"sloppy testing: {error}", file=sys.stderr)
        sys.exit(1)
