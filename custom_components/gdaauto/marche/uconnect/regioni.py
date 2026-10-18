"""Dove sta il cloud Uconnect, per ogni marchio e regione.

Ogni marchio ex-FCA ha il suo sito di login (Gigya, di SAP) con una chiave
pubblica dell'app; i dati delle auto stanno invece su due soli «canali»: uno
per l'Europa e l'Asia (``sdpr-01``, AWS ``eu-west-1``) e uno per le Americhe
(``sdpr-02``, AWS ``us-east-1``). I comandi col PIN passano da un terzo
servizio, ``mfa``, che consegna un token monouso.

Gli indirizzi, le chiavi pubbliche delle app e i codici di marchio vengono da
py-uconnect (hass-uconnect, licenza Apache 2.0) e dal client Go di evcc
(``vehicle/fiat``, licenza MIT): vedi RINGRAZIAMENTI.md. Sono fatti del
protocollo, gli stessi che le app mandano a ogni avvio.

Lancia non ha un sito suo fra quelli noti: si entra da quello di Fiat, e
l'elenco delle auto prova più codici di marchio.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

REGIONE_PREDEFINITA = "eu"

#: La tendina della regione: (valore, etichetta).
SCELTE_REGIONE: tuple[tuple[str, str], ...] = (
    ("eu", "Europa / Europe"),
    ("us", "America del Nord / North America"),
    ("asia", "Asia-Pacifico / Asia-Pacific"),
)

NOMI_MARCHI = {
    "fiat": "Fiat",
    "jeep": "Jeep",
    "alfaromeo": "Alfa Romeo",
    "lancia": "Lancia",
    "maserati": "Maserati",
}


@dataclass(frozen=True)
class Configurazione:
    """Tutto quello che serve per parlare con un marchio in una regione."""

    #: il sito Gigya del marchio, e la chiave pubblica della sua app
    login: str
    chiave_login: str
    #: dal JWT di Gigya all'identità Cognito
    token: str
    #: i canali con i dati delle auto, e la loro chiave
    api: str
    chiave_api: str
    #: il servizio del PIN, e la sua chiave
    pin: str
    chiave_pin: str
    #: la regione AWS: per la firma e per Cognito
    regione_aws: str
    locale: str
    #: i valori di ``brand`` da provare nell'elenco delle auto, in ordine
    codici_marchio: tuple[str, ...]
    #: Cognito sta su Amazon, nella stessa regione (si cambia solo nelle prove)
    url_cognito: str = ""

    @property
    def cognito(self) -> str:
        return self.url_cognito or f"https://cognito-identity.{self.regione_aws}.amazonaws.com/"


_EU = {
    "token": "https://authz.sdpr-01.fcagcv.com/v2/cognito/identity/token",
    "api": "https://channels.sdpr-01.fcagcv.com",
    "chiave_api": "2wGyL6PHec9o1UeLPYpoYa1SkEWqeBur9bLsi24i",
    "pin": "https://mfa.fcl-01.fcagcv.com",
    "chiave_pin": "JWRYW7IYhW9v0RqDghQSx4UcRYRILNmc8zAuh5ys",
    "regione_aws": "eu-west-1",
    "locale": "de_de",
}

_US = {
    "token": "https://authz.sdpr-02.fcagcv.com/v2/cognito/identity/token",
    "api": "https://channels.sdpr-02.fcagcv.com",
    "chiave_api": "OgNqp2eAv84oZvMrXPIzP8mR8a6d9bVm1aaH9LqU",
    "pin": "https://mfa.fcl-02.fcagcv.com",
    "chiave_pin": "fNQO6NjR1N6W0E5A6sTzR3YY4JGbuPv48Nj9aZci",
    "regione_aws": "us-east-1",
    "locale": "en_us",
    "codici_marchio": ("ALL",),
}


def _c(login: str, chiave_login: str, canale: dict, **altro: object) -> Configurazione:
    return Configurazione(login=login, chiave_login=chiave_login, **{**canale, **altro})  # type: ignore[arg-type]


#: (marchio, regione) → configurazione
CONFIGURAZIONI: dict[tuple[str, str], Configurazione] = {
    ("fiat", "eu"): _c(
        "https://loginmyuconnect.fiat.com",
        "3_mOx_J2dRgjXYCdyhchv3b5lhi54eBcdCTX4BI8MORqmZCoQWhA0mV2PTlptLGUQI",
        _EU,
        codici_marchio=("FIAT",),
    ),
    ("fiat", "us"): _c(
        "https://login-us.fiat.com",
        "3_WfFvlZJwcSdOD0LFQCngUV3W390R4Yshpuq3RsZvnV4VG0c9Q6R0RtDwcXc8dTrI",
        _US,
    ),
    ("fiat", "asia"): _c("https://login-iap.fiat.com", "4_YAQNaPqdPEUbbzhvhunKAA", _EU, codici_marchio=("FIAT",)),
    ("jeep", "eu"): _c(
        "https://login.jeep.com",
        "3_ZvJpoiZQ4jT5ACwouBG5D1seGEntHGhlL0JYlZNtj95yERzqpH4fFyIewVMmmK7j",
        _EU,
        codici_marchio=("REST",),
    ),
    ("jeep", "us"): _c(
        "https://login-us.jeep.com",
        "3_5qxvrevRPG7--nEXe6huWdVvF5kV7bmmJcyLdaTJ8A45XUYpaR398QNeHkd7EB1X",
        _US,
    ),
    ("jeep", "asia"): _c("https://login-iap.jeep.com", "4_zqGYHC7rM8RCHHl4YFDebA", _EU, codici_marchio=("REST",)),
    ("alfaromeo", "eu"): _c(
        "https://login.alfaromeo.com",
        "3_h8sj2VQI-KYXiunPq9a1QuAA4yWkY0r5AD1u8A8B1RPn_Cvl54xcoc2-InH5onJ1",
        _EU,
        codici_marchio=("ALFAROMEO",),
    ),
    ("alfaromeo", "us"): _c(
        "https://login-us.alfaromeo.com",
        "3_FSxGyaktviayTDRcgp9r9o2KjuFSrHT13wWNN9zPrvAGUCoXPDqoIPOwlBUhck4A",
        _US,
    ),
    ("alfaromeo", "asia"): _c(
        "https://login-iap.alfaromeo.com", "4_PSQeADnQ4p5XOaDgT0B5pA", _EU, codici_marchio=("ALFAROMEO",)
    ),
    ("maserati", "eu"): _c(
        "https://login.maserati.com",
        "3_rNbVuhn2gIt3BnLjlGsJcMo26Lft3avDne_FLRT34Dy_9OxHtCVOnplwY436lGZa",
        _EU,
        codici_marchio=("MASERATI",),
    ),
    ("maserati", "us"): _c(
        "https://login-us.maserati.com",
        "3_nShL4-O7IL0OGqroO8AzwiRU0-ZHcBZ4TLBrh5MORusMo5XYxhCLXPYfjI4OOLOy",
        _US,
    ),
    ("maserati", "asia"): _c(
        "https://accounts.au1.gigya.com", "4_uwF-in6KF-aMbEkPAb-fOg", _EU, codici_marchio=("MASERATI",)
    ),
}


def configurazione(marchio: str, regione: str) -> Configurazione:
    """La configurazione di un marchio in una regione. Lancia entra da Fiat;
    una regione che il marchio non ha ripiega sull'Europa."""
    marchio = (marchio or "fiat").lower().replace(" ", "").replace("_", "")
    regione = (regione or REGIONE_PREDEFINITA).lower()
    if marchio == "lancia":
        base = CONFIGURAZIONI.get(("fiat", regione)) or CONFIGURAZIONI[("fiat", REGIONE_PREDEFINITA)]
        if base.codici_marchio == ("ALL",):
            return base
        # Il codice dell'app Lancia non è noto: si prova il suo nome, poi quelli delle app sorelle.
        return replace(base, codici_marchio=("LANCIA", "REST", "FIAT"))
    if marchio not in NOMI_MARCHI:
        marchio = "fiat"
    return CONFIGURAZIONI.get((marchio, regione)) or CONFIGURAZIONI[(marchio, REGIONE_PREDEFINITA)]
