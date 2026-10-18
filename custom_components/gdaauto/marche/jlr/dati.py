"""Le letture del cloud InControl, tradotte in ``StatoVettura``.

Il cloud manda lo stato come un elenco di coppie ``{"key": …, "value": …}``,
tutte in testo: ``coreStatus`` per l'auto (porte, finestrini, gomme,
contachilometri, serbatoio) ed ``evStatus`` per la parte elettrica (batteria,
autonomia, ricarica, preclimatizzazione). I nomi delle chiavi sono quelli che
leggono ardevd/jlrpy e msp1974/homeassistant-jlrincontrol (licenza MIT, vedi
RINGRAZIAMENTI.md); le regole per leggerle sono scritte da capo qui.

Le distanze arrivano sia in km sia in miglia: si prendono i km, e le miglia
solo se i km mancano. Le pressioni delle gomme arrivano in kPa, o in decimi di
kPa su alcuni modelli: sopra 1000 si dividono per dieci.
"""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

MIGLIO_KM = 1.609344

#: Lo stato della ricarica (``EV_CHARGING_STATUS``) → quello di GdAAuto.
STATI_RICARICA: dict[str, StatoRicarica] = {
    "CHARGING": StatoRicarica.IN_CARICA,
    "INITIALIZATION": StatoRicarica.IN_CARICA,
    "FULLYCHARGED": StatoRicarica.COMPLETATA,
    "BULKCHARGED": StatoRicarica.COMPLETATA,
    "WAITINGTOCHARGE": StatoRicarica.PROGRAMMATA,
    "PAUSED": StatoRicarica.IN_PAUSA,
    "FAULT": StatoRicarica.ERRORE,
    "NOTCONNECTED": StatoRicarica.NON_IN_CARICA,
    "NO MESSAGE": StatoRicarica.NON_IN_CARICA,
}
#: Gli stati della ricarica che vogliono dire «il cavo c'è».
_CON_CAVO = {"CHARGING", "INITIALIZATION", "FULLYCHARGED", "BULKCHARGED", "WAITINGTOCHARGE", "PAUSED", "FAULT"}

#: Le quattro gomme: il suffisso della chiave → il nome di GdAAuto.
GOMME = {
    "FRONT_LEFT": "anteriore_sinistro",
    "FRONT_RIGHT": "anteriore_destro",
    "REAR_LEFT": "posteriore_sinistro",
    "REAR_RIGHT": "posteriore_destro",
}
_PORTE = ("FRONT_LEFT", "FRONT_RIGHT", "REAR_LEFT", "REAR_RIGHT")
_FINESTRINI_CHIUSI = {"CLOSED", "FALSE", "UNUSED"}
#: Valori che vogliono dire «non lo so».
_IGNOTI = {"", "UNKNOWN", "UNAVAILABLE", "NULL", "NONE", "N/A"}
#: Il quadro: gli stati del veicolo (``VEHICLE_STATE_TYPE``) a quadro acceso e spento.
_QUADRO_ACCESO = {"ENGINE_ON", "KEY_INSERTED", "IGNITION_ON", "RUNNING"}
_QUADRO_SPENTO = {"KEY_REMOVED", "ENGINE_OFF", "IGNITION_OFF", "ENGINE_ON_REMOTE_START"}


def coppie(status: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str]]:
    """Le due tabelle chiave → valore (auto ed elettrica) da una risposta di ``status``.

    Il formato v4 ha ``vehicleStatus.coreStatus`` ed ``evStatus``; il vecchio
    v3 aveva un elenco solo in ``vehicleStatus``: si leggono tutti e due.
    """
    stato = (status or {}).get("vehicleStatus")
    if isinstance(stato, list):
        tutte = _tabella(stato)
        auto = {k: v for k, v in tutte.items() if not k.startswith("EV_")}
        return auto, {k: v for k, v in tutte.items() if k.startswith("EV_")}
    if not isinstance(stato, dict):
        return {}, {}
    return _tabella(stato.get("coreStatus")), _tabella(stato.get("evStatus"))


def _tabella(elenco: Any) -> dict[str, str]:
    if not isinstance(elenco, list):
        return {}
    return {
        str(d["key"]): "" if d.get("value") is None else str(d["value"]).strip()
        for d in elenco
        if isinstance(d, dict) and d.get("key")
    }


def _testo(t: dict[str, str], chiave: str) -> str | None:
    valore = t.get(chiave)
    if valore is None or valore.upper() in _IGNOTI:
        return None
    return valore.upper()


