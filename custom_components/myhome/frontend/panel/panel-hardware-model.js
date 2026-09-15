/** Bounded display cache of explicit observations, never configuration storage. */
export const MAX_OBSERVATIONS = 100;

export function localAddress(value) {
  if (typeof value !== "string" || !/^(?:[0-9]{2}|[0-9]{4})$/.test(value)) return null;
  const half = value.length / 2;
  const a = Number(value.slice(0, half)), pl = Number(value.slice(half));
  return pl ? `${a}:${pl}` : null;
}

export class HardwareInventory {
  constructor() { this.observations = new Map(); }

  record(state) {
    const address = localAddress(state.where);
    if (state.phase !== "finished" || state.reason || !state.entry_id || !address
      || !/^[0-9A-F]{8}$/.test(state.hardware_id || "") || state.hardware_id === "00000000") return;
    const key = JSON.stringify([state.entry_id, address]);
    // Replace the previous observation of this address, including a changed ID.
    this.observations.delete(key);
    this.observations.set(key, { state: structuredClone(state), readAt: new Date().toISOString() });
    if (this.observations.size > MAX_OBSERVATIONS) this.observations.delete(this.observations.keys().next().value);
  }

  clear(entryId) {
    for (const [key, observation] of this.observations) {
      if (entryId == null || observation.state.entry_id === entryId) this.observations.delete(key);
    }
  }

  groups(entryId) {
    const groups = new Map();
    for (const observation of this.observations.values()) {
      const state = observation.state;
      if (state.entry_id !== entryId) continue;
      let group = groups.get(state.hardware_id);
      if (!group) {
        group = { id: state.hardware_id, reads: [], differs: false };
        groups.set(group.id, group);
      }
      if (group.latest && description(group.latest.state) !== description(state)) group.differs = true;
      group.reads.push(observation);
      group.latest = observation;
    }
    return [...groups.values()].reverse();
  }
}

function description(state) {
  // Compare descriptions without inventing a merged configuration from old reads.
  return JSON.stringify([state.firmware ?? null, state.identity ?? null,
    [...(state.modules || [])].sort((a, b) => a.slot - b.slot)
      .map((m) => [m.slot, m.object_id, m.flag, localAddress(m.address) || m.address])]);
}

export function candidateEntities(module, entryId, entities) {
  const who = { 6: "1", 8: "1", 218: "2" }[module.object_id];
  const address = localAddress(module.address);
  if (!who || !address) return [];
  return entities.filter((entity) => entity.entry_id === entryId && entity.who === who
    && entity.address?.interface == null && localAddress(entity.address?.raw) === address);
}
