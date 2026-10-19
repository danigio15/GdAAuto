"""Lo stato di una Volkswagen, dal ``selectivestatus`` del cloud.

Il cloud risponde a sezioni («jobs»), una per servizio dell'auto: ``charging``,
``climatisation``, ``access``, ``measurements``, ``fuelStatus``, ``readiness``…
Ogni voce ha il suo ``value`` col momento in cui l'auto l'ha mandata
(``carCapturedTimestamp``); un servizio che non va ha ``error`` al posto di
``value``, e si salta. Le unità sono nel nome del campo: ``_pct``, ``_km``,
``_kW``, ``_min``, e le temperature in kelvin (``_K``).

Le gomme non ci sono: il cloud europeo non dà la pressione.

I nomi dei campi e il significato dei valori vengono da
tillsteinbach/CarConnectivity-connector-volkswagen (``connector.py``,
``charging.py``) e dal suo nucleo tillsteinbach/CarConnectivity, licenza MIT:
vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

_ZERO_ASSOLUTO = 273.15

#: ``chargingState`` → lo stato di GdAAuto.
_STATI_RICARICA = {
    "charging": StatoRicarica.IN_CARICA,
    "chargePurposeReachedAndConservation": StatoRicarica.COMPLETATA,
    "chargePurposeReachedAndNotConservationCharging": StatoRicarica.COMPLETATA,
    "conservation": StatoRicarica.COMPLETATA,
    "readyForCharging": StatoRicarica.PROGRAMMATA,
    "notReadyForCharging": StatoRicarica.NON_IN_CARICA,
    "off": StatoRicarica.NON_IN_CARICA,
    "discharging": StatoRicarica.NON_IN_CARICA,
    "error": StatoRicarica.ERRORE,
}
#: ``climatisationState`` che vogliono dire «acceso».
_CLIMA_ACCESO = {"heating", "cooling", "ventilation", "on", "heatingAuxiliary"}
#: ``preferredChargeMode`` che aspettano un orario.
_MODI_PROGRAMMATI = {"timer", "preferredChargingTimes", "timerChargingWithClimatisation"}
#: Le porte che non sono portiere.
_NON_PORTIERE = {"bonnet", "trunk", "hood", "rearHatch"}
_BAGAGLIAIO = {"trunk", "rearHatch"}
#: I motori a carburante, nel ``fuelStatus``.
_CARBURANTI = {"gasoline", "diesel", "petrol", "cng", "lpg"}


def valore(d: Any, percorso: str) -> Any:
    """``valore(d, "charging.batteryStatus.value.currentSOC_pct")``; ``None`` se manca un pezzo."""
    for chiave in percorso.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(chiave)
    return d


def _numero(x: Any) -> float | None:
    if isinstance(x, bool) or x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _primo(*valori: Any) -> float | None:
    """Il primo numero fra quelli dati (lo zero conta)."""
    return next((n for v in valori if (n := _numero(v)) is not None), None)


def _celsius(kelvin: Any) -> float | None:
    k = _numero(kelvin)
    # Zero kelvin, o quasi, è un sensore che non c'è.
    return round(k - _ZERO_ASSOLUTO, 1) if k is not None and k > 100 else None


def data_cloud(testo: Any) -> datetime | None:
    """``2023-12-05T08:27:15Z`` o ``2023-09-23T18:30:39.307Z``."""
    if not isinstance(testo, str) or not testo:
        return None
    try:
        return datetime.fromisoformat(testo)
    except ValueError:
        return None


def _date(d: Any) -> list[datetime]:
    """Tutti i ``carCapturedTimestamp`` della risposta."""
    trovate: list[datetime] = []
    if isinstance(d, dict):
        for chiave, v in d.items():
            if chiave == "carCapturedTimestamp" and (quando := data_cloud(v)):
                trovate.append(quando)
            else:
                trovate.extend(_date(v))
    elif isinstance(d, list):
        for v in d:
            trovate.extend(_date(v))
    return trovate


def _motori(d: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Dal ``fuelStatus``: il motore elettrico e quello a carburante (vuoti se non ci sono)."""
    stato = valore(d, "fuelStatus.rangeStatus.value") or {}
    elettrico: dict[str, Any] = {}
    carburante: dict[str, Any] = {}
    for nome in ("primaryEngine", "secondaryEngine"):
        motore = stato.get(nome) if isinstance(stato, dict) else None
        if not isinstance(motore, dict):
            continue
        if motore.get("type") == "electric" and not elettrico:
            elettrico = motore
        elif motore.get("type") in _CARBURANTI and not carburante:
            carburante = motore
    return elettrico, carburante


