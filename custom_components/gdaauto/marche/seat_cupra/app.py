"""Le due app del cloud «OLA» di SEAT e CUPRA: chi sono quando parlano col cloud.

Il cloud è uno solo (``ola.prod.code.seat.cloud.vwgroup.com``), e il login è
quello del gruppo Volkswagen (``identity.vwgroup.io``), fatto da chi configura
nel suo browser. Cambiano il client OAuth dell'app (My SEAT o My CUPRA),
l'indirizzo di ritorno del login e le intestazioni con cui l'app si presenta.
I token si chiedono e si rinnovano allo stesso indirizzo di OLA
(``/authorization/api/v1/token``), per tutte e due le app (da ottobre 2026
anche CUPRA, come scrive pycupra).

Le costanti (client id, segreto pubblico dell'app CUPRA, versioni e
intestazioni) vengono da tillsteinbach/CarConnectivity-connector-seatcupra
(MIT) e da WulfgarW/pycupra (Apache 2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Il cloud delle auto, e il login del gruppo.
OLA = "https://ola.prod.code.seat.cloud.vwgroup.com"
IDENTITA = "https://identity.vwgroup.io"

#: Dove si scambia il codice del login, e si rinnovano i token.
PERCORSO_TOKEN = "/authorization/api/v1/token"

SCOPE = "openid profile nickname birthdate phone"


@dataclass(frozen=True)
class AppMarchio:
    """Un'app del marchio: il suo client OAuth e come si presenta al cloud."""

    marchio: str
    nome: str
    client_id: str
    redirect_uri: str
    versione: str
    nome_app: str
    user_agent: str
    #: solo l'app CUPRA ha un segreto (pubblico, è dentro l'app): lo manda nel rinnovo dei token
    client_secret: str = ""


APP: dict[str, AppMarchio] = {
    "cupra": AppMarchio(
        marchio="cupra",
        nome="CUPRA",
        client_id="3c756d46-f1ba-4d78-9f9a-cff0d5292d51@apps_vw-dilab_com",
        redirect_uri="cupra://oauth-callback",
        versione="2.20.3",
        nome_app="com.cupra.mycupra",
        user_agent="OLACupra/2.20.3 (Android 14; SM-G960F; samsung) Mobile",
        client_secret="eb8814e641c81a2640ad62eeccec11c98effc9bccd4269ab7af338b50a94b3a2",
    ),
    "seat": AppMarchio(
        marchio="seat",
        nome="SEAT",
        client_id="99a5b77d-bd88-4d53-b4e5-a539c60694a3@apps_vw-dilab_com",
        redirect_uri="seat://oauth-callback",
        versione="2.20.2",
        nome_app="com.seat.myseat",
        user_agent="OLASeat/2.20.2 (Android 14; SM-G960F; samsung) Mobile",
    ),
}

#: Le lingue che il cloud capisce, nella forma «it_IT».
LINGUE = {"it": "it_IT", "en": "en_GB", "de": "de_DE", "es": "es_ES", "fr": "fr_FR", "pt": "pt_PT", "nl": "nl_NL"}


def lingua_cloud(lingua: str | None) -> str:
    """La lingua di Home Assistant («it») in quella del cloud («it_IT»); l'inglese se non c'è."""
    return LINGUE.get((lingua or "en").lower()[:2], "en_GB")
