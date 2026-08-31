#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export OMARCHY_FIREFOX_BRIDGE_LIB_ROOT=$repo

cleanup() {
  if [[ -n ${app_write_probe:-} ]]; then
    rm -f -- "$app_write_probe"
  fi
  if [[ -n ${persistent_write_probe:-} ]]; then
    rm -f -- "$persistent_write_probe"
  fi
  rm -rf -- "$temp_root"
}

temp_root=$(mktemp -d)
trap cleanup EXIT
chmod 700 "$temp_root"
app_write_probe=$repo/src/sandbox-write-probe
persistent_write_probe=$HOME/.local/state/omarchy/current/bridge-write-probe
[[ ! -e $app_write_probe && ! -L $app_write_probe ]]
[[ ! -e $persistent_write_probe && ! -L $persistent_write_probe ]]

runtime_parent=$temp_root/runtime
mkdir -m 700 "$runtime_parent"
export XDG_RUNTIME_DIR=$runtime_parent

[[ -e $HOME/.ssh ]]

fake_root=$temp_root/fake-root
mkdir -p "$fake_root/src/omarchy_firefox_bridge" "$fake_root/libexec"
printf '' >"$fake_root/src/omarchy_firefox_bridge/__init__.py"
cat >"$fake_root/src/omarchy_firefox_bridge/client.py" <<'PY'
import json
import sys

print("client:" + json.dumps(sys.argv[1:], separators=(",", ":")))
PY
cat >"$fake_root/libexec/omarchy-firefox-bridge-sandbox" <<'SH'
#!/usr/bin/env bash
printf 'sandbox:'
printf '<%s>' "$@"
printf '\n'
SH
chmod 755 "$fake_root/libexec/omarchy-firefox-bridge-sandbox"

assert_dispatch() {
  local expected=$1
  shift
  local actual
  actual=$(OMARCHY_FIREFOX_BRIDGE_LIB_ROOT=$fake_root \
    "$repo/bin/omarchy-firefox-bridge" "$@")
  [[ $actual == "$expected" ]] || {
    printf 'dispatch mismatch: expected %s, got %s\n' "$expected" "$actual" >&2
    return 1
  }
}

assert_dispatch 'client:["tabs"]' tabs
assert_dispatch 'client:["activate","not","numeric"]' activate not numeric
assert_dispatch 'sandbox:<omarchy_firefox_bridge.host>'
assert_dispatch 'sandbox:<omarchy_firefox_bridge.host>' tabs extra
assert_dispatch 'sandbox:<omarchy_firefox_bridge.host>' activate
assert_dispatch 'sandbox:<omarchy_firefox_bridge.host>' activate one
assert_dispatch 'sandbox:<omarchy_firefox_bridge.host>' activate one two three
assert_dispatch 'sandbox:<omarchy_firefox_bridge.host>' unknown

assert_runtime_rejected() {
  local parent=$1
  local expected=$2
  local rejection

  if rejection=$(XDG_RUNTIME_DIR=$parent \
    "$repo/libexec/omarchy-firefox-bridge-sandbox" \
      omarchy_firefox_bridge.sandbox_probe 2>&1); then
    printf 'unsafe runtime path was accepted: %s\n' "$parent" >&2
    return 1
  fi
  [[ $rejection == *"$expected"* ]]
}

hostile_cwd=$temp_root/hostile-cwd
hostile_marker=$temp_root/hostile-imported
hostile_parent=$temp_root/hostile-runtime
mkdir -m 700 \
  "$hostile_cwd" \
  "$hostile_cwd/omarchy_firefox_bridge" \
  "$hostile_parent"
printf '' >"$hostile_cwd/omarchy_firefox_bridge/__init__.py"
cat >"$hostile_cwd/omarchy_firefox_bridge/socket_server.py" <<'PY'
import os
from pathlib import Path

Path(os.environ["HOSTILE_IMPORT_MARKER"]).write_text("imported")


def runtime_directory():
    return Path(os.environ["XDG_RUNTIME_DIR"]) / "omarchy-firefox-bridge"


def _open_application_directory(_directory):
    return os.open("/dev/null", os.O_RDONLY)
PY
printf 'keep' >"$hostile_parent/omarchy-firefox-bridge"

shadow_rejection=
shadow_status=0
if shadow_rejection=$(
  cd "$hostile_cwd" || exit 1
  HOSTILE_IMPORT_MARKER=$hostile_marker \
    XDG_RUNTIME_DIR=$hostile_parent \
    "$repo/libexec/omarchy-firefox-bridge-sandbox" \
      omarchy_firefox_bridge.sandbox_probe 2>&1
); then
  shadow_status=0
