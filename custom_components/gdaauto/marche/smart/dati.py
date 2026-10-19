"""Lo stato delle auto sulla piattaforma Geely (smart, e lo stesso formato per Zeekr).

Il cloud Geely manda un ``vehicleStatus`` con due parti:
``basicVehicleStatus`` (posizione, velocità, motore) e
``additionalVehicleStatus`` (batteria e ricarica in ``electricVehicleStatus``,
gomme e contachilometri in ``maintenanceStatus``, porte e serrature in
``drivingSafetyStatus``, clima e finestrini in ``climateStatus``). Quasi tutti
i valori arrivano come testo: ``"47"``, ``"6.400"``, ``"false"``.

Il significato dei campi e dei codici viene da pySmartHashtag e
SmartHashtag (DasBasti, licenza MIT) per smart, e da zeekr_homeassistant
(Fryyyyy, licenza MIT) per Zeekr: vedi RINGRAZIAMENTI.md.

Qui c'è l'interpretazione comune; quello che cambia da un marchio all'altro
(come si legge la serratura, quali codici vogliono dire «in carica») sta in
``Regole``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import math
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

#: La posizione del cloud Geely in decimillesimi di secondo d'arco (gradi × 3 600 000).
SCALA_POSIZIONE = 3_600_000
#: Il tempo alla fine della ricarica quando non si sta caricando.
_TEMPO_NON_VALIDO = 2047

#: I codici di ``chargerState`` per smart (pySmartHashtag, ``ChargingState``).
_RICARICA_SMART: dict[int, StatoRicarica] = {
    0: StatoRicarica.NON_IN_CARICA,  # NOT_CHARGING
    1: StatoRicarica.NON_IN_CARICA,  # DEFAULT
    2: StatoRicarica.IN_CARICA,  # CHARGING
    3: StatoRicarica.ERRORE,  # ERROR
    4: StatoRicarica.COMPLETATA,  # COMPLETE
    5: StatoRicarica.COMPLETATA,  # FULLY_CHARGED
    6: StatoRicarica.COMPLETATA,  # FINISHED_FULLY_CHARGED
    7: StatoRicarica.NON_IN_CARICA,  # FINISHED_NOT_FULL
    9: StatoRicarica.NON_IN_CARICA,  # PLUGGED_IN
    10: StatoRicarica.PROGRAMMATA,  # WAITING_FOR_CHARGING
    11: StatoRicarica.COMPLETATA,  # TARGET_REACHED
    15: StatoRicarica.IN_CARICA,  # DC_CHARGING
}
#: Il codice della ricarica in corrente continua.
RICARICA_DC = 15
#: I codici che vogliono dire «il cavo c'è» anche quando la connessione non è detta.
_COLLEGATA_SMART = frozenset({2, 4, 9, 15})


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


def _chiusa_smart(stato_serratura: int) -> bool:
    # SmartHashtag: 0 vuol dire aperta, il resto chiusa.
    return stato_serratura != 0


@dataclass(frozen=True)
class Regole:
    """Quello che cambia fra i marchi Geely nello stesso formato."""

    ricarica: dict[int, StatoRicarica] = field(default_factory=lambda: dict(_RICARICA_SMART))
    collegata: frozenset[int] = _COLLEGATA_SMART
    chiusa: Callable[[int], bool] = _chiusa_smart
    #: ``usageMode`` «0» vuol dire auto in sonno profondo (lo dice Zeekr)
    sonno_da_uso: bool = False


SMART = Regole()


def gruppo(radice: dict[str, Any], *chiavi: str) -> dict[str, Any]:
    for chiave in chiavi:
        radice = radice.get(chiave) if isinstance(radice, dict) else None  # type: ignore[assignment]
    return radice if isinstance(radice, dict) else {}


def _coordinata(valore: Any, limite: float) -> float | None:
    n = numero(valore)
    if n is None:
        return None
    if abs(n) > limite:
        # smart manda decimillesimi di secondo d'arco; Zeekr i gradi.
        n = n / SCALA_POSIZIONE
    return round(n, 6) if abs(n) <= limite else None


#: I quattro lati come li chiama il cloud: guidatore e passeggero, davanti e dietro.
_LATI = ("Driver", "Passenger", "DriverRear", "PassengerRear")


def _diverso_da_zero(valore: Any) -> bool | None:
    n = intero(valore)
    return None if n is None else n != 0


def _uguale_a_uno(valore: Any) -> bool | None:
    n = intero(valore)
    return None if n is None else n == 1


def _qualcuno(valori: Iterable[bool | None]) -> bool | None:
    letti = [v for v in valori if v is not None]
    if not letti:
        return None
    return any(letti)


def interpreta(stato_grezzo: dict[str, Any], regole: Regole = SMART) -> StatoVettura:
    """Da un ``vehicleStatus`` del cloud Geely a uno ``StatoVettura``."""
    s = StatoVettura(grezzo=stato_grezzo)
    base = gruppo(stato_grezzo, "basicVehicleStatus")
    altro = gruppo(stato_grezzo, "additionalVehicleStatus")
    ev = gruppo(altro, "electricVehicleStatus")
    manutenzione = gruppo(altro, "maintenanceStatus")
    sicurezza = gruppo(altro, "drivingSafetyStatus")
    clima = gruppo(altro, "climateStatus")

    s.letto = da_ms(stato_grezzo.get("updateTime")) or da_ms(clima.get("updateTime"))

    # --- batteria e ricarica ---------------------------------------------------
    s.batteria = numero(ev.get("chargeLevel"))
    s.autonomia_km = numero(ev.get("distanceToEmptyOnBatteryOnly"))
    tempo = intero(ev.get("timeToFullyCharged"))
    if tempo is not None and 0 <= tempo < _TEMPO_NON_VALIDO:
        s.tempo_ricarica_min = tempo
    codice = intero(ev.get("chargerState"))
    if codice is not None:
        s.stato_ricarica = regole.ricarica.get(codice)
    connessione = intero(ev.get("statusOfChargerConnection"))
    if connessione is not None:
        # 1 DC collegata, 2 collegata senza caricare, 3 in carica (SmartHashtag).
        s.cavo_collegato = connessione in (1, 2, 3)
    if codice is not None and codice in regole.collegata:
        s.cavo_collegato = True
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.ricarica_rapida = codice == RICARICA_DC
        tensione, corrente = numero(ev.get("chargeUAct")), numero(ev.get("chargeIAct"))
        if codice != RICARICA_DC and tensione and corrente:
            # Sopra 260 V è una trifase: la tensione è quella di fase.
            watt = tensione * corrente if tensione < 260 else tensione * corrente * math.sqrt(3)
            s.potenza_ricarica_diretta_kw = round(abs(watt) / 1000, 2)
    elif s.stato_ricarica is not None:
        s.potenza_ricarica_diretta_kw = 0.0

    # --- contachilometri e gomme ------------------------------------------------
    s.odometro_km = numero(manutenzione.get("odometer"))
    # Il guidatore è a sinistra: le auto europee.
    for lato, ruota in zip(
        _LATI, ("anteriore_sinistro", "anteriore_destro", "posteriore_sinistro", "posteriore_destro"), strict=True
    ):
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
        s.chiusa = regole.chiusa(serratura)
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
    posizioni = [
        intero(clima.get(k)) for k in ("winPosDriver", "winPosPassenger", "winPosDriverRear", "winPosPassengerRear")
    ]
    lette = [p for p in posizioni if p is not None and 0 <= p <= 100]
    if lette:
        s.finestrini_aperti = any(p > 0 for p in lette)

    # --- marcia, posizione, sonno ---------------------------------------------
    motore = str(base.get("engineStatus") or "").lower().replace("-", "_")
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
    if regole.sonno_da_uso and (uso := base.get("usageMode")) is not None:
        s.in_sonno = str(uso).strip() == "0"
    return s


def limite_da_soc(valore: Any) -> int | None:
    """Il limite di ricarica: il cloud lo dice in decimi di percento (800 = 80 %)."""
    n = numero(valore)
    if n is None or not 0 < n <= 1000:
        return None
    return round(n / 10)


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


def potenza_dc_smart5(info: dict[str, Any]) -> tuple[float, float] | None:
    """Corrente (A) e tensione (V) della ricarica DC della #5, dai valori grezzi del cloud.

    pySmartHashtag (da SMore#): corrente = (chargeI − 16380) / 10, tensione = chargeU / 4.
    """
    corrente, tensione = numero(info.get("chargeI")), numero(info.get("chargeU"))
    if corrente is None or tensione is None:
        return None
    return abs((corrente - 16380) / 10), tensione / 4
