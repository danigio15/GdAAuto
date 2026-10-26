"""SEAT e CUPRA: Born, Tavascan, Raval, e le ibride plug-in (Formentor, Leon, Terramar e-HYBRID), dal cloud dell'app.

Il cloud è «OLA» (``ola.prod.code.seat.cloud.vwgroup.com``), lo stesso per
le app My CUPRA e My SEAT, in Europa. Il marchio scelto nel catalogo
(``cupra`` o ``seat``) dice quale app imitare: il suo client OAuth,
l'indirizzo di ritorno (``cupra://`` o ``seat://``) e le intestazioni.

**Come si entra.** Dal browser: GdAAuto apre il login del gruppo Volkswagen
(``identity.vwgroup.io``) per l'app giusta, con una sfida PKCE; chi configura
entra lì con email e password, che GdAAuto non vede. Alla fine il login
rimanda a ``cupra://oauth-callback?code=…`` (o ``seat://…``), che il browser
non sa aprire: quell'indirizzo, o solo il codice, si incolla in GdAAuto.
I token che ne escono ruotano a ogni rinnovo, e si salvano nella voce senza
ricaricarla. Se OLA rifiuta il codice, l'app CUPRA ha una seconda strada:
lo stesso scambio sul login del gruppo, col segreto dell'app (come pycupra).
L'S-PIN dell'app, facoltativo, serve solo a chiudere e aprire.

Si legge tutto quello che il cloud dà (batteria, autonomie, ricarica, limite,
clima, serrature, portiere, finestrini, posizione, chilometri, consumo
medio); si comandano chiusura e apertura (con l'S-PIN), clima, ricarica,
limite di ricarica, clacson e luci, e la destinazione.

**Rischio noto.** Dall'8 giugno 2026 il cloud OLA chiede alle app un gettone
Firebase App Check, che un client non ufficiale non sa produrre: molti
indirizzi possono rispondere 403. GdAAuto lo dice con un messaggio chiaro
(``ErroreApi``), senza chiedere di rientrare. Finché il blocco resta, la
strada sicura è quella di Home Assistant (o il portale EU Data Act).

Le costanti dell'app e gli indirizzi vengono da
tillsteinbach/CarConnectivity-connector-seatcupra (MIT) e da WulfgarW/pycupra
(Apache 2.0): vedi ``cliente.py`` e RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import CodiceSbagliato, ErroreApi, ErroreRete
from .app import APP, AppMarchio
from .cliente import ID, MESSAGGIO_APP_CHECK, NOME, ClienteSeatCupra, comandi_di
from .login import indirizzo_login, leggi_incollato, pkce

_LOGGER = logging.getLogger(__name__)

ACCESSO = ACCESSO_CODICE_BROWSER

CONF_MARCHIO = "marchio"
CONF_PIN = "pin"
CONF_TOKEN = "token"

#: Il marchio arriva dal catalogo; si chiede solo l'S-PIN, facoltativo, prima di aprire il browser.
CAMPI: tuple[Campo, ...] = (Campo(CONF_PIN, obbligatorio=False, segreto=True),)

NOTA = {
    "it": (
        "Si entra con l'account dell'app **My CUPRA** o **My SEAT**: email e password le scrivi sul sito "
        "del gruppo Volkswagen, GdAAuto non le vede.\n\n"
        "**PIN** (facoltativo): è l'S-PIN dell'app, serve solo a chiudere e aprire l'auto. "
        "Puoi lasciarlo vuoto e scriverlo poi nelle opzioni.\n\n"
        "**Cosa fare, meglio da un computer (Chrome, Edge o Firefox):**\n"
        "1. Premi **Invia** qui sotto (anche col PIN vuoto): si apre la pagina con **il link per entrare**. "
        "Tieni aperta questa finestra di GdAAuto fino alla fine.\n"
        "2. Apri il link in una scheda nuova e, **prima di entrare**, premi **F12** "
        "(su Mac Cmd+Opzione+I) e scegli la scheda **Rete** (Network).\n"
        "3. Entra con email e password dell'app e accetta, se te lo chiede.\n"
        "4. Alla fine la pagina resta bianca o dice che non può aprire l'indirizzo: è giusto così. "
        "In **Rete** cerca la riga che contiene `oauth-callback` (scrivilo nel filtro), aprila e copia "
        "l'indirizzo intero, quello che inizia con `cupra://oauth-callback?code=` (o `seat://`).\n"
        "5. Torna su GdAAuto e incollalo, **subito**: il codice vale una volta sola e per pochi minuti. "
        "Va bene anche solo la parte dopo `code=`.\n\n"
        "Se dice che il codice non va: riapri il link della **stessa** finestra di GdAAuto e rifai dal punto 2 "
        "(un link vecchio o di un'altra prova non vale). Dal telefono è difficile, perché Safari e Chrome "
        "non mostrano la scheda Rete: usa un computer.\n\n"
        "Da giugno 2026 il cloud di SEAT e CUPRA può bloccare le app non ufficiali (Firebase App Check). "
        "Se GdAAuto dice «App Check», il login è giusto ma il cloud non risponde: usa l'integrazione di "
        "Home Assistant."
    ),
    "en": (
        "You sign in with your **My CUPRA** or **My SEAT** app account: you type email and password on the "
        "Volkswagen group site, GdAAuto never sees them.\n\n"
        "**PIN** (optional): the app S-PIN, only needed to lock and unlock the car. "
        "You can leave it empty and set it later in the options.\n\n"
        "**What to do, best from a computer (Chrome, Edge or Firefox):**\n"
        "1. Press **Submit** below (even with an empty PIN): the next page has **the sign-in link**. "
        "Keep this GdAAuto window open until the end.\n"
        "2. Open the link in a new tab and, **before signing in**, press **F12** "
        "(Cmd+Option+I on Mac) and pick the **Network** tab.\n"
        "3. Sign in with the app email and password and accept, if asked.\n"
        "4. In the end the page stays blank or says it cannot open the address: that is expected. "
        "In **Network** look for the row containing `oauth-callback` (type it in the filter), open it and copy "
        "the whole address, the one starting with `cupra://oauth-callback?code=` (or `seat://`).\n"
        "5. Go back to GdAAuto and paste it **right away**: the code works only once and for a few minutes. "
        "The part after `code=` alone is fine too.\n\n"
        "If it says the code does not work: open the link of the **same** GdAAuto window again and redo from "
        "step 2 (an old link, or one from another attempt, does not work). From a phone it is hard, because "
        "Safari and Chrome do not show the Network tab: use a computer.\n\n"
        "Since June 2026 the SEAT and CUPRA cloud may block unofficial apps (Firebase App Check). "
        "If GdAAuto says «App Check», the login is right but the cloud does not answer: use the Home "
        "Assistant integration."
    ),
}

VUOLE_PIN = True
#: Una lettura costa sei chiamate (più quattro o cinque lente ogni mezz'ora): piano, il cloud conta.
INTERVALLO_MINUTI = 10
#: In marcia o in carica l'auto manda dati più spesso, ma non ogni minuto.
INTERVALLO_IN_MOTO_SECONDI = 300


def _app(dati: dict[str, Any]) -> AppMarchio:
    marchio = str(dati.get(CONF_MARCHIO) or "cupra").strip().lower()
    if marchio not in APP:
        raise ErroreApi("marchio", marchio, f"Marchio sconosciuto per SEAT · CUPRA: {marchio}")
    return APP[marchio]


async def avvia_browser(_hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Il login del gruppo per l'app del marchio; nello stato il verifier PKCE e lo ``state`` del ritorno."""
    app = _app(dati)
    verifier, sfida = pkce()
    stato = secrets.token_urlsafe(16)
    return Dispositivo(
        link=indirizzo_login(app, sfida, stato, secrets.token_urlsafe(16)),
        codice_utente="",
        stato={"verifier": verifier, "state": stato, CONF_MARCHIO: app.marchio},
    )


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Dall'indirizzo finale (o dal solo codice) ai token, e poi le auto dell'account."""
    app = APP.get(str(dispositivo.stato.get(CONF_MARCHIO) or "")) or _app(dati)
    codice, stato = leggi_incollato(incollato)
    if stato is not None and stato != dispositivo.stato.get("state"):
        raise CodiceSbagliato("Questo indirizzo è di un altro login: riapri il link e riprova")
    cliente = ClienteSeatCupra(async_get_clientsession(hass), app.marchio, lingua=_lingua(hass))
    await cliente.scambia_codice(codice, str(dispositivo.stato["verifier"]))
    vetture = await cliente.vetture()
    if (pin := str(dati.get(CONF_PIN) or "").strip()) and any("chiudi" in comandi_di(v) for v in vetture):
        # Un S-PIN sbagliato si dice adesso, non al primo «chiudi»; un blocco o un guasto no:
        # il PIN è facoltativo, e si riprova al primo comando.
        try:
            await cliente.verifica_pin(pin)
        except (ErroreApi, ErroreRete) as err:
            _LOGGER.debug("S-PIN di %s non provato: %s", app.nome, err)
    salvati = {k: v for k, v in dati.items() if k not in ("codice", CONF_TOKEN)}
    salvati.update({CONF_MARCHIO: app.marchio, CONF_TOKEN: dict(cliente.sessione)})
    nomi = ", ".join(dict.fromkeys(v.modello or v.titolo for v in vetture)) or "nessuna auto"
    chi = str(cliente.sessione.get("chi") or nomi)
    return Accesso(
        dati=salvati,
        vetture=vetture,
        # Lo stesso account può entrare con tutte e due le app: una voce per app.
        unique_id=f"{ID}_{app.marchio}_{cliente.utente.lower()}",
        titolo=f"{app.nome} · {chi}",
    )


def _lingua(hass: HomeAssistant | None) -> str:
    return str(getattr(getattr(hass, "config", None), "language", None) or "en")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteSeatCupra:
    @callback
    def salva(token: dict[str, Any]) -> None:
        # Il refresh token ruota: cambiano i dati, non le opzioni, e non si ricarica.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteSeatCupra(
        async_get_clientsession(hass),
        str(entry.data.get(CONF_MARCHIO) or "cupra"),
        entry.data.get(CONF_TOKEN) or {},
        salva,
        lingua=_lingua(hass),
    )


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "MESSAGGIO_APP_CHECK",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteSeatCupra",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
