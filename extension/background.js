"use strict";

const HOST_NAME = "omarchy_firefox_bridge";
const MAX_BACKOFF_MS = 60_000;
let port = null;
let backoffMs = 1_000;

function scheduleReconnect() {
  browser.alarms.create("reconnect", { delayInMinutes: backoffMs / 60_000 });
  backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
}

function handleDisconnect(sourcePort) {
  if (port !== sourcePort) return;
  const error = sourcePort.error || browser.runtime.lastError;
  console.warn("[omarchy-firefox-bridge] native port disconnected", error);
  port = null;
  scheduleReconnect();
}

async function handleMessage(sourcePort, message) {
  try {
    const handled = await OmarchyBridgeBroker.handleNativeMessage(
      message,
      browser,
      (response) => {
        if (port === sourcePort) sourcePort.postMessage(response);
      },
    );
    if (handled && port === sourcePort) backoffMs = 1_000;
  } catch (error) {
    console.error("[omarchy-firefox-bridge] broker operation failed", error);
  }
}

function connect() {
  if (port) return;
  try {
    port = browser.runtime.connectNative(HOST_NAME);
  } catch (error) {
    console.error("[omarchy-firefox-bridge] connectNative failed", error);
    scheduleReconnect();
    return;
  }
  const connectedPort = port;
  connectedPort.onMessage.addListener((message) => handleMessage(connectedPort, message));
  connectedPort.onDisconnect.addListener(() => handleDisconnect(connectedPort));
  console.info("[omarchy-firefox-bridge] connected to native helper");
}

browser.runtime.onStartup.addListener(connect);
browser.runtime.onInstalled.addListener(connect);
browser.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "reconnect") connect();
});
connect();
