"""Lo stato delle Zeekr: il ``vehicleStatus`` della piattaforma Geely, letto con le regole di Zeekr.

Il cloud Zeekr manda lo stesso albero delle altre auto Geely:
``basicVehicleStatus`` (posizione, velocità, motore, uso) e
``additionalVehicleStatus`` (batteria e ricarica in ``electricVehicleStatus``,
gomme e contachilometri in ``maintenanceStatus``, porte e serrature in
``drivingSafetyStatus``, clima e finestrini in ``climateStatus``). Quasi tutti i
valori arrivano come testo: ``"47"``, ``"6.400"``, ``"false"``.

Rispetto a smart cambiano alcuni codici: la serratura chiusa è ``1``, la
ricarica in corso ha più codici (2, 15, 24, 28, 30), la posizione arriva già in
gradi, e ``usageMode`` «0» vuol dire auto in sonno profondo. Il flag
``isCharging`` non è affidabile: conta ``chargerState``, e la potenza vera
(corrente × tensione, trifase sopra i 300 V; in DC quella della colonnina).

Il significato dei campi e dei codici viene da zeekr_homeassistant e
zeekr_ev_api (Fryyyyy, licenza MIT) e da OpenZeekr (borconi, licenza MIT):
vedi RINGRAZIAMENTI.md. Le funzioni di base sono le stesse della marca smart,
duplicate qui perché ogni marca resti a sé.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
import math
import re
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

#: Il tempo alla fine della ricarica quando non si sta caricando.
_TEMPO_NON_VALIDO = 2047
#: Sotto questa potenza (W) una «ricarica» è solo il cavo collegato.
_POTENZA_MINIMA_W = 100.0
#: Da questa tensione AC la ricarica è trifase (la tensione è quella concatenata, ~400 V).
_TRIFASE_DA_V = 300.0

#: I codici di ``chargerState`` per Zeekr (OpenZeekr e zeekr_homeassistant).
RICARICA: dict[int, StatoRicarica] = {
    0: StatoRicarica.NON_IN_CARICA,
    2: StatoRicarica.IN_CARICA,
    4: StatoRicarica.COMPLETATA,
    6: StatoRicarica.PROGRAMMATA,
    15: StatoRicarica.IN_CARICA,  # in corrente continua
    24: StatoRicarica.IN_CARICA,
    25: StatoRicarica.NON_IN_CARICA,  # fermata
    26: StatoRicarica.COMPLETATA,
    28: StatoRicarica.IN_CARICA,
    30: StatoRicarica.IN_CARICA,
}
#: Il codice della ricarica in corrente continua.
RICARICA_DC = 15
#: ``statusOfChargerConnection`` e ``dcDcConnectStatus``: 1, 2, 3 vogliono dire «collegata».
_COLLEGATA = frozenset({1, 2, 3})
#: I quattro lati come li chiama il cloud: guidatore e passeggero, davanti e dietro.
_LATI = ("Driver", "Passenger", "DriverRear", "PassengerRear")
_RUOTE = ("anteriore_sinistro", "anteriore_destro", "posteriore_sinistro", "posteriore_destro")

#: Dal codice del modello (``appModelCode``, ``modelName``…) al nome che si vede.
_MODELLI = (
    ("7GT", "7GT"),
    ("CC1E", "7GT"),
    ("CX1E", "7X"),
    ("7X", "7X"),
    ("9X", "9X"),
    ("009", "009"),
    ("001", "001"),
)


# --- le funzioni di base (le stesse di smart) --------------------------------------------


def numero(valore: Any) -> float | None:
    """Un numero dal testo del cloud, o None."""
    if valore is None or isinstance(valore, bool):
        return None
    try:
        n = float(valore)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def intero(valore: Any) -> int | None:
    n = numero(valore)
    return int(n) if n is not None else None


def vero(valore: Any) -> bool | None:
    """``true``, ``"true"``, ``"1"`` sono veri; ``false``, ``"0"`` falsi."""
    if isinstance(valore, bool):
        return valore
    if valore is None:
        return None
    testo = str(valore).strip().lower()
    if testo in ("true", "1"):
        return True
    if testo in ("false", "0"):
        return False
    return None


def da_ms(valore: Any) -> datetime | None:
    n = numero(valore)
    if n is None or n <= 0:
        return None
    try:
        return datetime.fromtimestamp(n / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def gruppo(radice: Any, *chiavi: str) -> dict[str, Any]:
    for chiave in chiavi:
        radice = radice.get(chiave) if isinstance(radice, dict) else None
    return radice if isinstance(radice, dict) else {}


def _qualcuno(valori: Iterable[bool | None]) -> bool | None:
    letti = [v for v in valori if v is not None]
    if not letti:
        return None
    return any(letti)


def _diverso_da_zero(valore: Any) -> bool | None:
    n = intero(valore)
    return None if n is None else n != 0


def _uguale_a_uno(valore: Any) -> bool | None:
    n = intero(valore)
    return None if n is None else n == 1


def _coordinata(valore: Any, limite: float) -> float | None:
    n = numero(valore)
    if n is None:
        return None
    if abs(n) > limite:
        # Le Geely più vecchie mandano decimillesimi di secondo d'arco (gradi × 3 600 000).
        n = n / 3_600_000
    return round(n, 6) if abs(n) <= limite else None


# --- il modello ----------------------------------------------------------------------------


def modello_di(voce: dict[str, Any]) -> str:
    """Il nome del modello dall'elenco delle auto: «001», «7X», «X», «7GT»…"""
    nomi = [
        str(voce.get(k) or "")
        for k in ("modelName", "seriesName", "appModelCode", "appInnerCode", "innerCode", "seriesCode")
    ]
    for nome in nomi:
        pulito = nome.upper().replace(" ", "").replace("ZEEKR", "")
        for segno, modello in _MODELLI:
            if segno in pulito:
                return modello
        if pulito == "X":
            return "X"
    leggibile = next((n for n in nomi if n and re.fullmatch(r"[\w .-]{1,24}", n)), "")
    return leggibile or "Zeekr"


