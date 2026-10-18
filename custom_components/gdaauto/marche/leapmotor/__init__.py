"""Leapmotor: la prima marca di GdAAuto.

Il cloud è quello dell'app ufficiale (``appgateway.leapmotor-international.de``),
parlato in mTLS col certificato dell'app. Vale per tutti i modelli venduti
fuori dalla Cina: T03, B05, B10, C10, C16 e quelli che verranno, perché un
modello sconosciuto ripiega sul formato della C10.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any
import uuid

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...pem import FormatoSconosciuto, in_pem, tipo_di
from ..base import ACCESSO_CAMPI, Accesso, Campo
from ..errori import ErroreCertificato
from .cliente import BASE_URL, ID, NOME, ClienteLeapmotor, comandi_di

#: Chi preferisce un file al copia-incolla mette i due PEM qui, in /config.
CARTELLA_PEM = "gdaauto"

ACCESSO = ACCESSO_CAMPI

#: PEM, DER o PKCS#12: GdAAuto li converte da sola.
_FILE_ACCETTATI = ".pem,.crt,.cer,.cert,.der,.key,.p12,.pfx"

CAMPI = (
    Campo("email"),
    Campo("password", segreto=True),
    Campo("cert_app", obbligatorio=False, lungo=True, carica=_FILE_ACCETTATI),
    Campo("chiave_app", obbligatorio=False, lungo=True, segreto=True, carica=_FILE_ACCETTATI),
)

NOTA = {
    "it": (
        "Usa un account Leapmotor **dedicato**, con l'auto condivisa dall'account principale: il cloud tiene "
        "una sessione sola per account, e con lo stesso account l'app sul telefono verrebbe disconnessa. "
        "Il certificato e la chiave dell'app Leapmotor non sono in GdAAuto: caricali dal dispositivo (PEM, DER "
        "o .p12), incollane il testo, oppure mettili come `app_cert.pem` e `app_key.pem` in `/config/gdaauto`."
    ),
    "en": (
        "Use a **dedicated** Leapmotor account with the car shared from your main account: the cloud keeps one "
        "session per account, and sharing it would log out the phone app. The Leapmotor app certificate and key "
        "are not shipped with GdAAuto: upload them from your device (PEM, DER or .p12), paste their text, or put "
        "them as `app_cert.pem` and `app_key.pem` in `/config/gdaauto`."
    ),
}

# Il PIN per i comandi: lo chiede questa marca, non tutte.
VUOLE_PIN = True
INTERVALLO_MINUTI = 5


def crea(sessione_http: aiohttp.ClientSession, dati: dict[str, Any], cartella: Path, lingua: str) -> ClienteLeapmotor:
    return ClienteLeapmotor(
        sessione_http,
        email=dati["email"],
        password=dati["password"],
        cert_app_pem=dati.get("cert_app") or "",
        chiave_app_pem=dati.get("chiave_app") or "",
        cartella=cartella,
        lingua=lingua,
        base_url=dati.get("base_url") or BASE_URL,
    )


def _pem_da_file(hass: HomeAssistant, dati: dict[str, Any]) -> dict[str, Any]:
    """Se i PEM non sono stati incollati, si cercano in /config/gdaauto (anche in DER o PKCS#12)."""
    cartella = Path(hass.config.path(CARTELLA_PEM))
    for campo, nome in (("cert_app", "app_cert.pem"), ("chiave_app", "app_key.pem")):
        if not (dati.get(campo) or "").strip() and (cartella / nome).is_file():
            try:
                dati[campo] = in_pem((cartella / nome).read_bytes(), tipo_di(campo))
            except FormatoSconosciuto as err:
                raise ErroreCertificato(f"/config/{CARTELLA_PEM}/{nome} non si apre: {err}") from err
    return dati


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero, in una cartella di prova che poi si butta."""
    dati = await hass.async_add_executor_job(_pem_da_file, hass, dict(dati))
    if not (dati.get("cert_app") or "").strip() or not (dati.get("chiave_app") or "").strip():
        raise ErroreCertificato("Servono il certificato e la chiave dell'app Leapmotor")
    cartella = Path(hass.config.path(".storage", "gdaauto", f"prova-{uuid.uuid4().hex}"))
    cliente = crea(async_get_clientsession(hass), dati, cartella, hass.config.language)
    try:
        await cliente.accedi()
        vetture = await cliente.vetture()
    finally:
        await hass.async_add_executor_job(shutil.rmtree, cartella, True)
    email = str(dati["email"]).strip()
    return Accesso(dati=dati, vetture=vetture, unique_id=f"{ID}_{email.lower()}", titolo=f"{NOME} · {email}")


async def crea_da_voce(hass: HomeAssistant, entry: ConfigEntry, cartella: str) -> ClienteLeapmotor:
    # La sessione di tutti va bene: il certificato si passa a ogni richiesta.
    return crea(async_get_clientsession(hass), dict(entry.data), Path(cartella), hass.config.language)


__all__ = [
    "ACCESSO",
    "CAMPI",
    "ID",
    "INTERVALLO_MINUTI",
    "NOME",
    "NOTA",
    "VUOLE_PIN",
    "ClienteLeapmotor",
    "comandi_di",
    "crea",
    "crea_da_voce",
    "prova_accesso",
]
