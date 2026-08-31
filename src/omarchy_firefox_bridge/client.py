"""Short-lived CLI client for the local bridge socket."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys

from .socket_server import runtime_directory

CLIENT_TIMEOUT = 0.5


def request(
    path: Path,
    message: dict | None = None,
    timeout: float = CLIENT_TIMEOUT,
) -> dict:
    message = message or {"action": "tabs"}
    encoded = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(encoded)
        client.shutdown(socket.SHUT_WR)
        raw = client.makefile("rb").readline(2 * 1024 * 1024 + 1)
        if not raw.endswith(b"\n") or len(raw) > 2 * 1024 * 1024:
            return {"ok": False, "error": "bridge-error"}
        response = json.loads(raw.decode("utf-8"))
        return response if isinstance(response, dict) else {"ok": False, "error": "bridge-error"}
    except FileNotFoundError:
        return {"ok": False, "error": "unavailable"}
    except (ConnectionRefusedError, OSError) as error:
        if isinstance(error, socket.timeout):
            return {"ok": False, "error": "timeout"}
        return {"ok": False, "error": "unavailable"}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "bridge-error"}
    finally:
        client.close()


def parse_command(argv: list[str]) -> dict:
    if argv == ["tabs"]:
        return {"action": "tabs"}
    if len(argv) == 3 and argv[0] == "activate":
        if not argv[1].isdigit() or not argv[2].isdigit():
            raise ValueError("IDs must be decimal integers")
        window_id = int(argv[1])
        tab_id = int(argv[2])
        if window_id <= 0 or tab_id <= 0:
            raise ValueError("IDs must be positive integers")
        return {"action": "activate", "windowId": window_id, "tabId": tab_id}
    raise ValueError("usage: omarchy-firefox-bridge tabs|activate <windowId> <tabId>")


def emit(response: dict) -> None:
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        message = parse_command(argv)
    except ValueError:
        emit({"ok": False, "error": "invalid-request"})
        return 2
    try:
        path = runtime_directory() / "bridge.sock"
    except RuntimeError:
        emit({"ok": False, "error": "unavailable"})
        return 3
    response = request(path, message)
    emit(response)
    return 0 if response.get("ok") is True else 3


if __name__ == "__main__":
    raise SystemExit(main())
