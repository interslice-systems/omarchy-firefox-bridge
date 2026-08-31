import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.socket_server import BridgeSocketServer  # noqa: E402


class SocketServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name) / "runtime"
        self.stop = threading.Event()
        self.server = BridgeSocketServer(
            self.runtime,
            lambda request: {"ok": True, "echo": request},
        )
        self.thread = threading.Thread(target=self.server.serve_forever, args=(self.stop,))
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        self.server.close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def exchange(self, payload: bytes) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(self.server.path))
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            return json.loads(client.makefile("rb").readline())

    def test_runtime_and_socket_are_owner_only(self):
        self.assertEqual(stat.S_IMODE(self.runtime.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.server.path.stat().st_mode), 0o600)

    def test_serves_one_request_and_one_response(self):
        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": True, "echo": {"action": "tabs"}},
        )

    def test_rejects_malformed_and_oversized_requests(self):
        self.assertEqual(self.exchange(b"not-json\n"), {"ok": False, "error": "invalid-request"})
        self.assertEqual(
            self.exchange(b"x" * 4097),
            {"ok": False, "error": "invalid-request"},
        )

    def test_client_disconnect_does_not_stop_the_server(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.server.path))
        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": True, "echo": {"action": "tabs"}},
        )

    def test_silent_client_cannot_wedge_the_server(self):
        blocker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        blocker.connect(str(self.server.path))
        time.sleep(0.55)
        blocker.close()
        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": True, "echo": {"action": "tabs"}},
        )

    def test_refuses_to_unlink_an_active_socket(self):
        with self.assertRaisesRegex(RuntimeError, "already active"):
            BridgeSocketServer(self.runtime, lambda request: request)


if __name__ == "__main__":
    unittest.main()
