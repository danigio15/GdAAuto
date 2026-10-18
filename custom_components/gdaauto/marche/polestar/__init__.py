"""Polestar: Polestar 2, 3 e 4, dal cloud dell'app Polestar.

Si entra con l'account Polestar ID (email e password, le stesse dell'app) e si
leggono tutte le auto dell'account: batteria, autonomia, contachilometri, stato
e tempo della ricarica. L'API dell'app non apre comandi a chi non è l'app,
quindi Polestar si legge soltanto.

Il cloud tiene i dati che l'auto gli manda da sola: leggerli non la sveglia.
Gli indirizzi e le query vengono da pypolestar (licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, NOME, ClientePolestar, comandi_di

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
)

NOTA = {
    "it": (
        "Le stesse credenziali dell'app Polestar (Polestar ID). Si leggono tutte le auto dell'account: "
        "batteria, autonomia, contachilometri e ricarica. Polestar non apre comandi alle app di terzi, "
        "e non dice porte, serrature né posizione: questa marca si legge soltanto."
    ),
    "en": (
        "The same credentials as the Polestar app (Polestar ID). Every car on the account is read: "
        "battery, range, odometer and charging. Polestar offers no remote commands to third-party apps, "
        "and does not report doors, locks or location: this brand is read-only."
    ),
}

VUOLE_PIN = False
#: Si legge soltanto: nelle opzioni non c'è niente da accendere.
SENZA_COMANDI = True
#: Il cloud riceve i dati dall'auto ogni tanto: più spesso non serve.
INTERVALLO_MINUTI = 10
#: E in carica ogni due minuti: il tempo che il cloud ci mette a saperne di più.
INTERVALLO_IN_MOTO_SECONDI = 120

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_TOKEN = "token"


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero ed elenca le auto; i token si salvano con le credenziali."""
    email = str(dati[CONF_EMAIL]).strip()
    cliente = ClientePolestar(async_get_clientsession(hass), email, str(dati[CONF_PASSWORD]))
    await cliente.accedi()
    vetture = await cliente.vetture()
    salvati = {**dati, CONF_EMAIL: email, CONF_TOKEN: dict(cliente.token)}
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClientePolestar:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClientePolestar(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
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
    "SENZA_COMANDI",
    "VUOLE_PIN",
    "ClientePolestar",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
