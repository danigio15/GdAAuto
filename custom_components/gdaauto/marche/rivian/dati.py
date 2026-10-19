"""Dalle risposte GraphQL di Rivian allo stato di GdAAuto.

Il cloud dà ``vehicleState``: un oggetto con un campo per ogni segnale
dell'auto, e ogni segnale è ``{"timeStamp": …, "value": …}`` (la posizione è
``{"latitude", "longitude", "timeStamp"}``). I valori sono testi in
minuscolo («locked», «closed», «charging_active»…) o numeri, con queste
unità: carica e limite in %, autonomia (``distanceToEmpty``) in km, il
contachilometri (``vehicleMileage``) in **metri**, la velocità GPS in m/s, le
temperature in °C, la capacità in kWh, il tempo di fine ricarica in minuti.

Un segnale che l'auto non ha (o che ha «signal_not_available») resta
``None``. I nomi dei segnali e i loro valori sono quelli che usano
bretterer/rivian-python-client (MIT) e bretterer/home-assistant-rivian
(Apache 2.0): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...vettura import Marcia, StatoRicarica, StatoVettura, Vettura

ID = "rivian"

#: I segnali che si chiedono a ``vehicleState``: quelli che GdAAuto usa.
SEGNALI = (
    "batteryLevel",
    "batteryLimit",
    "batteryCapacity",
    "distanceToEmpty",
    "vehicleMileage",
    "chargerState",
    "chargerStatus",
    "chargePortState",
    "timeToEndOfCharge",
    "remoteChargingAvailable",
    "cabinClimateInteriorTemperature",
    "cabinClimateDriverTemperature",
    "cabinPreconditioningStatus",
    "cabinPreconditioningType",
    "defrostDefogStatus",
    "powerState",
    "gearStatus",
    "driveMode",
    "gnssLocation",
    "gnssSpeed",
    "gnssBearing",
    "alarmSoundStatus",
    "gearGuardLocked",
    "petModeStatus",
    "doorFrontLeftLocked",
    "doorFrontLeftClosed",
    "doorFrontRightLocked",
    "doorFrontRightClosed",
    "doorRearLeftLocked",
    "doorRearLeftClosed",
    "doorRearRightLocked",
    "doorRearRightClosed",
    "closureFrunkLocked",
    "closureFrunkClosed",
    "closureLiftgateLocked",
    "closureLiftgateClosed",
    "closureTailgateLocked",
    "closureTailgateClosed",
    "closureTonneauLocked",
    "closureTonneauClosed",
    "closureSideBinLeftLocked",
    "closureSideBinLeftClosed",
    "closureSideBinRightLocked",
    "closureSideBinRightClosed",
    "windowFrontLeftClosed",
    "windowFrontRightClosed",
    "windowRearLeftClosed",
    "windowRearRightClosed",
    "tirePressureStatusFrontLeft",
    "tirePressureStatusFrontRight",
    "tirePressureStatusRearLeft",
    "tirePressureStatusRearRight",
    "tirePressureStatusValidFrontLeft",
    "tirePressureStatusValidFrontRight",
    "tirePressureStatusValidRearLeft",
    "tirePressureStatusValidRearRight",
    "batteryHvThermalEvent",
    "twelveVoltBatteryHealth",
    "otaCurrentVersion",
)

#: Come si chiedono i segnali che non sono ``{ timeStamp value }``.
_FORME = {
    "gnssLocation": "{ latitude longitude timeStamp }",
    "cloudConnection": "{ lastSync isOnline }",
}


def frammento(segnali: tuple[str, ...] = SEGNALI) -> str:
    """Il pezzo di query GraphQL che chiede quei segnali."""
    return "{ " + " ".join(f"{s} {_FORME.get(s, '{ timeStamp value }')}" for s in segnali) + " }"


_PORTE = ("doorFrontLeft", "doorFrontRight", "doorRearLeft", "doorRearRight")
_CHIUSURE = (
    *_PORTE,
    "closureFrunk",
    "closureLiftgate",
    "closureTailgate",
    "closureTonneau",
    "closureSideBinLeft",
    "closureSideBinRight",
)
_BAGAGLIAIO = ("closureLiftgate", "closureTailgate")
_FINESTRINI = ("windowFrontLeft", "windowFrontRight", "windowRearLeft", "windowRearRight")
_GOMME = ("FrontLeft", "FrontRight", "RearLeft", "RearRight")

#: ``chargerState`` → lo stato di GdAAuto. Quelli con «error» sono errori.
_RICARICA = {
    "charging_active": StatoRicarica.IN_CARICA,
    "charging_connecting": StatoRicarica.IN_CARICA,
    "smart_charging_active": StatoRicarica.IN_CARICA,
    "charging_complete": StatoRicarica.COMPLETATA,
    "charging_scheduled": StatoRicarica.PROGRAMMATA,
    "smart_charging_paused": StatoRicarica.IN_PAUSA,
    "charging_stopped_by_user": StatoRicarica.IN_PAUSA,
    "charging_stopped_by_station": StatoRicarica.IN_PAUSA,
    "charging_vehicle_stopped": StatoRicarica.IN_PAUSA,
    "charging_ready": StatoRicarica.NON_IN_CARICA,
    "waiting_on_charger": StatoRicarica.NON_IN_CARICA,
    "charging_sd_compensation": StatoRicarica.IN_CARICA,
}

_MARCE = {
    "park": Marcia.PARCHEGGIO,
    "drive": Marcia.AVANTI,
    "reverse": Marcia.RETROMARCIA,
    "neutral": Marcia.FOLLE,
}

#: I valori che vogliono dire «il segnale non c'è», non «chiuso» o «aperto».
_NIENTE = {"", "signal_not_available", "undefined", "unknown", "not_available", "none"}

#: Il clima acceso (``cabinPreconditioningStatus``), come nell'app.
_CLIMA_ACCESO = {"active", "complete_maintain", "initiate"}


def _valore(stato: dict[str, Any], nome: str) -> Any:
    voce = stato.get(nome)
    if not isinstance(voce, dict):
        return None
    valore = voce.get("value")
    if isinstance(valore, str) and valore.strip().lower() in _NIENTE:
        return None
    return valore


def _testo(stato: dict[str, Any], nome: str) -> str | None:
    valore = _valore(stato, nome)
    return str(valore).strip().lower() if valore is not None else None


def _numero(stato: dict[str, Any], nome: str) -> float | None:
    valore = _valore(stato, nome)
    if valore is None or isinstance(valore, bool):
        return None
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def _quando(testo: Any) -> datetime | None:
    if not isinstance(testo, str) or not testo:
        return None
    try:
        return datetime.fromisoformat(testo)
    except ValueError:
        return None


def _tutti(valori: list[str | None], si: str, no: str) -> bool | None:
    """Vero se tutti i segnali noti dicono ``si``, falso se uno dice ``no``."""
    noti = [v for v in valori if v in (si, no)]
    if not noti:
        return None
    return all(v == si for v in noti)


def _almeno_uno(valori: list[str | None], si: str, no: str) -> bool | None:
    noti = [v for v in valori if v in (si, no)]
    if not noti:
        return None
    return any(v == si for v in noti)


def interpreta(stato: dict[str, Any]) -> StatoVettura:
    """``vehicleState`` → ``StatoVettura``."""
    s = StatoVettura(grezzo=dict(stato))
    s.batteria = _numero(stato, "batteryLevel")
    s.limite_ricarica = int(v) if (v := _numero(stato, "batteryLimit")) else None
    s.capacita_batteria_kwh = c if (c := _numero(stato, "batteryCapacity")) and 10 <= c <= 250 else None
    s.autonomia_km = _numero(stato, "distanceToEmpty")
    if (metri := _numero(stato, "vehicleMileage")) is not None:
        s.odometro_km = round(metri / 1000, 1)

    carica = _testo(stato, "chargerState")
    if carica is not None:
        s.stato_ricarica = _RICARICA.get(carica, StatoRicarica.ERRORE if "error" in carica else None)
    cavo = _testo(stato, "chargerStatus")
    if cavo is not None:
        s.cavo_collegato = cavo != "chrgr_sts_not_connected"
    fine = _numero(stato, "timeToEndOfCharge")
    # Fuori dalla ricarica il cloud lascia l'ultimo valore: conta solo mentre carica.
    if fine and fine > 0 and s.stato_ricarica == StatoRicarica.IN_CARICA:
        s.tempo_ricarica_min = int(fine)

    s.temperatura_interna_c = _numero(stato, "cabinClimateInteriorTemperature")
    # Qui «undefined» vuol dire «spento»: il clima non è mai partito.
    clima = (stato.get("cabinPreconditioningStatus") or {}).get("value")
    if isinstance(clima, str) and clima:
        s.clima_acceso = clima.strip().lower() in _CLIMA_ACCESO
    impostata = _numero(stato, "cabinClimateDriverTemperature")
    # 0 e 63,5 sono «LO» e «HI»: non sono gradi.
    s.temperatura_clima_c = impostata if impostata is not None and 10 <= impostata <= 35 else None
    sbrina = _testo(stato, "defrostDefogStatus")
    if sbrina is not None:
        s.sbrinamento = sbrina != "off"

    potenza = _testo(stato, "powerState")
    if potenza is not None:
        s.in_sonno = potenza == "sleep"
        s.quadro_acceso = potenza == "go"
    s.marcia = _MARCE.get(_testo(stato, "gearStatus") or "")

    s.chiusa = _tutti([_testo(stato, f"{c}Locked") for c in _CHIUSURE], "locked", "unlocked")
    s.portiere_aperte = _almeno_uno([_testo(stato, f"{c}Closed") for c in _PORTE], "open", "closed")
    s.bagagliaio_aperto = _almeno_uno([_testo(stato, f"{c}Closed") for c in _BAGAGLIAIO], "open", "closed")
    s.finestrini_aperti = _almeno_uno([_testo(stato, f"{c}Closed") for c in _FINESTRINI], "open", "closed")
    gomme: list[bool] = []
    for g in _GOMME:
        valido = _testo(stato, f"tirePressureStatusValid{g}")
        esito = _testo(stato, f"tirePressureStatus{g}")
        if esito is not None and valido in (None, "valid"):
            gomme.append(esito != "ok")
    s.pneumatici_allarme = any(gomme) if gomme else None

    posizione = stato.get("gnssLocation")
    if isinstance(posizione, dict) and posizione.get("latitude") is not None and posizione.get("longitude") is not None:
        s.latitudine = float(posizione["latitude"])
        s.longitudine = float(posizione["longitude"])
        s.posizione_con_segno = True
    if (velocita := _numero(stato, "gnssSpeed")) is not None:
        s.velocita_kmh = round(max(velocita, 0.0) * 3.6, 1)

    quando = [
        q for voce in stato.values() if isinstance(voce, dict) and (q := _quando(voce.get("timeStamp"))) is not None
    ]
    s.letto = max(quando) if quando else None
    return s


def _anno(valore: Any) -> int | None:
    try:
        anno = int(valore)
    except (TypeError, ValueError):
        return None
    return anno if 2015 <= anno <= 2100 else None


def vetture_da(utente: dict[str, Any], chiave_pubblica: str = "") -> list[Vettura]:
    """``currentUser`` → le auto di GdAAuto.

    ``chiave_pubblica`` è quella del telefono registrato che GdAAuto usa per
    i comandi (esadecimale, punto non compresso): se il cloud la conosce per
    un'auto, quell'auto si può comandare.
    """
    telefoni: dict[str, dict[str, str]] = {}
    for telefono in utente.get("enrolledPhones") or []:
        if not isinstance(telefono, dict):
            continue
        accesso = telefono.get("vas") or {}
        if not chiave_pubblica or str(accesso.get("publicKey") or "").lower() != chiave_pubblica.lower():
            continue
        for voce in telefono.get("enrolled") or []:
            if isinstance(voce, dict) and voce.get("vehicleId") and voce.get("identityId"):
                telefoni[str(voce["vehicleId"])] = {
                    "telefono": str(accesso.get("vasPhoneId") or ""),
                    "identita": str(voce["identityId"]),
                }
    elenco: list[Vettura] = []
    for voce in utente.get("vehicles") or []:
        if not isinstance(voce, dict) or not voce.get("vin") or not voce.get("id"):
            continue
        auto = voce.get("vehicle") or {}
        accesso = voce.get("vas") or {}
        funzioni = [
            str(f.get("name"))
            for f in ((auto.get("vehicleState") or {}).get("supportedFeatures") or [])
            if isinstance(f, dict) and f.get("status") == "AVAILABLE" and f.get("name")
        ]
        modello = str(auto.get("model") or "").strip() or "Rivian"
        nome = str(voce.get("name") or "").strip()
        ruoli = [str(r).lower() for r in voce.get("roles") or []]
        elenco.append(
            Vettura(
                vin=str(voce["vin"]),
                marca=ID,
                modello=modello,
                nome=nome if nome and nome.lower() != modello.lower() else f"Rivian {modello}",
                anno=_anno(auto.get("modelYear")),
                condivisa=bool(ruoli) and "primary-owner" not in ruoli,
                extra={
                    "id": str(voce["id"]),
                    "chiave_auto": str(accesso.get("vehiclePublicKey") or ""),
                    "funzioni": funzioni,
                    **telefoni.get(str(voce["id"]), {}),
                },
            )
        )
    return elenco


def ultima_ricarica(sessioni: Any, id_auto: str) -> dict[str, Any]:
    """L'ultima sessione di ``getCompletedSessionSummaries`` di quell'auto."""
    migliori: list[tuple[datetime, dict[str, Any]]] = []
    for voce in sessioni or []:
        if not isinstance(voce, dict):
            continue
        if voce.get("vehicleId") and str(voce["vehicleId"]) != id_auto:
            continue
        if (fine := _quando(voce.get("endInstant"))) is not None:
            migliori.append((fine, voce))
    if not migliori:
        return {}
    fine, voce = max(migliori, key=lambda x: x[0])
    tipo = str(voce.get("chargerType") or "").lower()
    try:
        energia = round(float(voce.get("totalEnergyKwh")), 2)
    except (TypeError, ValueError):
        energia = None
    return {
        "ultima_ricarica_kwh": energia,
        "ultima_ricarica_tipo": "dc" if "dc" in tipo or "fast" in tipo else "ac" if tipo else None,
        "ultima_ricarica_inizio": _quando(voce.get("startInstant")),
        "ultima_ricarica_fine": fine,
    }