def tipo_auto(d: dict[str, Any]) -> str | None:
    """``electric``, ``hybrid``, ``gasoline``…, come lo dice l'auto."""
    return valore(d, "fuelStatus.rangeStatus.value.carType") or valore(d, "measurements.fuelLevelStatus.value.carType")


def _porte(stato: dict[str, Any], quali: str) -> list[tuple[str, list[str]]]:
    voci = stato.get(quali) if isinstance(stato, dict) else None
    if not isinstance(voci, list):
        return []
    porte: list[tuple[str, list[str]]] = []
    for voce in voci:
        if isinstance(voce, dict) and voce.get("name"):
            stati = voce.get("status")
            stati = [str(s) for s in stati] if isinstance(stati, list) else [str(stati or "")]
            porte.append((str(voce["name"]), stati))
    return porte


def _aperto(porte: list[tuple[str, list[str]]]) -> bool | None:
    """Una aperta: ``True``; tutte chiuse: ``False``; nessuna che lo dica: ``None``."""
    note = [stati for _, stati in porte if "open" in stati or "closed" in stati]
    if not note:
        return None
    return any("open" in stati for stati in note)


def interpreta(d: dict[str, Any]) -> StatoVettura:
    s = StatoVettura(grezzo=d)
    elettrico, carburante = _motori(d)

    # --- la batteria e l'autonomia ---------------------------------------------------------
    batteria = valore(d, "charging.batteryStatus.value") or {}
    misure = valore(d, "measurements.rangeStatus.value") or {}
    s.batteria = _primo(
        batteria.get("currentSOC_pct"),
        valore(d, "measurements.fuelLevelStatus.value.currentSOC_pct"),
        elettrico.get("currentSOC_pct"),
    )
    s.autonomia_km = _primo(
        batteria.get("cruisingRangeElectric_km"), misure.get("electricRange"), elettrico.get("remainingRange_km")
    )
    if carburante or tipo_auto(d) == "hybrid":
        s.carburante_percento = _primo(
            carburante.get("currentFuelLevel_pct"), valore(d, "measurements.fuelLevelStatus.value.currentFuelLevel_pct")
        )
        s.autonomia_carburante_km = _primo(
            carburante.get("remainingRange_km"), misure.get("gasolineRange"), misure.get("dieselRange")
        )
        s.autonomia_totale_km = _primo(
            valore(d, "fuelStatus.rangeStatus.value.totalRange_km"), misure.get("totalRange_km")
        )

    s.odometro_km = _primo(
        valore(d, "measurements.odometerStatus.value.odometer"),
        valore(d, "vehicleHealthInspection.maintenanceStatus.value.mileage_km"),
    )

    batteria_t = valore(d, "measurements.temperatureBatteryStatus.value") or {}
    minima = _celsius(batteria_t.get("temperatureHvBatteryMin_K"))
    massima = _celsius(batteria_t.get("temperatureHvBatteryMax_K"))
    if minima is not None and massima is not None:
        s.temperatura_batteria_c = round((minima + massima) / 2, 1)
    else:
        s.temperatura_batteria_c = minima if minima is not None else massima
    s.temperatura_esterna_c = _celsius(valore(d, "measurements.temperatureOutsideStatus.value.temperatureOutside_K"))

    # --- la ricarica ----------------------------------------------------------------------
    ricarica = valore(d, "charging.chargingStatus.value") or {}
    stato_ricarica = str(ricarica.get("chargingState") or "")
    s.stato_ricarica = _STATI_RICARICA.get(stato_ricarica)
    if stato_ricarica == "discharging":
        s.v2l_attivo = True
    elif stato_ricarica:
        s.v2l_attivo = False
    tipo = str(ricarica.get("chargeType") or "").lower()
    if s.stato_ricarica == StatoRicarica.IN_CARICA and tipo in ("ac", "dc"):
        s.ricarica_rapida = tipo == "dc"
    potenza = _numero(ricarica.get("chargePower_kW"))
    if potenza is not None:
        s.potenza_ricarica_diretta_kw = potenza if s.stato_ricarica == StatoRicarica.IN_CARICA else 0.0
    minuti = _numero(ricarica.get("remainingChargingTimeToComplete_min"))
    if minuti is not None and s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.tempo_ricarica_min = int(minuti)

    impostazioni = valore(d, "charging.chargingSettings.value") or {}
    if (limite := _numero(impostazioni.get("targetSOC_pct"))) is not None:
        s.limite_ricarica = int(limite)

    modo = valore(d, "charging.chargeMode.value.preferredChargeMode") or ricarica.get("chargeMode")
    if modo:
        s.ricarica_programmata = modo in _MODI_PROGRAMMATI

    spina = valore(d, "charging.plugStatus.value") or {}
    connessione = spina.get("plugConnectionState")
    if connessione in ("connected", "disconnected"):
        s.cavo_collegato = connessione == "connected"
    elif s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.cavo_collegato = True

    # --- il clima ---------------------------------------------------------------------------
    clima = valore(d, "climatisation.climatisationStatus.value") or {}
    stato_clima = clima.get("climatisationState")
    if stato_clima and stato_clima != "invalid":
        s.clima_acceso = stato_clima in _CLIMA_ACCESO
    regole = valore(d, "climatisation.climatisationSettings.value") or {}
    s.temperatura_clima_c = _primo(regole.get("targetTemperature_C"), _celsius(regole.get("targetTemperature_K")))
    lunotti = valore(d, "climatisation.windowHeatingStatus.value.windowHeatingStatus")
    if isinstance(lunotti, list):
        stati = [str(w.get("windowHeatingState")) for w in lunotti if isinstance(w, dict)]
        if any(x in ("on", "off") for x in stati):
            s.sbrinamento = "on" in stati

    # --- porte, finestrini, serratura ------------------------------------------------------------
    accesso = valore(d, "access.accessStatus.value") or {}
    serratura = accesso.get("doorLockStatus")
    if serratura in ("locked", "unlocked"):
        s.chiusa = serratura == "locked"
    elif accesso.get("overallStatus") in ("safe", "unsafe"):
        s.chiusa = accesso["overallStatus"] == "safe"
    porte = _porte(accesso, "doors")
    s.portiere_aperte = _aperto([p for p in porte if p[0] not in _NON_PORTIERE])
    s.bagagliaio_aperto = _aperto([p for p in porte if p[0] in _BAGAGLIAIO])
    s.finestrini_aperti = _aperto(_porte(accesso, "windows"))

    # --- l'auto sveglia o addormentata ---------------------------------------------------------
    connessione_auto = valore(d, "readiness.readinessStatus.value.connectionState") or {}
    if isinstance(connessione_auto.get("isActive"), bool):
        s.quadro_acceso = connessione_auto["isActive"]
    if isinstance(connessione_auto.get("isOnline"), bool):
        s.in_sonno = not connessione_auto["isOnline"]

    date = _date(d)
    s.letto = max(date) if date else None
    return s


