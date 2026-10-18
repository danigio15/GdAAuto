"""Cosa vogliono dire i numeri del cloud MG iSMART.

Il cloud dice l'auto in due risposte, tutte e due «chieste all'auto»:

- ``/vehicle/status``: ``basicVehicleStatus`` (porte, serrature, finestrini,
  gomme, temperature, odometro…) e ``gpsPosition``;
- ``/vehicle/charging/mgmtData``: ``chrgMgmtData`` (la batteria vista dal
  BMS: percentuale, tensione, corrente, limite di ricarica, tempo che manca)
  e ``rvsChargeStatus`` (autonomia, energia, pistola collegata).

Quasi tutto arriva intero e scalato: i chilometri in decimi, la batteria in
per mille, la tensione in quarti di volt, la corrente in ventesimi di ampere
con 1000 A di scarto, le gomme in passi da 0,04 bar, le coordinate in
milionesimi di grado. Le scale e i valori «non validi» vengono da
SAIC-iSmart-API/saic-python-mqtt-gateway e saic-python-client-ng (MIT).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura, Vettura

ID = "mg"

#: Il limite di ricarica: il cloud vuole un codice, non una percentuale.
LIMITI = {1: 40, 2: 50, 3: 60, 4: 70, 5: 80, 6: 90, 7: 100}

#: ``bmsChrgSts``: quello che il BMS sta facendo.
_IN_CARICA = {1, 3, 10, 12}
_STATI_RICARICA = {
    0: StatoRicarica.NON_IN_CARICA,  # scollegata
    2: StatoRicarica.COMPLETATA,
    4: StatoRicarica.ERRORE,
    5: StatoRicarica.NON_IN_CARICA,  # si sta collegando
    6: StatoRicarica.NON_IN_CARICA,  # collegata, colonnina non riconosciuta
    7: StatoRicarica.NON_IN_CARICA,  # collegata, non carica
    8: StatoRicarica.NON_IN_CARICA,  # ricarica fermata
    9: StatoRicarica.PROGRAMMATA,
    13: StatoRicarica.NON_IN_CARICA,  # sta dando energia (V2X)
}

_ORE_NON_VALIDE = {0, 2147483647}
_GOMME = {
    "anteriore_sinistro": "frontLeftTyrePressure",
    "anteriore_destro": "frontRightTyrePressure",
    "posteriore_sinistro": "rearLeftTyrePressure",
    "posteriore_destro": "rearRightTyrePressure",
}
_PORTE = ("driverDoor", "passengerDoor", "rearLeftDoor", "rearRightDoor")
_FINESTRINI = ("driverWindow", "passengerWindow", "rearLeftWindow", "rearRightWindow")


def _int(valore: Any) -> int | None:
    if isinstance(valore, bool) or valore is None:
        return None
    try:
        return int(valore)
    except (TypeError, ValueError):
        return None


def _fra(valore: Any, minimo: float, massimo: float) -> int | None:
    """Il numero, se sta in [minimo, massimo); altrimenti «non lo dice»."""
    n = _int(valore)
    return n if n is not None and minimo <= n < massimo else None


def _acceso(valore: Any) -> bool | None:
    n = _int(valore)
    return None if n is None else n > 0


def _qualcuno(d: dict[str, Any], chiavi: tuple[str, ...]) -> bool | None:
    valori = [_acceso(d.get(k)) for k in chiavi]
    noti = [v for v in valori if v is not None]
    return any(noti) if noti else None


def _temperatura(valore: Any) -> float | None:
    # 87 è il «non lo so» del cloud, come -128 e 127.
    n = _fra(valore, -127, 127)
    return None if n is None or n == 87 else float(n)


def _ora(secondi: Any) -> datetime | None:
    n = _int(secondi)
    if n is None or n in _ORE_NON_VALIDE or n < 0:
        return None
    return datetime.fromtimestamp(n, tz=UTC)


def limite_da_codice(codice: Any) -> int | None:
    return LIMITI.get(_int(codice) or 0)


def codice_da_limite(percento: int) -> int:
    """La percentuale più vicina fra quelle che l'auto accetta (40…100, di dieci in dieci)."""
    decine = int(max(40, min(100, percento)) / 10 + 0.5)
    return decine - 3


