"""NIO: ET5, ET5 Touring, ET7, EL6, EL7, EL8 in Europa, dall'API ufficiale di NIO.

NIO apre i dati ai proprietari europei con la Open Telematics API. Serve una
«Personal Application», che si crea gratis nella console di NIO
(``open-eu.nio.com/console``) con il login a codice (Authorization Code) e un
solo indirizzo di ritorno: ``https://my.home-assistant.io/redirect/oauth``.
Il client ID e il client secret di quella applicazione, e la VIN dell'auto
(l'API non elenca le auto), sono quello che si scrive qui; poi si entra con
l'account NIO nel browser, sul sito di NIO, e si incolla l'indirizzo dove il
browser arriva. GdAAuto fa solo lo scambio del codice e il rinnovo dei token.

Si legge carica, autonomia, limite di carica, chilometri, velocità, tensione
e corrente della batteria, posizione, serratura (e, dove NIO li manda,
portiere, bagagliaio e finestrini), temperatura esterna e della batteria.
L'API è in sola lettura: questa marca non comanda nulla. Le risorse che NIO
non concede a quell'auto restano vuote.

Il protocollo viene da nicolasvangeluwe/nio-home-assistant (licenza MIT):
vedi RINGRAZIAMENTI.md. Le app cinesi di NIO (le integrazioni ha-nio) si
leggono solo intercettando l'app: qui non si usano.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import ErroreCredenziali
from . import cliente as _cliente
from .cliente import ID, NOME, RITORNO, ClienteNio, comandi_di, leggi_codice, pkce, scambia_codice

ACCESSO = ACCESSO_CODICE_BROWSER

CAMPI = (
    Campo("client_id"),
    Campo("client_secret", segreto=True),
    Campo("vin"),
)

NOTA = {
    "it": (
        "Crea una Personal Application nella console di NIO (open-eu.nio.com/console), con il login a codice e "
        f"come unico indirizzo di ritorno {RITORNO}. Scrivi qui il suo client ID, il client secret e la VIN "
        "dell'auto. Poi entra con l'account NIO nel browser: alla fine copia l'indirizzo dove arriva il browser "
        "(contiene «code=») e incollalo. L'API di NIO è in sola lettura: niente comandi."
    ),
    "en": (
        "Create a Personal Application in the NIO console (open-eu.nio.com/console), with Authorization Code "
        f"login and {RITORNO} as its only redirect URI. Enter its client ID, client secret and the car's VIN "
        "here. Then sign in with your NIO account in the browser: at the end copy the address the browser lands "
        "on (it contains «code=») and paste it. NIO's API is read-only: no commands."
    ),
}

VUOLE_PIN = False
SENZA_COMANDI = True
#: Tre letture per giro, una ogni 15 secondi: ogni cinque minuti la quota della Personal Application resta larga.
INTERVALLO_MINUTI = 5
#: E in marcia o in carica ogni due minuti.
INTERVALLO_IN_MOTO_SECONDI = 120

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_VIN = "vin"
CONF_TOKEN = "token"
CONF_MEMORIA = "memoria"

_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def _vin(dati: dict[str, Any]) -> str:
    vin = str(dati.get(CONF_VIN) or "").strip().upper()
    if not _VIN.fullmatch(vin):
        raise ErroreCredenziali("La VIN deve avere 17 caratteri (lettere e numeri, senza I, O e Q)")
    return vin


async def avvia_browser(_hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Il link del login NIO, con PKCE: il verifier e lo state restano per lo scambio."""
    verifier, challenge = pkce()
    stato = secrets.token_urlsafe(16)
    link = _cliente.link_di_accesso(str(dati[CONF_CLIENT_ID]).strip(), challenge, stato, autorizza=_cliente.AUTORIZZA)
    return Dispositivo(link=link, codice_utente="", stato={"verifier": verifier, "state": stato})


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Il codice (o l'indirizzo finale) → i token, e la prova che l'auto si legge."""
    codice, stato = leggi_codice(incollato)
    if not codice:
        raise ErroreCredenziali("Nell'indirizzo incollato non c'è il codice di NIO («code=»)")
    if stato and stato != dispositivo.stato.get("state"):
        raise ErroreCredenziali("Questo codice non è del login appena aperto: riapri il link e riprova")
    client_id = str(dati[CONF_CLIENT_ID]).strip()
    segreto = str(dati[CONF_CLIENT_SECRET]).strip()
    vin = _vin(dati)
    http = async_get_clientsession(hass)
    token = await scambia_codice(
        http, client_id, segreto, codice, str(dispositivo.stato["verifier"]), url_token=_cliente.URL_TOKEN
    )
    cliente = ClienteNio(http, client_id, segreto, vin, token, api=_cliente.API, url_token=_cliente.URL_TOKEN)
    vetture = await cliente.vetture()
    salvati = {CONF_CLIENT_ID: client_id, CONF_CLIENT_SECRET: segreto, CONF_VIN: vin, CONF_TOKEN: dict(cliente.token)}
    # L'API non dice di chi è l'account: una voce per auto, riconosciuta dalla VIN.
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{vin.lower()}", titolo=f"{NOME} · {vin}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteNio:
    @callback
    def salva(aggiornamento: dict[str, Any]) -> None:
        # Il token rinnovato, o le ultime letture buone: la voce si aggiorna senza ricaricarsi.
        hass.config_entries.async_update_entry(entry, data={**entry.data, **aggiornamento})

    return ClienteNio(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
        entry.data[CONF_VIN],
        dict(entry.data.get(CONF_TOKEN) or {}),
        salva,
        memoria=dict(entry.data.get(CONF_MEMORIA) or {}),
        api=_cliente.API,
        url_token=_cliente.URL_TOKEN,
    )


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "SENZA_COMANDI",
    "VUOLE_PIN",
    "ClienteNio",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
