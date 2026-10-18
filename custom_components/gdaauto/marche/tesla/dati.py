"""Da ``vehicle_data`` della Fleet API Tesla allo stato di GdAAuto.

Tesla parla in miglia e in bar, e per il resto dice le cose con un nome:
``charge_state.battery_level``, ``vehicle_state.locked``. Qui tutto diventa
chilometri, kPa e uno ``StatoVettura``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import Marcia, StatoRicarica, StatoVettura

MIGLIO_KM = 1.609344

_RICARICA = {
    "Charging": StatoRicarica.IN_CARICA,
    "Complete": StatoRicarica.COMPLETATA,
    "Stopped": StatoRicarica.IN_PAUSA,
    "Disconnected": StatoRicarica.NON_IN_CARICA,
    "Starting": StatoRicarica.PROGRAMMATA,
    "NoPower": StatoRicarica.ERRORE,
}

_MARCE = {"P": Marcia.PARCHEGGIO, "D": Marcia.AVANTI, "R": Marcia.RETROMARCIA, "N": Marcia.FOLLE}

_RUOTE = {
    "anteriore_sinistro": "tpms_pressure_fl",
    "anteriore_destro": "tpms_pressure_fr",
    "posteriore_sinistro": "tpms_pressure_rl",
    "posteriore_destro": "tpms_pressure_rr",
}


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _km(miglia: Any) -> float | None:
    n = _numero(miglia)
    return round(n * MIGLIO_KM, 1) if n is not None else None


def _aperto(*valori: Any) -> bool | None:
    noti = [v for v in valori if v is not None]
    if not noti:
        return None
    return any(bool(_numero(v)) for v in noti)


def _quando(*millisecondi: Any) -> datetime | None:
    noti = [n for v in millisecondi if (n := _numero(v))]
    if not noti:
        return None
    return datetime.fromtimestamp(max(noti) / 1000, UTC)


def interpreta(dati: dict[str, Any]) -> StatoVettura:
    """``dati`` è il ``response`` di ``vehicle_data``."""
    carica = dati.get("charge_state") or {}
    clima = dati.get("climate_state") or {}
    guida = dati.get("drive_state") or {}
    auto = dati.get("vehicle_state") or {}
    s = StatoVettura(grezzo=dati, in_sonno=False)

    s.batteria = _numero(carica.get("battery_level"))
    s.autonomia_km = _km(carica.get("battery_range"))
    s.limite_ricarica = int(n) if (n := _numero(carica.get("charge_limit_soc"))) is not None else None
    s.stato_ricarica = _RICARICA.get(str(carica.get("charging_state")))
    cavo = carica.get("conn_charge_cable")
    if cavo is not None:
        s.cavo_collegato = cavo not in ("<invalid>", "")
    elif s.stato_ricarica is not None:
        s.cavo_collegato = s.stato_ricarica != StatoRicarica.NON_IN_CARICA
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.potenza_ricarica_diretta_kw = _numero(carica.get("charger_power"))
        minuti = _numero(carica.get("minutes_to_full_charge"))
        s.tempo_ricarica_min = int(minuti) if minuti is not None else None
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0

    s.temperatura_interna_c = _numero(clima.get("inside_temp"))
    s.temperatura_esterna_c = _numero(clima.get("outside_temp"))
    s.clima_acceso = clima.get("is_climate_on") if isinstance(clima.get("is_climate_on"), bool) else None
    s.temperatura_clima_c = _numero(clima.get("driver_temp_setting"))

    # Da parcheggiata Tesla non dice né la marcia né la velocità.
    s.marcia = _MARCE.get(str(guida.get("shift_state")), Marcia.PARCHEGGIO if guida else None)
    velocita = _numero(guida.get("speed"))
    s.velocita_kmh = round(velocita * MIGLIO_KM, 1) if velocita is not None else (0.0 if guida else None)
    s.potenza_diretta_kw = _numero(guida.get("power"))
    s.latitudine = _numero(guida.get("latitude"))
    s.longitudine = _numero(guida.get("longitude"))
    s.posizione_con_segno = s.latitudine is not None

    s.odometro_km = _km(auto.get("odometer"))
    s.chiusa = auto.get("locked") if isinstance(auto.get("locked"), bool) else None
    s.portiere_aperte = _aperto(auto.get("df"), auto.get("dr"), auto.get("pf"), auto.get("pr"))
    s.bagagliaio_aperto = _aperto(auto.get("rt"))
    s.finestrini_aperti = _aperto(
        auto.get("fd_window"), auto.get("fp_window"), auto.get("rd_window"), auto.get("rp_window")
    )
    s.sentinella = auto.get("sentry_mode") if isinstance(auto.get("sentry_mode"), bool) else None
    for ruota, campo in _RUOTE.items():
        if (bar := _numero(auto.get(campo))) is not None and bar > 0:
            s.pneumatici_kpa[ruota] = round(bar * 100, 1)

    s.letto = _quando(carica.get("timestamp"), guida.get("timestamp"), auto.get("timestamp"))
    return s
