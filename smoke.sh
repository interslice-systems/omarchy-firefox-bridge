#!/bin/bash
# Standalone smoke test for omarchy-firefox-theme. Does NOT require Firefox,
# web-ext, AMO signing, or the install step — only Python 3.11+ and
# inotify-tools. Run from anywhere; the script cds into its own dir.
#
# Tests, in order:
#   1. Color mapping against the live colors.toml on this machine
#   2. Color mapping against a synthetic light theme (verifies dark/light branch)
#   3. Native helper end-to-end: spawn, capture initial JSON payload over the
#      native-messaging stdio protocol, kill — same wire format Firefox uses
#   4. web-ext lint (skipped if web-ext not installed)
#
# Each section either prints output and "OK", or fails loudly. Any failure
# means something needs fixing before you commit to the signing ceremony.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== 1. Color mapping (current colors.toml) ==="
python3 - <<'PY'
import tomllib, sys, json
from pathlib import Path
sys.path.insert(0, "helper")
from colors import omarchy_to_firefox_theme

with open(Path.home() / ".config/omarchy/current/theme/colors.toml", "rb") as f:
    palette = tomllib.load(f)

theme = omarchy_to_firefox_theme(palette)
assert theme["frame"].startswith("#"), "frame should be a hex string"
assert "toolbar_field_border_focus" in theme, "missing accent slot"

print(f"  input:  bg={palette['background']}  fg={palette['foreground']}  accent={palette['accent']}")
print(f"  output: {len(theme)} Firefox slots set")
print(f"  depth gradient (dark theme should ascend):")
print(f"    frame                 = {theme['frame']}")
print(f"    toolbar               = {theme['toolbar']}")
print(f"    toolbar_field         = {theme['toolbar_field']}")
print(f"    toolbar_field_focus   = {theme['toolbar_field_focus']}")
print(f"  accent routing:")
print(f"    tab_line                  = {theme['tab_line']}")
print(f"    toolbar_field_border_focus = {theme['toolbar_field_border_focus']}")
print(f"    sidebar_highlight         = {theme['sidebar_highlight']}")
PY
echo "  OK"
echo

echo "=== 2. Color mapping (synthetic light theme — verifies dark/light branch) ==="
python3 - <<'PY'
import sys
sys.path.insert(0, "helper")
from colors import omarchy_to_firefox_theme, _luminance, _hex_to_rgb

light = {
    "background": "#fafafa", "foreground": "#1a1a1a", "accent": "#0066cc",
    "selection_background": "#cce4ff", "selection_foreground": "#1a1a1a",
}
theme = omarchy_to_firefox_theme(light)
frame_lum = _luminance(_hex_to_rgb(theme["frame"]))
toolbar_lum = _luminance(_hex_to_rgb(theme["toolbar"]))
assert toolbar_lum < frame_lum, (
    f"light theme should darken toolbar from frame "
    f"(frame_lum={frame_lum:.3f}, toolbar_lum={toolbar_lum:.3f})"
)

print(f"  light frame   = {theme['frame']}   (lum {frame_lum:.3f})")
print(f"  light toolbar = {theme['toolbar']}   (lum {toolbar_lum:.3f})  ← darker than frame, correct")
print(f"  light field   = {theme['toolbar_field']}")
PY
echo "  OK"
echo

echo "=== 3. Native helper end-to-end (no Firefox needed) ==="
python3 - <<'PY'
import subprocess, struct, json, sys, signal

def timeout(signum, frame):
    raise TimeoutError("helper did not send initial payload within 5s")

signal.signal(signal.SIGALRM, timeout)

proc = subprocess.Popen(
    ["./helper/omarchy-firefox-theme-helper"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

try:
    signal.alarm(5)
    hdr = proc.stdout.read(4)
    if len(hdr) < 4:
        err = proc.stderr.read().decode()
        print("  FAIL: helper closed stdout before sending header")
        print(f"  stderr was:\n    {err.strip()}")
        proc.terminate()
        sys.exit(1)
    (n,) = struct.unpack("<I", hdr)
    payload = proc.stdout.read(n)
    signal.alarm(0)

    msg = json.loads(payload.decode())
    assert msg.get("type") == "theme", f"expected type=theme, got {msg.get('type')!r}"
    assert "frame" in msg["theme"], "payload missing 'frame' slot"

    print(f"  spawn:  ok (PID {proc.pid})")
    print(f"  initial payload: {4 + n} bytes total (4-byte LE length + {n}-byte JSON)")
    print(f"  message type:    {msg['type']}")
    print(f"  theme slots:     {len(msg['theme'])}")
    print(f"  frame in payload = {msg['theme']['frame']}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()

stderr_text = proc.stderr.read().decode().strip()
if stderr_text:
    print(f"  helper stderr (forwarded to extension console in real use):")
    for line in stderr_text.splitlines():
        print(f"    {line}")
PY
echo "  OK"
echo

echo "=== 4. web-ext lint (extension) ==="
if command -v web-ext >/dev/null 2>&1; then
    if (cd extension && web-ext lint --no-input --warnings-as-errors 2>&1); then
        echo "  OK"
    else
        echo "  FAIL: web-ext lint found issues"
        exit 1
    fi
else
    echo "  SKIPPED (web-ext not installed; run 'npm i -g web-ext' to enable)"
fi
echo

echo "=== All smoke tests passed ==="
echo "Safe to proceed with install.sh when ready."
