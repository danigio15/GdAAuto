"""Dalle risposte di MySubaru (STARLINK) allo stato di GdAAuto.

Il cloud dà quattro letture, tutte con l'ultimo dato che l'auto ha mandato:

- ``vehicleStatus.json``: chilometri (anche già in km), autonomia, carica
  della batteria ibrida, serbatoio, gomme (in kPa e in psi), posizione,
  finestrini e ``vehicleStateType`` (quadro spento o acceso);
- ``condition/execute.json`` (solo con l'abbonamento Security/Companion+ e
  la telematica g2 o più nuova): serrature, porte, finestrini e, per le
  ibride plug-in, la ricarica (``evChargerStateType``, ``evIsPluggedIn``,
  ``evTimeToFullyCharged`` in minuti, ``evDistanceToEmpty`` in miglia);
- ``locate/execute.json``: la posizione più fresca;
- ``vehicleHealth.json``: le spie, fra cui quella delle gomme (``TPMS_MIL``).

Alcuni valori vogliono dire «non lo so»: 16383 (autonomia, consumi), 65535
(tempo di ricarica), 32767 (gomme), latitudine 90 e longitudine 180, e i
testi «UNKNOWN» e «NOT_EQUIPPED». I nomi dei campi, le unità e questi valori
vengono da G-Two/subarulink (Apache 2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura, Vettura

ID = "subaru"

_MIGLIO_KM = 1.609344
_PSI_KPA = 6.894757

#: i valori che vogliono dire «il sensore non lo sa»
_NON_VALIDI = {16383, 65535, 32767}
_LAT_NON_VALIDA = 90
_LON_NON_VALIDA = 180
_NIENTE = {"", "UNKNOWN", "NOT_EQUIPPED", "NULL", "NONE"}

_GOMME = {
    "anteriore_sinistro": "FrontLeft",
    "anteriore_destro": "FrontRight",
    "posteriore_sinistro": "RearLeft",
    "posteriore_destro": "RearRight",
}
_SERRATURE = (
    "doorFrontLeftLockStatus",
    "doorFrontRightLockStatus",
    "doorRearLeftLockStatus",
    "doorRearRightLockStatus",
    "doorBootLockStatus",
)
_PORTE = ("doorFrontLeftPosition", "doorFrontRightPosition", "doorRearLeftPosition", "doorRearRightPosition")
_FINESTRINI = (
    "windowFrontLeftStatus",
    "windowFrontRightStatus",
    "windowRearLeftStatus",
    "windowRearRightStatus",
    "windowSunroofStatus",
)
#: ``vehicleStateType``: il quadro acceso
_QUADRO_ACCESO = {"IGN-ON", "IGN-ACC", "ENGINE_ON_REMOTE_START", "MOVING"}
_QUADRO_SPENTO = {"IGNITION_OFF", "IN_PARK"}

#: la telematica: g3 e g4 usano gli stessi indirizzi della g2
GENERAZIONI = ("g4", "g3", "g2", "g1")
#: gli abbonamenti che aprono i comandi a distanza
_REMOTI = {"REMOTE", "COMPANION_PLUS"}


def _numero(valore: Any) -> float | None:
    if valore is None or isinstance(valore, bool):
        return None
    try:
        numero = float(valore)
    except (TypeError, ValueError):
        return None
    return None if numero in _NON_VALIDI else numero


def _testo(valore: Any) -> str | None:
    if valore is None:
        return None
    testo = str(valore).strip().upper()
    return None if testo in _NIENTE else testo


def _quando(valore: Any) -> datetime | None:
    """Le ore di Subaru: «2022-09-27T11:22+0000», «…:15.000+0000», o millisecondi."""
    if isinstance(valore, (int, float)) and not isinstance(valore, bool) and valore > 0:
        return datetime.fromtimestamp(valore / 1000 if valore > 1e11 else valore, UTC)
    if not isinstance(valore, str) or not valore:
        return None
    for formato in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z"):
        try:
            return datetime.strptime(valore, formato)
        except ValueError:
            continue
    try:
        quando = datetime.fromisoformat(valore)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _km(valore: Any, unita: Any) -> float | None:
    numero = _numero(valore)
    if numero is None:
        return None
    return round(numero * _MIGLIO_KM, 1) if str(unita or "MILES").upper().startswith("MILE") else numero


def _tutti(valori: list[str | None], si: str, no: str) -> bool | None:
    noti = [v for v in valori if v in (si, no)]
    return all(v == si for v in noti) if noti else None


def _posizione(s: StatoVettura, lat: Any, lon: Any) -> None:
    lat_n, lon_n = _numero(lat), _numero(lon)
    if lat_n is None or lon_n is None or (lat_n == _LAT_NON_VALIDA and lon_n == _LON_NON_VALIDA):
        # Dopo lo spegnimento alcune auto mandano 90/180: non è un posto.
        return
    s.latitudine, s.longitudine, s.posizione_con_segno = lat_n, lon_n, True


def interpreta(
    stato: dict[str, Any] | None,
    condizioni: dict[str, Any] | None = None,
    posizione: dict[str, Any] | None = None,
    salute: dict[str, Any] | None = None,
    *,
    elettrica: bool = True,
) -> StatoVettura:
    """Le quattro letture (``data`` di ``vehicleStatus``, ``data.result`` delle altre) → ``StatoVettura``."""
    st = dict(stato or {})
    co = dict(condizioni or {})
    s = StatoVettura(grezzo={"stato": st, "condizioni": co, "posizione": dict(posizione or {})})

    if (km := _numero(st.get("odometerValueKilometers"))) is not None and km > 0:
        s.odometro_km = km
    elif (miglia := _numero(st.get("odometerValue") or co.get("odometer"))) is not None and miglia > 0:
        s.odometro_km = round(miglia * _MIGLIO_KM, 1)

    if elettrica:
        carica = _numero(co.get("evStateOfChargePercent"))
        if carica is None:
            carica = _numero(st.get("evStateOfChargePercent"))
        s.batteria = carica if carica is not None and 0 <= carica <= 100 else None
        autonomia = _numero(st.get("evDistanceToEmptyKilometers"))
        if autonomia is None:
            autonomia = _km(co.get("evDistanceToEmpty"), co.get("evDistanceToEmptyUnit"))
        s.autonomia_km = autonomia
        ricarica = _testo(co.get("evChargerStateType"))
        if ricarica == "CHARGING":
            s.stato_ricarica = StatoRicarica.IN_CARICA
        elif ricarica is not None:
            s.stato_ricarica = StatoRicarica.NON_IN_CARICA
        spina = _testo(co.get("evIsPluggedIn"))
        if spina is not None:
            s.cavo_collegato = spina.endswith("_CONNECTED") and not spina.startswith("NOT")
        minuti = _numero(co.get("evTimeToFullyCharged"))
        if s.stato_ricarica == StatoRicarica.IN_CARICA and minuti and minuti > 0:
            s.tempo_ricarica_min = int(minuti)

    benzina = _numero(st.get("distanceToEmptyFuelKilometers10s"))
    if benzina is None or benzina <= 0:
        benzina = _km(co.get("distanceToEmptyFuel"), co.get("distanceToEmptyFuelUnit"))
    s.autonomia_carburante_km = benzina if benzina and benzina > 0 else None
    serbatoio = _numero(st.get("remainingFuelPercent"))
    if serbatoio is None:
        serbatoio = _numero(co.get("remainingFuelPercent"))
    s.carburante_percento = serbatoio if serbatoio is not None and 0 <= serbatoio <= 100 else None
    if s.autonomia_carburante_km is not None and s.autonomia_km is not None:
        s.autonomia_totale_km = round(s.autonomia_carburante_km + s.autonomia_km, 1)

    for ruota, nome in _GOMME.items():
        kpa = _numero(st.get(f"tirePressure{nome}"))
        if kpa is None or kpa <= 0:
            psi = _numero(st.get(f"tirePressure{nome}Psi"))
            kpa = round(psi * _PSI_KPA, 1) if psi and psi > 0 else None
        if kpa is not None:
            s.pneumatici_kpa[ruota] = float(kpa)

    quadro = _testo(co.get("vehicleStateType") or st.get("vehicleStateType"))
    if quadro in _QUADRO_ACCESO:
        s.quadro_acceso = True
    elif quadro in _QUADRO_SPENTO:
        s.quadro_acceso = False

    s.chiusa = _tutti([_testo(co.get(k)) for k in _SERRATURE], "LOCKED", "UNLOCKED")
    porte = [_testo(co.get(k)) for k in _PORTE]
    if any(p in ("OPEN", "CLOSED") for p in porte):
        s.portiere_aperte = "OPEN" in porte
    baule = _testo(co.get("doorBootPosition"))
    if baule in ("OPEN", "CLOSED"):
        s.bagagliaio_aperto = baule == "OPEN"
    # «CLOSE»/«CLOSED» chiuso; aperto, socchiuso («VENTED») o il tetto inclinato contano come aperti.
    finestrini = [f for k in _FINESTRINI if (f := _testo(co.get(k) or st.get(k))) is not None]
    if finestrini:
        s.finestrini_aperti = any(f not in ("CLOSE", "CLOSED") for f in finestrini)

    _posizione(s, st.get("latitude"), st.get("longitude"))
    if posizione:
        _posizione(s, posizione.get("latitude"), posizione.get("longitude"))

    if salute is not None:
        for voce in salute.get("vehicleHealthItems") or []:
            if isinstance(voce, dict) and voce.get("featureCode") == "TPMS_MIL":
                s.pneumatici_allarme = bool(voce.get("isTrouble"))

    quando = [
        q
        for q in (
            _quando(st.get("eventDateStr")),
            _quando(co.get("lastUpdatedTime")),
            _quando((posizione or {}).get("timestamp")),
        )
        if q is not None
    ]
    s.letto = max(quando) if quando else None
    return s


def generazione(caratteristiche: list[str]) -> str:
    """La telematica dell'auto: la più nuova fra quelle annunciate."""
    return next((g for g in GENERAZIONI if g in caratteristiche), "")


