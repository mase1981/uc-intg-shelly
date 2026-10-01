"""Shelly media player - the ``media`` component of Shelly Wall Display (XL) devices.

Playback state, volume (device scale 0..10), metadata and artwork come from the
``media`` status block. Radio favourites and stored audio files can be browsed
and played from the Remote, matching the Home Assistant Shelly media player.
"""

import logging
from typing import Any

from ucapi import StatusCodes, media_player
from ucapi.api_definitions import Pagination
from ucapi.media_player import BrowseMediaItem, BrowseOptions, BrowseResults, MediaClass
from ucapi_framework import MediaPlayerEntity

from uc_intg_shelly.config import ShellyConfig
from uc_intg_shelly.const import MAX_ATTR_LEN, MEDIA_VOLUME_MAX
from uc_intg_shelly.descriptors import P_MEDIA, by_platform, entity_id
from uc_intg_shelly.device import ShellyDevice

_LOG = logging.getLogger(__name__)

TYPE_RADIO = "radio"
TYPE_AUDIO = "audio"
_MAX_THUMB = 32768

FEATURES = [
    media_player.Features.PLAY_PAUSE,
    media_player.Features.STOP,
    media_player.Features.NEXT,
    media_player.Features.PREVIOUS,
    media_player.Features.VOLUME,
    media_player.Features.VOLUME_UP_DOWN,
    media_player.Features.MEDIA_TITLE,
    media_player.Features.MEDIA_ARTIST,
    media_player.Features.MEDIA_ALBUM,
    media_player.Features.MEDIA_IMAGE_URL,
    media_player.Features.MEDIA_DURATION,
    media_player.Features.MEDIA_POSITION,
    media_player.Features.BROWSE_MEDIA,
    media_player.Features.PLAY_MEDIA,
]


def _thumb(value: Any) -> str:
    if isinstance(value, str) and (value.startswith("http") or value.startswith("data:")) and len(value) <= _MAX_THUMB:
        return value
    return ""


