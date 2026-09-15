import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { JSDOM } from "jsdom";
import { HardwareSection } from "../../custom_components/myhome/frontend/panel/panel-hardware.js";
import { translations } from "../../custom_components/myhome/frontend/panel/panel-translations.js";
const dom = new JSDOM("<body></body>");
const instances = [];
const tick = () => new Promise((resolve) => setImmediate(resolve));
const t = (key) => translations.it[key] || key;
function mount({ subscribe, connected = true, entry_id = "one" } = {}) {
  const host = dom.window.document.createElement("div"); dom.window.document.body.append(host);
  const section = new HardwareSection(); instances.push(section);
  let callback, stopped = 0;
  const requests = [];
  const hass = { connection: { subscribeMessage: async (cb, request) => {
    callback = cb; requests.push(request);
    return subscribe ? subscribe(() => stopped++) : () => stopped++;
  } } };
  const context = { host, hass, connected, entry_id, t };
  section.open(context);
  return { section, host, context, requests, push: (state) => callback({ entry_id, where: "0015", sequence: 1, phase: "reading", modules: [], frames: [], ...state }), stopped: () => stopped };
}
afterEach(() => { instances.splice(0).forEach((s) => s.close()); dom.window.document.body.replaceChildren(); });
after(() => dom.window.close());

test("hardware waits for explicit A/PL read and shows scoped raw/decoded data", async () => {
  const m = mount(); assert.equal(m.requests.length, 0);
  const form = m.host.querySelector("form"); form.elements.point.value = "15";
  m.host.querySelector("#hw-read").click(); await tick();
  assert.deepEqual(m.requests, [{ type: "myhome/hardware/inspect", entry_id: "one", where: "0015" }]);
  assert.equal(m.host.querySelector("#hw-read").disabled, true);
  m.push({ hardware_id: "08CF44BF", firmware: "2.16.0", identity: [81, 6, 2, 0],
    modules: [{ slot: 1, object_id: 218, disabled: false, flag: "0", address: "11" }],
    frames: [{ raw: "<img src=x onerror=bad()>", received_at: "2026-09-15" }], unassociated_frames: 1 });
  assert.match(m.host.textContent, /08CF44BF/); assert.match(m.host.textContent, /Attuatore tapparella/);
  assert.match(m.host.textContent, /indirizzo diverso o generico/); assert.equal(m.host.querySelector("img"), null);
  m.push({ sequence: 2, phase: "finished" });
  assert.equal(m.host.querySelector("#hw-read").disabled, false);
  m.section.open(m.context); assert.equal(m.requests.length, 1);
});

test("cancel invalidates a late subscription and navigation ignores old results", async () => {
  let resolve;
  const m = mount({ subscribe: (unsub) => new Promise((done) => { resolve = () => done(unsub); }) });
  m.host.querySelector("#hw-read").click(); await tick();
  m.host.querySelector("#hw-cancel").click(); resolve(); await tick();
  assert.equal(m.stopped(), 1); assert.equal(m.host.querySelector("#hw-read").disabled, false);
  m.push({ hardware_id: "stale" }); assert.doesNotMatch(m.host.textContent, /stale/);
  m.section.open({ ...m.context, entry_id: "two" });
  m.push({ hardware_id: "other gateway" }); assert.doesNotMatch(m.host.textContent, /other gateway/);
});

test("hardware read errors allow retry; missing gateway and offline disable requests", async () => {
  for (const options of [{ entry_id: "" }, { connected: false }]) {
    const m = mount(options); m.host.querySelector("#hw-read").click(); assert.equal(m.requests.length, 0);
  }
  const m = mount({ subscribe: () => { throw { code: "inspection_busy" }; } });
  m.host.querySelector("#hw-read").click(); await tick();
  assert.match(m.host.querySelector("#hw-error").textContent, /Un’altra lettura/);
  assert.equal(m.host.querySelector("#hw-read").disabled, false);
  m.section.open({ ...m.context, connected: false });
  assert.equal(m.host.querySelector("#hw-read").disabled, true);
});
