# Omarchy Firefox Bridge

Omarchy Firefox Bridge is a local Firefox gateway for Omarchy 4. It keeps
Firefox chrome colors synchronized with the current Omarchy theme and exposes
a bounded snapshot-and-activate interface for desktop components such as a
workspace menu.

The extension is named **Omarchy Bridge**. Its Gecko ID is
`omarchy-bridge@interslice.systems`; its native-host name is
`omarchy_firefox_bridge`.

Source: <https://github.com/interslice-systems/omarchy-firefox-bridge>. Built
for one desk. Shared in case it's useful on yours. No warranty, no promises,
no roadmap — but if it breaks in an interesting way, an issue is welcome.
MIT licensed; see `LICENSE`.

## Requirements

- Omarchy 4
- Firefox 142 or newer
- Python 3.11 or newer
- Bubblewrap
- inotify-tools
- Node 20 or newer and npm for tests, linting, and signing

Install runtime packages from official repositories:

```bash
sudo pacman -S --needed bubblewrap firefox inotify-tools python
```

Install development tools from official repositories and npm:

```bash
sudo pacman -S --needed nodejs npm
npm ci
```

The project does not use the AUR.

## Local Installation

```bash
./install.sh
```

The installer is idempotent, local-only, and refuses to run as root. It
requires the production runtimes at their exact `/usr/bin` paths, verifies
Omarchy 4 and Python 3.11 or newer, and installs copied regular files rather
than links back into the repository.

The installed product is exactly:

```text
~/.local/bin/omarchy-firefox-bridge
~/.local/lib/omarchy-firefox-bridge/
  libexec/omarchy-firefox-bridge-sandbox
  src/omarchy_firefox_bridge/__init__.py
  src/omarchy_firefox_bridge/client.py
  src/omarchy_firefox_bridge/colors.py
  src/omarchy_firefox_bridge/framing.py
  src/omarchy_firefox_bridge/host.py
  src/omarchy_firefox_bridge/protocol.py
  src/omarchy_firefox_bridge/sandbox_probe.py
  src/omarchy_firefox_bridge/socket_server.py
  src/omarchy_firefox_bridge/theme.py
~/.mozilla/native-messaging-hosts/omarchy_firefox_bridge.json
~/.config/omarchy/hooks/theme-set.d/omarchy-firefox-bridge
```

The manifest directory is mode `0700`, the manifest is `0600`, executable
files are `0755`, and installed Python files are `0644`. The installer also
keeps its owner-only serialization lock at:

```text
~/.local/state/omarchy-firefox-bridge/install.lock
```

The lock directory is mode `0700` and the lock file is `0600`. While holding
that lock, the installer validates mutable destinations, stages every
replacement beside its destination, swaps the complete product as one
transaction, asks Omarchy to install the hook, and verifies the exact installed
inventory, contents, ownership, and modes.
Handled failures and `SIGINT` or `SIGTERM` before commit trigger in-process rollback
of prior active and legacy entries, directory modes, and newly created parents;
the persistent lock state remains. A successful commit removes transaction
backups, staging entries, and the three known legacy installation entries.

`SIGKILL`, power loss, or a machine crash cannot run that rollback. If one
occurs during publication, an active entry may be absent and hidden entries
containing `.stage.` or `.backup.` may remain beside their destinations. A
blind rerun installs the current product but does not restore the exact previous
tree or remove that debris. First confirm the interrupted installer is no
longer running, inspect the active and hidden entries in the destination
parents listed above, and preserve or restore any backup needed for recovery.
Then rerun `./install.sh`, confirm it succeeds and the active inventory is
complete, and only then remove confirmed stale stage or backup entries.

It does not install packages, contact AMO, use signing credentials, or install
an XPI.

## Architecture

Firefox loads `extension/broker.js` and `extension/background.js`, which connect
to the native host with `runtime.connectNative("omarchy_firefox_bridge")`.
Firefox owns the helper lifetime and terminates it when the extension
disconnects or Firefox exits. There is no systemd service, daemon, pidfile, or
persistent tab database.

The native-host manifest points at `~/.local/bin/omarchy-firefox-bridge`.
Firefox supplies the manifest path and extension ID as process arguments. The
executable dispatches on the first argument alone: `tabs`, `activate`, and
`open` are CLI subcommands regardless of how many further arguments follow
(the client itself validates arity and reports `invalid-request` on a bad
command line), and every other first argument -- including the extension ID
Firefox actually passes -- enters native-host mode.

Native-host mode enters Bubblewrap with:

- a separate network namespace;
- read-only `/usr` and installed bridge source;
- read-only `~/.local/state/omarchy/current`;
- no general home-directory contents;
- tmpfs `/tmp`;
- one writable `$XDG_RUNTIME_DIR/omarchy-firefox-bridge` directory.

Binding the Omarchy `current` parent is deliberate. Omarchy 4 atomically
replaces its `theme` directory, and binding only that child would pin the old
directory inode and hide later theme changes.

## Color Synchronization

The helper reads only:

