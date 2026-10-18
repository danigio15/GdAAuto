"""Dalle risposte del cloud My Porsche allo stato di GdAAuto.

Il cloud (``api.ppa.porsche.com/app/connect/v1``) risponde con l'anagrafica
dell'auto e una lista di **misure**: ogni misura ha una chiave
(``BATTERY_LEVEL``, ``CHARGING_SUMMARY``, ``LOCK_STATE_VEHICLE``…), uno stato
(``status.isEnabled``: spenta, per esempio, con la modalità privacy) e un
valore, sempre un piccolo dizionario. Qui le misure accese diventano un
dizionario ``chiave → valore`` e poi uno ``StatoVettura``.

Le unità del cloud sono già quelle di GdAAuto (km, kW, %), tranne due: le
gomme in bar (×100 per i kPa) e la temperatura del clima in kelvin.

I nomi delle misure e dei campi vengono da CJNE/pyporscheconnectapi (licenza
MIT): vedi RINGRAZIAMENTI.md. Il codice è scritto da capo.
"""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from ...vettura import StatoRicarica, StatoVettura, Vettura

ID = "porsche"

#: Le misure che si chiedono a ogni lettura (``mf=…``). Sono quelle dell'app,
#: senza i segreti della chiave digitale (codici d'abbinamento, certificati),
#: che a GdAAuto non servono e non devono finire nella diagnostica.
MISURE = (
    "ALARM_STATE",
    "BATTERY_CONDITION",
    "BATTERY_LEVEL",
    "CHARGING_PROFILES",
    "CHARGING_RATE",
    "CHARGING_SESSION",
    "CHARGING_SETTINGS",
    "CHARGING_SUMMARY",
    "DEPARTURES",
    "DIRECT_CHARGING",
    "E_RANGE",
    "FUEL_LEVEL",
    "FUEL_RESERVE",
    "GLOBAL_PRIVACY_MODE",
    "GLOBAL_TIMESTAMP",
    "GPS_LOCATION",
    "HVAC_SUMMARY",
    "INTERMEDIATE_SERVICE_RANGE",
    "INTERMEDIATE_SERVICE_TIME",
    "LOCK_STATE_VEHICLE",
    "MAIN_SERVICE_RANGE",
    "MAIN_SERVICE_TIME",
    "MILEAGE",
    "OIL_LEVEL_CURRENT",
    "OIL_SERVICE_RANGE",
    "OIL_SERVICE_TIME",
    "OPEN_STATE_DOOR_FRONT_LEFT",
    "OPEN_STATE_DOOR_FRONT_RIGHT",
    "OPEN_STATE_DOOR_REAR_LEFT",
    "OPEN_STATE_DOOR_REAR_RIGHT",
    "OPEN_STATE_LID_FRONT",
    "OPEN_STATE_LID_REAR",
    "OPEN_STATE_SERVICE_FLAP",
    "OPEN_STATE_SPOILER",
    "OPEN_STATE_SUNROOF",
    "OPEN_STATE_SUNROOF_REAR",
    "OPEN_STATE_TOP",
    "OPEN_STATE_WINDOW_FRONT_LEFT",
    "OPEN_STATE_WINDOW_FRONT_RIGHT",
    "OPEN_STATE_WINDOW_REAR_LEFT",
    "OPEN_STATE_WINDOW_REAR_RIGHT",
    "PARKING_BRAKE",
    "PARKING_LIGHT",
    "RANGE",
    "REMOTE_ACCESS_AUTHORIZATION",
    "THEFT_STATE",
    "TIMERS",
    "TIRE_PRESSURE_FRONT_LEFT",
    "TIRE_PRESSURE_FRONT_RIGHT",
    "TIRE_PRESSURE_REAR_LEFT",
    "TIRE_PRESSURE_REAR_RIGHT",
)

#: Le statistiche dei viaggi: cambiano piano, si leggono ogni mezz'ora.
MISURE_VIAGGI = ("TRIP_STATISTICS_CYCLIC", "TRIP_STATISTICS_LONG_TERM", "TRIP_STATISTICS_SHORT_TERM")

