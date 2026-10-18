"""Dalle risposte GraphQL dell'app Polestar allo stato di GdAAuto.

Il cloud dell'app (``mystar-v2``) dà tre blocchi per ogni auto: ``battery``
(carica, autonomia, stato e tempo della ricarica), ``odometer`` (il
contachilometri, in metri) e ``health`` (i tagliandi, che GdAAuto non usa).

Le forme sono due, e si leggono tutte e due:

- ``carTelematicsV2`` (quella di oggi): ogni blocco è una lista, una voce per
  VIN, e l'ora è ``timestamp.seconds``;
- ``carTelematics`` e i vecchi ``getBatteryData``/``getOdometerData``: un
  oggetto solo, con l'ora in ``eventUpdatedTimestamp`` e qualche campo in più
  (cavo collegato, potenza di ricarica).

Porte, serrature, clima e posizione l'app li legge da un altro canale (gRPC
verso il cloud Volvo/Polestar), che con il solo ``aiohttp`` non si parla: qui
restano ``None``.
"""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from ...vettura import StatoRicarica, StatoVettura, Vettura

ID = "polestar"

_MIGLIO_KM = 1.609344

#: Lo stato della ricarica, senza il prefisso e senza «V2».
_RICARICA = {
    "CHARGING": StatoRicarica.IN_CARICA,
    "SMART_CHARGING": StatoRicarica.IN_CARICA,
    "SMART_CHARGING_PAUSED": StatoRicarica.IN_PAUSA,
    "DONE": StatoRicarica.COMPLETATA,
    "IDLE": StatoRicarica.NON_IN_CARICA,
    "DISCHARGING": StatoRicarica.NON_IN_CARICA,
    "SCHEDULED": StatoRicarica.PROGRAMMATA,
    "FAULT": StatoRicarica.ERRORE,
    "ERROR": StatoRicarica.ERRORE,
}

_CAVO = {
    "CHARGER_CONNECTION_STATUS_CONNECTED": True,
    "CHARGER_CONNECTION_STATUS_DISCONNECTED": False,
}


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def modello(nome: Any) -> str:
    """«Polestar4» → «Polestar 4»: il cloud a volte attacca il numero."""
    testo = str(nome or "").strip()
    if m := re.fullmatch(r"([A-Za-z]+)(\d+)", testo):
        return f"{m.group(1)} {m.group(2)}"
    return testo or "Polestar"


def vettura_da(voce: Any) -> Vettura | None:
    """Una voce di ``getConsumerCarsV2`` → l'anagrafica di GdAAuto."""
    if not isinstance(voce, dict) or not voce.get("vin"):
        return None
    nome = modello(voce.get("modelName"))
    anno = _numero(voce.get("modelYear"))
    return Vettura(
        vin=str(voce["vin"]),
        marca=ID,
        modello=nome,
        # Il cloud non dà un nome all'auto: si parte dal modello, come l'app.
        nome=nome,
        # I dati anonimi dicono «0000»: un anno così non è un anno.
        anno=int(anno) if anno and anno > 1990 else None,
        targa=str(voce.get("registrationNo") or ""),
        extra={
            "id": voce.get("internalVehicleIdentifier"),
            "pno34": voce.get("pno34"),
            "settimana": voce.get("structureWeek"),
        },
    )


def _per_vin(blocco: Any, vin: str | None) -> dict[str, Any]:
    """Il blocco di quell'auto: in V2 è una lista per VIN, prima un oggetto solo."""
    if isinstance(blocco, dict):
        return blocco
    if isinstance(blocco, list):
        voci = [x for x in blocco if isinstance(x, dict)]
        for x in voci:
            if vin is None or x.get("vin") == vin:
                return x
        # Una voce sola senza VIN è comunque la sua.
        if len(voci) == 1 and not voci[0].get("vin"):
            return voci[0]
    return {}


def _ora(blocco: dict[str, Any]) -> datetime | None:
    for chiave in ("timestamp", "eventUpdatedTimestamp"):
        t = blocco.get(chiave)
        if not isinstance(t, dict):
            continue
        secondi = _numero(t.get("seconds") if "seconds" in t else t.get("unix"))
        if secondi:
            return datetime.fromtimestamp(secondi, tz=UTC)
        if isinstance(t.get("iso"), str):
            try:
                return datetime.fromisoformat(t["iso"])
            except ValueError:
                pass
    return None


def stato_ricarica(valore: Any) -> StatoRicarica | None:
    """«CHARGING_STATUS_V2_CHARGING» o «CHARGING_STATUS_CHARGING» → in carica."""
    if not isinstance(valore, str):
        return None
    chiave = valore.upper().removeprefix("CHARGING_STATUS_").removeprefix("V2_")
    return _RICARICA.get(chiave)


def blocchi(dati: dict[str, Any], vin: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """La batteria e il contachilometri, in qualunque delle forme arrivino."""
    for chiave in ("carTelematicsV2", "carTelematics"):
        t = dati.get(chiave)
        if isinstance(t, dict):
            return _per_vin(t.get("battery"), vin), _per_vin(t.get("odometer"), vin)
    return _per_vin(dati.get("getBatteryData"), vin), _per_vin(dati.get("getOdometerData"), vin)


def interpreta(dati: dict[str, Any], vin: str | None = None) -> StatoVettura:
    """Il ``data`` di una risposta GraphQL → ``StatoVettura``."""
    s = StatoVettura(grezzo=dati)
    batteria, contachilometri = blocchi(dati, vin)

    s.batteria = _numero(batteria.get("batteryChargeLevelPercentage"))
    s.autonomia_km = _numero(batteria.get("estimatedDistanceToEmptyKm"))
    if s.autonomia_km is None and (miglia := _numero(batteria.get("estimatedDistanceToEmptyMiles"))) is not None:
        s.autonomia_km = round(miglia * _MIGLIO_KM, 1)

    s.stato_ricarica = stato_ricarica(batteria.get("chargingStatusV2") or batteria.get("chargingStatus"))
    in_carica = s.stato_ricarica == StatoRicarica.IN_CARICA
    s.cavo_collegato = _CAVO.get(str(batteria.get("chargerConnectionStatus")))
    if s.cavo_collegato is None and in_carica:
        s.cavo_collegato = True  # se carica, il cavo c'è
    if in_carica:
        tempo = _numero(batteria.get("estimatedChargingTimeToFullMinutes"))
        s.tempo_ricarica_min = round(tempo) if tempo is not None else None

    watt = _numero(batteria.get("chargingPowerWatts"))
    if watt is not None:
        s.potenza_ricarica_diretta_kw = round(watt / 1000, 3)
    elif "chargingPowerWatts" in batteria and s.stato_ricarica is not None and not in_carica:
        s.potenza_ricarica_diretta_kw = 0.0

    metri = _numero(contachilometri.get("odometerMeters"))
    if metri is not None:
        s.odometro_km = round(metri / 1000, 1)

    ore = [o for o in (_ora(batteria), _ora(contachilometri)) if o]
    s.letto = max(ore) if ore else None
    return s
