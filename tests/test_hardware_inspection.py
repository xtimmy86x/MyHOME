"""WHO1001 reads: allowlisted transport, attribution and finite socket lifecycle."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.exceptions import Unauthorized
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome import hardware_inspection as hw
from custom_components.myhome.bus_monitor import BusFrame, BusMonitor
from custom_components.myhome.const import CONF_ENTITY, DOMAIN
from tests.test_panel import installation as installation_fixture

installation = installation_fixture


@pytest.fixture
async def inspection(hass, installation, request):
    entry = installation.entries[0]
    monitor = BusMonitor(dedup_window=0)
    gw = SimpleNamespace(is_connected=True, send_buffer=asyncio.Queue())
    hass.data[DOMAIN][entry.data["mac"]].update({CONF_ENTITY: gw, "bus_monitor": monitor})
    connection = MagicMock(user=SimpleNamespace(is_admin=True), subscriptions={})
    msg = {"id": 20, "entry_id": entry.entry_id, "where": getattr(request, "param", "0015"), "type": hw.WS_INSPECT}
    hw.ws_inspect(hass, connection, msg)
    await hass.async_block_till_done()
    session = hass.data[hw.DATA_KEY][entry.entry_id]
    yield SimpleNamespace(
        session=session,
        gw=gw,
        monitor=monitor,
        connection=connection,
        entry=entry,
        msg=msg,
        installation=installation,
    )
    for session in list(hass.data.get(hw.DATA_KEY, {}).values()):
        session.close()


def receive(inspection, raw, direction="rx"):
    inspection.monitor.record_frame(direction, raw)


def dispatch(inspection):
    task = inspection.gw.send_buffer.get_nowait()
    assert task["guard"]()
    receive(inspection, str(task["message"]), "tx")
    return task


async def test_read_sends_only_dim0_and_decodes_scoped_description_without_writes(hass, inspection):
    original = copy.deepcopy(dict(inspection.entry.data))
    receive(inspection, "*#1001*0015*13*7##")  # Before dispatch, not this read.
    assert inspection.session.frames == []
    task = dispatch(inspection)
    assert str(task["message"]) == "*#1001*0015*0##"
    assert task["is_status_request"] is True
    for raw in [
        "*#1001*0015*1*81*6*2*0##",
        "*#1001*0015*2*2*16*0##",
        "*#1001*0015*13*147801279##",
        "*#1001*0015*32#1*1*0015##",
        "*#1001*0015*30*1*218*0##",
        "*#1001*0015*30*2*400*1##",
        "*#1001*0015*30*3*999*7##",
        "*#1001*0015*7*111111111111111101101111##",
        "*#1001*0*30*9*6*0##",
        "*#1001*16*30*8*6*0##",
        "*#1001*0015#4#1*30*7*6*0##",
        "*1*1*15##",
    ]:
        receive(inspection, raw)
    view = inspection.session.view()
    assert view["hardware_id"] == "08CF44BF"
    assert view["identity"] == ["81", "6", "2", "0"]
    assert view["firmware"] == "2.16.0"
    assert view["modules"] == [
        {"slot": 1, "object_id": 218, "disabled": False, "flag": "0", "address": "0015"},
        {"slot": 2, "object_id": 400, "disabled": True, "flag": "1", "address": None},
        {"slot": 3, "object_id": 999, "disabled": None, "flag": "7", "address": None},
    ]
    assert view["unassociated_frames"] == 3
    assert len(view["frames"]) == 11
    assert all(item["received_at"] for item in view["frames"])
    inspection.session.timer._run()
    assert inspection.session.phase == "finished" and not inspection.session.active
    assert inspection.gw.send_buffer.empty()
    assert dict(inspection.entry.data) == original
    assert not inspection.monitor._subscribers
    assert not hass.data[hw.DATA_KEY]


@pytest.mark.parametrize("inspection", ["01"], indirect=True)
async def test_real_f454_description_finishes_after_boundary_settles(inspection):
    """User's 2026-09-15 capture: 13 RX frames in about 0.675 seconds."""
    dispatch(inspection)
    frames = [
        "*#1001*01*1*107*6*1*1##",
        "*1001*3*71##",
        "*#1001*01*2*1*1*0##",
        "*#1001*01*4*0*0*0*0*0*0##",
        "*#1001*01*7*111111111111111101101111##",
        "*#1001*01*13*10179593##",
        "*#1001*01*30*1*6*0##",
        "*#1001*01*32#1*1*24##",
        "*#1001*01*30*2*6*0##",
        "*#1001*01*32#2*1*01##",
        "*#1001*01*30*3*400*0##",
        "*#1001*01*30*4*400*0##",
        "*1001*4*0##",
    ]
    for raw in frames[:-1]:
        receive(inspection, raw)
        assert inspection.session.settle_timer is None
    receive(inspection, frames[-1])
    session = inspection.session
    assert session.active and session.phase == "reading"
    assert session.settle_timer.when() < session.timer.when()
    session.settle_timer._run()
    assert not session.active and session.phase == "finished" and session.reason is None
    assert session.hardware_id == "009B5409" and session.firmware == "1.1.0"
    assert session.identity == ["107", "6", "1", "1"]
    assert [m["object_id"] for m in session.modules.values()] == [6, 6, 400, 400]
    assert [m["address"] for m in session.modules.values()] == ["24", "01", None, None]
    assert [frame["raw"] for frame in session.frames] == frames
    assert session.timer.cancelled() and session.settle_timer.cancelled()
    assert not inspection.monitor._subscribers and inspection.gw.send_buffer.empty()