```text
~/.local/state/omarchy/current/theme/colors.toml
```

It sends the current palette immediately after startup and watches the
`current` directory for Omarchy's atomic theme replacement. There is no
fallback to the pre-Omarchy-4 configuration path.

Color messages are:

```json
{"type":"theme","theme":{}}
```

The extension applies them with `browser.theme.update({colors: theme})`.

The installed theme-set hook is a diagnostic breadcrumb, not the transport.
It records only timestamp and theme name at:

```text
$XDG_STATE_HOME/omarchy-firefox-bridge/last-theme-set
```

## Tab Projection

The extension requests exactly `theme`, `nativeMessaging`, `alarms`, and
`tabs`. It has no host permissions, content scripts, scripting permission,
cookies, history, storage, or web-request access.

`tabs` necessarily exposes title, URL, pending URL, and favicon references to
the extension. Before data leaves Firefox, the broker emits only:

```json
{
  "tabId": 123,
  "windowId": 456,
  "index": 4,
  "title": "Issue 401",
  "displayUrl": "example.com/issues/401",
  "favicon": "data:image/png;base64,iVBORw0KGgo=",
  "active": false
}
```

Titles are bounded to 1,024 Unicode code points and display URLs to 4,096.
HTTP(S) schemes and credentials are removed, `about:` retains only its page
name, and every other scheme exposes only the scheme so `data:`, `javascript:`,
`blob:`, and similar values cannot carry embedded payloads. Favicons must be
strict base64 raster data URLs no larger than 64 KiB decoded; remote, internal,
SVG, malformed, and oversized values become empty strings so the caller can
use a packaged fallback. Raw URL and pending URL fields never cross native
messaging.

The extension declares `incognito: "not_allowed"` and still filters any tab
marked incognito. It queries only `{windowType: "normal"}`.

## Native Protocol

Native messages use a four-byte little-endian length followed by UTF-8 JSON.
The JSON body is capped at 1 MiB from helper to extension and 2 MiB from
extension to helper. Request IDs are 1-64 printable ASCII characters.

Helper to extension:

```json
{"type":"tabs.list","requestId":"32-hex-characters"}
{"type":"tabs.activate","requestId":"32-hex-characters","tabId":123,"windowId":456}
```

Extension to helper:

```json
{"type":"tabs.result","requestId":"32-hex-characters","tabs":[]}
{"type":"tabs.activated","requestId":"32-hex-characters","ok":true}
```

After sending `tabs.list` or `tabs.activate`, the helper waits up to 400 ms for
the correlated extension response.

Activation is allowed only for an ID pair in the latest successful snapshot.
The extension then re-reads that tab, verifies its current window, and calls
`browser.tabs.update(tabId, {active: true})`. No bridge command accepts a URL
or requests navigation.

## Local Socket And CLI

The host creates:

```text
$XDG_RUNTIME_DIR/omarchy-firefox-bridge/bridge.sock
```

The directory is mode `0700`; the socket is mode `0600`. Each connection sends
one newline-terminated JSON request and receives one newline-terminated JSON
response. A request is capped at 24,576 bytes including its terminating
newline, sized to accommodate a 4,096-code-point URL in 4-byte UTF-8 plus a
1,024-code-point window title.

```bash
omarchy-firefox-bridge tabs
omarchy-firefox-bridge activate 456 123
omarchy-firefox-bridge open --toplevel-title T --url https://example.com/
omarchy-firefox-bridge open --toplevel-title T --url https://example.com/ \
  --group oracle --color yellow
```

`open` correlates `--toplevel-title` against an existing Firefox window title,
creates a tab there for `--url`, and optionally files it into a tab group
named by `--group` (created if it does not already exist), coloured by
`--color`. `--color` requires `--group`.

`url` is 1-4,096 code points; `toplevelTitle` is 1-1,024 code points;
`group.title` is 1-64 code points. `group.color` is one of `blue`, `cyan`,
`grey`, `green`, `orange`, `pink`, `purple`, `red`, `yellow` (note `grey`, not
`gray`).

Requests:

```json
{"action":"tabs"}
{"action":"activate","windowId":456,"tabId":123}
{"action":"open","url":"https://example.com/","toplevelTitle":"T"}
{"action":"open","url":"https://example.com/","toplevelTitle":"T","group":{"title":"oracle","color":"yellow"}}
```

Responses:

```json
{"ok":true,"tabs":[]}
{"ok":true}
{"ok":false,"error":"unavailable"}
{"ok":false,"error":"timeout"}
{"ok":false,"error":"invalid-request"}
{"ok":false,"error":"stale-tab"}
{"ok":false,"error":"bridge-error"}
{"ok":false,"error":"no-window"}
{"ok":false,"error":"ambiguous-window"}
{"ok":false,"error":"create-failed"}
```

`no-window`, `ambiguous-window`, and `create-failed` are reported only for
`open`: no window matched `--toplevel-title`, more than one did, or the
extension's `tabs.create` call failed. Any extension error string outside
that allowlist is relayed as `bridge-error` rather than passed through
verbatim.

