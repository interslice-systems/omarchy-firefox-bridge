#!/bin/bash
# Idempotent installer for omarchy-firefox-theme. Re-run safely; symlinks are
# replaced atomically. One-time prereqs:
#   - inotify-tools                          (used by helper at runtime)
#   - web-ext                                (signs the extension)
#   - WEB_EXT_API_KEY / WEB_EXT_API_SECRET   (from https://addons.mozilla.org/developers/addon/api/key/)
#
# Pass --rebuild to force a fresh extension sign.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$REPO/extension"
HELPER_SRC="$REPO/helper/omarchy-firefox-theme-helper"
HOOK_SRC="$REPO/hook/firefox-color-sync"
NATIVE_TPL="$REPO/native-host/omarchy_firefox_theme.json.tpl"

HELPER_DEST="$HOME/.local/bin/omarchy-firefox-theme-helper"
NATIVE_DEST="$HOME/.mozilla/native-messaging-hosts/omarchy_firefox_theme.json"
HOOK_DEST_DIR="$HOME/.config/omarchy/hooks/theme-set.d"
HOOK_DEST="$HOOK_DEST_DIR/firefox-color-sync"

# --- Prereq check ---
missing=()
command -v inotifywait >/dev/null 2>&1 || missing+=("inotify-tools (pacman)")
command -v web-ext     >/dev/null 2>&1 || missing+=("web-ext (npm i -g web-ext, or AUR)")
if (( ${#missing[@]} > 0 )); then
    echo "Missing prerequisites:" >&2
    printf '  - %s\n' "${missing[@]}" >&2
    exit 1
fi

# --- Sign extension if needed ---
FORCE_REBUILD=0
for arg in "$@"; do
    [[ "$arg" == "--rebuild" ]] && FORCE_REBUILD=1
done

shopt -s nullglob
xpis=( "$EXT_DIR/web-ext-artifacts"/*.xpi )
if (( FORCE_REBUILD || ${#xpis[@]} == 0 )); then
    if [[ -z "${WEB_EXT_API_KEY:-}" || -z "${WEB_EXT_API_SECRET:-}" ]]; then
        echo "WEB_EXT_API_KEY / WEB_EXT_API_SECRET not set." >&2
        echo "  Generate keys at https://addons.mozilla.org/developers/addon/api/key/" >&2
        echo "  Then export them and re-run this installer." >&2
        exit 1
    fi
    rm -f "$EXT_DIR/web-ext-artifacts"/*.xpi 2>/dev/null || true
    ( cd "$EXT_DIR" && web-ext sign \
        --channel=unlisted \
        --api-key="$WEB_EXT_API_KEY" \
        --api-secret="$WEB_EXT_API_SECRET" )
    xpis=( "$EXT_DIR/web-ext-artifacts"/*.xpi )
fi

# --- Symlink helper into PATH ---
mkdir -p "$(dirname "$HELPER_DEST")"
ln -sfn "$HELPER_SRC" "$HELPER_DEST"
chmod +x "$HELPER_SRC"
echo "Linked helper: $HELPER_DEST -> $HELPER_SRC"

# --- Write native messaging host manifest (real file, not symlink) ---
mkdir -p "$(dirname "$NATIVE_DEST")"
sed "s|__HELPER_PATH__|$HELPER_DEST|" "$NATIVE_TPL" > "$NATIVE_DEST"
echo "Wrote native messaging host manifest: $NATIVE_DEST"

# --- Symlink Omarchy hook ---
mkdir -p "$HOOK_DEST_DIR"
ln -sfn "$HOOK_SRC" "$HOOK_DEST"
chmod +x "$HOOK_SRC"
echo "Linked hook: $HOOK_DEST -> $HOOK_SRC"

# --- Final manual step ---
XPI="${xpis[0]:-}"
cat <<EOF

Done. One manual step remains:
  1. Open Firefox → about:addons
  2. Click the gear icon → "Install Add-on From File…"
  3. Pick: ${XPI:-"<no signed XPI yet — re-run with --rebuild and creds set>"}

After installing, switch themes with 'omarchy theme set <name>' and Firefox
chrome should repaint live.

Helper logs: about:debugging → This Firefox → Inspect on the extension → Console
Hook breadcrumb: cat ~/.local/state/omarchy-firefox-theme/last-hook
EOF
