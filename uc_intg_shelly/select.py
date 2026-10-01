"""Shelly select entities - virtual enum components and Gen1 RGB light effects."""

import logging
from typing import Any

from ucapi import StatusCodes, select
from ucapi_framework import SelectEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.descriptors import P_SELECT, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)


class ShellySelect(SelectEntity):
    """One option selector."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        self._desc = desc
        titles: dict[str, str] = desc.get("titles") or {}
        if desc["kind"] == "rpc_enum":
            # Show titles where configured, keep the raw option value for the device.
            self._labels = [titles.get(o, o) for o in desc.get("options", [])]
            self._values: list[Any] = list(desc.get("options", []))
        else:  # gen1_effect: value is the effect index
            self._labels = list(titles.values())
            self._values = [int(k) for k in titles.keys()]
        super().__init__(
            entity_id(P_SELECT, device_config.identifier, desc["uid"]),
            desc["name"],
            {
                select.Attributes.STATE: select.States.UNKNOWN,
                select.Attributes.OPTIONS: self._labels,
                select.Attributes.CURRENT_OPTION: "",
            },
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    def _current_index(self) -> int | None:
        value = self._device.value(self._desc["path"])
        for idx, candidate in enumerate(self._values):
            if str(candidate) == str(value):
                return idx
        return None

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({select.Attributes.STATE: select.States.UNAVAILABLE})
            return
        idx = self._current_index()
        self.update(
            {
                select.Attributes.STATE: select.States.ON,
                select.Attributes.OPTIONS: self._labels,
                select.Attributes.CURRENT_OPTION: self._labels[idx] if idx is not None else "",
            }
        )

    async def _select_index(self, idx: int) -> None:
        value = self._values[idx]
        if self._desc["kind"] == "rpc_enum":
            await self._device.rpc("Enum.Set", {"id": self._desc["cid"], "value": value})
        else:
            await self._device.gen1(self._desc["endpoint"], {"effect": value})

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        if not self._labels:
            return StatusCodes.BAD_REQUEST
        current = self._current_index()
        try:
            match cmd_id:
                case select.Commands.SELECT_OPTION:
                    option = str((params or {}).get("option", ""))
                    if option not in self._labels:
                        return StatusCodes.BAD_REQUEST
                    await self._select_index(self._labels.index(option))
                case select.Commands.SELECT_FIRST:
                    await self._select_index(0)
                case select.Commands.SELECT_LAST:
                    await self._select_index(len(self._labels) - 1)
                case select.Commands.SELECT_NEXT:
                    await self._select_index(0 if current is None else (current + 1) % len(self._labels))
                case select.Commands.SELECT_PREVIOUS:
                    await self._select_index(0 if current is None else (current - 1) % len(self._labels))
                case _:
                    return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Select error (%s): %s", entity.id, cmd_id, err)
            return StatusCodes.SERVER_ERROR


def create_selects(device_config: ShellyConfig, device: ShellyDevice) -> list[SelectEntity]:
    return [ShellySelect(device_config, device, d) for d in by_platform(device_config.descriptors, P_SELECT)]
