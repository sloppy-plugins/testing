"""Small, harmless actions for testing Sloppy's menus and runtime input bubbles."""

from concurrent.futures import TimeoutError
import json
import math
from pathlib import Path
import threading

from sloppy_sdk import SloppyActionsProvider, SloppyError
from catalog import response


class TestingActions:
    def __init__(self, session, connection_file, descriptor):
        self.session = session
        self.counter = 0
        self.last_result = "No actions run yet"
        self._lock = threading.Lock()
        self._closed = False
        manifest = json.loads((Path(__file__).parent / "plugin.json").read_text())
        self.provider = SloppyActionsProvider(
            plugin_id="testing",
            name="Testing actions",
            descriptor=descriptor,
            categories=manifest["actions"]["catalog"]["categories"],
            provider_key=manifest["actions"]["provider_key"],
            application="sloppy-testing",
            identity_directory=session.identity.directory,
            connection_file=connection_file,
        )
        self.definitions = {
            definition["key"]: definition
            for definition in manifest["actions"]["catalog"]["actions"]
        }
        handlers = {
            "show_message": self.show_message,
            "increment_counter": self.increment,
            "greet": self.greet,
            "add_numbers": self.add_numbers,
        }
        if set(handlers) != set(self.definitions):
            raise ValueError("The testing manifest and action handlers disagree")
        for key, definition in self.definitions.items():
            self.provider.register_action(definition, handlers[key])
        # Share approval with the bubble connection. Opening two unapproved
        # connections at once would create competing certificate requests.
        session.ready.add_done_callback(self._session_ready)

    def _session_ready(self, ready):
        try:
            ready.result()
        except Exception as error:
            self.provider.last_error = str(error)
            if not self.provider.ready.done():
                self.provider.ready.set_exception(error)
        else:
            with self._lock:
                if not self._closed:
                    self.provider.start()

    def status(self):
        with self._lock:
            if not self.provider.online and self.provider.last_error:
                return "Actions unavailable: " + self.provider.last_error
            online = "connected" if self.provider.online else "offline"
            return f"Actions {online} · Counter: {self.counter} · {self.last_result}"

    def _success(self, message):
        with self._lock:
            self.last_result = message
        return {"status": "succeeded", "message": message}

    def _show(self, title, message):
        if not self.session.online:
            raise SloppyError(
                "service_unavailable",
                "Connect and enable this plugin's bubble session before running bubble actions.",
            )
        self.session.send(
            dict(kind="information", title=title, message=message, requires_response=False)
        ).created.result(timeout=3)

    def show_message(self, _input, context):
        context.check_cancelled()
        self._show(
            "Testing action", "This action ran immediately from Sloppy. No input was required."
        )
        return self._success("Test message shown")

    def increment(self, _input, context):
        context.check_cancelled()
        with self._lock:
            self.counter += 1
            value = self.counter
            self.last_result = f"Counter is now {value}"
        return {"status": "succeeded", "message": f"Counter is now {value}"}

    def _ask(self, key, context):
        if not self.session.online:
            raise SloppyError(
                "service_unavailable",
                "Connect and enable this plugin's bubble session before requesting input.",
            )
        definition = self.definitions[key]
        payload = dict(
            kind="question",
            title=definition["title"],
            message=definition["description"],
            requires_response=True,
            response=response("json", custom=True),
            bubble=definition["prompt"],
        )
        bubble = self.session.send(payload)
        bubble.created.result(timeout=3)
        try:
            while True:
                context.check_cancelled()
                try:
                    answer = bubble.result(timeout=0.1)
                    return answer["value"]
                except TimeoutError:
                    item = self.session.get(bubble.id).result(timeout=3)
                    if item["state"] == "submitted":
                        answer = item["resolution"]["answer"]
                        try:
                            self.session.acknowledge(
                                bubble.id, "accepted", "Input accepted by the testing action."
                            ).result(timeout=3)
                            self.session.resolve(bubble.id, answer).result(timeout=3)
                        except SloppyError as error:
                            # The TUI's normal refresh may have completed the same proposal.
                            if error.status != 409:
                                raise
        finally:
            if not bubble.done():
                self.session.dismiss(bubble.id, "The testing action stopped waiting for input.")

    def greet(self, _input, context):
        try:
            values = self._ask("greet", context)
        except SloppyError as error:
            if error.code == "dismissed":
                return {"status": "cancelled", "message": "Greeting cancelled"}
            raise
        name = values.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            return {
                "status": "failed",
                "error": {"code": "invalid_name", "message": "Enter a name of 1–200 characters."},
            }
        context.check_cancelled()
        self._show("Hello!", f"Hello, {name.strip()}!")
        return self._success("Greeting shown")

    def add_numbers(self, _input, context):
        try:
            values = self._ask("add_numbers", context)
        except SloppyError as error:
            if error.code == "dismissed":
                return {"status": "cancelled", "message": "Calculation cancelled"}
            raise
        numbers = [values.get(key) for key in ("first", "second")]
        if any(
            type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1_000_000
            for value in numbers
        ):
            return {
                "status": "failed",
                "error": {
                    "code": "invalid_numbers",
                    "message": "Enter two finite numbers between −1,000,000 and 1,000,000.",
                },
            }
        context.check_cancelled()
        result = f"{numbers[0]:g} + {numbers[1]:g} = {sum(numbers):g}"
        self._show("Calculation complete", result)
        return self._success(result)

    def close(self):
        with self._lock:
            self._closed = True
        self.provider.close()
