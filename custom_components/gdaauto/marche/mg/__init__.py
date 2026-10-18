"""MG (SAIC Motor): MG4, MG5, ZS EV, Marvel R, Cyberster e le altre, dal cloud dell'app MG iSMART.

Si entra con email e password dell'app e la regione del cloud. Il cloud
sveglia l'auto a ogni lettura: la si legge ogni mezz'ora da ferma e ogni
cinque minuti in marcia o in carica, come fa mg-saic-ha. Il protocollo
(firma, cifratura, risposte asincrone con l'event-id) viene da
SAIC-iSmart-API/saic-python-client-ng, licenza MIT: vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import NOME, NOMI_REGIONI, REGIONI, ClienteMG, comandi_di
from .dati import ID

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("regione", scelte=tuple((r, NOMI_REGIONI[r]) for r in REGIONI), predefinito="eu"),
)

NOTA = {
    "it": (
        "Usa un **account secondario** dell'app MG iSMART, con l'auto condivisa dall'account principale: "
        "con lo stesso account l'app sul telefono verrebbe disconnessa. Il cloud sveglia l'auto a ogni "
        "lettura: non scendere sotto i 15 minuti, o la batteria a 12 V ne soffre."
    ),
    "en": (
        "Use a **secondary account** of the MG iSMART app, with the car shared from your main account: "
        "sharing the same account would log out the phone app. The cloud wakes the car on every read: "
        "don't go below 15 minutes, or the 12 V battery will suffer."
    ),
}

#: I comandi del cloud MG non chiedono un PIN.
VUOLE_PIN = False
#: Ogni lettura sveglia l'auto: mezz'ora da ferma…
INTERVALLO_MINUTI = 30
#: …e cinque minuti in marcia o in carica.
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_REGIONE = "regione"
CONF_TOKEN = "token"


def crea(sessione_http: Any, dati: dict[str, Any], salva: Any = None, token: dict[str, Any] | None = None) -> ClienteMG:
    return ClienteMG(
        sessione_http,
        email=str(dati[CONF_EMAIL]),
        password=str(dati[CONF_PASSWORD]),
        regione=str(dati.get(CONF_REGIONE) or "eu"),
        token=token,
        salva=salva,
        base_url=dati.get("base_url") or None,
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero ed elenca le auto; il token si tiene per non rientrare subito."""
    dati = {**dati, CONF_EMAIL: str(dati[CONF_EMAIL]).strip(), CONF_REGIONE: dati.get(CONF_REGIONE) or "eu"}
    cliente = crea(async_get_clientsession(hass), dati)
    await cliente.accedi()
    vetture = await cliente.vetture()
    email = dati[CONF_EMAIL]
    return Accesso(
        dati={**dati, CONF_TOKEN: cliente.token},
        vetture=vetture,
        unique_id=f"{ID}_{email.lower()}",
        titolo=f"{NOME} · {email}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteMG:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return crea(async_get_clientsession(hass), dict(entry.data), salva, entry.data.get(CONF_TOKEN))


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteMG",
    "comandi_di",
    "crea",
    "crea_da_voce",
    "prova_accesso",
]