Exit `0` means success, `2` means invalid CLI input, and `3` means the bridge
was unavailable or rejected the operation. Missing sockets fail immediately;
after connecting, the CLI has one 500 ms deadline for sending its request and
receiving the complete response. The CLI always writes one compact JSON object
and never logs tab payloads.

## Automated Verification

```bash
npm ci
npm test
```

The gate runs all 79 Python unit tests, 15 extension broker and manifest tests,
warning-as-error `web-ext lint`, the live Bubblewrap sandbox/host smoke test,
14 adversarial installer tests in fake home directories, and Bash syntax
checks.

## Temporary Extension Verification

Install the native side first:

```bash
./install.sh
```

In Firefox open `about:debugging#/runtime/this-firefox`, select **Load Temporary
Add-on**, and choose `extension/manifest.json`. The temporary extension lasts
until Firefox restarts. Use its **Inspect** action to view generic connection
and failure logs.

Verify the projected snapshot:

```bash
omarchy-firefox-bridge tabs | jq .
```

Choose an ID pair from that result and activate it:

```bash
snapshot=$(omarchy-firefox-bridge tabs)
window_id=$(jq -r '.tabs[] | select(.active == false) | .windowId' <<<"$snapshot" | head -n1)
tab_id=$(jq -r '.tabs[] | select(.active == false) | .tabId' <<<"$snapshot" | head -n1)
omarchy-firefox-bridge activate "$window_id" "$tab_id" | jq .
```

Exercise one window with several tabs, multiple Firefox windows, duplicate
titles, a title changing during a snapshot, a tab closing before activation,
internal pages, missing favicons, a long URL, and approximately 15 tabs.
Disable the temporary extension and confirm `tabs` fails closed. Re-enable it
and verify chrome colors across an Omarchy theme change.

Temporary installation does not present the complete signed-install permission
and data-use prompt. Check that prompt again after signing.

## AMO Signing

Signing is human-gated and intentionally separate from `install.sh`. Firefox
stable requires a signed XPI for persistent installation. The project uses
AMO's unlisted/self-distributed channel.

After temporary verification and a clean `npm test`, authenticate to Mozilla
with 2FA and obtain AMO JWT credentials from
`https://addons.mozilla.org/developers/addon/api/key/`. Keep them out of the
repository and shell history:

```bash
read -r -p 'AMO JWT issuer: ' WEB_EXT_API_KEY
read -r -s -p 'AMO JWT secret: ' WEB_EXT_API_SECRET
printf '\n'
export WEB_EXT_API_KEY WEB_EXT_API_SECRET
npm run sign
unset WEB_EXT_API_KEY WEB_EXT_API_SECRET
```

The manifest declares required `websiteContent` and `browsingActivity` data
use because projected titles and URLs cross from the local browser into a local
native process. The extension does not transmit them over a network or persist
them.

On the first submission, `web-ext sign` creates the unlisted AMO record and AMO
checks that `omarchy-bridge@interslice.systems` is unique; there is no separate
Developer Hub record to pre-create. After submission, inspect the record at
`https://addons.mozilla.org/developers/addons` and confirm the Gecko ID, version,
unlisted channel, and required `websiteContent` and `browsingActivity`
declarations. `web-ext` waits for approval and downloads the signed XPI into
`extension/web-ext-artifacts/`. AMO may require manual review because of `tabs`,
`nativeMessaging`, and the data declarations. Killing the local polling process
does not cancel a submitted AMO review.

Install the approved XPI through `about:addons` -> gear menu -> **Install Add-on
From File**. Verify the real permission prompt, restart Firefox, confirm the
native connection returns, switch an Omarchy theme, list tabs, and activate one
tab.

AMO rejects duplicate versions for one Gecko ID. Increment
`extension/manifest.json` before every later submission, run `npm test`, commit
the version change, and then sign.

## Failure Behavior

| Condition | Result |
|---|---|
| Extension or helper absent | CLI returns `unavailable`; desktop caller keeps its Firefox window fallback |
| Connected helper does not answer | CLI returns `timeout` within 500ms |
| Malformed or oversized extension-to-helper native frame | Host disconnects and closes the local service; a fresh host starts with an empty activation allowlist and requires a new snapshot |
| Correctly framed native response carries a correlated protocol-invalid payload | Request returns `bridge-error`; previous activation allowlist remains |
| Tab moved or closed after snapshot | Activation fails; no URL is opened |
| Favicon is missing or unsafe | Empty favicon field; caller uses packaged fallback |
| Omarchy palette is absent or malformed | Theme push is skipped; host remains available |
| Runtime path is occupied by an active host | A second host refuses to replace its socket |

## Security Boundary

Firefox has no title-only permission. Trust rests on the small signed broker
because `tabs` can expose URLs and its update API is navigation-capable. The
bridge narrows that authority with exact permissions, strict CSP, no host
permissions, no URL-bearing commands, projected fields, bounded messages,
owner-only local IPC, no payload logs, an isolated network namespace with no
usable interfaces or routes, and no persistent writable helper path.
