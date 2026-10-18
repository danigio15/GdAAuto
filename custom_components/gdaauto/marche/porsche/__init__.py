"""Porsche: Taycan, Macan Electric e le ibride E-Hybrid, dal cloud dell'app My Porsche.

Si entra con l'account Porsche ID, **dal browser**: GdAAuto dà il link del
login dell'app My Porsche, la persona entra sul sito di Porsche (captcha e
passkey compresi) e incolla l'indirizzo finale, o solo il codice. GdAAuto non
vede la password: scambia il codice con i token e poi li rinnova da sola.

Si leggono tutte le auto dell'account con Porsche Connect attivo: batteria,
autonomia, ricarica (stato, potenza, fine prevista, limite), cavo, clima,
serratura, porte, bagagliai e finestrini, gomme, posizione, contachilometri,
freno a mano, e serbatoio e autonomia totale delle ibride. Si comanda quello
che l'auto accetta: chiudere, aprire (col PIN dell'app), clacson e frecce,
clima, ricarica immediata e limite di ricarica.

Il cloud tiene i dati che l'auto gli manda da sola: leggerli non la sveglia.
Indirizzi, client id e nomi dei campi vengono da CJNE/pyporscheconnectapi
(licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import os
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import ErroreCredenziali
from .cliente import ID, NOME, ClientePorsche, codice_da, comandi_di, dal_jwt, indirizzo_browser, nuovo_verificatore

ACCESSO = ACCESSO_CODICE_BROWSER
#: Niente da chiedere prima del browser: il PIN, per aprire, si scrive nelle opzioni.
CAMPI: tuple[Campo, ...] = ()

NOTA = {
    "it": (
        "Entra con l'account My Porsche (Porsche ID); serve Porsche Connect attivo. L'indirizzo finale comincia "
        "con «my-porsche-app://auth0/callback?code=…»: il browser non sa aprirlo, quindi su computer premi F12, "
        "scheda «Console», e copialo dal messaggio «Failed to launch…» (o dalla scheda «Rete»). Il **PIN** dei "
        "comandi dell'app serve solo per aprire l'auto: scrivilo dopo nelle opzioni."
    ),
    "en": (
        "Sign in with your My Porsche account (Porsche ID); Porsche Connect must be active. The final address "
        "starts with “my-porsche-app://auth0/callback?code=…”: the browser cannot open it, so on a computer press "
        "F12, “Console” tab, and copy it from the “Failed to launch…” message (or from the “Network” tab). The "
        "app's command **PIN** is only needed to unlock: add it later in the options."
    ),
}

#: Il PIN dell'app serve per aprire.
VUOLE_PIN = True
#: Il cloud riceve i dati quando l'auto li manda; l'integrazione della comunità legge ogni mezz'ora.
#: Ogni quarto d'ora è prudente, e una lettura non sveglia l'auto.
INTERVALLO_MINUTI = 15
#: In carica ogni cinque minuti: abbastanza per vedere la fine, senza martellare il cloud.
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_TOKEN = "token"
CONF_EMAIL = "email"


async def avvia_browser(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Il link del login di Porsche ID, lo stesso dell'app, con un PKCE nuovo."""
    verificatore, stato = nuovo_verificatore(), os.urandom(16).hex()
    return Dispositivo(
        link=indirizzo_browser(verificatore, stato),
        codice_utente="",
        stato={"verificatore": verificatore, "state": stato},
    )


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Dall'indirizzo finale incollato (o dal solo codice) ai token e alle auto."""
    codice, stato = codice_da(incollato)
    if stato is not None and stato != dispositivo.stato.get("state"):
        raise ErroreCredenziali("L'indirizzo incollato è di un altro login: riapri il link e riprova")
    cliente = ClientePorsche(async_get_clientsession(hass))
    await cliente.scambia_codice(codice, str(dispositivo.stato["verificatore"]))
    vetture = await cliente.vetture()
    # Chi è: l'email dell'id token (minuscola), o l'id di Porsche ID se l'email non c'è.
    utente = cliente.chi or dal_jwt(str(cliente.token.get("access_token") or ""))
    email = str(utente.get("email") or "").strip()
    chi = email.lower() or str(utente.get("sub") or "")
    if not chi:
        raise ErroreCredenziali("Porsche ID non dice di chi è l'account")
    salvati = {k: v for k, v in dati.items() if k != CONF_TOKEN}
    salvati[CONF_TOKEN] = dict(cliente.token)
    if email:
        salvati[CONF_EMAIL] = email
    titolo = f"{NOME} · {email or ', '.join(v.titolo for v in vetture)}"
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{chi}", titolo=titolo)


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClientePorsche:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClientePorsche(async_get_clientsession(hass), entry.data.get(CONF_TOKEN) or {}, salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClientePorsche",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
