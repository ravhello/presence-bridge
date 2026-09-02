class PresenceBridgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._loading = false;
    this._error = "";
    this._poll = null;
  }

  set hass(value) {
    this._hass = value;
    if (!this._data && !this._loading) this.load();
  }

  connectedCallback() {
    this.render();
    this._poll = window.setInterval(() => this.load(true), 2000);
  }

  disconnectedCallback() {
    if (this._poll) window.clearInterval(this._poll);
  }

  async load(quiet = false) {
    if (!this._hass || this._loading) return;
    this._loading = true;
    if (!quiet) this.render();
    try {
      this._data = await this._hass.callWS({ type: "presence_bridge/info" });
      this._error = "";
    } catch (error) {
      this._error = error?.message || String(error);
    } finally {
      this._loading = false;
      this.render();
    }
  }

  text(en, it) {
    return String(this._hass?.language || "en").toLowerCase().startsWith("it") ? it : en;
  }

  escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  render() {
    if (!this.shadowRoot) return;
    const data = this._data || { people: [], observers: [], identities: [], pairing: {}, areas: [] };
    const pairing = data.pairing || {};
    const availableObservers = data.observers.filter((item) => item.online && item.capabilities?.includes("app_pairing"));
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-height:100%; color:var(--primary-text-color); background:var(--primary-background-color); }
        * { box-sizing:border-box; }
        main { max-width:1040px; margin:0 auto; padding:24px clamp(16px,4vw,40px) 48px; }
        header { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:24px; }
        h1 { margin:0; font-size:28px; font-weight:600; letter-spacing:0; }
        h2 { margin:0 0 14px; font-size:18px; font-weight:600; letter-spacing:0; }
        section { border-top:1px solid var(--divider-color); padding:22px 0; }
        .status { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:10px; }
        .status-item { min-height:92px; padding:12px 14px; border:1px solid var(--divider-color); border-radius:8px; background:var(--card-background-color); }
        .status-item b,.status-item span { display:block; overflow-wrap:anywhere; }
        .status-item span { margin-top:5px; color:var(--secondary-text-color); font-size:13px; }
        .status-item select { min-height:36px; margin-top:10px; }
        .form { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto; gap:10px; align-items:end; }
        label { display:grid; gap:6px; color:var(--secondary-text-color); font-size:12px; }
        select,button,a.action { min-height:44px; border-radius:6px; font:inherit; letter-spacing:0; }
        select { width:100%; padding:0 12px; border:1px solid var(--divider-color); color:var(--primary-text-color); background:var(--card-background-color); }
        button,a.action { display:inline-flex; align-items:center; justify-content:center; gap:8px; border:0; padding:0 16px; cursor:pointer; text-decoration:none; }
        button.primary,a.primary { color:var(--text-primary-color,#fff); background:var(--primary-color); font-weight:600; }
        button.secondary { color:var(--primary-text-color); background:var(--secondary-background-color); }
        button.icon { width:44px; padding:0; color:var(--primary-text-color); background:transparent; }
        button:disabled { opacity:.45; cursor:not-allowed; }
        .pairing { display:grid; grid-template-columns:minmax(220px,320px) 1fr; gap:24px; align-items:center; }
        .qr { width:100%; aspect-ratio:1; padding:10px; border-radius:8px; background:#fff; }
        .qr img { width:100%; height:100%; display:block; }
        .pair-info { display:grid; gap:12px; align-content:center; }
        .pair-info strong { font-size:20px; overflow-wrap:anywhere; }
        .muted { color:var(--secondary-text-color); }
        .actions { display:flex; flex-wrap:wrap; gap:8px; }
        table { width:100%; border-collapse:collapse; }
        th,td { padding:11px 8px; text-align:left; border-bottom:1px solid var(--divider-color); }
        th:last-child,td:last-child { width:52px; text-align:right; }
        th { color:var(--secondary-text-color); font-size:12px; font-weight:500; }
        .dot { width:9px; height:9px; display:inline-block; border-radius:50%; margin-right:7px; background:var(--disabled-text-color); }
        .dot.on { background:var(--success-color,#2e7d32); }
        .error { padding:12px; border-left:4px solid var(--error-color); background:var(--secondary-background-color); }
        @media (max-width:720px) { main{padding-top:16px}.form{grid-template-columns:1fr}.pairing{grid-template-columns:1fr}.qr{max-width:320px;margin:auto}table{display:block;overflow:auto} }
      </style>
      <main>
        <header>
          <h1>Presence Bridge</h1>
          <button class="icon" data-action="refresh" title="${this.text("Refresh", "Aggiorna")}"><ha-icon icon="mdi:refresh"></ha-icon></button>
        </header>
        ${this._error ? `<div class="error">${this.escape(this._error)}</div>` : ""}
        <section>
          <h2>${this.text("Observers", "Ricevitori")}</h2>
          <div class="status">
            ${data.observers.length ? data.observers.map((item) => `<div class="status-item"><b><span class="dot ${item.online ? "on" : ""}"></span>${this.escape(item.name)}</b><span>${item.observation_count || 0} BLE · ${this.escape(item.version || "—")}</span><select data-action="area" data-observer="${this.escape(item.observer_id)}" aria-label="${this.text("Observer area", "Area del ricevitore")}"><option value="">${this.text("Area not assigned", "Area non assegnata")}</option>${data.areas.map((area) => `<option value="${this.escape(area.area_id)}" ${area.area_id === item.area_id ? "selected" : ""}>${this.escape(area.name)}</option>`).join("")}</select></div>`).join("") : `<span class="muted">${this.text("No bridge detected. Install the Windows observer first.", "Nessun bridge rilevato. Installa prima il ricevitore Windows.")}</span>`}
          </div>
        </section>
        <section>
          <h2>${this.text("Pair an iPhone", "Associa un iPhone")}</h2>
          ${pairing.active || ["complete","error","timeout"].includes(pairing.state) ? this.pairingView(pairing) : `
            <div class="form">
              <label>${this.text("Person", "Persona")}<select id="person">${data.people.map((item) => `<option value="${this.escape(item.entity_id)}">${this.escape(item.name)}</option>`).join("")}</select></label>
              <label>${this.text("Bridge", "Bridge")}<select id="observer">${availableObservers.map((item) => `<option value="${this.escape(item.observer_id)}">${this.escape(item.name)}</option>`).join("")}</select></label>
              <button class="primary" data-action="start" ${!data.people.length || !availableObservers.length ? "disabled" : ""}><ha-icon icon="mdi:qrcode-scan"></ha-icon>${this.text("Create code", "Crea codice")}</button>
            </div>`}
        </section>
        <section>
          <h2>${this.text("Paired identities", "Identità associate")}</h2>
          ${data.identities.length ? `<table><thead><tr><th>${this.text("Person", "Persona")}</th><th>${this.text("Status", "Stato")}</th><th>${this.text("Room", "Stanza")}</th><th>RSSI</th><th></th></tr></thead><tbody>${data.identities.map((item) => `<tr><td>${this.escape(item.label)}</td><td><span class="dot ${item.is_home ? "on" : ""}"></span>${item.is_home ? this.text("Home", "In casa") : this.text("Away", "Fuori")}</td><td>${this.escape(item.area_name || item.observer_name || "—")}</td><td>${item.rssi ?? "—"}</td><td><button class="icon" data-action="remove" data-identity="${this.escape(item.identity_id)}" title="${this.text("Remove identity", "Rimuovi identità")}"><ha-icon icon="mdi:delete-outline"></ha-icon></button></td></tr>`).join("")}</tbody></table>` : `<span class="muted">${this.text("No paired iPhone", "Nessun iPhone associato")}</span>`}
        </section>
      </main>`;
    this.bind();
  }

  pairingView(pairing) {
    const terminal = ["complete", "error", "timeout"].includes(pairing.state);
    return `<div class="pairing">
      ${pairing.qr_data_uri && !terminal ? `<div class="qr"><img alt="Pairing QR" src="${pairing.qr_data_uri}"></div>` : `<ha-icon icon="${pairing.state === "complete" ? "mdi:check-circle" : "mdi:bluetooth-connect"}" style="--mdc-icon-size:96px;color:var(--primary-color)"></ha-icon>`}
      <div class="pair-info"><strong>${this.escape(pairing.person_name || "")}</strong><span>${this.escape(pairing.message || "")}</span><span class="muted">${this.escape(pairing.observer_name || "")}</span><div class="actions">${pairing.pairing_uri && !terminal ? `<a class="action primary" href="${this.escape(pairing.pairing_uri)}"><ha-icon icon="mdi:apple"></ha-icon>${this.text("Open Presence Pair", "Apri Presence Pair")}</a>` : ""}<button class="secondary" data-action="cancel"><ha-icon icon="mdi:${terminal ? "close" : "cancel"}"></ha-icon>${terminal ? this.text("Close", "Chiudi") : this.text("Cancel", "Annulla")}</button></div></div>
    </div>`;
  }

  bind() {
    this.shadowRoot.querySelector('[data-action="refresh"]')?.addEventListener("click", () => this.load());
    this.shadowRoot.querySelector('[data-action="start"]')?.addEventListener("click", () => this.startPairing());
    this.shadowRoot.querySelector('[data-action="cancel"]')?.addEventListener("click", () => this.cancelPairing());
    this.shadowRoot.querySelectorAll('[data-action="area"]').forEach((element) => element.addEventListener("change", (event) => this.setObserverArea(event.currentTarget)));
    this.shadowRoot.querySelectorAll('[data-action="remove"]').forEach((element) => element.addEventListener("click", (event) => this.removeIdentity(event.currentTarget)));
  }

  async startPairing() {
    const person = this.shadowRoot.querySelector("#person")?.value;
    const observer = this.shadowRoot.querySelector("#observer")?.value;
    if (!person || !observer || this._loading) return;
    this._loading = true;
    try {
      await this._hass.callWS({ type: "presence_bridge/start_pairing", person, observer_id: observer, timeout_seconds: 180 });
      await this.load(true);
    } catch (error) {
      this._error = error?.message || String(error);
    } finally {
      this._loading = false;
      this.render();
    }
  }

  async cancelPairing() {
    try {
      await this._hass.callWS({ type: "presence_bridge/cancel_pairing" });
      await this.load(true);
    } catch (error) {
      this._error = error?.message || String(error);
      this.render();
    }
  }

  async setObserverArea(element) {
    try {
      await this._hass.callWS({
        type: "presence_bridge/set_observer_area",
        observer_id: element.dataset.observer,
        area_id: element.value || undefined,
      });
      await this.load(true);
    } catch (error) {
      this._error = error?.message || String(error);
      this.render();
    }
  }

  async removeIdentity(element) {
    if (!window.confirm(this.text("Remove this Bluetooth identity and its Home Assistant entities?", "Rimuovere questa identità Bluetooth e le relative entità di Home Assistant?"))) return;
    try {
      await this._hass.callWS({
        type: "presence_bridge/remove_identity",
        identity_id: element.dataset.identity,
      });
      await this.load(true);
    } catch (error) {
      this._error = error?.message || String(error);
      this.render();
    }
  }
}

if (!customElements.get("presence-bridge-panel")) customElements.define("presence-bridge-panel", PresenceBridgePanel);
