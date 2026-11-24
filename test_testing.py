"""Plugin-owned tests; no imports from Sloppy's implementation or its test suite."""

from copy import deepcopy
import errno
import http.client
import json
import os
from pathlib import Path
from types import SimpleNamespace
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from textual.document._syntax_aware_document import SyntaxAwareDocument

import catalog
import testing
from sloppy_sdk import SloppyError, SloppyIdentity, SloppySession, discover, inspect_service


class BackendErrorTests(unittest.TestCase):
    def test_unknown_bubble_is_not_misreported_as_a_service_restart(self):
        backend = testing.Backend.__new__(testing.Backend)
        backend.connect = lambda: "current-instance"
        backend.history = SimpleNamespace(data={"records": {}})
        with self.assertRaisesRegex(testing.PluginError, "not recorded.*check its ID"):
            backend.current("unknown")
        backend.history.data["records"]["old"] = {"service_instance": "old-instance"}
        with self.assertRaisesRegex(
            testing.PluginError, "earlier Sloppy service session.*send a new bubble"
        ):
            backend.current("old")


class CatalogTests(unittest.TestCase):
    def test_public_contract_variants_are_covered(self):
        items = [item["notification"] for item in catalog.CATALOG]
        self.assertEqual(
            {item["bubble"]["type"] for item in items},
            {
                "message",
                "command",
                "diff",
                "questionnaire",
                "resources",
                "plan",
                "progress",
                "report",
            },
        )
        self.assertEqual(
            {item["kind"] for item in items},
            {"information", "success", "warning", "error", "progress", "approval", "question"},
        )
        self.assertEqual(
            {item["response"]["value_type"] for item in items if item["requires_response"]},
            {"boolean", "single_choice", "text", "number", "json"},
        )
        fields = [
            field
            for item in items
            if item["bubble"]["type"] == "questionnaire"
            for field in item["bubble"]["parameters"]["questions"]
        ]
        self.assertEqual(
            {field["response"]["value_type"] for field in fields},
            {"boolean", "single_choice", "multi_choice", "text", "number", "json"},
        )
        self.assertEqual(
            {
                file["operation"]
                for item in items
                if item["bubble"]["type"] == "diff"
                for file in item["bubble"]["parameters"]["files"]
            },
            {"create", "edit", "delete", "rename"},
        )
        self.assertEqual(len({item["id"] for item in catalog.CATALOG}), len(catalog.CATALOG))

    def test_defaults_are_independent_copies(self):
        first = catalog.find_preset("command")
        first["response"]["choices"].clear()
        first["bubble"]["parameters"]["command"] = "changed"
        second = catalog.find_preset("command")
        self.assertEqual(len(second["response"]["choices"]), 2)
        self.assertNotEqual(second["bubble"]["parameters"]["command"], "changed")

    def test_custom_catalog_and_export_are_reusable_without_service(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "catalog.json"
            custom = dict(
                id="my-plugin-request", name="My request", notification=catalog.find_preset("plan")
            )
            testing.export_json(file, [custom])
            items = testing.load_catalog(file)
            self.assertEqual(items[-1], custom)
            output = Path(directory) / "request.json"
            testing.export_json(output, custom["notification"])
            self.assertEqual(testing.read_json(output), custom["notification"])
            with self.assertRaises(FileExistsError):
                testing.export_json(output, {})
            file.write_text(json.dumps([dict(custom, id="command")]), encoding="utf-8")
            with self.assertRaises(testing.PluginError):
                testing.load_catalog(file)

    def test_non_finite_json_is_rejected(self):
        for value in ["NaN", "Infinity", "-Infinity"]:
            with self.assertRaises(ValueError):
                testing.parse_json('{"value":' + value + "}")

    def test_platform_executables_and_portable_tui_dependency(self):
        manifest = testing.read_json(testing.PACKAGE / "plugin.json")
        paths = manifest["runtime"]["executables"]
        self.assertEqual(
            set(paths),
            {"macos-aarch64", "macos-x86_64", "linux-aarch64", "linux-x86_64", "windows-x86_64"},
        )
        for file in paths.values():
            self.assertTrue((testing.PACKAGE / file).is_file())
        requirements = (testing.PACKAGE / "requirements.txt").read_text()
        self.assertIn("textual[syntax]==", requirements)
        self.assertNotIn("curses", requirements)


class HistoryTests(unittest.TestCase):
    def test_history_is_unicode_safe_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            history = testing.History(directory)
            credentials = {
                "session_id": "session",
                "instance_id": "service",
                "resume_token": "private",
            }
            history.data["resume"] = credentials
            history.remember(
                dict(id="one", title="Unicode: \u4f60\u597d", state="resolved", created_at_ms=1),
                "service",
            )
            history.close()
            reopened = testing.History(directory)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.data["resume"], credentials)
            self.assertEqual(reopened.data["records"]["one"]["title"], "Unicode: \u4f60\u597d")
            if os.name != "nt":
                self.assertEqual(reopened.file.stat().st_mode & 0o777, 0o600)

    def test_two_processes_cannot_share_history(self):
        with tempfile.TemporaryDirectory() as directory:
            history = testing.History(directory)
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(testing.PACKAGE / "testing.py"),
                        "--state-dir",
                        directory,
                        "--sync",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Another testing plugin", result.stderr)
            finally:
                history.close()


