"""Le auto che ci sono già in Home Assistant.

Tesla, Stellantis, Kia e Hyundai, BMW, Renault, Volkswagen, Polestar: hanno
già un'integrazione, e non la rifacciamo. Si sceglie il dispositivo dell'auto,
GdAAuto propone da solo quale entità è la batteria, quale l'autonomia, quale
la serratura, e da lì in poi legge quelle e comanda attraverso quelle.

Il riconoscimento è una funzione pura su un elenco di ``Entita``: si prova a
tavolino con le entità di integrazioni vere, in ``tests/test_fonte_ha.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

# Le caselle che la mappa può riempire.
BATTERIA = "batteria"
AUTONOMIA = "autonomia"
ODOMETRO = "odometro"
VELOCITA = "velocita"
IN_CARICA = "in_carica"
CAVO = "cavo"
POTENZA_RICARICA = "potenza_ricarica"
TEMPERATURA_BATTERIA = "temperatura_batteria"
TEMPERATURA_INTERNA = "temperatura_interna"
TEMPERATURA_ESTERNA = "temperatura_esterna"
LIMITE = "limite"
SERRATURA = "serratura"
CLIMA = "clima"
INTERRUTTORE_RICARICA = "interruttore_ricarica"
TROVA = "trova"
POSIZIONE = "posizione"
TEMPO_RICARICA = "tempo_ricarica"

CASELLE = (
    BATTERIA,
    AUTONOMIA,
    ODOMETRO,
    VELOCITA,
    IN_CARICA,
    CAVO,
    POTENZA_RICARICA,
    TEMPO_RICARICA,
    TEMPERATURA_BATTERIA,
    TEMPERATURA_INTERNA,
    TEMPERATURA_ESTERNA,
    LIMITE,
    SERRATURA,
    CLIMA,
    INTERRUTTORE_RICARICA,
    TROVA,
    POSIZIONE,
)

# I domini che ogni casella accetta: servono anche ai selettori della configurazione.
DOMINI: dict[str, tuple[str, ...]] = {
    BATTERIA: ("sensor",),
    AUTONOMIA: ("sensor",),
    ODOMETRO: ("sensor",),
    VELOCITA: ("sensor",),
    IN_CARICA: ("binary_sensor", "sensor", "switch"),
    CAVO: ("binary_sensor", "sensor"),
    POTENZA_RICARICA: ("sensor",),
    TEMPO_RICARICA: ("sensor",),
    TEMPERATURA_BATTERIA: ("sensor",),
    TEMPERATURA_INTERNA: ("sensor",),
    TEMPERATURA_ESTERNA: ("sensor",),
    LIMITE: ("number", "select", "sensor"),
    SERRATURA: ("lock", "binary_sensor"),
    CLIMA: ("climate", "switch"),
    INTERRUTTORE_RICARICA: ("switch",),
    TROVA: ("button",),
    POSIZIONE: ("device_tracker",),
}


@dataclass(frozen=True)
class Entita:
    entity_id: str
    nome: str = ""
    unita: str | None = None
    classe: str | None = None
    categoria: str | None = None

    @property
    def dominio(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def testo(self) -> str:
        """Nome e id, minuscoli e senza accenti."""
        grezzo = f"{self.nome} {self.entity_id.split('.', 1)[-1].replace('_', ' ')}".lower()
        return "".join(c for c in unicodedata.normalize("NFKD", grezzo) if not unicodedata.combining(c))


def _c(*parole: str) -> re.Pattern[str]:
    return re.compile("|".join(parole))


_TEMPERATURE = {"°C", "°F", "K"}
_POTENZE = {"W", "kW"}
_DISTANZE = {"km", "mi", "m"}
_VELOCITA = {"km/h", "mph", "m/s"}
_TEMPI = {"min", "h", "s"}

# «Car battery» è come Kia e Hyundai chiamano la batteria da 12 V.
_NON_TRAZIONE = _c(
    r"12 ?v", r"\baux", "phone", "telefono", r"\bkey\b", "chiave", "remote", "tpms", "sensor battery", r"\bcar battery"
)
_OBIETTIVO = _c(
    "target", "limit", "limite", "obiettivo", r"\bmax", r"\bmin\b", "health", "salute", r"\bsoh\b", "capacity", "goal"
)
_CARICA = _c("charg", "ricarica", "carica", "lade", "recharge")
_CAVO = _c("plug", "cable", "cavo", "connected", "collegat", "connector", "angeschlossen")
_ESTERNA = _c("outside", "outdoor", "exterior", "external", "esterna", "ambient", "aussen", "exterieure")
_INTERNA = _c("inside", "interior", "indoor", "cabin", "interna", "abitacolo", "innen", "habitacle")


def _batteria(e: Entita) -> bool:
    if e.dominio != "sensor" or _NON_TRAZIONE.search(e.testo) or _OBIETTIVO.search(e.testo):
        return False
    if e.classe == "battery":
        return True
    return e.unita == "%" and bool(
        _c(
            r"\bsoc\b", "battery", "batteria", "state of charge", "ladestand", "akku", "batterie", "charge level"
        ).search(e.testo)
    )


def _autonomia(e: Entita) -> bool:
    return (
        e.dominio == "sensor"
        and e.unita in _DISTANZE
        and bool(_c("range", "autonom", "reichweite", "remaining", "distance to empty").search(e.testo))
        and not _c("target", r"\bmax", "full", "wltp", "fuel", "carburante", "benzina").search(e.testo)
    )


def _odometro(e: Entita) -> bool:
    return (
        e.dominio == "sensor"
        and e.unita in _DISTANZE
        and bool(
            _c(
                "odometer",
                "odometro",
                "contachilometri",
                "kilometerstand",
                "mileage",
                "total distance",
                "chilometraggio",
                "kilometrage",
            ).search(e.testo)
        )
    )


def _velocita(e: Entita) -> bool:
    return (
        e.dominio == "sensor"
        and (e.unita in _VELOCITA or e.classe == "speed")
        and bool(_c("speed", "velocita", "geschwindigkeit", "vitesse").search(e.testo))
        and not _c("wind", "vento", "fan", r"\bmax", "average", "media", "limit").search(e.testo)
    )


def _cavo(e: Entita) -> bool:
    if e.dominio == "binary_sensor" and e.classe == "plug":
        return True
    return e.dominio in ("binary_sensor", "sensor") and bool(_CAVO.search(e.testo)) and "door" not in e.testo


def _in_carica(e: Entita) -> bool:
    parole = _c("charging", "in carica", "in ricarica", r"\blad(t|en|evorgang)", "en charge", "is charging")
    if e.dominio == "binary_sensor":
        return e.classe == "battery_charging" or bool(parole.search(e.testo) and not _CAVO.search(e.testo))
    if e.dominio == "sensor":
        if _c("charging state", "charging status", "charge state", "stato ricarica", "stato della ricarica").search(
            e.testo
        ):
            return True
        # Tesla: un sensore «Charging» a stati (charging, complete, stopped…),
        # senza unità: non la potenza, non il tempo, non l'energia.
        return (
            not e.unita
            and e.classe in (None, "enum")
            and e.nome.strip().lower() in ("charging", "ricarica", "laden", "charge")
        )
    return False


def _potenza_ricarica(e: Entita) -> bool:
    return e.dominio == "sensor" and e.unita in _POTENZE and bool(_CARICA.search(e.testo))


def _tempo_ricarica(e: Entita) -> bool:
    return (
        e.dominio == "sensor"
        and (e.unita in _TEMPI or e.classe in ("duration", "timestamp"))
        and bool(_CARICA.search(e.testo))
        and bool(
            _c("remain", "time", "tempo", "fine", "end", "complete", "restante", "rest", "duration", "durata").search(
                e.testo
            )
        )
    )


def _temperatura(parole: re.Pattern[str]):
    def regola(e: Entita) -> bool:
        return (
            e.dominio == "sensor"
            and (e.unita in _TEMPERATURE or e.classe == "temperature")
            and bool(parole.search(e.testo))
        )

    return regola


def _limite(e: Entita) -> bool:
    if e.dominio not in DOMINI[LIMITE]:
        return False
    return bool(_OBIETTIVO.search(e.testo)) and (
        e.unita == "%" or bool(_c(r"\bsoc\b", "charg", "carica", "lade", "battery", "batteria").search(e.testo))
    )


def _serratura(e: Entita) -> bool:
    if e.dominio == "lock":
        return not _CAVO.search(e.testo) and not _c("charge port", "charger", "sportello").search(e.testo)
    return e.dominio == "binary_sensor" and e.classe == "lock"


def _clima(e: Entita) -> bool:
    if e.dominio == "climate":
        return True
    return e.dominio == "switch" and bool(
        _c("climate", "clima", r"\bhvac\b", r"\bac\b", "air condition", "precondition", "klima", "defrost").search(
            e.testo
        )
    )


def _interruttore_ricarica(e: Entita) -> bool:
    return (
        e.dominio == "switch"
        and bool(_CARICA.search(e.testo))
        and not _c("schedul", "programm", "port", "sportello", "limit").search(e.testo)
    )


def _trova(e: Entita) -> bool:
    return e.dominio == "button" and bool(
        _c("horn", "flash", "clacson", "lampeggi", "trova", "find", "locate", "hupe", "klaxon").search(e.testo)
    )


# Nell'ordine: prima le più facili da confondere, così ognuna prende la sua.
_REGOLE = (
    (
        POSIZIONE,
        lambda e: e.dominio == "device_tracker" and not _c("route", "destination", "destinazione").search(e.testo),
    ),
    (TEMPERATURA_BATTERIA, _temperatura(_c("battery", "batteria", "akku", "batterie", r"\bbms\b", "pack"))),
    (TEMPERATURA_ESTERNA, _temperatura(_ESTERNA)),
    (TEMPERATURA_INTERNA, _temperatura(_INTERNA)),
    (POTENZA_RICARICA, _potenza_ricarica),
    (TEMPO_RICARICA, _tempo_ricarica),
    (ODOMETRO, _odometro),
    (AUTONOMIA, _autonomia),
    (VELOCITA, _velocita),
    (LIMITE, _limite),
    (CAVO, _cavo),
    (IN_CARICA, _in_carica),
    (BATTERIA, _batteria),
    (SERRATURA, _serratura),
    (CLIMA, _clima),
    (INTERRUTTORE_RICARICA, _interruttore_ricarica),
    (TROVA, _trova),
)

# A parità, quale dominio vince.
_PREFERENZE: dict[str, tuple[str, ...]] = {
    IN_CARICA: ("binary_sensor", "sensor", "switch"),
    CAVO: ("binary_sensor", "sensor"),
    LIMITE: ("number", "select", "sensor"),
    SERRATURA: ("lock", "binary_sensor"),
    CLIMA: ("climate", "switch"),
}


def proponi(entita: list[Entita]) -> dict[str, str]:
    """Da tutte le entità del dispositivo, una per casella; nessuna due volte.

    Le impostazioni (categoria «config») non sono l'auto: non si propongono,
    tranne il limite di ricarica, che spesso sta proprio lì.
    """
    proposta: dict[str, str] = {}
    prese: set[str] = set()
    for casella, regola in _REGOLE:
        candidate = [
            e
            for e in entita
            if e.entity_id not in prese and regola(e) and (e.categoria != "config" or casella in (LIMITE, CLIMA))
        ]
        if preferiti := _PREFERENZE.get(casella):
            candidate.sort(key=lambda e: preferiti.index(e.dominio) if e.dominio in preferiti else len(preferiti))
        if candidate:
            proposta[casella] = candidate[0].entity_id
            prese.add(candidate[0].entity_id)
    return proposta
