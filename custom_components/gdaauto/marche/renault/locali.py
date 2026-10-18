"""I paesi dell'app MyRenault, e a quali server parla ognuno.

L'app sceglie i server dal paese (la «locale»): quasi tutta l'Europa usa gli
stessi, il Lussemburgo ha una sua chiave Gigya, Messico e Australia hanno
server propri. Le chiavi sono quelle pubbliche dell'app, prese da
hacf-fr/renault-api (``src/renault_api/const.py``, licenza MIT, Copyright (c)
2020 epenet): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from typing import TypedDict

PAESE_PREDEFINITO = "it_IT"


class Server(TypedDict):
    gigya_url: str
    gigya_chiave: str
    kamereon_url: str
    kamereon_chiave: str


_GIGYA_CHIAVE_EU = "3_VgdkgtIRH3AdHvJm-cjV2ug2EFE0lxt0IJzMC4MFqZjFpn_GYFXVdNZ19L7wZX0N"
_GIGYA_URL_AU = "https://gigya-prod-au1.renaultgroup.com"
_GIGYA_URL_EU = "https://accounts.eu1.gigya.com"
_GIGYA_URL_US = "https://accounts.us1.gigya.com"
_KAMEREON_CHIAVE = "YjkKtHmGfaceeuExUDKGxrLZGGvtVS0J"
_KAMEREON_URL_AU = "https://api-wired-prod-1-apn1.wrd-aws.com"
_KAMEREON_URL_EU = "https://api-wired-prod-1-euw1.wrd-aws.com"
_KAMEREON_URL_US = "https://api-wired-prod-1-usw2.wrd-aws.com"

_EU = Server(
    gigya_url=_GIGYA_URL_EU,
    gigya_chiave=_GIGYA_CHIAVE_EU,
    kamereon_url=_KAMEREON_URL_EU,
    kamereon_chiave=_KAMEREON_CHIAVE,
)

#: locale → (nome del paese, i suoi server)
LOCALI: dict[str, tuple[str, Server]] = {
    "bg_BG": ("България", _EU),
    "cs_CZ": ("Česko", _EU),
    "da_DK": ("Danmark", _EU),
    "de_DE": ("Deutschland", _EU),
    "de_AT": ("Österreich", _EU),
    "de_CH": ("Schweiz (Deutsch)", _EU),
    "en_AU": (
        "Australia",
        Server(
            gigya_url=_GIGYA_URL_AU,
            gigya_chiave="4_GC9iGd3sH8sMJpz2x0q0CQ",
            kamereon_url=_KAMEREON_URL_AU,
            kamereon_chiave=_KAMEREON_CHIAVE,
        ),
    ),
    "en_GB": ("United Kingdom", _EU),
    "en_IE": ("Ireland", _EU),
    "es_ES": ("España", _EU),
    "es_MX": (
        "México",
        Server(
            gigya_url=_GIGYA_URL_US,
            gigya_chiave="4_yTFqPSsGxVyRXPZUM7t1Iw",
            kamereon_url=_KAMEREON_URL_US,
            kamereon_chiave=_KAMEREON_CHIAVE,
        ),
    ),
    "fi_FI": ("Suomi", _EU),
    "fr_FR": ("France", _EU),
    "fr_BE": ("Belgique (français)", _EU),
    "fr_CH": ("Suisse (français)", _EU),
    "fr_LU": (
        "Luxembourg",
        Server(
            gigya_url=_GIGYA_URL_EU,
            gigya_chiave="3_zt44Wl_wT9mnqn-BHrR19PvXj3wYRPQKLcPbGWawlatFR837KdxSZZStbBTDaqnb",
            kamereon_url=_KAMEREON_URL_EU,
            kamereon_chiave=_KAMEREON_CHIAVE,
        ),
    ),
    "hr_HR": ("Hrvatska", _EU),
    "hu_HU": ("Magyarország", _EU),
    "it_IT": ("Italia", _EU),
    "it_CH": ("Svizzera (italiano)", _EU),
    "nl_NL": ("Nederland", _EU),
    "nl_BE": ("België (Nederlands)", _EU),
    "no_NO": ("Norge", _EU),
    "pl_PL": ("Polska", _EU),
    "pt_PT": ("Portugal", _EU),
    "ro_RO": ("România", _EU),
    "ru_RU": ("Россия", _EU),
    "sk_SK": ("Slovensko", _EU),
    "sl_SI": ("Slovenija", _EU),
    "sv_SE": ("Sverige", _EU),
}

#: per la tendina del paese: (locale, «Italia · it_IT»)
SCELTE_PAESE: tuple[tuple[str, str], ...] = tuple(
    sorted(((codice, f"{nome} · {codice}") for codice, (nome, _s) in LOCALI.items()), key=lambda c: c[1])
)


def server_di(paese: str) -> Server:
    """I server di quel paese; un paese sconosciuto va su quelli europei."""
    voce = LOCALI.get(paese)
    return voce[1] if voce else _EU


def nazione(paese: str) -> str:
    """Il ``country`` che Kamereon vuole a ogni richiesta: «IT» da «it_IT»."""
    return (paese[-2:] if len(paese) >= 2 else "IT").upper()
