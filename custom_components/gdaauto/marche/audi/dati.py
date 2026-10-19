"""Le risposte del cloud Audi, tradotte in ``Vettura`` e ``StatoVettura``.

Lo stato arriva da una sola chiamata, ``selectivestatus``, divisa in «lavori»
(``access``, ``charging``, ``climatisation``, ``measurements``…): ognuno ha i
suoi blocchi ``{"value": {…, "carCapturedTimestamp": …}}``, e un lavoro che
l'auto non sa fare torna con ``{"error": …}`` al posto di ``value``. Le unità
sono già quelle di GdAAuto (km, kW, %), tranne le temperature, in kelvin.

La posizione ha la sua chiamata (``parkingposition``), e c'è solo da ferma. Il
consumo e i chilometri degli ultimi giorni vengono dalle statistiche di
viaggio del vecchio cloud MBB (``tripdata``), lette di rado.

I nomi dei campi e i loro valori vengono da audiconnect/audi_connect_ha e da
acfischer42/CarConnectivity-connector-audi (licenza MIT): vedi
RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ...vettura import StatoRicarica, StatoVettura, Vettura

ID = "audi"

#: I lavori che si chiedono a ``selectivestatus`` (quelli dell'app; uno che l'auto non ha torna con un errore).
LAVORI = (
    "access",
    "batteryChargingCare",
    "batterySupport",
    "charging",
    "chargingProfiles",
    "chargingTimers",
    "climatisation",
    "climatisationTimers",
    "departureProfiles",
    "departureTimers",
    "fuelStatus",
    "honkAndFlash",
    "lvBattery",
    "measurements",
    "oilLevel",
    "readiness",
    "userCapabilities",
    "vehicleHealthInspection",
    "vehicleHealthWarnings",
    "vehicleLights",
)

_KELVIN = 273.15
_PORTIERE = ("frontLeft", "frontRight", "rearLeft", "rearRight")
_FINESTRINI = ("frontLeft", "frontRight", "rearLeft", "rearRight", "sunRoof", "sunRoofRear", "roofCover")
_CARBURANTI = ("gasoline", "diesel", "petrol", "cng", "lpg")

#: Gli stati della ricarica di Audi (``chargingState``).
_RICARICA = {
    "charging": StatoRicarica.IN_CARICA,
    "conservation": StatoRicarica.COMPLETATA,
    "chargePurposeReachedAndConservation": StatoRicarica.COMPLETATA,
    "chargePurposeReachedAndNotConservationCharging": StatoRicarica.COMPLETATA,
    "notReadyForCharging": StatoRicarica.NON_IN_CARICA,
    "off": StatoRicarica.NON_IN_CARICA,
    "discharging": StatoRicarica.NON_IN_CARICA,
    "error": StatoRicarica.ERRORE,
}
#: I modi di ricarica che aspettano un orario: con la spina pronta, la ricarica è programmata.
_MODI_A_ORARIO = {"timer", "preferredChargingTimes", "timerChargingWithClimatisation"}
_CLIMA_SPENTO = {"off", "invalid", "unsupported", ""}


def _valore(dati: dict[str, Any], lavoro: str, blocco: str) -> dict[str, Any]:
    """Il ``value`` di ``dati[lavoro][blocco]``, o ``{}`` se manca o è un errore."""
    v = ((dati.get(lavoro) or {}).get(blocco) or {}).get("value")
    return v if isinstance(v, dict) else {}


def _numero(x: Any) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _celsius(kelvin: Any) -> float | None:
    k = _numero(kelvin)
    # Sotto lo zero assoluto, o 0 K, è un valore che l'auto non ha.
    return round(k - _KELVIN, 1) if k is not None and k > 0 else None


def quando(x: Any) -> datetime | None:
    """Un istante del cloud (``2026-08-18T21:09:17Z``, o già un ``datetime``), sempre con il fuso."""
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=UTC)
    if not isinstance(x, str) or not x:
        return None
    try:
        t = datetime.fromisoformat(x)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _istanti(x: Any) -> list[datetime]:
    """Tutti i ``carCapturedTimestamp`` della risposta, per sapere quando l'auto ha parlato l'ultima volta."""
    trovati: list[datetime] = []
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "carCapturedTimestamp" and (t := quando(v)):
                trovati.append(t)
            else:
                trovati.extend(_istanti(v))
    elif isinstance(x, list):
        for v in x:
            trovati.extend(_istanti(v))
    return trovati


def errori_in(x: Any) -> int:
    """Quanti lavori sono tornati con un errore: se lo sono tutti, il cloud ha un guasto (audi_connect_ha #793)."""
    if isinstance(x, dict):
        return (1 if "error" in x else 0) + sum(errori_in(v) for v in x.values())
    if isinstance(x, list):
        return sum(errori_in(v) for v in x)
    return 0


def _stati(voce: dict[str, Any]) -> list[str]:
    s = voce.get("status")
    if isinstance(s, str):
        return [s]
    return [str(x) for x in s] if isinstance(s, list) else []


def _apertura(voci: Any, nomi: tuple[str, ...]) -> bool | None:
    """Una di queste è aperta? ``None`` se l'auto non ne dice nessuna."""
    visti: list[bool] = []
    for voce in voci if isinstance(voci, list) else []:
        if not isinstance(voce, dict) or voce.get("name") not in nomi:
            continue
        stati = _stati(voce)
        if "open" in stati:
            visti.append(True)
        elif "closed" in stati:
            visti.append(False)
    return any(visti) if visti else None


def _motori(autonomia: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in (autonomia.get("primaryEngine"), autonomia.get("secondaryEngine")) if isinstance(m, dict)]


def capacita_di(dati: dict[str, Any]) -> list[str]:
    """Le capacità che l'auto dichiara funzionanti (quelle con uno ``status`` sono sospese)."""
    voci = ((dati.get("userCapabilities") or {}).get("capabilitiesStatus") or {}).get("value")
    return sorted(str(v["id"]) for v in (voci if isinstance(voci, list) else []) if isinstance(v, dict) and v.get("id"))


def interpreta(dati: dict[str, Any], posizione: dict[str, Any] | None = None) -> StatoVettura:
    """``selectivestatus`` (e la ``parkingposition``, se c'è) in uno ``StatoVettura``."""
    s = StatoVettura()
    grezzo: dict[str, Any] = {}

    # --- la batteria e le autonomie ----------------------------------------------------------
    batteria = _valore(dati, "charging", "batteryStatus")
    autonomia = _valore(dati, "fuelStatus", "rangeStatus")
    misure_autonomia = _valore(dati, "measurements", "rangeStatus")
    livello = _valore(dati, "measurements", "fuelLevelStatus")
    motori = _motori(autonomia)
    elettrico = next((m for m in motori if m.get("type") == "electric"), {})
    termico = next((m for m in motori if str(m.get("type") or "").lower() in _CARBURANTI), {})

    s.batteria = next(
        (
            v
            for v in (
                _numero(batteria.get("currentSOC_pct")),
                _numero(elettrico.get("currentSOC_pct")),
                _numero(livello.get("currentSOC_pct")),
            )
            if v is not None
        ),
        None,
    )
    s.autonomia_km = next(
        (
            v
            for v in (
                _numero(batteria.get("cruisingRangeElectric_km")),
                _numero(elettrico.get("remainingRange_km")),
                _numero(misure_autonomia.get("electricRange")),
            )
            if v is not None
        ),
        None,
    )
    if termico or livello.get("currentFuelLevel_pct") is not None:
        # Le TFSI e: il serbatoio, l'autonomia a benzina e quella totale.
        s.carburante_percento = _numero(livello.get("currentFuelLevel_pct"))
        if s.carburante_percento is None:
            s.carburante_percento = _numero(termico.get("currentFuelLevel_pct"))
        s.autonomia_carburante_km = _numero(termico.get("remainingRange_km"))
        if s.autonomia_carburante_km is None:
            s.autonomia_carburante_km = _numero(
                misure_autonomia.get("gasolineRange") or misure_autonomia.get("dieselRange")
            )
        s.autonomia_totale_km = _numero(autonomia.get("totalRange_km") or misure_autonomia.get("totalRange_km"))
    grezzo["tipo_auto"] = autonomia.get("carType") or livello.get("carType")

    # --- la ricarica --------------------------------------------------------------------------
    stato_carica = _valore(dati, "charging", "chargingStatus")
    impostazioni = _valore(dati, "charging", "chargingSettings")
    spina = _valore(dati, "charging", "plugStatus")
    modo = _valore(dati, "charging", "chargeMode")
    preferito = str(modo.get("preferredChargeMode") or stato_carica.get("chargeMode") or "")

    collegato = spina.get("plugConnectionState")
    if collegato in ("connected", "disconnected"):
        s.cavo_collegato = collegato == "connected"
    stato = str(stato_carica.get("chargingState") or "")
    if stato in _RICARICA:
        s.stato_ricarica = _RICARICA[stato]
    elif stato == "readyForCharging":
        # Spina pronta e niente corrente: aspetta un orario, o non sta caricando.
        a_orario = preferito in _MODI_A_ORARIO and s.cavo_collegato is not False
        s.stato_ricarica = StatoRicarica.PROGRAMMATA if a_orario else StatoRicarica.NON_IN_CARICA
    if stato == "discharging":
        s.v2l_attivo = True
    elif stato:
        s.v2l_attivo = False

    potenza = _numero(stato_carica.get("chargePower_kW"))
    if potenza is not None:
        s.potenza_ricarica_diretta_kw = potenza if s.stato_ricarica == StatoRicarica.IN_CARICA else 0.0
    tempo = _numero(stato_carica.get("remainingChargingTimeToComplete_min"))
    if tempo is not None and tempo >= 0:
        s.tempo_ricarica_min = int(tempo)
    tipo = str(stato_carica.get("chargeType") or "")
    if tipo in ("ac", "dc"):
        s.ricarica_rapida = tipo == "dc"
    elif tipo == "off":
        s.ricarica_rapida = False
    if (limite := _numero(impostazioni.get("targetSOC_pct"))) is not None:
        s.limite_ricarica = int(limite)
    grezzo.update(
        stato_ricarica_audi=stato or None,
        modo_ricarica=preferito or None,
        velocita_ricarica_kmh=_numero(stato_carica.get("chargeRate_kmph")),
        corrente_esterna=spina.get("externalPower"),
        spina_bloccata=spina.get("plugLockState"),
        led_spina=spina.get("ledColor"),
        sblocco_spina_ac=impostazioni.get("autoUnlockPlugWhenChargedAC"),
        corrente_massima_ac=impostazioni.get("maxChargeCurrentAC_A") or impostazioni.get("maxChargeCurrentAC"),
    )
    _profili(dati, s, grezzo)

    # --- il clima e le temperature ------------------------------------------------------------
    clima = _valore(dati, "climatisation", "climatisationStatus")
    clima_impostazioni = _valore(dati, "climatisation", "climatisationSettings")
    stato_clima = clima.get("climatisationState")
    if isinstance(stato_clima, str):
        s.clima_acceso = stato_clima not in _CLIMA_SPENTO
        grezzo["stato_clima"] = stato_clima
    s.temperatura_clima_c = _numero(clima_impostazioni.get("targetTemperature_C"))
    if (resta := _numero(clima.get("remainingClimatisationTime_min"))) is not None and resta >= 0:
        grezzo["minuti_clima"] = int(resta)
    lunotti = _valore(dati, "climatisation", "windowHeatingStatus").get("windowHeatingStatus")
    stati_lunotti = [
        str(v.get("windowHeatingState")) for v in (lunotti if isinstance(lunotti, list) else []) if isinstance(v, dict)
    ]
    if "on" in stati_lunotti:
        s.sbrinamento = True
    elif "off" in stati_lunotti:
        s.sbrinamento = False

    misure = dati.get("measurements") or {}
    s.temperatura_esterna_c = _celsius(
        ((misure.get("temperatureOutsideStatus") or {}).get("value") or {}).get("temperatureOutside_K")
    )
    batteria_t = (misure.get("temperatureBatteryStatus") or {}).get("value") or {}
    gradi = [
        t
        for t in (
            _celsius(batteria_t.get("temperatureHvBatteryMin_K")),
            _celsius(batteria_t.get("temperatureHvBatteryMax_K")),
        )
        if t is not None
    ]
    if gradi:
        s.temperatura_batteria_c = round(sum(gradi) / len(gradi), 1)

    # --- porte, finestrini, serratura ---------------------------------------------------------
    accesso = _valore(dati, "access", "accessStatus")
    serratura = accesso.get("doorLockStatus")
    if serratura in ("locked", "unlocked"):
        s.chiusa = serratura == "locked"
    porte = accesso.get("doors")
    s.portiere_aperte = _apertura(porte, _PORTIERE)
    s.bagagliaio_aperto = _apertura(porte, ("trunk",))
    s.finestrini_aperti = _apertura(accesso.get("windows"), _FINESTRINI)
    grezzo["cofano_aperto"] = _apertura(porte, ("bonnet",))
    grezzo["stato_complessivo"] = accesso.get("overallStatus")

    # --- contachilometri, tagliando, luci, collegamento ---------------------------------------
    s.odometro_km = _numero(_valore(dati, "measurements", "odometerStatus").get("odometer"))
    tagliando = _valore(dati, "vehicleHealthInspection", "maintenanceStatus")
    if s.odometro_km is None:
        s.odometro_km = _numero(tagliando.get("mileage_km"))
    grezzo.update(
        tagliando_giorni=_numero(tagliando.get("inspectionDue_days")),
        tagliando_km=_numero(tagliando.get("inspectionDue_km")),
        cambio_olio_giorni=_numero(tagliando.get("oilServiceDue_days")),
        cambio_olio_km=_numero(tagliando.get("oilServiceDue_km")),
    )
    luci = _valore(dati, "vehicleLights", "lightsStatus").get("lights")
    if isinstance(luci, list) and luci:
        grezzo["luci_accese"] = any(isinstance(v, dict) and v.get("status") == "on" for v in luci)
    avvisi = ((dati.get("vehicleHealthWarnings") or {}).get("warningLights") or {}).get("value")
    if isinstance(avvisi, dict) and isinstance(avvisi.get("warningLights"), list):
        grezzo["spie"] = [str(v.get("text") or v.get("messageId") or v) for v in avvisi["warningLights"]]
    collegamento = _valore(dati, "readiness", "readinessStatus").get("connectionState") or {}
    if isinstance(collegamento, dict):
        if isinstance(collegamento.get("isOnline"), bool):
            # Fuori linea vuol dire che dorme: i dati sono quelli di quando si è addormentata.
            s.in_sonno = not collegamento["isOnline"]
        if isinstance(collegamento.get("isActive"), bool):
            s.quadro_acceso = collegamento["isActive"]

    # --- la posizione -------------------------------------------------------------------------
    istanti = _istanti(dati)
    if isinstance(posizione, dict):
        lat, lon = _numero(posizione.get("lat")), _numero(posizione.get("lon"))
        if lat is not None and lon is not None:
            s.latitudine, s.longitudine = lat, lon
            s.posizione_con_segno = True
            if t := quando(posizione.get("carCapturedTimestamp")):
                istanti.append(t)
    s.letto = max(istanti) if istanti else None
    s.grezzo = {k: v for k, v in grezzo.items() if v is not None}
    return s


def _profili(dati: dict[str, Any], s: StatoVettura, grezzo: dict[str, Any]) -> None:
    """Il profilo di ricarica del luogo dove l'auto è parcheggiata: il suo limite e la sua fascia oraria.

    È quello che decide davvero dove si ferma la ricarica in quel luogo (audi_connect_ha #722): si
    tiene nel grezzo accanto al limite generale, che resta quello comandato.
    """
    v = _valore(dati, "chargingProfiles", "chargingProfilesStatus")
    profili = [p for p in v.get("profiles") or [] if isinstance(p, dict)]
    attivo = next((p for p in profili if p.get("id") == v.get("vehiclePositionedInProfileID")), None)
    if attivo is None:
        return
    grezzo["profilo_ricarica"] = attivo.get("name")
    grezzo["limite_profilo"] = _numero(attivo.get("targetSOC_pct"))
    fasce = [f for f in attivo.get("preferredChargingTimes") or [] if isinstance(f, dict)]
    accesa = next((f for f in fasce if f.get("enabled")), None)
    if fasce:
        s.ricarica_programmata = accesa is not None
    if accesa is not None:
        s.ricarica_ora_inizio = str(accesa.get("startTimeLocal") or "") or None
        s.ricarica_ora_fine = str(accesa.get("endTimeLocal") or "") or None


# --- le auto dell'account ------------------------------------------------------------------------


def vettura_da(voce: dict[str, Any]) -> Vettura | None:
    """Una voce di ``/vehicle/v1/vehicles`` in una ``Vettura``."""
    vin = str(voce.get("vin") or "").strip().upper()
    if not vin:
        return None
    capacita = [
        str(c["id"])
        for c in voce.get("capabilities") or []
        if isinstance(c, dict) and c.get("id") and not c.get("status")
    ]
    ruolo = str(voce.get("role") or "")
    return Vettura(
        vin=vin,
        marca=ID,
        modello=str(voce.get("model") or "").strip() or "Audi",
        nome=str(voce.get("nickname") or "").strip(),
        condivisa=bool(ruolo) and ruolo != "PRIMARY_USER",
        extra={"capacita": capacita, "ruolo": ruolo, "piattaforma": voce.get("devicePlatform")},
    )


# --- le statistiche di viaggio -------------------------------------------------------------------


def _viaggi(risposta: Any) -> list[dict[str, Any]]:
    lista = ((risposta or {}).get("tripDataList") or {}).get("tripData") if isinstance(risposta, dict) else None
    return [v for v in lista or [] if isinstance(v, dict)]


def _kwh_100km(viaggio: dict[str, Any]) -> float | None:
    """``averageElectricEngineConsumption`` è in decimi di kWh/100 km."""
    c = _numero(viaggio.get("averageElectricEngineConsumption"))
    return round(c / 10, 1) if c is not None and c > 0 else None


def riassumi_viaggi(lungo: Any, corto: Any, ora: datetime | None = None) -> dict[str, Any]:
    """Consumo medio, chilometri ed energia degli ultimi sette giorni, dalle statistiche di viaggio.

    ``shortTerm`` ha un record per ogni viaggio, e più record dello stesso viaggio (stessa
    ``startMileage``) man mano che cresce: di ognuno si tiene l'ultimo. ``longTerm`` è la media
    dall'ultimo azzeramento: il consumo medio viene da lì quando la settimana ha troppo pochi km.
    """
    ora = ora or datetime.now(UTC)
    campi: dict[str, Any] = {}
    per_viaggio: dict[Any, dict[str, Any]] = {}
    for v in _viaggi(corto):
        t = quando(v.get("timestamp"))
        if t is None or ora - t > timedelta(days=7) or t > ora + timedelta(hours=2):
            continue
        chiave = v.get("startMileage", v.get("tripID"))
        prima = per_viaggio.get(chiave)
        if prima is None or (_numero(v.get("overallMileage")) or 0) >= (_numero(prima.get("overallMileage")) or 0):
            per_viaggio[chiave] = v
    if _viaggi(corto):
        km = energia = 0.0
        for v in per_viaggio.values():
            tratto = _numero(v.get("mileage")) or 0.0
            km += tratto
            if (consumo := _kwh_100km(v)) is not None:
                energia += tratto * consumo / 100
        campi["km_7_giorni"] = round(km, 1)
        campi["energia_7_giorni_kwh"] = round(energia, 2)
        if km >= 20 and energia > 0:
            campi["consumo_medio_kwh_100km"] = round(energia / km * 100, 1)
    if "consumo_medio_kwh_100km" not in campi:
        recente = max(_viaggi(lungo), key=lambda v: _numero(v.get("overallMileage")) or 0, default=None)
        if recente is not None and (consumo := _kwh_100km(recente)) is not None and 5 <= consumo <= 60:
            campi["consumo_medio_kwh_100km"] = consumo
    return campi


__all__ = [
    "ID",
    "LAVORI",
    "capacita_di",
    "errori_in",
    "interpreta",
    "quando",
    "riassumi_viaggi",
    "vettura_da",
]