def vettura_da(dati: dict[str, Any]) -> Vettura | None:
    """``selectVehicle.json`` → l'anagrafica di GdAAuto."""
    if not isinstance(dati, dict) or not dati.get("vin"):
        return None
    caratteristiche = [str(x) for x in dati.get("features") or []]
    abbonamenti = [str(x) for x in dati.get("subscriptionFeatures") or []]
    attivo = str(dati.get("subscriptionStatus") or "").upper() == "ACTIVE"
    modello = str(dati.get("modelName") or "").strip() or "Subaru"
    nome = str(dati.get("nickname") or dati.get("vehicleName") or "").strip()
    try:
        anno: int | None = int(dati.get("modelYear"))
    except (TypeError, ValueError):
        anno = None
    gen = generazione(caratteristiche)
    return Vettura(
        vin=str(dati["vin"]).upper(),
        marca=ID,
        modello=modello,
        nome=nome or f"Subaru {modello}",
        anno=anno,
        targa=str(dati.get("licensePlate") or ""),
        colore=str(dati.get("extDescrip") or ""),
        extra={
            "generazione": gen,
            # g3 e g4 parlano agli indirizzi della g2; solo la g1 ha i suoi.
            "percorso": "g1" if gen == "g1" else "g2",
            "elettrica": "PHEV" in caratteristiche,
            "remoto": attivo and bool(_REMOTI & set(abbonamenti)),
            "avviamento": "RES" in caratteristiche,
            "gomme": "TPMS_MIL" in caratteristiche,
        },
    )


def comandi_di(v: Vettura) -> set[str]:
    """I comandi a distanza vogliono l'abbonamento Security (o Companion+) attivo."""
    if not v.extra.get("remoto"):
        return set()
    comandi = {"chiudi", "apri", "trova"}
    if v.extra.get("percorso") == "g2" and (v.extra.get("elettrica") or v.extra.get("avviamento")):
        comandi.add("clima")
    if v.extra.get("percorso") == "g2" and v.extra.get("elettrica"):
        comandi.add("ricarica")
    return comandi
