from .logger import _LOGGER

import os

from homeassistant.core import HomeAssistant
from homeassistant.components import panel_custom, frontend

from .const import (
    CUSTOM_COMPONENTS,
    DOMAIN,
    INTEGRATION_FOLDER,
    PANEL_FILENAME,
    PANEL_FOLDER,
    PANEL_ICON,
    PANEL_NAME,
    PANEL_TITLE,
    PANEL_URL,
)


async def async_register_panel(hass: HomeAssistant):
    root_dir = os.path.join(hass.config.path(CUSTOM_COMPONENTS), INTEGRATION_FOLDER)
    panel_dir = os.path.join(root_dir, PANEL_FOLDER)
    panel_file = os.path.join(panel_dir, PANEL_FILENAME)

    hass.http.register_static_path(
        PANEL_URL,
        panel_file,
        cache_headers=False,
    )

    _LOGGER.debug("Registering panel")
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_NAME,
        frontend_url_path=DOMAIN,
        module_url=PANEL_URL,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=True,
        config={},
    )


def async_unregister_panel(hass: HomeAssistant):
    frontend.async_remove_panel(hass, DOMAIN)
    _LOGGER.debug("Unregistering panel")
