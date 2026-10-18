"""Le costanti delle app europee di Kia, Hyundai e Genesis.

Sono quelle delle app ufficiali (Kia Connect, Hyundai Bluelink, Genesis
Connected Services, e la nuova «OneApp» con cui si entra): identificativi,
segreti pubblici dell'app, indirizzi. Vengono da
Hyundai-Kia-Connect/hyundai_kia_connect_api (``KiaUvoApiEU.py`` e
``cci_fingerprint.py``), licenza MIT: vedi RINGRAZIAMENTI.md.

Le chiavi della firma ``X-Fingerprint`` cambiano con le versioni dell'app:
quando il cloud comincia a rispondere 9036, vanno aggiornate qui.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass(frozen=True)
class AppMarchio:
    """Tutto quello che distingue un marchio dagli altri, nello stesso cloud."""

    nome: str
    #: il cloud delle auto (``/api/v1/spa/…``), con la porta
    api: str
    #: il login (IDPConnect)
    login: str
    #: il cloud della OneApp, dove si scambiano i token
    cci: str
    service_id: str
    service_secret: str
    app_id: str
    #: la chiave dello ``Stamp``, messa in XOR con «app_id:secondi»
    cfb: bytes
    push: str
    oneapp_client_id: str
    oneapp_redirect: str
    pacchetto: str
    nome_cliente: str
    #: le tre parti della chiave HMAC di ``X-Fingerprint``
    chiavi_impronta: tuple[str, str, str]


_HYUNDAI_IMPRONTA = ("OsZDfDuCyVX", "yfqjCoA2JbX", "2MrLpGwNBC")

MARCHI: dict[str, AppMarchio] = {
    "kia": AppMarchio(
        nome="Kia",
        api="https://prd.eu-ccapi.kia.com:8080",
        login="https://idpconnect-eu.kia.com",
        cci="https://cci-api-eu.kia.com",
        service_id="fdc85c00-0a2f-4c64-bcb4-2cfb1500730a",
        service_secret="secret",
        app_id="a2b8469b-30a3-4361-8e13-6fceea8fbe74",
        cfb=base64.b64decode("wLTVxwidmH8CfJYBWSnHD6E0huk0ozdiuygB4hLkM5XCgzAL1Dk5sE36d/bx5PFMbZs="),
        push="APNS",
        oneapp_client_id="01b36c86-79e8-486c-8009-15f2ad88d670",
        oneapp_redirect="https://oneapp.kia.com/redirect",
        pacchetto="com.kia.oneapp.eu",
        nome_cliente="kia",
        chiavi_impronta=("yb4Z1n2nlKe", "ukNilE7QEdb", "KB34nStPVa"),
    ),
    "hyundai": AppMarchio(
        nome="Hyundai",
        api="https://prd.eu-ccapi.hyundai.com:8080",
        login="https://idpconnect-eu.hyundai.com",
        cci="https://cci-api-eu.hyundai.com",
        service_id="6d477c38-3ca4-4cf3-9557-2a1929a94654",
        service_secret="KUy49XxPzLpLuoK0xhBC77W6VXhmtQR9iQhmIFjjoY4IpxsV",
        app_id="014d2225-8495-4735-812d-2616334fd15d",
        cfb=base64.b64decode("RFtoRq/vDXJmRndoZaZQyfOot7OrIqGVFj96iY2WL3yyH5Z/pUvlUhqmCxD2t+D65SQ="),
        push="GCM",
        oneapp_client_id="4f4953b5-02e1-4dbc-8599-87e983ee1be5",
        oneapp_redirect="https://oneapp.hyundai.com/redirect",
        pacchetto="com.hyundai.oneapp.eu",
        nome_cliente="hyundai",
        chiavi_impronta=_HYUNDAI_IMPRONTA,
    ),
    # Di Genesis non si conoscono ancora le chiavi della firma: si usano quelle
    # di Hyundai, come fa il progetto di riferimento, e il cloud può rifiutarle.
    "genesis": AppMarchio(
        nome="Genesis",
        api="https://prd-eu-ccapi.genesis.com:443",
        login="https://idpconnect-eu.genesis.com",
        cci="https://cci-api-eu.genesis.com",
        service_id="3020afa2-30ff-412a-aa51-d28fbe901e10",
        service_secret="FKDdlef2ffdleFEweELFKERiLER2FED21sDdwdgQz6hFESE3",
        app_id="f11f2b86-e0e7-4851-90df-5600b01d8b70",
        cfb=base64.b64decode("RFtoRq/vDXJmRndoZaZQyYo3/qFLtVReW8P7utRPcc0ZxOzOELm9mexvviBk/qqIp4A="),
        push="GCM",
        oneapp_client_id="50e3b8b0-ced5-43b7-8a42-f86ac92fe50e",
        oneapp_redirect="https://oneapp.genesis.com/redirect",
        pacchetto="com.genesis.oneapp.eu",
        nome_cliente="genesis",
        chiavi_impronta=_HYUNDAI_IMPRONTA,
    ),
}

#: L'app vecchia (quella delle chiamate al cloud delle auto).
USER_AGENT_APP = "okhttp/3.12.0"
#: Il browser con cui l'app apre la pagina del login.
USER_AGENT_LOGIN = (
    "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) AppleWebKit/535.19 "
    "(KHTML, like Gecko) Chrome/18.0.1025.166 Mobile Safari/535.19_CCS_APP_AOS"
)

# L'identità della OneApp Android che il cloud controlla insieme alla firma.
ONEAPP_VERSIONE = "1.4.0"
ONEAPP_SISTEMA = "android"
ONEAPP_VERSIONE_SISTEMA = "14"
ONEAPP_TELEFONO = "SM-S918B"
ONEAPP_NOTIFICHE = "FCM"
ONEAPP_USER_AGENT = "okhttp/4.12.0"

# L'identità della OneApp per i percorsi vecchi (senza firma), che restano di riserva.
CCI_VERSIONE = "1.3.3"

LINGUE = ("en", "de", "fr", "it", "es", "sv", "nl", "no", "cs", "sk", "hu", "da", "pl", "fi", "pt")
