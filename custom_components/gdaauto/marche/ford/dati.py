"""Dalla telemetria di FordPass allo stato di GdAAuto.

Il cloud (Autonomic, la piattaforma dati di Ford) descrive l'auto in tre
sezioni: ``metrics`` (le misure: carica, autonomia, porte, gomme…),
``states`` (lo stato dei comandi e il sonno profondo) ed ``events`` (gli
eventi, come la fine di un viaggio). Ogni misura è ``{"value": …,
"updateTime": …}``; porte, finestrini e gomme sono elenchi con la posizione.

Le unità sono metriche: km, °C, kPa, kWh; la velocità è in m/s.

I nomi dei campi sono quelli che usano marq24/ha-fordpass (Apache 2.0) e
itchannel/fordpass-ha: vedi RINGRAZIAMENTI.md. Questa lettura è scritta da
capo per GdAAuto.

Accanto alla telemetria ci sono i dati «lenti», letti di rado: ``etl``
(l'ultima ricarica), ``pct`` (gli orari e il limite di ricarica per luogo),
``rcc`` (il profilo del clima, con la temperatura voluta).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ...vettura import Marcia, StatoRicarica, StatoVettura


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _data(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        quando = datetime.fromisoformat(v)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _misura(m: dict[str, Any], nome: str) -> dict[str, Any]:
    voce = m.get(nome)
    return voce if isinstance(voce, dict) else {}


def _valore(m: dict[str, Any], nome: str) -> Any:
    return _misura(m, nome).get("value")


def _testo(m: dict[str, Any], nome: str) -> str:
    v = _valore(m, nome)
    return v.upper() if isinstance(v, str) else ""


def _elenco(m: dict[str, Any], nome: str) -> list[dict[str, Any]]:
    voce = m.get(nome)
    return [x for x in voce if isinstance(x, dict)] if isinstance(voce, list) else []


def _personalizzata(m: dict[str, Any], pezzo: str) -> Any:
    """Le ``customMetrics`` hanno chiavi lunghe (``…:custom:global-ac-target-soc``): si cerca il pezzo."""
    for chiave, voce in (m.get("customMetrics") or {}).items():
        if pezzo in str(chiave) and isinstance(voce, dict):
            return voce.get("value")
    return None


# --- la ricarica -----------------------------------------------------------------------

#: ``xevBatteryChargeDisplayStatus``: quello che il cruscotto dice della ricarica.
_RICARICA = {
    "IN_PROGRESS": StatoRicarica.IN_CARICA,
    "SCHEDULED": StatoRicarica.PROGRAMMATA,
    "PAUSED": StatoRicarica.IN_PAUSA,
    "FAULT": StatoRicarica.ERRORE,
    "STOPPED": StatoRicarica.NON_IN_CARICA,
    "NOT_READY": StatoRicarica.NON_IN_CARICA,
    "STATION_NOT_DETECTED": StatoRicarica.NON_IN_CARICA,
}
#: ``xevPlugChargerStatus``: il cavo.
_CAVO_SI = {"CONNECTED", "CHARGING", "CHARGINGAC"}
_IN_CARICA_DAL_CAVO = {"CHARGING", "CHARGINGAC"}


def _stato_ricarica(m: dict[str, Any], cavo: bool | None, batteria: float | None, limite: int | None):
    display, spina = _testo(m, "xevBatteryChargeDisplayStatus"), _testo(m, "xevPlugChargerStatus")
    stato = _RICARICA.get(display)
    if spina in _IN_CARICA_DAL_CAVO:
        stato = StatoRicarica.IN_CARICA
    if stato is None and cavo is not None:
        stato = StatoRicarica.NON_IN_CARICA
    if stato == StatoRicarica.NON_IN_CARICA and cavo and batteria is not None and limite and batteria >= limite - 1:
        # Ferma, col cavo, e piena fino al limite: ha finito.
        stato = StatoRicarica.COMPLETATA
    return stato


def _potenza_caricatore(m: dict[str, Any]) -> float | None:
    """La potenza che il caricatore di bordo dà alla batteria: tensione per corrente."""
    diretta = _numero(_personalizzata(m, "custom:charge-power-kw"))
    if diretta is not None:
        return round(diretta, 2)
    volt = _numero(_valore(m, "xevBatteryChargerVoltageOutput"))
    ampere = _numero(_valore(m, "xevBatteryChargerCurrentOutput"))
    if volt is None or ampere is None:
        return None
    return round(abs(volt * ampere) / 1000, 2)


def _limite(m: dict[str, Any], eventi: dict[str, Any], pct: list[dict[str, Any]]) -> int | None:
    """Il limite di ricarica: quello generale dell'auto, se no quello del luogo di ricarica."""
    monitor = ((eventi.get("customEvents") or {}).get("xev-hv-battery-monitoring") or {}).get("oemData") or {}
    for valore in (
        (monitor.get("target_soc") or {}).get("longValue"),
        _personalizzata(m, "custom:global-ac-target-soc"),
        _personalizzata(m, "custom:global-dc-target-soc"),
        *(((voce.get("chargeProfile") or {}).get("targetSoc")) for voce in pct),
    ):
        if (n := _numero(valore)) is not None and 0 < n <= 100:
            return int(n)
    return None


