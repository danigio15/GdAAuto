"""Mazda: la **Mazda 6e** europea, dall'app «Mazda 6e» (piattaforma Changan).

**Quali Mazda.** Le Mazda hanno due cloud diversi:

- la **6e** (e le altre Mazda costruite con Changan che usano la stessa app)
  parla col gateway europeo di Changan: login JSON con email e password, un
  codice per email la prima volta, token che si rinnovano. È quello che
  GdAAuto legge e comanda qui;
- le altre (**MX-30**, **CX-60** e **CX-80** PHEV, e tutte le Mazda di
  «MyMazda») usano il cloud Mazda Connected Services. Lì il login nel browser
  si potrebbe fare, ma ogni richiesta all'API deve portare un'intestazione
  ``X-acf-sensor-data`` che è la telemetria anti-bot di Akamai (tocchi,
  sensori e tempi del telefono), e i progetti che funzionano la **fabbricano**
  e imitano anche l'impronta TLS dell'app. È un blocco di sicurezza: GdAAuto
  non lo aggira, e quindi queste Mazda qui non sono coperte. Servirebbe
  un'API ufficiale di Mazda (o un accesso per terze parti) senza Akamai.

**La chiave pubblica del cloud.** L'app cifra email, password e PIN con una
chiave pubblica RSA del cloud Changan, estratta dall'app. GdAAuto non la
contiene: se in ``/config/gdaauto/`` non c'è già il file, la scarica dal
repository pubblico fano0001/home-assistant-mazda-6e (un file a un commit
fisso, con l'impronta SHA-256 controllata) e la salva lì.

Il protocollo viene da fano0001/home-assistant-mazda-6e (Apache 2.0): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..base import ACCESSO_CAMPI, Accesso, Campo
from ..errori import ErroreCertificato
from . import cripto
from .cliente import BASE_URL, ID, NOME, ClienteMazda, comandi_di, entra

ACCESSO = ACCESSO_CAMPI

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("codice", obbligatorio=False),
    Campo("pin", obbligatorio=False, segreto=True),
)

NOTA = {
    "it": (
        "Per la **Mazda 6e** (app «Mazda 6e», Europa): email e password dell'app. La prima volta Mazda manda "
        "un codice per email per il nuovo accesso: scrivilo nel campo «codice» e conferma di nuovo. "
        "La password serve solo ora. Il **PIN** è il codice a sei cifre dei comandi dell'app (Control "
        "Passcode): serve per aprire e chiudere; senza, GdAAuto legge e comanda solo clima, lampeggio e "
        "limite di ricarica. GdAAuto scarica da solo la chiave pubblica del cloud dal repository pubblico "
        "fano0001/home-assistant-mazda-6e e la salva in `/config/gdaauto/`. Le Mazda di MyMazda (MX-30, "
        "CX-60, CX-80…) **non** sono coperte: il loro cloud chiede una protezione anti-bot che GdAAuto non aggira."
    ),
    "en": (
        "For the **Mazda 6e** (“Mazda 6e” app, Europe): the app's email and password. The first time Mazda "
        "emails a code for the new sign-in: type it in the «code» field and submit again. The password is "
        "only used now. The **PIN** is the app's six-digit command code (Control Passcode): it is needed to lock "
        "and unlock; without it GdAAuto reads and commands only climate, flash and charge limit. GdAAuto "
        "downloads the cloud's public key by itself from the public fano0001/home-assistant-mazda-6e repository "
        "and saves it in `/config/gdaauto/`. MyMazda cars (MX-30, CX-60, CX-80…) are **not** covered: their "
        "cloud requires an anti-bot protection that GdAAuto does not bypass."
    ),
}

VUOLE_PIN = True
#: Lo stato è l'ultimo dato che l'auto ha mandato al cloud: non la sveglia, ma si resta prudenti.
INTERVALLO_MINUTI = 5
#: in marcia o in carica, ogni due minuti
INTERVALLO_IN_MOTO_SECONDI = 120

CONF_EMAIL = "email"
CONF_TOKEN = "token"

#: Dove sta la chiave pubblica del cloud (GdAAuto non la contiene).
CARTELLA = "gdaauto"
NOME_FILE = "mazda_6e_chiave_cloud.txt"
#: Il file di fano0001/home-assistant-mazda-6e che la contiene, a un commit fisso, e la sua impronta.
URL_CHIAVE = (
    "https://raw.githubusercontent.com/fano0001/home-assistant-mazda-6e/9e7b7dc2f928908f829607f9ec6ad49ceddd4ad3"
    "/custom_components/mazda_6e/credential_crypto.py"
)
SHA256_FILE = "77739404c32fb299fd225762203f4340926551035a232161c16165fe2826ab61"
_CHIAVE_NEL_FILE = re.compile(r"SERVER_PUBLIC_KEY\s*=\s*['\"]([A-Za-z0-9+/=\s]+)['\"]")


def _leggi(percorso: Path) -> str:
    """La chiave salvata, controllata. Bloccante: va nell'executor."""
    try:
        chiave = "".join(percorso.read_text(encoding="ascii").split())
    except (OSError, UnicodeError) as err:
        raise ErroreCertificato(f"La chiave del cloud Mazda non si legge: {err}") from err
    cripto.carica_pubblica(chiave)
    return chiave


