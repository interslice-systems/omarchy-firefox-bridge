import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(
  new URL("../../extension/broker.js", import.meta.url),
  "utf8",
).catch(() => "");
const backgroundSource = await readFile(
  new URL("../../extension/background.js", import.meta.url),
  "utf8",
).catch(() => "");

function loadBrokerContext() {
  const context = { atob, URL };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "broker.js" });
  return context;
}

function loadBroker() {
  const context = loadBrokerContext();
  return context.OmarchyBridgeBroker;
}

const png = `data:image/png;base64,${Buffer.from("png").toString("base64")}`;

test("projects, bounds, filters, and sorts tabs", () => {
  const broker = loadBroker();
  assert.ok(broker);
  const projected = broker.projectTabs([
    {
      id: 9,
      windowId: 2,
      index: 3,
      title: "x".repeat(1100),
      url: "https://user:secret@example.com:8443/a?q=1#part",
      pendingUrl: "https://secret.invalid/",
      favIconUrl: png,
      active: true,
      incognito: false,
    },
    {
      id: 3,
      windowId: 1,
      index: 1,
      title: "Internal",
      url: "about:newtab",
      favIconUrl: "chrome://branding/content/icon.svg",
      active: true,
      incognito: false,
    },
    {
      id: 4,
      windowId: 1,
      index: 0,
      title: "Private",
      url: "https://private.invalid/",
      active: false,
      incognito: true,
    },
  ]);

  assert.deepEqual(Object.keys(projected[0]), [
    "tabId",
    "windowId",
    "index",
    "title",
    "displayUrl",
    "favicon",
    "active",
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(projected)), [
    {
      tabId: 3,
      windowId: 1,
      index: 1,
      title: "Internal",
      displayUrl: "about:newtab",
      favicon: "",
      active: true,
    },
    {
      tabId: 9,
      windowId: 2,
      index: 3,
      title: "x".repeat(1024),
      displayUrl: "example.com:8443/a?q=1#part",
      favicon: png,
      active: true,
    },
  ]);
  assert.equal(Object.hasOwn(projected[1], "url"), false);
  assert.equal(Object.hasOwn(projected[1], "pendingUrl"), false);
});

test("bounds Unicode by code point", () => {
  const broker = loadBroker();
  const title = "😀".repeat(1025);
  const [tab] = broker.projectTabs([
    {
      id: 1,
      windowId: 1,
      index: 0,
      title,
      url: `https://example.com/${"😀".repeat(5000)}`,
      active: true,
    },
  ]);
  assert.equal([...tab.title].length, 1024);
  assert.equal([...tab.displayUrl].length, 4096);
});

test("stops title iteration as soon as the code-point limit is reached", () => {
  const context = loadBrokerContext();
  vm.runInContext(
    `
      const originalIterator = String.prototype[Symbol.iterator];
      String.prototype[Symbol.iterator] = function guardedIterator() {
        const iterator = originalIterator.call(this);
        let reads = 0;
        return {
          next() {
            reads += 1;
            if (reads > 1024) throw new Error("title iterator exhausted past limit");
            return iterator.next();
          },
        };
      };
    `,
    context,
  );

  assert.equal(
    context.OmarchyBridgeBroker.boundedString("x".repeat(1_000_000), 1024),
    "x".repeat(1024),
  );
});

test("rejects oversized URLs before parsing website-controlled input", () => {
  const broker = loadBroker();
  const oversized = `https://example.com/${"x".repeat(1_000_000)}`;

  assert.equal(broker.displayUrl(oversized), "");
});