def _numero(t: dict[str, str], chiave: str) -> float | None:
    valore = t.get(chiave)
    if valore is None:
        return None
    try:
        n = float(valore.replace(",", "."))
    except ValueError:
        return None
    return n if n == n else None  # niente NaN


def _vero(t: dict[str, str], chiave: str) -> bool | None:
    valore = _testo(t, chiave)
    if valore in ("TRUE", "YES", "ON", "1"):
        return True
    if valore in ("FALSE", "NO", "OFF", "0"):
        return False
    return None


def _km(t: dict[str, str], chiave_km: str, chiave_miglia: str) -> float | None:
    km = _numero(t, chiave_km)
    if km is None and (miglia := _numero(t, chiave_miglia)) is not None:
        km = round(miglia * MIGLIO_KM, 1)
    return km if km is not None and 0 <= km < 5000 else None


def quando(testo: Any) -> datetime | None:
    """Le date del cloud: ``2020-04-12T16:02:43+0000``, con o senza millisecondi o «Z»."""
    if not isinstance(testo, str) or not testo.strip():
        return None
    t = testo.strip().replace("Z", "+00:00")
    t = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", t)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _limite(ev: dict[str, str]) -> int | None:
    """Il limite di carica: quello di questa volta, se c'è, se no quello fisso."""
    for chiave in ("EV_ONE_OFF_MAX_SOC_CHARGE_SETTING_CHOICE", "EV_PERMANENT_MAX_SOC_CHARGE_SETTING_CHOICE"):
        valore = _numero(ev, chiave)
        if valore is not None and 0 < valore <= 100:
            return int(valore)
    return None


def _pressione(valore: float | None) -> float | None:
    if valore is None or valore <= 0:
        return None
    kpa = valore / 10 if valore > 1000 else valore
    return round(kpa, 1) if 50 <= kpa <= 600 else None


