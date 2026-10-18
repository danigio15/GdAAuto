"""Volvo: EX30, EX40/XC40 Recharge, EC40, EX90 e le ibride plug-in, dalle API ufficiali di Volvo Cars.

Volvo apre gratis ai proprietari le sue API (Connected Vehicle v2, Energy v2,
Location v1) dal portale developer.volvocars.com. Chi configura crea lì
un'applicazione, che dà la **chiave VCC**; la «pubblica», che dà **client id**
e **client secret** (con l'indirizzo di ritorno di My Home Assistant e tutti
gli scope); poi entra col suo **Volvo ID** nel browser e incolla qui
l'indirizzo finale, o solo il codice. La password resta fra lui e Volvo.

Si leggono tutte le auto elettriche e plug-in dell'account; si comandano
chiusura, apertura, clima, clacson e luci, quando l'auto li accetta.

Il login è quello dell'integrazione ufficiale ``volvo`` di Home Assistant
(Apache 2.0), con le credenziali dell'applicazione di chi configura: vedi
``cliente.py`` e RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import CodiceSbagliato, ErroreCredenziali
from .cliente import (
    ID,
    NOME,
    REDIRECT_URI,
    ClienteVolvo,
    chi_e,
    comandi_di,
    indirizzo_login,
    leggi_incollato,
    nuovo_verifier,
    scambia_codice,
)

ACCESSO = ACCESSO_CODICE_BROWSER

CONF_API_KEY = "api_key"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_TOKEN = "token"
#: Un indirizzo di ritorno diverso da quello di My Home Assistant, se chi configura ne ha registrato un altro.
CONF_REDIRECT = "redirect_uri"

#: Chiesti prima di aprire il login del Volvo ID.
CAMPI: tuple[Campo, ...] = (
    Campo(CONF_API_KEY, segreto=True),
    Campo(CONF_CLIENT_ID),
    Campo(CONF_CLIENT_SECRET, segreto=True),
)

NOTA = {
    "it": (
        "Su developer.volvocars.com crea un'applicazione («API application»): la sua **chiave VCC** va in "
        "«api_key». Poi premi «Publish», seleziona **tutti** gli scope e aggiungi l'indirizzo di ritorno "
        f"`{REDIRECT_URI}`: avrai **client id** e **client secret**. Entra col tuo Volvo ID nella pagina che "
        "si apre e, alla fine, copia l'indirizzo dalla barra del browser (anche se la pagina dà errore) o solo "
        "il codice. Volvo concede circa 10 000 chiamate al giorno per applicazione: con più auto, meglio "
        "un'applicazione per auto. Il permesso delle applicazioni personali dura un tempo limitato: quando "
        "scade, GdAAuto chiede di rientrare."
    ),
    "en": (
        "On developer.volvocars.com create an “API application”: its **VCC API key** goes in “api_key”. Then "
        "press “Publish”, select **all** scopes and add the redirect URI "
        f"`{REDIRECT_URI}`: you will get a **client id** and a **client secret**. Sign in with your Volvo ID "
        "on the page that opens and, at the end, copy the address from the browser bar (even if the page shows "
        "an error) or just the code. Volvo allows about 10,000 calls a day per application: with several cars, "
        "use one application per car. Personal applications have a limited grant period: when it ends, "
        "GdAAuto asks you to sign in again."
    ),
}

VUOLE_PIN = False
#: Il cloud tiene quello che l'auto manda da sola; una lettura costa sette o otto chiamate.
INTERVALLO_MINUTI = 5
#: In marcia, accesa o in carica ogni due minuti: circa 240 chiamate l'ora, lontano dal tetto di Volvo.
INTERVALLO_IN_MOTO_SECONDI = 120


def _pulito(dati: dict[str, Any], chiave: str) -> str:
    return str(dati.get(chiave) or "").strip()


def _credenziali(dati: dict[str, Any]) -> tuple[str, str, str]:
    api_key, client_id, client_secret = (
        _pulito(dati, CONF_API_KEY),
        _pulito(dati, CONF_CLIENT_ID),
        _pulito(dati, CONF_CLIENT_SECRET),
    )
    if not (api_key and client_id and client_secret):
        raise ErroreCredenziali("Servono la chiave VCC, il client id e il client secret dell'applicazione Volvo")
    return api_key, client_id, client_secret


async def avvia_browser(_hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """La pagina del Volvo ID da aprire; nello stato il verifier PKCE e lo ``state`` per riconoscere il ritorno."""
    _api_key, client_id, _segreto = _credenziali(dati)
    verifier, sfida = nuovo_verifier()
    stato = secrets.token_urlsafe(24)
    ritorno = _pulito(dati, CONF_REDIRECT) or REDIRECT_URI
    return Dispositivo(
        link=indirizzo_login(client_id, ritorno, sfida, stato),
        codice_utente="",
        stato={"verifier": verifier, "state": stato, "redirect_uri": ritorno},
    )


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Dall'indirizzo finale (o dal solo codice) ai token, e poi le auto con la chiave VCC."""
    api_key, client_id, client_secret = _credenziali(dati)
    codice, stato = leggi_incollato(incollato)
    if stato is not None and stato != dispositivo.stato.get("state"):
        raise CodiceSbagliato("Questo indirizzo è di un altro login: riapri il link e riprova")
    http = async_get_clientsession(hass)
    ritorno = str(dispositivo.stato.get("redirect_uri") or REDIRECT_URI)
    token = await scambia_codice(http, client_id, client_secret, codice, str(dispositivo.stato["verifier"]), ritorno)
    chi = chi_e(token)
    token.pop("id_token", None)
    cliente = ClienteVolvo(http, api_key, client_id, client_secret, token)
    vetture = await cliente.vetture()
    account = chi.get("sub") or chi.get("email")
    if not account:
        # Senza id dell'account si riconosce dalle auto: le stesse auto, lo stesso account.
        account = hashlib.sha256(",".join(sorted(v.vin for v in vetture)).encode()).hexdigest()[:16]
    salvati = {k: v for k, v in dati.items() if k not in ("codice", CONF_TOKEN)}
    salvati.update(
        {
            CONF_API_KEY: api_key,
            CONF_CLIENT_ID: client_id,
            CONF_CLIENT_SECRET: client_secret,
            CONF_TOKEN: dict(cliente.token),
        }
    )
    if ritorno != REDIRECT_URI:
        salvati[CONF_REDIRECT] = ritorno
    nomi = ", ".join(dict.fromkeys(v.modello for v in vetture)) or "nessuna auto"
    return Accesso(
        dati=salvati,
        vetture=vetture,
        unique_id=f"{ID}_{account.lower()}",
        titolo=f"{NOME} · {chi.get('email') or nomi}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteVolvo:
    @callback
    def salva(token: dict[str, Any]) -> None:
        # Il refresh token ruota: cambiano i dati, non le opzioni, e non si ricarica.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteVolvo(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
        entry.data.get(CONF_TOKEN) or {},
        salva,
    )


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteVolvo",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
