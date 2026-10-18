"""Lo stato dell'auto, uno solo per tutti i modelli.

Il cloud risponde in due modi. La T03 (e la S01) mandano campi con un nome,
``soc``, ``expectedMileage``, ``totalMileage``. Le C10, B10, B05, C16 e le
altre mandano un dizionario ``signal`` con numeri al posto dei nomi: ``1204``
è la batteria, ``3260`` l'autonomia. Qui tutto diventa uno ``StatoVettura``:
chi sta sopra (i sensori, GdAHome, gdanav) non sa più che modello è.

I numeri dei segnali e il loro significato sono quelli ricostruiti dalla
comunità dall'app ufficiale; un segnale che non conosciamo resta in
``grezzo`` e si vede nella diagnostica.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum
from typing import Any

from ...vettura import Marcia, StatoRicarica, StatoVettura

# segnale numerico → nome del campo (lo stesso che la T03 manda già col nome)
SEGNALI: dict[str, str] = {
    "1204": "soc",
    "100003": "preciseSoc",
    "1200": "chargeRemainTime",
    "1178": "batteryCurrent",
    "1177": "batteryVoltage",
    "47": "acInputSlowCharge",
    "1197": "dcInputFastCharge",
    "1149": "chargeState",
    "1182": "minBatteryTemp",
    "3736": "chargeCompleted",
    "3260": "expectedMileage",
    "2188": "liveRemainingRange",
    "3257": "maxRange",
    "1319": "speed",
    "1318": "totalMileage",
    "1010": "gearStatus",
    "1944": "vehicleState",
    "1938": "acSwitch",
    "2183": "acSetting",
    "1349": "interiorTemp",
    "1298": "driverDoorLockStatus",
    "1277": "lbcmDriverDoorStatus",
    "1278": "rbcmDriverDoorStatus",
    "1279": "lbcmLeftRearDoorStatus",
    "1280": "rbcmRightRearDoorStatus",
    "1281": "bbcmBackDoorStatus",
    "1693": "driverWindowStatus",
    "1694": "rightFrontWindowStatus",
    "1695": "leftRearWindowStatus",
    "1696": "rightRearWindowStatus",
    "3727": "leftFrontWindowPercent",
    "3728": "rightFrontWindowPercent",
    "1879": "leftRearWindowPercent",
    "1880": "rightRearWindowPercent",
    "2667": "leftFrontTirePressure",
    "2653": "rightFrontTirePressure",
    "2646": "leftRearTirePressure",
    "2660": "rightRearTirePressure",
    "1258": "bcmKeyPositionOn3",
    "3636": "sentryMode",
    "1255": "vehicleSecurityActive",
}

# La posizione: prima i segnali con il segno (ovest e sud negativi), poi
# quelli in valore assoluto, in due versioni.
_LATITUDINI = ("3", "3725", "2190")
_LONGITUDINI = ("2", "3724", "2191")


# --- la lettura ---------------------------------------------------------------


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return float(v) if isinstance(v, bool) else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _intero(v: Any) -> int | None:
    n = _numero(v)
    return int(n) if n is not None else None


def _acceso(v: Any) -> bool | None:
    """0/1, true/false, "0"/"1": tutti modi per dire sì o no."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "yes", "on"):
            return True
        if t in ("false", "no", "off"):
            return False
    n = _numero(v)
    return None if n is None else n != 0


def _enum(classe: type[IntEnum], v: Any) -> Any:
    n = _intero(v)
    if n is None:
        return None
    try:
        return classe(n)
    except ValueError:
        return None


