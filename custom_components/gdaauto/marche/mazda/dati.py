"""Dalle risposte del cloud Mazda 6e allo stato di GdAAuto.

Lo stato (``cma-app-car-condition/api/vehicle/condition/v2``) arriva diviso
in gruppi:

- ``vehicleStatus``: ``soc`` (%), ``drvMileage`` (autonomia, km),
  ``totalMileage`` (km), ``speed`` (km/h), ``powerStatus`` (0 spenta,
  1 accessori, 2 accesa), ``status`` (1 in marcia, 2 parcheggiata),
  ``connectStatus`` (l'auto è in linea), ``steeringWheelHeater``;
- ``charge``: ``chargeStatus`` (0 non carica, 4 finita, 6 in carica,
  7 in pausa), ``chargeConStatus`` e ``dcChargeGunConnectStatus`` (1 = spina
  AC o DC inserita), ``chargeCurrent`` (A), ``maxSocPercent`` (il limite),
  ``remainChargeTime`` (minuti; 8191 = «non si sa»);
- ``hvac``: temperature in **decimi di grado** (``insideTemp``,
  ``remoteTemp``; 0 = «non si sa»), ``acStatus``, ``defrostStatus``;
- ``door``: ``doors`` (quattro 0/1), ``trunk``, ``driverLock`` e
  ``passengerLock`` (0 chiusa a chiave, 1 aperta);
- ``window``: ``windows`` (quattro 0/1);
- ``tire``: ``leftFront``… con ``pressure`` in kPa;
- ``location``: la posizione (i nomi dei campi cambiano, si provano i soliti);
- ``lastUpdatedAt``: quando l'auto ha mandato i dati, in millisecondi.

Nomi e significati vengono da fano0001/home-assistant-mazda-6e (Apache 2.0):
vedi RINGRAZIAMENTI.md. Un gruppo o un campo che manca resta ``None``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import Marcia, StatoRicarica, StatoVettura, Vettura

ID = "mazda"

#: ``remainChargeTime`` quando l'auto non sa quanto manca (0x1FFF)
_TEMPO_IGNOTO = 8191

_RICARICA = {
    0: StatoRicarica.NON_IN_CARICA,
    4: StatoRicarica.COMPLETATA,
    6: StatoRicarica.IN_CARICA,
    7: StatoRicarica.IN_PAUSA,
}

_GOMME = {
    "leftFront": "anteriore_sinistro",
    "rightFront": "anteriore_destro",
    "leftBack": "posteriore_sinistro",
    "rightBack": "posteriore_destro",
}

_LATITUDINE = ("latitude", "lat", "gpsLatitude")
_LONGITUDINE = ("longitude", "lng", "lon", "gpsLongitude")

#: Cosa chiedere allo stato: tutti i gruppi che GdAAuto usa.
CRITERI = {
    "seat": "1",
    "tire": "1",
    "charge": "1",
    "vehicleStatus": "1",
    "hvac": "1",
    "departurePlan": "0",
    "fuel": "0",
    "window": "1",
    "door": "1",
    "airConditionPlan": "0",
    "lamp": "1",
    "warmCoolingBox": "0",
    "welcome": "0",
    "location": "1",
}


def _numero(valore: Any) -> float | None:
    if valore is None or isinstance(valore, bool):
        return None
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def _intero(valore: Any) -> int | None:
    n = _numero(valore)
    return int(n) if n is not None else None


def _gruppo(stato: dict[str, Any], nome: str) -> dict[str, Any]:
    gruppo = stato.get(nome)
    return gruppo if isinstance(gruppo, dict) else {}


def _decimi(valore: Any) -> float | None:
    n = _numero(valore)
    return None if n is None or n == 0 else round(n / 10, 1)


def _qualcuno(valori: Any) -> bool | None:
    """Uno di quattro aperto; ``None`` se l'auto non dice niente."""
    if not isinstance(valori, list):
        return None
    noti = [n for n in (_numero(v) for v in valori) if n is not None]
    return any(noti) if noti else None


def _primo(gruppo: dict[str, Any], nomi: tuple[str, ...]) -> float | None:
    for nome in nomi:
        if (n := _numero(gruppo.get(nome))) is not None:
            return n
    return None


def _quando(valore: Any) -> datetime | None:
    n = _intero(valore)
    return datetime.fromtimestamp(n / 1000, tz=UTC) if n and n > 0 else None


