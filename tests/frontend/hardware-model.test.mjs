import assert from "node:assert/strict";
import { test } from "node:test";
import { HardwareInventory, MAX_OBSERVATIONS, candidateEntities, localAddress } from "../../custom_components/myhome/frontend/panel/panel-hardware-model.js";

const state = (extra = {}) => ({ entry_id: "one", where: "01", phase: "finished", reason: null,
  hardware_id: "009B5409", firmware: "1.1.0", identity: ["107", "6", "1", "1"],
  modules: [{ slot: 1, object_id: 6, flag: "0", address: "24" }, { slot: 2, object_id: 6, flag: "0", address: "01" }],
  frames: [{ raw: "*#1001*01*13*10179593##", received_at: "2026-09-15T21:22:07Z" }], ...extra });

test("real 01/24 observations group by hardware ID only within one gateway", () => {
  const cache = new HardwareInventory();
  const first = state(); cache.record(first);
  cache.record(state({ where: "24" }));
  cache.record(state({ entry_id: "two" }));
  const [group] = cache.groups("one");
  assert.equal(cache.groups("one").length, 1);
  assert.deepEqual(group.reads.map((read) => read.state.where), ["01", "24"]);
  assert.equal(group.differs, false);
  first.modules[0].address = "99"; first.frames[0].raw = "changed";
  assert.equal(group.reads[0].state.modules[0].address, "24");
  assert.match(group.reads[0].state.frames[0].raw, /10179593/);
  cache.clear("one");
  assert.equal(cache.groups("one").length, 0); assert.equal(cache.groups("two").length, 1);
  cache.clear(); assert.equal(cache.observations.size, 0);
});

test("rereads replace normalized addresses; reassigned addresses leave the old ID group", () => {
  const cache = new HardwareInventory(); cache.record(state()); cache.record(state({ where: "24" }));
  cache.record(state({ where: "0001", hardware_id: "00000002" }));
  assert.equal(cache.observations.size, 2);
  assert.deepEqual(cache.groups("one").map((g) => [g.id, g.reads.map((r) => r.state.where)]), [["00000002", ["0001"]], ["009B5409", ["24"]]]);
  cache.record(state({ where: "0024" })); // A=0 PL=24 is different from A=2 PL=4.
  assert.equal(cache.observations.size, 3);
  for (const invalid of ["0", "00", "2400", "24#4#1", "#24", null, 24]) assert.equal(localAddress(invalid), null);
});

test("partial, failed and unidentified reads never replace retained evidence", () => {
  const cache = new HardwareInventory(); cache.record(state());
  for (const extra of [{ phase: "queued" }, { phase: "reading" }, { reason: "cancelled" },
    { reason: "ambiguous_identity" }, { reason: "frame_limit" }, { hardware_id: null },
    { hardware_id: "00000000" }, { hardware_id: "invalid" }, { where: "0" }, { entry_id: "" }]) cache.record(state({ firmware: "bad", ...extra }));
  assert.equal(cache.observations.size, 1);
  assert.equal(cache.groups("one")[0].latest.state.firmware, "1.1.0");
});

test("different descriptions remain explicit and latest details never inherit missing modules", () => {
  const cache = new HardwareInventory(); cache.record(state());
  cache.record(state({ where: "24", firmware: null, modules: [{ slot: 3, object_id: 400, address: null }] }));
  const [group] = cache.groups("one");
  assert.equal(group.differs, true); assert.equal(group.latest.state.firmware, null);
  assert.deepEqual(group.latest.state.modules.map((m) => m.slot), [3]);
});

test("inventory evicts the oldest address across gateways and rereads renew retention", () => {
  const cache = new HardwareInventory(); cache.record(state());
  for (let i = 1; i < MAX_OBSERVATIONS; i++) cache.record(state({ entry_id: `gateway-${i}` }));
  cache.record(state()); cache.record(state({ entry_id: "extra" }));
  assert.equal(cache.observations.size, MAX_OBSERVATIONS);
  assert.equal(cache.groups("one").length, 1); assert.equal(cache.groups("gateway-1").length, 0);
});

test("HA candidates require explicit local address, same entry and supported WHO; preserve multiple matches", () => {
  const base = { entry_id: "one", who: "1", address: { raw: "0001", interface: null }, entity_id: "light.one" };
  const entities = [base, { ...base, entity_id: "button.lock" },
    { ...base, entry_id: "two" }, { ...base, who: "2" }, { ...base, who: "25" },
    { ...base, address: { raw: "01#3", interface: null } },
    { ...base, address: { raw: "01", interface: "0" } },
    { ...base, address: { raw: "0011", interface: null } }, { ...base, address: null }];
  for (const object_id of [6, 8]) assert.deepEqual(candidateEntities({ object_id, address: "01" }, "one", entities).map((e) => e.entity_id), ["light.one", "button.lock"]);
  assert.equal(candidateEntities({ object_id: 218, address: "01" }, "one", entities).length, 1);
  for (const object_id of [400, 401, 999]) assert.deepEqual(candidateEntities({ object_id, address: "01" }, "one", entities), []);
  assert.deepEqual(candidateEntities({ object_id: 6, address: "01#4#1" }, "one", entities), []);
});
