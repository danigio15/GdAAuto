"""Mercedes-Benz: le EQ e le ibride plug-in, dal cloud dell'app Mercedes me.

Si entra con email e password dell'account Mercedes, nella regione dove è
registrato (Europa, Nord America, Asia-Pacifico; la Cina no: lì il login vuole
un captcha). Lo stato si legge ogni dieci minuti dal REST dei widget, che non
sveglia l'auto; i comandi (chiudi, apri col PIN, trova, clima, limite di
carica) passano dal websocket dell'app, aperto solo per il tempo del comando.

Il protocollo è quello che ha ricostruito ReneNulschDE/mbapi2020 (licenza
MIT): vedi RINGRAZIAMENTI.md. Protobuf si legge e si scrive a mano
(``protobuf.py``), senza dipendenze nuove.
"""

from __future__ import annotations

from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, NOME, REGIONI, ClienteMercedes, comandi_di

ACCESSO = ACCESSO_CAMPI

CONF_REGIONE = "regione"
CONF_TOKEN = "token"
CONF_DISPOSITIVO = "dispositivo"

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo(
        CONF_REGIONE,
        scelte=(
            ("europa", "Europa / Europe"),
            ("nord_america", "Nord America / North America"),
            ("asia_pacifico", "Asia-Pacifico / Asia-Pacific"),
        ),
        predefinito="europa",
    ),
)

NOTA = {
    "it": (
        "Usa un account Mercedes me **dedicato**, invitato all'auto dall'account principale (fino a sei utenti per "
        "auto), e senza verifica in due passaggi: GdAAuto entra con email e password come l'app. Il PIN dell'app, "
        "nelle opzioni, serve solo per aprire. Thailandia e India usano la regione Europa; Messico e Brasile "
        "Asia-Pacifico. La Cina non è supportata."
    ),
    "en": (
        "Use a **dedicated** Mercedes me account, invited to the car by the main account (up to six users per car), "
        "without two-factor authentication: GdAAuto signs in with email and password like the app does. The app "
        "PIN, in the options, is only needed to unlock. Thailand and India use the Europe region; Mexico and Brazil "
        "Asia-Pacific. China is not supported."
    ),
}

#: Il PIN dell'app Mercedes: lo vuole il cloud per aprire le porte.
VUOLE_PIN = True
#: Il cloud tiene l'ultima fotografia dell'auto: leggerla più spesso non la rende più fresca.
INTERVALLO_MINUTI = 10
#: In carica o in marcia, ogni cinque minuti: il REST dei widget regge, ma non è fatto per essere martellato.
INTERVALLO_IN_MOTO_SECONDI = 300


def _cliente(hass: HomeAssistant, dati: dict[str, Any], salva: Any = None) -> ClienteMercedes:
    return ClienteMercedes(
        async_get_clientsession(hass),
        email=str(dati.get("email") or ""),
        password=str(dati.get("password") or ""),
        regione=str(dati.get(CONF_REGIONE) or "europa"),
        token=dati.get(CONF_TOKEN) or {},
        dispositivo=dati.get(CONF_DISPOSITIVO),
        salva=salva,
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero con email e password, ed elenca le auto dell'account."""
    dati = {**dati, CONF_TOKEN: {}, CONF_DISPOSITIVO: dati.get(CONF_DISPOSITIVO) or str(uuid.uuid4())}
    if dati.get(CONF_REGIONE) not in REGIONI:
        dati[CONF_REGIONE] = "europa"
    cliente = _cliente(hass, dati)
    await cliente.accedi()
    vetture = await cliente.vetture()
    email = cliente.email
    salvati = {
        **dati,
        "email": email,
        CONF_TOKEN: dict(cliente.token),
        CONF_DISPOSITIVO: cliente.dispositivo,
    }
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteMercedes:
    @callback
    def salva(token: dict[str, Any]) -> None:
        # Solo i dati: le opzioni non cambiano, quindi l'integrazione non si ricarica.
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return _cliente(hass, dict(entry.data), salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteMercedes",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
