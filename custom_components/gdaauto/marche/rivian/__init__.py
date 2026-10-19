"""Rivian: R1T, R1S e R2, dal cloud GraphQL dell'app Rivian (Stati Uniti e Canada).

Si entra con l'email e la password dell'app; se l'account ha la verifica in
due passaggi, Rivian manda un codice per SMS che si scrive nel campo
«codice». La password serve solo ora: GdAAuto tiene la sessione dell'utente.

Si legge batteria, autonomia, limite e stato della ricarica, chilometri,
serrature, porte, bagagliaio, finestrini, gomme, clima, marcia e posizione,
e ogni mezz'ora l'ultima ricarica. Si comanda (chiudi, apri, trova, clima,
ricarica, limite) solo con la chiave privata di un telefono già abbinato
all'auto in Bluetooth: Rivian non accetta comandi firmati in altro modo, e
GdAAuto non fa l'abbinamento.

Il protocollo viene da bretterer/rivian-python-client (MIT) e
bretterer/home-assistant-rivian (Apache 2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from ..errori import ErroreCertificato
from .chiave import carica as carica_chiave
from .cliente import BASE_URL, ID, NOME, ClienteRivian, comandi_di, entra

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("codice", obbligatorio=False),
    Campo("chiave_app", obbligatorio=False, segreto=True, lungo=True),
)

NOTA = {
    "it": (
        "Email e password dell'app Rivian. Meglio un account **dedicato** (un guidatore invitato dal "
        "proprietario): Rivian può bloccare un account usato da troppe parti. Se l'account ha la verifica in due "
        "passaggi, Rivian ti manda un codice per SMS: scrivilo nel campo «codice» e conferma di nuovo. "
        "La password serve solo ora. **Per comandare l'auto** (non serve per leggerla) incolla nel campo della "
        "chiave la chiave privata di un telefono già abbinato all'auto in Bluetooth, per esempio quella che "
        "l'integrazione Rivian di HACS salva nelle sue opzioni: GdAAuto non sa fare l'abbinamento. "
        "Lasciala vuota e GdAAuto leggerà soltanto."
    ),
    "en": (
        "Email and password of the Rivian app. A **dedicated** account (a driver invited by the owner) is best: "
        "Rivian may lock an account used from too many places. If the account has two-step verification, Rivian "
        "texts you a code: type it in the «code» field and submit again. The password is only used now. "
        "**To command the car** (not needed to read it) paste in the key field the private key of a phone key "
        "already paired with the car over Bluetooth, e.g. the one the Rivian HACS integration keeps in its "
        "options: GdAAuto cannot do the pairing. Leave it empty and GdAAuto only reads the car."
    ),
}

VUOLE_PIN = False
#: ``vehicleState`` è l'ultimo dato del cloud: leggerlo non sveglia l'auto, ma Rivian limita le richieste.
INTERVALLO_MINUTI = 5
#: in marcia o in carica, ogni due minuti
INTERVALLO_IN_MOTO_SECONDI = 120

CONF_EMAIL = "email"
CONF_TOKEN = "token"
CONF_CHIAVE = "chiave_app"


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero (con il codice, se serve), elenca le auto e controlla la chiave del telefono."""
    email = str(dati[CONF_EMAIL]).strip()
    chiave = str(dati.get(CONF_CHIAVE) or "").strip()
    http = async_get_clientsession(hass)
    base = str(dati.get("base_url") or BASE_URL)
    if chiave:
        # Una chiave che non si apre si dice subito, prima di mandare SMS.
        carica_chiave(chiave)
    token = await entra(http, email, str(dati["password"]), codice=str(dati.get("codice") or ""), base=base)
    cliente = ClienteRivian(http, token, chiave_telefono=chiave, base_url=base)
    vetture = await cliente.vetture()
    if chiave and vetture and not any(comandi_di(v) for v in vetture):
        raise ErroreCertificato(
            "La chiave del telefono non è registrata per nessuna auto di questo account: "
            "abbinala nell'auto (Drivers and Keys) o lascia vuoto il campo"
        )
    da_salvare: dict[str, Any] = {CONF_EMAIL: email, CONF_TOKEN: dict(cliente.token)}
    if chiave:
        da_salvare[CONF_CHIAVE] = chiave
    return Accesso(
        dati=da_salvare,
        vetture=vetture,
        unique_id=f"{ID}_{email.lower()}",
        titolo=f"{NOME} · {email}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteRivian:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteRivian(
        async_get_clientsession(hass),
        dict(entry.data.get(CONF_TOKEN) or {}),
        salva,
        chiave_telefono=str(entry.data.get(CONF_CHIAVE) or ""),
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
    "ClienteRivian",
    "comandi_di",
    "crea_da_voce",
    "entra",
    "prova_accesso",
]
