# Omarchy Firefox Theme Sync

Live-syncs Firefox chrome colors with the current Omarchy theme. When you run
`omarchy theme set <name>`, Firefox's URL bar, tabs, toolbar, popups, sidebar,
etc. repaint within ~200ms — no Firefox restart.

## How it works

```
omarchy theme set foo
  → ~/.config/omarchy/current/theme/colors.toml  (atomically replaced)
    → inotify event picked up by helper (Python script spawned by Firefox)
      → 4-byte length-prefixed JSON over native messaging stdio
        → background.js calls browser.theme.update({colors: …})
          → Firefox chrome repaints
```

The Python helper isn't a daemon — Firefox spawns it as a child when the
extension calls `runtime.connectNative()`, and reaps it when Firefox exits.
No systemd unit, no pidfile.

## Install

One-time prereqs:

```bash
sudo pacman -S inotify-tools
npm i -g web-ext        # or use the AUR package
```

Get an AMO API key at https://addons.mozilla.org/developers/addon/api/key/
(free Mozilla developer account, ~5 minutes). Export the credentials:

```bash
export WEB_EXT_API_KEY=user:12345:678
export WEB_EXT_API_SECRET=...
```

Then:

```bash
./install.sh
```

The installer signs the extension via Mozilla's unlisted self-distribution
signing (extension is real-signed, not listed on AMO), symlinks the helper
and hook into the right places, and writes the native messaging host
manifest. It prints a final manual step: install the signed XPI via Firefox's
`about:addons → ⚙ → Install Add-on From File…` dialog (manual because
Firefox stable has no clean CLI path for installing unlisted XPIs).

Re-run `./install.sh` after edits to symlinked files; pass `--rebuild` to
force re-signing the extension.

## Verify

Mapping smoke test (no Firefox required):

```bash
python3 -c '
import tomllib, sys
sys.path.insert(0, "helper")
from colors import omarchy_to_firefox_theme
with open("/home/chris/.config/omarchy/current/theme/colors.toml", "rb") as f:
    import json; print(json.dumps(omarchy_to_firefox_theme(tomllib.load(f)), indent=2))
'
```

End-to-end (after install): with Firefox running, `omarchy theme set
tokyo-night` — chrome should repaint within ~200ms.

Helper logs: `about:debugging → This Firefox → Inspect` on the extension →
Console (filter `[omarchy-firefox-theme]`).

Hook breadcrumb (proves Omarchy side fired): `cat
~/.local/state/omarchy-firefox-theme/last-hook`.

## Troubleshooting

- **Theme doesn't update** — check extension console for "connected to native
  helper" on startup and "pushed theme (…)" on each change.
- **No "connected" log** — the native messaging host manifest path is wrong.
  Verify `~/.mozilla/native-messaging-hosts/omarchy_firefox_theme.json` has
  the correct absolute path to the helper, and that the helper is executable.
- **Helper crashes mid-session** — `background.js` reconnects with exponential
  backoff (1s → 60s cap). A fixed helper picks up within a minute, or on the
  next theme change.
- **Wrong shades on light themes** — adjust the `_lift()` amounts in
  `helper/colors.py`. The dark/light branch is automatic; the lift factors
  are not theme-aware.

## Files

- `helper/colors.py` — palette → Firefox slot mapping (pure stdlib)
- `helper/omarchy-firefox-theme-helper` — native messaging entry point
- `extension/manifest.json` + `extension/background.js` — MV3 WebExtension
- `native-host/omarchy_firefox_theme.json.tpl` — host manifest template
- `hook/firefox-color-sync` — Omarchy `theme-set.d` debug breadcrumb hook
- `install.sh` — idempotent installer
