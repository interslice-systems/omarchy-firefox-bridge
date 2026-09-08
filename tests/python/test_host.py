from io import BytesIO
import queue
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.host import (  # noqa: E402
    EXTENSION_TIMEOUT,
    NativeBridge,
    main,
    native_reader,
)
from omarchy_firefox_bridge.protocol import validate_snapshot  # noqa: E402


class RecordingWriter:
    def __init__(self):
        self.messages = queue.Queue()

    def send(self, message):
        self.messages.put(message)


def tab(tab_id=7, window_id=4):
    return {
        "tabId": tab_id,
        "windowId": window_id,
        "index": 0,
        "title": "A",
        "displayUrl": "a.test/",
        "favicon": "",
        "active": True,
    }


class NativeBridgeTest(unittest.TestCase):
    def setUp(self):
        self.writer = RecordingWriter()
        self.bridge = NativeBridge(self.writer)

    def complete_request(self, response_factory):
        request = self.writer.messages.get(timeout=1)
        response = response_factory(request)
        self.bridge.receive(response)
        return request

    def test_tabs_request_correlates_response_and_updates_id_allowlist(self):
        completer = threading.Thread(
            target=self.complete_request,
            args=(lambda request: {
                "type": "tabs.result",
                "requestId": request["requestId"],
                "tabs": [tab()],
            },),
        )
        completer.start()
        response = self.bridge.handle_client({"action": "tabs"})
        completer.join(timeout=1)
        self.assertEqual(response, {"ok": True, "tabs": [tab()]})
        self.assertEqual(self.bridge.snapshot_ids, frozenset({(4, 7)}))

    def test_older_request_cannot_replace_a_newer_installed_snapshot(self):
        older_validating = threading.Event()
        release_older = threading.Event()
        results = {}

        def delayed_validation(value):
            if value[0]["tabId"] == 7:
                older_validating.set()
                release_older.wait(timeout=1)
            return validate_snapshot(value)

        workers = {
            label: threading.Thread(
                target=lambda label=label: results.setdefault(
                    label, self.bridge.handle_client({"action": "tabs"})
                )
            )
            for label in ("older", "newer")
        }
        with patch(
            "omarchy_firefox_bridge.host.validate_snapshot",
            side_effect=delayed_validation,
        ):
            workers["older"].start()
            older_request = self.writer.messages.get(timeout=1)
            self.bridge.receive({
                "type": "tabs.result",
                "requestId": older_request["requestId"],
                "tabs": [tab(tab_id=7)],
            })
            self.assertTrue(older_validating.wait(timeout=1))

            workers["newer"].start()
            newer_request = self.writer.messages.get(timeout=1)
            self.bridge.receive({
                "type": "tabs.result",
                "requestId": newer_request["requestId"],
                "tabs": [tab(tab_id=8)],
            })
            workers["newer"].join(timeout=1)
            self.assertFalse(workers["newer"].is_alive())

            release_older.set()
            workers["older"].join(timeout=1)
            self.assertFalse(workers["older"].is_alive())

        self.assertEqual(self.bridge.snapshot_ids, frozenset({(4, 8)}))

    def test_activation_requires_latest_snapshot_and_extension_confirmation(self):
        self.bridge.snapshot_ids = frozenset({(4, 7)})
        completer = threading.Thread(
            target=self.complete_request,
            args=(lambda request: {
                "type": "tabs.activated",
                "requestId": request["requestId"],
                "ok": True,
            },),
        )
        completer.start()
        self.assertEqual(
            self.bridge.handle_client({"action": "activate", "windowId": 4, "tabId": 7}),
            {"ok": True},
        )
        completer.join(timeout=1)
        self.assertEqual(
            self.bridge.handle_client({"action": "activate", "windowId": 4, "tabId": 8}),
            {"ok": False, "error": "stale-tab"},
        )

    def test_timeout_and_malformed_response_fail_closed_without_replacing_ids(self):
        self.bridge.timeout = 0.01
        self.bridge.snapshot_ids = frozenset({(4, 7)})
        self.assertEqual(
            self.bridge.handle_client({"action": "tabs"}),
            {"ok": False, "error": "timeout"},
        )
        self.assertEqual(self.bridge.snapshot_ids, frozenset({(4, 7)}))
        timed_out_request = self.writer.messages.get_nowait()
        self.assertEqual(timed_out_request["type"], "tabs.list")

        completer = threading.Thread(
            target=self.complete_request,
            args=(lambda request: {
                "type": "tabs.result",
                "requestId": request["requestId"],
                "tabs": [{**tab(), "url": "https://must-not-cross.test/"}],
            },),
        )
        completer.start()
        self.assertEqual(
            self.bridge.handle_client({"action": "tabs"}),
            {"ok": False, "error": "bridge-error"},
        )
        completer.join(timeout=1)
        self.assertEqual(self.bridge.snapshot_ids, frozenset({(4, 7)}))

    def test_no_response_uses_exact_wait_and_returns_within_client_budget(self):
        self.assertEqual(EXTENSION_TIMEOUT, 0.4)
        started = time.monotonic()
        response = self.bridge.handle_client({"action": "tabs"})
        elapsed = time.monotonic() - started

        request = self.writer.messages.get_nowait()
        self.assertEqual(request["type"], "tabs.list")
        self.assertEqual(response, {"ok": False, "error": "timeout"})
        self.assertGreaterEqual(elapsed, EXTENSION_TIMEOUT)
        self.assertLess(elapsed, 0.5)

    def test_invalid_client_actions_never_reach_native_messaging(self):
        for request in (
            {},
            {"action": "navigate", "url": "https://example.com"},
            {"action": "activate", "windowId": 4, "tabId": "7"},
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    self.bridge.handle_client(request),
                    {"ok": False, "error": "invalid-request"},
                )
        self.assertTrue(self.writer.messages.empty())

    def test_duplicate_native_response_cannot_block_the_reader(self):
        request_id = "duplicate-response"
        response_queue = queue.Queue(maxsize=1)
        response_queue.put({"type": "tabs.result", "requestId": request_id, "tabs": []})
        self.bridge.pending[request_id] = (1, response_queue)
        receiver = threading.Thread(
            target=self.bridge.receive,
            args=({"type": "tabs.result", "requestId": request_id, "tabs": []},),
        )
        receiver.start()
        receiver.join(timeout=0.1)
        self.assertFalse(receiver.is_alive())

    def test_two_pending_requests_correlate_reversed_responses(self):
        results = {}

        def make_request(label):
            results[label] = self.bridge.request({"type": "probe", "label": label})

        workers = [
            threading.Thread(target=make_request, args=(label,))
            for label in ("first", "second")
        ]
        for worker in workers:
            worker.start()
        requests = [self.writer.messages.get(timeout=1) for _ in workers]
        by_label = {request["label"]: request for request in requests}
        for label in ("second", "first"):
            self.bridge.receive({
                "type": "probe.result",
                "requestId": by_label[label]["requestId"],
                "label": label,
            })
        for worker in workers:
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results["first"][1]["label"], "first")
        self.assertEqual(results["second"][1]["label"], "second")

    def test_pending_request_ids_retry_collisions_without_overwriting(self):
        results = {}

        def make_request(label):
            try:
                results[label] = self.bridge.request({"type": "probe", "label": label})
            except ConnectionError:
                results[label] = "unavailable"

        workers = {
            label: threading.Thread(target=make_request, args=(label,))
            for label in ("first", "second")
        }
        with patch(
            "omarchy_firefox_bridge.host.secrets.token_hex",
            side_effect=("same-token", "same-token", "different-token"),
        ):
            workers["first"].start()
            first_request = self.writer.messages.get(timeout=1)
            workers["second"].start()
            second_request = self.writer.messages.get(timeout=1)

        distinct = first_request["requestId"] != second_request["requestId"]
        if distinct:
            for request in (first_request, second_request):
                self.bridge.receive({
                    "type": "probe.result",
                    "requestId": request["requestId"],
                    "label": request["label"],
                })
        else:
            self.bridge.disconnect()
        for worker in workers.values():
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())

        self.assertTrue(distinct)
        self.assertEqual(results["first"][1]["label"], "first")
        self.assertEqual(results["second"][1]["label"], "second")

    def test_native_eof_releases_pending_request_and_closes_server(self):
        class RecordingServer:
            closed = False

            def close(self):
                self.closed = True

        result = {}
        worker = threading.Thread(
            target=lambda: result.setdefault(
                "response", self.bridge.handle_client({"action": "tabs"})
            )
        )
        worker.start()
        self.writer.messages.get(timeout=1)
        stop = threading.Event()
        server = RecordingServer()
        native_reader(BytesIO(b""), self.bridge, stop, server)
        worker.join(timeout=0.1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result["response"], {"ok": False, "error": "unavailable"})
        self.assertTrue(stop.is_set())
        self.assertTrue(server.closed)


