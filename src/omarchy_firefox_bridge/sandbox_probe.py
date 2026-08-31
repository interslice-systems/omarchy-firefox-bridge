"""Executable sandbox assertions used by the release smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat


SENTINEL = "OMARCHY_FIREFOX_BRIDGE_SANDBOX_SENTINEL"


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


def write_blocked(path: Path) -> bool:
    try:
        path.write_text("must fail")
    except OSError:
        return True
    path.unlink(missing_ok=True)
    return False


def main() -> int:
    home = Path.home()
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "omarchy-firefox-bridge"
    colors = home / ".local/state/omarchy/current/theme/colors.toml"
    persistent_probe = home / ".local/state/omarchy/current/bridge-write-probe"
    app_probe = Path("/app/sandbox-write-probe")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime_metadata = runtime.lstat()
    runtime_owner_only = (
        stat.S_ISDIR(runtime_metadata.st_mode)
        and runtime_metadata.st_uid == os.getuid()
        and stat.S_IMODE(runtime_metadata.st_mode) == 0o700
    )
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
                "appWriteBlocked": write_blocked(app_probe),
                "colorsReadable": colors.is_file() and bool(colors.read_bytes()),
                "environmentCleared": SENTINEL not in os.environ,
                "homeHidden": not (home / ".ssh").exists(),
                "interfacesIsolated": interfaces_isolated(),
                "networkBlocked": blocked_network(),
                "networkNamespace": os.readlink("/proc/self/ns/net"),
                "persistentWriteBlocked": write_blocked(persistent_probe),
                "routesEmpty": routes_empty(),
                "runtimeOwnerOnly": runtime_owner_only,
                "runtimeSocketWritable": runtime_writable,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
