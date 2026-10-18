"""Dalle risposte del cloud Uconnect allo stato di GdAAuto.

Lo stato di un'auto arriva da tre letture, tutte di dati che il cloud ha già
(nessuna sveglia l'auto):

- ``/vehicles/{vin}/status``: ``vehicleInfo`` (contachilometri, pneumatici,
  12 V) ed ``evInfo`` (la batteria di trazione: percentuale, autonomia,
  cavo, ricarica, tempi alla carica completa per presa domestica, AC e DC);
- ``/vehicles/{vin}/location/lastknown``: l'ultima posizione;
- ``/vehicles/{vin}/remote/status``: serrature, finestrini, motore (non tutte
  le auto lo hanno).

Il cloud mescola numeri e stringhe («"12345"», «"null"») e manda le unità a
parte (km o mi, kPa, psi o bar): qui si converte tutto in km e kPa.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

_KM_PER_MIGLIO = 1.609344
_KPA_PER_PSI = 6.894757

#: Le ruote, con i nomi di GdAAuto.
_RUOTE = {
    "FL": "anteriore_sinistro",
    "FR": "anteriore_destro",
    "RL": "posteriore_sinistro",
    "RR": "posteriore_destro",
}

#: Il caricatore collegato → quale «tempo alla carica completa» vale.
_LIVELLI = {"LEVEL_1": "timeToFullyChargeL1", "LEVEL_2": "timeToFullyChargeL2", "LEVEL_3": "timeToFullyChargeL3"}


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v or v.lower() in ("null", "none", "nan"):
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dict(d: Any, *chiavi: str) -> dict[str, Any]:
    for k in chiavi:
        d = d.get(k) if isinstance(d, dict) else None
    return d if isinstance(d, dict) else {}


def _testo(v: Any) -> str:
    return str(v).strip().upper() if v is not None else ""


def _vero(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def km(valore: Any, unita: Any) -> float | None:
    """Una distanza in km, qualunque unità mandi il cloud."""
    n = _numero(valore)
    if n is None:
        return None
    u = str(unita or "km").strip().lower()
    if u in ("mi", "mile", "miles"):
        return round(n * _KM_PER_MIGLIO, 1)
    return n


def kpa(valore: Any, unita: Any) -> float | None:
    """Una pressione in kPa: il cloud la manda in kPa, psi o bar."""
    n = _numero(valore)
    if n is None or n <= 0:
        return None
    u = str(unita or "kpa").strip().lower()
    if u == "psi":
        return round(n * _KPA_PER_PSI, 1)
    if u == "bar":
        return round(n * 100, 1)
    return n


def _quando(*millisecondi: Any) -> datetime | None:
    date = []
    for ms in millisecondi:
        n = _numero(ms)
        if n is not None and n > 0:
            date.append(datetime.fromtimestamp(n / 1000, UTC))
    return max(date) if date else None


def _stato_ricarica(testo: str) -> StatoRicarica | None:
    if not testo:
        return None
    if testo == "CHARGING" or testo == "IN_PROGRESS":
        return StatoRicarica.IN_CARICA
    if "COMPLETE" in testo or testo in ("FULLY_CHARGED", "FULL", "DONE"):
        return StatoRicarica.COMPLETATA
    if "FAULT" in testo or "ERROR" in testo or "FAIL" in testo:
        return StatoRicarica.ERRORE
    if "SCHEDULED" in testo or "WAITING" in testo:
        return StatoRicarica.PROGRAMMATA
    if "PAUSE" in testo or "INTERRUPT" in testo:
        return StatoRicarica.IN_PAUSA
    return StatoRicarica.NON_IN_CARICA


def minuti_alla_fine(batteria: dict[str, Any]) -> int | None:
    """Il tempo alla carica completa del caricatore collegato.

    Il cloud dà tre tempi (presa domestica, AC, DC) e dice quale caricatore è
    attaccato. Se lo dice, vale solo il suo tempo: prendere quello di un altro
    sbaglierebbe di ore. Se non lo dice, il più breve fra quelli validi. I
    valori negativi vogliono dire «non lo so».
    """
    tempi = {livello: _numero(batteria.get(campo)) for livello, campo in _LIVELLI.items()}
    livello = _testo(batteria.get("chargingLevel"))
    if livello in tempi:
        t = tempi[livello]
        return round(t) if t is not None and t > 0 else None
    validi = [t for t in tempi.values() if t is not None and t > 0]
    return round(min(validi)) if validi else None


def interpreta(
    stato: dict[str, Any] | None,
    posizione: dict[str, Any] | None = None,
    remoto: dict[str, Any] | None = None,
) -> StatoVettura:
    stato, posizione, remoto = stato or {}, posizione or {}, remoto or {}
    s = StatoVettura(grezzo={"stato": stato, "posizione": posizione, "remoto": remoto})
    info = _dict(stato, "vehicleInfo")
    ev = _dict(stato, "evInfo")
    batteria = _dict(ev, "battery")

    # --- la batteria e la ricarica ------------------------------------------
    s.batteria = _numero(batteria.get("stateOfCharge"))
    distanza = _dict(batteria, "distanceToEmpty")
    if distanza:
        s.autonomia_km = km(distanza.get("value"), distanza.get("unit"))
    if s.autonomia_km is None:
        # Le auto a benzina, o le ibride senza il dato elettrico: l'autonomia totale.
        carburante = _dict(info, "fuel", "distanceToEmpty")
        if not ev and carburante:
            s.autonomia_km = km(carburante.get("value"), carburante.get("unit"))
    s.cavo_collegato = _vero(batteria.get("plugInStatus"))
    s.stato_ricarica = _stato_ricarica(_testo(batteria.get("chargingStatus")))
    if s.stato_ricarica is None and s.cavo_collegato is False:
        s.stato_ricarica = StatoRicarica.NON_IN_CARICA
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.tempo_ricarica_min = minuti_alla_fine(batteria)
        s.cavo_collegato = True if s.cavo_collegato is None else s.cavo_collegato

    # --- l'auto ---------------------------------------------------------------
    odometro = _dict(info, "odometer", "odometer")
    if odometro:
        s.odometro_km = km(odometro.get("value"), odometro.get("unit"))
    for gomma in info.get("tyrePressure") or []:
        if not isinstance(gomma, dict) or _testo(gomma.get("type")) not in _RUOTE:
            continue
        pressione = _dict(gomma, "pressure")
        if (p := kpa(pressione.get("value"), pressione.get("unit"))) is not None:
            s.pneumatici_kpa[_RUOTE[_testo(gomma.get("type"))]] = p
    accensione = _testo(ev.get("ignitionStatus"))
    if accensione in ("ON", "RUN", "START"):
        s.quadro_acceso = True
    elif accensione in ("OFF", "STOP"):
        s.quadro_acceso = False

    # --- serrature e finestrini, quando l'auto li manda ------------------------
    porte = _dict(remoto, "doors")
    serrature = [_testo(_dict(porte, p).get("status")) for p in ("driver", "passenger", "leftRear", "rightRear")]
    serrature = [x for x in serrature if x]
    if serrature:
        s.chiusa = all(x == "LOCKED" for x in serrature)
    finestrini = [_testo(_dict(remoto, "windows", f).get("status")) for f in ("driver", "passenger")]
    finestrini = [x for x in finestrini if x]
    if finestrini:
        s.finestrini_aperti = any(x != "CLOSED" for x in finestrini)
    motore = _testo(_dict(remoto, "engine").get("status"))
    if motore in ("ON", "OFF"):
        s.quadro_acceso = motore == "ON"
    if _testo(_dict(remoto, "evRunning").get("status")) == "ON":
        s.quadro_acceso = True  # un'elettrica «accesa», pronta a partire

    # --- dove sta ----------------------------------------------------------------
    lat, lon = _numero(posizione.get("latitude")), _numero(posizione.get("longitude"))
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True

    s.letto = _quando(stato.get("timestamp"), ev.get("timestamp"), info.get("timestamp"))
    return s
