"""Experimental WHO1001 description reads for one explicit local A/PL address.

Only a DIM0 read is queued. No enumeration-state, configuration or programming
commands are sent. Results are transient observations, never HA registry writes.
"""
from __future__ import annotations

import asyncio
import re
from time import monotonic

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_MAC, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, callback
from OWNd.message import OWNMessage

from .const import DOMAIN
from .websocket import _get_gateway_and_monitor

WS_INSPECT = "myhome/hardware/inspect"
DATA_KEY = "myhome_hardware_inspections"
READ_SECONDS = 20
QUEUE_SECONDS = 10
MAX_FRAMES = 200
MAX_MODULES = 64


def address(value):
    """Local individual A/PL only: reject ambient/group/routed addresses."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]{2}|[0-9]{4})", value):
        raise vol.Invalid("invalid_address")
    half = len(value) // 2
    a, pl = int(value[:half]), int(value[half:])
    if pl == 0:
        raise vol.Invalid("invalid_address")
    return (a, pl)


def validate_address(value):
    address(value)
    return value


class InspectionError(Exception):
    """Stable public refusal reason."""


def gateway(hass, entry_id):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise InspectionError("target_not_found")
    gw, monitor = _get_gateway_and_monitor(hass, entry.data.get(CONF_MAC)) if entry.data.get(CONF_MAC) else (None, None)
    if (entry.state != ConfigEntryState.LOADED or entry.disabled_by is not None
            or hass.state in (CoreState.stopping, CoreState.final_write, CoreState.stopped)
            or gw is None or monitor is None or not gw.is_connected):
        raise InspectionError("gateway_unavailable")
    return gw, monitor


class HardwareInspection:
    """One bounded observation window; socket cleanup invalidates queued reads."""

    def __init__(self, hass, connection, msg, gw, monitor):
        self.hass, self.connection, self.id = hass, connection, msg["id"]
        self.entry_id, self.where = msg["entry_id"], msg["where"]
        self.gw, self.monitor = gw, monitor
        self.request = f"*#1001*{self.where}*0##"
        self.scope = address(self.where)
        self.phase, self.reason, self.sequence = "queued", None, 0
        self.frames, self.modules = [], {}
        self.hardware_id = self.identity = self.firmware = None
        self.unassociated = 0
        self.expires = monotonic() + QUEUE_SECONDS
        self.active = True
        self.unsubscribe = monitor.subscribe(self.on_frame)
        self.shutdown = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.on_shutdown)
        self.timer = hass.loop.call_later(QUEUE_SECONDS, self.finish, "queue_timeout")

    def view(self):
        return {"entry_id": self.entry_id, "where": self.where, "sequence": self.sequence,
                "phase": self.phase, "reason": self.reason, "read_only": True,
                "hardware_id": self.hardware_id, "identity": self.identity, "firmware": self.firmware,
                "modules": [dict(value) for _, value in sorted(self.modules.items())],
                "frames": list(self.frames), "unassociated_frames": self.unassociated}

    def emit(self):
        self.sequence += 1
        self.connection.send_event(self.id, self.view())

    def guard(self):
        if not self.active or monotonic() > self.expires:
            return False
        try:
            gw, monitor = gateway(self.hass, self.entry_id)
            if gw is not self.gw or monitor is not self.monitor:
                raise InspectionError("gateway_unavailable")
        except InspectionError:
            self.finish("gateway_unavailable")
            return False
        return True

    @callback
    def on_shutdown(self, _event):
        self.shutdown = None
        self.close()

    def finish(self, reason=None, notify=True):
        if not self.active:
            return
        self.active = False
        self.phase, self.reason = "finished", reason
        self.timer.cancel()
        self.unsubscribe()
        if self.shutdown is not None:
            unsubscribe, self.shutdown = self.shutdown, None
            unsubscribe()
        if self.hass.data.get(DATA_KEY, {}).get(self.entry_id) is self:
            self.hass.data[DATA_KEY].pop(self.entry_id)
        if notify:
            self.emit()

    def close(self):
        self.finish("cancelled", notify=False)

    def on_frame(self, frame):
        raw = frame.raw
        if not self.active or not re.match(r"^\*#?1001\*", raw):
            return
        if frame.direction == "tx":
            if raw != self.request or self.phase != "queued":
                self.finish("overlapping_read")
                return
            self.phase = "reading"
            self.timer.cancel()
            self.timer = self.hass.loop.call_later(READ_SECONDS, self.finish)
            self.emit()
            return
        if frame.direction != "rx" or self.phase != "reading":
            return
        if len(self.frames) >= MAX_FRAMES:
            self.finish("frame_limit")
            return
        if len(raw) > 512:
            return
        self.frames.append({"raw": raw, "received_at": frame.iso_time})
        match = re.fullmatch(r"\*#1001\*([0-9#]+)\*([0-9#]+)\*((?:[0-9#]+\*)*[0-9#]+)##", raw)
        if match:
            try:
                matched = address(match[1]) == self.scope
            except vol.Invalid:
                matched = False
            if matched:
                self.decode(match[2], match[3].split("*"))
            else:
                self.unassociated += 1
        if self.active:
            self.emit()

    def decode(self, dimension, values):
        """Decode only documented shapes; retain every original frame separately."""
        if dimension == "13" and len(values) == 1 and values[0].isdigit() and len(values[0]) <= 10:
            number = int(values[0])
            if 0 < number <= 0xFFFFFFFF:
                hardware_id = f"{number:08X}"
                if self.hardware_id is not None and self.hardware_id != hardware_id:
                    self.hardware_id = None
                    self.identity = self.firmware = None
                    self.modules.clear()
                    self.finish("ambiguous_identity")
                    return
                self.hardware_id = hardware_id
        elif dimension == "1" and len(values) == 4 and all(v.isdigit() and len(v) <= 6 for v in values):
            self.identity = list(values)  # Catalogue signature, not a unique product/SKU.
        elif dimension == "2" and len(values) == 3 and all(v.isdigit() and len(v) <= 6 for v in values):
            self.firmware = ".".join(values)
        elif dimension == "30" and len(values) == 3 and all(v.isdigit() and len(v) <= 6 for v in values):
            slot = int(values[0])
            module = self.module(slot)
            if module is not None:
                module.update(object_id=int(values[1]), disabled={"0": False, "1": True}.get(values[2]), flag=values[2])
        elif re.fullmatch(r"32#[0-9]{1,6}", dimension) and len(values) == 2 and values[0] == "1":
            module = self.module(int(dimension.split("#")[1]))
            if module is not None:
                module["address"] = values[1]  # No inferred WHO or registry assignment.

    def module(self, slot):
        if slot not in self.modules:
            if len(self.modules) >= MAX_MODULES:
                self.finish("module_limit")
                return None
            self.modules[slot] = {"slot": slot, "object_id": None, "disabled": None, "flag": None, "address": None}
        return self.modules[slot]


@websocket_api.websocket_command({
    vol.Required("type"): WS_INSPECT, vol.Required("entry_id"): str,
    vol.Required("where"): validate_address,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_inspect(hass, connection, msg):
    try:
        gw, monitor = gateway(hass, msg["entry_id"])
        sessions = hass.data.setdefault(DATA_KEY, {})
        if msg["entry_id"] in sessions:
            raise InspectionError("inspection_busy")
        session = HardwareInspection(hass, connection, msg, gw, monitor)
        sessions[msg["entry_id"]] = session
        # Existing command workers serialize this short-lived read and collect RX.
        gw.send_buffer.put_nowait({"message": OWNMessage(session.request), "is_status_request": True,
                                  "guard": session.guard, "command_lock": asyncio.Lock()})
    except InspectionError as error:
        connection.send_error(msg["id"], str(error), str(error))
        return
    except asyncio.QueueFull:
        session.close()
        connection.send_error(msg["id"], "command_queue_full", "command_queue_full")
        return
    connection.subscriptions[msg["id"]] = session.close
    connection.send_result(msg["id"])
    session.emit()
