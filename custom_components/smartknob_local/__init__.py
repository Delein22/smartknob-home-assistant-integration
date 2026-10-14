"""Control selected Home Assistant entities from the optional local firmware."""
from __future__ import annotations

import asyncio
import logging
import secrets

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import CONF_KEY, DOMAIN, POLL_SECONDS
from .protocol import (
    AuthenticationError, Client, ProtocolError, RestartRequired, SessionExpired, commands, light_state,
    MEDIA_NEXT_TRACK, MEDIA_PAUSE, MEDIA_PLAY, MEDIA_PREVIOUS_TRACK, MEDIA_SELECT_SOURCE,
    MEDIA_TURN_OFF, MEDIA_TURN_ON, MEDIA_VOLUME_MUTE, MEDIA_VOLUME_SET,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = Client(entry.data["host"], entry.data[CONF_KEY], entry.data.get("port", 80))
    try:
        info = await hass.async_add_executor_job(client.info)
        if info["id"] != entry.unique_id:
            raise ConfigEntryAuthFailed("An dieser Adresse antwortet ein anderer Knob.")
        runtime = Runtime(hass, entry, client)
        await runtime.connect()
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed("Geräteschlüssel prüfen.") from err
    except RestartRequired as err:
        raise ConfigEntryNotReady("Leuchten-Auswahl geändert: Knob bitte neu starten.") from err
    except ProtocolError as err:
        raise ConfigEntryNotReady("SmartKnob nicht erreichbar oder Protokoll nicht kompatibel.") from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, info["id"])},
        manufacturer="SeedLabs", name=entry.title, model="SmartKnob Local (experimentell)",
    )
    runtime.task = entry.async_create_background_task(hass, runtime.run(), "SmartKnob local entities")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime and runtime.task:
        runtime.task.cancel()
        try:
            await runtime.task
        except asyncio.CancelledError:
            pass
    return True


class Runtime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Client) -> None:
        self.hass, self.entry, self.client = hass, entry, client
        self.apps = entry.options.get("apps", entry.data["apps"])
        self.task: asyncio.Task | None = None
        self.session = ""
        self.boot = ""
        self.ack = 0
        self.connected = False
        self.last_error: str | None = None
        self._listeners = set()
        self.feedback: dict[str, tuple[int, str]] = {}

    def subscribe(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def set_connection(self, connected: bool, error: str | None = None) -> None:
        if (self.connected, self.last_error) == (connected, error):
            return
        self.connected, self.last_error = connected, error
        for listener in tuple(self._listeners):
            listener()

    async def connect(self) -> None:
        info = await self.hass.async_add_executor_job(self.client.info)
        if info["id"] != self.entry.unique_id:
            raise AuthenticationError("wrong_device")
        self.session = secrets.token_hex(16)
        response = await self.hass.async_add_executor_job(
            self.client.request, "session", {"session": self.session, "apps": self.apps})
        if response.get("boot") != info["boot"]:
            raise SessionExpired("device_rebooted")
        self.boot, self.ack = info["boot"], 0
        self.set_connection(True)

    def snapshot(self) -> list[dict]:
        result = []
        for app in self.apps:
            state = self.hass.states.get(app["entity_id"])
            item = light_state(app, state.state if state else None, state.attributes if state else {})
            if app["app_slug"] == "media_player" and app["app_id"] in self.feedback:
                item["feedback_seq"], item["feedback"] = self.feedback[app["app_id"]]
            result.append(item)
        return result

    async def poll(self) -> None:
        snapshot = self.snapshot()
        response = await self.hass.async_add_executor_job(self.client.request, "exchange", {
            "session": self.session, "ack": self.ack,
            "states": snapshot,
        })
        batch = commands(response, self.apps, self.boot, self.ack)
        received_at = asyncio.get_running_loop().time()
        for seq, service, data in batch:
            # At most once per session, including service errors. No delayed replay.
            self.ack = seq
            if asyncio.get_running_loop().time() - received_at > 2:
                continue
            target = self.hass.states.get(data["entity_id"])
            domain = data["entity_id"].split(".", 1)[0]
            app = next((item for item in self.apps if item["entity_id"] == data["entity_id"]), None)
            if target is None or target.state in ("unknown", "unavailable") or (
                    domain == "light" and target.state not in ("on", "off")):
                if app and app["app_slug"] == "media_player":
                    self.feedback[app["app_id"]] = (seq, "failed")
                continue
            if domain == "media_player":
                features = target.attributes.get("supported_features", 0)
                # Entity feature values may be Home Assistant IntFlag objects.
                features = features if isinstance(features, int) else 0
                required = {
                    "volume_set": MEDIA_VOLUME_SET,
                    "media_play": MEDIA_PLAY,
                    "media_pause": MEDIA_PAUSE,
                    "volume_mute": MEDIA_VOLUME_MUTE,
                    "media_next_track": MEDIA_NEXT_TRACK,
                    "media_previous_track": MEDIA_PREVIOUS_TRACK,
                    "select_source": MEDIA_SELECT_SOURCE,
                    "turn_on": MEDIA_TURN_ON,
                    "turn_off": MEDIA_TURN_OFF,
                }.get(service)
                if service == "media_play_pause":
                    required = MEDIA_PAUSE if target.state == "playing" else MEDIA_PLAY
                if required is not None and not features & required:
                    self.feedback[app["app_id"]] = (seq, "unsupported")
                    continue
            try:
                async with asyncio.timeout(2):
                    await self.hass.services.async_call(domain, service, data, blocking=True)
                if app and app["app_slug"] == "media_player":
                    self.feedback[app["app_id"]] = (seq, "ok")
            except (HomeAssistantError, TimeoutError):
                if app and app["app_slug"] == "media_player":
                    self.feedback[app["app_id"]] = (seq, "failed")
                _LOGGER.warning("SmartKnob: Aktion fehlgeschlagen")

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                await self.poll()
                delay = 1
                await asyncio.sleep(POLL_SECONDS)
            except AuthenticationError:
                self.set_connection(False, "Geräteschlüssel wurde abgelehnt")
                self.entry.async_start_reauth(self.hass)
                return
            except ProtocolError as err:
                reason = "SmartKnob antwortet nicht – Adresse, Stromversorgung und Netzwerk prüfen"
                self.set_connection(False, reason)
                _LOGGER.warning("%s; Verbindung wird automatisch wiederhergestellt", reason)
                # A fresh session discards actions made during an outage.
                while True:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
                    try:
                        await self.connect()
                        break
                    except AuthenticationError:
                        self.set_connection(False, "Geräteschlüssel wurde abgelehnt")
                        self.entry.async_start_reauth(self.hass)
                        return
                    except ProtocolError:
                        continue
