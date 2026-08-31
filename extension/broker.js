(function installBroker(root) {
  "use strict";

  const TITLE_LIMIT = 1024;
  const URL_LIMIT = 4096;
  const FAVICON_LIMIT = 64 * 1024;
  const RASTER_TYPES = new Set([
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "image/bmp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
  ]);

  function boundedString(value, limit) {
    return [...String(value ?? "")].slice(0, limit).join("");
  }

  function validRequestId(value) {
    return typeof value === "string" && /^[\x20-\x7e]{1,64}$/.test(value);
  }

  function displayUrl(value) {
    try {
      const parsed = new URL(String(value ?? ""));
      const suffix = `${parsed.pathname}${parsed.search}${parsed.hash}`;
      let rendered;
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        rendered = `${parsed.host}${suffix}`;
      } else if (parsed.protocol === "about:") {
        rendered = `about:${parsed.pathname}`;
      } else {
        rendered = parsed.protocol;
      }
      return boundedString(rendered, URL_LIMIT);
    } catch (_error) {
      return "";
    }
  }

  function safeFavicon(value) {
    if (typeof value !== "string") return "";
    const match = /^data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})$/.exec(value);
    if (!match || !RASTER_TYPES.has(match[1].toLowerCase())) return "";
    if (match[2].length === 0 || match[2].length % 4 !== 0) return "";
    if (match[2].length > Math.ceil(FAVICON_LIMIT / 3) * 4) return "";
    try {
      return atob(match[2]).length <= FAVICON_LIMIT ? value : "";
    } catch (_error) {
      return "";
    }
  }

  function projectTabs(tabs) {
    if (!Array.isArray(tabs)) throw new TypeError("invalid tab snapshot");

    const projected = [];
    const seenTabIds = new Set();
    const seenIndexes = new Set();
    const activeCounts = new Map();

    for (const tab of tabs) {
      if (!tab || typeof tab !== "object" || Array.isArray(tab)) {
        throw new TypeError("invalid tab snapshot");
      }
      if (tab.incognito === true) continue;
      if (
        !Number.isInteger(tab.id) ||
        tab.id <= 0 ||
        !Number.isInteger(tab.windowId) ||
        tab.windowId <= 0 ||
        !Number.isInteger(tab.index) ||
        tab.index < 0
      ) {
        throw new TypeError("invalid tab snapshot");
      }

      const indexKey = `${tab.windowId}:${tab.index}`;
      if (seenTabIds.has(tab.id) || seenIndexes.has(indexKey)) {
        throw new TypeError("invalid tab snapshot");
      }
      seenTabIds.add(tab.id);
      seenIndexes.add(indexKey);

      const active = tab.active === true;
      activeCounts.set(tab.windowId, (activeCounts.get(tab.windowId) || 0) + (active ? 1 : 0));
      projected.push({
        tabId: tab.id,
        windowId: tab.windowId,
        index: tab.index,
        title: boundedString(tab.title, TITLE_LIMIT),
        displayUrl: displayUrl(tab.url),
        favicon: safeFavicon(tab.favIconUrl),
        active,
      });
    }

    for (const activeCount of activeCounts.values()) {
      if (activeCount !== 1) throw new TypeError("invalid tab snapshot");
    }
    return projected.sort(
      (left, right) => left.windowId - right.windowId || left.index - right.index,
    );
  }

  async function handleNativeMessage(message, browserApi, respond) {
    if (message?.type === "theme" && message.theme && typeof message.theme === "object") {
      await browserApi.theme.update({ colors: message.theme });
      return true;
    }
    if (!validRequestId(message?.requestId)) return false;
    if (message.type === "tabs.list") {
      try {
        const tabs = await browserApi.tabs.query({ windowType: "normal" });
        respond({ type: "tabs.result", requestId: message.requestId, tabs: projectTabs(tabs) });
      } catch (_error) {
        respond({ type: "tabs.result", requestId: message.requestId, tabs: [] });
      }
      return true;
    }
    if (message.type === "tabs.activate") {
      let ok = false;
      if (
        Number.isInteger(message.tabId) &&
        message.tabId > 0 &&
        Number.isInteger(message.windowId) &&
        message.windowId > 0
      ) {
        try {
          const tab = await browserApi.tabs.get(message.tabId);
          if (tab.windowId === message.windowId) {
            await browserApi.tabs.update(message.tabId, { active: true });
            ok = true;
          }
        } catch (_error) {
          ok = false;
        }
      }
      respond({ type: "tabs.activated", requestId: message.requestId, ok });
      return true;
    }
    return false;
  }

  root.OmarchyBridgeBroker = Object.freeze({
    boundedString,
    displayUrl,
    handleNativeMessage,
    projectTabs,
    safeFavicon,
    validRequestId,
  });
})(globalThis);
