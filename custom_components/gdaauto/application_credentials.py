"""Le credenziali applicative: dove Tesla fa entrare e dove consegna i token.

Sono quelle dell'app sviluppatore Tesla di chi configura (developer.tesla.com),
messe in Home Assistant: Impostazioni → Dispositivi e servizi → Credenziali
applicative. Le altre marche non passano di qui.
"""

from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

from .marche import tesla


async def async_get_authorization_server(_hass: HomeAssistant) -> AuthorizationServer:
    return AuthorizationServer(authorize_url=tesla.AUTORIZZA, token_url=tesla.TOKEN)


async def async_get_description_placeholders(_hass: HomeAssistant) -> dict[str, str]:
    return {
        "developer": "https://developer.tesla.com",
        "redirect": "https://my.home-assistant.io/redirect/oauth",
    }
