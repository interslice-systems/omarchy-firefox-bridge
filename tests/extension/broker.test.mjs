import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(
  new URL("../../extension/broker.js", import.meta.url),
  "utf8",
).catch(() => "");

function loadBroker() {
  const context = { atob, URL };
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "broker.js" });
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
  const oversized = `data:image/png;base64,${Buffer.alloc(65537).toString("base64")}`;
  assert.equal(broker.safeFavicon(oversized), "");
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
