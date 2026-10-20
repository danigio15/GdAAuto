"""Xpeng: G6, G9, P7 e le altre europee, da Enode.

Xpeng non dà un'API ai proprietari, e la sua app si legge solo con un
certificato estratto dall'APK e un login con captcha: GdAAuto non lo fa. Le
Xpeng si leggono da **Enode** (enode.com), un servizio con un'API pubblica:
chi configura crea un'applicazione nel portale sviluppatori di Enode, chiede
l'accesso «production» (Enode lo concede su richiesta, per un numero limitato
di auto), e scrive qui il client ID e il client secret. Poi GdAAuto apre il
collegamento di Enode: nel browser, sul sito di Enode, si entra con l'account
Xpeng e si autorizza l'auto; alla fine si incolla l'indirizzo dove arriva il
browser (o anche solo «fatto»).

Si legge carica, autonomia, limite di carica, capacità della batteria, cavo,
stato, potenza e tempo della ricarica, posizione (Xpeng la manda quando l'auto
si spegne) e, se Xpeng lo manda, il contachilometri. Si comanda la ricarica
(avvia e ferma), se Enode dice che l'auto la accetta.

Il protocollo viene da mnordseth/xpeng-homeassistant (licenza MIT) e dalla
documentazione pubblica di Enode: vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import ErroreCredenziali
from . import cliente as _cliente
from .cliente import ID, NOME, ClienteXpeng, comandi_di, leggi_ritorno, ritorno_con, utente_di

ACCESSO = ACCESSO_CODICE_BROWSER

CAMPI = (
    Campo("client_id"),
    Campo("client_secret", segreto=True),
)

NOTA = {
    "it": (
        "Le Xpeng si leggono da Enode (enode.com): crea un'applicazione nel portale sviluppatori di Enode, chiedi "
        "a Enode l'accesso «production» e scrivi qui il suo client ID e client secret. Poi apri il link: entri, "
        "sul sito di Enode, con l'account Xpeng e autorizzi l'auto. Alla fine il browser arriva su "
        "home-assistant.io: copia quell'indirizzo e incollalo (va bene anche solo «fatto»)."
    ),
    "en": (
        "Xpeng cars are read through Enode (enode.com): create an application in Enode's developer portal, ask "
        "Enode for production access and enter its client ID and client secret here. Then open the link: on "
        "Enode's site, sign in with your Xpeng account and authorise the car. At the end the browser lands on "
        "home-assistant.io: copy that address and paste it (just «done» works too)."
    ),
}

VUOLE_PIN = False
#: Enode tiene i dati aggiornati da solo; una lettura ogni cinque minuti è una richiesta per auto.
INTERVALLO_MINUTI = 5
#: In carica ogni tre minuti: Enode stesso non aggiorna più spesso di così.
INTERVALLO_IN_MOTO_SECONDI = 180

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_UTENTE = "utente_enode"


def _cliente_da(hass: HomeAssistant, dati: dict[str, Any]) -> ClienteXpeng:
    return ClienteXpeng(
        async_get_clientsession(hass),
        str(dati[CONF_CLIENT_ID]),
        str(dati[CONF_CLIENT_SECRET]),
        oauth=_cliente.OAUTH,
        api=_cliente.API,
    )


async def avvia_browser(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Una sessione di collegamento di Enode: il link per autorizzare l'auto con l'account Xpeng."""
    stato = secrets.token_urlsafe(12)
    utente = utente_di(str(dati[CONF_CLIENT_ID]))
    link = await _cliente_da(hass, dati).collega(utente, ritorno_con(stato, _cliente.RITORNO))
    return Dispositivo(link=link, codice_utente="", stato={"state": stato, "utente": utente})


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Il collegamento è fatto: si guarda quali auto ha adesso l'applicazione Enode."""
    stato, errore = leggi_ritorno(incollato)
    if errore:
        raise ErroreCredenziali(f"Enode dice che il collegamento non è riuscito: {errore}")
    if stato and stato != dispositivo.stato.get("state"):
        raise ErroreCredenziali("Questo indirizzo non è del collegamento appena aperto: riapri il link e riprova")
    cliente = _cliente_da(hass, dati)
    vetture = await cliente.vetture()
    client_id = str(dati[CONF_CLIENT_ID]).strip()
    salvati = {
        CONF_CLIENT_ID: client_id,
        CONF_CLIENT_SECRET: str(dati[CONF_CLIENT_SECRET]).strip(),
        CONF_UTENTE: dispositivo.stato.get("utente") or utente_di(client_id),
    }
    return Accesso(
        dati=salvati, vetture=vetture, unique_id=f"{ID}_{client_id.lower()}", titolo=f"{NOME} · Enode {client_id[:8]}"
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteXpeng:
    return _cliente_da(hass, dict(entry.data))


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteXpeng",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
