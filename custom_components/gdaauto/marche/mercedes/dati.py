"""Dagli attributi di Mercedes allo stato di GdAAuto.

Il cloud Mercedes descrive l'auto con attributi dal nome fisso (gli stessi
dell'app): ``soc`` è la batteria, ``rangeelectric`` l'autonomia, ``odo`` il
contachilometri, ``doorlockstatusvehicle`` la chiusura. Ognuno porta il
valore, a volte la sua unità (km o miglia, kPa, bar o psi, °C o °F) e uno
stato: 3 e 4 vogliono dire «l'auto non l'ha mandato», e allora per GdAAuto
è ``None``. I significati dei codici vengono da ReneNulschDE/mbapi2020 (MIT).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura
from . import messaggi as m
from .messaggi import Aggiornamento

MIGLIO_KM = 1.609344
BAR_KPA = 100.0
PSI_KPA = 6.894757

# chargingstatus, come lo dice l'app.
_RICARICA = {
    0: StatoRicarica.IN_CARICA,  # in carica
    1: StatoRicarica.COMPLETATA,  # carica finita
    2: StatoRicarica.IN_PAUSA,  # pausa
    3: StatoRicarica.NON_IN_CARICA,  # spina staccata
    4: StatoRicarica.ERRORE,  # guasto
    5: StatoRicarica.IN_CARICA,  # lenta
    6: StatoRicarica.IN_CARICA,  # veloce
    7: StatoRicarica.NON_IN_CARICA,  # sta scaricando (V2X)
    8: StatoRicarica.NON_IN_CARICA,  # non in carica
    9: StatoRicarica.IN_CARICA,  # lenta, oltre l'obiettivo del viaggio
    10: StatoRicarica.IN_CARICA,
    11: StatoRicarica.IN_CARICA,
    12: StatoRicarica.NON_IN_CARICA,  # collegata, ma ferma
    13: StatoRicarica.IN_CARICA,  # AC
    14: StatoRicarica.IN_CARICA,  # DC
    15: StatoRicarica.NON_IN_CARICA,  # calibrazione della batteria
}
# chargingstatus che dicono già se il cavo c'è.
_CAVO_SI = {0, 1, 2, 5, 6, 9, 10, 11, 12, 13, 14}
_CAVO_NO = {3}

# doorlockstatusvehicle: 0 aperta, 1 chiusa da dentro, 2 chiusa, 3 aperta in parte, 4 non si sa.
_CHIUSA = {0: False, 1: True, 2: True, 3: False}

_PORTE = ("doorstatusfrontleft", "doorstatusfrontright", "doorstatusrearleft", "doorstatusrearright")
_FINESTRINI = ("windowstatusfrontleft", "windowstatusfrontright", "windowstatusrearleft", "windowstatusrearright")
_FINESTRINO_CHIUSO = 2

_RUOTE = {
    "anteriore_sinistro": "tirepressureFrontLeft",
    "anteriore_destro": "tirepressureFrontRight",
    "posteriore_sinistro": "tirepressureRearLeft",
    "posteriore_destro": "tirepressureRearRight",
}

# ignitionstate: 0 bloccato, 1 spento, 2 accessori, 4 acceso, 5 avviamento.
_QUADRO_ACCESO = {4, 5}


def _valido(attributi: dict[str, dict[str, Any]], nome: str) -> dict[str, Any] | None:
    a = attributi.get(nome)
    if not a or a.get("nil") or a.get("stato") in m.STATI_SENZA_VALORE:
        return None
    if "valore" not in a and "programmi" not in a and "prese" not in a and "display" not in a:
        return None
    return a


def _numero(attributi: dict[str, dict[str, Any]], nome: str) -> float | None:
    a = _valido(attributi, nome)
    if a is None:
        return None
    v = a.get("valore")
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(a.get("display") if v is None else v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _intero(attributi: dict[str, dict[str, Any]], nome: str) -> int | None:
    n = _numero(attributi, nome)
    return int(n) if n is not None else None


def _vero(attributi: dict[str, dict[str, Any]], nome: str) -> bool | None:
    a = _valido(attributi, nome)
    if a is None:
        return None
    v = a.get("valore")
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return v > 0
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1")
    return None


def _unita(attributi: dict[str, dict[str, Any]], nome: str, quale: str) -> int:
    return int((attributi.get(nome) or {}).get("unita", {}).get(quale, 0))


def _km(attributi: dict[str, dict[str, Any]], nome: str) -> float | None:
    n = _numero(attributi, nome)
    if n is None:
        return None
    if _unita(attributi, nome, "distanza") == m.MIGLIA:
        return round(n * MIGLIO_KM, 1)
    return n


def _kpa(attributi: dict[str, dict[str, Any]], nome: str) -> float | None:
    n = _numero(attributi, nome)
    if n is None or n <= 0:
        return None
    unita = _unita(attributi, nome, "pressione")
    if unita == m.BAR or (unita == 0 and n < 10):
        return round(n * BAR_KPA, 1)
    if unita == m.PSI or (unita == 0 and n < 80):
        return round(n * PSI_KPA, 1)
    return round(n, 1)


def _celsius(attributi: dict[str, dict[str, Any]], nome: str) -> float | None:
    n = _numero(attributi, nome)
    if n is None:
        return None
    if _unita(attributi, nome, "temperatura") == m.FAHRENHEIT:
        return round((n - 32) * 5 / 9, 1)
    return n


def _qualcuno(valori: list[bool | None]) -> bool | None:
    noti = [v for v in valori if v is not None]
    return any(noti) if noti else None


def _limite(attributi: dict[str, dict[str, Any]]) -> int | None:
    """Il limite di carica: quello del programma scelto, o ``maxSoc`` dove c'è."""
    programmi = (_valido(attributi, "chargePrograms") or {}).get("programmi") or []
    scelto = _intero(attributi, "selectedChargeProgram")
    if programmi:
        if scelto is not None:
            for p in programmi:
                if p["programma"] == scelto and p["max_soc"]:
                    return int(p["max_soc"])
            # Alcune auto danno il numero di posizione nell'elenco, non il programma.
            if 0 <= scelto < len(programmi) and programmi[scelto]["max_soc"]:
                return int(programmi[scelto]["max_soc"])
        elif programmi[0]["max_soc"]:
            return int(programmi[0]["max_soc"])
    for nome in ("maxSoc", "max_soc"):
        if (n := _intero(attributi, nome)) is not None and n > 0:
            return n
    return None