test("rejects malformed, duplicate, and ambiguous snapshots", () => {
  const broker = loadBroker();
  const valid = {
    id: 1,
    windowId: 1,
    index: 0,
    title: "A",
    url: "https://a.test/",
    active: true,
  };

  assert.throws(() => broker.projectTabs([{ ...valid, id: 0 }]), /invalid tab snapshot/);
  assert.throws(() => broker.projectTabs([valid, { ...valid, index: 1 }]), /invalid tab snapshot/);
  assert.throws(
    () => broker.projectTabs([valid, { ...valid, id: 2 }]),
    /invalid tab snapshot/,
  );
  assert.throws(
    () => broker.projectTabs([valid, { ...valid, id: 2, index: 1, active: true }]),
    /invalid tab snapshot/,
  );
  assert.throws(
    () => broker.projectTabs([
      { ...valid, active: false },
      { ...valid, id: 2, index: 1, active: false },
    ]),
    /invalid tab snapshot/,
  );
});

test("filters malformed private records before validation and accepts empty snapshots", () => {
  const broker = loadBroker();
  assert.equal(broker.projectTabs([]).length, 0);
  assert.equal(
    broker.projectTabs([
      { id: 0, windowId: 0, index: -1, active: false, incognito: true },
    ]).length,
    0,
  );
});

test("redacts payload-bearing and unknown URL schemes", () => {
  const broker = loadBroker();
  assert.equal(broker.displayUrl("about:newtab"), "about:newtab");
  assert.equal(broker.displayUrl("data:text/html,<h1>secret</h1>"), "data:");
  assert.equal(broker.displayUrl("javascript:alert('secret')"), "javascript:");
  assert.equal(broker.displayUrl("blob:https://example.com/secret-id"), "blob:");
  assert.equal(broker.displayUrl("view-source:https://secret.test/path"), "view-source:");
});

test("accepts bounded raster favicons and rejects unsafe variants", () => {
  const broker = loadBroker();
  assert.equal(broker.safeFavicon(png), png);
  assert.equal(broker.safeFavicon("data:image/svg+xml;base64,PHN2Zz4="), "");
  assert.equal(broker.safeFavicon("https://example.com/favicon.png"), "");
  assert.equal(broker.safeFavicon("file:///tmp/favicon.png"), "");
  assert.equal(broker.safeFavicon("chrome://branding/content/icon.png"), "");
  assert.equal(broker.safeFavicon("moz-extension://id/favicon.png"), "");
  assert.equal(broker.safeFavicon("data:image/png;base64,"), "");
  assert.equal(broker.safeFavicon("data:image/png;base64,YQ"), "");
  assert.equal(broker.safeFavicon("data:image/png;charset=utf-8;base64,cG5n"), "");
  assert.equal(broker.safeFavicon("data:image/png;base64,not base64"), "");
  const maximum = `data:image/png;base64,${Buffer.alloc(65536).toString("base64")}`;
  const oversized = `data:image/png;base64,${Buffer.alloc(65537).toString("base64")}`;
  assert.equal(broker.safeFavicon(maximum), maximum);
  assert.equal(broker.safeFavicon(oversized), "");
});

test("rejects impossible-size favicons before regular-expression processing", () => {
  const context = loadBrokerContext();
  vm.runInContext(
    `
      RegExp.prototype.exec = function guardedExec() {
        throw new Error("favicon regex processed impossible-size input");
      };
    `,
    context,
  );
  const impossible = `data:image/png;base64,${"A".repeat(1_000_000)}`;

  assert.equal(context.OmarchyBridgeBroker.safeFavicon(impossible), "");
});

test("lists only normal projected tabs", async () => {
  const broker = loadBroker();
  const calls = [];
  const responses = [];
  const browser = {
    tabs: {
      query: async (query) => {
        calls.push(query);
        return [{ id: 7, windowId: 4, index: 0, title: "A", url: "https://a.test/", active: true }];
      },
    },
  };
  const handled = await broker.handleNativeMessage(
    { type: "tabs.list", requestId: "request-1" },
    browser,
    (message) => responses.push(message),
  );
  assert.equal(handled, true);
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{ windowType: "normal" }]);
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
    {
      type: "tabs.result",
      requestId: "request-1",
      tabs: [{ tabId: 7, windowId: 4, index: 0, title: "A", displayUrl: "a.test/", favicon: "", active: true }],
    },
  ]);
});

