"""Connection status for the local SmartKnob bridge."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([ConnectionEntity(entry, hass.data[DOMAIN][entry.entry_id])])


class ConnectionEntity(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_name = "Verbindung"

    def __init__(self, entry, runtime) -> None:
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.unique_id}_connection"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.unique_id)})

    @property
    def is_on(self) -> bool:
        return self._runtime.connected

    @property
    def extra_state_attributes(self):
        return {"hinweis": self._runtime.last_error} if self._runtime.last_error else {}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runtime.subscribe(self.async_write_ha_state))
