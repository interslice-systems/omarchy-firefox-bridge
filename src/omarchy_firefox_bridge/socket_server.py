"""Owner-only local Unix socket server for bridge clients."""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import threading
import time
from typing import Callable

APPLICATION_DIRECTORY = "omarchy-firefox-bridge"
SOCKET_NAME = "bridge.sock"
MAX_CLIENT_REQUEST = 4096
MAX_CLIENT_WORKERS = 8
CLIENT_READ_TIMEOUT = 0.5
HANDLER_TIMEOUT = 0.4
SHUTDOWN_TIMEOUT = 0.5
INVALID_REQUEST = {"ok": False, "error": "invalid-request"}
BRIDGE_ERROR = {"ok": False, "error": "bridge-error"}

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_RENAME_NOREPLACE = 1
_UMASK_LOCK = threading.Lock()
_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAMEAT2 = _LIBC.renameat2
_RENAMEAT2.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_uint,
]
_RENAMEAT2.restype = ctypes.c_int


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


def _rename_noreplace(directory_fd: int, source: str, target: str) -> None:
    result = _RENAMEAT2(
        directory_fd,
        os.fsencode(source),
        directory_fd,
        os.fsencode(target),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target)


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
    """Serve bounded clients whose injected handler returns within 400 ms.

    Python cannot cancel an arbitrary function. Client workers are therefore
    daemon threads, capped at eight, and shutdown waits one 500 ms aggregate
    budget for the real Task 6 handler's 400 ms deadline.
    """

    def __init__(self, directory: Path, handler: Callable[[dict], dict]) -> None:
        self.directory = directory
        self.path = directory / SOCKET_NAME
        self.handler = handler
        self.closed = False
        self.socket: socket.socket | None = None
        self._directory_fd: int | None = None
        self._socket_identity: tuple[int, int, int, int] | None = None
        self._state_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._worker_slots = threading.BoundedSemaphore(MAX_CLIENT_WORKERS)

        parent_fd = _open_owned_directory(directory.parent, "XDG_RUNTIME_DIR")
        try:
            try:
                os.mkdir(directory.name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            except OSError as error:
                raise RuntimeError("cannot create bridge runtime directory") from error
            try:
                self._directory_fd = os.open(
                    directory.name,
                    _DIRECTORY_FLAGS,
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise RuntimeError(
                    "bridge runtime path must be an owned real directory"
                ) from error
            _validate_owned_directory(
                self._directory_fd,
                "bridge runtime path",
            )
            os.fchmod(self._directory_fd, 0o700)
        finally:
            os.close(parent_fd)

        bind_name = None
        bind_identity = None
        try:
            self._remove_stale_socket()
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            bind_name = f".{SOCKET_NAME}.bind-{secrets.token_hex(16)}"
            with _UMASK_LOCK:
                old_umask = os.umask(0o177)
                try:
                    self.socket.bind(self._socket_path_for_fd(bind_name))
                finally:
                    os.umask(old_umask)
            metadata = self._socket_metadata(bind_name)
            if (
                not stat.S_ISSOCK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise RuntimeError("bound bridge socket has unsafe identity or mode")
            bind_identity = _identity(metadata)
            self.socket.listen(8)
            try:
                _rename_noreplace(self._directory_fd, bind_name, SOCKET_NAME)
            except OSError as error:
                raise RuntimeError("bridge socket path changed during bind") from error
            self._socket_identity = bind_identity
            bind_name = None
            self.socket.settimeout(0.1)
        except Exception:
            if self.socket is not None:
                self.socket.close()
            if self._socket_identity is not None:
                try:
                    self._remove_exact_socket(SOCKET_NAME, self._socket_identity)
                except RuntimeError:
                    pass
            elif bind_identity is not None and bind_name is not None:
                try:
                    self._remove_exact_socket(bind_name, bind_identity)
                except RuntimeError:
                    pass
            if self._directory_fd is not None:
                os.close(self._directory_fd)
                self._directory_fd = None
            raise

    def _socket_path_for_fd(self, name: str = SOCKET_NAME) -> str:
        assert self._directory_fd is not None
        return f"/proc/self/fd/{self._directory_fd}/{name}"

    def _socket_metadata(self, name: str = SOCKET_NAME) -> os.stat_result:
        assert self._directory_fd is not None
        return os.stat(
            name,
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
            self._remove_exact_socket(SOCKET_NAME, expected)
        except OSError as error:
            raise RuntimeError("cannot prove bridge socket is stale") from error
        else:
            raise RuntimeError("bridge socket is already active")
        finally:
            probe.close()

    def _remove_exact_socket(
        self,
        name: str,
        expected: tuple[int, int, int, int],
    ) -> None:
        assert self._directory_fd is not None
        quarantine = f".{SOCKET_NAME}.quarantine-{secrets.token_hex(16)}"
        try:
            os.rename(
                name,
                quarantine,
                src_dir_fd=self._directory_fd,
                dst_dir_fd=self._directory_fd,
            )
        except FileNotFoundError:
            return
        metadata = os.stat(
            quarantine,
            dir_fd=self._directory_fd,
            follow_symlinks=False,
        )
        if _identity(metadata) != expected:
            try:
                _rename_noreplace(self._directory_fd, quarantine, name)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    raise RuntimeError(
                        "socket identity changed and restoration failed"
                    ) from error
            raise RuntimeError("refusing to remove a changed socket path")
        os.unlink(quarantine, dir_fd=self._directory_fd)

    def _serve_client(self, client: socket.socket) -> None:
        response = INVALID_REQUEST
        client.settimeout(CLIENT_READ_TIMEOUT)
        try:
            with client.makefile("rb") as stream:
                raw = stream.readline(MAX_CLIENT_REQUEST + 1)
            if raw.endswith(b"\n") and len(raw) <= MAX_CLIENT_REQUEST:
                try:
                    request = _decode_request(raw)
                except Exception:
                    request = None
                if isinstance(request, dict):
                    try:
                        response = self.handler(request)
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
        worker = threading.Thread(
            target=self._client_worker,
            args=(client,),
            name="omarchy-firefox-bridge-client",
            daemon=True,
        )
        with self._state_lock:
            if self.closed:
                self._worker_slots.release()
                client.close()
                return
            self._clients.add(client)
            self._workers.add(worker)
        try:
            worker.start()
        except Exception:
            with self._state_lock:
                self._clients.discard(client)
                self._workers.discard(worker)
            self._worker_slots.release()
            client.close()
            return

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

            cleanup_error = None
            if self._socket_identity is not None and self._directory_fd is not None:
                try:
                    self._remove_exact_socket(SOCKET_NAME, self._socket_identity)
                except RuntimeError as error:
                    cleanup_error = error
            if self._directory_fd is not None:
                os.close(self._directory_fd)
                self._directory_fd = None
            if cleanup_error is not None:
                raise cleanup_error
