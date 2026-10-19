"""smart: #1, #3 e #5, dal cloud europeo dell'app Hello smart (piattaforma Geely).

Il login si fa nel browser di chi configura, sulla pagina smart ID dell'app:
l'indirizzo finale, con il token dentro, si incolla in Home Assistant. Poi
GdAAuto apre la sessione dell'API e la rinnova da sola, senza password.

Si legge tutto quello che l'app mostra (batteria, autonomia, ricarica e
limite, gomme, porte, serrature, finestrini, clima, posizione, il diario dei
viaggi) e si comanda: chiudi e apri, luci, clima, ricarica, limite e
destinazione al navigatore. Le smart EQ (fortwo, forfour) sono un'altra cosa:
usano il cloud Mercedes.

Indirizzi, firme e codici vengono da pySmartHashtag e SmartHashtag (DasBasti,
licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import ErroreCredenziali
from .cliente import ID, NOME, ClienteSmart, comandi_di, token_da

ACCESSO = ACCESSO_CODICE_BROWSER
CAMPI: tuple[Campo, ...] = ()

NOTA = {
    "it": (
        "Apri il link e accedi con lo smart ID dell'app Hello smart. Alla fine il browser va a un indirizzo "
        "con «access_token» dentro (se non si apre, lo trovi negli strumenti per sviluppatori, scheda Rete, "
        "nell'ultima richiesta): copialo intero e incollalo qui. La password resta nel tuo browser. "
        "Meglio un account secondario con l'auto condivisa: il cloud ricorda un'auto attiva per conto."
    ),
    "en": (
        "Open the link and sign in with the smart ID of the Hello smart app. At the end the browser goes to an "
        "address containing «access_token» (if it does not open, find it in the developer tools, Network tab, "
        "last request): copy all of it and paste it here. Your password stays in your browser. "
        "A secondary account with the car shared is best: the cloud remembers one active car per account."
    ),
}

VUOLE_PIN = False
#: Il cloud riceve i dati dall'auto ogni tanto, e il login è sorvegliato: con calma.
INTERVALLO_MINUTI = 5
INTERVALLO_IN_MOTO_SECONDI = 120

CONF_TOKEN = "token"


async def avvia_browser(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Il link della pagina smart ID, preso come lo prende l'app."""
    cliente = ClienteSmart(async_get_clientsession(hass), {})
    link = await cliente.link_login()
    return Dispositivo(link=link, codice_utente="", stato={"dispositivo": cliente.token["dispositivo"]})


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Dall'indirizzo finale (o dal token da solo) alla sessione, e l'elenco delle auto."""
    token_oauth, refresh = token_da(incollato)
    cliente = ClienteSmart(async_get_clientsession(hass), {"dispositivo": dispositivo.stato.get("dispositivo")})
    await cliente.apri_sessione(token_oauth, refresh)
    vetture = await cliente.vetture()
    utente = cliente.token.get("utente")
    if not utente:
        raise ErroreCredenziali("smart non dice chi è l'utente: rientra dal browser")
    salvati = {**dati, CONF_TOKEN: dict(cliente.token)}
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{utente}", titolo=f"{NOME} · {utente}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteSmart:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteSmart(async_get_clientsession(hass), entry.data.get(CONF_TOKEN) or {}, salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteSmart",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
