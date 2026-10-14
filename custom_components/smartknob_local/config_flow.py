"""Discovery, device key and light selection in the HA interface."""
from __future__ import annotations

try:
    import probatio as vol
except ImportError:
    import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_KEY, CONF_LIGHTS, DOMAIN, MAX_LIGHTS
from .protocol import AuthenticationError, Client, ProtocolError, make_app


def connection_error(err):
    code = err.args[0] if err.args else "cannot_connect"
    if code in ("invalid_host", "invalid_port"):
        return "invalid_address"
    if code in ("unsupported_device", "invalid_json", "invalid_response"):
        return "incompatible_firmware"
    if code == "wrong_device":
        return "wrong_device"
    return "cannot_connect"


def selection_schema(default=None):
    return vol.Schema({vol.Required(CONF_LIGHTS, default=default or []): selector.EntitySelector(
        selector.EntitySelectorConfig(filter=[{"domain": "light"}, {"domain": "scene"},
                                               {"domain": "media_player"}], multiple=True, reorder=True))})


def selected_apps(hass, selection):
    if not isinstance(selection, list) or not 1 <= len(selection) <= MAX_LIGHTS or len(set(selection)) != len(selection):
        raise ProtocolError("invalid_selection")
    apps = []
    for entity_id in selection:
        state = hass.states.get(entity_id)
        domain = entity_id.split(".", 1)[0]
        if state is None or domain not in ("light", "scene", "media_player"):
            raise ProtocolError("invalid_selection")
        modes = state.attributes.get("supported_color_modes", []) if domain == "light" else []
        dimmable = bool(set(modes or []) - {"onoff", "unknown"})
        apps.append(make_app(entity_id, state.name, dimmable))
    return apps


class SmartKnobFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._host = ""
        self._port = 80
        self._data = {}
        self._discovered_id = None

    async def async_step_zeroconf(self, discovery_info):
        properties = discovery_info.properties
        if properties.get("protocol") != "1":
            return self.async_abort(reason="unsupported_device")
        self._host, self._port = discovery_info.host, discovery_info.port
        self._discovered_id = properties.get("id")
        if not self._discovered_id:
            return self.async_abort(reason="unsupported_device")
        await self.async_set_unique_id(self._discovered_id)
        # Verify identity with the existing key before updating a saved address.
        existing = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, self._discovered_id)
        if existing:
            if CONF_KEY not in existing.data:
                return self.async_abort(reason="already_configured")
            try:
                client = Client(self._host, existing.data[CONF_KEY], self._port)
                info = await self.hass.async_add_executor_job(client.info)
                if info["id"] != existing.unique_id:
                    return self.async_abort(reason="unsupported_device")
            except ProtocolError:
                return self.async_abort(reason="cannot_connect")
            # DHCP may assign a new address. After the identity was verified
            # with the saved key, reload the active client with that address.
            self._abort_if_unique_id_configured(
                updates={"host": self._host, "port": self._port},
                reload_on_update=True,
            )
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                client = Client(user_input["host"], user_input[CONF_KEY], user_input.get("port", self._port))
                info = await self.hass.async_add_executor_job(client.info)
                if self._discovered_id and info["id"] != self._discovered_id:
                    raise ProtocolError("wrong_device")
                await self.async_set_unique_id(info["id"])
                self._abort_if_unique_id_configured()
                self._data = {"host": client.host, "port": client.port, CONF_KEY: client.key}
                return await self.async_step_lights()
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except ProtocolError as err:
                errors["base"] = connection_error(err)
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("host", default=self._host): str,
            vol.Required("port", default=self._port): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(CONF_KEY): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }), errors=errors)

    async def async_step_lights(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                apps = selected_apps(self.hass, user_input[CONF_LIGHTS])
                return self.async_create_entry(title="SmartKnob " + self.unique_id[-6:], data={**self._data, "apps": apps})
            except ProtocolError:
                errors["base"] = "invalid_selection"
        return self.async_show_form(step_id="lights", data_schema=selection_schema(), errors=errors)

    async def async_step_reauth(self, entry_data):
        self._data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                client = Client(user_input["host"], entry.data[CONF_KEY], user_input["port"])
                info = await self.hass.async_add_executor_job(client.info)
                if info["id"] != entry.unique_id:
                    raise ProtocolError("wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates={"host": client.host, "port": client.port})
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except ProtocolError as err:
                errors["base"] = connection_error(err)
        return self.async_show_form(step_id="reconfigure", data_schema=vol.Schema({
            vol.Required("host", default=entry.data["host"]): str,
            vol.Required("port", default=entry.data.get("port", 80)): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }), errors=errors)

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                client = Client(self._data["host"], user_input[CONF_KEY], self._data.get("port", 80))
                info = await self.hass.async_add_executor_job(client.info)
                entry = self._get_reauth_entry()
                if info["id"] != entry.unique_id:
                    raise ProtocolError("wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_KEY: client.key})
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except ProtocolError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({
            vol.Required(CONF_KEY): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmartKnobOptions()


class SmartKnobOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        apps = self.config_entry.options.get("apps", self.config_entry.data["apps"])
        if user_input is not None:
            try:
                selected = selected_apps(self.hass, user_input[CONF_LIGHTS])
                return self.async_create_entry(title="", data={"apps": selected})
            except ProtocolError:
                errors["base"] = "invalid_selection"
        return self.async_show_form(step_id="init", data_schema=selection_schema([a["entity_id"] for a in apps]), errors=errors)
