from io import BytesIO
import queue
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.host import NativeBridge, native_reader  # noqa: E402


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
        self.bridge.pending[request_id] = response_queue
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
        self.assertEqual(results["first"]["label"], "first")
        self.assertEqual(results["second"]["label"], "second")

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


if __name__ == "__main__":
    unittest.main()
