"""Dalle risposte dei due cloud Nissan allo stato di GdAAuto.

**NissanConnect Services** (Kamereon, le auto dal 2019) dà ogni cosa in una
lettura a parte, come Renault: ``battery-status``, ``hvac-status``,
``location``, ``lock-status`` e ``cockpit``, ognuna con i suoi
``data.attributes``. La batteria ha due forme:

- la **v1** delle Leaf (ZE1): ``batteryLevel``, ``rangeHvacOn``/``rangeHvacOff``
  in km, ``chargeStatus`` e ``plugStatus`` interi (1 sì, 0 no, -1 errore),
  ``chargePower`` (1 lenta, 2 normale, 3 veloce, 4 rapida in corrente
  continua) e i minuti alla carica piena per ogni velocità;
- la **v3** di Ariya e Micra, fatta come quella Renault: ``batteryAutonomy``,
  ``chargingStatus`` decimale (1.0 in carica, 0.2 finita, 0.1 e 0.3 in attesa),
  ``chargingRemainingTime`` in minuti.

**NissanConnect EV** (Carwings, le Leaf 2011-2019) dà l'ultima lettura che
l'auto ha mandato (``BatteryStatusRecords``) e l'ultimo comando del clima
(``RemoteACRecords``): le autonomie sono in metri, la carica in «tacche» (12
sulle prime Leaf, 240 sulle 30 kWh) o in percentuale (``SOC``), l'energia in Wh.

I nomi dei campi e il loro significato vengono da dan-r/HomeAssistant-NissanConnect
(MIT) e da filcole/pycarwings2 (Apache-2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

# --- piccoli attrezzi --------------------------------------------------------


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _quando_iso(testo: Any) -> datetime | None:
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


def _plausibile(capacita: float | None) -> float | None:
    """Una capacità in kWh (o in Wh, che si riportano a kWh) che sia di un'auto."""
    if capacita is None or capacita <= 0:
        return None
    if capacita > 1000:
        capacita /= 1000
    return round(capacita, 1) if 10 <= capacita <= 250 else None


def consumo(kwh: float | None, km: float | None) -> float | None:
    """kWh/100 km, solo se i km bastano e il numero è quello di un'auto elettrica."""
    if kwh is None or km is None or km < 20 or kwh <= 0:
        return None
    valore = kwh / km * 100
    return round(valore, 1) if 5 <= valore <= 40 else None


# --- NissanConnect Services (Kamereon) ---------------------------------------

#: ``chargingStatus`` della batteria v3, come su Kamereon Renault
_RICARICA_V3: dict[float, StatoRicarica] = {
    0.0: StatoRicarica.NON_IN_CARICA,
    0.1: StatoRicarica.PROGRAMMATA,
    0.2: StatoRicarica.COMPLETATA,
    0.3: StatoRicarica.PROGRAMMATA,
    0.4: StatoRicarica.NON_IN_CARICA,
    1.0: StatoRicarica.IN_CARICA,
    -1.0: StatoRicarica.ERRORE,
}
#: ``chargePower`` della v1 → il campo dei minuti alla carica piena
_MINUTI_PER_VELOCITA = {1: "timeRequiredToFullSlow", 2: "timeRequiredToFullNormal", 3: "timeRequiredToFullFast"}
_RAPIDA = 4

_PORTE = ("doorStatusFrontLeft", "doorStatusFrontRight", "doorStatusRearLeft", "doorStatusRearRight")


def _cavo(valore: float | None) -> bool | None:
    if valore is None:
        return None
    if valore >= 1:
        return True
    return False if valore == 0 else None


def _batteria_v1(s: StatoVettura, b: dict[str, Any]) -> None:
    s.batteria = _numero(b.get("batteryLevel"))
    s.capacita_batteria_kwh = _plausibile(_numero(b.get("batteryCapacity")))
    stato = _numero(b.get("chargeStatus"))
    if stato is not None:
        s.stato_ricarica = (
            StatoRicarica.IN_CARICA
            if stato >= 1
            else StatoRicarica.NON_IN_CARICA
            if stato == 0
            else StatoRicarica.ERRORE
        )
    s.cavo_collegato = _cavo(_numero(b.get("plugStatus")))
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        velocita = _numero(b.get("chargePower"))
        s.ricarica_rapida = velocita == _RAPIDA if velocita is not None else None
        if velocita is not None and (campo := _MINUTI_PER_VELOCITA.get(int(velocita))):
            minuti = _numero(b.get(campo))
            s.tempo_ricarica_min = int(minuti) if minuti is not None else None
        potenza = _numero(b.get("instantaneousPower"))
        s.potenza_ricarica_diretta_kw = abs(potenza) if potenza is not None else None
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0
        s.ricarica_rapida = False


