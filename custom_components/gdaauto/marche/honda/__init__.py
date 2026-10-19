"""Honda: Honda e, e:Ny1 e le altre Honda europee connesse, dal cloud dell'app My Honda+.

Si entra con l'email e la password dell'app. La prima volta Honda vuole
registrare Home Assistant come «dispositivo» nuovo: manda un'email con un
link, che NON va aperto ma copiato nel campo «codice». Da lì GdAAuto tiene la
chiave del dispositivo e i token (la password non si salva) e rinnova da solo
l'access token ogni ora.

Si legge carica, autonomia (con e senza clima), ricarica, cavo, limiti di
carica, chilometri, posizione, velocità, abitacolo, clima, porte, serrature,
finestrini, quadro e, sulle ibride come la ZR-V e:HEV, serbatoio e
autonomia a benzina; ogni mezz'ora i viaggi (km della settimana e
consumo medio). Si comandano chiusura, clacson e luci, clima, ricarica e
limite di carica, secondo quello che l'auto dichiara di saper fare.

Il protocollo viene da enricobattocchi/pymyhondaplus (licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from ..errori import ErroreCredenziali
from .cliente import ID, NOME, ClienteHonda, comandi_di, entra

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("codice", obbligatorio=False, lungo=True),
)

NOTA = {
    "it": (
        "Email e password dell'app My Honda+. La prima volta Honda ti manda un'email con un link per registrare "
        "Home Assistant: NON aprirlo, copialo e incollalo nel campo «codice», poi conferma di nuovo. La password "
        "non si salva: GdAAuto tiene solo la chiave del dispositivo e i token."
    ),
    "en": (
        "Email and password of the My Honda+ app. The first time, Honda emails you a link to register Home "
        "Assistant: do NOT open it, copy it into the «code» field and submit again. The password is not stored: "
        "GdAAuto keeps only the device key and the tokens."
    ),
}

VUOLE_PIN = False
#: L'app stessa legge il cruscotto ogni dieci minuti: più spesso non porta dati nuovi.
INTERVALLO_MINUTI = 10
#: In marcia o in carica, ogni cinque minuti.
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_EMAIL = "email"
CONF_TOKEN = "token"
CONF_CHIAVE = "chiave_dispositivo"
CONF_LINGUA = "lingua"
CONF_PAESE = "paese"


def _lingua_e_paese(hass: HomeAssistant) -> tuple[str, str]:
    lingua = (hass.config.language or "it").split("-")[0].lower() or "it"
    paese = (hass.config.country or lingua).upper()
    return lingua, paese


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero (con il link dell'email, se Honda lo chiede), ed elenca le auto."""
    email = str(dati[CONF_EMAIL]).strip()
    lingua, paese = _lingua_e_paese(hass)
    lingua = str(dati.get(CONF_LINGUA) or lingua)
    paese = str(dati.get(CONF_PAESE) or paese)
    if not dati.get("password"):
        raise ErroreCredenziali("Serve la password di My Honda+ (non si salva: va riscritta per rientrare)")
    http = async_get_clientsession(hass)
    # Nel reauth la chiave del dispositivo già registrato si riusa: niente email nuova.
    token, chiave = await entra(
        http,
        email,
        str(dati["password"]),
        codice=str(dati.get("codice") or ""),
        chiave=dati.get(CONF_CHIAVE) or None,
        lingua=lingua,
    )
    cliente = ClienteHonda(http, token, lingua=lingua, paese=paese)
    vetture = await cliente.vetture()
    # La password e il link non si salvano.
    da_salvare = {
        CONF_EMAIL: email,
        CONF_TOKEN: dict(cliente.token),
        CONF_CHIAVE: chiave,
        CONF_LINGUA: lingua,
        CONF_PAESE: paese,
    }
    return Accesso(dati=da_salvare, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteHonda:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    lingua, paese = _lingua_e_paese(hass)
    return ClienteHonda(
        async_get_clientsession(hass),
        dict(entry.data.get(CONF_TOKEN) or {}),
        salva,
        lingua=entry.data.get(CONF_LINGUA) or lingua,
        paese=entry.data.get(CONF_PAESE) or paese,
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
    "ClienteHonda",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