class FakeWorker:
    def __init__(self):
        self.commands = []
        self.events = queue.Queue()
        self.stopping = threading.Event()
        self.started = False

    def dispatch(self, operation, *args):
        self.commands.append((operation, args))

    def start(self):
        self.started = True

    def stop(self):
        self.stopping.set()

    def join(self, timeout):
        pass

    def is_alive(self):
        return False


class TUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.worker = FakeWorker()
        self.ui = testing.TUI(self.worker)

    @property
    def editor(self):
        return self.ui.query_one("#json", testing.TextArea)

    def snapshot(self, pending=(), resolved=(), status="Connected to Sloppy"):
        return dict(pending=list(pending), resolved=list(resolved), online=True, status=status)

    def record(self, id="one", preset="permission", state="pending"):
        return dict(catalog.find_preset(preset), id=id, state=state)

    def test_syntax_point_access_preserves_reference_counts(self):
        # 0.26.0 over-decremented Point.row/column references, corrupting unrelated
        # Python objects later. Isolate the probe and bypass cleanup on failure.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import os
import sys
from tree_sitter import Range
for field, index in [('row', 0), ('column', 1)]:
    for _ in range(1000):
        point = Range((300, 400), (300, 410), 0, 10).start_point
        value = point[index]
        before = sys.getrefcount(value)
        observed = getattr(point, field)
        del observed
        after = sys.getrefcount(value)
        if before != after:
            print(f'{field}: refcount changed from {before} to {after}', flush=True)
            os._exit(1)
