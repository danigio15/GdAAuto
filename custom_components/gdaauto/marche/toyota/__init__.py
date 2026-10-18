"""Toyota e Lexus in Europa: il cloud delle app MyToyota e Lexus Link+.

È lo stesso cloud (Toyota Connected Europe) e lo stesso account per i due
marchi: il preimpostato ``marchio`` del catalogo (``toyota`` o ``lexus``)
cambia solo il nome sullo schermo e l'intestazione ``x-brand``.

Si entra con l'email e la password dell'app. Si leggono le auto con la
spina: elettriche (bZ4X, bZ, C-HR+, Urban Cruiser, Lexus RZ e UX 300e) e
ibride plug-in (RAV4, C-HR, Prius plug-in, Lexus NX e RX 450h+). Si
chiudono e aprono le portiere e si accende il clima, se l'auto lo dichiara.

Indirizzi, intestazioni e chiave dell'app vengono da pytoyoda (MIT); vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, MARCHI, NOME, ClienteToyota, comandi_di

ACCESSO = ACCESSO_CAMPI
CAMPI = (Campo("email"), Campo("password", segreto=True))
VUOLE_PIN = False
#: Il cloud limita le richieste (429) e l'auto aggiorna il cloud solo ogni tanto:
#: leggerla più spesso non dà dati più freschi.
INTERVALLO_MINUTI = 15
#: In carica o in marcia, comunque con calma: ogni lettura sono cinque richieste.
INTERVALLO_IN_MOTO_SECONDI = 600

CONF_MARCHIO = "marchio"
CONF_TOKEN = "token"

NOTA = {
    "it": (
        "Email e password dell'app **MyToyota** (o **Lexus Link+**), con l'auto già attivata nell'app e i "
        "servizi connessi attivi. Vale per le auto europee. Il cloud manda i dati che l'auto gli ha dato "
        "l'ultima volta: non la si sveglia per leggerla. Il clima da remoto ha un limite di avvii fra un "
        "giro di chiave e l'altro."
    ),
    "en": (
        "Email and password of the **MyToyota** (or **Lexus Link+**) app, with the car already activated in "
        "the app and connected services active. European cars only. The cloud returns what the car last "
        "reported: it is not woken up to be read. Remote climate has a limited number of starts between two "
        "drives."
    ),
}


def _marchio(dati: dict[str, Any]) -> str:
    m = str(dati.get(CONF_MARCHIO) or "toyota").lower()
    return m if m in MARCHI else "toyota"


def crea(hass: HomeAssistant, dati: dict[str, Any], salva: Any = None) -> ClienteToyota:
    return ClienteToyota(
        async_get_clientsession(hass),
        str(dati["email"]),
        str(dati["password"]),
        _marchio(dati),
        dati.get(CONF_TOKEN),
        salva,
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero (da capo, con la password) ed elenca le auto con la spina."""
    dati = {**dati, "email": str(dati["email"]).strip(), CONF_MARCHIO: _marchio(dati)}
    dati.pop(CONF_TOKEN, None)
    cliente = crea(hass, dati)
    await cliente.accedi()
    vetture = await cliente.vetture()
    email = dati["email"]
    marchio = dati[CONF_MARCHIO]
    # Toyota e Lexus sono lo stesso account, ma due voci diverse se servono tutte e due.
    chi = email.lower() if marchio == "toyota" else f"{marchio}_{email.lower()}"
    return Accesso(
        dati={**dati, CONF_TOKEN: dict(cliente.token)},
        vetture=vetture,
        unique_id=f"{ID}_{chi}",
        titolo=f"{MARCHI[marchio][0]} · {email}",
    )


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteToyota:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return crea(hass, dict(entry.data), salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "MARCHI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteToyota",
    "comandi_di",
    "crea",
    "crea_da_voce",
    "prova_accesso",
]
