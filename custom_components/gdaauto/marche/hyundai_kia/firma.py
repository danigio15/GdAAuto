"""Le firme che il cloud europeo di Hyundai e Kia vuole su ogni richiesta.

Due, e tutte e due si calcolano senza chiedere niente a nessuno:

- lo ``Stamp`` delle chiamate al cloud delle auto: «app_id:secondi» in XOR
  con una chiave fissa dell'app, in base64. Cambia ogni secondo;
- la ``X-Fingerprint`` della OneApp, sugli scambi di token: un HMAC-SHA256
  dell'identità dell'app (id, nome, versione, sistema) più l'ora in
  millisecondi e il corpo della richiesta senza spazi. La chiave è
  l'identità seguita dalle tre parti segrete dell'app (in ``marchi.py``).

Il metodo viene da Hyundai-Kia-Connect/hyundai_kia_connect_api
(``ApiImplType1._get_stamp`` e ``cci_fingerprint.py``), licenza MIT.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import hmac
import json
import re
import uuid
from zoneinfo import ZoneInfo

from .marchi import (
    CCI_VERSIONE,
    ONEAPP_NOTIFICHE,
    ONEAPP_SISTEMA,
    ONEAPP_TELEFONO,
    ONEAPP_USER_AGENT,
    ONEAPP_VERSIONE,
    ONEAPP_VERSIONE_SISTEMA,
    USER_AGENT_APP,
    AppMarchio,
)

#: Il cloud europeo ragiona con l'ora di Berlino.
FUSO_CLOUD = ZoneInfo("Europe/Berlin")

_IDENTITA = ("client-id", "client-name", "client-version", "client-os-code")
#: 9035 manca la firma, 9036 è sbagliata, 9037 l'ora è fuori tempo.
CODICI_FIRMA_RIFIUTATA = frozenset({9035, 9036, 9037})


def timbro(app: AppMarchio, adesso: datetime | None = None) -> str:
    """Lo ``Stamp``: «app_id:secondi» in XOR con la chiave dell'app."""
    secondi = int((adesso or datetime.now(UTC)).timestamp())
    grezzo = f"{app.app_id}:{secondi}".encode()
    return base64.b64encode(bytes(a ^ b for a, b in zip(app.cfb, grezzo, strict=False))).decode()


def impronta(intestazioni: dict[str, str], chiavi: tuple[str, str, str], corpo: str = "") -> str:
    """La ``X-Fingerprint``: HMAC-SHA256 dell'identità, dell'ora e del corpo."""
    minuscole = {k.lower(): v for k, v in intestazioni.items()}
    identita = "".join(minuscole.get(nome, "") for nome in _IDENTITA)
    messaggio = (
        identita
        + minuscole.get("client-os-version", "")
        + minuscole.get("x-timestamp", "")
        # Come Java: si tolgono solo gli spazi ASCII.
        + re.sub(r"\s+", "", corpo or "", flags=re.ASCII)
    )
    chiave = identita + "".join(chiavi)
    return hmac.new(chiave.encode(), messaggio.encode(), hashlib.sha256).hexdigest()


def fuso_orario(adesso: datetime | None = None) -> str:
    """Lo scarto di Berlino da UTC adesso, come «+02:00»."""
    scarto = (adesso or datetime.now(UTC)).astimezone(FUSO_CLOUD).strftime("%z")
    return f"{scarto[:3]}:{scarto[3:]}" if scarto else "+00:00"


def intestazioni_firmate(
    app: AppMarchio, device_id: str, lingua: str, corpo: str = "", adesso: datetime | None = None
) -> dict[str, str]:
    """Le intestazioni della OneApp Android, con l'ora e la firma."""
    adesso = adesso or datetime.now(UTC)
    intestazioni = {
        "client-id": app.pacchetto,
        "client-name": f"{app.nome}EU",
        "client-version": ONEAPP_VERSIONE,
        "client-os-code": ONEAPP_SISTEMA,
        "client-os-version": ONEAPP_VERSIONE_SISTEMA,
        "client-device-id": device_id or "",
        "client-device-model": ONEAPP_TELEFONO,
        "client-notification-provider-type": ONEAPP_NOTIFICHE,
        "locale": lingua.upper(),
        "timezone": fuso_orario(adesso),
        "App-Request-Id": f"{uuid.uuid4().hex[:24].upper()}_{(device_id or '').replace('-', '')}_AOS",
        "X-Brand": app.nome,
        "X-Region": "eu",
        "User-Agent": ONEAPP_USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": lingua.lower(),
        "X-Timestamp": str(int(adesso.timestamp() * 1000)),
    }
    intestazioni["X-Fingerprint"] = impronta(intestazioni, app.chiavi_impronta, corpo)
    return intestazioni


def intestazioni_vecchie(app: AppMarchio, device_id: str, lingua: str) -> dict[str, str]:
    """Le intestazioni della OneApp iOS per i percorsi ``/domain/api`` senza firma.

    Sono la riserva: si usano solo quando il cloud rifiuta la firma.
    """
    kia = app.nome == "Kia"
    return {
        "client-id": app.pacchetto,
        "client-name": app.nome_cliente,
        "client-version": CCI_VERSIONE,
        "client-os-code": "ios",
        "client-os-version": "27" if kia else "18.7",
        "client-device-id": device_id or "",
        "client-device-model": "iPhone",
        "client-notification-provider-type": "IOS_APPSTORE" if kia else "APNS",
        "locale": lingua.upper(),
        "timezone": fuso_orario(),
        "Accept": "application/json",
        "Accept-Language": lingua,
        "User-Agent": USER_AGENT_APP,
    }


def firma_rifiutata(http: int, testo: str) -> bool:
    """Il cloud ha rifiutato la firma (e non, per esempio, le credenziali)."""
    if not 400 <= http < 500:
        return False
    try:
        corpo = json.loads(testo)
    except ValueError:
        return False
    return isinstance(corpo, dict) and corpo.get("code") in CODICI_FIRMA_RIFIUTATA
