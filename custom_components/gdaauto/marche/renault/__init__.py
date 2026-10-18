"""Renault, Dacia e Alpine: il cloud delle app MyRenault, MyDacia e MyAlpine.

Si entra con l'email e la password dell'app, e il paese dell'account (le
chiavi dei server cambiano col paese). La password serve una volta sola: si
tiene il ``login_token`` di Gigya, e da quello si rinnova il JWT per
Kamereon ogni quarto d'ora. Se Gigya chiede la verifica in due passaggi, il
codice arriva per email e si scrive nel campo «codice».

Si legge batteria, autonomia, ricarica, chilometri, clima, posizione e,
dove l'auto li dice, porte, gomme e limite di carica. Si comandano il clima
e la ricarica (e su alcuni modelli il limite di carica e luci/clacson); la
chiusura delle porte da remoto Kamereon non la offre.

Le chiavi dell'app e la tabella dei modelli vengono da hacf-fr/renault-api
(licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, MARCHI, NOME, ClienteRenault, comandi_di
from .gigya import Gigya, entra
from .locali import PAESE_PREDEFINITO, SCELTE_PAESE, server_di

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("paese", scelte=SCELTE_PAESE, predefinito=PAESE_PREDEFINITO),
    Campo("codice", obbligatorio=False),
)

NOTA = {
    "it": (
        "Email e password dell'app MyRenault, MyDacia o MyAlpine, e il paese dell'account. La password serve "
        "solo ora: GdAAuto tiene il token di accesso, non la password. Se Renault chiede la verifica in due "
        "passaggi, ti manda un codice per email: scrivilo nel campo «codice» e conferma di nuovo (il modulo "
        "dirà «credenziali rifiutate» finché il codice non c'è)."
    ),
    "en": (
        "Email and password of the MyRenault, MyDacia or MyAlpine app, and the account's country. The password "
        "is only used now: GdAAuto keeps the access token, not the password. If Renault asks for two-factor "
        "verification, it emails you a code: type it in the «code» field and submit again (the form shows "
        "«credentials rejected» until the code is there)."
    ),
}

VUOLE_PIN = False
#: Kamereon conta le richieste (una lettura ne fa fino a sette): con calma.
INTERVALLO_MINUTI = 10
#: in carica, ogni cinque minuti
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_EMAIL = "email"
CONF_PAESE = "paese"
CONF_MARCHIO = "marchio"
CONF_TOKEN = "token"


def _lingua(paese: str) -> str:
    return (paese.split("_")[0] or "it").lower()


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero: login (con la verifica, se serve), account e auto."""
    email = str(dati[CONF_EMAIL]).strip()
    paese = str(dati.get(CONF_PAESE) or PAESE_PREDEFINITO)
    marchio = str(dati.get(CONF_MARCHIO) or "renault")
    http = async_get_clientsession(hass)
    server = server_di(paese)
    # Nel reauth il dispositivo già verificato si riusa: niente codice nuovo.
    vecchio = dati.get(CONF_TOKEN) or {}
    token = await entra(
        Gigya(http, server["gigya_url"], server["gigya_chiave"]),
        email,
        str(dati["password"]),
        codice=str(dati.get("codice") or ""),
        dispositivo=vecchio if isinstance(vecchio, dict) else None,
        lingua=_lingua(paese),
    )
    cliente = ClienteRenault(http, paese, token, marchio=marchio, server=server)
    await cliente.accedi()
    vetture = await cliente.vetture()
    # La password e il codice non si salvano.
    da_salvare = {CONF_EMAIL: email, CONF_PAESE: paese, CONF_MARCHIO: marchio, CONF_TOKEN: dict(cliente.token)}
    return Accesso(
        dati=da_salvare,
        vetture=vetture,
        unique_id=f"{ID}_{email.lower()}",
        titolo=f"{MARCHI.get(marchio, NOME)} · {email}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteRenault:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteRenault(
        async_get_clientsession(hass),
        entry.data.get(CONF_PAESE) or PAESE_PREDEFINITO,
        dict(entry.data.get(CONF_TOKEN) or {}),
        salva,
        marchio=entry.data.get(CONF_MARCHIO) or "renault",
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
    "ClienteRenault",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