def interpreta(stato: dict[str, Any] | None) -> StatoVettura:
    """``data`` dello stato → ``StatoVettura``."""
    stato = stato if isinstance(stato, dict) else {}
    vs = _gruppo(stato, "vehicleStatus")
    carica = _gruppo(stato, "charge")
    clima = _gruppo(stato, "hvac")
    porte = _gruppo(stato, "door")
    finestrini = _gruppo(stato, "window")
    gomme = _gruppo(stato, "tire")
    posto = _gruppo(stato, "location")

    s = StatoVettura(grezzo=stato)
    s.batteria = _numero(vs.get("soc"))
    s.autonomia_km = _numero(vs.get("drvMileage"))
    s.odometro_km = _numero(vs.get("totalMileage"))
    velocita = _numero(vs.get("speed"))
    s.velocita_kmh = velocita if velocita is not None and 0 <= velocita <= 250 else None
    marcia = _intero(vs.get("status"))
    s.marcia = {1: Marcia.AVANTI, 2: Marcia.PARCHEGGIO}.get(marcia) if marcia is not None else None
    if (accensione := _intero(vs.get("powerStatus"))) is not None:
        s.quadro_acceso = accensione == 2
    if (in_linea := _intero(vs.get("connectStatus"))) is not None:
        s.in_sonno = in_linea == 0

    if (stato_carica := _intero(carica.get("chargeStatus"))) is not None:
        s.stato_ricarica = _RICARICA.get(stato_carica)
    ac = _intero(carica.get("chargeConStatus"))
    dc = _intero(carica.get("dcChargeGunConnectStatus"))
    if ac is not None or dc is not None:
        s.cavo_collegato = ac == 1 or dc == 1
    if dc is not None:
        s.ricarica_rapida = dc == 1
    tempo = _intero(carica.get("remainChargeTime"))
    if tempo is not None and 0 <= tempo < _TEMPO_IGNOTO:
        s.tempo_ricarica_min = tempo
    if (limite := _intero(carica.get("maxSocPercent"))) is not None and 0 < limite <= 100:
        s.limite_ricarica = limite

    s.temperatura_interna_c = _decimi(clima.get("insideTemp"))
    s.temperatura_clima_c = _decimi(clima.get("remoteTemp"))
    if (acceso := _intero(clima.get("acStatus"))) is not None:
        s.clima_acceso = bool(acceso)
    if (sbrina := _intero(clima.get("defrostStatus"))) is not None:
        s.sbrinamento = bool(sbrina)

    serrature = [_intero(porte.get("driverLock")), _intero(porte.get("passengerLock"))]
    if any(x is not None for x in serrature):
        s.chiusa = all(x == 0 for x in serrature if x is not None)
    s.portiere_aperte = _qualcuno(porte.get("doors"))
    if (baule := _intero(porte.get("trunk"))) is not None:
        s.bagagliaio_aperto = bool(baule)
    s.finestrini_aperti = _qualcuno(finestrini.get("windows"))

    for sua, nostra in _GOMME.items():
        gomma = gomme.get(sua)
        pressione = _numero(gomma.get("pressure")) if isinstance(gomma, dict) else None
        if pressione is not None and pressione > 0:
            s.pneumatici_kpa[nostra] = pressione

    lat, lon = _primo(posto, _LATITUDINE), _primo(posto, _LONGITUDINE)
    if lat is not None and lon is not None and (lat or lon):
        s.latitudine, s.longitudine = lat, lon
    s.letto = _quando(stato.get("lastUpdatedAt"))
    return s


def vetture_da(elenco: Any) -> list[Vettura]:
    """``data`` dell'elenco delle auto → le auto di GdAAuto."""
    vetture: list[Vettura] = []
    for voce in elenco if isinstance(elenco, list) else []:
        if not isinstance(voce, dict) or not voce.get("vin"):
            continue
        id_auto = voce.get("carId") or voce.get("vehicleId")
        if id_auto in (None, ""):
            continue
        modello = str(voce.get("modelName") or voce.get("seriesName") or "6e").strip()
        nome = str(voce.get("carName") or "").strip()
        vetture.append(
            Vettura(
                vin=str(voce["vin"]),
                marca=ID,
                modello=modello,
                nome=nome or (modello if modello.lower().startswith("mazda") else f"Mazda {modello}"),
                targa=str(voce.get("plateNumber") or ""),
                extra={"id": id_auto, "serie": str(voce.get("seriesName") or "")},
            )
        )
    return vetture