# --- la carrozzeria ------------------------------------------------------------------------

_CHIUSA = {"LOCKED", "DOUBLE_LOCKED"}
_IGNORATI = {"CLOSED", "INVALID", "UNKNOWN", "NOT_SUPPORTED", ""}
_BAGAGLIAIO = {"TAILGATE", "INNER_TAILGATE", "TRUNK", "REAR_TRUNK", "LIFTGATE", "HATCH", "REAR_HATCH"}
_NON_PORTIERE = _BAGAGLIAIO | {"FRUNK", "HOOD", "TONNEAU_COVER"}


def _chiusa(m: dict[str, Any]) -> bool | None:
    """Chiusa se lo dice «tutte le porte», o se lo dicono tutte le serrature che contano."""
    serrature = _elenco(m, "doorLockStatus")
    if not serrature:
        return None
    contano = chiuse = 0
    for s in serrature:
        valore = str(s.get("value") or "").upper()
        porta = str(s.get("vehicleDoor") or "").upper()
        if porta == "ALL_DOORS":
            return valore in _CHIUSA
        # Le serrature meccaniche e quelle che l'auto non conosce non dicono niente.
        if valore == "UNKNOWN" or (s.get("tags") or {}).get("DOOR_LATCH_TYPE") == "MECHANICAL":
            continue
        contano += 1
        chiuse += valore in _CHIUSA
    return chiuse == contano if contano else None


def _aperte(m: dict[str, Any], gruppo: set[str], fuori: bool) -> bool | None:
    """Una porta del gruppo aperta; con ``fuori``, una di quelle che nel gruppo non sono."""
    trovate = None
    for p in _elenco(m, "doorStatus"):
        porta = str(p.get("vehicleDoor") or "").upper()
        if (porta in gruppo) == fuori:
            continue
        trovate = trovate or False
        if str(p.get("value") or "").upper() not in _IGNORATI:
            return True
    return trovate


def _finestrini(m: dict[str, Any]) -> bool | None:
    finestrini = _elenco(m, "windowStatus")
    if not finestrini:
        return None
    for f in finestrini:
        valore = f.get("value")
        if isinstance(valore, dict):
            intervallo = valore.get("doubleRange") or {}
            if _numero(intervallo.get("lowerBound")) or _numero(intervallo.get("upperBound")):
                return True
        elif isinstance(valore, str) and valore.strip() and "closed" not in valore.lower():
            return True  # «Fully open position», «Btwn 10% and 60% open»…
    return False


_RUOTE = {
    "FRONT_LEFT": "anteriore_sinistro",
    "FRONT_RIGHT": "anteriore_destro",
    "REAR_LEFT": "posteriore_sinistro",
    "REAR_RIGHT": "posteriore_destro",
    # i pick-up a ruote gemellate: quella esterna
    "OUTER_REAR_LEFT": "posteriore_sinistro",
    "OUTER_REAR_RIGHT": "posteriore_destro",
}
_GOMMA_A_POSTO = {"NORMAL", "UNKNOWN", "NOT_SUPPORTED", "INVALID", ""}


def _gomme(s: StatoVettura, m: dict[str, Any]) -> None:
    for g in _elenco(m, "tirePressure"):
        ruota = _RUOTE.get(str(g.get("vehicleWheel") or "").upper())
        kpa = _numero(g.get("value"))
        if ruota and kpa is not None and 0 < kpa < 1000 and ruota not in s.pneumatici_kpa:
            s.pneumatici_kpa[ruota] = round(kpa, 1)
    allarmi = [str(g.get("value") or "").upper() for g in _elenco(m, "tirePressureStatus")]
    if allarmi:
        s.pneumatici_allarme = any(a not in _GOMMA_A_POSTO for a in allarmi)