class NativeHostStartupTest(unittest.TestCase):
    class RecordingServer:
        def __init__(self, _directory, handler):
            self.handler = handler
            self.closed = False

        def close(self):
            self.closed = True

        def serve_forever(self, _stop):
            raise AssertionError("startup failure must not enter the server loop")

    def test_watcher_start_failure_cleans_up_started_reader_and_server(self):
        reader_started = threading.Event()
        reader_stopped = threading.Event()
        watcher_started = threading.Event()
        server = None

        def fake_reader(_stream, _bridge, stop, _server):
            reader_started.set()
            stop.wait(timeout=0.2)
            reader_stopped.set()

        def fake_watcher(_stop, _changed):
            watcher_started.set()

        def make_server(directory, handler):
            nonlocal server
            server = self.RecordingServer(directory, handler)
            return server

        real_start = threading.Thread.start

        def fail_watcher_start(thread):
            if thread._target is fake_watcher:
                raise RuntimeError("watcher start failed")
            return real_start(thread)

        with (
            patch("omarchy_firefox_bridge.host.NativeWriter", return_value=RecordingWriter()),
            patch("omarchy_firefox_bridge.host.runtime_directory", return_value=Path("/tmp")),
            patch("omarchy_firefox_bridge.host.BridgeSocketServer", side_effect=make_server),
            patch("omarchy_firefox_bridge.host.native_reader", new=fake_reader),
            patch("omarchy_firefox_bridge.host.watch_theme", new=fake_watcher),
            patch.object(threading.Thread, "start", new=fail_watcher_start),
            self.assertRaisesRegex(RuntimeError, "watcher start failed"),
        ):
            main()

        self.assertTrue(reader_started.is_set())
        self.assertTrue(reader_stopped.wait(timeout=0.1))
        self.assertFalse(watcher_started.is_set())
        self.assertIsNotNone(server)
        self.assertTrue(server.closed)
        self.assertFalse(server.handler.__self__.connected)

    def test_initial_theme_send_failure_stops_both_threads_and_closes_server(self):
        reader_started = threading.Event()
        reader_stopped = threading.Event()
        watcher_started = threading.Event()
        watcher_stopped = threading.Event()
        server = None

        class FailingWriter:
            def send(self, _message):
                raise RuntimeError("initial theme send failed")

        def fake_reader(_stream, _bridge, stop, _server):
            reader_started.set()
            stop.wait(timeout=0.2)
            reader_stopped.set()

        def fake_watcher(stop, _changed):
            watcher_started.set()
            stop.wait(timeout=0.2)
            watcher_stopped.set()

        def make_server(directory, handler):
            nonlocal server
            server = self.RecordingServer(directory, handler)
            return server

        with (
            patch("omarchy_firefox_bridge.host.NativeWriter", return_value=FailingWriter()),
            patch("omarchy_firefox_bridge.host.runtime_directory", return_value=Path("/tmp")),
            patch("omarchy_firefox_bridge.host.BridgeSocketServer", side_effect=make_server),
            patch("omarchy_firefox_bridge.host.native_reader", new=fake_reader),
            patch("omarchy_firefox_bridge.host.watch_theme", new=fake_watcher),
            patch(
                "omarchy_firefox_bridge.host.read_theme_message",
                return_value={"type": "theme", "theme": {}},
            ),
            self.assertRaisesRegex(RuntimeError, "initial theme send failed"),
        ):
            main()

        self.assertTrue(reader_started.is_set())
        self.assertTrue(watcher_started.is_set())
        self.assertTrue(reader_stopped.wait(timeout=0.1))
        self.assertTrue(watcher_stopped.wait(timeout=0.1))
        self.assertIsNotNone(server)
        self.assertTrue(server.closed)
        self.assertFalse(server.handler.__self__.connected)