# --- la potenza di ricarica ----------------------------------------------------------------


def _potenza_w(ev: dict[str, Any]) -> tuple[float | None, bool]:
    """La potenza di ricarica in W e se è in DC, da corrente e tensione del cloud."""
    corrente_dc, tensione_dc = numero(ev.get("dcChargePileIAct")), numero(ev.get("dcChargePileUAct"))
    if corrente_dc and tensione_dc and corrente_dc > 0 and tensione_dc > 0:
        return corrente_dc * tensione_dc, True
    corrente, tensione = numero(ev.get("chargeIAct")), numero(ev.get("chargeUAct"))
    if not corrente or not tensione or corrente <= 0 or tensione <= 0:
        return None, False
    watt = corrente * tensione
    return (watt * math.sqrt(3) if tensione >= _TRIFASE_DA_V else watt), False


# --- lo stato ------------------------------------------------------------------------------


def interpreta(stato_grezzo: dict[str, Any]) -> StatoVettura:
    """Da un ``vehicleStatus`` del cloud Zeekr a uno ``StatoVettura``."""
    if not isinstance(stato_grezzo, dict):
        stato_grezzo = {}
    s = StatoVettura(grezzo=stato_grezzo)
    base = gruppo(stato_grezzo, "basicVehicleStatus")
    altro = gruppo(stato_grezzo, "additionalVehicleStatus")
    ev = gruppo(altro, "electricVehicleStatus")
    manutenzione = gruppo(altro, "maintenanceStatus")
    sicurezza = gruppo(altro, "drivingSafetyStatus")
    clima = gruppo(altro, "climateStatus")

    s.letto = da_ms(stato_grezzo.get("updateTime")) or da_ms(clima.get("updateTime"))

    # --- batteria e ricarica ---------------------------------------------------
    # stateOfCharge è vuoto su molte auto: la carica vera è chargeLevel.
    s.batteria = numero(ev.get("chargeLevel"))
    if s.batteria is None:
        s.batteria = numero(ev.get("stateOfCharge"))
    s.autonomia_km = numero(ev.get("distanceToEmptyOnBatteryOnly"))
    if s.autonomia_km is None:
        s.autonomia_km = numero(base.get("distanceToEmpty"))
    consumo = numero(ev.get("averPowerConsumption"))
    if not consumo or consumo <= 0:
        consumo = numero(ev.get("averTraPowerConsumption"))
    if consumo and 0 < consumo < 100:
        s.consumo_medio_kwh_100km = round(consumo, 1)
    s.batteria_in_riscaldamento = vero(ev.get("hvBatteryPreHeatingActive"))

    watt, dc = _potenza_w(ev)
    codice = intero(ev.get("chargerState"))
    if codice is not None:
        s.stato_ricarica = RICARICA.get(codice)
    if s.stato_ricarica in (None, StatoRicarica.NON_IN_CARICA) and watt and watt > _POTENZA_MINIMA_W:
        s.stato_ricarica = StatoRicarica.IN_CARICA
    collegamenti = [intero(ev.get(k)) for k in ("statusOfChargerConnection", "dcDcConnectStatus")]
    letti = [c for c in collegamenti if c is not None]
    if letti:
        s.cavo_collegato = any(c in _COLLEGATA for c in letti)
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.cavo_collegato = True
        s.ricarica_rapida = dc or codice == RICARICA_DC
        if watt:
            s.potenza_ricarica_diretta_kw = round(watt / 1000, 2)
        tempo = intero(ev.get("timeToFullyCharged"))
        if tempo is not None and 0 < tempo < _TEMPO_NON_VALIDO:
            s.tempo_ricarica_min = tempo
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0

    # --- contachilometri e gomme ------------------------------------------------
    s.odometro_km = numero(manutenzione.get("odometer"))
    # Il guidatore è a sinistra: le auto europee.
    for lato, ruota in zip(_LATI, _RUOTE, strict=True):
        kpa = numero(manutenzione.get(f"tyreStatus{lato}"))
        if kpa is not None and kpa > 0:
            s.pneumatici_kpa[ruota] = round(kpa, 1)
    s.pneumatici_allarme = _qualcuno(
        _diverso_da_zero(manutenzione.get(f"{tipo}{lato}"))
        for tipo in ("tyrePreWarning", "tyreTempWarning")
        for lato in _LATI
    )

    # --- porte, serrature, freno ---------------------------------------------
    serratura = intero(sicurezza.get("centralLockingStatus"))
    if serratura is not None:
        # zeekr_homeassistant: «1» chiusa, il resto aperta.
        s.chiusa = serratura == 1
    s.portiere_aperte = _qualcuno(_uguale_a_uno(sicurezza.get(f"doorOpenStatus{lato}")) for lato in _LATI)
    bagagliaio = intero(sicurezza.get("trunkOpenStatus"))
    if bagagliaio is not None:
        s.bagagliaio_aperto = bagagliaio == 1
    freno = intero(sicurezza.get("electricParkBrakeStatus"))
    if freno is not None:
        s.freno_stazionamento = freno == 1

    # --- clima e finestrini ---------------------------------------------------
    s.temperatura_interna_c = numero(clima.get("interiorTemp"))
    s.temperatura_esterna_c = numero(clima.get("exteriorTemp"))
    s.clima_acceso = vero(clima.get("preClimateActive"))
    s.sbrinamento = vero(clima.get("defrost"))
    impostata = numero(clima.get("crSetTemp"))
    if impostata is not None and 10 <= impostata <= 35:
        s.temperatura_clima_c = impostata
    # La posizione in percento (0 chiuso, 100 tutto aperto) dice più dello stato a codici.
    posizioni = [intero(clima.get(f"winPos{lato}")) for lato in _LATI]
    lette = [p for p in posizioni if p is not None and 0 <= p <= 100]
    if lette:
        s.finestrini_aperti = any(p > 0 for p in lette)
    else:
        codici = [str(clima.get(f"winStatus{lato}") or "").strip() for lato in _LATI]
        codici = [c for c in codici if c]
        if codici:
            s.finestrini_aperti = any(c not in ("0", "2") for c in codici)

    # --- marcia, posizione, sonno ---------------------------------------------
    motore = str(base.get("engineStatus") or "").lower().replace("_", "-")
    if motore:
        s.quadro_acceso = "running" in motore or motore == "ready"
    if vero(base.get("speedValidity")) is not False:
        s.velocita_kmh = numero(base.get("speed"))
    elif s.quadro_acceso is False:
        s.velocita_kmh = 0.0
    posizione = gruppo(base, "position")
    lat, lon = _coordinata(posizione.get("latitude"), 90), _coordinata(posizione.get("longitude"), 180)
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True
    if (uso := base.get("usageMode")) is not None and str(uso).strip():
        s.in_sonno = str(uso).strip() == "0"
    return s


