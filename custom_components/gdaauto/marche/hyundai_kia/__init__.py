"""Kia, Hyundai e Genesis, dal cloud europeo delle loro app.

Un cloud solo per i tre marchi (Kia Connect, Hyundai Bluelink, Genesis
Connected Services), con le chiavi dell'app di ognuno: il marchio arriva dal
catalogo (``preimpostati``). Si entra con l'email e la password dell'app; il
PIN dell'app, facoltativo, serve ai comandi delle auto recenti.

Vale per le auto europee. Lo stato è quello che l'auto ha mandato l'ultima
volta al cloud: leggerlo non la sveglia e non consuma la batteria a 12 V.
"""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, NOME, ClienteHyundaiKia, comandi_di
from .marchi import MARCHI

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("pin", obbligatorio=False, segreto=True),
)

NOTA = {
    "it": (
        "Email e password dell'app del marchio (Kia Connect, Hyundai Bluelink o Genesis), per le auto europee. "
        "Il PIN dell'app serve ai comandi: puoi lasciarlo vuoto e scriverlo poi nelle opzioni. Il cloud dà i dati "
        "che l'auto ha mandato l'ultima volta: GdAAuto non la sveglia per leggerla. Se il login dice di accettare "
        "le condizioni, entra una volta dall'app e riprova."
    ),
    "en": (
        "Email and password of the brand app (Kia Connect, Hyundai Bluelink or Genesis), for European cars. "
        "The app PIN is needed for commands: you can leave it empty and set it later in the options. The cloud "
        "returns what the car last uploaded: GdAAuto never wakes the car up to read it. If the login asks you to "
        "accept terms, sign in once in the app and try again."
    ),
}

VUOLE_PIN = True
#: Il cloud ha solo quello che l'auto ha mandato: leggerlo spesso non lo rende più fresco.
INTERVALLO_MINUTI = 10
#: In marcia o in carica l'auto manda dati più spesso.
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_TOKEN = "token"


def crea(
    sessione_http: aiohttp.ClientSession, dati: dict[str, Any], lingua: str, salva: Any = None
) -> ClienteHyundaiKia:
    return ClienteHyundaiKia(
        sessione_http,
        email=str(dati["email"]).strip(),
        password=str(dati["password"]),
        marchio=str(dati.get("marchio") or "kia"),
        lingua=lingua,
        pin=dati.get("pin") or None,
        sessione=dati.get(CONF_TOKEN) or None,
        salva=salva,
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero, elenca le auto, e tiene i token per non rifare il login."""
    dati = {k: v for k, v in dati.items() if k != CONF_TOKEN}
    cliente = crea(async_get_clientsession(hass), dati, hass.config.language)
    await cliente.accedi()
    vetture = await cliente.vetture()
    email = str(dati["email"]).strip()
    marchio = cliente.marchio
    return Accesso(
        dati={**dati, "marchio": marchio, CONF_TOKEN: dict(cliente.sessione)},
        vetture=vetture,
        unique_id=f"{ID}_{marchio}_{email.lower()}",
        titolo=f"{MARCHI[marchio].nome} · {email}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteHyundaiKia:
    @callback
    def salva(sessione: dict[str, Any]) -> None:
        # Cambiano solo i dati, non le opzioni: niente ricarica dell'integrazione.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: sessione})

    return crea(async_get_clientsession(hass), dict(entry.data), hass.config.language, salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteHyundaiKia",
    "comandi_di",
    "crea",
    "crea_da_voce",
    "prova_accesso",
]