def _data(v: Any) -> datetime | None:
    """``collectTime`` della T03 («2026-05-07 10:30:00», ora locale del server)
    oppure ``sts`` delle altre (epoca in millisecondi)."""
    if v is None:
        return None
    if isinstance(v, int | float) and not isinstance(v, bool):
        secondi = v / 1000 if v > 9_999_999_999 else v
        try:
            return datetime.fromtimestamp(secondi, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace(" ", "T"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return None


def _pressione(v: Any) -> float | None:
    """In kPa. Una C10 dice 250 (2,5 bar); se arrivasse ×100 si riporta giù."""
    n = _numero(v)
    if n is None or n <= 0:
        return None
    return round(n / 100, 1) if n > 1000 else float(n)


def appiattisci(dati: dict[str, Any]) -> dict[str, Any]:
    """I segnali numerici diventano campi con un nome; i campi con un nome
    che ci sono già vincono. La T03 passa così com'è."""
    piatto = {k: v for k, v in dati.items() if k not in ("signal", "config")}
    segnali = dati.get("signal")
    if isinstance(segnali, dict):
        for numero, nome in SEGNALI.items():
            if numero in segnali and nome not in piatto:
                piatto[nome] = segnali[numero]
        for nome, numeri in (("latitude", _LATITUDINI), ("longitude", _LONGITUDINI)):
            if nome in piatto:
                continue
            for numero in numeri:
                if segnali.get(numero) is not None:
                    piatto[nome] = segnali[numero]
                    piatto[f"_{nome}ConSegno"] = numero == numeri[0]
                    break
        if "sts" in segnali and "collectTime" not in piatto:
            piatto["collectTime"] = segnali["sts"]
    piano = (dati.get("config") or {}).get("3") if isinstance(dati.get("config"), dict) else None
    if isinstance(piano, dict) and "chargesocSetting" not in piatto:
        piatto["chargesocSetting"] = piano.get("percent")
    return piatto


def interpreta(dati: dict[str, Any]) -> StatoVettura:
    """Dalla risposta del cloud (il ``data``) allo stato dell'auto."""
    d = appiattisci(dati)
    s = StatoVettura(grezzo=dati)

    s.batteria = _numero(d.get("preciseSoc")) if d.get("preciseSoc") is not None else _numero(d.get("soc"))
    if s.batteria is not None and not 0 <= s.batteria <= 100:
        s.batteria = None
    s.autonomia_km = _numero(d.get("expectedMileage"))
    if s.autonomia_km is None:
        s.autonomia_km = _numero(d.get("liveRemainingRange"))
    s.odometro_km = _numero(d.get("totalMileage"))
    s.velocita_kmh = _numero(d.get("speed"))
    s.marcia = _enum(Marcia, d.get("gearStatus"))
    s.stato_ricarica = _enum(StatoRicarica, d.get("chargeState"))
    s.tensione_v = _numero(d.get("batteryVoltage"))
    s.corrente_a = _numero(d.get("batteryCurrent"))
    s.tempo_ricarica_min = _intero(d.get("chargeRemainTime"))
    wh = _numero(d.get("dumpEnergy"))
    s.energia_residua_kwh = round(wh / 1000, 2) if wh is not None else None
    s.temperatura_batteria_c = _numero(d.get("minBatteryTemp"))
    s.temperatura_interna_c = _numero(d.get("interiorTemp"))
    s.limite_ricarica = _intero(d.get("chargesocSetting"))
    s.clima_acceso = _acceso(d.get("acSwitch"))
    s.temperatura_clima_c = _numero(d.get("acSetting"))
    s.chiusa = _acceso(d.get("driverDoorLockStatus"))
    s.quadro_acceso = _acceso(d.get("bcmKeyPositionOn3"))
    s.sentinella = _acceso(d.get("sentryMode"))

    # Il cavo: le C10 hanno due sensori (lento e rapido); la T03 lo lascia
    # capire dallo stato della ricarica.
    lento = _acceso(d.get("acInputSlowCharge"))
    rapido = _acceso(d.get("dcInputFastCharge"))
    if lento is not None or rapido is not None:
        s.cavo_collegato = bool(lento) or bool(rapido)
    elif s.stato_ricarica is not None:
        s.cavo_collegato = s.stato_ricarica in (
            StatoRicarica.IN_CARICA,
            StatoRicarica.COMPLETATA,
            StatoRicarica.ERRORE,
            StatoRicarica.PROGRAMMATA,
            StatoRicarica.IN_PAUSA,
        )

    porte = [
        _acceso(d.get(k))
        for k in ("lbcmDriverDoorStatus", "rbcmDriverDoorStatus", "lbcmLeftRearDoorStatus", "rbcmRightRearDoorStatus")
    ]
    if any(p is not None for p in porte):
        s.portiere_aperte = any(bool(p) for p in porte)
    s.bagagliaio_aperto = _acceso(d.get("bbcmBackDoorStatus"))
    finestrini = [
        _acceso(d.get(k))
        for k in ("driverWindowStatus", "rightFrontWindowStatus", "leftRearWindowStatus", "rightRearWindowStatus")
    ]
    percentuali = [
        _numero(d.get(k))
        for k in (
            "leftFrontWindowPercent",
            "rightFrontWindowPercent",
            "leftRearWindowPercent",
            "rightRearWindowPercent",
        )
    ]
    if any(f is not None for f in finestrini) or any(p is not None for p in percentuali):
        s.finestrini_aperti = any(bool(f) for f in finestrini) or any(p and p > 0 for p in percentuali)

    for ruota, campo in (
        ("anteriore_sinistro", "leftFrontTirePressure"),
        ("anteriore_destro", "rightFrontTirePressure"),
        ("posteriore_sinistro", "leftRearTirePressure"),
        ("posteriore_destro", "rightRearTirePressure"),
    ):
        p = _pressione(d.get(campo))
        if p is not None:
            s.pneumatici_kpa[ruota] = p

    s.latitudine = _numero(d.get("latitude"))
    s.longitudine = _numero(d.get("longitude"))
    s.posizione_con_segno = bool(d.get("_latitudeConSegno")) and bool(d.get("_longitudeConSegno"))
    if s.latitudine is not None and s.longitudine is not None and not (s.latitudine or s.longitudine):
        # 0,0 è «non lo so», non il Golfo di Guinea.
        s.latitudine = s.longitudine = None
    s.letto = _data(d.get("collectTime"))
    return s
