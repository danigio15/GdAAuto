"""Dalle risposte del cloud Toyota allo stato di GdAAuto.

Il cloud non dà lo stato in una risposta sola: ogni pezzo ha il suo
indirizzo, e qui arrivano i ``payload`` già raccolti, con questi nomi:

- ``elettrico`` (``/v1/vehicle/electric/status``): batteria, autonomia
  elettrica, stato della ricarica, minuti al pieno;
- ``stato`` (``/v1/vehicle/status``): serrature e aperture di portiere,
  portellone e finestrini;
- ``telemetria`` (``/v3/telemetry``): contachilometri, e di riserva batteria
  e autonomia;
- ``posizione`` (``/v1/location``): dove è stata parcheggiata l'ultima volta;
- ``clima`` (``/v1/vehicle/climate-status``): se il clima va, la temperatura
  in abitacolo e quella chiesta.

Le distanze arrivano con la loro unità (``{"value": 57.3, "unit": "km"}``,
a volte in miglia), le temperature in °C o °F: qui diventa tutto km e °C.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

MIGLIO_KM = 1.609344

#: Oltre un giorno al pieno è il segnaposto del cloud (65535…), non una stima.
MINUTI_PLAUSIBILI = 24 * 60

_RICARICA: dict[str, tuple[StatoRicarica, bool | None]] = {
    "charging": (StatoRicarica.IN_CARICA, True),
    "chargecomplete": (StatoRicarica.COMPLETATA, True),
    "charge_complete": (StatoRicarica.COMPLETATA, True),
    "complete": (StatoRicarica.COMPLETATA, True),
    "plugged": (StatoRicarica.NON_IN_CARICA, True),
    "none": (StatoRicarica.NON_IN_CARICA, False),
}

#: Le quattro portiere: il portellone (``rearBack``) e il cofano si dicono a parte.
_PORTIERE = ("driver", "passenger", "rearLeft", "rearRight")
_FINESTRINI = ("driver", "passenger", "rearLeft", "rearRight")


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _distanza_km(v: Any) -> float | None:
    """``{"value": …, "unit": "km"|"mi"}`` in chilometri."""
    d = _dict(v)
    n = _numero(d.get("value"))
    if n is None:
        return None
    if str(d.get("unit") or "km").lower() in ("mi", "mile", "miles"):
        return round(n * MIGLIO_KM, 1)
    return round(n, 1)


def _gradi_c(v: Any) -> float | None:
    d = _dict(v)
    n = _numero(d.get("value"))
    if n is None:
        return None
    if str(d.get("unit") or "C").upper().startswith("F"):
        return round((n - 32) * 5 / 9, 1)
    return n


def _quando(*valori: Any) -> datetime | None:
    """Il momento più recente fra quelli che il cloud dice (ISO 8601, con la Z)."""
    noti = []
    for v in valori:
        if not isinstance(v, str) or not v:
            continue
        try:
            noti.append(datetime.fromisoformat(v))
        except ValueError:
            continue
    return max(noti) if noti else None


def _stato_di(v: Any) -> str | None:
    s = _dict(v).get("status")
    return s.lower() if isinstance(s, str) else None


def chiusa(stato: dict[str, Any]) -> bool | None:
    """Chiusa se ogni portiera che il cloud nomina è ``locked``; aperta se una
    è ``unlocked``; altrimenti non si sa."""
    porte = _dict(stato.get("doors"))
    serrature = [_stato_di(_dict(porte.get(nome)).get("lockStatus")) for nome in _PORTIERE if nome in porte]
    serrature = [s for s in serrature if s is not None]
    if not serrature:
        return None
    if "unlocked" in serrature:
        return False
    return True if all(s == "locked" for s in serrature) else None


def _aperto(*stati: str | None) -> bool | None:
    noti = [s for s in stati if s in ("open", "close", "closed")]
    if not noti:
        return None
    return "open" in noti


def interpreta(parti: dict[str, Any]) -> StatoVettura:
    """Lo stato, da quello che il cloud ha detto adesso (i pezzi che mancano restano ``None``)."""
    el = _dict(parti.get("elettrico"))
    st = _dict(parti.get("stato"))
    te = _dict(parti.get("telemetria"))
    po = _dict(_dict(parti.get("posizione")).get("vehicleLocation"))
    cl = _dict(parti.get("clima"))
    s = StatoVettura(grezzo={k: v for k, v in parti.items() if v})

    # --- la batteria e la ricarica ------------------------------------------------
    s.batteria = _numero(el.get("batteryLevel"))
    if s.batteria is None:
        s.batteria = _numero(te.get("batteryLevel"))
    s.autonomia_km = _distanza_km(el.get("evRange"))
    if s.autonomia_km is None and str(te.get("fuelType") or "").upper() == "E":
        # Su un'elettrica pura l'autonomia della telemetria è quella della batteria.
        s.autonomia_km = _distanza_km(te.get("distanceToEmpty"))
    ricarica = str(el.get("chargingStatus") or te.get("chargingStatus") or "").lower()
    if ricarica in _RICARICA:
        s.stato_ricarica, s.cavo_collegato = _RICARICA[ricarica]
    minuti = _numero(el.get("remainingChargeTime"))
    if minuti is not None and (minuti <= MINUTI_PLAUSIBILI or s.stato_ricarica == StatoRicarica.IN_CARICA):
        s.tempo_ricarica_min = int(minuti)

    # --- il resto -------------------------------------------------------------------
    s.odometro_km = _distanza_km(te.get("odometer"))

    porte = _dict(st.get("doors"))
    if porte:
        s.chiusa = chiusa(st)
        s.portiere_aperte = _aperto(*(_stato_di(_dict(porte.get(n)).get("openStatus")) for n in _PORTIERE))
        s.bagagliaio_aperto = _aperto(_stato_di(_dict(porte.get("rearBack")).get("openStatus")))
    finestrini = _dict(st.get("windows"))
    if finestrini:
        s.finestrini_aperti = _aperto(*(_stato_di(finestrini.get(n)) for n in _FINESTRINI))

    lat, lon = _numero(po.get("latitude")), _numero(po.get("longitude"))
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True

    stato_clima = str(cl.get("status") or "").lower()
    if stato_clima in ("running", "starting"):
        s.clima_acceso = True
    elif stato_clima in ("stopped", "stopping"):
        s.clima_acceso = False
    s.temperatura_interna_c = _gradi_c(cl.get("currentTemperature"))
    s.temperatura_clima_c = _gradi_c(cl.get("targetTemperature"))

    s.letto = _quando(
        el.get("lastUpdateTimestamp"),
        st.get("lastUpdateTimestamp"),
        te.get("timestamp"),
        po.get("locationAcquisitionDatetime"),
    )
    return s
