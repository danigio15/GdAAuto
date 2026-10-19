"""Nissan: Ariya, Leaf e Micra dal cloud NissanConnect, nuovo e vecchio.

Nissan ha due cloud con due account separati (spesso con la stessa email):

- **NissanConnect Services** (Ariya, Leaf dal 2019, Micra; Kamereon, solo
  Europa). Il login è una pagina web: l'utente entra nel suo browser dal link
  che GdAAuto prepara, e incolla l'indirizzo finale (``com://wso2.service.nci?code=…``)
  o solo il codice. GdAAuto fa lo scambio e poi rinnova i token da solo, senza
  password (``ACCESSO_CODICE_BROWSER``).
- **NissanConnect EV** (il Carwings delle Leaf 2011-2019, anche in America,
  Australia e Giappone). Il login è l'API JSON che l'app chiama, con email e
  password; la sessione non si rinnova, e la password resta salvata.

In Europa, con «riconosci da solo», GdAAuto prova tutti e due: il browser per
le auto nuove e, se c'è la password, il vecchio cloud per le Leaf.

Su NissanConnect Services si leggono batteria, autonomia, ricarica e spina,
clima e temperature, posizione, porte e serratura (dove l'auto le dice),
chilometri e i viaggi della settimana; si comandano clima, avvio (e su alcune
auto fermata) della ricarica, luci e clacson. Sul Carwings si leggono
batteria, energia, autonomia, ricarica, spina, clima e il consumo del mese;
si comandano clima e avvio della ricarica.

Indirizzi, chiavi e campi vengono da dan-r/HomeAssistant-NissanConnect (MIT)
e da filcole/pycarwings2 (Apache-2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, ACCESSO_CODICE_BROWSER, Accesso, Campo, Dispositivo
from ..errori import ErroreCredenziali
from .cliente import (
    AUTO_EUROPA,
    CARWINGS,
    EUROPA_NUOVE,
    ID,
    KAMEREON,
    NOME,
    ClienteNissan,
    comandi_di,
    vie_della_regione,
)
from .kamereon import EUROPA, codice_da, link_login

ACCESSO = ACCESSO_CODICE_BROWSER

SCELTE_REGIONE: tuple[tuple[str, str], ...] = (
    (AUTO_EUROPA, "Europa: riconosci da solo (tutte le Nissan connesse)"),
    (EUROPA_NUOVE, "Europa: Ariya, Leaf dal 2019, Micra (NissanConnect Services)"),
    ("NE", "Europa: Leaf 2011-2019 (NissanConnect EV)"),
    ("NNA", "Stati Uniti: Leaf 2011-2019 (NissanConnect EV)"),
    ("NCI", "Canada: Leaf 2011-2019 (NissanConnect EV)"),
    ("NMA", "Australia: Leaf 2011-2019 (NissanConnect EV)"),
    ("NML", "Giappone: Leaf 2011-2019 (NissanConnect EV)"),
)

CAMPI = (
    Campo("email"),
    # Solo per le Leaf fino al 2019: NissanConnect Services entra dal browser.
    Campo("password", obbligatorio=False, segreto=True),
    Campo("regione", scelte=SCELTE_REGIONE, predefinito=AUTO_EUROPA),
)

NOTA = {
    "it": (
        "Le Leaf fino al 2019 usano NissanConnect EV, le auto più nuove NissanConnect Services: sono due account "
        "diversi. Per le auto nuove si entra nel browser: apri il link, entra con l'account Nissan, e alla fine il "
        "browser prova ad aprire un indirizzo che inizia con «com://wso2.service.nci?code=». Copialo (se la barra "
        "non lo mostra, è negli strumenti per sviluppatori, scheda Rete) e incollalo qui. Per le Leaf fino al 2019 "
        "scrivi la password dell'app: resta salvata, perché quel cloud non ha un token che si rinnova. GdAAuto "
        "legge l'ultimo dato che l'auto ha mandato e non la sveglia."
    ),
    "en": (
        "Leafs up to 2019 use NissanConnect EV, newer cars NissanConnect Services: they are two separate accounts. "
        "For newer cars you sign in in the browser: open the link, sign in with the Nissan account, and at the end "
        "the browser tries to open an address starting with «com://wso2.service.nci?code=». Copy it (if the address "
        "bar does not show it, look in the developer tools, Network tab) and paste it here. For Leafs up to 2019 "
        "type the app password: it stays stored, because that cloud has no renewable token. GdAAuto reads the last "
        "data the car sent and never wakes it."
    ),
}

VUOLE_PIN = False
#: il cloud non è veloce, e il vecchio Carwings meno ancora: con calma
INTERVALLO_MINUTI = 10
#: in carica o col clima acceso, ogni cinque minuti
INTERVALLO_IN_MOTO_SECONDI = 300

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_REGIONE = "regione"
CONF_TOKEN = "token"
CONF_VIE = "vie"


def _regione(dati: dict[str, Any]) -> str:
    regione = str(dati.get(CONF_REGIONE) or AUTO_EUROPA)
    return regione if regione in {v for v, _ in SCELTE_REGIONE} else AUTO_EUROPA


def serve_browser(dati: dict[str, Any]) -> bool:
    """Se la regione scelta passa da NissanConnect Services, il cui login si fa nel browser."""
    return KAMEREON in vie_della_regione(_regione(dati))


def accesso_per(dati: dict[str, Any]) -> str:
    """Dal browser per NissanConnect Services; con email e password per le sole Leaf 2011-2019."""
    return ACCESSO_CODICE_BROWSER if serve_browser(dati) else ACCESSO_CAMPI


def _accesso(dati: dict[str, Any], cliente: ClienteNissan, vetture: list) -> Accesso:
    email = str(dati[CONF_EMAIL]).strip()
    da_salvare: dict[str, Any] = {
        CONF_EMAIL: email,
        CONF_REGIONE: cliente.regione,
        CONF_VIE: list(cliente.vie),
        CONF_TOKEN: dict(cliente.token) if KAMEREON in cliente.vie else {},
    }
    if CARWINGS in cliente.vie:
        # Il Carwings non ha un token che si rinnova: per rientrare serve la password.
        da_salvare[CONF_PASSWORD] = cliente.password
    return Accesso(dati=da_salvare, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


def _cliente(hass: HomeAssistant, dati: dict[str, Any]) -> ClienteNissan:
    return ClienteNissan(
        async_get_clientsession(hass),
        str(dati[CONF_EMAIL]).strip(),
        str(dati.get(CONF_PASSWORD) or ""),
        _regione(dati),
    )


async def avvia_browser(hass: HomeAssistant, dati: dict[str, Any]) -> Dispositivo:
    """Il link del login di NissanConnect Services, col suo PKCE.

    Per le regioni che hanno solo il vecchio cloud il browser non serve: il
    link è vuoto, e ``completa_browser`` entra con la password (vedi
    ``serve_browser``, perché il config flow possa saltare questo passo).
    """
    if not serve_browser(dati):
        return Dispositivo(link="", codice_utente="", stato={"senza_browser": True})
    link, stato = link_login(EUROPA)
    return Dispositivo(link=link, codice_utente="", stato=stato)


async def completa_browser(
    hass: HomeAssistant, dati: dict[str, Any], dispositivo: Dispositivo, incollato: str
) -> Accesso:
    """Lo scambio del codice incollato (l'indirizzo finale o il codice da solo).

    Con «riconosci da solo» e la password, entra anche nel vecchio cloud.
    """
    cliente = _cliente(hass, dati)
    codice = ""
    if not dispositivo.stato.get("senza_browser"):
        try:
            codice = codice_da(incollato, str(dispositivo.stato.get("state") or ""))
        except ErroreCredenziali:
            # In Europa chi ha solo una Leaf vecchia può non avere l'account nuovo: basta la password.
            if not (cliente.regione == AUTO_EUROPA and cliente.password):
                raise
    vetture = await cliente.prova(codice, str(dispositivo.stato.get("verifier") or ""))
    return _accesso(dati, cliente, vetture)


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Solo per le regioni senza browser (le Leaf 2011-2019): entra con email e password."""
    if serve_browser(dati):
        raise ErroreCredenziali("NissanConnect Services si apre dal browser: usa il link del login")
    cliente = _cliente(hass, dati)
    return _accesso(dati, cliente, await cliente.prova())


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteNissan:
    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteNissan(
        async_get_clientsession(hass),
        str(entry.data.get(CONF_EMAIL) or ""),
        str(entry.data.get(CONF_PASSWORD) or ""),
        _regione(dict(entry.data)),
        dict(entry.data.get(CONF_TOKEN) or {}),
        salva,
        vie=entry.data.get(CONF_VIE) or None,
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
    "ClienteNissan",
    "accesso_per",
    "avvia_browser",
    "comandi_di",
    "completa_browser",
    "crea_da_voce",
    "prova_accesso",
    "serve_browser",
]