# --- le letture a parte ---------------------------------------------------------------------


def limite_da_soc(valore: Any) -> int | None:
    """Il limite di ricarica: il cloud lo dice in decimi di percento (800 = 80 %)."""
    n = numero(valore)
    if n is None or not 0 < n <= 1000:
        return None
    return round(n / 10)


def aggiungi_ricarica(s: StatoVettura, info: dict[str, Any]) -> None:
    """Lo stato di ricarica a parte (``qrvs``): la potenza che dice l'auto, in kW.

    Si usa solo se l'auto sta caricando: da ferma il cloud lascia qui l'ultima
    potenza vista.
    """
    if not isinstance(info, dict):
        return
    codice = intero(info.get("chargerState"))
    if codice is not None and RICARICA.get(codice) == StatoRicarica.IN_CARICA:
        s.stato_ricarica = StatoRicarica.IN_CARICA
        s.cavo_collegato = True
        if codice == RICARICA_DC:
            s.ricarica_rapida = True
    if s.stato_ricarica != StatoRicarica.IN_CARICA:
        return
    kw = numero(info.get("chargePower"))
    if kw is not None and 0 <= kw < 1000:
        s.potenza_ricarica_diretta_kw = round(kw, 2)


def sentinella_da(stato_comandi: dict[str, Any]) -> bool | None:
    """La modalità sentinella dallo stato dei comandi (``vstdModeState``)."""
    if not isinstance(stato_comandi, dict):
        return None
    valore = stato_comandi.get("vstdModeState")
    if valore is None or str(valore).strip() == "":
        return None
    return str(valore).strip().lower() in ("1", "true")


_ORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)")


def _ora(valore: Any) -> str | None:
    m = _ORA.match(str(valore or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def piano_di_ricarica(piano: dict[str, Any]) -> dict[str, Any]:
    """La ricarica programmata (``getChargingPlan``): accesa, e dalle/alle «HH:MM»."""
    if not isinstance(piano, dict) or not piano:
        return {}
    campi: dict[str, Any] = {}
    comando = str(piano.get("command") or "").strip().lower()
    if comando in ("start", "stop"):
        campi["ricarica_programmata"] = comando == "start"
    if inizio := _ora(piano.get("startTime")):
        campi["ricarica_ora_inizio"] = inizio
    if fine := _ora(piano.get("endTime")):
        campi["ricarica_ora_fine"] = fine
    return campi


def viaggi_da(risposta: Any) -> list[dict[str, Any]]:
    """I viaggi del diario: la lista sta in ``data`` (o ``records``, ``list``) della pagina."""
    if isinstance(risposta, list):
        return [v for v in risposta if isinstance(v, dict)]
    if not isinstance(risposta, dict):
        return []
    for chiave in ("data", "records", "list", "rows"):
        if isinstance(risposta.get(chiave), list):
            return [v for v in risposta[chiave] if isinstance(v, dict)]
    return []


def ultimi_7_giorni(viaggi: Iterable[dict[str, Any]], ora: datetime | None = None) -> dict[str, float]:
    """I km e l'energia dei viaggi degli ultimi sette giorni, oggi compreso.

    Ogni viaggio del diario ha ``traveledDistance`` (km), ``electricConsumption``
    (kWh/100 km di quel viaggio) e ``startTime`` (ms).
    """
    ora = ora or datetime.now(UTC)
    inizio = (ora - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    km = kwh = 0.0
    contati = 0
    for viaggio in viaggi:
        if not isinstance(viaggio, dict):
            continue
        partenza = da_ms(viaggio.get("startTime"))
        distanza = numero(viaggio.get("traveledDistance"))
        if partenza is None or distanza is None or partenza < inizio:
            continue
        contati += 1
        km += distanza
        consumo = numero(viaggio.get("electricConsumption"))
        if consumo is not None:
            kwh += distanza * consumo / 100
    if not contati:
        return {"km_7_giorni": 0.0, "energia_7_giorni_kwh": 0.0}
    return {"km_7_giorni": round(km, 1), "energia_7_giorni_kwh": round(kwh, 2)}
