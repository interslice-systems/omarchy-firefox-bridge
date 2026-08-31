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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from omarchy_firefox_bridge.socket_server import BridgeSocketServer  # noqa: E402


class SocketServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.xdg_runtime = Path(self.temp.name) / "xdg-runtime"
        self.xdg_runtime.mkdir()
        self.runtime = self.xdg_runtime / "omarchy-firefox-bridge"
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
        self.assertFalse(self.thread.is_alive())
        self.temp.cleanup()

    def exchange(self, payload: bytes) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(str(self.server.path))
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            with client.makefile("rb") as stream:
                return json.loads(stream.readline())

    def assert_server_rejected(self, directory: Path) -> None:
        server = None
        try:
            server = BridgeSocketServer(directory, lambda request: request)
        except RuntimeError:
            return
        except Exception as error:
            self.fail(f"wrong rejection type: {type(error).__name__}")
        finally:
            if server is not None:
                server.close()
        self.fail("unsafe runtime path was accepted")

    def test_runtime_and_socket_are_owner_only(self):
        self.assertEqual(stat.S_IMODE(self.runtime.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.server.path.stat().st_mode), 0o600)

    def test_runtime_parent_must_exist_as_a_real_owned_directory(self):
        missing = Path(self.temp.name) / "missing" / "omarchy-firefox-bridge"
        self.assert_server_rejected(missing)
        self.assertFalse(missing.parent.exists())

        target = Path(self.temp.name) / "target"
        target.mkdir(mode=0o755)
        link = Path(self.temp.name) / "runtime-link"
        link.symlink_to(target, target_is_directory=True)
        self.assert_server_rejected(link / "omarchy-firefox-bridge")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
        self.assertFalse((target / "omarchy-firefox-bridge").exists())

        with patch(
            "omarchy_firefox_bridge.socket_server.os.getuid",
            return_value=os.getuid() + 1,
        ):
            self.assert_server_rejected(Path(self.temp.name) / "unowned-check")

    def test_application_runtime_component_refuses_symlinks_and_non_directories(self):
        target = Path(self.temp.name) / "app-target"
        target.mkdir(mode=0o755)
        linked = Path(self.temp.name) / "linked-app"
        linked.symlink_to(target, target_is_directory=True)
        self.assert_server_rejected(linked)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

        regular = Path(self.temp.name) / "regular-app"
        regular.write_text("keep")
        self.assert_server_rejected(regular)
        self.assertEqual(regular.read_text(), "keep")

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

    def test_rejects_non_finite_and_recursive_json_without_stopping(self):
        for payload in (
            b'{"value":NaN}\n',
            b'{"value":Infinity}\n',
            b'{"value":-Infinity}\n',
        ):
            with self.subTest(payload=payload[:40]):
                self.assertEqual(
                    self.exchange(payload),
                    {"ok": False, "error": "invalid-request"},
                )
        with patch(
            "omarchy_firefox_bridge.socket_server._decode_request",
            side_effect=RecursionError("too deep"),
        ):
            self.assertEqual(
                self.exchange(b'{"value":[]}\n'),
                {"ok": False, "error": "invalid-request"},
            )
        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": True, "echo": {"action": "tabs"}},
        )

    def test_handler_and_serialization_failures_return_bridge_error_and_survive(self):
        def fail(_request):
            raise RuntimeError("tab title must not escape")

        self.server.handler = fail
        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": False, "error": "bridge-error"},
        )

        cyclic = {"ok": True}
        cyclic["cycle"] = cyclic
        for response in (cyclic, {"ok": True, "value": float("nan")}, {"bad": object()}):
            with self.subTest(response_type=type(response.get("bad"))):
                self.server.handler = lambda _request, response=response: response
                self.assertEqual(
                    self.exchange(b'{"action":"tabs"}\n'),
                    {"ok": False, "error": "bridge-error"},
                )

        self.server.handler = lambda request: {"ok": True, "echo": request}
        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": True, "echo": {"action": "tabs"}},
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
        time.sleep(0.05)
        started = time.monotonic()
        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": True, "echo": {"action": "tabs"}},
        )
        self.assertLess(time.monotonic() - started, 0.3)
        blocker.close()

    def test_client_worker_count_is_bounded_and_workers_are_daemons(self):
        blockers = []
        try:
            for _ in range(20):
                blocker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                blocker.settimeout(0.2)
                try:
                    blocker.connect(str(self.server.path))
                except OSError:
                    blocker.close()
                else:
                    blockers.append(blocker)
            time.sleep(0.1)
            with self.server._state_lock:
                workers = list(self.server._workers)
            self.assertGreater(len(workers), 0)
            self.assertLessEqual(len(workers), 8)
            self.assertTrue(all(worker.daemon for worker in workers))
        finally:
            for blocker in blockers:
                blocker.close()

    def test_worker_start_failure_does_not_stop_the_accept_loop(self):
        real_start = threading.Thread.start
        failed = threading.Event()

        def fail_one_worker_start(thread):
            if thread.name == "omarchy-firefox-bridge-client" and not failed.is_set():
                failed.set()
                raise RuntimeError("thread unavailable")
            return real_start(thread)

        with patch.object(threading.Thread, "start", fail_one_worker_start):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self.server.path))
                client.sendall(b'{"action":"tabs"}\n')
            self.assertTrue(failed.wait(timeout=0.5))

        self.assertEqual(
            self.exchange(b'{"action":"tabs"}\n'),
            {"ok": True, "echo": {"action": "tabs"}},
        )

    def test_shutdown_waits_for_bounded_handlers_and_terminates_workers(self):
        entered = threading.Event()

        def bounded_handler(_request):
            entered.set()
            time.sleep(0.35)
            return {"ok": True}

        self.server.handler = bounded_handler
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(self.server.path))
        client.sendall(b'{"action":"tabs"}\n')
        self.assertTrue(entered.wait(timeout=0.5))
        with self.server._state_lock:
            workers = list(self.server._workers)
        self.assertEqual(len(workers), 1)

        self.stop.set()
        started = time.monotonic()
        self.server.close()
        elapsed = time.monotonic() - started
        self.thread.join(timeout=1)
        client.close()
        self.assertLess(elapsed, 0.6)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(all(not worker.is_alive() for worker in workers))

    def test_refuses_to_unlink_an_active_socket(self):
        with self.assertRaisesRegex(RuntimeError, "already active"):
            BridgeSocketServer(self.runtime, lambda request: request)

    def test_startup_does_not_unlink_a_replacement_raced_into_the_socket_path(self):
        directory = Path(self.temp.name) / "race-app"
        directory.mkdir()
        path = directory / "bridge.sock"
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()
        real_rename = os.rename
        replaced = False

        def replace_before_rename(source, target, **kwargs):
            nonlocal replaced
            if not replaced and source == "bridge.sock":
                replaced = True
                path.unlink()
                path.write_text("replacement")
            return real_rename(source, target, **kwargs)

        with patch(
            "omarchy_firefox_bridge.socket_server.os.rename",
            side_effect=replace_before_rename,
        ), self.assertRaises(RuntimeError):
            BridgeSocketServer(directory, lambda request: request)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(), "replacement")

    def test_bind_publication_never_replaces_a_raced_socket_path(self):
        directory = Path(self.temp.name) / "publish-race-app"
        directory.mkdir()
        path = directory / "bridge.sock"
        from omarchy_firefox_bridge import socket_server as socket_server_module

        real_rename_noreplace = socket_server_module._rename_noreplace
        raced = False

        def race_publication(directory_fd, source, target):
            nonlocal raced
            if source.startswith(".bridge.sock.bind-") and not raced:
                raced = True
                path.write_text("replacement")
            return real_rename_noreplace(directory_fd, source, target)

        server = None
        try:
            with patch(
                "omarchy_firefox_bridge.socket_server._rename_noreplace",
                side_effect=race_publication,
            ), self.assertRaises(RuntimeError):
                server = BridgeSocketServer(directory, lambda request: request)
        finally:
            if server is not None:
                server.close()
        self.assertTrue(raced)
        self.assertEqual(path.read_text(), "replacement")
        self.assertEqual(list(directory.glob(".bridge.sock.bind-*")), [])

    def test_close_refuses_non_socket_and_active_socket_replacements(self):
        self.server.path.unlink()
        self.server.path.write_text("replacement")
        with self.assertRaises(RuntimeError):
            self.server.close()
        self.assertEqual(self.server.path.read_text(), "replacement")

    def test_close_does_not_unlink_an_active_successor_socket(self):
        self.server.path.unlink()
        successor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        successor.bind(str(self.server.path))
        successor.listen(1)
        try:
            with self.assertRaises(RuntimeError):
                self.server.close()
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(str(self.server.path))
            finally:
                probe.close()
        finally:
            successor.close()
            self.server.path.unlink(missing_ok=True)

    def test_concurrent_close_calls_are_synchronized(self):
        errors = []

        def close():
            try:
                self.server.close()
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        closers = [threading.Thread(target=close) for _ in range(8)]
        for closer in closers:
            closer.start()
        for closer in closers:
            closer.join(timeout=1)
            self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
