import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const path = new URL("../custom_components/presence_bridge/frontend/panel-element.js", import.meta.url);
const source = readFileSync(path, "utf8").replace(/^import .*?;\s*/s, "");

function panel(language = "en", beforeUpgrade = null) {
  let Panel;
  const root = { querySelector: () => null, querySelectorAll: () => [] };
  const context = vm.createContext({
    HTMLElement: class {
      constructor() {
        if (beforeUpgrade) Object.defineProperty(this, "hass", { value: beforeUpgrade, writable: true, configurable: true });
      }
      attachShadow() { this.shadowRoot = root; }
    },
    customElements: { get: () => null, define: (name, value) => { Panel = value; } },
    isAppleMobile: () => false,
    presencePairLinks: () => "",
    bindPresencePairLinks: () => {},
    window: {},
  });
  vm.runInContext(source, context);
  const instance = new Panel();
  if (!beforeUpgrade) instance._hass = { language };
  return instance;
}

const windows = { observer_id: "windows", name: "Receiver", online: true, capabilities: ["scanner", "app_pairing"] };
const proxy = { observer_id: "proxy", name: "Proxy", online: true, capabilities: ["scanner", "ha_bluetooth"] };
const data = (observers = [], people = [{ entity_id: "person.test", name: "Test" }]) => ({ observers, people, identities: [], areas: [], pairing: {} });

test("HA js_url entry point parses as a classic script and loads the module", () => {
  const loader = readFileSync(new URL("../custom_components/presence_bridge/frontend/panel.js", import.meta.url), "utf8");
  assert.doesNotThrow(() => new vm.Script(loader));
  assert.match(loader, /import\("\.\/panel-element\.js\?v=\d+"\)/);
});

test("HA assigned before module upgrade is replayed and fetches live state", async () => {
  const calls = [];
  const info = data([windows]);
  const hass = { language: "it", callWS: async request => { calls.push(request.type); return info; } };
  const p = panel("it", hass);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(Object.hasOwn(p, "hass"), false);
  assert.deepEqual(calls, ["presence_bridge/info"]);
  assert.equal(p._data, info);
  assert.match(p.shadowRoot.innerHTML, /Ricevitore per abbinamento online/);
});

test("loading is distinct from missing hardware", () => {
  assert.equal(panel().setupState(null), "loading");
  assert.equal(panel().setupState(data()), "receiver_missing");
});

test("local Linux prerequisite failures are visible and escaped", () => {
  const p = panel();
  const info = { ...data(), local_receiver: { enabled: true, ready: false, message: "BlueZ <storage> missing" } };
  assert.match(p.setupView(info), /BlueZ &lt;storage&gt; missing/);
  assert.match(p.setupView(info), /No MQTT required/);
  assert.equal(p.setupState(info), "receiver_missing");
});

test("native HA proxy alone never enables initial enrollment", () => {
  const p = panel();
  p._data = data([proxy]);
  p.render();
  assert.equal(p.setupState(p._data), "receiver_missing");
  assert.match(p.shadowRoot.innerHTML, /Online passive-only receivers: 1/);
  assert.match(p.shadowRoot.innerHTML, /data-action="start" disabled/);
  assert.match(p.shadowRoot.innerHTML, /id="observer" disabled/);
});

test("offline pairing receiver stays unavailable despite a working proxy", () => {
  const p = panel();
  p._data = data([{ ...windows, online: false }, proxy]);
  p.render();
  assert.equal(p.setupState(p._data), "receiver_offline");
  assert.match(p.shadowRoot.innerHTML, /data-action="start" disabled/);
  assert.doesNotMatch(p.shadowRoot.innerHTML, /<option value="proxy"/);
});

test("missing people have a concrete next action and cannot start pairing", () => {
  const p = panel();
  p._data = data([windows], []);
  p.render();
  assert.equal(p.setupState(p._data), "person_missing");
  assert.match(p.shadowRoot.innerHTML, /href="\/config\/person"/);
  assert.match(p.shadowRoot.innerHTML, /id="person" disabled/);
  assert.match(p.shadowRoot.innerHTML, /data-action="start" disabled/);
});

test("online receiver enables invitation but does not claim hardware certification", () => {
  const p = panel();
  p._data = data([windows, proxy]);
  p.render();
  assert.equal(p.setupState(p._data), "receiver_online");
  assert.doesNotMatch(p.shadowRoot.innerHTML, /data-action="start" disabled/);
  assert.match(p.setupView(p._data), /online alone does not certify the adapter/);
  assert.match(p.setupView(p._data), /configured user signed in/);
});

test("capabilities missing or manual pairing are not app enrollment", () => {
  for (const capabilities of [undefined, [], ["manual_pairing"], ["scanner"]]) {
    assert.equal(panel().setupState(data([{ ...windows, capabilities }])), "receiver_missing");
  }
});

test("requirements stay expanded across status polling", () => {
  const p = panel();
  p._data = data([windows]);
  p.shadowRoot.querySelector = selector => selector === "[data-setup-details]" ? { open: true } : null;
  p.render();
  assert.match(p.shadowRoot.innerHTML, /data-setup-details open/);
  p.shadowRoot.querySelector = selector => selector === "[data-setup-details]" ? { open: false } : null;
  p.render();
  assert.doesNotMatch(p.shadowRoot.innerHTML, /data-setup-details open/);
});

test("Italian onboarding and external links are localized and invitation-free", () => {
  const html = panel("it-IT").setupView(data([proxy]));
  assert.match(html, /Ricevitore per abbinamento mancante/);
  assert.match(html, /tutorial-it.md/);
  assert.doesNotMatch(html, /presencepair:|secret=|sid=/);
  const links = [...html.matchAll(/<a[^>]+>/g)].map(item => item[0]);
  assert.equal(links.length, 2);
  for (const link of links) assert.match(link, /rel="noopener noreferrer"/);
});
