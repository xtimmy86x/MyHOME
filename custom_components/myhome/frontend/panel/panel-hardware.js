/** Explicit, short-lived hardware reads. No configuration lives in the browser. */
const url = new URL("panel-dom.js", import.meta.url);
url.search = new URL(import.meta.url).search;
const { escapeHtml: esc } = await import(url.href);
const objects = { 6: "hwObjectLight", 8: "hwObjectDimmer", 128: "hwObjectPresence", 164: "hwObjectDaylight", 218: "hwObjectCover", 400: "hwObjectLightControl", 401: "hwObjectCoverControl", 406: "hwObjectScenario", 431: "hwObjectIR" };

export class HardwareSection {
  constructor() { this.generation = 0; }

  close() {
    this.generation++;
    Promise.resolve(this.unsubscribe?.()).catch(() => {});
    this.unsubscribe = null;
    this.context = null;
    this.state = null;
    this.starting = false;
  }

  open(context) {
    if (this.context?.host === context.host && this.context.entry_id === context.entry_id && this.context.connected === context.connected && this.context.hass.connection === context.hass.connection) return;
    this.close();
    this.context = context;
    const { host, t, entry_id, connected } = context;
    host.innerHTML = `<article class="profile-section hardware-section"><h2>${esc(t("hardware"))}</h2>
      <p class="notice">${esc(t("hwHelp"))}</p><p class="muted">${esc(t("hwScope"))}</p>
      ${!entry_id || !connected ? `<p class="notice">${esc(t(entry_id ? "hwUnavailable" : "hwSelectGateway"))}</p>` : ""}
      <form id="hw-form"><div class="profile-times">
        <label>A<input name="ambient" type="number" min="0" max="99" step="1" value="0" required ${!entry_id || !connected ? "disabled" : ""}></label>
        <label>PL<input name="point" type="number" min="1" max="99" step="1" value="1" required ${!entry_id || !connected ? "disabled" : ""}></label>
      </div><div class="actions"><button id="hw-read" type="submit" ${!entry_id || !connected ? "disabled" : ""}>${esc(t("hwRead"))}</button>
        <button id="hw-cancel" type="button" hidden>${esc(t("hwCancel"))}</button></div></form>
      <p id="hw-status" role="status"></p><p id="hw-error" role="alert"></p><div id="hw-result"></div></article>`;
    host.querySelector("#hw-form").onsubmit = (event) => { event.preventDefault(); this.read(); };
    host.querySelector("#hw-cancel").onclick = () => {
      this.generation++;
      Promise.resolve(this.unsubscribe?.()).catch(() => {});
      this.unsubscribe = null; this.starting = false;
      this.state = { ...this.state, phase: "finished", reason: "cancelled" };
      this.render();
    };
  }

  async read() {
    if (!this.context || this.starting || ["queued", "reading"].includes(this.state?.phase)) return;
    const { hass, entry_id, connected, host, t } = this.context;
    const form = host.querySelector("#hw-form");
    if (!entry_id || !connected || !form.reportValidity()) return;
    const a = Number(form.elements.ambient.value), pl = Number(form.elements.point.value);
    const width = a > 9 || pl > 9 ? 2 : 1;
    const where = `${String(a).padStart(width, "0")}${String(pl).padStart(width, "0")}`;
    Promise.resolve(this.unsubscribe?.()).catch(() => {}); this.unsubscribe = null;
    const generation = ++this.generation;
    this.starting = true;
    this.state = { entry_id, where, phase: "queued", sequence: 0, modules: [], frames: [] };
    host.querySelector("#hw-result").innerHTML = "";
    host.querySelector("#hw-error").textContent = "";
    this.render();
    try {
      const unsubscribe = await hass.connection.subscribeMessage((state) => {
        if (generation !== this.generation || !host.isConnected || state.entry_id !== entry_id) return;
        if (this.state && state.sequence < this.state.sequence) return;
        this.state = state; this.render();
      }, { type: "myhome/hardware/inspect", entry_id, where });
      if (generation !== this.generation || !host.isConnected) { Promise.resolve(unsubscribe()).catch(() => {}); return; }
      this.unsubscribe = unsubscribe;
    } catch (error) {
      if (generation === this.generation && host.isConnected) {
        this.state = null;
        const key = `hwError_${error.code}`;
        host.querySelector("#hw-error").textContent = t(key) === key ? t("hwError") : t(key);
      }
    } finally {
      if (generation === this.generation && host.isConnected) { this.starting = false; this.render(); }
    }
  }

  render() {
    const { host, t, connected, entry_id } = this.context;
    const state = this.state;
    const busy = this.starting || ["queued", "reading"].includes(state?.phase);
    host.querySelector("#hw-read").disabled = busy || !entry_id || !connected;
    host.querySelector("#hw-cancel").hidden = !busy;
    for (const input of host.querySelectorAll("#hw-form input")) input.disabled = busy || !connected;
    host.querySelector("#hw-status").textContent = t(`hwPhase_${state?.phase || (this.starting ? "queued" : "idle")}`);
    if (!state) return;
    if (state.reason) host.querySelector("#hw-error").textContent = t(`hwError_${state.reason}`);
    const unknown = t("hwUnknown");
    host.querySelector("#hw-result").innerHTML = `<div class="profile-stats">
      <div><strong>${esc(t("hwId"))}</strong><p>${esc(state.hardware_id || unknown)}</p></div>
      <div><strong>${esc(t("firmware"))}</strong><p>${esc(state.firmware || unknown)}</p></div>
      <div><strong>${esc(t("hwIdentity"))}</strong><p>${esc(state.identity?.join(" · ") || unknown)}</p></div></div>
      <h3>${esc(t("hwModules"))}</h3><div class="hw-modules">${(state.modules || []).map((m) => `<article class="profile-section">
        <strong>${esc(t("hwSlot"))} ${esc(m.slot)}</strong>
        <p>${esc(m.object_id == null ? unknown : `${m.object_id} · ${objects[m.object_id] ? t(objects[m.object_id]) : unknown}`)}</p>
        <p>${esc(t("hwAddress"))}: ${esc(m.address || unknown)}</p>
        <p>${esc(t("hwModuleState"))}: ${esc(m.disabled === true ? t("disabled") : m.disabled === false ? t("hwEnabled") : unknown)}${m.flag == null ? "" : ` (${esc(m.flag)})`}</p>
      </article>`).join("") || `<p>${esc(t("hwNoModules"))}</p>`}</div>
      ${state.unassociated_frames ? `<p class="notice">${esc(t("hwUnassociated"))}: ${esc(state.unassociated_frames)}</p>` : ""}
      <details><summary>${esc(t("hwFrames"))} (${(state.frames || []).length})</summary><pre class="hw-frames">${(state.frames || []).map((f) => esc(`${f.received_at} ${f.raw}`)).join("\n")}</pre></details>`;
  }
}