test("returns an empty list result when querying or projecting fails", async () => {
  const broker = loadBroker();
  const responses = [];
  await broker.handleNativeMessage(
    { type: "tabs.list", requestId: "query-failure" },
    { tabs: { query: async () => { throw new Error("query failed"); } } },
    (message) => responses.push(message),
  );
  await broker.handleNativeMessage(
    { type: "tabs.list", requestId: "projection-failure" },
    { tabs: { query: async () => [{ id: 0, windowId: 1, index: 0, active: true }] } },
    (message) => responses.push(message),
  );
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
    { type: "tabs.result", requestId: "query-failure", tabs: [] },
    { type: "tabs.result", requestId: "projection-failure", tabs: [] },
  ]);
});

test("activates only positive IDs still belonging to the requested window", async () => {
  const broker = loadBroker();
  const updates = [];
  const responses = [];
  const browser = {
    tabs: {
      get: async () => ({ id: 7, windowId: 4 }),
      update: async (...args) => updates.push(args),
    },
  };
  await broker.handleNativeMessage(
    { type: "tabs.activate", requestId: "request-2", tabId: 7, windowId: 4 },
    browser,
    (message) => responses.push(message),
  );
  assert.deepEqual(JSON.parse(JSON.stringify(updates)), [[7, { active: true }]]);
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
    { type: "tabs.activated", requestId: "request-2", ok: true },
  ]);
  await broker.handleNativeMessage(
    { type: "tabs.activate", requestId: "request-invalid", tabId: 0, windowId: 4 },
    browser,
    (message) => responses.push(message),
  );
  assert.deepEqual(JSON.parse(JSON.stringify(updates)), [[7, { active: true }]]);
  assert.deepEqual(JSON.parse(JSON.stringify(responses[1])), {
    type: "tabs.activated",
    requestId: "request-invalid",
    ok: false,
  });
});

test("never navigates and fails closed on a moved or missing tab", async () => {
  const broker = loadBroker();
  const updates = [];
  const responses = [];
  const browser = {
    tabs: {
      get: async () => ({ id: 7, windowId: 99 }),
      update: async (...args) => updates.push(args),
      create: async () => assert.fail("tabs.create must not be called"),
    },
  };
  await broker.handleNativeMessage(
    { type: "tabs.activate", requestId: "request-3", tabId: 7, windowId: 4 },
    browser,
    (message) => responses.push(message),
  );
  assert.deepEqual(updates, []);
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
    { type: "tabs.activated", requestId: "request-3", ok: false },
  ]);
});

test("retains theme updates and rejects malformed request IDs", async () => {
  const broker = loadBroker();
  const themes = [];
  const browser = { theme: { update: async (theme) => themes.push(theme) } };
  assert.equal(
    await broker.handleNativeMessage(
      { type: "theme", theme: { frame: "#000000" } },
      browser,
      () => assert.fail("theme does not produce a response"),
    ),
    true,
  );
  assert.equal(
    await broker.handleNativeMessage(
      { type: "tabs.list", requestId: "é" },
      { tabs: { query: async () => assert.fail("invalid request must be ignored") } },
      () => assert.fail("invalid request must not respond"),
    ),
    false,
  );
  assert.deepEqual(JSON.parse(JSON.stringify(themes)), [{ colors: { frame: "#000000" } }]);
});

