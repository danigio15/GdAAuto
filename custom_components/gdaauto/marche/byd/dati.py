"""Da quello che dice il cloud BYD a uno ``StatoVettura`` di GdAAuto.

Il cloud risponde in ``camelCase`` con parecchi valori «sentinella»: ``-129``
per una temperatura che non c'è, ``-1`` per un dato mancante, ``"--"`` per una
stringa vuota. Qui li togliamo e traduciamo tutto nelle unità di GdAAuto
(km, kW, °C, kPa). I nomi dei campi e i loro significati sono fatti del
protocollo, ricavati dai modelli della libreria MIT pyBYD (vedi
RINGRAZIAMENTI.md).

Lo stato si compone da più endpoint:

- ``realtime`` (``vehicleRealTimeResult``): batteria, autonomia, odometro,
  porte, finestrini, serrature, gomme, temperatura, ricarica…;
- ``gps`` (``getGpsInfoResult``): posizione e velocità;
- ``ricarica`` (``smartCharge/homePage``): SOC, cavo, programmazione;
- ``clima`` (``getStatusNow``): condizionatore e temperature;
- ``energia`` (``getEnergyConsumption``, letta di rado): consumo medio.
"""

from __future__ import annotations

from typing import Any

from ...vettura import StatoRicarica, StatoVettura

#: Le porte del condizionatore: BAR e PSI si portano a kPa.
_A_KPA = {"bar": 100.0, "psi": 6.894757, "kpa": 1.0}


def _num(valore: Any) -> float | None:
    """Un numero dal cloud, o ``None`` se è una sentinella (``--``, ``-1``…)."""
    if valore is None:
        return None
    if isinstance(valore, str):
        testo = valore.strip()
        if testo in ("", "--"):
            return None
        try:
            valore = float(testo)
        except ValueError:
            return None
    try:
        n = float(valore)
    except (TypeError, ValueError):
        return None
    return n


def _positivo(valore: Any) -> float | None:
    """Come ``_num`` ma scarta i negativi (``-1`` è «non lo so»)."""
    n = _num(valore)
    return n if n is not None and n >= 0 else None


def _temp(valore: Any) -> float | None:
    """Una temperatura, con la sentinella ``-129`` tolta."""
    n = _num(valore)
    return None if n is None or n == -129.0 else n


def _intero(valore: Any) -> int | None:
    n = _num(valore)
    return int(n) if n is not None else None


def _bool_da(valore: Any, acceso: int) -> bool | None:
    """``True`` se il campo vale ``acceso``, ``None`` se manca o è ``-1``."""
    n = _intero(valore)
    if n is None or n < 0:
        return None
    return n == acceso


def _primo(raw: dict[str, Any], *chiavi: str) -> Any:
    """Il primo campo presente fra più nomi equivalenti."""
    for k in chiavi:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


# --- gruppi di campi ---------------------------------------------------------

_PORTE = ("leftFrontDoor", "rightFrontDoor", "leftRearDoor", "rightRearDoor", "slidingDoor")
_FINESTRINI = ("leftFrontWindow", "rightFrontWindow", "leftRearWindow", "rightRearWindow", "skylight")
_SERRATURE = ("leftFrontDoorLock", "rightFrontDoorLock", "leftRearDoorLock", "rightRearDoorLock")
_GOMME = {
    "ant_sx": ("leftFrontTirepressure", "leftFrontTirePressure"),
    "ant_dx": ("rightFrontTirepressure", "rightFrontTirePressure"),
    "post_sx": ("leftRearTirepressure", "leftRearTirePressure"),
    "post_dx": ("rightRearTirepressure", "rightRearTirePressure"),
}
_STATO_GOMME = ("leftFrontTireStatus", "rightFrontTireStatus", "leftRearTireStatus", "rightRearTireStatus")


def _qualcuno(raw: dict[str, Any], chiavi: tuple[str, ...], aperto: int) -> bool | None:
    """``True`` se almeno un elemento del gruppo è «aperto»; ``None`` se nessuno è noto."""
    noti = [_intero(raw.get(k)) for k in chiavi]
    noti = [n for n in noti if n is not None and n >= 0]
    if not noti:
        return None
    return any(n == aperto for n in noti)


def _chiusa(raw: dict[str, Any]) -> bool | None:
    """Tutte le serrature bloccate? ``2`` = bloccata, ``1`` = aperta, ``0`` = n.d."""
    noti = [_intero(raw.get(k)) for k in _SERRATURE]
    noti = [n for n in noti if n in (1, 2)]
    if not noti:
        return None
    return all(n == 2 for n in noti)


def _gomme_kpa(raw: dict[str, Any]) -> dict[str, float]:
    """Le pressioni delle quattro gomme, portate a kPa secondo l'unità del cloud."""
    fattore = _A_KPA.get({1: "bar", 2: "psi", 3: "kpa"}.get(_intero(raw.get("tirePressUnit")) or 3, "kpa"), 1.0)
    fuori: dict[str, float] = {}
    for nome, chiavi in _GOMME.items():
        p = _positivo(_primo(raw, *chiavi))
        if p:
            fuori[nome] = round(p * fattore, 1)
    return fuori


def _stato_ricarica(raw: dict[str, Any], programmata: bool) -> StatoRicarica | None:
    """Lo stato della ricarica: ``chargeState``/``chargingState`` è l'autorità."""
    carica = _intero(_primo(raw, "chargeState", "chargingState"))
    if carica is None:
        return StatoRicarica.PROGRAMMATA if programmata else None
    if carica == 1:
        return StatoRicarica.IN_CARICA
    if programmata:
        return StatoRicarica.PROGRAMMATA
    return StatoRicarica.NON_IN_CARICA