#: I comandi di cui si chiede se l'auto li accetta (``cf=…``).
COMANDI_CLOUD = (
    "CHARGING_PROFILES_EDIT",
    "CHARGING_SETTINGS_EDIT",
    "DIRECT_CHARGING_START",
    "DIRECT_CHARGING_STOP",
    "HONK_FLASH",
    "LOCK",
    "REMOTE_CLIMATIZER_START",
    "REMOTE_CLIMATIZER_STOP",
    "SPIN_CHALLENGE",
    "UNLOCK",
)

#: Lo stato della ricarica come lo dice ``CHARGING_SUMMARY.status``.
_RICARICA = {
    "CHARGING": StatoRicarica.IN_CARICA,
    "INSTANT_CHARGING": StatoRicarica.IN_CARICA,
    "INITIALISING": StatoRicarica.IN_CARICA,
    "CHARGING_COMPLETED": StatoRicarica.COMPLETATA,
    "COMPLETED": StatoRicarica.COMPLETATA,
    "CHARGING_ERROR": StatoRicarica.ERRORE,
    "ERROR": StatoRicarica.ERRORE,
    "CHARGING_PAUSED": StatoRicarica.IN_PAUSA,
    "CHARGING_STOPPED": StatoRicarica.NON_IN_CARICA,
    "NOT_CHARGING": StatoRicarica.NON_IN_CARICA,
    "NOT_PLUGGED": StatoRicarica.NON_IN_CARICA,
    "OFF": StatoRicarica.NON_IN_CARICA,
    "STANDBY": StatoRicarica.NON_IN_CARICA,
}

#: Gli stati che vogliono il cavo per forza: carica, ha finito, o si è guastata col cavo dentro.
_COL_CAVO = {"CHARGING", "INSTANT_CHARGING", "INITIALISING", "CHARGING_COMPLETED", "COMPLETED", "CHARGING_ERROR"}

#: Il cavo, quando il cloud lo dice per esteso.
_CAVO = {"CONNECTED": True, "PLUGGED": True, "LOCKED": True, "DISCONNECTED": False, "UNPLUGGED": False}

_RUOTE = {
    "anteriore_sinistro": "TIRE_PRESSURE_FRONT_LEFT",
    "anteriore_destro": "TIRE_PRESSURE_FRONT_RIGHT",
    "posteriore_sinistro": "TIRE_PRESSURE_REAR_LEFT",
    "posteriore_destro": "TIRE_PRESSURE_REAR_RIGHT",
}
#: Come l'app: una gomma fuori di più di 0,3 bar dalla pressione giusta è in allarme.
_TOLLERANZA_BAR = 0.3

_PORTIERE = ("DOOR_FRONT_LEFT", "DOOR_FRONT_RIGHT", "DOOR_REAR_LEFT", "DOOR_REAR_RIGHT")
#: Il bagagliaio dietro e quello davanti (Taycan e Macan ne hanno due).
_BAGAGLIAI = ("LID_REAR", "LID_FRONT")
#: I finestrini, i tetti apribili e la capote: tutto quello da cui entra la pioggia.
_FINESTRINI = (
    "WINDOW_FRONT_LEFT",
    "WINDOW_FRONT_RIGHT",
    "WINDOW_REAR_LEFT",
    "WINDOW_REAR_RIGHT",
    "SUNROOF",
    "SUNROOF_REAR",
    "TOP",
)

_KELVIN = 273.15
_POSIZIONE = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*")

