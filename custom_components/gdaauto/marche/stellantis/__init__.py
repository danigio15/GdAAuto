"""Stellantis: Peugeot, Citroën, DS, Opel e Vauxhall, dal cloud del gruppo.

Le credenziali dell'app di ogni marchio (``app.json``) vengono da
andreadegiovine/homeassistant-stellantis-vehicles, licenza MIT: vedi
RINGRAZIAMENTI.md. Fiat, Jeep, Alfa Romeo e Lancia usano un altro cloud:
per loro c'è la strada «È già in Home Assistant».
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cliente import APP, ID, MARCHI, NOME, ClienteStellantis, codice_da, comandi_di, indirizzo_login

CAMPI: tuple[dict[str, Any], ...] = ()
VUOLE_PIN = False
INTERVALLO_MINUTI = 5

CONF_APP = "app"
CONF_PAESE = "paese"
CONF_TOKEN = "token"


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteStellantis:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteStellantis(
        async_get_clientsession(hass), entry.data[CONF_APP], entry.data[CONF_PAESE], entry.data[CONF_TOKEN], salva
    )


__all__ = [
    "APP",
    "CAMPI",
    "ID",
    "INTERVALLO_MINUTI",
    "MARCHI",
    "NOME",
    "VUOLE_PIN",
    "ClienteStellantis",
    "codice_da",
    "comandi_di",
    "crea_da_voce",
    "indirizzo_login",
]
