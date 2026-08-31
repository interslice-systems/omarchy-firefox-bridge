"""Executable sandbox assertions used by the release smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket


def blocked_network() -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.1)
    try:
        probe.connect(("1.1.1.1", 53))
    except OSError:
        return True
    finally:
        probe.close()
    return False


def interfaces_isolated() -> bool:
    return {name for _, name in socket.if_nameindex()} == {"lo"}


def routes_empty() -> bool:
    routes = Path("/proc/net/route").read_text().splitlines()
    return len(routes) <= 1


def main() -> int:
    home = Path.home()
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "omarchy-firefox-bridge"
    colors = home / ".local/state/omarchy/current/theme/colors.toml"
    persistent_probe = home / ".local/state/omarchy/current/bridge-write-probe"
    persistent_blocked = False
    try:
        persistent_probe.write_text("must fail")
    except OSError:
        persistent_blocked = True
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket_path = runtime / "sandbox-probe.sock"
    test_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        test_socket.bind(str(socket_path))
        runtime_writable = socket_path.exists()
    finally:
        test_socket.close()
        socket_path.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "colorsReadable": colors.is_file() and bool(colors.read_bytes()),
                "homeHidden": not (home / ".ssh").exists(),
                "interfacesIsolated": interfaces_isolated(),
                "networkBlocked": blocked_network(),
                "networkNamespace": os.readlink("/proc/self/ns/net"),
                "persistentWriteBlocked": persistent_blocked,
                "routesEmpty": routes_empty(),
                "runtimeSocketWritable": runtime_writable,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
