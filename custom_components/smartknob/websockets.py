import json

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.data_validator import RequestDataValidator
from homeassistant.components.websocket_api import async_register_command
from homeassistant.core import HomeAssistant, callback

from .const import APP_SLUGS, DOMAIN
from .logger import _LOGGER


async def async_register_websockets(hass):
    """Register websockets."""
    hass.http.register_view(SmartknobAppSlugsView)
    hass.http.register_view(SmartknobKnobsView)
    hass.http.register_view(SmartknobAppsView)


class SmartknobAppSlugsView(HomeAssistantView):
    """View to send appslugs from "backend" to frontend."""

    url = "/api/smartknob/app_slugs"
    name = "api:smartknob:app_slugs"

    async def get(self, request):
        """Get Smartknob AppSlugs."""
        # hass: HomeAssistant = request.app["hass"]
        # coordinator = hass.data[DOMAIN]["coordinator"]

        return self.json({"success": True, "app_slugs": APP_SLUGS})


class SmartknobKnobsView(HomeAssistantView):
    """View to handle Smartknob config requests."""

    url = "/api/smartknob/knobs"
    name = "api:smartknob:knobs"

    async def get(self, request):
        """Get Smartknob config."""
        hass: HomeAssistant = request.app["hass"]
        coordinator = hass.data[DOMAIN]["coordinator"]
        knobs = coordinator.store.async_get_knobs()
        return self.json(
            {"success": True, "knobs": knobs}
        )  # TODO return actual success or error


class SmartknobAppsView(HomeAssistantView):
    """View to handle Smartknob config requests."""

    url = "/api/smartknob/apps"
    name = "api:smartknob:apps"

    @RequestDataValidator(
        vol.Schema(
            {
                vol.Required("mac_address"): str,
                vol.Required("apps"): [
                    {
                        vol.Required("app_id"): str,
                        vol.Required("app_slug_id"): str,
                        vol.Required("entity_id"): str,
                        vol.Required("friendly_name"): str,
                    }
                ],
            }
        )
    )
    async def post(self, request, data: dict):
        """Update config for app."""
        hass: HomeAssistant = request.app["hass"]
        coordinator = hass.data[DOMAIN]["coordinator"]
        if "mac_address" and "apps" in data:
            apps = data.get("apps")

            _LOGGER.error(apps)

            if len(apps) > 1:
                await coordinator.store.async_update_apps(apps)
            await coordinator.store.async_add_app(
                data.get("mac_address"), data.get("apps")[0]
            )

        return self.json({"success": True})  # TODO return actual success or error
