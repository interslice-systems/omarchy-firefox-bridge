#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/home"

for command in bwrap firefox inotifywait; do
  cat > "$tmp/bin/$command" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  chmod 755 "$tmp/bin/$command"
done

cat > "$tmp/bin/omarchy" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == version ]]; then
  printf '4.0.1-1\n'
elif [[ ${1:-} == hook && ${2:-} == install && ${3:-} == theme-set ]]; then
  destination=$HOME/.config/omarchy/hooks/theme-set.d
  mkdir -p "$destination"
  cp "$4" "$destination/$(basename "$4")"
  chmod 755 "$destination/$(basename "$4")"
else
  exit 1
fi
STUB
chmod 755 "$tmp/bin/omarchy"

HOME=$tmp/home PATH=$tmp/bin:/usr/bin "$repo/install.sh" > "$tmp/output"
HOME=$tmp/home PATH=$tmp/bin:/usr/bin "$repo/install.sh" >> "$tmp/output"

test -x "$tmp/home/.local/bin/omarchy-firefox-bridge"
test -x "$tmp/home/.local/lib/omarchy-firefox-bridge/libexec/omarchy-firefox-bridge-sandbox"
test -f "$tmp/home/.local/lib/omarchy-firefox-bridge/src/omarchy_firefox_bridge/host.py"
test -x "$tmp/home/.config/omarchy/hooks/theme-set.d/omarchy-firefox-bridge"

python3 - "$tmp/home/.mozilla/native-messaging-hosts/omarchy_firefox_bridge.json" "$tmp/home" <<'PY'
import json
from pathlib import Path
import sys

manifest = json.loads(Path(sys.argv[1]).read_text())
home = Path(sys.argv[2])
assert manifest == {
    "name": "omarchy_firefox_bridge",
    "description": "Omarchy Firefox Bridge native host",
    "path": str(home / ".local/bin/omarchy-firefox-bridge"),
    "type": "stdio",
    "allowed_extensions": ["omarchy-bridge@interslice.systems"],
}
PY

test "$(grep -c 'Local bridge installation complete' "$tmp/output")" -eq 2
printf 'ok - installer is local-only and idempotent\n'