class OpenActionTests(unittest.TestCase):
    def setUp(self):
        self.writer = RecordingWriter()

    def handle(self, request, response):
        bridge = NativeBridge(self.writer, timeout=0.05)
        bridge.request = lambda message: (1, response) if response is not None else None
        return bridge.handle_client(request)

    def test_forwards_a_valid_open_and_reports_success(self):
        sent = {}
        bridge = NativeBridge(self.writer, timeout=0.05)

        def capture(message):
            sent.update(message)
            return (1, {"type": "tabs.opened", "ok": True, "tabId": 5})

        bridge.request = capture
        result = bridge.handle_client({
            "action": "open",
            "url": "https://e.com/",
            "toplevelTitle": "T",
            "group": {"title": "oracle", "color": "yellow"},
        })
        self.assertEqual(result, {"ok": True})
        self.assertEqual(sent["type"], "tabs.open")
        self.assertEqual(sent["group"], {"title": "oracle", "color": "yellow"})
        self.assertNotIn("action", sent)

    def test_rejects_an_invalid_open_without_contacting_the_extension(self):
        bridge = NativeBridge(self.writer, timeout=0.05)

        def explode(message):
            raise AssertionError("must not reach the extension")

        bridge.request = explode
        self.assertEqual(
            bridge.handle_client({"action": "open", "url": "file:///x", "toplevelTitle": "T"}),
            {"ok": False, "error": "invalid-request"},
        )

    def test_passes_through_only_allowlisted_extension_errors(self):
        for error, expected in (
            ("no-window", "no-window"),
            ("ambiguous-window", "ambiguous-window"),
            ("create-failed", "create-failed"),
            ("something-invented", "bridge-error"),
            (None, "bridge-error"),
        ):
            with self.subTest(error=error):
                response = {"type": "tabs.opened", "ok": False, "error": error}
                self.assertEqual(
                    self.handle(
                        {"action": "open", "url": "https://e.com/", "toplevelTitle": "T"},
                        response,
                    ),
                    {"ok": False, "error": expected},
                )

    def test_reports_timeout_when_the_extension_does_not_answer(self):
        self.assertEqual(
            self.handle(
                {"action": "open", "url": "https://e.com/", "toplevelTitle": "T"}, None
            ),
            {"ok": False, "error": "timeout"},
        )

    def test_rejects_unknown_keys_on_the_open_action(self):
        self.assertEqual(
            self.handle(
                {"action": "open", "url": "https://e.com/", "toplevelTitle": "T", "x": 1},
                {"type": "tabs.opened", "ok": True},
            ),
            {"ok": False, "error": "invalid-request"},
        )


if __name__ == "__main__":
    unittest.main()
