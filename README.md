# Omarchy Color Sync

Live-syncs Firefox chrome colors with the current Omarchy theme. When you run
`omarchy theme set <name>`, Firefox's URL bar, tabs, toolbar, popups, and
sidebar repaint within ~200ms — no Firefox restart.

(The extension is named "Omarchy Color Sync" rather than "Omarchy Firefox
Theme Sync" because AMO's automated validator rejects extension names
containing the words "Firefox" or "Mozilla" under their trademark policy.
The README, code, project directory, and gecko ID all reference Firefox
because nominative use outside the `name` field is fine.)

## How it works

```
omarchy theme set foo
  → ~/.config/omarchy/current/theme/colors.toml  (atomically replaced)
    → inotify event picked up by helper (Python script spawned by Firefox)
      → 4-byte length-prefixed JSON over native messaging stdio
        → background.js calls browser.theme.update({colors: …})
          → Firefox chrome repaints
```

The Python helper is not a daemon — Firefox spawns it as a child when the
extension calls `runtime.connectNative()`, and reaps it when Firefox exits.
No systemd unit, no pidfile, no restart logic.

## Install

One-time prereqs:

```bash
sudo pacman -S inotify-tools
yay -S web-ext-bin            # or paru, or: npm i -g web-ext
```

Get an AMO API key (see [Signing the extension](#signing-the-extension)
below for what this is and why it's needed), then:

```bash
export WEB_EXT_API_KEY=user:12345:678
export WEB_EXT_API_SECRET=...
cd ~/miyagi/scripts/firefox-color-sync
./install.sh
```

The installer:
1. Symlinks the helper into `~/.local/bin/`
2. Writes `~/.mozilla/native-messaging-hosts/omarchy_firefox_theme.json` with the resolved helper path
3. Symlinks the Omarchy hook into `~/.config/omarchy/hooks/theme-set.d/`
4. Signs the extension via AMO (slow; see Signing section)
5. Prints the path to the signed XPI for manual install in Firefox

Re-run any time — the symlinks/manifest steps are idempotent. Pass
`--rebuild` to force re-signing after editing the extension.

Final step (manual, since Firefox stable has no clean CLI path for unlisted XPIs):

1. Open Firefox → `about:addons`
2. Click the gear ⚙ → **Install Add-on From File…**
3. Pick the XPI path that `install.sh` printed

## Verify

Standalone smoke tests (no Firefox required):

```bash
./smoke.sh
```

Covers: color mapping against the current theme, dark/light branch with a
synthetic light palette, full helper end-to-end with the real native
messaging wire protocol, and `web-ext lint` if available.

End-to-end with Firefox running:

```bash
omarchy theme set tokyo-night   # any installed theme
omarchy theme set aether        # switch back
```

Chrome should repaint within ~200ms each time.

Helper logs: `about:debugging → This Firefox → Inspect` on the extension →
Console (filter `[omarchy-firefox-theme]`).

Hook breadcrumb (proves the Omarchy side of the chain fired):

```bash
cat ~/.local/state/omarchy-firefox-theme/last-hook
```

## Signing the extension

Firefox stable requires signed extensions and ignores
`xpinstall.signatures.required=false`. Mozilla's free path is **unlisted
self-distribution signing**: they sign your XPI for installation, but it
doesn't appear on the public AMO catalog. Real signature, no public listing,
no human review for the unlisted channel in most cases.

### One-time setup

1. **Mozilla developer account + 2FA** at https://addons.mozilla.org. 2FA is
   mandatory now — set up TOTP or a hardware key.
2. **Generate JWT credentials** at
   https://addons.mozilla.org/developers/addon/api/key/. Copy the issuer
   (`user:1234:5678`) and the secret. Stash them in 1Password or
   `~/.config/web-ext/config.json` (chmod 600). Never commit them.
3. **Export for use by `install.sh`**:
   ```bash
   export WEB_EXT_API_KEY='user:1234:5678'
   export WEB_EXT_API_SECRET='...'
   ```

### What `web-ext sign --channel=unlisted` does

1. Builds an XPI from `extension/`.
2. Uploads it to AMO via the v5 submission API using the JWT credentials.
3. AMO runs automated validation (manifest schema, no remote code execution,
   no banned trademarks in `name`, no deprecated APIs).
4. AMO either signs immediately (automated review path) or queues for human
   review.
5. `web-ext` polls for status. When signing is complete, it downloads the
   signed XPI to `extension/web-ext-artifacts/`.

The default polling timeout is ~15 minutes. If AMO needs longer (human
review can take hours to days), `web-ext` times out — but the submission
stays in AMO's queue regardless, and you can re-fetch the signed XPI later.

## Gotchas

- **AMO rejects names with "Firefox" or "Mozilla"** — trademark policy.
  Applies to `name` in `manifest.json`. Nominative use elsewhere is fine.
- **Signed XPIs get hash-prefix filenames** — e.g.,
  `aa6a400863c74485b161-1.0.1.xpi` rather than `omarchy_color_sync-1.0.1.xpi`.
  AMO does this on the unlisted channel; Firefox doesn't care about the
  filename. Just install whichever XPI is in `extension/web-ext-artifacts/`.
- **AMO refuses duplicate versions** — bump `version` in `manifest.json`
  before any re-sign. Same gecko ID + same version = "already exists" error.
- **`nativeMessaging` may trigger manual review** — but doesn't always.
  In practice, the first sign of this extension went through automated
  review in minutes. Don't assume the worst.
- **`web-ext sign` killed during polling is safe** — the submission stays
  in AMO's queue. Recover by downloading the signed XPI from
  https://addons.mozilla.org/developers/ after the approval email arrives.

## Iteration

Edits to the **Python helper** (`helper/colors.py`,
`helper/omarchy-firefox-theme-helper`) don't require re-signing — the helper
is read from disk each time Firefox spawns it. Restart Firefox to pick up
changes; the extension's connection to the old helper drops, and a fresh one
spawns. For quicker feedback, run `./smoke.sh` to see the new mapping output
without touching Firefox.

Edits to the **WebExtension** (`extension/manifest.json`,
`extension/background.js`) have two paths:

**Fast loop — temporary add-on**:
1. `about:debugging → This Firefox → Load Temporary Add-on…`
2. Pick: `~/miyagi/scripts/firefox-color-sync/extension/manifest.json`

Lasts until Firefox restart. Bypasses signing entirely. Runtime behavior is
identical to a signed install — same permissions, same native messaging,
same `browser.theme.update()`. Use this for iterating on `background.js`.

**Release — signed XPI**:
1. Bump `version` in `extension/manifest.json` (e.g., 1.0.1 → 1.0.2).
2. Set `WEB_EXT_API_KEY` / `WEB_EXT_API_SECRET`.
3. `./install.sh --rebuild`.
4. Install the new XPI via `about:addons → ⚙ → Install Add-on From File…`.
   Firefox upgrades in place because the gecko ID is unchanged.

## Troubleshooting

- **Theme doesn't update** — check extension console for "connected to native
  helper" on startup and "pushed theme (…)" on each change.
- **No "connected" log** — the native messaging host manifest is wrong.
  Verify `~/.mozilla/native-messaging-hosts/omarchy_firefox_theme.json`
  has the correct absolute path to the helper, and that the helper is
  executable (`ls -l ~/.local/bin/omarchy-firefox-theme-helper`).
- **Helper crashes mid-session** — `background.js` reconnects with
  exponential backoff (1s → 60s cap), scheduled via `browser.alarms` so
  the timer survives even if the event page idles out after the disconnect.
  A fixed helper picks up within a minute, or on the next theme change.
- **Theme reverts after Firefox relaunch** — extension console should show
  `[omarchy] connected to native helper` within a second or two of launch.
  If it doesn't, the MV3 event page isn't waking on startup. The script
  registers `runtime.onStartup` + `runtime.onInstalled` listeners at module
  load for exactly this reason; check that those calls still exist
  unconditionally at the top level of `background.js`.
- **Wrong shades on light themes** — tune the `_lift()` amounts in
  `helper/colors.py`. The dark/light branch is automatic; the lift factors
  are not theme-aware.
- **`web-ext sign` hangs >5 min** — probably in manual review. Ctrl-C is
  safe; the submission stays in AMO's queue. Wait for the approval email
  and either re-run `install.sh --rebuild` after bumping the version, or
  download the signed XPI directly from the AMO Developer Hub.
- **AMO rejects "Add-on names cannot contain the Mozilla or Firefox
  trademarks"** — trademark policy on the `name` field. Edit
  `manifest.json` to use a neutral name.
- **AMO rejects "Version already exists"** — bump `version` in
  `manifest.json` before re-signing.

## Architecture details

**Native messaging protocol** (Firefox spec):
- Each message: 4-byte little-endian length prefix + UTF-8 JSON body.
- Helper → extension: `{"type": "theme", "theme": { ...firefox theme dict... }}`.
- Extension → helper: not used; helper drains incoming frames but ignores
  them. EOF on stdin = Firefox shutting the helper down.

**Native messaging host manifest** (`~/.mozilla/native-messaging-hosts/omarchy_firefox_theme.json`):
- Tells Firefox where to find the helper executable and which extensions
  may spawn it. The `allowed_extensions` list contains the gecko ID
  (`omarchy-firefox-theme@miyagi.local`), so only this extension can
  connect. The `path` must be absolute; `install.sh` resolves it via `sed`.

**Extension wake-up** (`extension/background.js`):
- MV3 event pages only run when something wakes them. Three top-level
  listeners cover the wake cases: `runtime.onStartup` (browser launch),
  `runtime.onInstalled` (install/update), and `alarms.onAlarm` (reconnect
  timer, set when the native port disconnects). Without these, the script
  stays dormant after a Firefox relaunch and the theme never re-applies —
  Firefox shows whatever static theme `extensions.activeThemeID` names.
  The native port keeps the page alive *during* a session, which is why
  the bug shape is "works perfectly until you quit Firefox."

**Color mapping** (`helper/colors.py`):
- Pure-stdlib palette → Firefox slot translation. Importable and
  unit-testable. Detects dark vs light by WCAG luminance of background vs
  foreground; lifts toward white (dark themes) or black (light themes).
  Routes Omarchy `accent` to highlight slots (focused URL bar border,
  active tab line, sidebar highlight, `icons_attention`) — not to chrome
  fills, because Omarchy accents are tuned for highlighting, not filling.

## Files

- `helper/colors.py` — palette → Firefox slot mapping (pure stdlib)
- `helper/omarchy-firefox-theme-helper` — native messaging entry point
- `extension/manifest.json` + `extension/background.js` — MV3 WebExtension
- `extension/icons/` — generated PNGs (48/96), gradient from background to accent
- `native-host/omarchy_firefox_theme.json.tpl` — host manifest template (helper-path placeholder)
- `hook/firefox-color-sync` — Omarchy `theme-set.d` debug breadcrumb hook
- `install.sh` — idempotent installer (symlinks first, then sign)
- `smoke.sh` — standalone end-to-end test (no Firefox required)