# --- la guida ----------------------------------------------------------------------------

_MARCIA = {"P": Marcia.PARCHEGGIO, "R": Marcia.RETROMARCIA, "N": Marcia.FOLLE}
_QUADRO_ACCESO = {"ON", "RUN", "START", "RUNNING"}


def _marcia(m: dict[str, Any]) -> Marcia | None:
    leva = _testo(m, "gearLeverPosition")
    if not leva or leva in {"UNKNOWN", "INVALID"}:
        return None
    if leva in {"PARK", "P"}:
        return Marcia.PARCHEGGIO
    return _MARCIA.get(leva[:1], Marcia.AVANTI)


def _clima(m: dict[str, Any], adesso: datetime) -> bool | None:
    """Il clima da remoto: l'avvio a distanza ha un conto alla rovescia, acceso finché non arriva a zero."""
    timer = _misura(m, "remoteStartCountdownTimer")
    secondi = _numero(timer.get("value"))
    if secondi is None:
        return None
    if secondi <= 0:
        return False
    partito = _data(timer.get("updateTime"))
    return partito is None or partito + timedelta(seconds=secondi) > adesso


def _temperatura_clima(rcc: dict[str, Any]) -> float | None:
    for p in rcc.get("rccUserProfiles") or []:
        if isinstance(p, dict) and p.get("preferenceType") == "SetPointTemp_Rq":
            valore = str(p.get("preferenceValue") or "").lower()
            if valore == "lo":
                return 16.0
            if valore == "hi":
                return 30.0
            return _numero(valore.replace("_", "."))
    return None


def _sonno(stati: dict[str, Any], m: dict[str, Any]) -> bool | None:
    """Il sonno profondo: l'auto non prende comandi e non manda dati nuovi."""
    verso = str(((stati.get("commandPreclusion") or {}).get("value") or {}).get("toState") or "").upper()
    if verso == "COMMANDS_PRECLUDED":
        return True
    profondo = _valore(m, "deepSleepInProgress")
    if isinstance(profondo, bool):
        return profondo
    if verso == "COMMANDS_PERMITTED":
        return False
    return None


def _letto(dati: dict[str, Any], m: dict[str, Any]) -> datetime | None:
    if quando := _data(dati.get("updateTime")):
        return quando
    tempi = [t for voce in m.values() if isinstance(voce, dict) and (t := _data(voce.get("updateTime"))) is not None]
    return max(tempi) if tempi else None


# --- l'ultima ricarica ----------------------------------------------------------------------


def _ultima_ricarica(s: StatoVettura, etl: dict[str, Any]) -> None:
    sessioni = [x for x in etl.get("energyTransferLogs") or [] if isinstance(x, dict)]
    if not sessioni:
        return
    ultima = sessioni[0]
    if (kwh := _numero(ultima.get("energyConsumed"))) is not None and kwh >= 0:
        s.ultima_ricarica_kwh = round(kwh, 2)
    tipo = str(ultima.get("chargerType") or "").upper()
    if tipo.startswith("DC"):
        s.ultima_ricarica_tipo = "dc"
    elif tipo.startswith("AC"):
        s.ultima_ricarica_tipo = "ac"
    durata = ultima.get("energyTransferDuration") or {}
    spina = ultima.get("plugDetails") or {}
    s.ultima_ricarica_inizio = _data(durata.get("begin")) or _data(spina.get("plugInTime"))
    s.ultima_ricarica_fine = (
        _data(durata.get("end")) or _data(spina.get("plugOutTime")) or _data(ultima.get("timeStamp"))
    )


# --- tutto insieme --------------------------------------------------------------------------


