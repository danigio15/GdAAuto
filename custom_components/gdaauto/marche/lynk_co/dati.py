"""Lo stato di una Lynk & Co (01 dal 2025, 02, 08), dalle risposte del cloud dell'app.

Il cloud ``agw.mobile-app.lynkco.biz`` divide l'auto in pezzi, ognuno col suo
indirizzo: ``vehicle_data`` (serratura, in marcia), ``charge_state``
(batteria e ricarica), ``climate_state``, ``doors_windows_state``,
``location_state``, ``fuel_state`` (le ibride) e ``vehicle_metadata``
(contachilometri, capacità della batteria). La carica e il carburante
arrivano come frazione (0,57 = 57 %).

I nomi dei campi e il loro significato vengono da ha_lynkco_2025 (b12e,
licenza MIT): vedi RINGRAZIAMENTI.md. I valori di ``status`` della ricarica
che quel progetto non elenca sono interpretati con prudenza: uno sconosciuto
resta «non lo dice».
"""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from ...vettura import StatoRicarica, StatoVettura


def dig(dati: Any, *chiavi: str) -> Any:
    for chiave in chiavi:
        if not isinstance(dati, dict):
            return None
        dati = dati.get(chiave)
    return dati


def numero(valore: Any) -> float | None:
    if valore is None or isinstance(valore, bool):
        return None
    try:
        n = float(valore)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def frazione(valore: Any) -> float | None:
    """0,57 → 57 %; un valore già in percento resta com'è."""
    n = numero(valore)
    if n is None or n < 0:
        return None
    return round(n * 100, 1) if n <= 1 else round(n, 1)


def quando(valore: Any) -> datetime | None:
    if not isinstance(valore, str) or not valore:
        return None
    try:
        return datetime.fromisoformat(valore)
    except ValueError:
        return None


def stato_ricarica(status: Any) -> StatoRicarica | None:
    if not status:
        return None
    s = str(status).upper()
    if s.startswith("NOT_") or s in ("DISCONNECTED", "IDLE", "UNPLUGGED", "INACTIVE"):
        return StatoRicarica.NON_IN_CARICA
    if "FULL" in s or "COMPLETE" in s or "FINISHED" in s or "TARGET" in s:
        return StatoRicarica.COMPLETATA
    if s == "CHARGING" or s.startswith("CHARGING_"):
        return StatoRicarica.IN_CARICA
    if "SCHEDULED" in s or "WAITING" in s or "DELAYED" in s:
        return StatoRicarica.PROGRAMMATA
    if "PAUSE" in s:
        return StatoRicarica.IN_PAUSA
    if "ERROR" in s or "FAULT" in s:
        return StatoRicarica.ERRORE
    return None


def _aperto(valore: Any) -> bool | None:
    if valore is None:
        return None
    return str(valore).upper() != "CLOSED"


def _qualcuno(valori: list[bool | None]) -> bool | None:
    letti = [v for v in valori if v is not None]
    return any(letti) if letti else None


def interpreta(pezzi: dict[str, Any]) -> StatoVettura:
    """Dai pezzi letti (``vehicle_data``, ``charge``, ``climate``…) a uno ``StatoVettura``."""
    s = StatoVettura(grezzo=pezzi)
    batteria = dig(pezzi, "charge", "batteryState") or {}
    meta = pezzi.get("metadata") or {}

    s.batteria = frazione(batteria.get("stateOfCharge"))
    s.autonomia_km = numero(batteria.get("remainingRange"))
    s.stato_ricarica = stato_ricarica(batteria.get("status"))
    if s.stato_ricarica in (StatoRicarica.IN_CARICA, StatoRicarica.COMPLETATA, StatoRicarica.PROGRAMMATA):
        s.cavo_collegato = True
    elif s.stato_ricarica == StatoRicarica.NON_IN_CARICA and str(batteria.get("status")).upper() in (
        "DISCONNECTED",
        "UNPLUGGED",
        "NOT_CONNECTED",
    ):
        s.cavo_collegato = False
    potenza = numero(dig(batteria, "chargingSpeed", "kW"))
    if potenza is not None:
        s.potenza_ricarica_diretta_kw = round(potenza, 2) if s.stato_ricarica == StatoRicarica.IN_CARICA else 0.0
    tempo = numero(batteria.get("remainingChargingTime"))
    if tempo is not None and tempo >= 0 and s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.tempo_ricarica_min = int(tempo)
    limite = numero(dig(batteria, "chargeLimit", "value"))
    if limite is not None and 0 < limite <= 100:
        s.limite_ricarica = int(limite)
    s.consumo_medio_kwh_100km = numero(batteria.get("powerAverageConsumption"))
    s.capacita_batteria_kwh = numero(dig(meta, "batteryInfo", "batteryCapacity"))
    if s.capacita_batteria_kwh and s.batteria is not None:
        s.energia_residua_kwh = round(s.capacita_batteria_kwh * s.batteria / 100, 1)
    s.odometro_km = numero(dig(meta, "vehicle", "odometer"))

    # Le ibride: serbatoio e autonomia a benzina.
    carburante = dig(pezzi, "fuel", "fuelState") or {}
    s.carburante_percento = frazione(carburante.get("percentageOfRemainingFuel"))
    s.autonomia_carburante_km = numero(carburante.get("remainingRange"))
    if s.autonomia_carburante_km is not None and s.autonomia_km is not None:
        s.autonomia_totale_km = s.autonomia_km + s.autonomia_carburante_km

    clima = pezzi.get("climate") or {}
    s.temperatura_interna_c = numero(clima.get("interiorTemperature"))
    s.temperatura_clima_c = numero(clima.get("targetTemperature"))
    if clima.get("status") is not None:
        # ACTIVE_COOLING, ACTIVE_HEATING… accesi; INACTIVE, DISABLED_UNLOCKED_CAR… spenti.
        s.clima_acceso = str(clima["status"]).upper().startswith("ACTIVE")
    parabrezza = dig(clima, "heaters", "windshield", "status")
    if parabrezza is not None:
        s.sbrinamento = str(parabrezza).upper().startswith("ACTIVE")

    auto = pezzi.get("vehicle_data") or {}
    serratura = dig(auto, "centralLock", "status")
    if serratura is not None:
        testo = str(serratura).upper()
        s.chiusa = True if testo == "LOCKED" else False if testo == "UNLOCKED" else None
    if isinstance(auto.get("driveModeEnabled"), bool):
        s.quadro_acceso = auto["driveModeEnabled"]

    porte = pezzi.get("doors") or {}
    s.portiere_aperte = _qualcuno(
        [_aperto(porte.get(f"door{p}Status")) for p in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")]
    )
    s.finestrini_aperti = _qualcuno(
        [_aperto(porte.get(f"window{p}Status")) for p in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")]
    )
    s.bagagliaio_aperto = _aperto(porte.get("trunkStatus"))

    posizione = dig(pezzi, "location", "vehicleLocation") or {}
    lat, lon = numero(dig(posizione, "coordinates", "latitude")), numero(dig(posizione, "coordinates", "longitude"))
    if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0):
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True

    ore = [
        quando(posizione.get("updatedAt")),
        quando(clima.get("updatedAt")),
        quando(carburante.get("updatedAt")),
        quando(batteria.get("updatedAt")),
    ]
    lette = [o for o in ore if o is not None]
    s.letto = max(lette) if lette else None
    return s