test("discards work and disconnects from stale native ports", async () => {
  function event() {
    let listener;
    return {
      addListener(candidate) {
        listener = candidate;
      },
      emit(...args) {
        return listener(...args);
      },
    };
  }

  function makePort() {
    const messages = [];
    return {
      error: null,
      messages,
      onMessage: event(),
      onDisconnect: event(),
      postMessage(message) {
        messages.push(JSON.parse(JSON.stringify(message)));
      },
    };
  }

  let resolveOperation;
  const operation = new Promise((resolve) => {
    resolveOperation = resolve;
  });
  const ports = [makePort(), makePort(), makePort(), makePort()];
  const alarms = [];
  let connections = 0;
  const alarmEvent = event();
  const browser = {
    alarms: {
      create: (_name, alarm) => alarms.push(JSON.parse(JSON.stringify(alarm))),
      onAlarm: alarmEvent,
    },
    runtime: {
      connectNative: () => ports[connections++],
      lastError: null,
      onInstalled: event(),
      onStartup: event(),
    },
  };
  const broker = {
    handleNativeMessage: async (_message, _browser, respond) => {
      await operation;
      respond({ type: "tabs.result", requestId: "stale", tabs: [] });
      return true;
    },
  };
  const context = {
    browser,
    console: { error() {}, info() {}, warn() {} },
    OmarchyBridgeBroker: broker,
  };
  context.globalThis = context;
  vm.runInNewContext(backgroundSource, context, { filename: "background.js" });

  const pending = ports[0].onMessage.emit({ type: "tabs.list", requestId: "stale" });
  ports[0].onDisconnect.emit();
  alarmEvent.emit({ name: "reconnect" });
  resolveOperation();
  await pending;

  assert.deepEqual(ports[1].messages, []);
  ports[1].onDisconnect.emit();
  assert.deepEqual(alarms, [
    { delayInMinutes: 1_000 / 60_000 },
    { delayInMinutes: 2_000 / 60_000 },
  ]);

  alarmEvent.emit({ name: "reconnect" });
  ports[1].onDisconnect.emit();
  assert.equal(alarms.length, 2);
  alarmEvent.emit({ name: "reconnect" });
  assert.equal(connections, 3);
});


const FF = " — Mozilla Firefox";

function fakeBrowser() {
  const calls = { created: [], grouped: [], updated: [], queried: [] };
  return {
    calls,
    tabs: {
      query: async () => [
        { id: 1, windowId: 10, active: true, title: "Alpha" },
        { id: 2, windowId: 20, active: true, title: "Beta" },
      ],
      create: async ({ windowId, url, active }) => {
        calls.created.push({ windowId, url, active });
        return { id: 99, windowId };
      },
      group: async (options) => {
        calls.grouped.push(options);
        return 500;
      },
    },
    tabGroups: {
      query: async (filter) => {
        calls.queried.push(filter);
        return [];
      },
      update: async (groupId, properties) => {
        calls.updated.push({ groupId, properties });
      },
    },
  };
}

function openMessage(changes = {}) {
  return {
    type: "tabs.open",
    requestId: "r1",
    toplevelTitle: `Alpha${FF}`,
    url: "https://example.com/a",
    ...changes,
  };
}

test("creates a tab in the correlated window and answers before grouping", async () => {
  const broker = loadBroker();
  const api = fakeBrowser();
  let releaseGroupQuery;
  const groupQueryGate = new Promise((resolve) => {
    releaseGroupQuery = resolve;
  });
  api.tabGroups.query = async (filter) => {
    api.calls.queried.push(filter);
    await groupQueryGate;
    return [];
  };
  const responses = [];
  const handled = broker.handleNativeMessage(
    openMessage({ group: { title: "oracle", color: "yellow" } }),
    api,
    (response) => responses.push(response),
  );
  await handled;
  // The response must land before grouping does any work at all -- not just
  // before it finishes. tabGroups.query above is gated shut, so if the
  // implementation awaited the whole grouping chain before responding, this
  // assertion would hang or see zero responses instead of one.
  assert.equal(responses.length, 1);
  assert.deepEqual(api.calls.created, [
    { windowId: 10, url: "https://example.com/a", active: true },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
    { type: "tabs.opened", requestId: "r1", ok: true, tabId: 99 },
  ]);
  assert.equal(responses[0].groupId, undefined);
  releaseGroupQuery();
  await broker.groupsSettled();
  assert.deepEqual(JSON.parse(JSON.stringify(api.calls.updated)), [
    { groupId: 500, properties: { title: "oracle", color: "yellow" } },
  ]);
});

