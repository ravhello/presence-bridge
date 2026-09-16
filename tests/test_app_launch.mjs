import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import {
  PRESENCE_PAIR_STORE_URL, isAppleMobile, presencePairLinks,
  launchPresencePair, bindPresencePairLinks,
} from "../custom_components/presence_bridge/frontend/presence-pair-launch.js";

const iphone = { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", maxTouchPoints: 5 };
const desktop = { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", maxTouchPoints: 10 };
const uri = "presencepair://pair?v=2&sid=test-only&secret=test-only&oid=receiver&exp=9999999999";

function browser(nav = iphone) {
  const doc = { hidden: false };
  const win = {};
  const calls = [];
  win.navigator = nav;
  win.open = (...args) => { calls.push(args); return null; };
  Object.defineProperty(win, "location", {
    get() { throw new Error("Must not navigate Home Assistant's webview"); },
    set() { throw new Error("Must not replace Home Assistant's webview"); },
  });
  win.setTimeout = () => { throw new Error("Must not schedule a store redirect"); };
  return { win, doc, calls, root: {} };
}

test("only Apple mobile devices get an app link, including desktop-mode iPad", () => {
  for (const nav of [iphone, { userAgent: "iPad" }, { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X)", maxTouchPoints: 5 }]) {
    assert.equal(isAppleMobile(nav), true);
    assert.match(presencePairLinks(uri, {}, nav), /data-presence-pair-link/);
  }
  for (const nav of [desktop, { userAgent: "Android", maxTouchPoints: 5 }, { userAgent: "Macintosh", maxTouchPoints: 0 }]) {
    const html = presencePairLinks(uri, {}, nav);
    assert.equal(isAppleMobile(nav), false);
    assert.doesNotMatch(html, /presencepair:|data-presence-pair-link/);
    assert.match(html, /apps\.apple\.com\/app\/id6808059201/);
    assert.match(html, /noopener noreferrer/);
  }
});

test("pair and monitor links preserve the complete payload, escaped only for HTML", () => {
  for (const link of [uri, "presencepair://monitor?code=test%2Fonly&name=A%20B"]) {
    const html = presencePairLinks(link, {}, iphone);
    assert.ok(html.includes(link.replaceAll("&", "&amp;")));
    assert.match(html.split("</a>")[0], /target="_blank" rel="noopener noreferrer"/);
    const store = html.split('<a class="action store-link"')[1];
    assert.doesNotMatch(store, /secret=|sid=|code=/);
  }
});

test("unsafe or unrelated schemes cannot be launched", () => {
  for (const bad of ["javascript:alert(1)", "https://example.com", "presencepair://other", "presencepair://bad@pair", "presencepair://pair/unexpected", "not a url"]) {
    const b = browser();
    assert.doesNotMatch(presencePairLinks(bad, {}, iphone), /data-presence-pair-link/);
    assert.equal(launchPresencePair(bad, b.win, b.doc), false);
    assert.deepEqual(b.calls, []);
  }
});

test("same tap passes the exact invitation to Companion's external-window path", () => {
  const b = browser();
  assert.equal(launchPresencePair(uri, b.win, b.doc), true);
  assert.deepEqual(b.calls, [[uri, "_blank", "noopener,noreferrer"]]);
});

test("null WindowProxy is not installation failure and must not send an installed app to the store", () => {
  const b = browser();
  launchPresencePair(uri, b.win, b.doc);
  assert.equal(b.calls.length, 1);
  assert.notEqual(b.calls[0][0], PRESENCE_PAIR_STORE_URL);
});

test("desktop and background pages cannot start native launch", () => {
  const b = browser(desktop);
  assert.equal(launchPresencePair(uri, b.win, b.doc), false);
  b.win.navigator = iphone;
  b.doc.hidden = true;
  assert.equal(launchPresencePair(uri, b.win, b.doc), false);
  assert.deepEqual(b.calls, []);
});

test("a launch error cannot navigate HA or infer that Presence Pair is absent", () => {
  const b = browser();
  b.win.open = () => { throw new Error("WebKit blocked opening"); };
  assert.throws(() => launchPresencePair(uri, b.win, b.doc), /WebKit blocked/);
  assert.deepEqual(b.calls, []);
});

test("no launch reads or replaces location or creates asynchronous navigation", () => {
  const b = browser();
  assert.doesNotThrow(() => launchPresencePair(uri, b.win, b.doc));
  assert.doesNotThrow(() => launchPresencePair("presencepair://monitor?code=test-only", b.win, b.doc));
});

test("click binding preserves the user gesture and uses the latest invitation exactly once", (t) => {
  const b = browser();
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "window", { value: b.win, configurable: true });
  Object.defineProperty(globalThis, "document", { value: b.doc, configurable: true });
  t.after(() => {
    if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow); else delete globalThis.window;
    if (previousDocument) Object.defineProperty(globalThis, "document", previousDocument); else delete globalThis.document;
  });
  let current = uri;
  const link = { getAttribute: () => current };
  b.root.querySelectorAll = () => [link];
  bindPresencePairLinks(b.root);
  bindPresencePairLinks(b.root);
  current = "presencepair://pair?sid=new-test-code&secret=new-test-secret";
  const effects = [];
  link.onclick({ button: 0, preventDefault: () => effects.push("prevent"), stopPropagation: () => effects.push("stop") });
  assert.deepEqual(b.calls, [[current, "_blank", "noopener,noreferrer"]]);
  assert.deepEqual(effects, ["prevent", "stop"]);
  link.onclick({ button: 0, ctrlKey: true });
  assert.equal(b.calls.length, 1);
});

function component(path, name, nav) {
  const registry = new Map();
  const context = vm.createContext({
    HTMLElement: class { attachShadow() { this.shadowRoot = {}; } },
    customElements: { get: key => registry.get(key), define: (key, value) => registry.set(key, value) },
    window: {}, console, navigator: nav, URL,
    isAppleMobile: () => isAppleMobile(nav),
    presencePairLinks: (value, options) => presencePairLinks(value, options, nav),
    bindPresencePairLinks,
  });
  vm.runInContext(readFileSync(path, "utf8").replace(/^import .*?;\s*/s, ""), context);
  return new (registry.get(name))();
}

const panelPath = new URL("../custom_components/presence_bridge/frontend/panel-element.js", import.meta.url);
const cardPath = process.env.PRESENCE_PEOPLE_CARD;
const pairing = {
  state: "waiting", active: true, pairing_uri: uri, qr_data_uri: "test-qr",
  person_entity_id: "person.test", observer_id: "test", person_name: "Test",
  expires_at: Date.now() / 1000 + 600,
};

for (const [platform, nav] of [["phone", iphone], ["desktop", desktop]]) {
  test(`public panel ${platform}: device-specific links and unavailable invitations`, () => {
    const panel = component(panelPath, "presence-bridge-panel", nav);
    panel._hass = { language: "it" };
    assert.equal(panel.pairingView(pairing).includes("data-presence-pair-link"), platform === "phone");
    assert.match(panel.pairingView(pairing), /Presence Pair su App Store/);
    for (const ended of [{ state: "complete" }, { state: "error" }, { state: "timeout" }, { state: "cancelled" }, { invitation_consumed: true }, { expires_at: 1 }]) {
      assert.doesNotMatch(panel.pairingView({ ...pairing, ...ended }), /data-presence-pair-link|presencepair:|test-qr/);
    }
  });
  test(`HA people card ${platform}: pairing and monitor use the shared launcher`, { skip: !cardPath || !existsSync(cardPath) }, () => {
    const card = component(cardPath, "smart-presence-people-devices-card", nav);
    card._data = { people: [{ id: "test", entity_id: "person.test", name: "Test" }] };
    card._setupPersonId = "test";
    card._pairingPlatform = "ios";
    card._bridgeData = { pairing };
    assert.equal(card.renderPairingDialog().includes("data-presence-pair-link"), platform === "phone");
    assert.match(card.renderPairingDialog(), /Presence Pair su App Store/);
    for (const ended of [{ state: "complete" }, { state: "error" }, { state: "timeout" }, { state: "cancelled" }, { invitation_consumed: true }, { expires_at: 1 }]) {
      card._bridgeData.pairing = { ...pairing, ...ended };
      assert.doesNotMatch(card.renderPairingDialog(), /data-presence-pair-link|presencepair:|test-qr/);
    }
    card._bridgeData.pairing = pairing;
    card._monitorDialog = { uri: "presencepair://monitor?code=test-only", qr_data_uri: "test-qr" };
    assert.equal(card.renderMonitorDialog().includes("data-presence-pair-link"), platform === "phone");
  });
}
