"""Leapmotor: la prima marca di GdAAuto.

Il cloud è quello dell'app ufficiale (``appgateway.leapmotor-international.de``),
parlato in mTLS col certificato dell'app. Vale per tutti i modelli venduti
fuori dalla Cina: T03, B05, B10, C10, C16 e quelli che verranno, perché un
modello sconosciuto ripiega sul formato della C10.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp

from .cliente import BASE_URL, ID, NOME, ClienteLeapmotor, comandi_di

# I campi che la configurazione chiede per questa marca. ``segreto`` dice se
# si scrive nascosto, ``lungo`` se è un testo su più righe (i PEM).
CAMPI: tuple[dict[str, Any], ...] = (
    {"nome": "email", "obbligatorio": True},
    {"nome": "password", "obbligatorio": True, "segreto": True},
    {"nome": "cert_app", "obbligatorio": False, "lungo": True},
    {"nome": "chiave_app", "obbligatorio": False, "lungo": True, "segreto": True},
)

# Il PIN per i comandi: lo chiede questa marca, non tutte.
VUOLE_PIN = True


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


__all__ = ["CAMPI", "ID", "NOME", "VUOLE_PIN", "ClienteLeapmotor", "comandi_di", "crea"]
