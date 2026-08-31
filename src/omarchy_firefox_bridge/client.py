"""Short-lived CLI client for the local bridge socket."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import time

from .socket_server import runtime_directory

CLIENT_TIMEOUT = 0.5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
BRIDGE_ERROR = {"ok": False, "error": "bridge-error"}
UNAVAILABLE = {"ok": False, "error": "unavailable"}
TIMEOUT = {"ok": False, "error": "timeout"}
INVALID_REQUEST = {"ok": False, "error": "invalid-request"}


def _reject_json_constant(constant: str) -> None:
    raise ValueError(f"non-finite JSON constant: {constant}")


def _encode(response: dict) -> str:
    return json.dumps(
        response,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise socket.timeout("bridge response deadline expired")
    return remaining


def request(
    path: Path,
    message: dict | None = None,
    timeout: float = CLIENT_TIMEOUT,
) -> dict:
    message = message or {"action": "tabs"}
    try:
        encoded = _encode(message).encode("utf-8") + b"\n"
    except Exception:
        return BRIDGE_ERROR.copy()

    client = None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        client.connect(str(path))
        deadline = time.monotonic() + timeout
        client.settimeout(_remaining(deadline))
        client.sendall(encoded)
        client.shutdown(socket.SHUT_WR)

        received = bytearray()
        while True:
            client.settimeout(_remaining(deadline))
            chunk = client.recv(MAX_RESPONSE_BYTES + 1 - len(received))
            if not chunk:
                return BRIDGE_ERROR.copy()
            received.extend(chunk)
            newline = received.find(b"\n")
            if newline >= 0:
                raw = bytes(received[: newline + 1])
                if len(raw) > MAX_RESPONSE_BYTES:
                    return BRIDGE_ERROR.copy()
                break
            if len(received) > MAX_RESPONSE_BYTES:
                return BRIDGE_ERROR.copy()

        response = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
        return response if isinstance(response, dict) else BRIDGE_ERROR.copy()
    except socket.timeout:
        return TIMEOUT.copy()
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return UNAVAILABLE.copy()
    except Exception:
        return BRIDGE_ERROR.copy()
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _ascii_decimal(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all("0" <= character <= "9" for character in value)
    )


def parse_command(argv: list[str]) -> dict:
    if argv == ["tabs"]:
        return {"action": "tabs"}
    if len(argv) == 3 and argv[0] == "activate":
        if not _ascii_decimal(argv[1]) or not _ascii_decimal(argv[2]):
            raise ValueError("IDs must be ASCII decimal integers")
        window_id = int(argv[1])
        tab_id = int(argv[2])
        if window_id <= 0 or tab_id <= 0:
            raise ValueError("IDs must be positive integers")
        return {"action": "activate", "windowId": window_id, "tabId": tab_id}
    raise ValueError("usage: omarchy-firefox-bridge tabs|activate <windowId> <tabId>")


def emit(response: dict) -> bool:
    serialized_original = True
    fallback = _encode(BRIDGE_ERROR)
    try:
        encoded = _encode(response)
    except Exception:
        encoded = fallback
        serialized_original = False
    try:
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()
    except Exception:
        if encoded != fallback:
            try:
                sys.stdout.write(fallback + "\n")
                sys.stdout.flush()
            except Exception:
                pass
        return False
    return serialized_original


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        message = parse_command(arguments)
    except Exception:
        return 2 if emit(INVALID_REQUEST) else 3
    try:
        path = runtime_directory() / "bridge.sock"
    except Exception:
        emit(UNAVAILABLE)
        return 3
    try:
        response = request(path, message)
    except Exception:
        response = BRIDGE_ERROR
    emitted_original = emit(response)
    if not emitted_original or not isinstance(response, dict):
        return 3
    return 0 if response.get("ok") is True else 3


if __name__ == "__main__":
    raise SystemExit(main())
