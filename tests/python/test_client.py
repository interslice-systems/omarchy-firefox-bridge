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
    def serve_once(self, payloads, delay=0):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "server.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)

        def send_response():
            connection, _ = server.accept()
            with connection:
                connection.recv(4096)
                try:
                    for payload in payloads:
                        connection.sendall(payload)
                        if delay:
                            time.sleep(delay)
                except OSError:
                    pass

        thread = threading.Thread(target=send_response)
        thread.start()
        return directory, path, server, thread

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

    def test_slow_drip_cannot_extend_the_500ms_response_deadline(self):
        directory, path, server, thread = self.serve_once(
            [bytes([byte]) for byte in b'{"ok":true}\n'],
            delay=0.1,
        )
        try:
            started = time.monotonic()
            response = request(path, {"action": "tabs"})
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.45)
            self.assertLess(elapsed, 0.75)
            self.assertEqual(response, {"ok": False, "error": "timeout"})
        finally:
            server.close()
            thread.join(timeout=2)
            directory.cleanup()

    def test_rejects_non_finite_and_recursive_responses(self):
        for payload in (b'{"value":NaN}\n', b'{"value":Infinity}\n'):
            with self.subTest(payload=payload[:30]):
                directory, path, server, thread = self.serve_once([payload])
                try:
                    self.assertEqual(
                        request(path, {"action": "tabs"}),
                        {"ok": False, "error": "bridge-error"},
                    )
                finally:
                    server.close()
                    thread.join(timeout=2)
                    directory.cleanup()

        directory, path, server, thread = self.serve_once([b'{"ok":true}\n'])
        try:
            with patch(
                "omarchy_firefox_bridge.client.json.loads",
                side_effect=RecursionError("too deep"),
            ):
                self.assertEqual(
                    request(path, {"action": "tabs"}),
                    {"ok": False, "error": "bridge-error"},
                )
        finally:
            server.close()
            thread.join(timeout=2)
            directory.cleanup()

    def test_request_contains_encoding_and_socket_creation_failures(self):
        cyclic = {"action": "tabs"}
        cyclic["cycle"] = cyclic
        self.assertEqual(
            request(Path("/unused"), cyclic),
            {"ok": False, "error": "bridge-error"},
        )
        self.assertEqual(
            request(Path("/unused"), {"value": float("nan")}),
            {"ok": False, "error": "bridge-error"},
        )
        with patch(
            "omarchy_firefox_bridge.client.socket.socket",
            side_effect=OSError("socket unavailable"),
        ):
            self.assertEqual(
                request(Path("/unused")),
                {"ok": False, "error": "unavailable"},
            )

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

    def test_activate_requires_positive_ascii_decimal_ids_and_exact_argv(self):
        invalid = (
            ["activate", "-1", "7"],
            ["activate", "+4", "7"],
            ["activate", "٤", "٧"],
            ["activate", "4"],
            ["activate", "4", "7", "extra"],
            ["tabs", "extra"],
            [],
        )
        for argv in invalid:
            with self.subTest(argv=argv):
                output = StringIO()
                with redirect_stdout(output):
                    code = main(argv)
                self.assertEqual(code, 2)
                self.assertEqual(
                    output.getvalue(),
                    '{"ok":false,"error":"invalid-request"}\n',
                )

    def test_main_contains_request_and_response_serialization_failures(self):
        cyclic = {"ok": True}
        cyclic["cycle"] = cyclic
        failures = (
            RuntimeError("runtime failure"),
            cyclic,
            {"ok": True, "value": float("nan")},
            {"ok": True, "value": object()},
        )
        for failure in failures:
            with self.subTest(failure=type(failure)), patch(
                "omarchy_firefox_bridge.client.runtime_directory",
                return_value=Path("/unused"),
            ), patch("omarchy_firefox_bridge.client.request") as mocked_request:
                if isinstance(failure, Exception):
                    mocked_request.side_effect = failure
                else:
                    mocked_request.return_value = failure
                output = StringIO()
                with redirect_stdout(output):
                    code = main(["tabs"])
                self.assertEqual(code, 3)
                self.assertEqual(
                    output.getvalue(),
                    '{"ok":false,"error":"bridge-error"}\n',
                )

    def test_stdout_failure_is_contained_as_exit_three(self):
        class BrokenStdout:
            def write(self, _value):
                raise OSError("stdout closed")

            def flush(self):
                raise OSError("stdout closed")

        with patch(
            "omarchy_firefox_bridge.client.sys.stdout",
            BrokenStdout(),
        ):
            self.assertEqual(main(["bad-command"]), 3)

    def test_stdout_encoding_failure_falls_back_to_ascii_bridge_error(self):
        class AsciiOnlyStdout:
            def __init__(self):
                self.output = ""

            def write(self, value):
                try:
                    value.encode("ascii")
                except UnicodeEncodeError:
                    raise
                self.output += value
                return len(value)

            def flush(self):
                pass

        stdout = AsciiOnlyStdout()
        with patch(
            "omarchy_firefox_bridge.client.runtime_directory",
            return_value=Path("/unused"),
        ), patch(
            "omarchy_firefox_bridge.client.request",
            return_value={"ok": True, "title": "café"},
        ), patch("omarchy_firefox_bridge.client.sys.stdout", stdout):
            self.assertEqual(main(["tabs"]), 3)
        self.assertEqual(stdout.output, '{"ok":false,"error":"bridge-error"}\n')

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
