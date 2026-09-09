class PresenceBridgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._loading = false;
    this._error = "";
    this._poll = null;
    this._clock = null;
    this._selectedPerson = "";
    this._selectedObserver = "";
  }

  set hass(value) {
    this._hass = value;
    if (!this._data && !this._loading) this.load();
  }

  connectedCallback() {
    this.render();
    this._poll = window.setInterval(() => this.load(true), 2000);
    this._clock = window.setInterval(() => this.tickCountdowns(), 1000);
  }

  disconnectedCallback() {
    if (this._poll) window.clearInterval(this._poll);
    if (this._clock) window.clearInterval(this._clock);
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
    this._selectedPerson = this.shadowRoot.querySelector("#person")?.value || this._selectedPerson;
    this._selectedObserver = this.shadowRoot.querySelector("#observer")?.value || this._selectedObserver;
    const data = this._data || { people: [], observers: [], identities: [], pairing: {}, areas: [] };
    const pairing = data.pairing || {};
    const availableObservers = data.observers.filter((item) => item.online && item.capabilities?.includes("app_pairing"));
    if (!data.people.some((item) => item.entity_id === this._selectedPerson)) this._selectedPerson = data.people[0]?.entity_id || "";
    if (!availableObservers.some((item) => item.observer_id === this._selectedObserver)) this._selectedObserver = availableObservers[0]?.observer_id || "";
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
        .guidance { display:grid; gap:6px; padding:12px 14px; border-left:4px solid var(--primary-color); background:var(--secondary-background-color); }
        .guidance.warning { border-left-color:var(--warning-color,#f0a000); }
        .guidance.error { border-left-color:var(--error-color); }
        .guidance b { font-size:14px; }
        .lease { display:flex; align-items:flex-start; gap:10px; padding:10px 12px; border-radius:6px; background:var(--secondary-background-color); }
        .lease ha-icon { flex:0 0 auto; margin-top:1px; color:var(--primary-color); }
        .lease span { display:block; }
        .lease .countdown { margin-top:3px; color:var(--secondary-text-color); font-size:13px; font-variant-numeric:tabular-nums; }
        .diagnostic { font:12px ui-monospace,SFMono-Regular,Consolas,monospace; color:var(--secondary-text-color); }
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
              <label>${this.text("Person", "Persona")}<select id="person">${data.people.map((item) => `<option value="${this.escape(item.entity_id)}" ${item.entity_id === this._selectedPerson ? "selected" : ""}>${this.escape(item.name)}</option>`).join("")}</select></label>
              <label>${this.text("Bridge", "Bridge")}<select id="observer">${availableObservers.map((item) => `<option value="${this.escape(item.observer_id)}" ${item.observer_id === this._selectedObserver ? "selected" : ""}>${this.escape(item.name)}</option>`).join("")}</select></label>
              <button class="primary" data-action="start" ${!data.people.length || !availableObservers.length ? "disabled" : ""}><ha-icon icon="mdi:qrcode-scan"></ha-icon>${this.text("Create code", "Crea codice")}</button>
            </div>`}
        </section>
        <section>
          <h2>${this.text("Paired identities", "Identità associate")}</h2>
          ${data.identities.length ? `<table><thead><tr><th>${this.text("Person", "Persona")}</th><th>${this.text("Status", "Stato")}</th><th>${this.text("Room", "Stanza")}</th><th>RSSI</th><th></th></tr></thead><tbody>${data.identities.map((item) => `<tr><td>${this.escape(item.label)}</td><td><span class="dot ${item.is_home ? "on" : ""}"></span>${item.is_home ? this.text("Home", "In casa") : this.text("Away", "Fuori")}</td><td>${this.escape(item.area_name || item.observer_name || "—")}</td><td>${item.rssi ?? "—"}</td><td><button class="icon" data-action="remove" data-identity="${this.escape(item.identity_id)}" title="${this.text("Remove identity", "Rimuovi identità")}"><ha-icon icon="mdi:delete-outline"></ha-icon></button></td></tr>`).join("")}</tbody></table>` : `<span class="muted">${this.text("No paired iPhone", "Nessun iPhone associato")}</span>`}
        </section>
      </main>`;
    this.bind();
    this.tickCountdowns();
  }

  pairingView(pairing) {
    const terminal = ["complete", "error", "timeout"].includes(pairing.state);
    const guidance = this.pairingGuidance(pairing);
    const canRenew = pairing.person_entity_id && pairing.observer_id && pairing.state !== "complete";
    const invitationConsumed = Boolean(pairing.invitation_consumed);
    const deadline = this.pairingDeadline(pairing);
    return `<div class="pairing">
      ${pairing.qr_data_uri && !terminal && !invitationConsumed ? `<div class="qr"><img alt="Pairing QR" src="${pairing.qr_data_uri}"></div>` : `<ha-icon icon="${pairing.state === "complete" ? "mdi:check-circle" : "mdi:bluetooth-connect"}" style="--mdc-icon-size:96px;color:var(--primary-color)"></ha-icon>`}
      <div class="pair-info"><strong>${this.escape(pairing.person_name || "")}</strong><span>${this.escape(pairing.message || "")}</span><span class="muted">${this.escape(pairing.observer_name || "")}</span>${deadline}${guidance ? `<div class="guidance ${guidance.tone}"><b>${this.escape(guidance.title)}</b><span>${this.escape(guidance.body)}</span>${pairing.advertisement_status ? `<span class="diagnostic">Dell BLE: ${this.escape(pairing.advertisement_status)}${pairing.advertisement_error && pairing.advertisement_error !== "success" && pairing.advertisement_error !== "none" ? ` · ${this.escape(pairing.advertisement_error)}` : ""}</span>` : ""}</div>` : ""}<div class="actions">${pairing.pairing_uri && !terminal && !invitationConsumed ? `<a class="action primary" href="${this.escape(pairing.pairing_uri)}"><ha-icon icon="mdi:apple"></ha-icon>${this.text("Open Presence Pair", "Apri Presence Pair")}</a>` : ""}${canRenew ? `<button class="secondary" data-action="restart" data-person="${this.escape(pairing.person_entity_id)}" data-observer="${this.escape(pairing.observer_id)}"><ha-icon icon="mdi:qrcode-plus"></ha-icon>${this.text("New code", "Nuovo codice")}</button>` : ""}<button class="secondary" data-action="cancel"><ha-icon icon="mdi:${terminal ? "close" : "cancel"}"></ha-icon>${terminal ? this.text("Close", "Chiudi") : this.text("Cancel", "Annulla")}</button></div></div>
    </div>`;
  }

  pairingDeadline(pairing) {
    if (["complete", "error", "timeout"].includes(pairing.state)) return "";
    const consumed = Boolean(pairing.invitation_consumed);
    const handoff = Boolean(pairing.handoff_started) && !consumed;
    const expiresAt = Number(
      pairing.completion_expires_at || pairing.attempt_expires_at || pairing.expires_at || 0,
    );
    const seconds = Math.max(0, Math.ceil(expiresAt - Date.now() / 1000));
    const remaining = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    if (consumed) {
      return `<div class="lease"><ha-icon icon="mdi:shield-check"></ha-icon><div><span><b>${this.text("Code accepted", "Codice acquisito")}</b> · ${this.text("The QR expiry can no longer interrupt this attempt.", "La scadenza del QR non può più interrompere questo tentativo.")}</span><span class="countdown" data-deadline="${expiresAt}" data-label="${this.escape(this.text("Maximum time to complete", "Tempo massimo per completare"))}">${this.text("Maximum time to complete", "Tempo massimo per completare")}: ${remaining}</span></div></div>`;
    }
    if (handoff) {
      return `<div class="lease"><ha-icon icon="mdi:cellphone-link"></ha-icon><div><span><b>${this.text("iPhone detected", "iPhone rilevato")}</b> · ${this.text("The receiver is verifying this exact QR session.", "Il ricevitore sta verificando questa precisa sessione QR.")}</span><span class="countdown" data-deadline="${expiresAt}" data-label="${this.escape(this.text("Time to verify", "Tempo per la verifica"))}">${this.text("Time to verify", "Tempo per la verifica")}: ${remaining}</span></div></div>`;
    }
    return `<div class="lease"><ha-icon icon="mdi:timer-outline"></ha-icon><div><span>${this.text("This code only limits when pairing may start. Once the iPhone is recognized, pairing continues in a separate completion window.", "Questo codice limita solo l'avvio. Dopo il riconoscimento dell'iPhone, il collegamento continua in una finestra separata.")}</span><span class="countdown" data-deadline="${expiresAt}" data-label="${this.escape(this.text("Valid to start for", "Valido per iniziare ancora"))}">${this.text("Valid to start for", "Valido per iniziare ancora")}: ${remaining}</span></div></div>`;
  }

  tickCountdowns() {
    this.shadowRoot?.querySelectorAll("[data-deadline]").forEach((element) => {
      const seconds = Math.max(0, Math.ceil(Number(element.dataset.deadline || 0) - Date.now() / 1000));
      const remaining = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
      element.textContent = `${element.dataset.label || ""}: ${remaining}`;
    });
  }

  pairingGuidance(pairing) {
    if (pairing.state === "complete") {
      return {
        tone: "",
        title: this.text("Pairing complete", "Associazione completata"),
        body: this.text("The identity is now available to Home Assistant.", "L'identità è ora disponibile in Home Assistant."),
      };
    }
    if (pairing.state === "error") {
      return {
        tone: "error",
        title: this.text("Pairing stopped with an error", "Associazione interrotta da un errore"),
        body: pairing.message || this.text("Create a new code and try again.", "Crea un nuovo codice e riprova."),
      };
    }
    if (pairing.detail_code === "legacy_receiver_advertising") {
      return {
        tone: "",
        title: this.text("Dell receiver visible", "Ricevitore Dell visibile"),
        body: this.text("Presence Pair is searching for this Dell automatically. Keep the scanned-code screen open; no Windows confirmation is required.", "Presence Pair sta cercando automaticamente questo Dell. Tieni aperta la schermata del codice scansionato; su Windows non serve alcuna conferma."),
      };
    }
    if (pairing.detail_code === "receiver_proximity_check") {
      return {
        tone: "",
        title: this.text("Checking the distance on iPhone", "Controllo della distanza su iPhone"),
        body: this.text("After scanning, Presence Pair shows the live Dell signal. Secure pairing stays stopped until the iPhone is close enough, then starts automatically.", "Dopo la scansione, Presence Pair mostra il segnale live del Dell. L'associazione protetta resta ferma finché l'iPhone non è abbastanza vicino, poi parte automaticamente."),
      };
    }
    if (pairing.detail_code === "receiver_proximity_confirmed") {
      return {
        tone: "",
        title: this.text("Distance confirmed", "Distanza confermata"),
        body: this.text("The iPhone verified a stable receiver signal. The encrypted pairing is now starting automatically.", "L'iPhone ha verificato un segnale stabile del ricevitore. L'associazione cifrata sta partendo automaticamente."),
      };
    }
    if (pairing.detail_code === "windows_adapter_recovering") {
      return {
        tone: "warning",
        title: this.text("Bluetooth adapter recovery", "Ripristino adattatore Bluetooth"),
        body: this.text("Windows is reopening the adapter without restarting the computer. Keep Presence Pair open while it retries.", "Windows sta riaprendo l'adattatore senza riavviare il computer. Tieni Presence Pair aperta durante il nuovo tentativo."),
      };
    }
    if (pairing.detail_code === "iphone_signal_too_weak") {
      const rssi = pairing.rssi ? ` (${pairing.rssi} dBm)` : "";
      return {
        tone: "warning",
        title: this.text("iPhone signal too weak", "Segnale iPhone troppo debole"),
        body: this.text(`The Dell found the iPhone${rssi}, but the radio link is not stable enough for the one-time encrypted setup. Move the iPhone beside the Dell; this is not required after pairing.`, `Il Dell ha trovato l'iPhone${rssi}, ma il collegamento radio non e abbastanza stabile per la configurazione cifrata iniziale. Avvicina l'iPhone al Dell; dopo l'associazione non sara piu necessario.`),
      };
    }
    if (pairing.state === "timeout" || pairing.detail_code === "waiting_for_iphone_advertisement") {
      return {
        tone: "warning",
        title: this.text("The Dell did not find the iPhone", "Il Dell non ha trovato l'iPhone"),
        body: this.text("Keep Presence Pair open beside the selected receiver. Nothing must be accepted on the Dell; create a new code after checking Bluetooth on the iPhone.", "Tieni Presence Pair aperta accanto al ricevitore selezionato. Sul Dell non devi accettare nulla; controlla il Bluetooth dell'iPhone e crea un nuovo codice."),
      };
    }
    if (["iphone_advertisement_seen", "iphone_connected", "iphone_session_verified"].includes(pairing.detail_code)) {
      return {
        tone: "",
        title: this.text("iPhone found", "iPhone trovato"),
        body: this.text("The Dell is completing the encrypted connection automatically. Keep Presence Pair open and tap Pair only if iOS asks.", "Il Dell sta completando automaticamente la connessione cifrata. Tieni Presence Pair aperta e tocca Abbina solo se lo chiede iOS."),
      };
    }
    if (pairing.state === "bonding" || ["iphone_claim_accepted", "iphone_ack_deferred"].includes(pairing.detail_code)) {
      return {
        tone: "",
        title: this.text("iPhone connected", "iPhone connesso"),
        body: this.text("The encrypted request arrived. Home Assistant is capturing and verifying the private Bluetooth identity.", "La richiesta cifrata è arrivata. Home Assistant sta acquisendo e verificando l'identità Bluetooth privata."),
      };
    }
    if (pairing.state === "waiting_for_app") {
      const updated = Date.parse(pairing.updated_at || "");
      const waited = Number.isFinite(updated) ? (Date.now() - updated) / 1000 : 0;
      if (waited >= 15) {
        return {
          tone: "warning",
          title: this.text("Still waiting for the iPhone", "Ancora in attesa dell'iPhone"),
          body: this.text("The Dell is searching. Keep the new Presence Pair screen open on the iPhone and verify that Bluetooth is allowed. There is no confirmation to make on the Dell.", "Il Dell sta cercando. Tieni aperta sull'iPhone la nuova schermata di Presence Pair e verifica che il Bluetooth sia consentito. Sul Dell non c'è alcuna conferma da dare."),
        };
      }
      return {
        tone: "",
        title: this.text("Scan and keep the app open", "Scansiona e tieni aperta l'app"),
        body: this.text("The iPhone now becomes visible to the selected receiver. Everything else is automatic; tap Pair only if iOS asks.", "L'iPhone ora diventa visibile al ricevitore selezionato. Tutto il resto è automatico; tocca Abbina solo se lo chiede iOS."),
      };
    }
    return {
      tone: "",
      title: this.text("Preparing the Dell receiver", "Preparazione del ricevitore Dell"),
      body: this.text("Wait until Home Assistant confirms that the receiver is searching, then scan the code.", "Attendi che Home Assistant confermi che il ricevitore sta cercando, poi scansiona il codice."),
    };
  }

  bind() {
    this.shadowRoot.querySelector('[data-action="refresh"]')?.addEventListener("click", () => this.load());
    this.shadowRoot.querySelector('[data-action="start"]')?.addEventListener("click", () => this.startPairing());
    this.shadowRoot.querySelector('[data-action="restart"]')?.addEventListener("click", (event) => this.restartPairing(event.currentTarget));
    this.shadowRoot.querySelector('[data-action="cancel"]')?.addEventListener("click", () => this.cancelPairing());
    this.shadowRoot.querySelectorAll('[data-action="area"]').forEach((element) => element.addEventListener("change", (event) => this.setObserverArea(event.currentTarget)));
    this.shadowRoot.querySelectorAll('[data-action="remove"]').forEach((element) => element.addEventListener("click", (event) => this.removeIdentity(event.currentTarget)));
    this.shadowRoot.querySelector("#person")?.addEventListener("change", (event) => { this._selectedPerson = event.currentTarget.value; });
    this.shadowRoot.querySelector("#observer")?.addEventListener("change", (event) => { this._selectedObserver = event.currentTarget.value; });
  }

  async startPairing() {
    const person = this.shadowRoot.querySelector("#person")?.value;
    const observer = this.shadowRoot.querySelector("#observer")?.value;
    if (!person || !observer || this._loading) return;
    this._loading = true;
    try {
      await this._hass.callWS({ type: "presence_bridge/start_pairing", person, observer_id: observer, timeout_seconds: 600 });
      this._loading = false;
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

  async restartPairing(element) {
    if (this._loading) return;
    this._loading = true;
    try {
      await this._hass.callWS({
        type: "presence_bridge/start_pairing",
        person: element.dataset.person,
        observer_id: element.dataset.observer,
        timeout_seconds: 600,
        force_new: true,
      });
      this._loading = false;
      await this.load(true);
    } catch (error) {
      this._error = error?.message || String(error);
    } finally {
      this._loading = false;
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
