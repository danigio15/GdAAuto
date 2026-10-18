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
    # Le gomme in ordine: l'app ufficiale, leapmotor-ha e leapmotor-mate (provato su due B10) concordano.
    "2646": "leftFrontTirePressure",
    "2653": "rightFrontTirePressure",
    "2660": "leftRearTirePressure",
    "2667": "rightRearTirePressure",
    "2641": "leftFrontTirePressureState",
    "2648": "rightFrontTirePressureState",
    "2655": "leftRearTirePressureState",
    "2662": "rightRearTirePressureState",
    "1258": "bcmKeyPositionOn3",
    "3636": "sentryMode",
    "1255": "vehicleSecurityActive",
    "3262": "rangeMode",
    "3235": "fuelPercent",
    "3259": "expectedFuelMileage",
    "3261": "expectedCombinedMileage",
    "1186": "batteryThermalRequest",
    "1480": "parkingBrakeState",
    "1945": "windshieldDefrost",
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
    if isinstance(piano, dict):
        if "chargesocSetting" not in piatto:
            piatto["chargesocSetting"] = piano.get("percent")
        piatto["_piano"] = piano
    piatto["_conSegnali"] = isinstance(segnali, dict)
    return piatto


#: Oltre questo, i dati sono di quando l'auto si è addormentata (come fa leapmotor-ha).
_VECCHI_DOPO_S = 900
#: La marcia: 0 P, 1 R, 2 N, 3 D, come dicono l'app ufficiale (leapmotor-ha) e leapmotor-mate.
_MARCE = {0: Marcia.PARCHEGGIO, 1: Marcia.RETROMARCIA, 2: Marcia.FOLLE, 3: Marcia.AVANTI}


def _ora(v: Any) -> str | None:
    """«HH:MM» del piano di ricarica, o niente."""
    if not isinstance(v, str) or len(v.strip()) < 4 or ":" not in v:
        return None
    ore, _, minuti = v.strip().partition(":")
    if not (ore.isdigit() and minuti[:2].isdigit()):
        return None
    return f"{int(ore):02d}:{minuti[:2]}"


def _ricarica(d: dict[str, Any], s: StatoVettura, in_moto: bool) -> None:
    """Lo stato della ricarica e il cavo, con quello che le auto europee fanno davvero.

    Il codice 1149 non basta da solo: le C10 e alcune B10 dicono 2 («finita»)
    mentre caricano, una ibrida dice 3 per un attimo a metà carica, 4 è
    «collegata, aspetta l'ora del piano», 5 è un codice di marcia. Si guarda
    anche se l'auto sta davvero prendendo corrente.
    """
    codice = _intero(d.get("chargeState"))
    resta = _intero(d.get("chargeRemainTime"))
    corrente = _numero(d.get("batteryCurrent"))
    tensione = _numero(d.get("batteryVoltage"))
    potenza = abs(corrente * tensione) / 1000 if corrente is not None and tensione is not None else 0.0
    # Corrente che entra nella batteria (negativa), più del rumore di un'auto ferma col cavo.
    entra = corrente is not None and corrente <= -2 and potenza >= 1
    prende = bool(resta and resta > 0) or entra

    lento = _intero(d.get("acInputSlowCharge"))
    rapido = _acceso(d.get("dcInputFastCharge"))
    s.v2l_attivo = (lento == 2) if lento is not None else None

    if in_moto:
        # In marcia non si carica, qualunque cosa dica un segnale rimasto indietro.
        s.stato_ricarica = StatoRicarica.NON_IN_CARICA if codice is not None or lento is not None else None
        s.cavo_collegato = False if (codice is not None or lento is not None or rapido is not None) else None
        return
    if codice is not None:
        if codice in (1, 2, 3):
            if codice == 1 or prende:
                s.stato_ricarica = StatoRicarica.IN_CARICA
            elif codice == 2:
                s.stato_ricarica = StatoRicarica.COMPLETATA
            else:
                s.stato_ricarica = StatoRicarica.IN_PAUSA
        elif codice == 4:
            s.stato_ricarica = StatoRicarica.PROGRAMMATA
        elif codice == 6:
            s.stato_ricarica = StatoRicarica.IN_PAUSA
        elif codice in (0, 5):
            s.stato_ricarica = StatoRicarica.NON_IN_CARICA
        if s.stato_ricarica is not None:
            s.cavo_collegato = codice in (1, 2, 3, 4, 6)
        elif lento is not None or rapido is not None:
            # Un codice che non conosciamo: decide la presa.
            s.cavo_collegato = lento == 1 or bool(rapido)
    elif lento is not None or rapido is not None:
        s.cavo_collegato = lento == 1 or bool(rapido)
    if s.stato_ricarica == StatoRicarica.IN_CARICA and d.get("_conSegnali"):
        # Sulla T03 il segnale «rapida» resta acceso anche in una pausa della wallbox: lì non dice niente.
        s.ricarica_rapida = bool(rapido)


