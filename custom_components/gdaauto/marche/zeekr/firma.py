"""Le firme del cloud Zeekr, e le chiavi dell'app che servono a farle.

Il cloud Zeekr ha due porte, e ognuna vuole la sua firma:

- il **centro utenti** (login, profilo, codice TSP) vuole una HMAC-SHA256 con la
  coppia «access key / secret key» dell'app: si firma ``METODO``, percorso,
  parametri in ordine, access key e data, e a parte il corpo
  (``X-HMAC-SIGNATURE``, ``X-HMAC-DIGEST``, ``X-DATE``);
- il **gateway TSP** (auto, stato, comandi) vuole ``X-SIGNATURE``: una
  HMAC-SHA256, con il «prod secret» dell'app, delle intestazioni scelte, dei
  parametri, dell'MD5 del corpo JSON (con le chiavi in ordine), del metodo e del
  percorso. Il VIN viaggia cifrato in ``X-VIN`` (AES-CBC con chiave e IV
  dell'app).

La password si manda cifrata con la chiave pubblica RSA dell'app.

Le sei chiavi (access key, secret key, chiave pubblica, prod secret, chiave e IV
del VIN) stanno dentro l'app Zeekr e **non sono pubblicate** da nessuna parte con
un permesso di riuso: GdAAuto non le contiene. Chi configura le estrae dalla
propria app (per esempio con zeekr_key_extractor) e le incolla nel campo «Chiave
API», così come escono (``zeekr_secrets.json``) o come righe ``NOME=valore``.

L'algoritmo delle firme viene da zeekr_ev_api (Fryyyyy, licenza MIT) e da
OpenZeekr (borconi, licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import formatdate
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key, load_pem_public_key

from ..errori import ErroreCertificato

#: Le intestazioni che entrano nella firma del gateway TSP (zeekr_ev_api).
_FIRMATE = frozenset(
    {
        "x-app-id",
        "content-type",
        "x-api-signature-nonce",
        "x-timestamp",
        "x-api-signature-version",
        "x-project-id",
        "authorization",
        "accept-language",
        "x-vin",
        "x-device-id",
        "x-platform",
    }
)
#: Queste entrano solo se hanno un valore.
_SOLO_PIENE = frozenset({"x-vin", "authorization"})

#: I nomi delle sei chiavi, come li scrive zeekr_key_extractor.
NOMI_CHIAVI = ("hmac_access_key", "hmac_secret_key", "password_public_key", "prod_secret", "vin_key", "vin_iv")


@dataclass(frozen=True)
class ChiaviApp:
    """Le sei chiavi dell'app Zeekr, incollate da chi configura."""

    access_key: str
    secret_key: str
    chiave_pubblica: str
    prod_secret: str
    vin_key: str
    vin_iv: str


def chiavi_da(testo: str) -> ChiaviApp:
    """Le chiavi dal JSON di zeekr_key_extractor, o da righe ``NOME=valore`` (anche maiuscole)."""
    valori: dict[str, str] = {}
    testo = (testo or "").strip()
    try:
        letto = json.loads(testo) if testo.startswith("{") else None
    except ValueError as err:
        raise ErroreCertificato(f"Le chiavi Zeekr non sono un JSON valido: {err}") from err
    if isinstance(letto, dict):
        valori = {str(k).strip().lower(): str(v or "").strip() for k, v in letto.items()}
    else:
        for riga in testo.splitlines():
            riga = riga.strip()
            if not riga or riga.startswith("#") or ("=" not in riga and ":" not in riga):
                continue
            separatore = "=" if "=" in riga else ":"
            nome, valore = riga.split(separatore, 1)
            valori[nome.strip().strip('"').lower()] = valore.strip().strip('",')
    mancano = [n for n in NOMI_CHIAVI if not valori.get(n)]
    if mancano:
        raise ErroreCertificato(f"Mancano chiavi dell'app Zeekr: {', '.join(mancano)}")
    chiavi = ChiaviApp(*(valori[n] for n in NOMI_CHIAVI))
    if len(chiavi.vin_key.encode()) != 16 or len(chiavi.vin_iv.encode()) != 16:
        raise ErroreCertificato("vin_key e vin_iv dell'app Zeekr devono essere di 16 caratteri")
    chiave_pubblica(chiavi.chiave_pubblica)
    return chiavi


def chiave_pubblica(testo: str) -> RSAPublicKey:
    """La chiave pubblica RSA della password: base64 del DER, o un PEM."""
    try:
        if "BEGIN" in testo:
            chiave = load_pem_public_key(testo.encode())
        else:
            chiave = load_der_public_key(base64.b64decode("".join(testo.split())))
    except (ValueError, TypeError) as err:
        raise ErroreCertificato(f"La chiave pubblica dell'app Zeekr non si apre: {err}") from err
    if not isinstance(chiave, RSAPublicKey):
        raise ErroreCertificato("La chiave pubblica dell'app Zeekr non è una chiave RSA")
    return chiave


