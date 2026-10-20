"""Škoda: Enyaq, Elroq e le plug-in iV, dal cloud dell'app MyŠkoda.

Il cloud è quello dell'app MyŠkoda, ``mysmob.api.connect.skoda-auto.cz``,
col login del gruppo Volkswagen su ``identity.vwgroup.io``. Vale per le auto
vendute in Europa: Enyaq ed Enyaq Coupé, Elroq, Citigo-e iV e le ibride
plug-in (Superb iV, Octavia iV, Kodiaq iV). Le Škoda a benzina o diesel
dell'account non si aggiungono.

Il login si fa nel browser di chi configura (vedi ``accesso.py``): GdAAuto
prepara il link col PKCE, e scambia l'indirizzo finale incollato per i token,
che poi rinnova da sola. La password non passa mai da GdAAuto. L'S-PIN
dell'app, facoltativo, serve solo a chiudere e aprire.

Lo stato è quello che l'auto ha mandato l'ultima volta al cloud: leggerlo non
la sveglia.
"""

from __future__ import annotations

import secrets
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import ErroreApi, ErroreCredenziali, ErroreRete
from . import accesso
from .cliente import ID, NOME, ClienteSkoda, comandi_di

ACCESSO = ACCESSO_CODICE_BROWSER

#: Prima del browser si chiede solo l'S-PIN, che si può lasciare vuoto.
CAMPI = (Campo("pin", obbligatorio=False, segreto=True),)

NOTA = {
    "it": (
        "Entra con l'account Škoda (quello dell'app MyŠkoda), per le auto europee; se il sito chiede di "
        "accettare le condizioni d'uso o il consenso al marketing, rispondi. Il browser si ferma su un "
        "indirizzo che comincia con **myskoda://redirect/login/**: se la barra degli indirizzi non lo mostra, "
        "lo trovi negli strumenti per sviluppatori (F12, scheda Console o Rete). Copialo tutto. Il **PIN** è "
        "l'S-PIN dell'app: serve solo a chiudere e aprire, e puoi scriverlo poi nelle opzioni. GdAAuto legge "
        "quello che l'auto ha mandato al cloud, senza svegliarla."
    ),
    "en": (
        "Sign in with your Škoda account (the one of the MyŠkoda app), for European cars; if the site asks you "
        "to accept the terms of use or the marketing consent, answer. The browser stops at an address starting "
        "with **myskoda://redirect/login/**: if the address bar does not show it, find it in the developer "
        "tools (F12, Console or Network tab). Copy all of it. The **PIN** is the app S-PIN: it is only needed "
        "to lock and unlock, and you can set it later in the options. GdAAuto reads what the car last sent to "
        "the cloud, without waking it up."
    ),
}

VUOLE_PIN = True
#: Ogni lettura sono sette chiamate, e il cloud conta quelle di ogni account: piano.
INTERVALLO_MINUTI = 15
#: In marcia o in carica l'auto manda dati più spesso, ma non ogni minuto.
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_TOKEN = "token"


def crea(
    sessione_http: aiohttp.ClientSession, dati: dict[str, Any], salva: Any = None, fuso: str = "UTC"
) -> ClienteSkoda:
    return ClienteSkoda(sessione_http, dati.get(CONF_TOKEN) or None, salva, fuso=fuso)


async def avvia_browser(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Il link del login col PKCE; il ``code_verifier`` resta nello stato, fuori dal link."""
    verifier, sfida = accesso.coppia_pkce()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(16)
    return Dispositivo(
        link=accesso.indirizzo_login(sfida, state, nonce),
        codice_utente="",
        stato={"verifier": verifier, "state": state, "nonce": nonce},
    )


async def _chi(cliente: ClienteSkoda) -> tuple[str, str]:
    """L'identificativo stabile dell'account e l'email: dall'account, o dai token se l'account non risponde."""
    try:
        utente = await cliente.utente()
    except (ErroreApi, ErroreRete):
        utente = {}
    if utente.get("id"):
        return str(utente["id"]), str(utente.get("email") or "")
    for chiave in ("id_token", "access_token"):
        contenuto = accesso.dal_jwt(cliente.sessione.get(chiave))
        if contenuto.get("sub"):
            return str(contenuto["sub"]), str(contenuto.get("email") or "")
    return "", ""


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """L'indirizzo finale (o il solo codice) diventa i token; poi le auto, e l'S-PIN se c'è."""
    valori = accesso.codice_da(incollato)
    if valori.get("state") and valori["state"] != dispositivo.stato.get("state"):
        raise ErroreCredenziali("L'indirizzo incollato è di un altro login: riapri il link e riprova")
    sessione_http = async_get_clientsession(hass)
    token = await accesso.TokenSkoda(sessione_http).scambia(valori, str(dispositivo.stato["verifier"]))
    dati = {k: v for k, v in dati.items() if k != CONF_TOKEN}
    cliente = crea(sessione_http, dati)
    cliente.usa(token)
    vetture = await cliente.vetture()
    if (pin := str(dati.get("pin") or "").strip()) and any("chiudi" in comandi_di(v) for v in vetture):
        # Un S-PIN sbagliato si dice adesso, non al primo «chiudi».
        await cliente.verifica_pin(pin)
    utente, email = await _chi(cliente)
    if not utente:
        raise ErroreCredenziali("Il token Škoda non dice di quale account è: rifai il login")
    titolo = email or ", ".join(v.titolo for v in vetture) or utente[:8]
    return Accesso(
        dati={**dati, CONF_TOKEN: dict(cliente.sessione)},
        vetture=vetture,
        unique_id=f"{ID}_{utente.lower()}",
        titolo=f"{NOME} · {titolo}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteSkoda:
    @callback
    def salva(token: dict[str, Any]) -> None:
        # Cambiano solo i dati, non le opzioni: niente ricarica dell'integrazione.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return crea(async_get_clientsession(hass), dict(entry.data), salva, str(hass.config.time_zone or "UTC"))


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteSkoda",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea",
    "crea_da_voce",
]