def interpreta(stato: dict[str, Any] | None, ricarica: dict[str, Any] | None) -> StatoVettura:
    """Le due risposte del cloud in uno ``StatoVettura``. Una delle due può mancare."""
    stato = stato or {}
    ricarica = ricarica or {}
    base = stato.get("basicVehicleStatus") or {}
    gps = stato.get("gpsPosition") or {}
    bms = ricarica.get("chrgMgmtData") or {}
    rvs = ricarica.get("rvsChargeStatus") or {}
    s = StatoVettura(grezzo={"stato": stato, "ricarica": ricarica}, in_sonno=False)

    # La batteria: dal BMS in per mille, se no dallo stato in percento.
    if (soc := _fra(bms.get("bmsPackSOCDsp"), 0, 1001)) is not None:
        s.batteria = round(soc / 10, 1)
    elif (soc := _fra(base.get("extendedData1"), 0, 101)) is not None:
        s.batteria = float(soc)

    # L'autonomia elettrica, in decimi di chilometro.
    for grezza in (rvs.get("fuelRangeElec"), base.get("fuelRangeElec")):
        if (km := _fra(grezza, 1, 20460)) is not None:
            s.autonomia_km = km / 10
            break

    for grezzo in (base.get("mileage"), rvs.get("mileage")):
        if (decimi := _fra(grezzo, 1, 2147483647)) is not None:
            s.odometro_km = decimi / 10
            break

    # La batteria ad alta tensione: tensione e corrente (negativa in carica).
    if (vol := _fra(bms.get("bmsPackVol"), 0, 65536)) is not None:
        s.tensione_v = round(vol * 0.25, 2)
    if _int(bms.get("bmsPackCrntV")) != 1 and (crnt := _fra(bms.get("bmsPackCrnt"), 0, 65536)) is not None:
        s.corrente_a = round(crnt * 0.05 - 1000.0, 3)

    codice_ricarica = _int(bms.get("bmsChrgSts"))
    if codice_ricarica is not None:
        s.stato_ricarica = (
            StatoRicarica.IN_CARICA
            if codice_ricarica in _IN_CARICA
            else _STATI_RICARICA.get(codice_ricarica, StatoRicarica.NON_IN_CARICA)
        )
    pistola = _acceso(rvs.get("chargingGunState"))
    if pistola is not None:
        s.cavo_collegato = pistola
    elif codice_ricarica is not None:
        s.cavo_collegato = codice_ricarica != 0
    if _int(bms.get("chrgngRmnngTimeV")) != 1 and (minuti := _fra(bms.get("chrgngRmnngTime"), 0, 65535)) is not None:
        s.tempo_ricarica_min = minuti
    if (energia := _fra(rvs.get("realtimePower"), 1, 65535)) is not None:
        s.energia_residua_kwh = round(energia / 10, 2)
    s.limite_ricarica = limite_da_codice(bms.get("bmsOnBdChrgTrgtSOCDspCmd"))

    s.temperatura_interna_c = _temperatura(base.get("interiorTemperature"))
    s.temperatura_esterna_c = _temperatura(base.get("exteriorTemperature"))
    # 0 spento, 1 solo ventilazione, 2 acceso, 5 sbrinamento.
    s.clima_acceso = _acceso(base.get("remoteClimateStatus"))

    s.chiusa = _acceso(base.get("lockStatus"))
    s.portiere_aperte = _qualcuno(base, _PORTE)
    s.bagagliaio_aperto = _acceso(base.get("bootStatus"))
    s.finestrini_aperti = _qualcuno(base, _FINESTRINI)
    for ruota, chiave in _GOMME.items():
        if (passi := _fra(base.get(chiave), 1, 255)) is not None:
            s.pneumatici_kpa[ruota] = round(passi * 4.0, 1)  # 0,04 bar = 4 kPa

    motore = _int(base.get("engineStatus"))
    if motore is not None:
        s.quadro_acceso = motore == 1
    in_sosta = motore != 1 or _int(base.get("handBrake")) == 1

    # La posizione, solo con un fix GPS (2D o 3D).
    punto = gps.get("wayPoint") or {}
    if _int(gps.get("gpsStatus")) in (2, 3):
        posizione = punto.get("position") or {}
        lat, lon = _int(posizione.get("latitude")), _int(posizione.get("longitude"))
        if lat is not None and lon is not None and abs(lat) <= 90_000_000 and abs(lon) <= 180_000_000:
            s.latitudine, s.longitudine = lat / 1_000_000, lon / 1_000_000
            s.posizione_con_segno = True
        if (velocita := _fra(punto.get("speed"), 0, 65535)) is not None:
            s.velocita_kmh = 0.0 if in_sosta else velocita / 10
    elif motore is not None and in_sosta:
        s.velocita_kmh = 0.0

    s.letto = _ora(stato.get("statusTime")) or _ora(gps.get("timeStamp"))
    return s


def _configurazione(voce: dict[str, Any]) -> dict[str, str]:
    config: dict[str, str] = {}
    for c in voce.get("vehicleModelConfiguration") or []:
        if isinstance(c, dict) and c.get("itemCode") is not None:
            config[str(c["itemCode"]).strip().upper()] = str(c.get("itemValue") or "").strip().upper()
    return config


def vettura_da_json(voce: dict[str, Any]) -> Vettura:
    """Un'auto dell'elenco dell'account (``vinList``)."""
    modello = str(voce.get("modelName") or voce.get("series") or "MG").strip()
    nome = str(voce.get("name") or "").strip()
    if not nome:
        nome = modello if modello.upper().startswith("MG") else f"MG {modello}"
    anno = str(voce.get("modelYear") or "").strip()
    config = _configurazione(voce)
    return Vettura(
        vin=str(voce["vin"]),
        marca=ID,
        modello=modello,
        nome=nome,
        anno=int(anno) if anno.isdigit() else None,
        colore=str(voce.get("colorName") or ""),
        condivisa=bool(voce.get("isSubaccount")),
        extra={
            "serie": str(voce.get("series") or "").strip().upper(),
            "configurazione": config,
        },
    )


def regge_il_limite(v: Vettura) -> bool:
    """Il limite di ricarica da remoto c'è con ``BType`` = 1 (le batterie NMC).

    Senza la configurazione non si sa: lo si offre, e decide il cloud."""
    config = v.extra.get("configurazione") or {}
    return "BTYPE" not in config or config["BTYPE"] == "1"


def indice_temperatura(v: Vettura, gradi: int) -> int:
    """La temperatura del clima come la vuole il cloud: un indice, non i gradi."""
    serie = str(v.extra.get("serie") or "")
    if serie.startswith("EH32"):  # MG4: da 17 a 31 gradi
        return 3 + max(17, min(31, gradi)) - 17
    massimo = 31 if serie.startswith("MZS3E") else 28
    return 2 + max(16, min(massimo, gradi)) - 16
