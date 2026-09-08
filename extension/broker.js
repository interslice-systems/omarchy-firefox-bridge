(function installBroker(root) {
  "use strict";

  const TITLE_LIMIT = 1024;
  const URL_LIMIT = 4096;
  const URL_INPUT_LIMIT = URL_LIMIT * 2;
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
  const FAVICON_BASE64_LIMIT = Math.ceil(FAVICON_LIMIT / 3) * 4;
  const FAVICON_DATA_URL_LIMIT =
    "data:image/vnd.microsoft.icon;base64,".length + FAVICON_BASE64_LIMIT;
  const URL_LIMIT_OPEN = 4096;
  const TOPLEVEL_TITLE_LIMIT = 1024;
  const GROUP_TITLE_LIMIT = 64;
  const TOPLEVEL_SUFFIX = " — Mozilla Firefox";
  const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;
  const GROUP_COLORS = new Set([
    "blue", "cyan", "grey", "green", "orange", "pink", "purple", "red", "yellow",
  ]);
  const OPEN_ERRORS = {
    NO_WINDOW: "no-window",
    AMBIGUOUS: "ambiguous-window",
    INVALID: "invalid-request",
    CREATE_FAILED: "create-failed",
  };
  // One promise chain per window, so concurrent opens cannot both observe
  // "no group" and both create one. The socket server runs up to 8 handlers.
  const groupQueues = new Map();

  function boundedString(value, limit) {
    const output = [];
    const iterator = String(value ?? "")[Symbol.iterator]();
    while (output.length < limit) {
      const next = iterator.next();
      if (next.done) break;
      output.push(next.value);
    }
    return output.join("");
  }

  function validRequestId(value) {
    return typeof value === "string" && /^[\x20-\x7e]{1,64}$/.test(value);
  }

  function displayUrl(value) {
    try {
      const raw = String(value ?? "");
      if (boundedString(raw, URL_INPUT_LIMIT) !== raw) return "";
      const parsed = new URL(raw);
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
    if (value.length > FAVICON_DATA_URL_LIMIT) return "";
    const match = /^data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})$/.exec(value);
    if (!match || !RASTER_TYPES.has(match[1].toLowerCase())) return "";
    if (match[2].length === 0 || match[2].length % 4 !== 0) return "";
    if (match[2].length > FAVICON_BASE64_LIMIT) return "";
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

  function safeOpenUrl(value) {
    if (typeof value !== "string") return "";
    if (value.length === 0 || boundedString(value, URL_LIMIT_OPEN) !== value) return "";
    if (CONTROL_CHARACTERS.test(value)) return "";
    let parsed;
    try {
      parsed = new URL(value);
    } catch (_error) {
      return "";
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    // Return the canonical serialisation: the value checked is the value that
    // navigates, which removes every parser-differential argument.
    return parsed.href;
  }

  function safeGroup(value) {
    if (value === undefined) return null;
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const title = value.title;
    if (
      typeof title !== "string" ||
      title.length === 0 ||
      boundedString(title, GROUP_TITLE_LIMIT) !== title
    ) {
      return false;
    }
    if (CONTROL_CHARACTERS.test(title)) return false;
    if (value.color !== undefined && !GROUP_COLORS.has(value.color)) return false;
    return value.color === undefined ? { title } : { title, color: value.color };
  }

  async function correlateWindow(browserApi, toplevelTitle) {
    if (
      typeof toplevelTitle !== "string" ||
      toplevelTitle.length === 0 ||
      boundedString(toplevelTitle, TOPLEVEL_TITLE_LIMIT) !== toplevelTitle
    ) {
      return { error: OPEN_ERRORS.INVALID };
    }
    let active;
    try {
      active = await browserApi.tabs.query({ windowType: "normal", active: true });
    } catch (_error) {
      return { error: OPEN_ERRORS.NO_WINDOW };
    }
    if (!Array.isArray(active)) return { error: OPEN_ERRORS.NO_WINDOW };
    const matches = active.filter(
      (tab) =>
        `${boundedString(tab && tab.title, TITLE_LIMIT)}${TOPLEVEL_SUFFIX}` === toplevelTitle,
    );
    if (matches.length === 0) return { error: OPEN_ERRORS.NO_WINDOW };
    if (matches.length > 1) return { error: OPEN_ERRORS.AMBIGUOUS };
    const windowId = matches[0].windowId;
    if (!Number.isSafeInteger(windowId) || windowId <= 0) {
      return { error: OPEN_ERRORS.NO_WINDOW };
    }
    return { windowId };
  }

  async function attachToGroup(browserApi, windowId, tabId, group) {
    // Query by window only. Firefox globs a title filter (new MatchGlob), and
    // "*" and "?" survive wsid_session_name, so filtering happens here.
    const groups = await browserApi.tabGroups.query({ windowId });
    const matches = (Array.isArray(groups) ? groups : []).filter(
      (candidate) => candidate && candidate.title === group.title,
    );
    if (matches.length > 1) return;
    if (matches.length === 1) {
      await browserApi.tabs.group({ tabIds: tabId, groupId: matches[0].id });
      return;
    }
    const groupId = await browserApi.tabs.group({
      tabIds: tabId,
      createProperties: { windowId },
    });
    const properties = { title: group.title };
    if (group.color) properties.color = group.color;
    await browserApi.tabGroups.update(groupId, properties);
  }

  function enqueueGroup(windowId, work) {
    const previous = groupQueues.get(windowId) || Promise.resolve();
    const next = previous.then(work, work).catch(() => {});
    groupQueues.set(windowId, next);
    return next;
  }

  // Test seam: resolves once every queued grouping operation has settled.
  async function groupsSettled() {
    await Promise.all(Array.from(groupQueues.values()));
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
    if (message.type === "tabs.open") {
      const url = safeOpenUrl(message.url);
      const group = safeGroup(message.group);
      if (!url || group === false) {
        respond({
          type: "tabs.opened",
          requestId: message.requestId,
          ok: false,
          error: OPEN_ERRORS.INVALID,
        });
        return true;
      }
      const correlated = await correlateWindow(browserApi, message.toplevelTitle);
      if (correlated.error) {
        respond({
          type: "tabs.opened",
          requestId: message.requestId,
          ok: false,
          error: correlated.error,
        });
        return true;
      }
      let tabId;
      try {
        const tab = await browserApi.tabs.create({
          windowId: correlated.windowId,
          url,
          active: true,
        });
        tabId = tab.id;
      } catch (_error) {
        respond({
          type: "tabs.opened",
          requestId: message.requestId,
          ok: false,
          error: OPEN_ERRORS.CREATE_FAILED,
        });
        return true;
      }
      // Answer before grouping: the tab is the deliverable, the group is the
      // garnish, and a short vulnerable interval means fewer duplicate tabs.
      respond({
        type: "tabs.opened",
        requestId: message.requestId,
        ok: true,
        tabId,
      });
      if (group) {
        enqueueGroup(correlated.windowId, () =>
          attachToGroup(browserApi, correlated.windowId, tabId, group),
        );
      }
      return true;
    }
    return false;
  }

  root.OmarchyBridgeBroker = Object.freeze({
    boundedString,
    correlateWindow,
    displayUrl,
    groupsSettled,
    handleNativeMessage,
    projectTabs,
    safeFavicon,
    safeGroup,
    safeOpenUrl,
    validRequestId,
  });
})(globalThis);