def cifra_password(password: str, chiave: str) -> str:
    """La password cifrata RSA (PKCS#1 v1.5), in base64, come la manda l'app."""
    cifrata = chiave_pubblica(chiave).encrypt(password.encode(), rsa_padding.PKCS1v15())
    return base64.b64encode(cifrata).decode()


def cifra_vin(vin: str, chiave: str, iv: str) -> str:
    """Il VIN per ``X-VIN``: AES-128-CBC con PKCS#7, chiave e IV presi come testo, in base64."""
    imbottito = padding.PKCS7(128).padder()
    dati = imbottito.update(vin.encode()) + imbottito.finalize()
    cifratore = Cipher(algorithms.AES(chiave.encode()), modes.CBC(iv.encode())).encryptor()
    return base64.b64encode(cifratore.update(dati) + cifratore.finalize()).decode()


def json_canonico(corpo: Any) -> str:
    """Il corpo come lo firma il gateway: chiavi in ordine, senza spazi. Si manda così com'è."""
    return json.dumps(corpo, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _b64_hmac(chiave: str, testo: str) -> str:
    return base64.b64encode(hmac.new(chiave.encode(), testo.encode(), hashlib.sha256).digest()).decode()


def _coppie(query: str) -> list[tuple[str, str]]:
    coppie = []
    for parte in query.split("&"):
        if "=" in parte:
            nome, valore = parte.split("=", 1)
            coppie.append((nome, valore))
    return coppie


# --- il centro utenti ---------------------------------------------------------------------


def firma_centro_utenti(
    metodo: str, url: str, corpo: str | None, access_key: str, secret_key: str, data: str | None = None
) -> dict[str, str]:
    """Le intestazioni ``X-HMAC-*`` di una richiesta al centro utenti."""
    parti = urlsplit(url)
    segmenti = [s for s in parti.path.strip("/").split("/") if s]
    percorso = "/" + "/".join(segmenti) if segmenti else "/"
    parametri = dict(_coppie(parti.query))
    query = "&".join(f"{k}={parametri[k]}" for k in sorted(parametri, key=str.lower))
    data = data or formatdate(usegmt=True)
    da_firmare = "\n".join((metodo.upper(), percorso, query, access_key, data)) + "\n"
    return {
        "X-HMAC-ALGORITHM": "hmac-sha256",
        "X-HMAC-SIGNATURE": _b64_hmac(secret_key, da_firmare),
        "X-HMAC-ACCESS-KEY": access_key,
        "X-HMAC-DIGEST": _b64_hmac(secret_key, corpo or ""),
        "X-DATE": data,
    }


# --- il gateway TSP ---------------------------------------------------------------------------


def testo_da_firmare_tsp(metodo: str, url: str, intestazioni: Mapping[str, str], corpo: str | None) -> str:
    """Il testo che il gateway TSP ricostruisce e firma (zeekr_ev_api, ``calculate_sig``)."""
    righe = sorted(
        (k.lower(), v) for k, v in intestazioni.items() if k.lower() in _FIRMATE and (k.lower() not in _SOLO_PIENE or v)
    )
    testo = "".join(f"{k}:{v}\n" for k, v in righe)
    parti = urlsplit(url)
    if parti.query:
        coppie = sorted(dict(_coppie(parti.query)).items())
        query = "&".join(f"{k}={v.replace('%2F', '/').replace('%3F', '?').replace('*', '%2A')}" for k, v in coppie)
        if query:
            testo += query + "\n"
    tipo = next((v for k, v in intestazioni.items() if k.lower() == "content-type"), "")
    if corpo and "application/json" in tipo.lower():
        md5 = hashlib.md5(json_canonico(json.loads(corpo)).encode(), usedforsecurity=False).digest()
        testo += base64.b64encode(md5).decode() + "\n"
    return testo + metodo.upper() + "\n" + parti.path.rstrip()


def firma_tsp(metodo: str, url: str, intestazioni: dict[str, str], corpo: str | None, segreto: str) -> dict[str, str]:
    """Aggiunge nonce, ora e ``X-SIGNATURE`` alle intestazioni di una richiesta al gateway."""
    intestazioni = dict(intestazioni)
    intestazioni.setdefault("X-API-SIGNATURE-NONCE", str(uuid.uuid4()))
    intestazioni.setdefault("X-TIMESTAMP", str(time.time_ns() // 1_000_000))
    intestazioni["X-SIGNATURE"] = _b64_hmac(segreto, testo_da_firmare_tsp(metodo, url, intestazioni, corpo))
    return intestazioni