def _ora(valore: Any) -> str | None:
    """Un orario ``HH:MM`` dal cloud, tollerando ``"8:5"`` e i minuti separati."""
    if valore in (None, "", "--"):
        return None
    testo = str(valore).strip()
    if ":" in testo:
        try:
            h, m = testo.split(":")[:2]
            return f"{int(h):02d}:{int(m):02d}"
        except ValueError:
            return None
    return None


def interpreta(
    realtime: dict[str, Any] | None = None,
    *,
    gps: dict[str, Any] | None = None,
    ricarica: dict[str, Any] | None = None,
    clima: dict[str, Any] | None = None,
    energia: dict[str, Any] | None = None,
) -> StatoVettura:
    """Mette insieme le risposte degli endpoint in un unico ``StatoVettura``."""
    r = dict(realtime or {})
    c = dict(ricarica or {})
    h = dict(clima or {})
    s = StatoVettura(grezzo={"realtime": r, "gps": gps or {}, "ricarica": c, "clima": h, "energia": energia or {}})

    # offline/dorme: il cloud manda onlineState 0 o 2 con i campi a zero
    if _intero(r.get("onlineState")) in (0, 2):
        s.in_sonno = True

    # --- batteria e autonomie ---
    s.batteria = _positivo(_primo(r, "elecPercent", "powerBattery")) or _positivo(c.get("soc"))
    s.autonomia_km = _positivo(_primo(r, "enduranceMileage", "evEndurance"))
    s.odometro_km = _positivo(_primo(r, "totalMileage"))
    s.carburante_percento = _positivo(r.get("oilPercent"))
    s.autonomia_carburante_km = _positivo(r.get("oilEndurance"))

    # --- marcia ---
    s.velocita_kmh = _positivo(r.get("speed"))
    s.quadro_acceso = _bool_da(r.get("powerGear"), 3)

    # --- ricarica ---
    programmata = bool(_intero((c.get("charge") or {}).get("status")) or _intero(r.get("bookingChargeState")))
    s.stato_ricarica = _stato_ricarica({**r, **({"chargingState": c.get("chargingState")} if c else {})}, programmata)
    connesso = _intero(_primo(r, "connectState"))
    if connesso is None:
        connesso = _intero(c.get("connectState"))
    s.cavo_collegato = (connesso != 0) if connesso is not None else None
    ore = _positivo(r.get("fullHour"))
    if ore is None:
        ore = _positivo(c.get("fullHour"))
    minuti = _positivo(r.get("fullMinute"))
    if minuti is None:
        minuti = _positivo(c.get("fullMinute"))
    if ore is not None or minuti is not None:
        s.tempo_ricarica_min = int((ore or 0) * 60 + (minuti or 0))
    carica_cfg = c.get("charge") or {}
    s.ricarica_programmata = bool(_intero(carica_cfg.get("status")) or _intero(r.get("bookingChargeState"))) or None
    s.ricarica_ora_inizio = _ora(carica_cfg.get("startTime"))
    s.ricarica_ora_fine = _ora(carica_cfg.get("endTime"))
    s.batteria_in_riscaldamento = _bool_da(r.get("batteryHeatState"), 1)

    # --- apertura e chiusura ---
    s.chiusa = _chiusa(r)
    s.portiere_aperte = _qualcuno(r, _PORTE, aperto=1)
    s.bagagliaio_aperto = _bool_da(_primo(r, "trunkLid", "backCover"), 1)
    s.finestrini_aperti = _qualcuno(r, _FINESTRINI, aperto=2)

    # --- gomme ---
    s.pneumatici_kpa = _gomme_kpa(r)
    allarme_gomme = [_intero(r.get(k)) for k in (*_STATO_GOMME, "tirepressureSystem", "rapidTireLeak")]
    allarme_gomme = [n for n in allarme_gomme if n is not None and n >= 0]
    s.pneumatici_allarme = any(n > 0 for n in allarme_gomme) if allarme_gomme else None

    # --- freno di stazionamento, antifurto ---
    s.freno_stazionamento = _bool_da(r.get("epb"), 1)
    antifurto = _intero(r.get("sentryStatus"))
    if antifurto in (2, 4):
        s.antifurto = antifurto == 2

    # --- temperature e clima ---
    s.temperatura_interna_c = _temp(_primo(h, "tempInCar") or r.get("tempInCar"))
    s.temperatura_esterna_c = _temp(h.get("tempOutCar"))
    s.temperatura_clima_c = _num(_primo(h, "mainSettingTempNew") or r.get("mainSettingTempNew"))
    stato_clima = _intero(h.get("status"))
    if stato_clima in (1, 2):
        s.clima_acceso = stato_clima == 1
    sbrina = [_intero(h.get(k)) for k in ("frontDefrostStatus", "electricDefrostStatus", "wiperHeatStatus")]
    sbrina = [n for n in sbrina if n is not None and n >= 0]
    if sbrina:
        s.sbrinamento = any(n > 0 for n in sbrina)

    # --- consumo medio (dato lento) ---
    if energia:
        cum = energia.get("cumulativeEnergyConsumption") or {}
        unita = str(cum.get("evUnit") or "").lower()
        consumo = _positivo(cum.get("avgEvConsumption"))
        if consumo is not None and "kwh" in unita and "100km" in unita.replace("·", "").replace(" ", ""):
            s.consumo_medio_kwh_100km = consumo

    # --- posizione ---
    if gps:
        s.latitudine = _num(gps.get("latitude"))
        s.longitudine = _num(gps.get("longitude"))
        s.posizione_con_segno = True
        v = _positivo(gps.get("speed"))
        if v is not None and s.velocita_kmh is None:
            s.velocita_kmh = v

    return s