test("refuses a group colour outside the nine allowed values", async () => {
  const broker = loadBroker();
  const api = fakeBrowser();
  const responses = [];
  await broker.handleNativeMessage(
    openMessage({ group: { title: "oracle", color: "gray" } }),
    api,
    (r) => responses.push(r),
  );
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
    { type: "tabs.opened", requestId: "r1", ok: false, error: "invalid-request" },
  ]);
  assert.equal(api.calls.created.length, 0);
});

test("bounds group titles by code point, not UTF-16 code units", () => {
  const broker = loadBroker();
  const emojiTitle = "\u{1F600}".repeat(64);
  assert.equal(emojiTitle.length, 128);
  assert.equal([...emojiTitle].length, 64);
  assert.deepEqual(JSON.parse(JSON.stringify(broker.safeGroup({ title: emojiTitle }))), {
    title: emojiTitle,
  });
});

test("bounds the toplevel title by code point, not UTF-16 code units", async () => {
  const broker = loadBroker();
  const emojiToplevel = "\u{1F600}".repeat(600);
  assert.equal(emojiToplevel.length, 1200);
  assert.equal([...emojiToplevel].length, 600);
  const api = fakeBrowser();
  api.tabs.query = async () => [
    { id: 1, windowId: 10, active: true, title: emojiToplevel },
  ];
  const result = await broker.correlateWindow(api, `${emojiToplevel}${FF}`);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { windowId: 10 });
});

test("bounds the open url by code point, not UTF-16 code units", () => {
  const broker = loadBroker();
  const path = "\u{1F600}".repeat(2100);
  const url = `https://example.com/${path}`;
  assert.equal(url.length, 4220);
  assert.equal([...url].length, 2120);
  assert.notEqual(broker.safeOpenUrl(url), "");
});

test("reports create-failed and sends exactly one response when tabs.create throws", async () => {
  const broker = loadBroker();
  const api = fakeBrowser();
  api.tabs.create = async () => {
    throw new Error("boom");
  };
  const responses = [];
  await broker.handleNativeMessage(
    openMessage({ group: { title: "oracle" } }), api, (r) => responses.push(r),
  );
  await broker.groupsSettled();
  assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
    { type: "tabs.opened", requestId: "r1", ok: false, error: "create-failed" },
  ]);
});

test("passes the parsed href to tabs.create, not the raw string", async () => {
  const broker = loadBroker();
  const api = fakeBrowser();
  await broker.handleNativeMessage(
    openMessage({ url: "HTTPS://Example.COM/a" }), api, () => {},
  );
  assert.equal(api.calls.created[0].url, new URL("HTTPS://Example.COM/a").href);
  assert.notEqual(api.calls.created[0].url, "HTTPS://Example.COM/a");
});

test("refuses non-http schemes and malformed urls", async () => {
  const broker = loadBroker();
  for (const url of [
    "javascript:alert(1)",
    "data:text/html,x",
    "file:///etc/passwd",
    "about:config",
    "nonsense",
    "",
  ]) {
    const responses = [];
    await broker.handleNativeMessage(
      openMessage({ url }), fakeBrowser(), (r) => responses.push(r),
    );
    assert.deepEqual(JSON.parse(JSON.stringify(responses)), [
      { type: "tabs.opened", requestId: "r1", ok: false, error: "invalid-request" },
    ]);
  }
});

test("refuses urls containing control characters", async () => {
  const broker = loadBroker();
  const responses = [];
  await broker.handleNativeMessage(
    openMessage({ url: "https://example.com/\u000a" }), fakeBrowser(), (r) => responses.push(r),
  );
  assert.equal(responses[0].error, "invalid-request");
});