else
  shadow_status=$?
fi
[[ ! -e $hostile_marker ]] || {
  printf 'hostile current-directory package was imported\n' >&2
  exit 1
}
[[ $shadow_status -ne 0 ]]
[[ $shadow_rejection == *"bridge runtime path must be an owned real directory"* ]]
[[ $(<"$hostile_parent/omarchy-firefox-bridge") == keep ]]

symlink_parent=$temp_root/symlink-runtime
symlink_target=$temp_root/symlink-target
mkdir -m 700 "$symlink_parent" "$symlink_target"
ln -s "$symlink_target" "$symlink_parent/omarchy-firefox-bridge"
assert_runtime_rejected \
  "$symlink_parent" \
  "bridge runtime path must be an owned real directory"
[[ -L $symlink_parent/omarchy-firefox-bridge ]]
[[ ! -e $symlink_target/sandbox-probe.sock ]]

file_parent=$temp_root/file-runtime
mkdir -m 700 "$file_parent"
printf 'keep' >"$file_parent/omarchy-firefox-bridge"
assert_runtime_rejected \
  "$file_parent" \
  "bridge runtime path must be an owned real directory"
[[ $(<"$file_parent/omarchy-firefox-bridge") == keep ]]

if [[ $(stat -c %u /usr) != $(id -u) ]]; then
  assert_runtime_rejected /usr "XDG_RUNTIME_DIR must be an owned real directory"
  [[ ! -e /usr/omarchy-firefox-bridge ]]
fi

export OMARCHY_FIREFOX_BRIDGE_SANDBOX_SENTINEL=must-not-cross
output=$(
  "$repo/libexec/omarchy-firefox-bridge-sandbox" \
    omarchy_firefox_bridge.sandbox_probe
)

python3 - "$output" "$repo/libexec/omarchy-firefox-bridge-sandbox" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

result = json.loads(sys.argv[1])
sandbox_launcher = Path(sys.argv[2]).read_text()

sandbox_namespace = result.pop("networkNamespace")
assert sandbox_namespace != os.readlink("/proc/self/ns/net")
assert result == {
    "appWriteBlocked": True,
    "colorsReadable": True,
    "environmentCleared": True,
    "homeHidden": True,
    "interfacesIsolated": True,
    "networkBlocked": True,
    "persistentWriteBlocked": True,
    "routesEmpty": True,
    "runtimeOwnerOnly": True,
    "runtimeSocketWritable": True,
}

lines = [line.strip() for line in sandbox_launcher.splitlines()]
command_start = lines.index("exec /usr/bin/bwrap \\")
command_end = lines.index('/usr/bin/python3 -m "$module" "$@"', command_start)
command_lines = lines[command_start : command_end + 1]
assert command_lines.count("--unshare-user \\") == 1
assert command_lines.count("--disable-userns \\") == 1
assert command_lines.index("--disable-userns \\") == (
    command_lines.index("--unshare-user \\") + 1
)
assert [line for line in command_lines if line.startswith("--symlink ")] == [
    "--symlink usr/lib /lib \\",
    "--symlink usr/lib /lib64 \\",
    "--symlink usr/bin /bin \\",
    "--symlink usr/bin /sbin \\",
]

runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "omarchy-firefox-bridge"
runtime_metadata = runtime.lstat()
parent_metadata = runtime.parent.lstat()
assert stat.S_ISDIR(runtime_metadata.st_mode)
assert stat.S_ISDIR(parent_metadata.st_mode)
assert runtime_metadata.st_uid == os.getuid()
assert runtime_metadata.st_mode & 0o777 == 0o700
assert parent_metadata.st_uid == os.getuid()
assert parent_metadata.st_mode & 0o777 == 0o700
print("ok - Bubblewrap exposes only read-only Omarchy state and writable runtime IPC")
PY

[[ ! -e $app_write_probe && ! -L $app_write_probe ]]
[[ ! -e $persistent_write_probe && ! -L $persistent_write_probe ]]
[[ ! -e $XDG_RUNTIME_DIR/omarchy-firefox-bridge/sandbox-probe.sock ]]

host_log=$temp_root/host.log
"$repo/libexec/omarchy-firefox-bridge-sandbox" \
  omarchy_firefox_bridge.host </dev/null >/dev/null 2>"$host_log"
[[ $(<"$host_log") == *"[omarchy-firefox-bridge] started"* ]]
