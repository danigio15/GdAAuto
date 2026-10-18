"""Uconnect: Fiat, Abarth, Jeep, Alfa Romeo, Lancia e Maserati, dal cloud ex-FCA.

È il cloud dell'app del marchio (My Uconnect, Jeep, Alfa Romeo…), lo stesso
per le elettriche (Fiat 500e e 600e, Jeep Avenger, Alfa Romeo Junior, Lancia
Ypsilon) e per le ibride plug-in (Jeep 4xe). Si entra con l'email e la
password dell'app; il PIN dell'app, facoltativo, serve solo ai comandi.

Il marchio arriva dal catalogo (``preimpostati``), la regione la sceglie chi
configura: Europa, America del Nord o Asia. Indirizzi e chiavi pubbliche
delle app vengono da py-uconnect (Apache 2.0) ed evcc (MIT): vedi
RINGRAZIAMENTI.md e ``regioni.py``.
"""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, NOME, ClienteUconnect, comandi_di
from .regioni import NOMI_MARCHI, REGIONE_PREDEFINITA, SCELTE_REGIONE

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("regione", scelte=SCELTE_REGIONE, predefinito=REGIONE_PREDEFINITA),
    Campo("pin", obbligatorio=False, segreto=True),
)

NOTA = {
    "it": (
        "Usa l'email e la password dell'app del marchio (My Uconnect, Jeep, Alfa Romeo…). Il PIN dell'app serve "
        "solo per chiudere, aprire, accendere il clima e avviare la ricarica: puoi lasciarlo vuoto e scriverlo poi "
        "nelle opzioni. Il cloud dà i dati che l'auto ha mandato l'ultima volta: GdAAuto non la sveglia per leggerla. "
        "Se il login chiede un captcha, entra una volta dal sito o dall'app e riprova."
    ),
    "en": (
        "Use the email and password of the brand app (My Uconnect, Jeep, Alfa Romeo…). The app PIN is only needed "
        "to lock, unlock, start climate and start charging: you can leave it empty and set it later in the options. "
        "The cloud returns what the car last uploaded: GdAAuto never wakes the car up to read it. "
        "If the login asks for a captcha, sign in once on the website or in the app and try again."
    ),
}

VUOLE_PIN = True
#: Il cloud ha solo quello che l'auto ha mandato: leggerlo spesso non lo rende più fresco.
INTERVALLO_MINUTI = 10
#: In marcia o in carica l'auto manda dati più spesso, ma non ogni minuto.
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_TOKEN = "token"


def crea(sessione_http: aiohttp.ClientSession, dati: dict[str, Any], salva: Any = None) -> ClienteUconnect:
    return ClienteUconnect(
        sessione_http,
        email=str(dati["email"]).strip(),
        password=str(dati["password"]),
        marchio=str(dati.get("marchio") or "fiat"),
        regione=str(dati.get("regione") or REGIONE_PREDEFINITA),
        pin=dati.get("pin") or None,
        sessione=dati.get(CONF_TOKEN) or None,
        salva=salva,
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero, elenca le auto, e tiene il login di Gigya per non rifarlo."""
    dati = {k: v for k, v in dati.items() if k != CONF_TOKEN}
    cliente = crea(async_get_clientsession(hass), dati)
    await cliente.accedi()
    vetture = await cliente.vetture()
    email = str(dati["email"]).strip()
    marchio = str(dati.get("marchio") or "fiat")
    regione = str(dati.get("regione") or REGIONE_PREDEFINITA)
    return Accesso(
        dati={**dati, "regione": regione, CONF_TOKEN: dict(cliente.sessione)},
        vetture=vetture,
        unique_id=f"{ID}_{marchio}_{regione}_{email.lower()}",
        titolo=f"{NOMI_MARCHI.get(marchio, NOME)} · {email}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteUconnect:
    @callback
    def salva(sessione: dict[str, Any]) -> None:
        # Cambiano solo i dati, non le opzioni: niente ricarica dell'integrazione.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: sessione})

    return crea(async_get_clientsession(hass), dict(entry.data), salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteUconnect",
    "comandi_di",
    "crea",
    "crea_da_voce",
    "prova_accesso",
]
