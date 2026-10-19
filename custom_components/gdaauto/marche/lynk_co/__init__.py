"""Lynk & Co: 01 (dal 2025), 02 e 08, dal cloud europeo dell'app Lynk & Co.

Il login è quello dell'app (email, password e codice SMS) e si fa nel browser
di chi configura; l'indirizzo ``msauth://…`` della fine si incolla in Home
Assistant. Poi GdAAuto rinnova da sola il token.

Si leggono batteria, autonomia, ricarica e limite, consumo medio, capacità,
carburante delle ibride, contachilometri, porte, finestrini, serratura, clima
e posizione; si comandano serratura, luci, clima, ricarica e limite.

Le 01 di prima del 2025 stanno su un'altra piattaforma (``aion.connectedcar``):
qualcuno riferisce che rispondono anche qui, ma non è garantito.

Indirizzi, firma e campi vengono da ha_lynkco_2025 (b12e, licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from .cliente import ID, NOME, ClienteLynk, codice_da, comandi_di, indirizzo_login, pkce, scambia_codice

ACCESSO = ACCESSO_CODICE_BROWSER
CAMPI: tuple[Campo, ...] = ()

NOTA = {
    "it": (
        "Apri il link e accedi come nell'app Lynk & Co (email, password, codice SMS). Alla fine il browser prova "
        "ad aprire un indirizzo «msauth://…» e si ferma: copialo dagli strumenti per sviluppatori (scheda Rete, "
        "intestazione «Location» dell'ultima richiesta) e incollalo qui. Lynk & Co tiene un solo dispositivo "
        "per account: meglio un secondo account con l'auto condivisa, o l'app sul telefono verrà scollegata."
    ),
    "en": (
        "Open the link and sign in as in the Lynk & Co app (email, password, SMS code). At the end the browser "
        "tries to open an «msauth://…» address and stops: copy it from the developer tools (Network tab, "
        "«Location» header of the last request) and paste it here. Lynk & Co keeps one device per account: "
        "use a second account with the car shared, or the phone app will be logged out."
    ),
}

VUOLE_PIN = False
#: Ogni lettura sono sei o sette richieste: ogni quarto d'ora, come l'integrazione di riferimento.
INTERVALLO_MINUTI = 15
INTERVALLO_IN_MOTO_SECONDI = 120

CONF_TOKEN = "token"
CONF_DISPOSITIVO = "dispositivo"


async def avvia_browser(_hass: HomeAssistant, _dati: dict[str, Any]) -> Dispositivo:
    verificatore, sfida = pkce()
    return Dispositivo(
        link=indirizzo_login(sfida),
        codice_utente="",
        stato={"verificatore": verificatore, CONF_DISPOSITIVO: str(uuid.uuid4())},
    )


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    sessione = async_get_clientsession(hass)
    token = await scambia_codice(sessione, codice_da(incollato), dispositivo.stato["verificatore"])
    cliente = ClienteLynk(sessione, token, dispositivo.stato[CONF_DISPOSITIVO])
    await cliente.accedi()
    vetture = await cliente.vetture()
    chi = cliente.chi
    email = str(chi.get("email") or "").strip()
    utente = email.lower() or str(chi.get("snowflakeId") or chi.get("sub") or "")
    salvati = {**dati, CONF_TOKEN: dict(cliente.token), CONF_DISPOSITIVO: cliente.dispositivo}
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{utente}", titolo=f"{NOME} · {email or utente}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteLynk:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteLynk(
        async_get_clientsession(hass), entry.data.get(CONF_TOKEN) or {}, entry.data.get(CONF_DISPOSITIVO), salva
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
    "ClienteLynk",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
]
