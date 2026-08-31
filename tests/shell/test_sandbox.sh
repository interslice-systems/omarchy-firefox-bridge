#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export OMARCHY_FIREFOX_BRIDGE_LIB_ROOT=$repo

[[ -e $HOME/.ssh ]]

output=$(
  "$repo/libexec/omarchy-firefox-bridge-sandbox" \
    omarchy_firefox_bridge.sandbox_probe
)

python3 - "$output" "$repo/libexec/omarchy-firefox-bridge-sandbox" \
  "$repo/bin/omarchy-firefox-bridge" <<'PY'
import json
import os
from pathlib import Path
import sys

result = json.loads(sys.argv[1])
sandbox_launcher = Path(sys.argv[2]).read_text()
public_launcher = Path(sys.argv[3]).read_text()

sandbox_namespace = result.pop("networkNamespace")
assert sandbox_namespace != os.readlink("/proc/self/ns/net")
assert result == {
    "colorsReadable": True,
    "homeHidden": True,
    "interfacesIsolated": True,
    "networkBlocked": True,
    "persistentWriteBlocked": True,
    "routesEmpty": True,
    "runtimeSocketWritable": True,
}

lines = [line.strip() for line in sandbox_launcher.splitlines()]
public_lines = [line.strip() for line in public_launcher.splitlines()]
assert lines.count("--unshare-user \\") == 1
assert lines.count("--disable-userns \\") == 1
assert lines.index("--disable-userns \\") == lines.index("--unshare-user \\") + 1
assert [line for line in lines if line.startswith("--setenv ")] == [
    '--setenv HOME "$HOME" \\',
    "--setenv PATH /usr/bin \\",
    "--setenv PYTHONPATH /app \\",
    '--setenv XDG_RUNTIME_DIR "$XDG_RUNTIME_DIR" \\',
]
assert [line for line in lines if line.startswith("--ro-bind ")] == [
    "--ro-bind /usr /usr \\",
    '--ro-bind "$current" "$current" \\',
    '--ro-bind "$app_src" /app \\',
]
assert [line for line in lines if line.startswith("--bind ")] == [
    '--bind "$runtime" "$runtime" \\',
]
assert [line for line in lines if line.startswith("--symlink ")] == [
    "--symlink usr/lib /lib \\",
    "--symlink usr/lib /lib64 \\",
    "--symlink usr/bin /bin \\",
    "--symlink usr/bin /sbin \\",
]
for required in (
    "exec /usr/bin/bwrap \\",
    "--die-with-parent \\",
    "--new-session \\",
    "--unshare-all \\",
    "--clearenv \\",
    "/usr/bin/python3 -m \"$module\" \"$@\"",
):
    assert lines.count(required) == 1, required

assert "case ${1:-} in" in public_launcher
assert "tabs|activate)" in public_launcher
assert 'exec /usr/bin/python3 -m omarchy_firefox_bridge.client "$@"' in public_launcher
public_exec = 'exec "$lib_root/libexec/omarchy-firefox-bridge-sandbox" \\'
host_module = "omarchy_firefox_bridge.host"
assert public_exec in public_lines
assert public_lines.index(host_module) == public_lines.index(public_exec) + 1

runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "omarchy-firefox-bridge"
assert runtime.stat().st_uid == os.getuid()
assert runtime.stat().st_mode & 0o777 == 0o700
print("ok - Bubblewrap exposes only read-only Omarchy state and writable runtime IPC")
PY
