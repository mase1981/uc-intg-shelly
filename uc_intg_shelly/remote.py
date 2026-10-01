"""Shelly remote entity - every controllable output as simple commands for activities.

Commands are generated from the device's descriptors, so the remote always
matches the device: ``<OUTPUT>_ON/_OFF/_TOGGLE`` for switches and lights,
``<LIGHT>_BRIGHTER/_DIMMER`` for dimmable lights, ``<COVER>_OPEN/_CLOSE/_STOP``
for covers, one command per button (reboot, virtual buttons ...), media
transport for Wall Displays and ``ALL_ON/ALL_OFF/ALL_TOGGLE`` for all outputs.
"""

import logging
from typing import Any, Awaitable, Callable

from ucapi import StatusCodes, remote
from ucapi.ui import Buttons, Size, UiPage, create_btn_mapping, create_ui_text
from ucapi_framework import RemoteEntity

from uc_intg_shelly.button import press_button
from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.cover import move_cover
from uc_intg_shelly.descriptors import P_BUTTON, P_COVER, P_LIGHT, P_MEDIA, P_SWITCH, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice
from uc_intg_shelly.light import LightControl
from uc_intg_shelly.switch import set_switch

_LOG = logging.getLogger(__name__)

_ROWS = 6
_BRIGHTNESS_STEP = 10


def _short(desc: dict, device_name: str) -> str:
    name = desc["name"]
    if name.startswith(device_name):
        name = name[len(device_name):].strip()
    return name or "Main"


