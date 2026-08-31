"""Owner-only local Unix socket server for bridge clients."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import threading
from typing import Callable

MAX_CLIENT_REQUEST = 4096


def runtime_directory() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if not value:
        raise RuntimeError("XDG_RUNTIME_DIR is required")
    return Path(value) / "omarchy-firefox-bridge"


class BridgeSocketServer:
    def __init__(self, directory: Path, handler: Callable[[dict], dict]) -> None:
        self.directory = directory
        self.path = directory / "bridge.sock"
        self.handler = handler
        self.closed = False
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        self._remove_stale_socket()
        old_umask = os.umask(0o177)
        try:
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.bind(str(self.path))
        finally:
            os.umask(old_umask)
        self.path.chmod(0o600)
        self.socket.listen(8)
        self.socket.settimeout(0.1)

    def _remove_stale_socket(self) -> None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("refusing to replace unowned or non-socket runtime path")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.05)
        try:
            probe.connect(str(self.path))
        except (ConnectionRefusedError, FileNotFoundError):
            self.path.unlink()
        else:
            raise RuntimeError("bridge socket is already active")
        finally:
            probe.close()

    def _serve_client(self, client: socket.socket) -> None:
        response = {"ok": False, "error": "invalid-request"}
        client.settimeout(0.5)
        try:
            stream = client.makefile("rb")
            raw = stream.readline(MAX_CLIENT_REQUEST + 1)
            if raw.endswith(b"\n") and len(raw) <= MAX_CLIENT_REQUEST:
                request = json.loads(raw.decode("utf-8"))
                if isinstance(request, dict):
                    response = self.handler(request)
        except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError):
            response = {"ok": False, "error": "invalid-request"}
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            client.sendall(encoded + b"\n")
        except OSError:
            pass

    def serve_forever(self, stop: threading.Event) -> None:
        while not stop.is_set() and not self.closed:
            try:
                client, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                self._serve_client(client)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.socket.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
