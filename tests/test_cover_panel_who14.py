"""WHO 14 integration must preserve panel geometry without inventing motor feedback."""
import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import State
from homeassistant.helpers.restore_state import RestoredExtraData
from OWNd.message import OWNMessage

from custom_components.myhome.actuator_lock import ActuatorLockManager
from custom_components.myhome.cover_motion import MotionPosition
from custom_components.myhome.cover_translation import movement_translation
from tests.test_cover_nonlinear_backend import nonlinear
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_cover_travel_scaling import write

plant = plant_fixture


async def prepare(hass, plant):
    cover, gateway = plant.covers[0], plant.gateways[0]
    gateway.hass = hass
    gateway.config_entry = plant.entries[0]
    gateway._actuator_locks = ActuatorLockManager(gateway)
    cover._register_actuator_lock()
    await write(hass, plant, profile=nonlinear(reference_travel_cm=None))
    cover._motion.confirm(50)
    cover._start_position = cover._attr_current_cover_position = 50
    return cover


@pytest.mark.parametrize("locked", [None, True])
async def test_synthetic_translation_cannot_confirm_or_unlock_profiled_cover(hass, plant, locked):
    cover = await prepare(hass, plant)
    if locked is not None:
        cover.handle_actuator_lock(locked)
    cover._begin_run("open")
    translated = movement_translation(OWNMessage.parse("*2*1000#1*11##"))
    assert translated is not None
    cover.handle_event(translated)
    assert cover.actuator_locked is locked
    assert not cover._move_confirmed
    assert cover._motion.anchor is None
    assert cover.current_cover_position == 50
    # Actual actuator feedback still confirms motion and recovers an offline unlock.
    cover.handle_event(OWNMessage.parse("*2*1*11##"))
    assert cover._move_confirmed
    assert cover._motion.anchor is not None
    assert cover.actuator_locked is not True
    cover._freeze_position(cover._motion.anchor)


async def test_stationary_model_and_lock_share_restore_record(hass, plant):
    cover = await prepare(hass, plant)
    cover.handle_actuator_lock(True)
    cover._last_run = {"seconds": 5.0, "direction": "open", "ended_at": "2026-11-25T10:00:00Z"}
    record = cover.extra_restore_state_data.as_dict()
    assert record["motion"]["height"] == 0.5
    assert record["actuator_locked"] is True
    cover._motion.position = None
    cover._actuator_locked = None
    cover._last_run = {}
    with patch.object(cover, "async_get_last_extra_data", AsyncMock(return_value=RestoredExtraData(record))):
        await cover.async_restore_last_state(State(cover.entity_id, "unavailable"))
    assert cover._motion.position == MotionPosition.at_height(0.5)
    assert cover.actuator_locked is True
    assert cover.extra_state_attributes["last_run_seconds"] == 5.0
    assert cover._relay_hold_until is None


async def test_old_model_record_cannot_replace_a_live_run(hass, plant):
    cover = await prepare(hass, plant)
    record = cover.extra_restore_state_data.as_dict()
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
        anchor = cover._motion.anchor
        with patch.object(cover, "async_get_last_extra_data", AsyncMock(return_value=RestoredExtraData(record))):
            await cover.async_restore_last_state(State(cover.entity_id, "unavailable"))
        assert cover._motion.anchor == anchor
        assert cover.is_opening
        cover._freeze_position(101)


async def test_lock_freezes_nonlinear_height_and_slats(hass, plant):
    cover = await prepare(hass, plant)
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
    expected = cover._motion.sample(103)
    with patch("custom_components.myhome.cover.time.monotonic", return_value=103):
        cover.handle_actuator_lock(True)
    assert cover._motion.position == expected
    assert cover._motion.anchor is None
    assert not cover.is_opening and not cover.is_closing
    assert cover.actuator_locked is True


async def test_ack_without_motor_echo_does_not_start_nonlinear_clock(hass, plant):
    cover = await prepare(hass, plant)
    written = asyncio.get_running_loop().create_future()
    cover._begin_run("open")
    cover._track_write(written)
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        written.set_result(100)
        await asyncio.sleep(0)
        assert cover._motion.anchor is None
        assert cover.current_cover_position == 50
        cover._abort_motion()
    assert cover._motion.position == MotionPosition.at_height(0.5)
    assert cover._motion.pending is None


async def test_unconfirmed_nonlinear_target_finishes_without_background_exception(hass, plant, monkeypatch):
    cover = await prepare(hass, plant)
    monkeypatch.setattr("custom_components.myhome.cover.ECHO_WINDOW", 0.01)

    async def send(command):
        result = asyncio.get_running_loop().create_future()
        result.set_result(time.monotonic())
        return result

    async def no_reply(command):
        result = asyncio.get_running_loop().create_future()
        result.cancel()
        return result

    plant.gateways[0].send = send
    plant.gateways[0].send_status_request = no_reply
    await cover.async_set_cover_position(position=70)
    task = cover._stop_task
    assert task is not None
    await asyncio.wait_for(task, timeout=2)
    assert cover.current_cover_position == 50
    assert cover._motion.anchor is None
    assert cover._motion.pending is None
    assert cover._actuation_unconfirmed
    assert not cover.is_opening and not cover.is_closing


async def test_feedback_arriving_during_model_restore_keeps_the_live_anchor(hass, plant):
    cover = await prepare(hass, plant)

    async def restore(_model):
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
        return MotionPosition.at_height(0.1)

    with patch.object(cover, "_async_restored_motion", side_effect=restore):
        await cover.async_restore_last_state(State(cover.entity_id, "unavailable"))
    assert cover.is_opening
    assert cover._motion.anchor is not None
    assert cover.current_cover_position == 50
    cover._freeze_position(cover._motion.anchor)


async def test_nonlinear_target_superseded_while_pre_stop_is_delivered(hass, plant):
    cover = await prepare(hass, plant)
    cover._stop_before_move = True
    entered = asyncio.Event()
    written = hass.loop.create_future()
    sent = []

    async def send(command):
        sent.append(str(command))
        entered.set()
        return written if len(sent) == 1 else None

    plant.gateways[0].send = send
    task = asyncio.create_task(cover.async_set_cover_position(position=70))
    await asyncio.wait_for(entered.wait(), 2)
    await cover.async_stop_cover()
    written.set_result(time.monotonic())
    await asyncio.wait_for(task, 2)
    assert sent == ["*2*0*11##", "*2*0*11##"]
    assert cover._stop_task is None
    assert not cover.is_opening and not cover.is_closing


async def test_nonlinear_target_superseded_while_direction_is_queued(hass, plant):
    cover = await prepare(hass, plant)
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(command):
        entered.set()
        await release.wait()
        return None

    plant.gateways[0].send = send
    task = asyncio.create_task(cover.async_set_cover_position(position=70))
    await asyncio.wait_for(entered.wait(), 2)
    cover.handle_actuator_lock(True)
    release.set()
    await asyncio.wait_for(task, 2)
    assert cover._stop_task is None
    assert cover._motion.anchor is None
    assert cover.current_cover_position == 50
