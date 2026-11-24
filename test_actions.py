from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import actions
from sloppy_sdk import SloppyError


def done(value=None):
    future = Future()
    future.set_result(value)
    return future


class Provider:
    def __init__(self, **_):
        self.online = True
        self.last_error = None
        self.definitions = {}
        self.ready = Future()
        self.started = False

    def register_action(self, definition, run):
        self.definitions[definition["key"]] = deepcopy(definition)

    def start(self):
        self.started = True

    def close(self):
        pass


class Session:
    online = True

    def __init__(self):
        self.identity = SimpleNamespace(directory=None)
        self.ready = done()
        self.sent = []
        self.responses = {}

    def send(self, payload):
        self.sent.append(deepcopy(payload))
        id = str(len(self.sent))
        bubble = Future()
        bubble.id = id
        bubble.created = done({"id": id})
        if payload["requires_response"]:
            self.responses[id] = bubble
        else:
            bubble.set_result(None)
        return bubble

    def get(self, id):
        return done({"id": id, "state": "pending"})

    def dismiss(self, id, _reason):
        self.responses[id].set_exception(SloppyError("dismissed", "Dismissed"))
        return done()


class Context:
    def check_cancelled(self):
        pass


class TestingActionTests(unittest.TestCase):
    def setUp(self):
        with patch.object(actions, "SloppyActionsProvider", Provider):
            self.session = Session()
            self.actions = actions.TestingActions(self.session, None, {"id": "testing"})

    def test_provider_waits_for_session_approval_and_stays_closed(self):
        session = Session()
        session.ready = Future()
        with patch.object(actions, "SloppyActionsProvider", Provider):
            pending = actions.TestingActions(session, None, {"id": "testing"})
        self.assertFalse(pending.provider.started)
        session.ready.set_result(None)
        self.assertTrue(pending.provider.started)
        session.ready = Future()
        with patch.object(actions, "SloppyActionsProvider", Provider):
            closed = actions.TestingActions(session, None, {"id": "testing"})
        closed.close()
        session.ready.set_result(None)
        self.assertFalse(closed.provider.started)

    def test_manifest_is_the_catalog_and_declares_every_runtime_input(self):
        manifest = json.loads((Path(__file__).parent / "plugin.json").read_text())
        self.assertEqual(manifest["manifest_version"], 1)
        self.assertEqual(self.actions.provider.definitions, self.actions.definitions)
        self.assertEqual(
            set(self.actions.definitions),
            {"show_message", "increment_counter", "greet", "add_numbers"},
        )
        for key in ("greet", "add_numbers"):
            self.assertEqual(self.actions.definitions[key]["interaction"], "input_bubble")
            self.assertTrue(self.actions.definitions[key]["prompt"]["parameters"]["questions"])

    def test_immediate_actions_do_not_wait_for_input(self):
        self.assertEqual(self.actions.increment({}, Context())["status"], "succeeded")
        self.assertEqual(self.actions.counter, 1)
        self.assertEqual(self.session.sent, [])
        self.assertEqual(self.actions.show_message({}, Context())["status"], "succeeded")
        self.assertFalse(self.session.sent[0]["requires_response"])

    def run_form(self, method, answer):
        result = []
        worker = threading.Thread(target=lambda: result.append(method({}, Context())))
        worker.start()
        deadline = time.monotonic() + 2
        while not self.session.responses:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(len(self.session.sent), 1)  # No greeting/calculation before submission.
        self.assertTrue(worker.is_alive())
        next(iter(self.session.responses.values())).set_result({"value": answer})
        worker.join(2)
        self.assertFalse(worker.is_alive())
        return result[0]

    def test_name_and_number_forms_execute_only_after_send(self):
        result = self.run_form(self.actions.greet, {"name": "Marat"})
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.session.sent[-1]["message"], "Hello, Marat!")
        self.setUp()
        result = self.run_form(self.actions.add_numbers, {"first": 2, "second": 3.5})
        self.assertEqual(result["message"], "2 + 3.5 = 5.5")

    def test_invalid_input_never_executes_the_result_action(self):
        result = self.run_form(self.actions.greet, {"name": ""})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.session.sent), 1)