def interpreta(dati: dict[str, Any]) -> StatoVettura:
    """Dalla risposta del cloud (il ``data``) allo stato dell'auto."""
    d = appiattisci(dati)
    s = StatoVettura(grezzo=dati)

    s.batteria = _numero(d.get("preciseSoc")) if d.get("preciseSoc") is not None else _numero(d.get("soc"))
    if s.batteria is not None and not 0 <= s.batteria <= 100:
        s.batteria = None
    s.autonomia_km = _numero(d.get("expectedMileage"))
    if s.autonomia_km == 0 and (s.batteria or 0) > 5:
        # La C10 manda 0 km quando si addormenta, anche al 90 %: non è vero.
        s.autonomia_km = None
    if s.autonomia_km is None:
        s.autonomia_km = _numero(d.get("liveRemainingRange")) or None
    s.autonomia_nominale_km = _numero(d.get("maxRange")) or None
    s.carburante_percento = _numero(d.get("fuelPercent"))
    if s.carburante_percento is not None:
        # C'è il serbatoio: è un'ibrida col range extender.
        s.autonomia_carburante_km = _numero(d.get("expectedFuelMileage"))
        s.autonomia_totale_km = _numero(d.get("expectedCombinedMileage")) or None
    s.odometro_km = _numero(d.get("totalMileage"))
    s.velocita_kmh = _numero(d.get("speed"))
    marcia = _intero(d.get("gearStatus"))
    s.marcia = _MARCE.get(marcia) if marcia is not None else None
    s.tensione_v = _numero(d.get("batteryVoltage"))
    s.corrente_a = _numero(d.get("batteryCurrent"))
    s.tempo_ricarica_min = _intero(d.get("chargeRemainTime"))
    wh = _numero(d.get("dumpEnergy"))
    s.energia_residua_kwh = round(wh / 1000, 2) if wh is not None else None
    s.temperatura_batteria_c = _numero(d.get("minBatteryTemp"))
    s.temperatura_interna_c = _numero(d.get("interiorTemp"))
    s.temperatura_esterna_c = _numero(d.get("outdoorTemp"))
    s.limite_ricarica = _intero(d.get("chargesocSetting"))
    s.clima_acceso = _acceso(d.get("acSwitch"))
    s.temperatura_clima_c = _numero(d.get("acSetting"))
    sbrinamento = _intero(d.get("windshieldDefrost"))
    s.sbrinamento = sbrinamento in (1, 2) if sbrinamento is not None else None
    # 1 chiusa, 0 aperta; un altro valore non si sa (leapmotor-ha).
    serratura = _intero(d.get("driverDoorLockStatus"))
    s.chiusa = True if serratura == 1 else False if serratura == 0 else None
    s.quadro_acceso = _acceso(d.get("bcmKeyPositionOn3"))
    s.sentinella = _acceso(d.get("sentryMode"))
    antifurto = _intero(d.get("vehicleSecurityActive"))
    s.antifurto = antifurto != 0 if antifurto is not None else None
    s.freno_stazionamento = _acceso(d.get("parkingBrakeState"))
    termico = _intero(d.get("batteryThermalRequest"))
    s.batteria_in_riscaldamento = termico == 4 if termico is not None else None

    in_moto = bool(s.velocita_kmh and s.velocita_kmh > 2) or s.marcia in (Marcia.AVANTI, Marcia.RETROMARCIA)
    _ricarica(d, s, in_moto)

    piano = d.get("_piano")
    if isinstance(piano, dict) and piano.get("isEnable") is not None:
        s.ricarica_programmata = _acceso(piano.get("isEnable"))
        if s.ricarica_programmata:
            s.ricarica_ora_inizio = _ora(piano.get("beginTime"))
            s.ricarica_ora_fine = _ora(piano.get("endTime"))

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
    if d.get("_conSegnali"):
        # Sulle C10 e B10 le percentuali restano a 0 anche aperti: contano i segnali (una B10 dice 2).
        percentuali = []
    else:
        # Sulla T03 è il contrario: i segnali restano a 0, le percentuali sono vere.
        finestrini = []
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

    allarmi = [
        _intero(d.get(k))
        for k in (
            "leftFrontTirePressureState",
            "rightFrontTirePressureState",
            "leftRearTirePressureState",
            "rightRearTirePressureState",
        )
    ]
    if any(a is not None for a in allarmi):
        s.pneumatici_allarme = any(a for a in allarmi if a is not None)

    s.latitudine = _numero(d.get("latitude"))
    s.longitudine = _numero(d.get("longitude"))
    s.posizione_con_segno = bool(d.get("_latitudeConSegno")) and bool(d.get("_longitudeConSegno"))
    if s.latitudine is not None and s.longitudine is not None and not (s.latitudine or s.longitudine):
        # 0,0 è «non lo so», non il Golfo di Guinea.
        s.latitudine = s.longitudine = None
    s.letto = _data(d.get("collectTime"))
    if s.letto is not None and d.get("_conSegnali"):
        s.in_sonno = (datetime.now(UTC) - s.letto).total_seconds() > _VECCHI_DOPO_S
    return s
