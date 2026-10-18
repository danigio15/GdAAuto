"""Dallo stato del cloud europeo di Hyundai, Kia e Genesis allo stato di GdAAuto.

Il cloud parla due lingue, secondo l'auto:

- le auto recenti (EV3, EV9, Ioniq 5 e 6 aggiornate, Inster…) usano il
  protocollo «CCS2»: un albero con ``Green`` (la batteria di trazione e la
  ricarica), ``Cabin`` (porte, finestrini, clima), ``Body``, ``Chassis``
  (le gomme), ``Drivetrain`` (autonomia e chilometri) e ``Location``;
- le altre (e-Niro, Kona, Ioniq, EV6 dei primi anni…) il formato vecchio,
  con tutto in ``vehicleStatus`` e ``evStatus``.

È sempre lo stato che il cloud tiene in memoria, cioè quello che l'auto ha
mandato l'ultima volta: leggerlo non sveglia l'auto.

Le posizioni dei campi vengono da Hyundai-Kia-Connect/hyundai_kia_connect_api
(``KiaUvoApiEU.py``, ``ApiImplType1.py``), licenza MIT.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from email.utils import parsedate_to_datetime
from functools import partial
import re
from typing import Any

from ...vettura import Marcia, StatoRicarica, StatoVettura
from .firma import FUSO_CLOUD

_MIGLIO = 1.609344
_PSI = 6.894757
#: Distanze: 1 chilometri, 2 e 3 miglia.
_MIGLIA = (2, 3)
#: Gomme: l'unità che l'auto mostra, e per quanto moltiplicare il valore grezzo.
_GOMME_SCALA = {0: 1.0, 1: 5.0, 2: 0.1}
_GOMME_IN_KPA = {0: _PSI, 1: 1.0, 2: 100.0}
_RUOTE = {
    "anteriore_sinistro": ("Row1", "Left"),
    "anteriore_destro": ("Row1", "Right"),
    "posteriore_sinistro": ("Row2", "Left"),
    "posteriore_destro": ("Row2", "Right"),
}
_PORTE = (("Row1", "Driver"), ("Row1", "Passenger"), ("Row2", "Left"), ("Row2", "Right"))
_LATI_VECCHI = ("frontLeft", "frontRight", "backLeft", "backRight")


def valore(dati: Any, percorso: str) -> Any:
    """``valore(d, "Green.ChargingInformation.TargetSoC.Standard")``; ``None`` se manca."""
    for chiave in percorso.split("."):
        if isinstance(dati, dict):
            dati = dati.get(chiave)
        elif isinstance(dati, list) and chiave.isdigit() and int(chiave) < len(dati):
            dati = dati[int(chiave)]
        else:
            return None
    return dati


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _vero(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "on")
    return bool(v)


def _km(v: Any, unita: Any) -> float | None:
    n = _numero(v)
    if n is None:
        return None
    return round(n * _MIGLIO, 1) if unita in _MIGLIA else n


def _celsius(v: Any, unita: Any) -> float | None:
    n = _numero(v)
    if n is None:
        return None
    return round((n - 32) * 5 / 9, 1) if unita == 1 else n


def data_cloud(v: Any, fuso: tzinfo) -> datetime | None:
    """«20240915140000», «20250420202723.000» o «Tue, 24 Jun 2025 16:18:10 GMT».

    La forma compatta non dice il fuso: chi chiama sa qual è (``Date`` del
    CCS2 è UTC, il resto è l'ora di Berlino).
    """
    if not isinstance(v, str) or not v.strip():
        return None
    if v.strip().endswith("GMT"):
        try:
            return parsedate_to_datetime(v).astimezone(UTC)
        except (TypeError, ValueError):
            return None
    cifre = re.sub(r"\D", "", v.split(".")[0])
    if len(cifre) < 14:
        return None
    try:
        locale = datetime.strptime(cifre[:14], "%Y%m%d%H%M%S").replace(tzinfo=fuso)
    except ValueError:
        return None
    return locale.astimezone(UTC)


# --- le auto recenti (CCS2) -----------------------------------------------------


def _ricarica(s: StatoVettura, cavo: bool | None, in_carica: bool) -> None:
    s.cavo_collegato = cavo
    if in_carica and cavo is not False:
        s.stato_ricarica = StatoRicarica.IN_CARICA
    elif cavo and s.batteria is not None and s.limite_ricarica is not None and s.batteria >= s.limite_ricarica:
        s.stato_ricarica = StatoRicarica.COMPLETATA
    else:
        s.stato_ricarica = StatoRicarica.NON_IN_CARICA


def interpreta_ccs2(stato: dict[str, Any], tipo: str = "EV") -> StatoVettura:
    """``resMsg.state.Vehicle`` di ``/ccs2/carstatus/latest``."""
    s = StatoVettura(grezzo=stato)
    v = partial(valore, stato)

    s.batteria = _numero(v("Green.BatteryManagement.BatteryRemain.Ratio"))
    unita = v("Drivetrain.FuelSystem.DTE.Unit")
    autonomia = v("Drivetrain.FuelSystem.DTE.EV") if tipo == "PHEV" else None
    s.autonomia_km = _km(autonomia if autonomia is not None else v("Drivetrain.FuelSystem.DTE.Total"), unita)
    s.odometro_km = _numero(v("Drivetrain.Odometer"))

    residua = _numero(v("Green.BatteryManagement.BatteryRemain.Value"))
    if residua:
        # Le auto vere la danno in kJ; qualche risposta già in kWh.
        kj = v("Green.BatteryManagement.BatteryRemain.Unit") == "kJ" or residua > 500
        s.energia_residua_kwh = round(residua / 3600, 2) if kj else residua
    temperature = [
        _numero(v(f"Green.BatteryManagement.Temperature.{estremo}.Decimal"))
        or _numero(v(f"Green.BatteryManagement.Temperature.{estremo}.Raw"))
        for estremo in ("Min", "Max")
    ]
    if temperature := [t for t in temperature if t is not None]:
        s.temperatura_batteria_c = round(sum(temperature) / len(temperature), 1)

    limite = _numero(v("Green.ChargingInformation.TargetSoC.Standard"))
    s.limite_ricarica = int(limite) if limite else None
    resta = _numero(v("Green.ChargingInformation.Charging.RemainTime"))
    potenza = _numero(v("Green.Electric.SmartGrid.RealTimePower"))
    in_carica = bool(resta and resta > 0) or bool(potenza and potenza > 0)
    _ricarica(s, _vero(v("Green.ChargingInformation.ConnectorFastening.State")), in_carica)
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.tempo_ricarica_min = int(resta) if resta else None
        s.potenza_ricarica_diretta_kw = potenza
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0

    # Le porte: «Lock» a 0 vuol dire chiusa a chiave.
    serrature = [v(f"Cabin.Door.{fila}.{lato}.Lock") for fila, lato in _PORTE]
    if any(x is not None for x in serrature):
        s.chiusa = not any(_vero(x) for x in serrature if x is not None)
    porte = [v(f"Cabin.Door.{fila}.{lato}.Open") for fila, lato in _PORTE]
    if any(x is not None for x in porte):
        s.portiere_aperte = any(_vero(x) for x in porte if x is not None)
    if (bagagliaio := v("Body.Trunk.Open")) is not None:
        s.bagagliaio_aperto = bool(_vero(bagagliaio))
    finestrini = [
        x
        for fila, lato in _PORTE
        for x in (v(f"Cabin.Window.{fila}.{lato}.Open"), v(f"Cabin.Window.{fila}.{lato}.OpenLevel"))
    ]
    if any(x is not None for x in finestrini):
        s.finestrini_aperti = any(_vero(x) for x in finestrini if x is not None)

    gradi = v("Cabin.HVAC.Row1.Driver.Temperature.Value")
    if gradi not in (None, "OFF"):
        s.temperatura_clima_c = _celsius(gradi, v("Cabin.HVAC.Row1.Driver.Temperature.Unit"))
    ventola = _numero(v("Cabin.HVAC.Row1.Driver.Blower.SpeedLevel"))
    if ventola is not None:
        s.clima_acceso = ventola > 0
    elif gradi is not None:
        s.clima_acceso = gradi != "OFF"
    s.temperatura_esterna_c = _celsius(
        v("Cabin.HVAC.OutsideTemperature.Value"), v("Cabin.HVAC.OutsideTemperature.Unit")
    )

    unita_gomme = v("Chassis.Axle.Tire.PressureUnit")
    if unita_gomme in _GOMME_SCALA:
        for ruota, (fila, lato) in _RUOTE.items():
            grezza = _numero(v(f"Chassis.Axle.{fila}.{lato}.Tire.Pressure"))
            if grezza is None or grezza == 255:  # 255: il sensore non ha ancora letto
                continue
            s.pneumatici_kpa[ruota] = round(grezza * _GOMME_SCALA[unita_gomme] * _GOMME_IN_KPA[unita_gomme], 1)

    pronta = v("DrivingReady")
    if pronta is None:
        pronta = v("Drivetrain.DrivingReady")
    s.quadro_acceso = _vero(pronta)
    if v("Drivetrain.Transmission.ParkingPosition") == 1:
        s.marcia = Marcia.PARCHEGGIO

    lat, lon = _numero(v("Location.GeoCoord.Latitude")), _numero(v("Location.GeoCoord.Longitude"))
    if lat is not None and lon is not None and (lat or lon):
        s.latitudine, s.longitudine, s.posizione_con_segno = lat, lon, True
    s.letto = data_cloud(v("Date"), UTC)
    return s


# --- le auto col formato vecchio --------------------------------------------------

#: Le temperature del clima, come indici esadecimali: «10H» è 22 °C.
_GRADI_VECCHI = tuple(x * 0.5 for x in range(28, 64))


def _gradi_esadecimali(codice: Any) -> float | None:
    if not isinstance(codice, str):
        return None
    try:
        indice = int(codice.upper().replace("H", ""), 16)
    except ValueError:
        return None
    return _GRADI_VECCHI[indice] if 0 <= indice < len(_GRADI_VECCHI) else None


def interpreta_vecchio(info: dict[str, Any], tipo: str = "EV") -> StatoVettura:
    """``resMsg.vehicleStatusInfo`` di ``/status/latest``."""
    s = StatoVettura(grezzo=info)
    v = partial(valore, info)
    ev = "vehicleStatus.evStatus"

    s.batteria = _numero(v(f"{ev}.batteryStatus"))
    distanza = f"{ev}.drvDistance.0.rangeByFuel"
    chiave = f"{distanza}.evModeRange" if v(f"{distanza}.evModeRange.value") is not None else None
    chiave = chiave or f"{distanza}.totalAvailableRange"
    s.autonomia_km = _km(v(f"{chiave}.value"), v(f"{chiave}.unit"))
    s.odometro_km = _km(v("odometer.value"), v("odometer.unit"))

    soglie = v(f"{ev}.reservChargeInfos.targetSOClist") or []
    ac = [x.get("targetSOClevel") for x in soglie if isinstance(x, dict) and x.get("plugType") == 1]
    if ac and _numero(ac[-1]):
        s.limite_ricarica = int(ac[-1])
    spina = v(f"{ev}.batteryPlugin")
    cavo = None if spina is None else bool(_numero(spina))
    _ricarica(s, cavo, bool(_vero(v(f"{ev}.batteryCharge"))))
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        resta = _numero(v(f"{ev}.remainTime2.atc.value"))
        s.tempo_ricarica_min = int(resta) if resta else None
        s.potenza_ricarica_diretta_kw = _numero(v(f"{ev}.batteryPower.batteryStndChrgPower"))
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0

    if (chiusa := v("vehicleStatus.doorLock")) is not None:
        s.chiusa = bool(_vero(chiusa))
    porte = v("vehicleStatus.doorOpen")
    if isinstance(porte, dict):
        s.portiere_aperte = any(_vero(porte.get(lato)) for lato in _LATI_VECCHI)
    if (bagagliaio := v("vehicleStatus.trunkOpen")) is not None:
        s.bagagliaio_aperto = bool(_vero(bagagliaio))
    finestrini = v("vehicleStatus.windowOpen")
    if isinstance(finestrini, dict):
        s.finestrini_aperti = any(_vero(finestrini.get(lato)) for lato in _LATI_VECCHI)

    if (clima := v("vehicleStatus.airCtrlOn")) is not None:
        s.clima_acceso = bool(_vero(clima))
    if v("vehicleStatus.airTemp.unit") in (0, None):
        s.temperatura_clima_c = _gradi_esadecimali(v("vehicleStatus.airTemp.value"))
    s.quadro_acceso = _vero(v("vehicleStatus.engine"))

    lat, lon = _numero(v("vehicleLocation.coord.lat")), _numero(v("vehicleLocation.coord.lon"))
    if lat is not None and lon is not None and (lat or lon):
        s.latitudine, s.longitudine, s.posizione_con_segno = lat, lon, True
    s.letto = data_cloud(v("vehicleStatus.time"), FUSO_CLOUD)
    return s


# --- il resto ---------------------------------------------------------------------


def posizione_parcheggio(res: Any) -> tuple[float, float] | None:
    """``resMsg`` di ``/location/park`` (o ``/location``): dove è parcheggiata."""
    if not isinstance(res, dict):
        return None
    dettaglio = res.get("gpsDetail") if isinstance(res.get("gpsDetail"), dict) else res
    lat, lon = _numero(valore(dettaglio, "coord.lat")), _numero(valore(dettaglio, "coord.lon"))
    if lat is None or lon is None or not (lat or lon):
        return None
    return lat, lon


def limite_rapida(grezzo: dict[str, Any]) -> int | None:
    """Il limite della ricarica in corrente continua, per lasciarlo com'è."""
    n = _numero(valore(grezzo, "Green.ChargingInformation.TargetSoC.Quick"))
    if n is None:
        soglie = valore(grezzo, "vehicleStatus.evStatus.reservChargeInfos.targetSOClist") or []
        dc = [x.get("targetSOClevel") for x in soglie if isinstance(x, dict) and x.get("plugType") == 0]
        n = _numero(dc[-1]) if dc else None
    return int(n) if n else None