class ShellyRemote(RemoteEntity):
    """Exposes all Shelly outputs as simple commands with UI pages."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice) -> None:
        self._device = device
        self._actions: dict[str, Callable[[], Awaitable[None]]] = {}
        self._outputs: list[tuple[dict, Any]] = []  # (descriptor, LightControl | None)
        dev_name = device_config.name
        descriptors = device_config.descriptors

        output_rows: list[tuple[str, list[tuple[str, str]]]] = []
        cover_rows: list[tuple[str, list[tuple[str, str]]]] = []
        button_cmds: list[tuple[str, str]] = []

        for desc in by_platform(descriptors, P_SWITCH):
            self._outputs.append((desc, None))
        for desc in by_platform(descriptors, P_LIGHT):
            self._outputs.append((desc, LightControl(device, desc)))

        for desc, ctl in self._outputs:
            cmd = desc["uid"].upper()
            label = _short(desc, dev_name)
            if ctl is None:
                self._add(f"{cmd}_ON", lambda d=desc: set_switch(device, d, True))
                self._add(f"{cmd}_OFF", lambda d=desc: set_switch(device, d, False))
                self._add(f"{cmd}_TOGGLE", lambda d=desc: set_switch(device, d, None))
            else:
                self._add(f"{cmd}_ON", ctl.turn_on)
                self._add(f"{cmd}_OFF", ctl.turn_off)
                self._add(f"{cmd}_TOGGLE", ctl.toggle)
                if desc.get("dim"):
                    self._add(f"{cmd}_BRIGHTER", lambda c=ctl: c.step_brightness(_BRIGHTNESS_STEP))
                    self._add(f"{cmd}_DIMMER", lambda c=ctl: c.step_brightness(-_BRIGHTNESS_STEP))
            output_rows.append((label, [("On", f"{cmd}_ON"), ("Off", f"{cmd}_OFF"), ("Toggle", f"{cmd}_TOGGLE")]))

        covers = by_platform(descriptors, P_COVER)
        for desc in covers:
            cmd = desc["uid"].upper()
            for action in ("open", "close", "stop"):
                self._add(f"{cmd}_{action.upper()}", lambda d=desc, a=action: move_cover(device, d, a))
            cover_rows.append((_short(desc, dev_name),
                               [("Open", f"{cmd}_OPEN"), ("Stop", f"{cmd}_STOP"), ("Close", f"{cmd}_CLOSE")]))

        for desc in by_platform(descriptors, P_BUTTON):
            cmd = desc["uid"].upper()
            self._add(cmd, lambda d=desc: press_button(device, d))
            button_cmds.append((_short(desc, dev_name), cmd))

        has_media = bool(by_platform(descriptors, P_MEDIA))
        if has_media:
            for cmd, method in (
                ("PLAY_PAUSE", "Media.MediaPlayer.PlayOrPause"),
                ("STOP", "Media.MediaPlayer.Stop"),
                ("NEXT", "Media.MediaPlayer.Next"),
                ("PREVIOUS", "Media.MediaPlayer.Previous"),
            ):
                self._add(cmd, lambda m=method: device.rpc(m))
            self._add("VOLUME_UP", lambda: self._media_volume(1))
            self._add("VOLUME_DOWN", lambda: self._media_volume(-1))

        if self._outputs:
            self._add("ALL_ON", lambda: self._all(True))
            self._add("ALL_OFF", lambda: self._all(False))
            self._add("ALL_TOGGLE", lambda: self._all(None))

        super().__init__(
            entity_id("remote", device_config.identifier, "remote"),
            f"{dev_name} Remote",
            [remote.Features.ON_OFF, remote.Features.TOGGLE, remote.Features.SEND_CMD],
            {remote.Attributes.STATE: remote.States.UNKNOWN},
            simple_commands=list(self._actions.keys()),
            button_mapping=self._button_mapping(covers, has_media),
            ui_pages=self._ui_pages(output_rows, cover_rows, button_cmds, has_media),
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    def _add(self, command: str, action: Callable[[], Awaitable[Any]]) -> None:
        self._actions[command] = action

    async def _all(self, on: bool | None) -> None:
        if on is None:
            on = not any(self._is_on(desc, ctl) for desc, ctl in self._outputs)
        for desc, ctl in self._outputs:
            try:
                if ctl is None:
                    await set_switch(self._device, desc, on)
                elif on:
                    await ctl.turn_on()
                else:
                    await ctl.turn_off()
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOG.warning("[%s] %s failed: %s", self._device.log_id, desc["uid"], err)

    def _is_on(self, desc: dict, ctl: LightControl | None) -> bool:
        if ctl is None:
            return bool(self._device.value(desc["path"]))
        return ctl.is_on(ctl.status())

    async def _media_volume(self, delta: int) -> None:
        current = self._device.value(["media", "playback", "volume"]) or 0
        await self._device.rpc("Media.SetVolume", {"volume": max(0, min(10, int(current) + delta))})

    def _button_mapping(self, covers: list[dict], has_media: bool) -> list:
        mapping = []
        if self._outputs:
            mapping.append(create_btn_mapping(Buttons.POWER, short="ALL_TOGGLE"))
        if covers:
            cmd = covers[0]["uid"].upper()
            mapping += [
                create_btn_mapping(Buttons.DPAD_UP, short=f"{cmd}_OPEN"),
                create_btn_mapping(Buttons.DPAD_DOWN, short=f"{cmd}_CLOSE"),
                create_btn_mapping(Buttons.DPAD_MIDDLE, short=f"{cmd}_STOP"),
            ]
        else:
            dimmable = next((d for d, c in self._outputs if c is not None and d.get("dim")), None)
            if dimmable is not None:
                cmd = dimmable["uid"].upper()
                mapping += [
                    create_btn_mapping(Buttons.DPAD_UP, short=f"{cmd}_BRIGHTER"),
                    create_btn_mapping(Buttons.DPAD_DOWN, short=f"{cmd}_DIMMER"),
                    create_btn_mapping(Buttons.DPAD_MIDDLE, short=f"{cmd}_TOGGLE"),
                ]
            elif self._outputs:
                mapping.append(
                    create_btn_mapping(Buttons.DPAD_MIDDLE, short=f"{self._outputs[0][0]['uid'].upper()}_TOGGLE")
                )
        if has_media:
            mapping += [
                create_btn_mapping(Buttons.PLAY, short="PLAY_PAUSE"),
                create_btn_mapping(Buttons.NEXT, short="NEXT"),
                create_btn_mapping(Buttons.PREV, short="PREVIOUS"),
                create_btn_mapping(Buttons.VOLUME_UP, short="VOLUME_UP"),
                create_btn_mapping(Buttons.VOLUME_DOWN, short="VOLUME_DOWN"),
            ]
        return mapping

    @staticmethod
    def _ui_pages(
        output_rows: list[tuple[str, list[tuple[str, str]]]],
        cover_rows: list[tuple[str, list[tuple[str, str]]]],
        button_cmds: list[tuple[str, str]],
        has_media: bool,
    ) -> list[UiPage]:
        pages: list[UiPage] = []

        def row_pages(page_id: str, title: str, rows: list[tuple[str, list[tuple[str, str]]]]) -> None:
            for start in range(0, len(rows), _ROWS):
                chunk = rows[start:start + _ROWS]
                items = []
                for y, (label, cmds) in enumerate(chunk):
                    items.append(create_ui_text(label, 0, y))
                    for x, (text, cmd) in enumerate(cmds, start=1):
                        items.append(create_ui_text(text, x, y, cmd=cmd))
                suffix = f" {start // _ROWS + 1}" if len(rows) > _ROWS else ""
                pages.append(UiPage(f"{page_id}{start // _ROWS}", f"{title}{suffix}", grid=Size(4, 6), items=items))

        row_pages("outputs", "Outputs", output_rows)
        row_pages("covers", "Covers", cover_rows)

        if has_media:
            pages.append(UiPage("media", "Media", grid=Size(4, 6), items=[
                create_ui_text("Prev", 0, 0, cmd="PREVIOUS"),
                create_ui_text("Play/Pause", 1, 0, size=Size(2, 1), cmd="PLAY_PAUSE"),
                create_ui_text("Next", 3, 0, cmd="NEXT"),
                create_ui_text("Stop", 1, 1, size=Size(2, 1), cmd="STOP"),
                create_ui_text("Vol -", 0, 2, size=Size(2, 1), cmd="VOLUME_DOWN"),
                create_ui_text("Vol +", 2, 2, size=Size(2, 1), cmd="VOLUME_UP"),
            ]))

        per_page = 2 * _ROWS
        for start in range(0, len(button_cmds), per_page):
            chunk = button_cmds[start:start + per_page]
            items = [
                create_ui_text(label, (i % 2) * 2, i // 2, size=Size(2, 1), cmd=cmd)
                for i, (label, cmd) in enumerate(chunk)
            ]
            suffix = f" {start // per_page + 1}" if len(button_cmds) > per_page else ""
            pages.append(UiPage(f"actions{start // per_page}", f"Actions{suffix}", grid=Size(4, 6), items=items))
        return pages

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({remote.Attributes.STATE: remote.States.UNAVAILABLE})
            return
        any_on = any(self._is_on(desc, ctl) for desc, ctl in self._outputs) if self._outputs else True
        self.update({remote.Attributes.STATE: remote.States.ON if any_on else remote.States.OFF})

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        params = params or {}
        try:
            if cmd_id == remote.Commands.ON:
                await self._all(True)
                return StatusCodes.OK
            if cmd_id == remote.Commands.OFF:
                await self._all(False)
                return StatusCodes.OK
            if cmd_id == remote.Commands.TOGGLE:
                await self._all(None)
                return StatusCodes.OK
            if cmd_id == remote.Commands.SEND_CMD:
                return await self._run(str(params.get("command", "")))
            if cmd_id == remote.Commands.SEND_CMD_SEQUENCE:
                for command in params.get("sequence", []) or []:
                    status = await self._run(str(command))
                    if status != StatusCodes.OK:
                        return status
                return StatusCodes.OK
            return StatusCodes.NOT_IMPLEMENTED
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Remote command error (%s): %s", entity.id, cmd_id, err)
            return StatusCodes.SERVER_ERROR

    async def _run(self, command: str) -> StatusCodes:
        action = self._actions.get(command)
        if action is None:
            return StatusCodes.NOT_IMPLEMENTED
        await action()
        return StatusCodes.OK


def create_remote(device_config: ShellyConfig, device: ShellyDevice) -> list[RemoteEntity]:
    return [ShellyRemote(device_config, device)]
