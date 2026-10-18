"""Ford: Mustang Mach-E, Explorer EV, Capri EV, Puma Gen-E, E-Transit, Kuga PHEV, dal cloud FordPass.

Dal 2024 il login di Ford si fa solo nel browser: chi configura apre il
link, entra col suo account FordPass, e alla fine il sito rimanda all'app
(``fordapp://userauthorized/?code=…``). Nel browser del computer quella
pagina resta a caricare: l'indirizzo si copia dagli strumenti per
sviluppatori (scheda Rete) e si incolla in Home Assistant, intero o solo il
codice. Nessuna password passa da GdAAuto.

Il refresh token di Ford ruota a ogni rinnovo: ``crea_da_voce`` dà al
cliente una ``salva`` che lo conserva nella configurazione senza ricaricare.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import ErroreCredenziali
from .cliente import (
    ID,
    NOME,
    ClienteFord,
    Indirizzi,
    codice_da,
    comandi_di,
    indirizzo_login,
    nuovo_verifier,
)
from .regioni import PREDEFINITA, REGIONI, regione_di

ACCESSO = ACCESSO_CODICE_BROWSER
CAMPI = (Campo("paese", scelte=tuple((k, r.nome) for k, r in REGIONI.items()), predefinito=PREDEFINITA),)
VUOLE_PIN = False
#: Leggere apre il WebSocket della telemetria per pochi secondi e non sveglia l'auto;
#: Ford però blocca gli account che interrogano troppo: da ferma ogni cinque minuti.
INTERVALLO_MINUTI = 5
INTERVALLO_IN_MOTO_SECONDI = 60

CONF_PAESE = "paese"
CONF_TOKEN = "token"

NOTA = {
    "it": (
        "Meglio un account FordPass **dedicato**, con l'auto condivisa dall'account principale: Ford può "
        "bloccare un account che il suo cloud vede interrogare spesso. Apri il link in un browser da "
        "computer con gli strumenti per sviluppatori aperti sulla scheda **Rete**, ed entra. Alla fine la "
        "pagina resta a caricare: è normale. Nella scheda Rete copia l'ultimo indirizzo, quello che "
        "comincia con `fordapp://userauthorized/?code=`, e incollalo qui (va bene anche solo il codice). "
        "Il codice vale pochi minuti."
    ),
    "en": (
        "A **dedicated** FordPass account, with the car shared from your main account, is recommended: Ford "
        "may block an account its cloud sees polling often. Open the link in a desktop browser with the "
        "developer tools open on the **Network** tab, and sign in. At the end the page keeps loading: that "
        "is expected. In the Network tab copy the last address, the one starting with "
        "`fordapp://userauthorized/?code=`, and paste it here (the code alone works too). The code is only "
        "valid for a few minutes."
    ),
}


async def avvia_browser(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Il link del login, con un verifier PKCE nuovo che resta nello stato."""
    regione = regione_di(dati.get(CONF_PAESE), dati.get("marchio"))
    verifier = nuovo_verifier()
    return Dispositivo(
        link=indirizzo_login(regione, verifier),
        codice_utente="",
        stato={"verifier": verifier, CONF_PAESE: dati.get(CONF_PAESE) or PREDEFINITA},
    )


async def completa_browser(
    hass: HomeAssistant,
    dati: dict[str, Any],
    dispositivo: Dispositivo,
    incollato: str,
    *,
    indirizzi: Indirizzi | None = None,
) -> Accesso:
    """Dal codice (o dall'indirizzo finale) ai token e alle auto dell'account."""
    paese = dispositivo.stato.get(CONF_PAESE) or dati.get(CONF_PAESE) or PREDEFINITA
    regione = regione_di(paese, dati.get("marchio"))
    verifier = dispositivo.stato.get("verifier")
    if not verifier:
        raise ErroreCredenziali("Il login è da ricominciare: manca il verifier")
    cliente = ClienteFord(async_get_clientsession(hass), regione, {}, indirizzi=indirizzi)
    token = await cliente.scambia_codice(codice_da(incollato), verifier)
    vetture = await cliente.vetture()
    # Il token può essere ruotato leggendo le auto: si salva l'ultimo.
    token = dict(cliente.token)
    utente = token.get("utente") or {}
    if utente.get("id"):
        unique_id = f"{ID}_{str(utente['id']).lower()}"
    elif vetture:
        unique_id = f"{ID}_{min(v.vin for v in vetture).lower()}"
    else:
        raise ErroreCredenziali("Non si capisce di chi è l'account Ford, e non ha auto")
    if utente.get("email"):
        titolo = f"{NOME} · {utente['email']}"
    elif len(vetture) == 1:
        titolo = f"{NOME} · {vetture[0].titolo}"
    else:
        titolo = f"{NOME} · {len(vetture)} auto"
    salvare = {k: v for k, v in dati.items() if k not in (CONF_TOKEN, CONF_PAESE)}
    return Accesso({**salvare, CONF_PAESE: paese, CONF_TOKEN: token}, vetture, unique_id, titolo)


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteFord:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteFord(
        async_get_clientsession(hass),
        regione_di(entry.data.get(CONF_PAESE), entry.data.get("marchio")),
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
    "ClienteFord",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