def _cavo(attributi: dict[str, dict[str, Any]], ricarica: int | None) -> bool | None:
    prese = (_valido(attributi, "chargeInlets") or {}).get("prese") or []
    spine = [p["spina"] for p in prese]
    if any(s in (m.SPINA_INSERITA, m.SPINA_INSERITA_IN_AUTO) for s in spine):
        return True
    if spine and all(s == m.SPINA_STACCATA for s in spine):
        return False
    if ricarica in _CAVO_SI:
        return True
    if ricarica in _CAVO_NO:
        return False
    return None


def _letto(agg: Aggiornamento) -> datetime | None:
    istanti = [a["ts_ms"] / 1000 for a in agg.attributi.values() if a.get("ts_ms")]
    istanti += [a["ts"] for a in agg.attributi.values() if a.get("ts") and not a.get("ts_ms")]
    if agg.emesso_ms:
        istanti.append(agg.emesso_ms / 1000)
    if not istanti:
        return None
    return datetime.fromtimestamp(max(istanti), UTC)


def interpreta(agg: Aggiornamento) -> StatoVettura:
    a = agg.attributi
    codice = _intero(a, "chargingstatus")
    ricarica = _RICARICA.get(codice) if codice is not None else None
    attiva = _vero(a, "chargingactive")
    if attiva is True:
        ricarica = StatoRicarica.IN_CARICA
    elif attiva is False and ricarica == StatoRicarica.IN_CARICA:
        ricarica = StatoRicarica.NON_IN_CARICA

    potenza = _numero(a, "chargingPower")
    if potenza is not None and ricarica not in (None, StatoRicarica.IN_CARICA):
        potenza = 0.0

    serratura = _intero(a, "doorlockstatusvehicle")
    finestrini = [None if (n := _intero(a, f)) is None else n != _FINESTRINO_CHIUSO for f in _FINESTRINI]
    clima = _qualcuno([_vero(a, "precondActive"), _vero(a, "precondNow")])
    quadro = _intero(a, "ignitionstate")

    return StatoVettura(
        batteria=_numero(a, "soc"),
        autonomia_km=_km(a, "rangeelectric"),
        odometro_km=_km(a, "odo"),
        stato_ricarica=ricarica,
        cavo_collegato=_cavo(a, codice),
        potenza_ricarica_diretta_kw=potenza,
        limite_ricarica=_limite(a),
        temperatura_esterna_c=_celsius(a, "outsideTemperature"),
        clima_acceso=clima,
        chiusa=_CHIUSA.get(serratura) if serratura is not None else None,
        portiere_aperte=_qualcuno([_vero(a, p) for p in _PORTE]),
        bagagliaio_aperto=_vero(a, "decklidstatus"),
        finestrini_aperti=_qualcuno(finestrini),
        pneumatici_kpa={r: p for r, nome in _RUOTE.items() if (p := _kpa(a, nome)) is not None},
        quadro_acceso=quadro in _QUADRO_ACCESO if quadro is not None else None,
        latitudine=_numero(a, "positionLat"),
        longitudine=_numero(a, "positionLong"),
        # Mercedes manda latitudine e longitudine col loro segno.
        posizione_con_segno=_numero(a, "positionLat") is not None,
        letto=_letto(agg),
        # Tutto il resto, per la diagnostica; la posizione no, perché lì non si toglierebbe.
        grezzo={
            nome: {k: v for k, v in voce.items() if k in ("valore", "display", "stato", "programmi", "prese")}
            for nome, voce in a.items()
            if not nome.startswith("position")
        },
    )