def _batteria_v3(s: StatoVettura, b: dict[str, Any]) -> None:
    s.batteria = _numero(b.get("batteryLevel"))
    s.capacita_batteria_kwh = _plausibile(_numero(b.get("batteryCapacity")))
    s.temperatura_batteria_c = _numero(b.get("batteryTemperature"))
    stato = _numero(b.get("chargingStatus"))
    if stato is None:
        stato = _numero(b.get("chargeStatus"))
    if stato is not None:
        s.stato_ricarica = _RICARICA_V3.get(round(stato, 1))
    s.cavo_collegato = _cavo(_numero(b.get("plugStatus")))
    if s.cavo_collegato is None and s.stato_ricarica in (StatoRicarica.IN_CARICA, StatoRicarica.COMPLETATA):
        s.cavo_collegato = True
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        minuti = _numero(b.get("chargingRemainingTime"))
        s.tempo_ricarica_min = int(minuti) if minuti is not None else None
        potenza = _numero(b.get("instantaneousPower") or b.get("chargingInstantaneousPower"))
        s.potenza_ricarica_diretta_kw = abs(potenza) if potenza is not None else None
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0


def interpreta_kamereon(parti: dict[str, dict[str, Any]], *, batteria_v3: bool = False) -> StatoVettura:
    """``parti`` sono gli ``attributes`` di ogni lettura, per nome della lettura."""
    b = parti.get("battery-status") or {}
    clima = parti.get("hvac-status") or {}
    posizione = parti.get("location") or {}
    porte = parti.get("lock-status") or {}
    cockpit = parti.get("cockpit") or {}
    # La posizione resta fuori dal grezzo: finisce nella diagnostica.
    s = StatoVettura(grezzo={k: v for k, v in parti.items() if k != "location"})

    if batteria_v3:
        _batteria_v3(s, b)
    else:
        _batteria_v1(s, b)

    s.temperatura_esterna_c = _numero(clima.get("externalTemperature"))
    s.temperatura_interna_c = _numero(clima.get("internalTemperature"))
    s.temperatura_clima_c = _numero(clima.get("nextTargetTemperature"))
    if isinstance(clima.get("hvacStatus"), str):
        s.clima_acceso = clima["hvacStatus"].lower() == "on"

    # L'autonomia col clima acceso, se è acceso; se no quella senza.
    acceso, spento = _numero(b.get("rangeHvacOn")), _numero(b.get("rangeHvacOff"))
    s.autonomia_km = _numero(b.get("batteryAutonomy"))
    if s.autonomia_km is None:
        s.autonomia_km = (acceso if s.clima_acceso else spento) or acceso or spento

    s.latitudine = _numero(posizione.get("gpsLatitude"))
    s.longitudine = _numero(posizione.get("gpsLongitude"))
    s.posizione_con_segno = s.latitudine is not None and s.longitudine is not None

    if isinstance(porte.get("lockStatus"), str):
        s.chiusa = porte["lockStatus"].lower() == "locked"
    s.portiere_aperte = _aperto(*(porte.get(p) for p in _PORTE))
    s.bagagliaio_aperto = _aperto(porte.get("hatchStatus"))

    s.odometro_km = _numero(cockpit.get("totalMileage"))
    if s.batteria is None:
        # Un'auto senza batteria di trazione (le ibride e-Power): il serbatoio.
        s.carburante_percento = _numero(cockpit.get("fuelLevel"))
        s.autonomia_carburante_km = _numero(cockpit.get("fuelAutonomy"))

    quando = [
        t
        for t in (
            _quando_iso(b.get("lastUpdateTime")),
            _quando_iso(clima.get("lastUpdateTime")),
            _quando_iso(posizione.get("lastUpdateTime")),
            _quando_iso(porte.get("lastUpdateTime")),
        )
        if t is not None
    ]
    s.letto = max(quando) if quando else None
    return s


def storico_kamereon(riassunti: list[Any]) -> dict[str, Any]:
    """I riassunti giornalieri dei viaggi (``trip-history``) → km ed energia della settimana.

    ``consumedElectricity`` è in kWh (dan-r ne ricava i km/kWh); un numero
    mille volte più grande è in Wh, e si riporta.
    """
    km = 0.0
    kwh = 0.0
    visti = False
    for r in riassunti:
        if not isinstance(r, dict):
            continue
        distanza = _numero(r.get("distance"))
        energia = _numero(r.get("consumedElectricity"))
        if distanza is not None:
            km += distanza
            visti = True
        if energia is not None:
            kwh += energia
    if not visti:
        return {}
    if km > 0 and kwh / km > 2:
        kwh /= 1000
    campi: dict[str, Any] = {"km_7_giorni": round(km, 1), "energia_7_giorni_kwh": round(kwh, 2)}
    if (medio := consumo(kwh, km)) is not None:
        campi["consumo_medio_kwh_100km"] = medio
    return campi


# --- NissanConnect EV (Carwings) ---------------------------------------------

_IN_CARICA = {"NORMAL_CHARGING", "RAPIDLY_CHARGING", "CHARGING", "YES"}
_CAVO_SI = {"CONNECTED", "QC_CONNECTED", "J1772_CONNECTED", "CHADEMO_CONNECTED"}
#: i minuti alla carica piena, dal caricatore più veloce al più lento
_MINUTI_CARWINGS = ("TimeRequiredToFull200_6kW", "TimeRequiredToFull200", "TimeRequiredToFull")


