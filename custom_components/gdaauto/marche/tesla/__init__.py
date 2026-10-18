"""Tesla: l'API ufficiale (Fleet API), con OAuth.

Serve un'app sviluppatore Tesla (developer.tesla.com), registrata nella
regione dell'auto, e le sue credenziali in Home Assistant (Impostazioni →
Dispositivi e servizi → Credenziali applicative). Se usi già l'integrazione
Tesla Fleet di Home Assistant, l'app ce l'hai: va bene la stessa.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_OAUTH_HA, Campo
from .cliente import ID, NOME, SERVER, ClienteTesla, comandi_di

ACCESSO = ACCESSO_OAUTH_HA
CAMPI: tuple[Campo, ...] = ()
VUOLE_PIN = False
#: Ogni lettura costa a chi ha l'app sviluppatore: si legge meno spesso.
INTERVALLO_MINUTI = 10
#: Anche in marcia e in carica, con calma: ogni lettura si paga.
INTERVALLO_IN_MOTO_SECONDI = 120

CONF_PROXY = "proxy"
CONF_PROXY_VERIFICA = "proxy_verifica"

SCOPE = ("openid", "offline_access", "vehicle_device_data", "vehicle_location", "vehicle_cmds", "vehicle_charging_cmds")
AUTORIZZA = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/authorize"
TOKEN = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"


def dal_token(access_token: str) -> dict[str, Any]:
    """Il contenuto del JWT di Tesla, senza verificarne la firma: serve solo a
    sapere chi è (``sub``) e in che regione sta (``ou_code``)."""
    try:
        corpo = access_token.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        return json.loads(base64.urlsafe_b64decode(corpo))
    except (IndexError, ValueError):
        return {}


def regione(access_token: str) -> str:
    codice = str(dal_token(access_token).get("ou_code") or "").lower()
    return codice if codice in SERVER else "eu"


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteTesla:
    implementazione = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
    sessione_oauth = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementazione)

    async def token() -> str:
        await sessione_oauth.async_ensure_token_valid()
        return str(sessione_oauth.token["access_token"])

    return ClienteTesla(
        async_get_clientsession(hass),
        token,
        regione(entry.data["token"]["access_token"]),
        proxy=entry.options.get(CONF_PROXY) or "",
        proxy_verifica=entry.options.get(CONF_PROXY_VERIFICA, True),
    )


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_MINUTI",
    "NOME",
    "VUOLE_PIN",
    "ClienteTesla",
    "comandi_di",
    "crea_da_voce",
]