def interpreta(
    dati: dict[str, Any], lenti: dict[str, Any] | None = None, adesso: datetime | None = None
) -> StatoVettura:
    """La telemetria (``metrics``, ``states``, ``events``) e i dati lenti in uno ``StatoVettura``."""
    lenti = lenti or {}
    adesso = adesso or datetime.now(UTC)
    m = dati.get("metrics") or {}
    stati = dati.get("states") or {}
    eventi = dati.get("events") or {}
    pct = [x for x in lenti.get("pct") or [] if isinstance(x, dict)]
    s = StatoVettura(grezzo={"telemetria": dati, **{k: v for k, v in lenti.items() if v}})

    # La batteria e l'autonomia
    s.batteria = _numero(_valore(m, "xevBatteryStateOfCharge"))
    s.autonomia_km = _numero(_valore(m, "xevBatteryRange"))
    s.energia_residua_kwh = _numero(_valore(m, "xevBatteryEnergyRemaining"))
    capacita = _numero(_valore(m, "xevBatteryCapacity"))
    s.capacita_batteria_kwh = capacita if capacita is not None and 10 <= capacita <= 250 else None
    s.temperatura_batteria_c = _numero(_valore(m, "xevBatteryTemperature"))
    s.tensione_v = _numero(_valore(m, "xevBatteryVoltage"))
    s.odometro_km = _numero(_valore(m, "odometer"))

    # Le ibride plug-in (Kuga, Explorer PHEV): il serbatoio
    s.carburante_percento = _numero(_valore(m, "fuelLevel"))
    s.autonomia_carburante_km = _numero(_valore(m, "fuelRange"))
    if s.autonomia_carburante_km is not None and s.autonomia_km is not None:
        s.autonomia_totale_km = round(s.autonomia_carburante_km + s.autonomia_km, 1)

    # La ricarica
    spina = _testo(m, "xevPlugChargerStatus")
    s.cavo_collegato = spina in _CAVO_SI if spina else None
    s.limite_ricarica = _limite(m, eventi, pct)
    s.stato_ricarica = _stato_ricarica(m, s.cavo_collegato, s.batteria, s.limite_ricarica)
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.potenza_ricarica_diretta_kw = _potenza_caricatore(m)
        minuti = _numero(_valore(m, "xevBatteryTimeToFullCharge"))
        s.tempo_ricarica_min = round(minuti) if minuti is not None and minuti >= 0 else None
    elif s.cavo_collegato is not None:
        s.potenza_ricarica_diretta_kw = 0.0
    tipo_presa = _testo(m, "xevChargeStationPowerType")
    if s.cavo_collegato and tipo_presa:
        s.ricarica_rapida = tipo_presa.startswith("DC")
    elif s.cavo_collegato is False:
        s.ricarica_rapida = False
    if s.stato_ricarica == StatoRicarica.PROGRAMMATA:
        s.ricarica_programmata = True

    # La guida
    velocita = _numero(_valore(m, "speed"))
    s.velocita_kmh = round(velocita * 3.6, 1) if velocita is not None else None
    s.marcia = _marcia(m)
    quadro = _testo(m, "ignitionStatus")
    s.quadro_acceso = quadro in _QUADRO_ACCESO if quadro else None
    volt_motore = _numero(_valore(m, "xevTractionMotorVoltage"))
    ampere_motore = _numero(_valore(m, "xevTractionMotorCurrent"))
    if volt_motore is not None and ampere_motore is not None:
        s.potenza_diretta_kw = round(volt_motore * ampere_motore / 1000, 2)
    freno = _testo(m, "parkingBrakeStatus")
    if freno:
        s.freno_stazionamento = freno in {"ENGAGED", "ON", "SET", "APPLIED"}

    # Le temperature e il clima
    s.temperatura_esterna_c = _numero(_valore(m, "outsideTemperature"))
    if s.temperatura_esterna_c is None:
        s.temperatura_esterna_c = _numero(_valore(m, "ambientTemp"))
    s.clima_acceso = _clima(m, adesso)
    s.temperatura_clima_c = _temperatura_clima(lenti.get("rcc") or {})

    # La carrozzeria
    s.chiusa = _chiusa(m)
    s.portiere_aperte = _aperte(m, _NON_PORTIERE, fuori=True)
    s.bagagliaio_aperto = _aperte(m, _BAGAGLIAIO, fuori=False)
    s.finestrini_aperti = _finestrini(m)
    _gomme(s, m)
    allarme = _testo(m, "alarmStatus")
    if allarme:
        s.antifurto = allarme in {"ARMED", "SET", "ON"}

    # Dov'è
    posto = _valore(m, "position")
    luogo = (posto.get("location") or {}) if isinstance(posto, dict) else {}
    s.latitudine, s.longitudine = _numero(luogo.get("lat")), _numero(luogo.get("lon"))
    s.posizione_con_segno = s.latitudine is not None

    s.in_sonno = _sonno(stati, m)
    s.letto = _letto(dati, m)
    _ultima_ricarica(s, lenti.get("etl") or {})
    return s