#: I motori che hanno una batteria di trazione.
ELETTRICHE = ("BEV", "PHEV")


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _vero(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _ora(testo: Any) -> datetime | None:
    """«2024-11-03T09:12:44Z» o «…+01:00» → datetime con il fuso."""
    if not isinstance(testo, str) or not testo:
        return None
    try:
        quando = datetime.fromisoformat(testo)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


# --- l'anagrafica ---------------------------------------------------------------


def motore(voce: dict[str, Any]) -> str:
    """«BEV», «PHEV» o «COMBUSTION»; vuoto se il cloud non lo dice."""
    tipo = voce.get("modelType")
    return str(tipo.get("engine") or "").upper() if isinstance(tipo, dict) else ""


def vettura_da(voce: Any) -> Vettura | None:
    """Una voce di ``/connect/v1/vehicles`` → l'anagrafica di GdAAuto."""
    if not isinstance(voce, dict) or not voce.get("vin"):
        return None
    tipo = voce.get("modelType") if isinstance(voce.get("modelType"), dict) else {}
    modello = str(voce.get("modelName") or tipo.get("model") or "Porsche").strip()
    anno = _numero(tipo.get("year"))
    relazione = str(voce.get("relationship") or voce.get("ownershipType") or "").upper()
    return Vettura(
        vin=str(voce["vin"]),
        marca=ID,
        modello=modello,
        # Il nome che la persona ha dato all'auto nell'app, se c'è.
        nome=str(voce.get("customName") or "").strip() or modello,
        anno=int(anno) if anno and anno > 1990 else None,
        condivisa=bool(relazione) and relazione not in ("OWNER", "PRIMARY", "PRIMARY_USER"),
        extra={
            "motore": motore(voce),
            "codice_modello": tipo.get("code"),
            "connect": voce.get("connect"),
        },
    )


def misure(risposta: Any) -> dict[str, dict[str, Any]]:
    """Le misure accese di una risposta, ``chiave → valore``.

    Una misura spenta (privacy, servizio non acquistato) non c'è: meglio
    «non si sa» che un valore vecchio.
    """
    trovate: dict[str, dict[str, Any]] = {}
    if not isinstance(risposta, dict):
        return trovate
    for m in risposta.get("measurements") or []:
        if not isinstance(m, dict) or not isinstance(m.get("key"), str):
            continue
        stato = m.get("status") if isinstance(m.get("status"), dict) else {}
        if stato.get("isEnabled") is False or not isinstance(m.get("value"), dict):
            continue
        trovate[m["key"]] = m["value"]
    return trovate


def capacita_comandi(risposta: Any) -> tuple[list[str], dict[str, Any]]:
    """Dai comandi della risposta (``cf=…``): quelli accesi, e i limiti del clima."""
    accesi: list[str] = []
    clima: dict[str, Any] = {}
    if not isinstance(risposta, dict):
        return accesi, clima
    for c in risposta.get("commands") or []:
        if not isinstance(c, dict) or not isinstance(c.get("key"), str):
            continue
        stato = c.get("status") if isinstance(c.get("status"), dict) else {}
        if stato.get("isEnabled") is True:
            accesi.append(c["key"])
        if c["key"] == "REMOTE_CLIMATIZER_START" and isinstance(c.get("metadata"), dict):
            md = c["metadata"]
            clima = {k: md[k] for k in ("minTemperature", "maxTemperature") if _numero(md.get(k)) is not None}
    return sorted(accesi), clima


# --- lo stato --------------------------------------------------------------------


def stato_ricarica(valore: Any) -> StatoRicarica | None:
    if not isinstance(valore, str):
        return None
    return _RICARICA.get(valore.upper())


def limite_ricarica(m: dict[str, dict[str, Any]]) -> int | None:
    """Il limite di ricarica, da dove lo tiene quel modello.

    Le auto nuove lo dicono in ``CHARGING_SUMMARY.targetSoC``; quelle coi
    profili nel profilo attivo; quelle con le partenze (Macan Electric) in
    ``CHARGING_SETTINGS.targetSoc``. In ricarica immediata è il 100 %.
    """
    riepilogo = m.get("CHARGING_SUMMARY") or {}
    for valore in (
        riepilogo.get("targetSoC"),
        (m.get("CHARGING_SETTINGS") or {}).get("targetSoc") if "DEPARTURES" in m else None,
        (riepilogo.get("chargingProfile") or {}).get("minSoC") if riepilogo.get("mode") == "PROFILE" else None,
        riepilogo.get("minSoC"),
    ):
        if (n := _numero(valore)) is not None:
            return round(n)
    if riepilogo.get("mode") == "DIRECT":
        return 100
    return None


def _apertura(m: dict[str, dict[str, Any]], nomi: tuple[str, ...]) -> bool | None:
    """Vero se una è aperta; falso se il cloud le dice tutte chiuse; ``None`` se non ne dice nessuna."""
    valori = [_vero((m.get(f"OPEN_STATE_{n}") or {}).get("isOpen")) for n in nomi]
    noti = [v for v in valori if v is not None]
    return any(noti) if noti else None


def _cavo(m: dict[str, dict[str, Any]], stato: str) -> bool | None:
    for blocco in (m.get("CHARGING_SUMMARY") or {}, m.get("BATTERY_CHARGING_STATE") or {}):
        for chiave in ("isPlugged", "plugged", "isPluggedIn"):
            if isinstance(blocco.get(chiave), bool):
                return blocco[chiave]
        if (testo := blocco.get("plugState") or blocco.get("plugStatus")) and str(testo).upper() in _CAVO:
            return _CAVO[str(testo).upper()]
    if stato == "NOT_PLUGGED":
        return False
    if stato in _COL_CAVO:
        return True
    return None


def _ricarica(s: StatoVettura, m: dict[str, dict[str, Any]], adesso: datetime) -> None:
    riepilogo = m.get("CHARGING_SUMMARY") or {}
    vecchio = m.get("BATTERY_CHARGING_STATE") or {}  # la forma di prima, che qualche auto manda ancora
    stato = str(riepilogo.get("status") or vecchio.get("chargingStatus") or "").upper()
    s.stato_ricarica = stato_ricarica(stato)
    s.cavo_collegato = _cavo(m, stato)
    s.limite_ricarica = limite_ricarica(m)
    in_carica = s.stato_ricarica == StatoRicarica.IN_CARICA

    rate = m.get("CHARGING_RATE") or {}
    potenza = _numero(rate.get("chargingPower"))
    if potenza is None:
        potenza = _numero(vecchio.get("chargingPower"))
    if potenza is not None:
        s.potenza_ricarica_diretta_kw = round(potenza, 2) if in_carica else 0.0
    elif s.stato_ricarica is not None and not in_carica:
        s.potenza_ricarica_diretta_kw = 0.0

    tipo = str(vecchio.get("chargingType") or riepilogo.get("chargingType") or "").upper()
    if tipo in ("AC", "DC"):
        s.ricarica_rapida = tipo == "DC"

    if in_carica:
        fine = _ora(riepilogo.get("targetDateTimeWithOffset") or riepilogo.get("targetDateTime"))
        fine = fine or _ora(vecchio.get("endsAt"))
        if fine is not None and fine > adesso:
            s.tempo_ricarica_min = round((fine - adesso).total_seconds() / 60)


def _gomme(s: StatoVettura, m: dict[str, dict[str, Any]]) -> None:
    differenze: list[float] = []
    for ruota, chiave in _RUOTE.items():
        gomma = m.get(chiave) or {}
        if (bar := _numero(gomma.get("actualPressureBar"))) is not None and bar > 0:
            s.pneumatici_kpa[ruota] = round(bar * 100, 1)
        if (diff := _numero(gomma.get("differenceBar"))) is not None:
            differenze.append(abs(diff))
    if differenze:
        s.pneumatici_allarme = max(differenze) > _TOLLERANZA_BAR + 1e-9


def _posizione(s: StatoVettura, m: dict[str, dict[str, Any]]) -> None:
    gps = m.get("GPS_LOCATION") or {}
    luogo = gps.get("location")
    if isinstance(luogo, str) and (trovato := _POSIZIONE.fullmatch(luogo)):
        lat, lon = float(trovato.group(1)), float(trovato.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0):
            s.latitudine, s.longitudine = lat, lon
            # Il cloud scrive il segno: un sud o un ovest si vedono.
            s.posizione_con_segno = True


def _letto(m: dict[str, dict[str, Any]], risposta: dict[str, Any]) -> datetime | None:
    if (globale := _ora((m.get("GLOBAL_TIMESTAMP") or {}).get("timestamp"))) is not None:
        return globale
    ore = [o for v in m.values() if (o := _ora(v.get("lastModified")))]
    return max(ore) if ore else _ora(risposta.get("timestamp"))


def interpreta(
    risposta: dict[str, Any], vettura: Vettura | None = None, adesso: datetime | None = None
) -> StatoVettura:
    """La risposta di ``/connect/v1/vehicles/{vin}?mf=…`` → ``StatoVettura``."""
    adesso = adesso or datetime.now(UTC)
    m = misure(risposta)
    s = StatoVettura(grezzo=m)
    tipo = (vettura.extra.get("motore") if vettura else "") or motore(risposta)
    elettrica = tipo in ELETTRICHE or (not tipo and ("E_RANGE" in m or "CHARGING_SUMMARY" in m))
    ibrida = tipo == "PHEV"

    if elettrica:
        # Su un'auto a benzina ``BATTERY_LEVEL`` non è la batteria di trazione.
        s.batteria = _numero((m.get("BATTERY_LEVEL") or {}).get("percent"))
        s.autonomia_km = _numero((m.get("E_RANGE") or {}).get("kilometers"))
        if s.autonomia_km is None and not ibrida:
            s.autonomia_km = _numero((m.get("RANGE") or {}).get("kilometers"))
        _ricarica(s, m, adesso)
    if tipo == "COMBUSTION" or ibrida:
        s.carburante_percento = _numero((m.get("FUEL_LEVEL") or {}).get("percent"))
        # Nelle ibride ``RANGE`` è l'autonomia di tutte e due insieme.
        s.autonomia_totale_km = _numero((m.get("RANGE") or {}).get("kilometers"))

    s.odometro_km = _numero((m.get("MILEAGE") or {}).get("kilometers"))

    clima = m.get("HVAC_SUMMARY") or {}
    if isinstance(clima.get("status"), str):
        s.clima_acceso = clima["status"].upper() != "OFF"
    elif isinstance((m.get("CLIMATIZER_STATE") or {}).get("isOn"), bool):
        s.clima_acceso = m["CLIMATIZER_STATE"]["isOn"]
    if (kelvin := _numero(clima.get("targetTemperature"))) is not None and kelvin > 200:
        s.temperatura_clima_c = round(kelvin - _KELVIN, 1)

    s.chiusa = _vero((m.get("LOCK_STATE_VEHICLE") or {}).get("isLocked"))
    s.portiere_aperte = _apertura(m, _PORTIERE)
    s.bagagliaio_aperto = _apertura(m, _BAGAGLIAI)
    s.finestrini_aperti = _apertura(m, _FINESTRINI)
    s.freno_stazionamento = _vero((m.get("PARKING_BRAKE") or {}).get("isOn"))
    _gomme(s, m)
    _posizione(s, m)
    s.letto = _letto(m, risposta)
    return s


def _kwh_100km(v: Any) -> float | None:
    """Un consumo che il cloud scrive come numero o come ``{"kilowattHoursPer100Kilometers": …}``."""
    if isinstance(v, dict):
        v = v.get("kilowattHoursPer100Kilometers", v.get("value"))
    n = _numero(v)
    return round(n, 1) if n is not None and 0 < n < 100 else None


def consumo_medio(risposta: Any) -> float | None:
    """Il consumo elettrico medio dalle statistiche dei viaggi: dal lungo periodo, se no dal ciclo."""
    m = misure(risposta)
    for chiave in ("TRIP_STATISTICS_LONG_TERM", "TRIP_STATISTICS_CYCLIC", "TRIP_STATISTICS_SHORT_TERM"):
        viaggio = m.get(chiave) or {}
        for campo in ("averageElectricEngineConsumption", "averageElectricConsumption"):
            if (valore := _kwh_100km(viaggio.get(campo))) is not None:
                return valore
    return None


__all__ = [
    "COMANDI_CLOUD",
    "ELETTRICHE",
    "ID",
    "MISURE",
    "MISURE_VIAGGI",
    "capacita_comandi",
    "consumo_medio",
    "interpreta",
    "limite_ricarica",
    "misure",
    "motore",
    "stato_ricarica",
    "vettura_da",
]