""",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    async def test_large_json_syntax_survives_repeated_reloads(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            await pilot.press("e")
            for iteration in range(5):
                draft = json.dumps({"values": ["x" * 400] * 350, "edit": iteration}, indent=2)
                self.editor.load_text(draft)
                self.editor.move_cursor((300, 350))
                await pilot.pause()
                self.assertEqual(self.editor.text, draft)
                self.assertIsInstance(self.editor.document, SyntaxAwareDocument)

    async def test_choose_edit_send_and_choose_another_without_waiting(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            await pilot.press("enter", "e")
            self.assertFalse(self.editor.read_only)
            self.editor.load_text(json.dumps(dict(self.ui.composer, title="Custom title")))
            await pilot.press("ctrl+s")
            await pilot.click("#send")
            self.assertEqual(self.worker.commands[0][0], "send")
            self.assertEqual(self.worker.commands[0][1][0]["title"], "Custom title")
            await pilot.press("escape", "down", "enter", "s")
            self.assertEqual(len(self.worker.commands), 2)
            self.assertEqual(self.worker.commands[1][1][0]["kind"], "success")
        self.assertTrue(self.worker.started)
        self.assertTrue(self.worker.stopping.is_set())

    async def test_pending_resolve_custom_answer_and_dismiss(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            self.ui.apply_snapshot(self.snapshot(pending=[self.record()]))
            await pilot.press("tab", "r")
            self.assertEqual(
                self.worker.commands[-1],
                ("resolve", ("one", catalog.default_answer(self.record()))),
            )
            await pilot.click("#answer")
            self.editor.load_text('{"selected_choice_ids":["reject"]}')
            await pilot.press("ctrl+s")
            self.assertEqual(
                self.worker.commands[-1], ("resolve", ("one", {"selected_choice_ids": ["reject"]}))
            )
            await pilot.click("#dismiss")
            self.assertEqual(self.worker.commands[-1], ("dismiss", ("one",)))

    async def test_invalid_editor_json_preserves_draft(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            await pilot.press("enter", "e")
            original = deepcopy(self.ui.composer)
            for invalid in ("{invalid", "[]", '{"fraction":NaN}'):
                self.editor.load_text(invalid)
                await pilot.press("ctrl+s")
                self.assertIsNotNone(self.ui.edit_target)
                self.assertEqual(self.editor.text, invalid)
                self.assertEqual(self.ui.composer, original)
                self.assertTrue(self.ui.query_one("#status", testing.Static).content)
            await pilot.press("ctrl+g")
            self.assertIsNone(self.ui.edit_target)
            self.assertEqual(self.ui.composer, original)

    async def test_import_export_and_overwrite_error_preserve_composition(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.json", Path(directory) / "output.json"
            testing.export_json(source, catalog.find_preset("typed-input"))
            async with self.ui.run_test(size=(120, 30)) as pilot:
                await pilot.press("i")
                self.editor.load_text(json.dumps(str(source)))
                await pilot.press("ctrl+s", "w")
                self.editor.load_text(json.dumps(str(output)))
                await pilot.press("ctrl+s")
                self.assertEqual(testing.read_json(output), testing.read_json(source))
                await pilot.press("w")
                self.editor.load_text(json.dumps(str(output)))
                await pilot.press("ctrl+s")
                self.assertEqual(self.ui.edit_target[0], "export")
                self.assertIn("File exists", self.ui.query_one("#status", testing.Static).content)

    async def test_terminal_resize_preserves_unicode_draft_and_cursor(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            await pilot.press("e")
            draft = json.dumps(
                {"text": "\u4f60\u597d" * 200, "lines": "\n" * 100}, ensure_ascii=False
            )
            self.editor.load_text(draft)
            self.editor.move_cursor((0, 150))
            await pilot.pause()
            position = self.editor.cursor_location
            for width, height in [(40, 10), (90, 20), (120, 30), (180, 50)]:
                await pilot.resize_terminal(width, height)
                self.assertEqual(
                    self.ui.query_one("#workbench").display, width >= 90 and height >= 20
                )
                self.assertEqual(self.editor.text, draft)
                self.assertEqual(self.editor.cursor_location, position)
            self.assertGreater(self.editor.scroll_x, 0)
            self.assertTrue(self.editor.show_cursor)
        self.assertNotIn("\x1b", testing.display_text("malicious\x1b[31m"))

    async def test_editor_typing_selection_undo_and_shortcuts_do_not_send(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            await pilot.press("e")
            self.editor.load_text("")
            await pilot.press("q", "s", "d", "e", "i", "w", "r", "enter", "tab", "\u4f60", "\u597d")
            self.assertEqual(self.editor.text, "qsdeiwr\n    \u4f60\u597d")
            self.assertEqual(self.worker.commands, [])
            self.assertIs(self.ui.focused, self.editor)
            await pilot.press("f7", "backspace")
            self.assertEqual(self.editor.text, "")
            await pilot.press("ctrl+z")
            self.assertEqual(self.editor.text, "qsdeiwr\n    \u4f60\u597d")

    async def test_polling_preserves_editor_cursor_and_pending_selection_by_id(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            self.ui.apply_snapshot(self.snapshot(pending=[self.record("one"), self.record("two")]))
            self.ui.query_one("#pending", testing.OptionList).highlighted = 1
            await pilot.press("e")
            self.editor.load_text('{\n  "a": 1\n}')
            self.editor.move_cursor((1, 2))
            await pilot.pause()
            self.worker.events.put(("snapshot", self.snapshot(pending=[self.record("two")])))
            self.worker.events.put(("message", "Status updated while editing"))
            self.ui.poll_worker()
            await pilot.pause()
            self.assertEqual(self.editor.text, '{\n  "a": 1\n}')
            self.assertEqual(self.editor.cursor_location, (1, 2))
            self.assertTrue(self.editor.show_cursor)
            self.assertEqual(self.ui.selected_record("pending")["id"], "two")
            self.assertIs(self.ui.focused, self.editor)

    async def test_mouse_selects_catalog_and_record_without_discarding_editor(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            await pilot.click("#catalog", offset=(3, 2))
            self.assertEqual(self.ui.composer["kind"], "success")
            await pilot.click("#edit")
            draft = self.editor.text
            self.ui.apply_snapshot(self.snapshot(pending=[self.record()]))
            await pilot.click("#pending", offset=(3, 1))
            await pilot.click("#answer")
            self.assertEqual(self.ui.edit_target[0], "notification")
            self.assertEqual(self.editor.text, draft)
            self.assertEqual(self.worker.commands, [])
            await pilot.click("#cancel")
            self.assertIsNone(self.ui.edit_target)

    async def test_history_record_is_read_only_and_preserves_composition(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            await pilot.press("enter")
            composition = deepcopy(self.ui.composer)
            record = self.record(state="resolved")
            record["resolution"] = {"answer": {"value": "An answer from Sloppy"}}
            self.ui.apply_snapshot(self.snapshot(resolved=[record]))
            await pilot.press("tab", "tab", "enter")
            self.assertTrue(self.editor.read_only)
            original = self.editor.text
            await pilot.press("q", "s", "delete")
            self.assertEqual(self.editor.text, original)
            await pilot.press("escape")
            self.assertEqual(self.ui.composer, composition)

    async def test_informational_completion_and_empty_lists(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            self.assertTrue(self.ui.query_one("#resolve", testing.Button).disabled)
            self.ui.apply_snapshot(self.snapshot(pending=[self.record(preset="information")]))
            self.assertTrue(self.ui.query_one("#answer", testing.Button).disabled)
            await pilot.click("#resolve")
            self.assertEqual(
                self.worker.commands[-1], ("dismiss", ("one", "Completed in testing TUI"))
            )
            self.ui.apply_snapshot(self.snapshot())
            self.assertTrue(self.ui.query_one("#dismiss", testing.Button).disabled)

    async def test_queue_full_error_preserves_answer_draft(self):
        async with self.ui.run_test(size=(120, 30)) as pilot:
            self.ui.apply_snapshot(self.snapshot(pending=[self.record()]))
            await pilot.click("#answer")
            draft = self.editor.text
            with patch.object(
                self.worker, "dispatch", side_effect=testing.PluginError("Queue full")
            ):
                await pilot.press("ctrl+s")
            self.assertEqual(self.ui.edit_target, ("answer", "one"))
            self.assertEqual(self.editor.text, draft)
            self.assertEqual(self.ui.query_one("#status", testing.Static).content, "Queue full")

    async def test_untrusted_titles_are_literal_text_not_terminal_or_rich_markup(self):
        custom = [
            {
                "id": "literal",
                "name": "[bold]Literal\x1b[31m",
                "notification": catalog.find_preset("text"),
            }
        ]
        self.ui = testing.TUI(self.worker, custom)
        async with self.ui.run_test(size=(120, 30)):
            prompt = self.ui.query_one("#catalog", testing.OptionList).get_option_at_index(0).prompt
            self.assertIn("[bold]Literal", prompt.plain)
            self.assertNotIn("\x1b", prompt.plain)
            self.assertEqual(prompt.spans, [])


SERVICE_BINARY = os.environ.get("SLOPPY_TESTING_SERVICE") or shutil.which("SloppyService")
CLI_BINARY = os.environ.get("SLOPPY_TESTING_CLI") or shutil.which("sloppy")


def administer(file, path, body=None):
    connection = discover(file)
    identity = SloppyIdentity(application="Testing integration", plugin_id="testing")
    pin = next(
        entry["fingerprint"]
        for entry in identity.list_known_services()
        if entry["endpoint"] == connection["base_url"]
    )
    client = http.client.HTTPSConnection(
        "127.0.0.1",
        int(connection["base_url"].rsplit(":", 1)[1]),
        timeout=1,
        context=identity._client_context(pin),
    )
    try:
        client.request(
            "GET" if body is None else "POST",
            path,
            body=None if body is None else json.dumps(body),
            headers={
                "Authorization": "Bearer " + connection["token"],
                "Content-Type": "application/json",
            },
        )
        reply = client.getresponse()
        value = json.loads(reply.read())
        if reply.status != 200:
            raise AssertionError(f"Administration failed: {reply.status}: {value}")
        return value
    finally:
        client.close()


@unittest.skipUnless(
    SERVICE_BINARY and CLI_BINARY,
    "Set SLOPPY_TESTING_SERVICE and SLOPPY_TESTING_CLI to run public-API integration tests",
)
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="sloppy-testing-tests-")
        cls.directory = Path(cls.temporary.name)
        cls.file = cls.directory / "connection.json"
        cls.environment = patch.dict(
            os.environ,
            {
                "HOME": str(cls.directory),
                "APPDATA": str(cls.directory),
                "XDG_DATA_HOME": str(cls.directory),
                "SLOPPY_IDENTITY_PROFILE": "testing-integration",
                "SLOPPY_PLUGIN_MANIFEST": str(testing.PACKAGE / "plugin.json"),
            },
        )
        cls.environment.start()
        cls.server = None
        try:
            identity = SloppyIdentity(application="Testing integration", plugin_id="testing")
            sha = identity.fingerprint()
            now = int(time.time() * 1000)
            (cls.directory / "client-trust.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "allow_remote_clients": False,
                        "ignore_unknown_clients": False,
                        "trusted_clients": {
                            sha: {
                                "fingerprint": sha,
                                "name": "Testing integration",
                                "first_seen_at_ms": now,
                                "last_connected_at_ms": now,
                                "address": "127.0.0.1",
                            }
                        },
                        "rejected_clients": {},
                    }
                )
            )
            cls.start_service()
            cls.enable_testing()
        except Exception:
            cls.stop_service()
            cls.environment.stop()
            cls.temporary.cleanup()
            raise

    @classmethod
    def enable_testing(cls):
        administer(
            cls.file, "/v1/plugins", testing.read_json(testing.PACKAGE / "plugin.json")["plugin"]
        )
        administer(cls.file, "/v1/plugins/testing/enabled", {"enabled": True})

    @classmethod
    def start_service(cls):
        cls.server = subprocess.Popen(
            [SERVICE_BINARY, "--port", "0", "--connection-file", str(cls.file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cls.file.exists():
                connection = testing.read_json(cls.file)
                try:
                    identity = SloppyIdentity(
                        application="Testing integration", plugin_id="testing"
                    )
                    if not any(
                        entry["endpoint"] == connection["base_url"]
                        for entry in identity.list_known_services()
                    ):
                        identity.remember_service(inspect_service(connection["base_url"]))
                    if administer(cls.file, "/health")["instance_id"] == connection["instance_id"]:
                        return
                except OSError:
                    pass
            if cls.server.poll() is not None:
                raise AssertionError(cls.server.stderr.read())
            time.sleep(0.05)
        raise AssertionError("Service did not start")

    @classmethod
    def stop_service(cls):
        if cls.server:
            if cls.server.poll() is None:
                cls.server.terminate()
            cls.server.wait(timeout=5)
            cls.server.stdout.close()
            cls.server.stderr.close()
            cls.server = None

    @classmethod
    def tearDownClass(cls):
        cls.stop_service()
        cls.environment.stop()
        cls.temporary.cleanup()

    def setUp(self):
        self.state = tempfile.TemporaryDirectory(prefix="sloppy-testing-history-")
        self.history = testing.History(self.state.name)
        self.backend = testing.Backend(self.file, self.history)

    def tearDown(self):
        self.backend.close()
        self.history.close()
        self.state.cleanup()

    def test_all_presets_send_and_resolve_using_public_api(self):
        for preset in catalog.CATALOG:
            with self.subTest(preset=preset["id"]):
                item = self.backend.send(preset["notification"])
                self.assertEqual(item["state"], "pending")
                self.assertEqual(item["origin"]["plugin"], "testing")
                answer = catalog.default_answer(item) if item["requires_response"] else {}
                completed = self.backend.resolve(item["id"], answer)
                self.assertNotIn(completed["state"], testing.ACTIVE)
        snapshot = self.backend.snapshot()
        self.assertEqual(snapshot["pending"], [])
        self.assertEqual(len(snapshot["resolved"]), len(catalog.CATALOG))

    def test_submitted_proposal_is_accepted_and_authoritatively_completed(self):
        item = self.backend.send(catalog.find_preset("questionnaire"))
        answer = catalog.default_answer(item)
        self.backend.session.answer(item["id"], answer).result(timeout=3)
        self.backend.refresh()
        completed = self.backend.snapshot()["resolved"][0]
        self.assertEqual(completed["state"], "resolved")
        self.assertEqual(completed["resolution"]["answer"]["source"], testing.APPLICATION)
        self.assertEqual(completed["resolution"]["answer"]["value"], answer["value"])
        self.assertEqual(completed["acknowledgements"][-1]["status"], "accepted")

    def test_session_dismissal_moves_to_history(self):
        item = self.backend.send(catalog.find_preset("information"))
        self.backend.session.dismiss(item["id"], "Dismissed").result(timeout=3)
        self.backend.refresh()
        self.assertEqual(self.backend.snapshot()["pending"], [])
        self.assertEqual(
            self.backend.snapshot()["resolved"][0]["cancellation"]["reason"], "Dismissed"
        )

    def test_invalid_parameters_and_answers_remain_visible_errors(self):
        payload = catalog.find_preset("progress")
        payload["bubble"]["parameters"]["fraction"] = 2
        with self.assertRaises(SloppyError) as raised:
            self.backend.send(payload)
        self.assertEqual(raised.exception.status, 400)
        item = self.backend.send(catalog.find_preset("number"))
        with self.assertRaises(SloppyError):
            self.backend.resolve(item["id"], {"value": 100})
        self.assertEqual(self.backend.snapshot()["pending"][0]["state"], "pending")

    def test_first_proposal_is_not_overwritten_by_tui(self):
        item = self.backend.send(catalog.find_preset("permission"))
        self.backend.session.answer(item["id"], {"selected_choice_ids": ["reject"]}).result(
            timeout=3
        )
        with self.assertRaises(testing.PluginError):
            self.backend.resolve(item["id"], catalog.default_answer(item))
        self.backend.refresh()
        completed = self.backend.snapshot()["resolved"][0]
        self.assertEqual(completed["resolution"]["answer"]["selected_choice_ids"], ["reject"])

    def test_other_origin_bubbles_are_never_resolved(self):
        with SloppySession(
            plugin_id="testing", application="other-plugin", connection_file=self.file
        ) as other:
            item = other.send(catalog.find_preset("text")).created.result(timeout=3)
            other.answer(item["id"], {"value": "Other session"}).result(timeout=3)
            self.backend.refresh()
            self.assertNotIn(item["id"], self.history.data["records"])
            self.assertEqual(other.get(item["id"]).result(timeout=3)["state"], "submitted")
            with self.assertRaises(SloppyError) as denied:
                self.backend.session.get(item["id"]).result(timeout=3)
            self.assertEqual(denied.exception.status, 404)

    def test_authentication_does_not_use_proxy_environment(self):
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://invalid.invalid:1",
                "http_proxy": "http://invalid.invalid:1",
                "NO_PROXY": "",
            },
        ):
            self.assertEqual(self.backend.connect(), discover(self.file)["instance_id"])

    def test_plugin_layouts_can_be_previewed_without_changing_other_plugins(self):
        descriptor = testing.read_json(testing.PACKAGE / "plugin.json")["plugin"]
        descriptor["layouts"]["default"].update(width=500, accent="green", template="command")
        self.backend.close()
        backend = self.backend = testing.Backend(self.file, self.history, descriptor)
        item = backend.send(catalog.find_preset("information"))
        self.assertEqual(item["presentation"]["width"], 500)
        self.assertEqual(item["presentation"]["accent"], "green")
        self.assertEqual(item["origin"]["plugin"], "testing")

    def test_disabled_mirroring_is_not_silently_enabled(self):
        self.backend.connect()
        administer(self.file, "/v1/plugins/testing/enabled", {"enabled": False})
        try:
            with self.assertRaisesRegex(SloppyError, "disabled"):
                self.backend.send(catalog.find_preset("information"))
        finally:
            administer(self.file, "/v1/plugins/testing/enabled", {"enabled": True})

    def test_install_trust_and_installed_plugin_are_independent_of_cwd(self):
        env = dict(
            os.environ,
            SLOPPY_CLI_DATA_DIR=str(self.directory / "cli"),
            SLOPPY_TESTING_STATE_DIR=str(self.directory / "installed-history"),
            SLOPPY_CONNECTION_FILE=str(self.file),
            PYTHONDONTWRITEBYTECODE="1",
            PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        )

        def cli(*args):
            return subprocess.run(
                [CLI_BINARY, *args],
                env=env,
                cwd=self.directory,
                capture_output=True,
                text=True,
                timeout=10,
            )

        installed = cli("plugins", "install", str(testing.PACKAGE))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertNotEqual(cli("testing", "--list").returncode, 0)
        self.assertEqual(cli("plugins", "enable", "testing").returncode, 0)
        listed = cli("testing", "--list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("typed-input", listed.stdout)
        session_id = None
        for preset in catalog.CATALOG:
            with self.subTest(preset=preset["id"]):
                created = cli("testing", "--send", preset["id"])
                self.assertEqual(created.returncode, 0, created.stderr)
                item = json.loads(created.stdout)
                session_id = session_id or item["session_id"]
                self.assertEqual(item["session_id"], session_id)
                resolved = cli("testing", "--resolve", item["id"])
                self.assertEqual(resolved.returncode, 0, resolved.stderr)
                self.assertNotIn(json.loads(resolved.stdout)["state"], testing.ACTIVE)
        self.assertEqual(cli("plugins", "disable", "testing").returncode, 0)
        self.assertNotEqual(cli("testing", "--list").returncode, 0)
        administer(self.file, "/v1/plugins/testing/enabled", {"enabled": True})

    @unittest.skipIf(
        os.name == "nt", "Unix PTY check; Windows rendering/key paths have platform-neutral tests"
    )
    def test_real_tui_composing_editing_sending_and_dismissal(self):
        import fcntl
        import pty
        import select
        import struct
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
        state = Path(self.state.name) / "interactive"
        env = dict(
            os.environ,
            TERM="xterm-256color",
            SLOPPY_CLI_DATA_DIR=str(Path(self.state.name) / "interactive-cli"),
            SLOPPY_CONNECTION_FILE=str(self.file),
            PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        )
        for arguments in [
            ("plugins", "install", str(testing.PACKAGE)),
            ("plugins", "enable", "testing"),
        ]:
            subprocess.run(
                [CLI_BINARY, *arguments],
                cwd=self.directory,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        child = subprocess.Popen(
            [CLI_BINARY, "testing", "--state-dir", str(state)],
            cwd=self.directory,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
        )
        os.close(slave)
        output = bytearray()

        def wait(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    output.extend(os.read(master, 65536))
                if child.poll() is not None:
                    self.fail(f"TUI exited unexpectedly: {output.decode(errors='replace')}")
                result = predicate()
                if result:
                    return result
            self.fail(f"TUI condition timed out: {output.decode(errors='replace')}")

        def press(key):
            os.write(master, key)
            settled = time.monotonic() + 0.15
            wait(lambda: time.monotonic() >= settled)

        try:
            wait(lambda: (state / "history.json").exists() and b"Bubble catalog" in output)

            def records():
                return list(testing.read_json(state / "history.json")["records"].values())

            press(b"\r")
            press(b"s")
            first = wait(lambda: next(iter(records()), None))
            self.assertEqual(first["state"], "pending")
            # Send a second card without waiting, then dismiss it in the TUI.
            press(b"\x07")
            press(b"\x1b[B")
            press(b"\r")
            press(b"e")
            press(b"\x1b[18~")  # F7 selects the complete JSON document.
            payload = deepcopy(catalog.CATALOG[1]["notification"])
            payload["title"] = "Edited in the real Textual terminal"
            press(b"\x1b[200~" + json.dumps(payload).encode() + b"\x1b[201~")
            press(b"\x13")
            press(b"s")
            second = wait(
                lambda: next((item for item in records() if item["id"] != first["id"]), None)
            )
            self.assertEqual(second["kind"], "success")
            self.assertEqual(second["title"], payload["title"])
            press(b"\t")
            press(b"\x1b[B")
            press(b"d")
            wait(
                lambda: any(
                    item["id"] == second["id"] and item["state"] == "cancelled"
                    for item in records()
                )
            )
            press(b"\x1b[A")
            press(b"d")
            wait(
                lambda: all(
                    item["state"] not in testing.ACTIVE
                    for item in testing.read_json(state / "history.json")["records"].values()
                )
            )
            os.write(master, b"q")
            deadline = time.monotonic() + 5
            while child.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break  # A PTY reports EIO when its slave closes.
            child.wait(timeout=1)
            self.assertEqual(child.returncode, 0, output.decode(errors="replace"))
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            os.close(master)

    def test_service_restart_recovers_without_replaying_old_bubbles(self):
        item = self.backend.send(catalog.find_preset("command"))
        old_instance = self.backend.session.instance_id
        self.stop_service()
        with self.assertRaises(testing.PluginError):
            self.backend.refresh()
        self.start_service()
        self.enable_testing()
        self.backend.refresh()
        self.assertNotEqual(old_instance, self.backend.session.instance_id)
        self.assertEqual(self.backend.snapshot()["pending"], [])
        self.assertEqual(self.history.data["records"][item["id"]]["state"], "unavailable")
        fresh = self.backend.send(catalog.find_preset("typed-input"))
        self.assertEqual(fresh["state"], "pending")
        self.assertEqual(len(self.backend.session.list().result(timeout=3)), 1)

    def test_plugin_restart_resumes_pending_bubbles_and_credentials_are_private(self):
        item = self.backend.send(catalog.find_preset("text"))
        credentials = deepcopy(self.history.data["resume"])
        self.backend.close()
        self.history.close()
        self.history = testing.History(self.state.name)
        self.backend = testing.Backend(self.file, self.history)
        self.backend.refresh()
        self.assertEqual(self.backend.session.resume_credentials, credentials)
        self.assertEqual(self.backend.snapshot()["pending"][0]["id"], item["id"])
        if os.name != "nt":
            self.assertEqual(self.history.file.stat().st_mode & 0o777, 0o600)
        resolved = self.backend.resolve(item["id"], {"value": "Resumed answer"})
        self.assertEqual(resolved["state"], "resolved")


if __name__ == "__main__":
    unittest.main()