async def test_boundary_requires_dispatched_scoped_identity_and_exact_what4(inspection):
    receive(inspection, "*1001*4*0##")
    dispatch(inspection)
    for raw in ["*1001*4*0##", "*#1001*0*13*1##", "*#1001*24*13*1##", "*1001*4*0##",
                "*#1001*0015*13*1##", "*#1001*0015*4*0*0*0*0*0*0##", "*1001*4*24##"]:
        receive(inspection, raw)
        assert inspection.session.settle_timer is None
    # A description without the observed end boundary retains the original ceiling.
    inspection.session.timer._run()
    assert inspection.session.phase == "finished"


async def test_trailing_diagnostics_restart_quiet_period_without_extending_deadline(inspection):
    dispatch(inspection)
    receive(inspection, "*#1001*0015*13*1##")
    receive(inspection, "*1001*4*0##")
    session = inspection.session
    deadline, first = session.timer, session.settle_timer
    receive(inspection, "*1*1*11##")
    assert session.settle_timer is first  # Ordinary bus traffic is irrelevant.
    receive(inspection, "*#1001*0015*30*1*6*0##")
    second = session.settle_timer
    assert first.cancelled() and second is not first
    assert second.when() >= first.when() and session.timer is deadline
    second._run()
    assert session.modules[1]["object_id"] == 6
    assert session.phase == "finished" and session.reason is None
    count = len(session.frames)
    receive(inspection, "*#1001*0015*30*2*6*0##")
    assert len(session.frames) == count


@pytest.mark.parametrize("outcome", ["cancel", "shutdown", "overlap", "ambiguous", "timeout"])
async def test_quiet_period_cleanup_and_original_failure_protections(hass, inspection, outcome):
    dispatch(inspection)
    receive(inspection, "*#1001*0015*13*1##")
    receive(inspection, "*1001*4*0##")
    session = inspection.session
    if outcome == "cancel":
        session.close()
    elif outcome == "shutdown":
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    elif outcome == "overlap":
        receive(inspection, "*#1001*24*0##", "tx")
        assert session.reason == "overlapping_read"
    elif outcome == "ambiguous":
        receive(inspection, "*#1001*0015*13*2##")
        assert session.reason == "ambiguous_identity" and session.hardware_id is None
    else:
        session.timer._run()
    assert not session.active and session.timer.cancelled() and session.settle_timer.cancelled()
    assert not hass.data[hw.DATA_KEY] and not inspection.monitor._subscribers


@pytest.mark.parametrize(
    "value", ["0", "00", "1000", "11#4#1", "#11", "*#1001*0*13##", "", None, 11]
)
def test_rejects_group_routed_and_injected_addresses(value):
    with pytest.raises(vol.Invalid):
        hw.validate_address(value)


async def test_equivalent_apl_notation_and_unknown_shapes_remain_raw(inspection):
    inspection.session.scope = (0, 1)
    dispatch(inspection)
    for raw in [
        "*#1001*01*13*1##",
        "*#1001*0001*13*1##",
        "*#1001*0001*13*0##",
        "*#1001*0001*13*4294967296##",
        "*#1001*0001*13*999999999999999##",
        "*#1001*0001*2*1*2##",
        "*#1001*0001*1*1*2*3##",
        "*#1001*0001*35#10#1*42##",
        "*#1001*0001*30*9999999*6*0##",
        "*#1001*0001*32#1*2*11##",
        "*1001*4*0##",
        "*#1001*0001*2*1#1*2*3##",
    ]:
        receive(inspection, raw)
    assert inspection.session.hardware_id == "00000001"
    assert inspection.session.identity is inspection.session.firmware is None
    assert inspection.session.modules == {}
    assert len(inspection.session.frames) == 12
    receive(inspection, "*#1001*0001*7*" + "1" * 513 + "##")
    inspection.session.on_frame(BusFrame("other", "*#1001*0001*13*5##"))
    assert len(inspection.session.frames) == 12


async def test_conflicting_hardware_ids_discard_decoded_details(inspection):
    dispatch(inspection)
    for raw in ["*#1001*0015*13*1##", "*#1001*0015*30*1*6*0##", "*#1001*0015*13*2##"]:
        receive(inspection, raw)
    assert inspection.session.reason == "ambiguous_identity"
    assert inspection.session.hardware_id is None
    assert inspection.session.modules == {}
    assert len(inspection.session.frames) == 3


