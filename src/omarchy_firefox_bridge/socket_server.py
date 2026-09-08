"""Owner-only local Unix socket server for bridge clients."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import socket
import stat
import threading
import time
from typing import Callable

APPLICATION_DIRECTORY = "omarchy-firefox-bridge"
LOCK_NAME = "bridge.lock"
SOCKET_NAME = "bridge.sock"
# Budget (spec 2026-09-08 § Size budget): url 4096 code points x 4 bytes = 16384,
# toplevelTitle 1024 x 4 = 4096, group.title 64 x 4 = 256, requestId/type/color
# plus JSON structure ~300. Total ~21 KiB.
MAX_CLIENT_REQUEST = 24576
MAX_CLIENT_WORKERS = 8
CLIENT_READ_TIMEOUT = 0.5
SHUTDOWN_TIMEOUT = 0.5
INVALID_REQUEST = {"ok": False, "error": "invalid-request"}
BRIDGE_ERROR = {"ok": False, "error": "bridge-error"}

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_UMASK_LOCK = threading.Lock()


def _reject_json_constant(constant: str) -> None:
    raise ValueError(f"non-finite JSON constant: {constant}")


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IFMT(metadata.st_mode),
    )


def _validate_owned_directory(fd: int, description: str) -> None:
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise RuntimeError(f"{description} must be an owned real directory")


def _open_owned_directory(path: Path, description: str) -> int:
    try:
        fd = os.open(path, _DIRECTORY_FLAGS)
    except OSError as error:
        raise RuntimeError(f"{description} must be an owned real directory") from error
    try:
        _validate_owned_directory(fd, description)
    except Exception:
        os.close(fd)
        raise
    return fd


def _open_application_directory(directory: Path) -> int:
    parent_fd = _open_owned_directory(directory.parent, "XDG_RUNTIME_DIR")
    child_fd = None
    try:
        try:
            os.mkdir(directory.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise RuntimeError("cannot create bridge runtime directory") from error
        try:
            child_fd = os.open(directory.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            raise RuntimeError(
                "bridge runtime path must be an owned real directory"
            ) from error
        _validate_owned_directory(child_fd, "bridge runtime path")
        os.fchmod(child_fd, 0o700)
        result = child_fd
        child_fd = None
        return result
    finally:
        if child_fd is not None:
            os.close(child_fd)
        os.close(parent_fd)


def _open_lifecycle_lock(directory_fd: int) -> int:
    try:
        lock_fd = os.open(LOCK_NAME, _LOCK_FLAGS, 0o600, dir_fd=directory_fd)
    except OSError as error:
        raise RuntimeError("bridge lock must be an owned regular file") from error
    try:
        metadata = os.fstat(lock_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("bridge lock must be an owned regular file")
        os.fchmod(lock_fd, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("bridge socket is already active") from error
    except Exception:
        os.close(lock_fd)
        raise
    return lock_fd


def _compact_json(response: dict) -> bytes:
    return json.dumps(
        response,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_request(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_json_constant,
    )


def runtime_directory() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if not value:
        raise RuntimeError("XDG_RUNTIME_DIR is required")
    parent = Path(value)
    if not parent.is_absolute():
        raise RuntimeError("XDG_RUNTIME_DIR must be absolute")
    fd = _open_owned_directory(parent, "XDG_RUNTIME_DIR")
    os.close(fd)
    return parent / APPLICATION_DIRECTORY


class BridgeSocketServer:
    """Serve local clients under an owner-only advisory lifecycle lock.

    The lock serializes conforming bridge instances and protects against
    accidental replacement. Other same-UID processes are trusted and outside
    this filesystem threat boundary because they can mutate owned directories,
    ptrace the process, and race any pathname operation.

    Handlers must return a dict. The production host may spend up to 400 ms
    waiting for the extension and must complete the callback within the client's
    total 500 ms connected-response budget under normal scheduling. Python cannot
    safely cancel an arbitrary callback, so at most eight daemon workers are
    admitted and close waits one aggregate 500 ms budget. A violating handler
    cannot block process exit, but may remain alive until it returns.
    """

    def __init__(self, directory: Path, handler: Callable[[dict], dict]) -> None:
        self.directory = directory
        self.path = directory / SOCKET_NAME
        self.handler = handler
        self.closed = False
        self.socket: socket.socket | None = None
        self._directory_fd: int | None = None
        self._lock_fd: int | None = None
        self._socket_identity: tuple[int, int, int, int] | None = None
        self._state_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._worker_slots = threading.BoundedSemaphore(MAX_CLIENT_WORKERS)

        try:
            self._directory_fd = _open_application_directory(directory)
            self._lock_fd = _open_lifecycle_lock(self._directory_fd)
            self._remove_stale_socket()
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with _UMASK_LOCK:
                old_umask = os.umask(0o177)
                try:
                    self.socket.bind(self._socket_path_for_fd())
                finally:
                    os.umask(old_umask)
            metadata = self._socket_metadata()
            if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise RuntimeError("bound bridge socket has unsafe identity")
            self._socket_identity = _identity(metadata)
            os.chmod(
                SOCKET_NAME,
                0o600,
                dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            self.socket.listen(8)
            self.socket.settimeout(0.1)
        except Exception:
            if self.socket is not None:
                self.socket.close()
            self._release_lifecycle()
            raise

    def _socket_path_for_fd(self) -> str:
        assert self._directory_fd is not None
        return f"/proc/self/fd/{self._directory_fd}/{SOCKET_NAME}"

    def _socket_metadata(self) -> os.stat_result:
        assert self._directory_fd is not None
        return os.stat(
            SOCKET_NAME,
            dir_fd=self._directory_fd,
            follow_symlinks=False,
        )

    def _remove_stale_socket(self) -> None:
        try:
            metadata = self._socket_metadata()
        except FileNotFoundError:
            return
        expected = _identity(metadata)
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("refusing to replace unowned or non-socket runtime path")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.05)
        try:
            probe.connect(self._socket_path_for_fd())
        except (ConnectionRefusedError, FileNotFoundError):
            try:
                current = self._socket_metadata()
            except FileNotFoundError:
                return
            if _identity(current) != expected:
                raise RuntimeError("refusing to remove a changed socket path")
            assert self._directory_fd is not None
            os.unlink(SOCKET_NAME, dir_fd=self._directory_fd)
        except OSError as error:
            raise RuntimeError("cannot prove bridge socket is stale") from error
        else:
            raise RuntimeError("bridge socket is already active")
        finally:
            probe.close()

    def _remove_bound_socket(self) -> None:
        if self._socket_identity is None or self._directory_fd is None:
            return
        try:
            metadata = self._socket_metadata()
        except FileNotFoundError:
            return
        if _identity(metadata) != self._socket_identity:
            return
        try:
            os.unlink(SOCKET_NAME, dir_fd=self._directory_fd)
        except FileNotFoundError:
            pass

    def _close_descriptors(self) -> None:
        lock_fd, self._lock_fd = self._lock_fd, None
        directory_fd, self._directory_fd = self._directory_fd, None
        try:
            if lock_fd is not None:
                os.close(lock_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    def _release_lifecycle(self) -> None:
        try:
            self._remove_bound_socket()
        except OSError:
            pass
        finally:
            self._close_descriptors()

    def _serve_client(self, client: socket.socket) -> None:
        response = INVALID_REQUEST
        try:
            client.settimeout(CLIENT_READ_TIMEOUT)
            with client.makefile("rb") as stream:
                raw = stream.readline(MAX_CLIENT_REQUEST + 1)
            if raw.endswith(b"\n") and len(raw) <= MAX_CLIENT_REQUEST:
                try:
                    request = _decode_request(raw)
                except Exception:
                    request = None
                if isinstance(request, dict):
                    try:
                        candidate = self.handler(request)
                        response = candidate if isinstance(candidate, dict) else BRIDGE_ERROR
                    except Exception:
                        response = BRIDGE_ERROR
        except Exception:
            response = INVALID_REQUEST
        try:
            encoded = _compact_json(response)
        except Exception:
            encoded = _compact_json(BRIDGE_ERROR)
        try:
            client.sendall(encoded + b"\n")
        except Exception:
            pass

    def _client_worker(self, client: socket.socket) -> None:
        try:
            with client:
                self._serve_client(client)
        finally:
            with self._state_lock:
                self._clients.discard(client)
                self._workers.discard(threading.current_thread())
            self._worker_slots.release()

    def _start_client(self, client: socket.socket) -> None:
        if not self._worker_slots.acquire(blocking=False):
            client.close()
            return
        with self._state_lock:
            if self.closed:
                self._worker_slots.release()
                client.close()
                return
            try:
                worker = threading.Thread(
                    target=self._client_worker,
                    args=(client,),
                    name="omarchy-firefox-bridge-client",
                    daemon=True,
                )
                self._clients.add(client)
                self._workers.add(worker)
                worker.start()
            except Exception:
                self._clients.discard(client)
                if "worker" in locals():
                    self._workers.discard(worker)
                self._worker_slots.release()
                client.close()

    def serve_forever(self, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                with self._state_lock:
                    if self.closed:
                        break
                    listener = self.socket
                if listener is None:
                    break
                try:
                    client, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._start_client(client)
        finally:
            self.close()

    def close(self) -> None:
        with self._close_lock:
            with self._state_lock:
                if self.closed:
                    return
                self.closed = True
                listener = self.socket
                clients = list(self._clients)
                workers = list(self._workers)
            try:
                if listener is not None:
                    try:
                        listener.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    listener.close()
                for client in clients:
                    try:
                        client.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    client.close()

                deadline = time.monotonic() + SHUTDOWN_TIMEOUT
                current = threading.current_thread()
                for worker in workers:
                    if worker is current:
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    worker.join(remaining)
            finally:
                self._release_lifecycle()