def posizione(risposta: Any) -> tuple[float, float] | None:
    """La risposta di ``/parkingposition``: ``{"data": {"lat": …, "lon": …}}``."""
    dati = risposta.get("data") if isinstance(risposta, dict) else None
    if not isinstance(dati, dict):
        return None
    lat, lon = _numero(dati.get("lat")), _numero(dati.get("lon"))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    return lat, lon


def consumo_medio(risposta: Any) -> float | None:
    """Il consumo medio del viaggio lungo (``trips/…/longterm/last``), in kWh/100 km."""
    consumo = _numero(valore(risposta, "data.averageElectricConsumption"))
    return round(consumo, 1) if consumo is not None and 0 < consumo < 100 else None


def impostazioni_clima(d: dict[str, Any]) -> dict[str, Any]:
    """Quello che il comando del clima deve rimandare uguale, preso dalle impostazioni lette."""
    regole = valore(d, "climatisation.climatisationSettings.value") or {}
    chiavi = (
        "climatisationWithoutExternalPower",
        "climatizationAtUnlock",
        "windowHeatingEnabled",
        "zoneFrontLeftEnabled",
        "zoneFrontRightEnabled",
        "zoneRearLeftEnabled",
        "zoneRearRightEnabled",
        "heaterSource",
    )
    return {k: regole[k] for k in chiavi if k in regole and regole[k] is not None}


def impostazioni_ricarica(d: dict[str, Any]) -> dict[str, Any]:
    """Le impostazioni di ricarica da rimandare uguali quando si cambia il limite."""
    regole = valore(d, "charging.chargingSettings.value") or {}
    chiavi = ("maxChargeCurrentAC", "maxChargeCurrentAC_A", "autoUnlockPlugWhenChargedAC")
    return {k: regole[k] for k in chiavi if k in regole and regole[k] not in (None, "")}
