"""Dallo stato del Connected Car API v4 di Stellantis allo stato di GdAAuto.

Vale per le auto del gruppo che usano le app MyPeugeot, MyCitroën, MyDS,
MyOpel e MyVauxhall: le elettriche e le ibride plug-in. La batteria, l'autonomia e la
ricarica stanno in ``energies``, nella voce di tipo ``Electric``.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

_RICARICA = {
    "InProgress": StatoRicarica.IN_CARICA,
    "Finished": StatoRicarica.COMPLETATA,
    "Stopped": StatoRicarica.IN_PAUSA,
    "Disconnected": StatoRicarica.NON_IN_CARICA,
    "Failure": StatoRicarica.ERRORE,
}

_DURATA = re.compile(r"^P(?:(?P<g>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>[\d.]+)S)?)?$")


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def minuti(durata: Any) -> int | None:
    """«PT1H23M» → 83. ISO 8601, come lo manda il cloud."""
    if not isinstance(durata, str) or not (m := _DURATA.match(durata.strip())):
        return None
    g, h, mi, s = (float(m.group(k) or 0) for k in ("g", "h", "m", "s"))
    return round(g * 1440 + h * 60 + mi + s / 60)


def _data(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _energia(dati: dict[str, Any], tipo: str) -> dict[str, Any]:
    for voce in dati.get("energies") or dati.get("energy") or []:
        if isinstance(voce, dict) and voce.get("type") == tipo:
            return voce
    return {}


def interpreta(dati: dict[str, Any]) -> StatoVettura:
    s = StatoVettura(grezzo=dati)
    elettrica = _energia(dati, "Electric")
    estensione = (elettrica.get("extension") or {}).get("electric") or {}
    carica = estensione.get("charging") or {}

    s.batteria = _numero(elettrica.get("level"))
    s.autonomia_km = _numero(elettrica.get("autonomy"))
    residua = ((estensione.get("battery") or {}).get("load") or {}).get("residual")
    s.energia_residua_kwh = _numero(residua)
    s.stato_ricarica = _RICARICA.get(str(carica.get("status")))
    if isinstance(carica.get("plugged"), bool):
        s.cavo_collegato = carica["plugged"]
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.tempo_ricarica_min = minuti(carica.get("remainingTime"))

    s.odometro_km = _numero((dati.get("odometer") or {}).get("mileage"))
    cinetica = dati.get("kinetic") or {}
    s.velocita_kmh = _numero(cinetica.get("speed"))
    if cinetica.get("moving") is False and s.velocita_kmh is None:
        s.velocita_kmh = 0.0
    s.temperatura_esterna_c = _numero(((dati.get("environment") or {}).get("air") or {}).get("temp"))

    porte = dati.get("doorsState") or {}
    chiusura = porte.get("lockedStates") or porte.get("lockedState")
    stati = chiusura if isinstance(chiusura, list) else [chiusura] if chiusura else []
    if stati:
        s.chiusa = not any(str(x) == "Unlocked" for x in stati)
    aperture = {str(o.get("identifier")): o.get("state") for o in porte.get("opening") or [] if isinstance(o, dict)}
    porte_vere = [aperture[k] for k in ("Driver", "Passenger", "RearLeft", "RearRight") if k in aperture]
    if porte_vere:
        s.portiere_aperte = any(x == "Open" for x in porte_vere)
    if "Trunk" in aperture:
        s.bagagliaio_aperto = aperture["Trunk"] == "Open"

    clima = (dati.get("preconditioning") or dati.get("preconditionning") or {}).get("airConditioning") or {}
    if clima.get("status"):
        s.clima_acceso = clima["status"] in ("Enabled", "Active", "InProgress")
    accensione = (dati.get("ignition") or {}).get("type")
    if accensione:
        s.quadro_acceso = accensione in ("StartUp", "Start")

    coordinate = ((dati.get("lastPosition") or {}).get("geometry") or {}).get("coordinates") or []
    if len(coordinate) >= 2:
        s.longitudine, s.latitudine = _numero(coordinate[0]), _numero(coordinate[1])
        s.posizione_con_segno = True

    date = [
        d
        for d in (
            _data(elettrica.get("updatedAt") or elettrica.get("createdAt")),
            _data(dati.get("updatedAt")),
            _data((dati.get("lastPosition") or {}).get("properties", {}).get("updatedAt")),
        )
        if d
    ]
    s.letto = max(date) if date else None
    return s
