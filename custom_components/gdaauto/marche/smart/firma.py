"""La firma delle richieste al cloud Geely di smart (``api.ecloudeu.com``).

Ogni richiesta porta un HMAC-SHA1 di quello che chiede: il tipo di risposta,
un nonce, i parametri, l'MD5 del corpo, l'ora e il percorso. Il segreto è
quello dell'app Hello smart europea, lo stesso per tutti.

Il modo di firmare e il segreto vengono da pySmartHashtag (DasBasti, licenza
MIT): vedi RINGRAZIAMENTI.md. Qui sono riscritti da capo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote

#: Il segreto della firma dell'app Hello smart (Europa), da pySmartHashtag (MIT).
SEGRETO = base64.b64decode("NzRlNzQ2OWFmZjUwNDJiYmJlZDdiYmIxYjM2YzE1ZTk=")

#: Dove il VIN nelle intestazioni non va: il VIN viaggia già nel corpo.
_SENZA_VIN = ("/user/session/update", "/geelyTCAccess/tcservices/capability/")

#: L'app iOS di Hello smart: il cloud vuole vedere questa.
AGENTE_APP = "Hello smart/1.4.0 (iPhone; iOS 17.1; Scale/3.00)"


def unisci_parametri(parametri: dict[str, object]) -> str:
    """I parametri come li mette l'app, nell'ordine dato e senza ricodificarli."""
    return "&".join(f"{k}={v}" for k, v in parametri.items())


def adesso_ms() -> str:
    return str(int(time.time() * 1000))


def firma(
    nonce: str, parametri: dict[str, object], ora: str, metodo: str, percorso: str, corpo: str | None = None
) -> str:
    """L'HMAC-SHA1, in base64, della richiesta."""
    md5 = base64.b64encode(hashlib.md5((corpo or "").encode()).digest()).decode()
    testo = (
        "application/json;responseformat=3\n"
        f"x-api-signature-nonce:{nonce}\n"
        "x-api-signature-version:1.0\n"
        "\n"
        f"{unisci_parametri(parametri)}\n"
        f"{md5}\n"
        f"{ora}\n"
        f"{metodo}\n"
        f"{percorso}"
    )
    return base64.b64encode(hmac.new(SEGRETO, testo.encode(), hashlib.sha1).digest()).decode()


def intestazioni(
    dispositivo: str,
    token: str | None,
    parametri: dict[str, object],
    metodo: str,
    percorso: str,
    corpo: str | None = None,
    vin: str | None = None,
    codice_modello: str | None = None,
) -> dict[str, str]:
    """Le intestazioni dell'app, firmate.

    Con il VIN e il ``matCode`` dell'auto si legano anche le intestazioni
    ``X-Vehicle-*``: così il cloud non dipende dall'«auto attiva» del conto,
    che un altro cliente (l'app sul telefono) può cambiare.
    """
    ora = adesso_ms()
    nonce = secrets.token_hex(8)
    h = {
        "x-app-id": "SmartAPPEU",
        "accept": "application/json;responseformat=3",
        "x-agent-type": "iOS",
        "x-device-type": "mobile",
        "x-operator-code": "SMART",
        "x-device-identifier": dispositivo,
        "x-env-type": "production",
        "x-version": "smartNew",
        "accept-language": "en_US",
        "x-api-signature-version": "1.0",
        "x-api-signature-nonce": nonce,
        "x-device-manufacture": "Apple",
        "x-device-brand": "Apple",
        "x-device-model": "iPhone",
        "x-agent-version": "17.1",
        "content-type": "application/json; charset=utf-8",
        "user-agent": AGENTE_APP,
        "x-signature": firma(nonce, parametri, ora, metodo, percorso, corpo),
        "x-timestamp": ora,
    }
    if token:
        h["authorization"] = token
    if vin and codice_modello and not any(p in percorso for p in _SENZA_VIN):
        codificato = quote(codice_modello, safe="")
        h["X-Vehicle-IDENTIFIER"] = vin
        h["X-VEHICLE-SERIES"] = codificato
        h["X-VEHICLE-MODEL"] = codificato
    return h