test("reports no-window and ambiguous-window without creating a tab", async () => {
  const broker = loadBroker();

  const none = fakeBrowser();
  let responses = [];
  await broker.handleNativeMessage(
    openMessage({ toplevelTitle: `Nothing${FF}` }), none, (r) => responses.push(r),
  );
  assert.equal(responses[0].error, "no-window");
  assert.equal(none.calls.created.length, 0);

  const duplicate = fakeBrowser();
  duplicate.tabs.query = async () => [
    { id: 1, windowId: 10, active: true, title: "Alpha" },
    { id: 3, windowId: 30, active: true, title: "Alpha" },
  ];
  responses = [];
  await broker.handleNativeMessage(openMessage(), duplicate, (r) => responses.push(r));
  assert.equal(responses[0].error, "ambiguous-window");
  assert.equal(duplicate.calls.created.length, 0);
});

test("never passes a title to tabGroups.query, so globs cannot match", async () => {
  const broker = loadBroker();
  const api = fakeBrowser();
  api.tabGroups.query = async (filter) => {
    api.calls.queried.push(filter);
    return [{ id: 7, title: "weird", windowId: 10 }];
  };
  await broker.handleNativeMessage(
    openMessage({ group: { title: "we*rd" } }), api, () => {},
  );
  await broker.groupsSettled();
  assert.deepEqual(JSON.parse(JSON.stringify(api.calls.queried)), [{ windowId: 10 }]);
  assert.deepEqual(JSON.parse(JSON.stringify(api.calls.grouped)), [
    { tabIds: 99, createProperties: { windowId: 10 } },
  ]);
});

test("reuses exactly one same-titled group and leaves several ambiguous", async () => {
  const broker = loadBroker();

  const one = fakeBrowser();
  one.tabGroups.query = async () => [
    { id: 7, title: "oracle", windowId: 10 },
    { id: 8, title: "other", windowId: 10 },
  ];
  await broker.handleNativeMessage(
    openMessage({ group: { title: "oracle" } }), one, () => {},
  );
  await broker.groupsSettled();
  assert.deepEqual(JSON.parse(JSON.stringify(one.calls.grouped)), [{ tabIds: 99, groupId: 7 }]);
  assert.deepEqual(one.calls.updated, []);

  const several = fakeBrowser();
  several.tabGroups.query = async () => [
    { id: 7, title: "oracle", windowId: 10 },
    { id: 8, title: "oracle", windowId: 10 },
  ];
  await broker.handleNativeMessage(
    openMessage({ group: { title: "oracle" } }), several, () => {},
  );
  await broker.groupsSettled();
  assert.deepEqual(several.calls.grouped, []);
});

test("a failed group step still leaves the tab created and the answer ok", async () => {
  const broker = loadBroker();
  const api = fakeBrowser();
  api.tabs.group = async () => {
    throw new Error("boom");
  };
  const responses = [];
  await broker.handleNativeMessage(
    openMessage({ group: { title: "oracle" } }), api, (r) => responses.push(r),
  );
  await broker.groupsSettled();
  assert.equal(responses[0].ok, true);
  assert.equal(responses.length, 1);
  assert.equal(api.calls.created.length, 1);
});

test("serialises concurrent opens so one group is created, not two", async () => {
  const broker = loadBroker();
  const api = fakeBrowser();
  const groups = [];
  api.tabGroups.query = async () => groups.slice();
  api.tabs.group = async (options) => {
    api.calls.grouped.push(options);
    if (options.createProperties) {
      groups.push({ id: 500, title: "oracle", windowId: 10 });
      return 500;
    }
    return options.groupId;
  };
  await Promise.all([
    broker.handleNativeMessage(openMessage({ group: { title: "oracle" } }), api, () => {}),
    broker.handleNativeMessage(openMessage({ group: { title: "oracle" } }), api, () => {}),
  ]);
  await broker.groupsSettled();
  const created = api.calls.grouped.filter((call) => call.createProperties);
  assert.equal(created.length, 1);
});
