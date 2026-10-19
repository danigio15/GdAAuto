"""Tutti i marchi di auto elettriche, e da dove entra ognuno.

Chi configura sceglie il marchio. Se GdAAuto lo sa leggere dal suo cloud
(``nativo``), parte la configurazione di quella marca, con quello che è già
noto del marchio (``preimpostati``: l'app Stellantis, il marchio dentro
Hyundai-Kia…). Se no, GdAAuto dice quale integrazione di Home Assistant
installare (``ha``, ``link``) e poi legge l'auto da lì.

Un marchio che punta a una marca nativa non ancora presente in ``marche/``
passa da solo alla strada di Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HACS = "https://hacs.xyz"
HA = "https://www.home-assistant.io/integrations/"


@dataclass(frozen=True)
class Marchio:
    id: str
    nome: str
    nativo: str | None = None
    preimpostati: dict[str, Any] = field(default_factory=dict)
    #: l'integrazione di Home Assistant da usare quando non c'è (o non va) quella nativa
    ha: str = ""
    link: str = ""


_U = "Uconnect (hass-uconnect, HACS)"
_U_LINK = "https://github.com/hass-uconnect/hass-uconnect"
_VW = "l'integrazione del gruppo Volkswagen per la tua marca (HACS), o il portale EU Data Act"
_NESSUNA = "nessuna, per ora: il costruttore non ha un'API aperta"

MARCHI: tuple[Marchio, ...] = (
    Marchio("abarth", "Abarth", "uconnect", {"marchio": "fiat"}, _U, _U_LINK),
    Marchio("aiways", "Aiways", ha=_NESSUNA),
    Marchio("alfa_romeo", "Alfa Romeo", "uconnect", {"marchio": "alfaromeo"}, _U, _U_LINK),
    Marchio("alpine", "Alpine", "renault", {"marchio": "alpine"}, "Renault", HA + "renault"),
    Marchio(
        "audi", "Audi", "audi", {}, "Audi Connect (audiconnect, HACS)", "https://github.com/audiconnect/audi_connect_ha"
    ),
    Marchio(
        "bmw",
        "BMW",
        "bmw",
        {"marchio": "bmw"},
        "BMW CarData (BavarianData, HACS)",
        "https://github.com/JustChr/BavarianData",
    ),
    Marchio(
        # BYD non è nativa: il suo cloud si apre solo con tabelle estratte
        # dall'app, che nessuno può ridistribuire. Si passa dalla comunità.
        "byd",
        "BYD",
        ha="BYD Vehicle (hass-byd-vehicle, HACS)",
        link="https://github.com/jkaberg/hass-byd-vehicle",
    ),
    Marchio(
        "citroen",
        "Citroën",
        "stellantis",
        {"app": "MyCitroen"},
        "Stellantis Vehicles (HACS)",
        "https://github.com/andreadegiovine/homeassistant-stellantis-vehicles",
    ),
    Marchio("cupra", "Cupra", ha=_VW),
    Marchio("dacia", "Dacia", "renault", {"marchio": "dacia"}, "Renault", HA + "renault"),
    Marchio(
        "ds",
        "DS",
        "stellantis",
        {"app": "MyDS"},
        "Stellantis Vehicles (HACS)",
        "https://github.com/andreadegiovine/homeassistant-stellantis-vehicles",
    ),
    Marchio("fiat", "Fiat", "uconnect", {"marchio": "fiat"}, _U, _U_LINK),
    Marchio("ford", "Ford", "ford", {}, "FordPass (HACS)", "https://github.com/marq24/ha-fordpass"),
    Marchio(
        "genesis",
        "Genesis",
        "hyundai_kia",
        {"marchio": "genesis"},
        "Kia Uvo / Hyundai Bluelink (HACS)",
        "https://github.com/Hyundai-Kia-Connect/kia_uvo",
    ),
    Marchio("honda", "Honda", ha=_NESSUNA),
    Marchio(
        "hyundai",
        "Hyundai",
        "hyundai_kia",
        {"marchio": "hyundai"},
        "Kia Uvo / Hyundai Bluelink (HACS)",
        "https://github.com/Hyundai-Kia-Connect/kia_uvo",
    ),
    Marchio(
        "jaguar",
        "Jaguar",
        "jlr",
        {"marchio": "jaguar"},
        "Jaguar Land Rover InControl (HACS)",
        "https://github.com/msp1974/homeassistant-jlrincontrol",
    ),
    Marchio("jeep", "Jeep", "uconnect", {"marchio": "jeep"}, _U, _U_LINK),
    Marchio(
        "kia",
        "Kia",
        "hyundai_kia",
        {"marchio": "kia"},
        "Kia Uvo / Hyundai Bluelink (HACS)",
        "https://github.com/Hyundai-Kia-Connect/kia_uvo",
    ),
    Marchio("lancia", "Lancia", "uconnect", {"marchio": "lancia"}, _U, _U_LINK),
    Marchio(
        "land_rover",
        "Land Rover",
        "jlr",
        {"marchio": "land_rover"},
        "Jaguar Land Rover InControl (HACS)",
        "https://github.com/msp1974/homeassistant-jlrincontrol",
    ),
    Marchio(
        "leapmotor",
        "Leapmotor",
        "leapmotor",
        {},
        "Leapmotor (leapmotor-ha, HACS)",
        "https://github.com/kerniger/leapmotor-ha",
    ),
    Marchio(
        "lexus",
        "Lexus",
        "toyota",
        {"marchio": "lexus"},
        "Toyota (ha_toyota, HACS)",
        "https://github.com/pytoyoda/ha_toyota",
    ),
    Marchio("lotus", "Lotus", ha=_NESSUNA),
    Marchio("lucid", "Lucid", ha="Lucid Motors (HACS)", link=HACS),
    Marchio("lynk_co", "Lynk & Co", ha=_NESSUNA),
    Marchio("maserati", "Maserati", "uconnect", {"marchio": "maserati"}, _U, _U_LINK),
    Marchio("mazda", "Mazda", ha=_NESSUNA),
    Marchio(
        "mercedes",
        "Mercedes-Benz",
        "mercedes",
        {},
        "Mercedes-Benz (mbapi2020, HACS)",
        "https://github.com/ReneNulschDE/mbapi2020",
    ),
    Marchio("mg", "MG", "mg", {}, "MG / SAIC (mg-saic-ha, HACS)", "https://github.com/ad-ha/mg-saic-ha"),
    Marchio(
        "mini",
        "Mini",
        "bmw",
        {"marchio": "mini"},
        "BMW CarData (BavarianData, HACS)",
        "https://github.com/JustChr/BavarianData",
    ),
    Marchio("nio", "NIO", ha=_NESSUNA),
    Marchio(
        "nissan",
        "Nissan",
        "nissan",
        {},
        ha="Nissan Connect (HACS); per le Leaf prima del 2019 l'integrazione Nissan Leaf",
        link=HA + "nissan_leaf",
    ),
    Marchio(
        "opel",
        "Opel",
        "stellantis",
        {"app": "MyOpel"},
        "Stellantis Vehicles (HACS)",
        "https://github.com/andreadegiovine/homeassistant-stellantis-vehicles",
    ),
    Marchio(
        "peugeot",
        "Peugeot",
        "stellantis",
        {"app": "MyPeugeot"},
        "Stellantis Vehicles (HACS)",
        "https://github.com/andreadegiovine/homeassistant-stellantis-vehicles",
    ),
    Marchio(
        "polestar", "Polestar", "polestar", {}, "Polestar API (HACS)", "https://github.com/pypolestar/polestar_api"
    ),
    Marchio("porsche", "Porsche", "porsche", {}, "Porsche Connect (HACS)", "https://github.com/CJNE/ha-porscheconnect"),
    Marchio("renault", "Renault", "renault", {"marchio": "renault"}, "Renault", HA + "renault"),
    Marchio("rivian", "Rivian", ha="Rivian (HACS)", link="https://github.com/bretterer/home-assistant-rivian"),
    Marchio("seat", "Seat", ha=_VW),
    Marchio(
        "skoda",
        "Škoda",
        ha="MyŠkoda, o l'integrazione ufficiale Škoda di Home Assistant",
        link="https://github.com/skodaconnect/homeassistant-myskoda",
    ),
    Marchio("smart", "smart", ha="Hello smart (HACS) per #1 e #3; Mercedes-Benz (mbapi2020) per le EQ", link=HACS),
    Marchio("subaru", "Subaru", ha="Subaru (solo Stati Uniti)", link=HA + "subaru"),
    Marchio("tesla", "Tesla", "tesla", {}, "Tesla Fleet", HA + "tesla_fleet"),
    Marchio(
        "toyota",
        "Toyota",
        "toyota",
        {"marchio": "toyota"},
        "Toyota (ha_toyota, HACS)",
        "https://github.com/pytoyoda/ha_toyota",
    ),
    Marchio(
        "vauxhall",
        "Vauxhall",
        "stellantis",
        {"app": "MyVauxhall"},
        "Stellantis Vehicles (HACS)",
        "https://github.com/andreadegiovine/homeassistant-stellantis-vehicles",
    ),
    Marchio(
        "volkswagen",
        "Volkswagen",
        "volkswagen",
        {},
        "Volkswagen Connect (volkswagencarnet, HACS)",
        "https://github.com/robinostlund/homeassistant-volkswagencarnet",
    ),
    Marchio("volvo", "Volvo", "volvo", {}, "Volvo (integrazione ufficiale di Home Assistant)", HA + "volvo"),
    Marchio("xpeng", "Xpeng", ha=_NESSUNA),
    Marchio("zeekr", "Zeekr", ha=_NESSUNA),
)

PER_ID: dict[str, Marchio] = {m.id: m for m in MARCHI}

#: La scelta in fondo all'elenco: una marca che non c'è.
ALTRO = "altro"