@pytest.mark.parametrize("limit", ["frames", "modules"])
async def test_bounded_results(inspection, monkeypatch, limit):
    dispatch(inspection)
    monkeypatch.setattr(hw, "MAX_FRAMES" if limit == "frames" else "MAX_MODULES", 1)
    receive(inspection, "*#1001*0015*30*1*6*0##")
    receive(inspection, "*#1001*0015*30*2*6*0##")
    assert inspection.session.reason == ("frame_limit" if limit == "frames" else "module_limit")
    assert len(inspection.session.modules) == 1


@pytest.mark.parametrize(
    "reason", ["cancel", "shutdown", "queue_timeout", "offline", "reload", "overlap"]
)
async def test_cleanup_and_queued_guard(hass, inspection, reason):
    task = inspection.gw.send_buffer.get_nowait()
    if reason == "cancel":
        inspection.connection.subscriptions[20]()
    elif reason == "shutdown":
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    elif reason == "queue_timeout":
        with patch.object(hw, "monotonic", return_value=inspection.session.expires + 1):
            assert not task["guard"]()
        inspection.session.timer._run()
    elif reason == "offline":
        inspection.gw.is_connected = False
        assert not task["guard"]()
    elif reason == "reload":
        hass.data[DOMAIN][inspection.entry.data["mac"]][CONF_ENTITY] = SimpleNamespace(
            is_connected=True
        )
        assert not task["guard"]()
    else:
        receive(inspection, "*#1001*11*0##", "tx")
    assert not task["guard"]()
    assert not inspection.monitor._subscribers
    assert not hass.data[hw.DATA_KEY]
    inspection.session.close()  # Cleanup is idempotent.
    inspection.session.on_frame(BusFrame("rx", "*#1001*0015*13*1##"))
    assert inspection.session.frames == []


async def test_gateway_and_owner_refusals_before_queueing(hass, inspection):
    hw.ws_inspect(hass, inspection.connection, {**inspection.msg, "id": 21})
    await hass.async_block_till_done()
    assert inspection.connection.send_error.call_args.args[1] == "inspection_busy"
    assert inspection.gw.send_buffer.qsize() == 1
    for entry_id in [
        "missing",
        inspection.installation.entries[2].entry_id,
        inspection.installation.entries[1].entry_id,
    ]:
        with pytest.raises(hw.InspectionError):
            hw.gateway(hass, entry_id)
    with patch.object(hass, "state", CoreState.stopping), pytest.raises(hw.InspectionError):
        hw.gateway(hass, inspection.entry.entry_id)
    inspection.session.close()
    with patch.object(inspection.gw.send_buffer, "put_nowait", side_effect=asyncio.QueueFull):
        hw.ws_inspect(hass, inspection.connection, {**inspection.msg, "id": 22})
        await hass.async_block_till_done()
    assert inspection.connection.send_error.call_args.args[1] == "command_queue_full"
    assert not hass.data[hw.DATA_KEY] and not inspection.monitor._subscribers


@pytest.mark.parametrize("user", [None, SimpleNamespace(is_admin=False)])
async def test_admin_before_gateway_lookup(hass, user):
    with patch.object(hw, "gateway") as get_gateway, pytest.raises(Unauthorized):
        hw.ws_inspect(hass, MagicMock(user=user), {})
    get_gateway.assert_not_called()


async def test_real_websocket_start_and_disconnect(hass, inspection, hass_ws_client):
    from aiohttp.resolver import ThreadedResolver

    inspection.session.close()
    websocket_api.async_register_command(hass, hw.ws_inspect)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    await client.send_json({**inspection.msg, "id": 1})
    assert (await client.receive_json())["success"]
    event = (await client.receive_json())["event"]
    assert event["phase"] == "queued" and event["read_only"]
    current = hass.data[hw.DATA_KEY][inspection.entry.entry_id]
    await client.close()
    await hass.async_block_till_done()
    assert not current.active and not current.guard()


async def test_real_command_worker_carries_read_flag_and_feeds_monitor(hass, inspection):
    from custom_components.myhome.gateway import MyHOMEGatewayHandler
    from tests.test_gateway import mock_config_entry as entry_fixture

    entry = entry_fixture.__wrapped__()
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.is_connected = True
    handler._event_session_ready.set()
    handler.bus_monitor = inspection.monitor
    handler.send_buffer = inspection.gw.send_buffer
    hass.data[DOMAIN][inspection.entry.data["mac"]][CONF_ENTITY] = handler
    inspection.session.gw = handler
    handler.send_buffer.put_nowait(None)
    with patch("custom_components.myhome.gateway.OWNCommandSession") as factory:
        command = factory.return_value
        command.connect = AsyncMock(return_value={"Success": True})
        command.send = AsyncMock(return_value=["*#1001*0015*13*147801279##"])
        command.close = AsyncMock()
        await handler.sending_loop(0)
    assert command.send.call_args.kwargs["is_status_request"] is True
    assert str(command.send.call_args.kwargs["message"]) == "*#1001*0015*0##"
    assert inspection.session.hardware_id == "08CF44BF"