def _scrivi(percorso: Path, chiave: str) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = percorso.with_suffix(".scarico")
    provvisorio.write_text(chiave + "\n", encoding="ascii")
    provvisorio.replace(percorso)


def chiave_dal_file(contenuto: bytes) -> str:
    """Controlla l'impronta del file scaricato e ne tira fuori la chiave."""
    if hashlib.sha256(contenuto).hexdigest() != SHA256_FILE:
        raise ErroreCertificato("Il file scaricato con la chiave del cloud Mazda non è quello atteso: non si usa")
    trovata = _CHIAVE_NEL_FILE.search(contenuto.decode("utf-8", "replace"))
    if not trovata:
        raise ErroreCertificato("Nel file scaricato non c'è la chiave del cloud Mazda")
    chiave = "".join(trovata.group(1).split())
    cripto.carica_pubblica(chiave)
    return chiave


async def _scarica(hass: HomeAssistant, percorso: Path) -> str:
    try:
        async with async_get_clientsession(hass).get(URL_CHIAVE, timeout=aiohttp.ClientTimeout(total=60)) as r:
            r.raise_for_status()
            contenuto = await r.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ErroreCertificato(
            f"La chiave del cloud Mazda non si scarica ({err}): riprova, o mettila in /config/gdaauto/{NOME_FILE}"
        ) from err
    chiave = chiave_dal_file(contenuto)
    await hass.async_add_executor_job(_scrivi, percorso, chiave)
    return chiave


async def chiave_cloud(hass: HomeAssistant) -> str:
    """La chiave pubblica del cloud: quella salvata, o scaricata una volta."""
    percorso = Path(hass.config.path(CARTELLA)) / NOME_FILE
    if await hass.async_add_executor_job(percorso.is_file):
        return await hass.async_add_executor_job(_leggi, percorso)
    return await _scarica(hass, percorso)


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero (col codice dell'email, se serve) ed elenca le auto."""
    chiave = await chiave_cloud(hass)
    email = str(dati[CONF_EMAIL]).strip()
    http = async_get_clientsession(hass)
    base = str(dati.get("base_url") or BASE_URL)
    # Al rientro si tiene lo stesso «telefono»: Mazda non rimanda il codice.
    dispositivo = str((dati.get(CONF_TOKEN) or {}).get("dispositivo") or "")
    token = await entra(
        http,
        email,
        str(dati["password"]),
        chiave_server=chiave,
        codice=str(dati.get("codice") or ""),
        dispositivo=dispositivo,
        base=base,
        esecutore=hass.async_add_executor_job,
    )
    cliente = ClienteMazda(http, token, chiave_server=chiave, base_url=base, esecutore=hass.async_add_executor_job)
    vetture = await cliente.vetture()
    da_salvare: dict[str, Any] = {CONF_EMAIL: email, CONF_TOKEN: dict(cliente.token)}
    if pin := str(dati.get("pin") or "").strip():
        da_salvare["pin"] = pin
    if dati.get("base_url"):
        da_salvare["base_url"] = base
    return Accesso(dati=da_salvare, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, _cartella: str) -> ClienteMazda:
    chiave = await chiave_cloud(hass)

    @callback
    def salva(token: dict[str, Any]) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: token})

    return ClienteMazda(
        async_get_clientsession(hass),
        dict(entry.data.get(CONF_TOKEN) or {}),
        chiave_server=chiave,
        salva=salva,
        base_url=str(entry.data.get("base_url") or BASE_URL),
        esecutore=hass.async_add_executor_job,
    )


__all__ = [
    "ACCESSO",
    "BASE_URL",
    "CAMPI",
    "ID",
    "INTERVALLO_IN_MOTO_SECONDI",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteMazda",
    "chiave_cloud",
    "comandi_di",
    "crea_da_voce",
    "entra",
    "prova_accesso",
]
