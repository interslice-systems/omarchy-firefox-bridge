import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const manifest = JSON.parse(
  await readFile(new URL("../../extension/manifest.json", import.meta.url), "utf8"),
);

test("uses the machine-neutral bridge identity", () => {
  assert.equal(manifest.name, "Omarchy Bridge");
  assert.equal(
    manifest.browser_specific_settings.gecko.id,
    "omarchy-bridge@interslice.systems",
  );
  assert.equal(manifest.browser_specific_settings.gecko.strict_min_version, "142.0");
  assert.equal(manifest.incognito, "not_allowed");
});

test("declares exactly the approved capabilities and data use", () => {
  assert.deepEqual(manifest.permissions, [
    "theme",
    "nativeMessaging",
    "alarms",
    "tabs",
    "tabGroups",
  ]);
  assert.deepEqual(
    manifest.browser_specific_settings.gecko.data_collection_permissions.required,
    ["websiteContent", "browsingActivity"],
  );
  assert.equal(manifest.host_permissions, undefined);
  assert.equal(manifest.content_scripts, undefined);
});

test("locks extension pages to packaged scripts and no network sinks", () => {
  const csp = manifest.content_security_policy.extension_pages;
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /script-src 'self'/);
  assert.match(csp, /connect-src 'none'/);
  assert.match(csp, /object-src 'none'/);
  assert.match(csp, /frame-src 'none'/);
  assert.match(csp, /form-action 'none'/);
  assert.deepEqual(manifest.background.scripts, ["broker.js", "background.js"]);
});

test("declares exactly the five expected permissions", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("../../extension/manifest.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(manifest.permissions.slice().sort(), [
    "alarms", "nativeMessaging", "tabGroups", "tabs", "theme",
  ]);
  assert.equal(manifest.browser_specific_settings.gecko.strict_min_version, "142.0");
});
