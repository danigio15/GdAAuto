"""Subaru: le auto con MySubaru Connected Services (STARLINK), negli Stati Uniti e in Canada.

Si entra con l'email e la password dell'app MySubaru. La prima volta Subaru
vuole autorizzare GdAAuto come un nuovo dispositivo: manda un codice (per
email, o per SMS se l'email non c'è) che si scrive nel campo «codice». Il
dispositivo resta ricordato; la password si salva perché la sessione di
Subaru dura al più quattro ore e poi si rientra.

Si legge la carica e l'autonomia elettrica delle ibride plug-in (Crosstrek
Hybrid), il serbatoio e l'autonomia a benzina, i chilometri, le gomme, la
posizione e, con l'abbonamento Security (Companion+ sulle 2026), serrature,
porte, finestrini e ricarica. Con lo stesso abbonamento si comanda: chiudi,
apri, trova (clacson e luci), clima (avvio a distanza) e, sulle plug-in,
l'avvio della ricarica. I comandi vogliono il PIN dell'app.

La Solterra (e le elettriche Subaru nate con Toyota) non usano questo cloud
ma quello dell'app «SOLTERRA CONNECT»: qui non si leggono.

Il protocollo viene da G-Two/subarulink e dall'integrazione ``subaru`` di
Home Assistant (Apache 2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import date
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, NOME, ClienteSubaru, Sessione, autorizza, comandi_di

ACCESSO = ACCESSO_CAMPI

SCELTE_PAESE = (("USA", "United States"), ("CAN", "Canada"))

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("paese", scelte=SCELTE_PAESE, predefinito="USA"),
    Campo("pin", obbligatorio=False, segreto=True),
    Campo("codice", obbligatorio=False),
)

NOTA = {
    "it": (
        "Email e password dell'app MySubaru (Stati Uniti o Canada). La prima volta Subaru ti manda un codice per "
        "autorizzare GdAAuto come nuovo dispositivo: scrivilo nel campo «codice» e conferma di nuovo. La password "
        "resta salvata: Subaru chiude la sessione ogni quattro ore. Il **PIN** è quello dei comandi nell'app: senza, "
        "GdAAuto legge l'auto e non la comanda. Attenzione: dopo pochi PIN sbagliati Subaru blocca l'account. "
        "Le Solterra usano un'altra app e qui non si leggono."
    ),
    "en": (
        "Email and password of the MySubaru app (United States or Canada). The first time, Subaru sends you a code "
        "to authorize GdAAuto as a new device: type it in the «code» field and submit again. The password is kept: "
        "Subaru ends the session every four hours. The **PIN** is the app's command PIN: without it GdAAuto reads "
        "the car but does not command it. Beware: a few wrong PINs lock the account. Solterra uses another app "
        "and is not supported here."
    ),
}

#: I comandi vogliono il PIN dell'app.
VUOLE_PIN = True
#: come l'integrazione di Home Assistant: il cloud si rilegge ogni cinque minuti
INTERVALLO_MINUTI = 5
INTERVALLO_IN_MOTO_SECONDI = 180

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_PAESE = "paese"
CONF_DISPOSITIVO = "dispositivo"

_CHIAVE_HTTP = "gdaauto_subaru_http"


def _sessione_http(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Una sessione HTTP tutta di Subaru, senza barattolo dei cookie.

    I cookie della sessione Subaru li tiene il cliente, per ogni account: in
    una sessione condivisa il barattolo li mescolerebbe fra due account.
    """
    sessione = hass.data.get(_CHIAVE_HTTP)
    if sessione is None or sessione.closed:
        sessione = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
        hass.data[_CHIAVE_HTTP] = sessione
    return sessione


def _nome_dispositivo() -> str:
    return f"GdAAuto (Home Assistant) {date.today():%Y-%m-%d}"


def crea(http: aiohttp.ClientSession, dati: dict[str, Any], server: str | None = None) -> Sessione:
    return Sessione(
        http,
        str(dati[CONF_EMAIL]),
        str(dati[CONF_PASSWORD]),
        str(dati.get(CONF_DISPOSITIVO) or int(time.time())),
        paese=str(dati.get(CONF_PAESE) or "USA"),
        nome_dispositivo=_nome_dispositivo(),
        server=server or dati.get("server"),
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero, autorizza il dispositivo col codice se serve, ed elenca le auto."""
    sessione = crea(_sessione_http(hass), dati)
    await autorizza(sessione, str(dati.get("codice") or ""))
    cliente = ClienteSubaru(sessione)
    vetture = await cliente.vetture()
    email = sessione.email
    da_salvare = {
        CONF_EMAIL: email,
        CONF_PASSWORD: str(dati[CONF_PASSWORD]),
        CONF_PAESE: sessione.paese,
        CONF_DISPOSITIVO: sessione.dispositivo,
    }
    if pin := str(dati.get("pin") or "").strip():
        # Il config flow lo sposta nelle opzioni.
        da_salvare["pin"] = pin
    return Accesso(
        dati=da_salvare,
        vetture=vetture,
        unique_id=f"{ID}_{email.lower()}",
        titolo=f"{NOME} · {email}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteSubaru:
    return ClienteSubaru(crea(_sessione_http(hass), dict(entry.data)))


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteSubaru",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