class ShellyMediaPlayer(MediaPlayerEntity):
    """Wall Display media player."""

    def __init__(self, device_config: ShellyConfig, device: ShellyDevice, desc: dict) -> None:
        self._device = device
        super().__init__(
            entity_id(P_MEDIA, device_config.identifier, desc["uid"]),
            desc["name"],
            FEATURES,
            {
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
                media_player.Attributes.VOLUME: 0,
                media_player.Attributes.MEDIA_TITLE: "",
                media_player.Attributes.MEDIA_ARTIST: "",
                media_player.Attributes.MEDIA_ALBUM: "",
                media_player.Attributes.MEDIA_IMAGE_URL: "",
            },
            device_class=media_player.DeviceClasses.SPEAKER,
            options={media_player.Options.VOLUME_STEPS: MEDIA_VOLUME_MAX}
            if hasattr(media_player.Options, "VOLUME_STEPS") else None,
            cmd_handler=self._handle_command,
        )
        self.subscribe_to_device(device)

    def _playback(self) -> dict[str, Any]:
        value = self._device.value(["media", "playback"])
        return value if isinstance(value, dict) else {}

    def _volume(self) -> int:
        try:
            return int(self._playback().get("volume", 0))
        except (TypeError, ValueError):
            return 0

    async def sync_state(self) -> None:
        if not self._device.available:
            self.update({media_player.Attributes.STATE: media_player.States.UNAVAILABLE})
            return
        playback = self._playback()
        if playback.get("buffering"):
            state = media_player.States.BUFFERING
        elif playback.get("enable"):
            state = media_player.States.PLAYING
        else:
            state = media_player.States.ON
        meta = playback.get("media_meta") or {}
        radio = playback.get("media_type") == "RADIO"
        attrs: dict[str, Any] = {
            media_player.Attributes.STATE: state,
            media_player.Attributes.VOLUME: round(self._volume() * 100 / MEDIA_VOLUME_MAX),
            media_player.Attributes.MEDIA_TITLE: str(meta.get("title") or "")[:MAX_ATTR_LEN],
            media_player.Attributes.MEDIA_ARTIST: "" if radio else str(meta.get("artist") or "")[:MAX_ATTR_LEN],
            media_player.Attributes.MEDIA_ALBUM: "" if radio else str(meta.get("album") or "")[:MAX_ATTR_LEN],
            media_player.Attributes.MEDIA_IMAGE_URL: _thumb(meta.get("thumb")),
        }
        if not radio and isinstance(meta.get("duration"), (int, float)):
            attrs[media_player.Attributes.MEDIA_DURATION] = int(meta["duration"]) // 1000
        if isinstance(meta.get("position"), (int, float)):
            attrs[media_player.Attributes.MEDIA_POSITION] = int(meta["position"]) // 1000
        self.update(attrs)

    async def _set_volume_level(self, level: int) -> None:
        level = max(0, min(MEDIA_VOLUME_MAX, int(level)))
        await self._device.rpc("Media.SetVolume", {"volume": level})

    async def _play_media(self, media_type: str, media_id: str) -> StatusCodes:
        if not str(media_id).isdecimal():
            return StatusCodes.BAD_REQUEST
        if media_type == TYPE_RADIO:
            await self._device.rpc("Media.Radio.PlayFavourite", {"id": int(media_id)})
        elif media_type == TYPE_AUDIO:
            await self._device.rpc("Media.MediaPlayer.Play", {"id": int(media_id)})
        else:
            return StatusCodes.BAD_REQUEST
        return StatusCodes.OK

    async def _handle_command(self, entity: Any, cmd_id: str, params: dict | None = None, *_a: Any) -> StatusCodes:
        params = params or {}
        try:
            match cmd_id:
                case media_player.Commands.PLAY_PAUSE:
                    await self._device.rpc("Media.MediaPlayer.PlayOrPause")
                case media_player.Commands.STOP:
                    await self._device.rpc("Media.MediaPlayer.Stop")
                case media_player.Commands.NEXT:
                    await self._device.rpc("Media.MediaPlayer.Next")
                case media_player.Commands.PREVIOUS:
                    await self._device.rpc("Media.MediaPlayer.Previous")
                case media_player.Commands.VOLUME:
                    await self._set_volume_level(round(float(params.get("volume", 0)) * MEDIA_VOLUME_MAX / 100))
                case media_player.Commands.VOLUME_UP:
                    await self._set_volume_level(self._volume() + 1)
                case media_player.Commands.VOLUME_DOWN:
                    await self._set_volume_level(self._volume() - 1)
                case media_player.Commands.PLAY_MEDIA:
                    return await self._play_media(str(params.get("media_type", "")), str(params.get("media_id", "")))
                case _:
                    return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Media command error (%s): %s", entity.id, cmd_id, err)
            return StatusCodes.SERVER_ERROR

    async def browse(self, options: BrowseOptions) -> BrowseResults | StatusCodes:
        """Browse radio favourites and audio files stored on the Wall Display."""
        paging = options.paging
        media_type = options.media_type or ""
        try:
            if media_type == TYPE_RADIO:
                result = await self._device.rpc("Media.Radio.ListFavourites")
                children = [
                    BrowseMediaItem(
                        media_id=str(s.get("id")),
                        title=str(s.get("name") or f"Station {s.get('id')}")[:MAX_ATTR_LEN],
                        media_class=MediaClass.RADIO,
                        media_type=TYPE_RADIO,
                        can_play=True,
                        thumbnail=_thumb(s.get("icon")) or None,
                    )
                    for s in (result or {}).get("list", [])
                ]
                root = BrowseMediaItem(media_id=TYPE_RADIO, title="Radio stations", media_class=MediaClass.DIRECTORY,
                                       media_type=TYPE_RADIO, can_browse=True)
            elif media_type == TYPE_AUDIO:
                result = await self._device.rpc("Media.List")
                children = [
                    BrowseMediaItem(
                        media_id=str(f.get("id")),
                        title=str(f.get("title") or f"File {f.get('id')}")[:MAX_ATTR_LEN],
                        media_class=MediaClass.MUSIC,
                        media_type=TYPE_AUDIO,
                        can_play=True,
                        thumbnail=_thumb(f.get("preview")) or None,
                    )
                    for f in (result or {}).get("list", [])
                    if f.get("type", "AUDIO") == "AUDIO"
                ]
                root = BrowseMediaItem(media_id=TYPE_AUDIO, title="Audio files", media_class=MediaClass.DIRECTORY,
                                       media_type=TYPE_AUDIO, can_browse=True)
            else:
                children = [
                    BrowseMediaItem(media_id=TYPE_RADIO, title="Radio stations", media_class=MediaClass.DIRECTORY,
                                    media_type=TYPE_RADIO, can_browse=True, thumbnail="icon://uc:radio"),
                    BrowseMediaItem(media_id=TYPE_AUDIO, title="Audio files", media_class=MediaClass.DIRECTORY,
                                    media_type=TYPE_AUDIO, can_browse=True, thumbnail="icon://uc:music"),
                ]
                root = BrowseMediaItem(media_id="root", title="Shelly", media_class=MediaClass.DIRECTORY,
                                       can_browse=True)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Browse failed: %s", self.id, err)
            return StatusCodes.SERVER_ERROR

        page_items = children[paging.offset: paging.offset + paging.limit]
        root.items = page_items
        return BrowseResults(
            media=root,
            pagination=Pagination(page=paging.page, limit=len(page_items), count=len(children)),
        )


def create_media_players(device_config: ShellyConfig, device: ShellyDevice) -> list[MediaPlayerEntity]:
    return [ShellyMediaPlayer(device_config, device, d) for d in by_platform(device_config.descriptors, P_MEDIA)]
