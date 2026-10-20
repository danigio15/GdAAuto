"""Da un veicolo di Enode allo stato di GdAAuto.

Enode dà ogni auto collegata come un oggetto con dentro:

- ``information``: ``vin``, ``brand``, ``model``, ``year``, ``displayName``;
- ``chargeState``: ``batteryLevel`` (%), ``range`` (km), ``isPluggedIn``,
  ``isCharging``, ``isFullyCharged``, ``chargeLimit`` (%), ``chargeRate``
  (kW), ``chargeTimeRemaining`` (minuti), ``batteryCapacity`` (kWh),
  ``maxCurrent`` (A), ``powerDeliveryState`` (``UNPLUGGED``,
  ``PLUGGED_IN:CHARGING``, ``PLUGGED_IN:COMPLETE``…) e ``lastUpdated``;
- ``location``: ``latitude``, ``longitude``, ``lastUpdated`` (le Xpeng la
  mandano quando si spengono, non in marcia);
- ``odometer``: ``distance`` (km), ``lastUpdated`` (le Xpeng spesso non lo mandano);
- ``capabilities``: per ogni cosa ``isCapable`` (``startCharging``,
  ``stopCharging``…); ``isReachable`` e ``lastSeen``.

Un campo che Enode non ha per quell'auto arriva ``null``. Nomi e unità vengono
da mnordseth/xpeng-homeassistant (licenza MIT) e dalla documentazione
pubblica di Enode; gli stati di ``powerDeliveryState`` anche da
decline27/Xpeng (GPL: solo i nomi, il codice è scritto da capo).
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from ...vettura import StatoRicarica, StatoVettura, Vettura

ID = "xpeng"
NOME = "Xpeng"

_CONSEGNA = {
    "UNPLUGGED": StatoRicarica.NON_IN_CARICA,
    "PLUGGED_IN:INITIALIZING": StatoRicarica.NON_IN_CARICA,
    "PLUGGED_IN:CHARGING": StatoRicarica.IN_CARICA,
    "PLUGGED_IN:STOPPED": StatoRicarica.IN_PAUSA,
    "PLUGGED_IN:NO_POWER": StatoRicarica.IN_PAUSA,
    "PLUGGED_IN:COMPLETE": StatoRicarica.COMPLETATA,
    "PLUGGED_IN:FAULT": StatoRicarica.ERRORE,
    "PLUGGED_IN:DISCHARGING": StatoRicarica.NON_IN_CARICA,
}


def numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def quando(v: Any) -> datetime | None:
    """Le date ISO di Enode (``…Z``)."""
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _vero(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def capace(veicolo: dict[str, Any], cosa: str) -> bool:
    return bool(((veicolo.get("capabilities") or {}).get(cosa) or {}).get("isCapable"))


def vettura_da(veicolo: dict[str, Any]) -> Vettura | None:
    """Un veicolo di ``/vehicles`` → ``Vettura`` (``None`` se non ha né id né VIN)."""
    info = veicolo.get("information") or {}
    id_ = str(veicolo.get("id") or "")
    vin = str(info.get("vin") or "").strip().upper()
    if not id_ or not vin:
        return None
    modello = str(info.get("model") or NOME).strip()
    nome = str(info.get("displayName") or "").strip()
    if not nome:
        nome = modello if modello.upper().startswith("XPENG") else f"{NOME} {modello}"
    anno = numero(info.get("year"))
    return Vettura(
        vin=vin,
        marca=ID,
        modello=modello,
        nome=nome,
        anno=int(anno) if anno else None,
        extra={
            "id": id_,
            "vendor": str(veicolo.get("vendor") or ""),
            "ricarica": capace(veicolo, "startCharging") or capace(veicolo, "stopCharging"),
        },
    )


def interpreta(veicolo: dict[str, Any]) -> StatoVettura:
    carica = veicolo.get("chargeState") or {}
    posizione = veicolo.get("location") or {}
    contakm = veicolo.get("odometer") or {}
    s = StatoVettura()

    livello = numero(carica.get("batteryLevel"))
    s.batteria = livello if livello is not None and 0 <= livello <= 100 else None
    autonomia = numero(carica.get("range"))
    s.autonomia_km = autonomia if autonomia is not None and autonomia >= 0 else None
    limite = numero(carica.get("chargeLimit"))
    s.limite_ricarica = int(limite) if limite is not None and 0 < limite <= 100 else None
    capacita = numero(carica.get("batteryCapacity"))
    s.capacita_batteria_kwh = capacita if capacita and capacita > 0 else None

    consegna = str(carica.get("powerDeliveryState") or "").upper()
    collegata = _vero(carica.get("isPluggedIn"))
    if consegna:
        collegata = consegna.startswith("PLUGGED_IN") if collegata is None else collegata
    s.cavo_collegato = collegata
    in_carica = _vero(carica.get("isCharging"))
    s.stato_ricarica = _CONSEGNA.get(consegna)
    if s.stato_ricarica is None:
        if in_carica:
            s.stato_ricarica = StatoRicarica.IN_CARICA
        elif carica.get("isFullyCharged") is True and collegata:
            s.stato_ricarica = StatoRicarica.COMPLETATA
        elif in_carica is False or collegata is False:
            s.stato_ricarica = StatoRicarica.NON_IN_CARICA
    if consegna == "PLUGGED_IN:DISCHARGING":
        s.v2l_attivo = True
    elif consegna:
        s.v2l_attivo = False

    potenza = numero(carica.get("chargeRate"))
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.potenza_ricarica_diretta_kw = potenza if potenza is not None and potenza >= 0 else None
        minuti = numero(carica.get("chargeTimeRemaining"))
        s.tempo_ricarica_min = int(minuti) if minuti is not None and minuti >= 0 else None
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0

    km = numero(contakm.get("distance"))
    s.odometro_km = km if km is not None and km > 0 else None

    lat, lon = numero(posizione.get("latitude")), numero(posizione.get("longitude"))
    if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0):
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True

    momenti = [
        m
        for m in (
            quando(carica.get("lastUpdated")),
            quando(posizione.get("lastUpdated")),
            quando(contakm.get("lastUpdated")),
        )
        if m
    ]
    s.letto = max(momenti) if momenti else quando(veicolo.get("lastSeen"))
    s.grezzo = {
        "vendor": veicolo.get("vendor"),
        "isReachable": veicolo.get("isReachable"),
        "lastSeen": veicolo.get("lastSeen"),
        "chargeState": carica,
        "odometer": contakm,
        "smartChargingPolicy": veicolo.get("smartChargingPolicy"),
        "capabilities": {k: bool((c or {}).get("isCapable")) for k, c in (veicolo.get("capabilities") or {}).items()},
    }
    return s
