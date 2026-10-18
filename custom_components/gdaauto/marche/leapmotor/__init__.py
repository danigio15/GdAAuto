"""Leapmotor: la prima marca di GdAAuto.

Il cloud è quello dell'app ufficiale (``appgateway.leapmotor-international.de``),
parlato in mTLS col certificato dell'app. Vale per tutti i modelli venduti
fuori dalla Cina: T03, B05, B10, C10, C16 e quelli che verranno, perché un
modello sconosciuto ripiega sul formato della C10.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import Any
import uuid

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID
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

CERT_APP, CHIAVE_APP = "cert_app", "chiave_app"
#: Il certificato dell'app è uguale per tutti: se chi configura non lo dà, si scarica da qui.
#: GdAAuto non lo contiene: è di Leapmotor, estratto dall'app, e non ha una licenza per ridistribuirlo.
URL_CERT_PUBBLICO = "https://raw.githubusercontent.com/markoceri/leapmotor-certs/main/app.crt"
URL_CHIAVE_PUBBLICA = "https://raw.githubusercontent.com/markoceri/leapmotor-certs/main/app.key"
#: Il nome nel certificato dell'app: quello scaricato deve averlo.
CN_APP = "LeapmotorAppCrtCN"

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
        "Il certificato e la chiave dell'app puoi lasciarli vuoti: GdAAuto li scarica da sola dal repository "
        "pubblico markoceri/leapmotor-certs. Se preferisci i tuoi, caricali (PEM, DER o .p12) o incollane il testo."
    ),
    "en": (
        "Use a **dedicated** Leapmotor account with the car shared from your main account: the cloud keeps one "
        "session per account, and sharing it would log out the phone app. The Leapmotor app certificate and key "
        "can be left empty: GdAAuto downloads them from the public markoceri/leapmotor-certs repository. If you "
        "prefer your own, upload them (PEM, DER or .p12) or paste their text."
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


def controlla_pubblici(cert_pem: str, chiave_pem: str) -> None:
    """Il certificato scaricato è quello dell'app Leapmotor, valido, e la chiave è la sua."""
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    chiave = serialization.load_pem_private_key(chiave_pem.encode(), None)
    nomi = [a.value for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    if nomi != [CN_APP]:
        raise ErroreCertificato(f"Il certificato scaricato non è quello dell'app Leapmotor ({nomi})")
    if cert.not_valid_after_utc < datetime.now(UTC):
        raise ErroreCertificato(f"Il certificato pubblico dell'app è scaduto il {cert.not_valid_after_utc:%d/%m/%Y}")
    if chiave.public_key().public_numbers() != cert.public_key().public_numbers():  # type: ignore[union-attr]
        raise ErroreCertificato("La chiave scaricata non è quella del certificato")


async def scarica_pubblici(hass: HomeAssistant) -> dict[str, str]:
    """Il certificato e la chiave dell'app, uguali per tutti, dal repository pubblico che li tiene.

    GdAAuto non li contiene: li scarica qui, una volta, e li salva nella configurazione.
    """
    sessione = async_get_clientsession(hass)
    scaricati: dict[str, str] = {}
    try:
        for campo, url in ((CERT_APP, URL_CERT_PUBBLICO), (CHIAVE_APP, URL_CHIAVE_PUBBLICA)):
            async with sessione.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    raise ErroreCertificato(f"{url} risponde HTTP {r.status}")
                scaricati[campo] = in_pem(await r.read(), tipo_di(campo))
        controlla_pubblici(scaricati[CERT_APP], scaricati[CHIAVE_APP])
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ErroreCertificato(f"Il certificato pubblico dell'app non si scarica: {err}") from err
    except (FormatoSconosciuto, ValueError) as err:
        raise ErroreCertificato(f"Il certificato pubblico dell'app non si apre: {err}") from err
    return scaricati


def _mancano(dati: dict[str, Any]) -> bool:
    return not (dati.get(CERT_APP) or "").strip() or not (dati.get(CHIAVE_APP) or "").strip()


async def prova_accesso(hass: HomeAssistant, dati: dict[str, Any]) -> Accesso:
    """Entra davvero, in una cartella di prova che poi si butta.

    Il certificato dell'app viene, in quest'ordine, da quello che ha dato chi
    configura, da /config/gdaauto, o dal repository pubblico.
    """
    dati = await hass.async_add_executor_job(_pem_da_file, hass, dict(dati))
    if _mancano(dati):
        dati.update(await scarica_pubblici(hass))
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
