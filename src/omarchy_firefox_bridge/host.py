"""Firefox-owned native host coordinating theme and local tab requests."""
from __future__ import annotations

import queue
import secrets
import sys
import threading
from typing import BinaryIO

from .framing import FrameError, NativeWriter, read_message
from .protocol import (
    ProtocolError,
    snapshot_ids,
    valid_request_id,
    validate_activation,
    validate_open_request,
    validate_snapshot,
)
from .socket_server import BridgeSocketServer, runtime_directory
from .theme import read_theme_message, watch_theme

EXTENSION_TIMEOUT = 0.4

# Errors the extension may report for tabs.open. Anything else becomes
# bridge-error, so an unexpected string cannot be relayed to the caller.
OPEN_ERRORS = frozenset({"no-window", "ambiguous-window", "invalid-request", "create-failed"})


def log(message: str) -> None:
    print(f"[omarchy-firefox-bridge] {message}", file=sys.stderr, flush=True)


class NativeBridge:
    def __init__(self, writer: NativeWriter, timeout: float = EXTENSION_TIMEOUT) -> None:
        self.writer = writer
        self.timeout = timeout
        self.pending: dict[str, tuple[int, queue.Queue]] = {}
        self.pending_lock = threading.Lock()
        self.connected = True
        self.request_generation = 0
        self.snapshot_ids: frozenset[tuple[int, int]] = frozenset()
        self.snapshot_generation = 0
        self.snapshot_lock = threading.Lock()

    def receive(self, message: dict) -> None:
        request_id = message.get("requestId")
        if not valid_request_id(request_id):
            return
        with self.pending_lock:
            pending = self.pending.get(request_id)
        if pending is not None:
            generation, target = pending
            try:
                target.put_nowait((generation, message))
            except queue.Full:
                pass

    def disconnect(self) -> None:
        with self.pending_lock:
            self.connected = False
            targets = [target for _, target in self.pending.values()]
        for target in targets:
            try:
                target.put_nowait(None)
            except queue.Full:
                pass

    def request(self, message: dict) -> tuple[int, dict] | None:
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        with self.pending_lock:
            if not self.connected:
                raise ConnectionError("native extension disconnected")
            while True:
                request_id = secrets.token_hex(16)
                if request_id not in self.pending:
                    break
            self.request_generation += 1
            generation = self.request_generation
            self.pending[request_id] = (generation, response_queue)
        try:
            self.writer.send({**message, "requestId": request_id})
            response = response_queue.get(timeout=self.timeout)
            if response is None:
                raise ConnectionError("native extension disconnected")
            return response
        except queue.Empty:
            return None
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)

    def handle_client(self, request: dict) -> dict:
        action = request.get("action")
        if action == "tabs" and frozenset(request) == {"action"}:
            try:
                result = self.request({"type": "tabs.list"})
            except ConnectionError:
                return {"ok": False, "error": "unavailable"}
            if result is None:
                return {"ok": False, "error": "timeout"}
            generation, response = result
            try:
                if response.get("type") != "tabs.result":
                    raise ProtocolError("wrong response type")
                tabs = validate_snapshot(response.get("tabs"))
            except ProtocolError:
                return {"ok": False, "error": "bridge-error"}
            with self.snapshot_lock:
                if generation > self.snapshot_generation:
                    self.snapshot_ids = snapshot_ids(tabs)
                    self.snapshot_generation = generation
            return {"ok": True, "tabs": tabs}
        if action == "activate" and frozenset(request) == {"action", "windowId", "tabId"}:
            try:
                window_id, tab_id = validate_activation(request["windowId"], request["tabId"])
            except ProtocolError:
                return {"ok": False, "error": "invalid-request"}
            with self.snapshot_lock:
                allowed = (window_id, tab_id) in self.snapshot_ids
            if not allowed:
                return {"ok": False, "error": "stale-tab"}
            try:
                result = self.request(
                    {"type": "tabs.activate", "windowId": window_id, "tabId": tab_id}
                )
            except ConnectionError:
                return {"ok": False, "error": "unavailable"}
            if result is None:
                return {"ok": False, "error": "timeout"}
            _, response = result
            if response.get("type") != "tabs.activated" or response.get("ok") is not True:
                return {"ok": False, "error": "bridge-error"}
            return {"ok": True}
        if action == "open" and frozenset(request) in (
            frozenset({"action", "url", "toplevelTitle"}),
            frozenset({"action", "url", "toplevelTitle", "group"}),
        ):
            try:
                payload = validate_open_request(
                    {key: value for key, value in request.items() if key != "action"}
                )
            except ProtocolError:
                return {"ok": False, "error": "invalid-request"}
            try:
                result = self.request({"type": "tabs.open", **payload})
            except ConnectionError:
                return {"ok": False, "error": "unavailable"}
            if result is None:
                return {"ok": False, "error": "timeout"}
            _, response = result
            if response.get("type") != "tabs.opened":
                return {"ok": False, "error": "bridge-error"}
            if response.get("ok") is True:
                return {"ok": True}
            error = response.get("error")
            return {"ok": False, "error": error if error in OPEN_ERRORS else "bridge-error"}
        return {"ok": False, "error": "invalid-request"}


def native_reader(
    stream: BinaryIO,
    bridge: NativeBridge,
    stop: threading.Event,
    server: BridgeSocketServer,
) -> None:
    try:
        while not stop.is_set():
            bridge.receive(read_message(stream))
    except (FrameError, EOFError):
        pass
    finally:
        bridge.disconnect()
        stop.set()
        server.close()


def main() -> int:
    stop = threading.Event()
    writer = NativeWriter(sys.stdout.buffer)
    bridge = NativeBridge(writer)
    server = BridgeSocketServer(runtime_directory(), bridge.handle_client)
    reader = None
    watcher = None
    reader_started = False
    watcher_started = False
    try:
        reader = threading.Thread(
            target=native_reader,
            args=(sys.stdin.buffer, bridge, stop, server),
            daemon=True,
        )
        reader.start()
        reader_started = True

        def push_theme() -> None:
            message = read_theme_message()
            if message is not None:
                writer.send(message)

        watcher = threading.Thread(target=watch_theme, args=(stop, push_theme), daemon=True)
        watcher.start()
        watcher_started = True
        push_theme()
        log("started")
        server.serve_forever(stop)
    finally:
        stop.set()
        bridge.disconnect()
        server.close()
        if reader_started:
            reader.join(timeout=1.5)
        if watcher_started:
            watcher.join(timeout=1.5)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        log(f"fatal {type(error).__name__}")
        raise SystemExit(1)
