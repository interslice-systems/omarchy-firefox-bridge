// Native messaging client for omarchy-firefox-theme-helper. Receives theme
// payloads and applies them via browser.theme.update(). Reconnects with
// exponential backoff (1s → 60s) so a recovered helper picks up within a
// minute without spinning if the helper is missing entirely.
//
// MV3 event-page lifecycle: runtime.onStartup wakes the page on Firefox
// launch; runtime.onInstalled wakes it on install/update; alarms.onAlarm
// wakes it when the reconnect timer fires. Without these listeners, the
// page can stay dormant when we need it — after a browser relaunch (no
// onStartup) or after a helper disconnect that lets the page idle out
// before a setTimeout fires (no alarm). We use browser.alarms instead of
// setTimeout for that reason: alarms survive event-page unloads.

const HOST_NAME = "omarchy_firefox_theme";
const MAX_BACKOFF_MS = 60_000;

let port = null;
let backoffMs = 1_000;

function handleMessage(msg) {
  if (msg?.type === "theme" && msg.theme) {
    browser.theme.update({ colors: msg.theme });
  }
}

function handleDisconnect() {
  const err = port?.error || browser.runtime.lastError;
  console.warn("[omarchy] native port disconnected:", err);
  port = null;
  scheduleReconnect();
}

function scheduleReconnect() {
  browser.alarms.create("reconnect", { delayInMinutes: backoffMs / 60_000 });
  backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
}

function connect() {
  if (port) return;
  try {
    port = browser.runtime.connectNative(HOST_NAME);
  } catch (e) {
    console.error("[omarchy] connectNative threw:", e);
    scheduleReconnect();
    return;
  }
  port.onMessage.addListener(handleMessage);
  port.onDisconnect.addListener(handleDisconnect);
  backoffMs = 1_000;
  console.info("[omarchy] connected to native helper");
}

browser.runtime.onStartup.addListener(connect);
browser.runtime.onInstalled.addListener(connect);
browser.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "reconnect") connect();
});
connect();
