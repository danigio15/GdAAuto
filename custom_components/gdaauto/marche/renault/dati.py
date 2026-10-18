"""Dalle letture di Kamereon allo stato di GdAAuto.

Kamereon non dà lo stato in un pezzo solo: ogni cosa ha la sua lettura
(``battery-status``, ``cockpit``, ``hvac-status``, ``location``,
``lock-status``, ``pressure``, ``soc-levels``), e qui arrivano già aperte
(gli ``attributes`` di ognuna). Unità: km, kW (W per la ZOE di prima
serie), °C e mbar per le gomme.

Lo stato della ricarica è un numero (``chargingStatus``): 1.0 in carica,
0.2 finita, 0.1 e 0.3 in attesa, -1.0 «non in carica» per le ZOE fino alla
ZE40 e «errore» per le altre. La mappa viene da hacf-fr/renault-api
(``kamereon/enums.py``, MIT) e da come l'integrazione Renault di Home
Assistant (Apache-2.0) legge cavo e ricarica.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

_RICARICA: dict[float, StatoRicarica | None] = {
    0.0: StatoRicarica.NON_IN_CARICA,
    0.1: StatoRicarica.PROGRAMMATA,  # aspetta una ricarica programmata
    0.2: StatoRicarica.COMPLETATA,
    0.3: StatoRicarica.PROGRAMMATA,  # aspetta che la colonnina dia corrente
    0.4: StatoRicarica.NON_IN_CARICA,  # sportellino aperto
    1.0: StatoRicarica.IN_CARICA,
    -1.1: None,  # non disponibile
    -1.3: StatoRicarica.PROGRAMMATA,  # V2G in attesa
    -1.4: StatoRicarica.NON_IN_CARICA,  # V2L collegato
    -1.5: StatoRicarica.NON_IN_CARICA,  # V2G che cede energia
    -1.6: StatoRicarica.IN_CARICA,  # V2G in carica
}
_ERRORE = -1.0

#: gli stati della ricarica che dicono che il cavo c'è
_CAVO_DA_RICARICA = {0.1, 0.2, 0.3, 1.0, -1.3, -1.5, -1.6}

_RUOTE = {
    "anteriore_sinistro": "flPressure",
    "anteriore_destro": "frPressure",
    "posteriore_sinistro": "rlPressure",
    "posteriore_destro": "rrPressure",
}

_PORTE = ("doorStatusDriver", "doorStatusPassenger", "doorStatusRearLeft", "doorStatusRearRight")


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _quando(testo: Any) -> datetime | None:
    if not isinstance(testo, str) or not testo:
        return None
    try:
        t = datetime.fromisoformat(testo)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _aperto(*valori: Any) -> bool | None:
    noti = [str(v).lower() for v in valori if isinstance(v, str) and v]
    if not noti:
        return None
    return any(v == "open" for v in noti)


def stato_ricarica(stato: float | None, cavo: float | None) -> StatoRicarica | None:
    if stato is None:
        return None
    if stato == _ERRORE:
        # Sulle ZOE fino alla ZE40 vuol dire «non in carica»: col cavo staccato lo è di sicuro.
        return StatoRicarica.ERRORE if cavo == 1 else StatoRicarica.NON_IN_CARICA
    return _RICARICA.get(round(stato, 1))


def cavo_collegato(cavo: float | None, stato: float | None) -> bool | None:
    if cavo == 1:
        return True
    if cavo == 0:
        return False
    # Spina «in errore» o sconosciuta: lo dice, se può, lo stato della ricarica.
    if stato is not None and round(stato, 1) in _CAVO_DA_RICARICA:
        return True
    return None


def interpreta(parti: dict[str, dict[str, Any]], *, potenza_in_watt: bool = False) -> StatoVettura:
    """``parti`` sono gli ``attributes`` di ogni lettura, per nome della lettura."""
    batteria = parti.get("battery-status") or {}
    cockpit = parti.get("cockpit") or {}
    clima = parti.get("hvac-status") or {}
    posizione = parti.get("location") or {}
    porte = parti.get("lock-status") or {}
    gomme = parti.get("pressure") or {}
    limiti = parti.get("soc-levels") or {}
    # La posizione resta fuori dal grezzo: finisce nella diagnostica.
    s = StatoVettura(grezzo={k: v for k, v in parti.items() if k != "location"})

    s.batteria = _numero(batteria.get("batteryLevel"))
    s.autonomia_km = _numero(batteria.get("batteryAutonomy"))
    s.temperatura_batteria_c = _numero(batteria.get("batteryTemperature"))
    energia = _numero(batteria.get("batteryAvailableEnergy"))
    # Le ZOE più vecchie dicono 0 kWh per «non lo so».
    s.energia_residua_kwh = energia or None
    stato = _numero(batteria.get("chargingStatus"))
    cavo = _numero(batteria.get("plugStatus"))
    s.stato_ricarica = stato_ricarica(stato, cavo)
    s.cavo_collegato = cavo_collegato(cavo, stato)
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        potenza = _numero(batteria.get("chargingInstantaneousPower"))
        if potenza is not None:
            s.potenza_ricarica_diretta_kw = round(potenza / 1000, 3) if potenza_in_watt else potenza
        minuti = _numero(batteria.get("chargingRemainingTime"))
        s.tempo_ricarica_min = int(minuti) if minuti is not None else None
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0

    s.odometro_km = _numero(cockpit.get("totalMileage"))

    s.temperatura_esterna_c = _numero(clima.get("externalTemperature"))
    s.temperatura_interna_c = _numero(clima.get("internalTemperature"))
    if isinstance(clima.get("hvacStatus"), str):
        s.clima_acceso = clima["hvacStatus"].lower() == "on"

    s.latitudine = _numero(posizione.get("gpsLatitude"))
    s.longitudine = _numero(posizione.get("gpsLongitude"))
    s.posizione_con_segno = s.latitudine is not None and s.longitudine is not None

    if isinstance(porte.get("lockStatus"), str):
        s.chiusa = porte["lockStatus"].lower() == "locked"
    s.portiere_aperte = _aperto(*(porte.get(p) for p in _PORTE))
    s.bagagliaio_aperto = _aperto(porte.get("hatchStatus"))

    for ruota, campo in _RUOTE.items():
        if (mbar := _numero(gomme.get(campo))) is not None and mbar > 0:
            s.pneumatici_kpa[ruota] = round(mbar / 10, 1)

    if (limite := _numero(limiti.get("socTarget"))) is not None:
        s.limite_ricarica = int(limite)

    quando = [
        t
        for t in (
            _quando(batteria.get("timestamp")),
            _quando(clima.get("lastUpdateTime")),
            _quando(posizione.get("lastUpdateTime")),
            _quando(porte.get("lastUpdateTime")),
        )
        if t is not None
    ]
    s.letto = max(quando) if quando else None
    return s