def interpreta(
    status: dict[str, Any] | None,
    posizione: dict[str, Any] | None = None,
    *,
    elettrica: bool | None = None,
) -> StatoVettura:
    """Lo stato e la posizione, come li manda il cloud, in uno ``StatoVettura``.

    ``elettrica`` dice se l'auto è tutta elettrica (I-PACE, Range Rover
    Electric): allora serbatoio e autonomia a benzina non si leggono, anche se
    il cloud manda le chiavi con dentro qualcosa.
    """
    auto, ev = coppie(status)
    s = StatoVettura()

    # La batteria e l'autonomia.
    soc = _numero(ev, "EV_STATE_OF_CHARGE")
    s.batteria = soc if soc is not None and 0 <= soc <= 100 else None
    s.autonomia_km = _km(ev, "EV_RANGE_ON_BATTERY_KM", "EV_RANGE_ON_BATTERY_MILES")
    if not elettrica:
        combinata = _km(ev, "EV_PHEV_RANGE_COMBINED_KM", "EV_PHEV_RANGE_COMBINED_MILES")
        carburante = _numero(auto, "FUEL_LEVEL_PERC")
        benzina = _numero(auto, "DISTANCE_TO_EMPTY_FUEL")
        s.carburante_percento = carburante if carburante is not None and 0 <= carburante <= 100 else None
        s.autonomia_carburante_km = benzina if benzina is not None and 0 <= benzina < 5000 else None
        s.autonomia_totale_km = combinata
    metri = _numero(auto, "ODOMETER_METER")
    if metri is not None and metri >= 0:
        s.odometro_km = round(metri / 1000, 1)
    elif (miglia := _numero(auto, "ODOMETER_MILES")) is not None and miglia >= 0:
        s.odometro_km = round(miglia * MIGLIO_KM, 1)

    # La ricarica.
    carica = _testo(ev, "EV_CHARGING_STATUS")
    s.stato_ricarica = STATI_RICARICA.get(carica) if carica else None
    metodo = _testo(ev, "EV_CHARGING_METHOD")
    if metodo == "NOTCONNECTED":
        s.cavo_collegato = False
    elif metodo in ("WIRED", "WIRELESS"):
        s.cavo_collegato = True
    elif carica == "NOTCONNECTED":
        s.cavo_collegato = False
    elif carica in _CON_CAVO:
        s.cavo_collegato = True
    minuti = _numero(ev, "EV_MINUTES_TO_FULLY_CHARGED")
    if s.stato_ricarica == StatoRicarica.IN_CARICA and minuti is not None and 0 <= minuti < 6000:
        s.tempo_ricarica_min = int(minuti)
    s.limite_ricarica = _limite(ev)
    # L'energia dell'ultima ricarica arriva in decimi di kWh.
    ultima = _numero(ev, "EV_ENERGY_CONSUMED_LAST_CHARGE_KWH")
    if ultima is not None and 0 < ultima < 2000:
        s.ultima_ricarica_kwh = round(ultima / 10, 1)

    # Il clima: la preclimatizzazione elettrica, o il motore acceso da remoto (ibride).
    precondizionamento = _testo(ev, "EV_PRECONDITION_OPERATING_STATUS")
    remoto = _testo(auto, "VEHICLE_STATE_TYPE") == "ENGINE_ON_REMOTE_START"
    if precondizionamento is not None:
        s.clima_acceso = precondizionamento != "OFF" or remoto
    elif remoto:
        s.clima_acceso = True
    stato_veicolo = _testo(auto, "VEHICLE_STATE_TYPE")
    if stato_veicolo in _QUADRO_ACCESO:
        s.quadro_acceso = True
    elif stato_veicolo in _QUADRO_SPENTO:
        s.quadro_acceso = False

    # Porte, bagagliaio, finestrini.
    s.chiusa = _vero(auto, "DOOR_IS_ALL_DOORS_LOCKED")
    if s.chiusa is None:
        serrature = [_testo(auto, f"DOOR_{p}_LOCK_STATUS") for p in _PORTE]
        if all(serrature):
            s.chiusa = all(x == "LOCKED" for x in serrature)
    porte = [_testo(auto, f"DOOR_{p}_POSITION") for p in _PORTE]
    if any(porte):
        s.portiere_aperte = any(x == "OPEN" for x in porte)
    if (bagagliaio := _testo(auto, "DOOR_BOOT_POSITION")) is not None:
        s.bagagliaio_aperto = bagagliaio == "OPEN"
    finestrini = [_testo(auto, f"WINDOW_{p}_STATUS") for p in _PORTE]
    tetto = _vero(auto, "IS_SUNROOF_OPEN")
    if any(finestrini) or tetto is not None:
        s.finestrini_aperti = any(x not in _FINESTRINI_CHIUSI for x in finestrini if x) or bool(tetto)

    # Le gomme.
    for suffisso, nome in GOMME.items():
        kpa = _pressione(_numero(auto, f"TYRE_PRESSURE_{suffisso}"))
        if kpa is not None:
            s.pneumatici_kpa[nome] = kpa
    allarmi = [_testo(auto, f"TYRE_STATUS_{suffisso}") for suffisso in GOMME]
    if any(allarmi):
        s.pneumatici_allarme = any(x not in ("NORMAL", "CLEAR") for x in allarmi if x)

    # L'antifurto.
    antifurto = _testo(auto, "THEFT_ALARM_STATUS")
    if antifurto is not None and "UNKNOWN" not in antifurto:
        s.antifurto = "ARMED" in antifurto and not any(x in antifurto for x in ("DISARMED", "UNARMED"))
        if "TRIGGERED" in antifurto or "ACTIVE" in antifurto:
            s.antifurto = True

    # La posizione: l'ultima che l'auto ha mandato.
    punto = (posizione or {}).get("position") if isinstance(posizione, dict) else None
    if isinstance(punto, dict):
        lat, lon = punto.get("latitude"), punto.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float) and (lat, lon) != (0, 0):
            s.latitudine, s.longitudine = float(lat), float(lon)
            s.posizione_con_segno = True

    s.letto = quando((status or {}).get("lastUpdatedTime"))
    s.grezzo = {"auto": dict(sorted(auto.items())), "elettrica": dict(sorted(ev.items()))}
    return s


def km_dei_viaggi(viaggi: dict[str, Any] | None, dal: datetime) -> float | None:
    """I km dei viaggi cominciati dopo ``dal`` (la distanza dei viaggi è in metri)."""
    elenco = (viaggi or {}).get("trips") if isinstance(viaggi, dict) else None
    if not isinstance(elenco, list):
        return None
    metri = 0.0
    for viaggio in elenco:
        dettagli = viaggio.get("tripDetails") if isinstance(viaggio, dict) else None
        if not isinstance(dettagli, dict):
            continue
        inizio = quando(dettagli.get("startTime"))
        distanza = dettagli.get("distance")
        if inizio is None or inizio < dal or not isinstance(distanza, int | float) or distanza < 0:
            continue
        metri += distanza
    return round(metri / 1000, 1)
