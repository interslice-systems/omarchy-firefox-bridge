from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.client import main, request  # noqa: E402


class ClientTest(unittest.TestCase):
    def test_missing_socket_fails_immediately(self):
        started = time.monotonic()
        response = request(Path("/definitely/missing/bridge.sock"))
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(response, {"ok": False, "error": "unavailable"})

    def test_connected_silent_socket_times_out_within_500ms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silent.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path))
            server.listen(1)

            def stay_silent():
                connection, _ = server.accept()
                with connection:
                    connection.recv(4096)
                    time.sleep(1)

            thread = threading.Thread(target=stay_silent)
            thread.start()
            started = time.monotonic()
            response = request(path, {"action": "tabs"})
            elapsed = time.monotonic() - started
            server.close()
            thread.join(timeout=2)
            self.assertGreaterEqual(elapsed, 0.45)
            self.assertLess(elapsed, 0.75)
            self.assertEqual(response, {"ok": False, "error": "timeout"})

    def test_main_emits_one_json_object_and_exact_exit_codes(self):
        output = StringIO()
        with redirect_stdout(output):
            code = main(["activate", "bad", "7"])
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"ok": False, "error": "invalid-request"},
        )
        self.assertEqual(output.getvalue().count("\n"), 1)

        for response, expected in (({"ok": True, "tabs": []}, 0), ({"ok": False}, 3)):
            with self.subTest(response=response), patch(
                "omarchy_firefox_bridge.client.runtime_directory",
                return_value=Path("/unused"),
            ), patch(
                "omarchy_firefox_bridge.client.request",
                return_value=response,
            ):
                output = StringIO()
                with redirect_stdout(output):
                    code = main(["tabs"])
                self.assertEqual(code, expected)
                self.assertEqual(json.loads(output.getvalue()), response)

        output = StringIO()
        with redirect_stdout(output):
            code = main(["activate", "0", "7"])
        self.assertEqual(code, 2)

    def test_valid_commands_without_runtime_directory_exit_three(self):
        for argv in (["tabs"], ["activate", "4", "7"]):
            with self.subTest(argv=argv), patch(
                "omarchy_firefox_bridge.client.runtime_directory",
                side_effect=RuntimeError("XDG_RUNTIME_DIR is required"),
            ):
                output = StringIO()
                with redirect_stdout(output):
                    code = main(argv)
                self.assertEqual(code, 3)
                self.assertEqual(
                    json.loads(output.getvalue()),
                    {"ok": False, "error": "unavailable"},
                )


if __name__ == "__main__":
    unittest.main()
