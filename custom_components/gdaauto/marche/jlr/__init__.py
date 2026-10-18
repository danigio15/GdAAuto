"""Jaguar e Land Rover: l'I-PACE, le Range Rover elettriche e le ibride plug-in, dal cloud InControl.

Jaguar e Land Rover hanno un cloud solo (InControl, oggi nell'app «JLR
Remote»): la stessa marca serve i due marchi del catalogo, e il marchio scelto
(``marchio``, preimpostato) dà solo il nome alla voce. Si entra con email e
password dell'account InControl, come l'app; il cloud è quello del «resto del
mondo» o quello cinese.

Si legge lo stato che l'auto ha mandato al cloud (batteria, autonomia,
ricarica, clima, porte, finestrini, serrature, gomme, antifurto, posizione,
contachilometri, serbatoio delle ibride) senza svegliarla. I comandi sono
quelli dei servizi attivi sull'auto: chiudere e aprire col PIN personale
dell'app, clacson e luci, preclimatizzazione, ricarica e limite di carica.

Il protocollo è quello di ardevd/jlrpy e msp1974/homeassistant-jlrincontrol
(licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, MARCHI, NOME, REGIONI, ClienteJLR, comandi_di

ACCESSO = ACCESSO_CAMPI

CONF_REGIONE = "regione"
CONF_MARCHIO = "marchio"
CONF_TOKEN = "token"
CONF_ID_DISPOSITIVO = "id_dispositivo"

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo(
        CONF_REGIONE,
        scelte=(("mondo", "Resto del mondo / Rest of the world"), ("cina", "Cina / China")),
        predefinito="mondo",
    ),
    Campo("pin", obbligatorio=False, segreto=True),
)

NOTA = {
    "it": (
        "Email e password sono quelle dell'app InControl / JLR Remote. Meglio un account **dedicato**, aggiunto "
        "all'auto come utente secondario dall'account principale. Il **PIN** è quello personale a quattro cifre "
        "dell'app: serve solo per chiudere e aprire; clima, ricarica e clacson non lo chiedono. Puoi aggiungerlo "
        "dopo nelle opzioni. Attenzione: JLR ha cominciato a bloccare i programmi non ufficiali, e il cloud "
        "potrebbe rifiutare GdAAuto anche con le credenziali giuste."
    ),
    "en": (
        "Email and password are those of the InControl / JLR Remote app. A **dedicated** account, added to the car "
        "as a secondary user by the main account, is best. The **PIN** is the app's personal four-digit PIN: it is "
        "only needed to lock and unlock; climate, charging and horn do not ask for it. You can add it later in the "
        "options. Note: JLR has started blocking unofficial clients, and the cloud may refuse GdAAuto even with "
        "the right credentials."
    ),
}

#: Il PIN personale dell'app: lo vuole il cloud per chiudere e aprire.
VUOLE_PIN = True
#: Il cloud tiene l'ultima fotografia che l'auto ha mandato: leggerla più spesso non la rende più fresca,
#: e il cloud JLR non ama chi lo interroga troppo.
INTERVALLO_MINUTI = 10
#: In carica o in marcia, ogni cinque minuti.
INTERVALLO_IN_MOTO_SECONDI = 300


def _cliente(hass: HomeAssistant, dati: dict[str, Any], salva: Any = None) -> ClienteJLR:
    return ClienteJLR(
        async_get_clientsession(hass),
        email=str(dati.get("email") or ""),
        password=str(dati.get("password") or ""),
        regione=str(dati.get(CONF_REGIONE) or "mondo"),
        dispositivo=dati.get(CONF_ID_DISPOSITIVO),
        token=dati.get(CONF_TOKEN) or {},
        salva=salva,
    )


def titolo(dati: dict[str, Any], email: str) -> str:
    """«Jaguar · email» o «Land Rover · email», secondo il marchio scelto."""
    return f"{MARCHI.get(str(dati.get(CONF_MARCHIO) or ''), NOME)} · {email}"


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero con email e password (un token nuovo, sempre), ed elenca le auto dell'account."""
    dati = {
        **dati,
        CONF_TOKEN: {},
        CONF_ID_DISPOSITIVO: dati.get(CONF_ID_DISPOSITIVO) or str(uuid.uuid4()),
    }
    if dati.get(CONF_REGIONE) not in REGIONI:
        dati[CONF_REGIONE] = "mondo"
    cliente = _cliente(hass, dati)
    await cliente.accedi()
    vetture = await cliente.vetture()
    email = cliente.email
    salvati = {**dati, "email": email, CONF_TOKEN: dict(cliente.token), CONF_ID_DISPOSITIVO: cliente.dispositivo}
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=titolo(dati, email))


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteJLR:
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
    "ClienteJLR",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
