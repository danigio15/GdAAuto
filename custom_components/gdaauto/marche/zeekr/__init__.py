"""Zeekr: 001, 7X, X, 7GT e 009, dal cloud dell'app Zeekr fuori dalla Cina.

Si entra con email e password, attraverso le API JSON del login dell'app (non
una pagina web): la password parte cifrata con la chiave pubblica dell'app.
Quando il token scade, GdAAuto rifà da sola lo stesso login.

Il cloud firma ogni richiesta con sei chiavi che stanno dentro l'app Zeekr. Non
sono pubblicate da nessuna parte con un permesso di riuso, e GdAAuto non le
contiene: chi configura le estrae dalla propria app (lo strumento libero
zeekr_key_extractor le scrive in ``zeekr_secrets.json``) e le incolla nel campo
«Chiave API».

Si leggono batteria, autonomia, ricarica (stato, potenza, tempo, limite,
programmazione), consumo medio, contachilometri, gomme, porte, serratura,
finestrini, clima, temperatura, sentinella, posizione, sonno e i km degli ultimi
sette giorni; si comandano serratura, luci, clima, ricarica, limite e
destinazione al navigatore.

Indirizzi, firme e campi vengono da zeekr_ev_api e zeekr_homeassistant
(Fryyyyy, licenza MIT) e da OpenZeekr (borconi, licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from .cliente import ID, NOME, ClienteZeekr, comandi_di
from .firma import chiavi_da

ACCESSO = ACCESSO_CAMPI

#: I paesi dove Zeekr vende, e la regione del cloud (zeekr_homeassistant, ``COUNTRY_CODE_MAPPING``).
PAESI: dict[str, tuple[str, str]] = {
    "IT": ("Italia", "EU"),
    "AT": ("Austria", "EU"),
    "BE": ("Belgio", "EU"),
    "CH": ("Svizzera", "EU"),
    "CZ": ("Cechia", "EU"),
    "DE": ("Germania", "EU"),
    "DK": ("Danimarca", "EU"),
    "EE": ("Estonia", "EU"),
    "ES": ("Spagna", "EU"),
    "FI": ("Finlandia", "EU"),
    "FR": ("Francia", "EU"),
    "GB": ("Regno Unito", "EU"),
    "GR": ("Grecia", "EU"),
    "HR": ("Croazia", "EU"),
    "HU": ("Ungheria", "EU"),
    "IE": ("Irlanda", "EU"),
    "IS": ("Islanda", "EU"),
    "LT": ("Lituania", "EU"),
    "LU": ("Lussemburgo", "EU"),
    "LV": ("Lettonia", "EU"),
    "MT": ("Malta", "EU"),
    "NL": ("Paesi Bassi", "EU"),
    "NO": ("Norvegia", "EU"),
    "PL": ("Polonia", "EU"),
    "PT": ("Portogallo", "EU"),
    "RO": ("Romania", "EU"),
    "SE": ("Svezia", "EU"),
    "SI": ("Slovenia", "EU"),
    "SK": ("Slovacchia", "EU"),
    "SM": ("San Marino", "EU"),
    "AU": ("Australia", "SEA"),
    "NZ": ("Nuova Zelanda", "SEA"),
    "SG": ("Singapore", "SEA"),
    "HK": ("Hong Kong", "SEA"),
    "MY": ("Malesia", "SEA"),
    "TH": ("Thailandia", "SEA"),
    "KZ": ("Kazakistan", "SEA"),
    "AE": ("Emirati Arabi Uniti", "UAE"),
    "QA": ("Qatar", "UAE"),
    "KW": ("Kuwait", "UAE"),
    "BH": ("Bahrein", "UAE"),
    "MX": ("Messico", "LA"),
    "BR": ("Brasile", "LA"),
    "CL": ("Cile", "LA"),
    "CO": ("Colombia", "LA"),
    "PE": ("Perù", "LA"),
}

CONF_CHIAVI = "api_key"
CONF_TOKEN = "token"

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("paese", scelte=tuple((codice, nome) for codice, (nome, _r) in PAESI.items()), predefinito="IT"),
    # Le sei chiavi dell'app, incollate così come escono da zeekr_key_extractor.
    # Non è un campo «segreto» solo perché nel reauth resti com'è: è redatto nella diagnostica.
    Campo(CONF_CHIAVI, lungo=True),
)

NOTA = {
    "it": (
        "Usa un **account Zeekr dedicato**, con l'auto condivisa dall'account principale: il cloud tiene una "
        "sola sessione per account, e GdAAuto farebbe uscire l'app dal telefono. Nel campo «Chiave API» "
        "incolla le **sei chiavi dell'app Zeekr** (hmac_access_key, hmac_secret_key, password_public_key, "
        "prod_secret, vin_key, vin_iv): GdAAuto non le contiene perché Zeekr non le pubblica. Si estraggono "
        "dalla tua app con zeekr_key_extractor (github.com/Wysie/zeekr_key_extractor): incolla il "
        "contenuto di `zeekr_secrets.json` così com'è, oppure righe `NOME=valore`."
    ),
    "en": (
        "Use a **dedicated Zeekr account** with the car shared from your main account: the cloud keeps a "
        "single session per account, and GdAAuto would log the phone app out. In the «API key» field paste "
        "the **six Zeekr app keys** (hmac_access_key, hmac_secret_key, password_public_key, prod_secret, "
        "vin_key, vin_iv): GdAAuto does not contain them because Zeekr does not publish them. Extract them "
        "from your app with zeekr_key_extractor (github.com/Wysie/zeekr_key_extractor): paste the content "
        "of `zeekr_secrets.json` as is, or `NAME=value` lines."
    ),
}

VUOLE_PIN = False
#: Ogni lettura sono tre o quattro richieste firmate; il cloud non sveglia l'auto per leggerla.
INTERVALLO_MINUTI = 5
INTERVALLO_IN_MOTO_SECONDI = 120


def regione_di(paese: str) -> str:
    return PAESI.get((paese or "").upper(), ("", "EU"))[1]


def _crea(hass: HomeAssistant, dati: dict[str, Any], salva: Any = None) -> ClienteZeekr:
    paese = str(dati.get("paese") or "IT").upper()
    return ClienteZeekr(
        async_get_clientsession(hass),
        email=str(dati.get("email") or ""),
        password=str(dati.get("password") or ""),
        paese=paese,
        regione=regione_di(paese),
        chiavi=chiavi_da(str(dati.get(CONF_CHIAVI) or "")),
        token=dati.get(CONF_TOKEN),
        salva=salva,
    )


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero (login completo, senza riusare vecchi token) ed elenca le auto."""
    dati = {k: v for k, v in dati.items() if k != CONF_TOKEN}
    cliente = _crea(hass, dati)
    await cliente.accedi()
    vetture = await cliente.vetture()
    email = str(dati["email"]).strip()
    salvati = {**dati, CONF_TOKEN: dict(cliente.token)}
    return Accesso(dati=salvati, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteZeekr:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return _crea(hass, dict(entry.data), salva)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "PAESI",
    "VUOLE_PIN",
    "ClienteZeekr",
    "comandi_di",
    "crea_da_voce",
    "prova_accesso",
]
