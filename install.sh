#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
lib_root=$HOME/.local/lib/omarchy-firefox-bridge
bin_dest=$HOME/.local/bin/omarchy-firefox-bridge
manifest_dest=$HOME/.mozilla/native-messaging-hosts/omarchy_firefox_bridge.json

missing=()
for command in bwrap firefox inotifywait python3 omarchy; do
  command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
if (( ${#missing[@]} )); then
  printf 'Missing runtime commands: %s\n' "${missing[*]}" >&2
  printf 'Install official packages with: sudo pacman -S --needed bubblewrap firefox inotify-tools python\n' >&2
  exit 1
fi

version=$(omarchy version)
if [[ $version != 4.* ]]; then
  printf 'Omarchy 4 is required; found %s\n' "$version" >&2
  exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
PY

install -d -m 755 "$HOME/.local/bin" "$lib_root/libexec"
stage=$(mktemp -d "$lib_root/.stage.XXXXXX")
trap 'rm -rf "$stage"' EXIT
cp -a "$repo/src" "$stage/src"
rm -rf "$lib_root/src"
mv "$stage/src" "$lib_root/src"
install -m 755 "$repo/libexec/omarchy-firefox-bridge-sandbox" \
  "$lib_root/libexec/omarchy-firefox-bridge-sandbox"
install -m 755 "$repo/bin/omarchy-firefox-bridge" "$bin_dest"

install -d -m 700 "$(dirname "$manifest_dest")"
python3 - "$manifest_dest" "$bin_dest" <<'PY'
import json
from pathlib import Path
import sys

destination = Path(sys.argv[1])
executable = str(Path(sys.argv[2]).resolve())
manifest = {
    "name": "omarchy_firefox_bridge",
    "description": "Omarchy Firefox Bridge native host",
    "path": executable,
    "type": "stdio",
    "allowed_extensions": ["omarchy-bridge@interslice.systems"],
}
temporary = destination.with_suffix(".json.tmp")
temporary.write_text(json.dumps(manifest, indent=2) + "\n")
temporary.chmod(0o600)
temporary.replace(destination)
PY

omarchy hook install theme-set "$repo/hooks/omarchy-firefox-bridge"

cat <<EOF
Local bridge installation complete.

Native host: $manifest_dest
Executable:  $bin_dest
Extension:   $repo/extension/manifest.json

Load temporarily in Firefox:
  about:debugging#/runtime/this-firefox
  Load Temporary Add-on -> $repo/extension/manifest.json

This installer does not contact AMO or sign the extension.
EOF