def _minuti_carwings(t: Any) -> int | None:
    if not isinstance(t, dict):
        return None
    ore = _numero(t.get("HourRequiredToFull") or t.get("hours")) or 0
    minuti = _numero(t.get("MinutesRequiredToFull") or t.get("minutes")) or 0
    totale = int(ore * 60 + minuti)
    return totale or None


def _quando_carwings(testo: Any) -> datetime | None:
    """``NotificationDateAndTime``: «2016/02/10 04:10», in UTC."""
    if not isinstance(testo, str) or not testo:
        return None
    for forma in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(testo.strip(), forma).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def clima_carwings(record: Any) -> bool | None:
    """Il clima è acceso se l'ultimo comando era «START» ed è riuscito (come pycarwings2)."""
    if not isinstance(record, dict):
        # «RemoteACRecords: []»: nessun comando del clima, quindi spento.
        return False if record == [] else None
    esito = str(record.get("OperationResult") or "")
    if esito == "ELECTRIC_WAVE_ABNORMAL":
        return None
    return esito.startswith("START") and record.get("RemoteACOperation") == "START"


def interpreta_carwings(batteria: dict[str, Any], clima: Any = None) -> StatoVettura:
    """``batteria`` è ``BatteryStatusRecords``, ``clima`` è ``RemoteACRecords`` (o ``None``)."""
    s = StatoVettura(grezzo={"BatteryStatusRecords": batteria, "RemoteACRecords": clima})
    bs = batteria.get("BatteryStatus") or {}

    soc = _numero((bs.get("SOC") or {}).get("Value")) if isinstance(bs.get("SOC"), dict) else None
    resto = _numero(bs.get("BatteryRemainingAmount"))
    tacche = _numero(bs.get("BatteryCapacity"))
    if soc is not None:
        s.batteria = soc
    elif resto is not None and tacche:
        # Le prime Leaf contano in dodicesimi, le 30 kWh in 240esimi.
        s.batteria = round(min(100.0, resto / (12 if tacche <= 12 else 240) * 100), 1)
    wh = _numero(bs.get("BatteryRemainingAmountWH"))
    kwh = _numero(bs.get("BatteryRemainingAmountkWH"))
    if wh:
        s.energia_residua_kwh = round(wh / 1000, 2)
    elif kwh:
        s.energia_residua_kwh = kwh

    stato = str(bs.get("BatteryChargingStatus") or "").upper()
    if stato == "NOT_CHARGING":
        s.stato_ricarica = StatoRicarica.NON_IN_CARICA
    elif stato in _IN_CARICA:
        s.stato_ricarica = StatoRicarica.IN_CARICA
    spina = str(batteria.get("PluginState") or "").upper()
    if spina == "NOT_CONNECTED":
        s.cavo_collegato = False
    elif spina in _CAVO_SI:
        s.cavo_collegato = True
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.ricarica_rapida = stato == "RAPIDLY_CHARGING" or spina == "QC_CONNECTED"
        if not s.ricarica_rapida:
            for campo in _MINUTI_CARWINGS:
                if (minuti := _minuti_carwings(batteria.get(campo))) is not None:
                    s.tempo_ricarica_min = minuti
                    break
    elif s.stato_ricarica is not None:
        s.ricarica_rapida = False
        s.potenza_ricarica_diretta_kw = 0.0

    s.clima_acceso = clima_carwings(clima) if clima is not None else None
    if isinstance(clima, dict):
        gradi = _numero(clima.get("PreAC_temp"))
        if gradi is not None:
            fahrenheit = str(clima.get("PreAC_unit") or "C").upper() == "F"
            s.temperatura_clima_c = round((gradi - 32) * 5 / 9, 1) if fahrenheit else gradi

    acceso = _numero(batteria.get("CruisingRangeAcOn"))
    spento = _numero(batteria.get("CruisingRangeAcOff"))
    metri = (acceso if s.clima_acceso else spento) or spento or acceso
    s.autonomia_km = round(metri / 1000, 1) if metri is not None else None

    s.letto = _quando_carwings(batteria.get("NotificationDateAndTime")) or _quando_carwings(batteria.get("TargetDate"))
    return s


def storico_carwings(risposta: dict[str, Any]) -> dict[str, Any]:
    """Il riepilogo del mese (``PriceSimulatorDetailInfoRequest``) → il consumo medio.

    ``TotalPowerConsumptTotal`` è in kWh e ``TotalTravelDistance`` in metri
    (come in pycarwings2); un consumo fuori misura vuol dire unità diverse, e
    non si usa.
    """
    dati = risposta.get("PriceSimulatorDetailInfoResponsePersonalData") or {}
    totale = dati.get("PriceSimulatorTotalInfo") if isinstance(dati, dict) else None
    if not isinstance(totale, dict):
        return {}
    kwh = _numero(totale.get("TotalPowerConsumptTotal"))
    metri = _numero(totale.get("TotalTravelDistance"))
    medio = consumo(kwh, metri / 1000 if metri is not None else None)
    return {"consumo_medio_kwh_100km": medio} if medio is not None else {}
