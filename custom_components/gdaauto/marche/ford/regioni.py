"""Le regioni di FordPass: dove si entra e con quale app.

Il login di Ford è un Azure AD B2C diverso per paese (indirizzo e lingua),
e ogni regione ha il suo ``Application-Id`` dell'app. La tabella viene da
marq24/ha-fordpass (``const.py``), licenza Apache 2.0, © 2025 Matthias
Marquardt: vedi RINGRAZIAMENTI.md. Qui è riscritta coi nomi di GdAAuto, senza
le chiavi vecchie che l'app non usa più.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Il tenant B2C di Ford e il client OAuth dell'app FordPass (uguali in tutto il mondo).
OAUTH_ID = "4566605f-43a7-400a-946e-89cc9fdb0bd7"
CLIENT_ID = "09852200-05fd-41f6-8c21-d36d3497dc64"

#: L'Application-Id dell'app, per continente.
_APP = {
    "africa": "71AA9ED7-B26B-4C15-835E-9F35CC238561",
    "asia_pacifico": "39CD6590-B1B9-42CB-BEF9-0DC1FDB96260",
    "europa": "667D773E-1BDC-4139-8AD0-2B16474E8DC7",
    "nord_america": "BFE8C5ED-D687-4C19-A5DD-F92CDFC4503A",
    "sud_america": "C1DFFEF5-5BA5-486A-9054-8B39A9DF9AFC",
    "lincoln": "45133B88-0671-4AAF-B8D1-99E684ED4E45",
}

_LOGIN = "https://login.ford.com"


@dataclass(frozen=True)
class Regione:
    nome: str
    app_id: str
    lingua: str
    paese: str
    login: str = _LOGIN
    #: ``B2C_1A_SignInSignUp_<aggiunta><lingua>``: Lincoln ha il suo flusso
    aggiunta: str = ""
    #: dove il login rimanda alla fine (l'app, che nel browser non si apre)
    schema: str = "fordapp"


REGIONI: dict[str, Regione] = {
    "ita": Regione("Italia", _APP["europa"], "it-IT", "ITA"),
    "deu": Regione("Deutschland", _APP["europa"], "de-DE", "DEU", "https://login.ford.de"),
    "fra": Regione("France", _APP["europa"], "fr-FR", "FRA"),
    "esp": Regione("España", _APP["europa"], "es-ES", "ESP"),
    "prt": Regione("Portugal", _APP["europa"], "pt-PT", "PRT"),
    "nld": Regione("Nederland", _APP["europa"], "nl-NL", "NLD"),
    "dnk": Regione("Danmark", _APP["europa"], "da-DK", "DNK"),
    "nor": Regione("Norge", _APP["europa"], "no-NO", "NOR", "https://login.ford.no"),
    "pol": Regione("Polska", _APP["europa"], "pl-PL", "POL", "https://login.ford.pl"),
    "gbr": Regione("United Kingdom", _APP["europa"], "en-GB", "GBR", "https://login.ford.co.uk"),
    "rest_of_europe": Regione("Europa (altri paesi) / Rest of Europe", _APP["europa"], "en-GB", "GBR"),
    "usa": Regione("United States", _APP["nord_america"], "en-US", "USA"),
    "can": Regione("Canada", _APP["nord_america"], "en-CA", "CAN"),
    "mex": Regione("México", _APP["nord_america"], "es-MX", "MEX"),
    "aus": Regione("Australia", _APP["asia_pacifico"], "en-AU", "AUS"),
    "nzl": Regione("New Zealand", _APP["asia_pacifico"], "en-NZ", "NZL"),
    "zaf": Regione("South Africa", _APP["africa"], "en-ZA", "ZAF"),
    "bra": Regione("Brasil", _APP["sud_america"], "pt-BR", "BRA"),
    "arg": Regione("Argentina", _APP["sud_america"], "es-AR", "ARG"),
    "rest_of_world": Regione("Resto del mondo / Rest of the world", _APP["nord_america"], "en-US", "USA"),
}

#: Lincoln (solo Stati Uniti): stesso cloud, un altro login e un altro schema dell'app.
LINCOLN = Regione(
    "Lincoln (USA)",
    _APP["lincoln"],
    "en-US",
    "USA",
    "https://login.lincoln.com",
    aggiunta="Lincoln_",
    schema="lincolnapp",
)

PREDEFINITA = "ita"


def regione_di(paese: str | None, marchio: str | None = None) -> Regione:
    """La regione salvata, o quella predefinita se è sconosciuta."""
    if (marchio or "").lower() == "lincoln":
        return LINCOLN
    return REGIONI.get(str(paese or "").lower(), REGIONI[PREDEFINITA])
