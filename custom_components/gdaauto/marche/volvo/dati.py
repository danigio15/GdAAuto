"""Le risposte delle API Volvo Cars, tradotte in ``StatoVettura``.

Ogni API del portale Volvo risponde a modo suo, ma i campi sono quasi tutti
fatti allo stesso modo: ``{"value": …, "unit": …, "timestamp": …}``. L'Energy
API v2 aggiunge ``status`` (``OK`` o ``ERROR``) e usa ``updatedAt``; un campo
in ``ERROR`` vuol dire «questa auto non lo dice» e qui diventa ``None``.

``interpreta`` riceve un dizionario con quello che si è letto, una voce per
risorsa (``doors``, ``windows``, ``energia``, ``statistics``…), già tolta
dall'involucro ``data`` della risposta. Una risorsa che manca lascia ``None``
i suoi campi: niente si inventa.

La mappa dei campi e dei valori (porte ``OPEN``/``AJAR``, gomme
``LOW_PRESSURE``, ricarica ``CHARGING``/``DONE``…) segue l'integrazione
ufficiale ``volvo`` di Home Assistant (Apache 2.0) e la documentazione
pubblica del portale Volvo: vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...vettura import StatoRicarica, StatoVettura, Vettura

#: I valori che vogliono dire «aperto» per porte, cofano, finestrini e tetto.
APERTO = ("OPEN", "AJAR")
#: Le gomme in allarme.
GOMME_IN_ALLARME = ("VERY_LOW_PRESSURE", "LOW_PRESSURE", "HIGH_PRESSURE")
#: Quando un avviso dice che qualcosa non va (luci, liquidi, olio, tagliando).
_GUASTI = ("FAILURE", "TOO_LOW", "TOO_HIGH", "SERVICE_REQUIRED")
#: Il valore che il cloud usa per «non lo so».
NON_SPECIFICATO = "UNSPECIFIED"

_MIGLIO_KM = 1.609344

#: Energy API v2: lo stato della ricarica.
_RICARICA_V2 = {
    "CHARGING": StatoRicarica.IN_CARICA,
    "DONE": StatoRicarica.COMPLETATA,
    "ERROR": StatoRicarica.ERRORE,
    "SCHEDULED": StatoRicarica.PROGRAMMATA,
    "IDLE": StatoRicarica.NON_IN_CARICA,
    # L'auto dà corrente fuori (EX90 bidirezionale): non sta caricando.
    "DISCHARGING": StatoRicarica.NON_IN_CARICA,
}

#: Energy API v1 (``recharge-status``, in dismissione): lo stesso, coi nomi vecchi.
_RICARICA_V1 = {
    "CHARGING_SYSTEM_CHARGING": StatoRicarica.IN_CARICA,
    "CHARGING_SYSTEM_DONE": StatoRicarica.COMPLETATA,
    "CHARGING_SYSTEM_FAULT": StatoRicarica.ERRORE,
    "CHARGING_SYSTEM_SCHEDULED": StatoRicarica.PROGRAMMATA,
    "CHARGING_SYSTEM_IDLE": StatoRicarica.NON_IN_CARICA,
}

#: I tipi di motore che non hanno una batteria di trazione da leggere.
SOLO_CARBURANTE = ("DIESEL", "PETROL")
#: E quelli col serbatoio (si legge anche la Fuel API).
COL_SERBATOIO = ("DIESEL", "PETROL", "PETROL/ELECTRIC")


# --- i campi -----------------------------------------------------------------------


def campo(risorsa: dict[str, Any] | None, nome: str) -> dict[str, Any] | None:
    """Il campo, se c'è e non è in errore."""
    if not isinstance(risorsa, dict):
        return None
    c = risorsa.get(nome)
    if not isinstance(c, dict) or str(c.get("status") or "OK").upper() == "ERROR":
        return None
    return c


def valore(risorsa: dict[str, Any] | None, nome: str) -> Any:
    c = campo(risorsa, nome)
    if c is None:
        return None
    v = c.get("value")
    if isinstance(v, str) and v.strip().upper() in (NON_SPECIFICATO, "", "NULL"):
        return None
    return v


def _testo(risorsa: dict[str, Any] | None, nome: str) -> str | None:
    v = valore(risorsa, nome)
    return str(v).strip().upper() if v is not None else None


def _numero(v: Any) -> float | None:
    """Il cloud manda i numeri a volte come numeri, a volte come stringhe («58.0»)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def numero(risorsa: dict[str, Any] | None, nome: str) -> float | None:
    return _numero(valore(risorsa, nome))


def km(risorsa: dict[str, Any] | None, nome: str) -> float | None:
    """Una distanza in km, anche se l'auto è impostata in miglia (``mi``, ``miles``)."""
    c = campo(risorsa, nome)
    n = numero(risorsa, nome)
    if c is None or n is None:
        return None
    unita = str(c.get("unit") or "km").strip().lower()
    if unita in ("mi", "mile", "miles"):
        return round(n * _MIGLIO_KM, 1)
    return round(n, 1)


def _intero(n: float | None) -> int | None:
    return round(n) if n is not None else None


def _quando(c: Any) -> datetime | None:
    if not isinstance(c, dict):
        return None
    testo = c.get("updatedAt") or c.get("timestamp")
    if not isinstance(testo, str) or not testo:
        return None
    try:
        return datetime.fromisoformat(testo)
    except ValueError:
        return None


def _qualcuno(valori: list[str | None], si: tuple[str, ...]) -> bool | None:
    """Vero se uno è fra ``si``; falso se tutti quelli noti non lo sono; ``None`` se nessuno è noto."""
    noti = [v for v in valori if v is not None]
    if not noti:
        return None
    return any(v in si for v in noti)


# --- l'auto ---------------------------------------------------------------------------


def ha_batteria(carburante: str | None) -> bool:
    """Elettriche, ibride plug-in, e le EX30 che dicono «NONE»."""
    return str(carburante or "").upper() not in SOLO_CARBURANTE


def ha_serbatoio(carburante: str | None) -> bool:
    return str(carburante or "").upper() in COL_SERBATOIO


def vettura_da(dettagli: dict[str, Any], marca: str, comandi: dict[str, str] | None = None) -> Vettura:
    """L'anagrafica, da ``GET /connected-vehicle/v2/vehicles/{vin}``."""
    descrizioni = dettagli.get("descriptions") or dettagli.get("description") or {}
    modello = str(descrizioni.get("model") or "").strip() or "Volvo"
    anno = dettagli.get("modelYear")
    colore = dettagli.get("externalColour")
    capacita = _numero(dettagli.get("batteryCapacityKWH"))
    return Vettura(
        vin=str(dettagli["vin"]),
        marca=marca,
        modello=modello,
        nome=f"Volvo {modello}" if modello != "Volvo" else "Volvo",
        anno=int(anno) if isinstance(anno, int | float) or str(anno or "").isdigit() else None,
        colore=str(colore) if colore and str(colore).lower() != "null" else "",
        extra={
            "carburante": str(dettagli.get("fuelType") or "").upper() or None,
            "cambio": dettagli.get("gearbox"),
            "capacita_kwh": capacita if capacita and capacita > 0 else None,
            "comandi": dict(comandi or {}),
        },
    )


def comandi_da(risposta: dict[str, Any]) -> dict[str, str]:
    """I comandi che l'auto accetta, ``{"LOCK": "lock", "HONK_AND_FLASH": "honk-flash", …}``.

    Il pezzo finale dell'indirizzo viene da ``href``; se manca si ricava dal nome.
    """
    comandi: dict[str, str] = {}
    for voce in risposta.get("data") or []:
        if not isinstance(voce, dict) or not voce.get("command"):
            continue
        nome = str(voce["command"]).upper()
        href = str(voce.get("href") or "").rstrip("/")
        pezzo = (
            href.rsplit("/", 1)[-1] if "/commands/" in href else nome.lower().replace("_and_", "-").replace("_", "-")
        )
        comandi[nome] = pezzo
    return comandi


# --- lo stato -----------------------------------------------------------------------


def interpreta(letture: dict[str, Any], vettura: Vettura | None = None) -> StatoVettura:
    """Da quello che si è letto (una voce per risorsa) allo stato di GdAAuto."""
    s = StatoVettura()
    porte = letture.get("doors")
    finestrini = letture.get("windows")
    energia = letture.get("energia")
    v1 = letture.get("ricarica_v1")
    statistiche = letture.get("statistics")
    carburante = letture.get("fuel")
    gomme = letture.get("tyres")

    # --- batteria e autonomia
    s.batteria = numero(energia, "batteryChargeLevel")
    if s.batteria is None:
        s.batteria = numero(v1, "batteryChargeLevel")
    if s.batteria is None:
        # Le ibride plug-in senza Energy API dicono la carica nella Fuel API.
        s.batteria = numero(carburante, "batteryChargeLevel")
    # Come l'app (e l'integrazione di Home Assistant): l'autonomia è quella delle statistiche,
    # sempre in km; quella dell'Energy API è il ripiego.
    s.autonomia_km = km(statistiche, "distanceToEmptyBattery")
    if s.autonomia_km is None:
        s.autonomia_km = km(energia, "electricRange")
    if s.autonomia_km is None:
        s.autonomia_km = km(v1, "electricRange")
    s.autonomia_carburante_km = km(statistiche, "distanceToEmptyTank")
    if s.autonomia_carburante_km is not None and s.autonomia_km is not None:
        s.autonomia_totale_km = round(s.autonomia_km + s.autonomia_carburante_km, 1)
    s.consumo_medio_kwh_100km = numero(statistiche, "averageEnergyConsumption")
    s.odometro_km = km(letture.get("odometer"), "odometer")
    if vettura is not None:
        s.capacita_batteria_kwh = vettura.extra.get("capacita_kwh")

    # --- la ricarica
    _ricarica(s, energia, v1)

    # --- porte, serratura, finestrini
    serratura = _testo(porte, "centralLock")
    s.chiusa = {"LOCKED": True, "UNLOCKED": False}.get(serratura or "")
    s.portiere_aperte = _qualcuno(
        [_testo(porte, n) for n in ("frontLeftDoor", "frontRightDoor", "rearLeftDoor", "rearRightDoor")], APERTO
    )
    portellone = _testo(porte, "tailgate")
    s.bagagliaio_aperto = None if portellone is None else portellone in APERTO
    s.finestrini_aperti = _qualcuno(
        [
            _testo(finestrini, n)
            for n in ("frontLeftWindow", "frontRightWindow", "rearLeftWindow", "rearRightWindow", "sunroof")
        ],
        APERTO,
    )

    # --- motore, gomme, connessione
    motore = _testo(letture.get("engine-status"), "engineStatus")
    s.quadro_acceso = None if motore is None else motore == "RUNNING"
    s.pneumatici_allarme = _qualcuno(
        [_testo(gomme, n) for n in ("frontLeft", "frontRight", "rearLeft", "rearRight")], GOMME_IN_ALLARME
    )
    s.in_sonno = _in_sonno(letture.get("command-accessibility"))

    # --- la posizione (GeoJSON: [longitudine, latitudine, altezza], col segno)
    _posizione(s, letture.get("location"))

    s.letto = _piu_recente(letture)
    s.grezzo = grezzo(letture)
    return s


def _ricarica(s: StatoVettura, energia: dict[str, Any] | None, v1: dict[str, Any] | None) -> None:
    stato = _testo(energia, "chargingStatus")
    if stato is not None:
        s.stato_ricarica = _RICARICA_V2.get(stato)
        s.v2l_attivo = True if stato == "DISCHARGING" else None
    else:
        vecchio = _testo(v1, "chargingSystemStatus")
        if vecchio is not None:
            s.stato_ricarica = _RICARICA_V1.get(vecchio)

    cavo = _testo(energia, "chargerConnectionStatus")
    if cavo is not None:
        s.cavo_collegato = {"CONNECTED": True, "DISCONNECTED": False}.get(cavo)
    else:
        vecchio = _testo(v1, "chargingConnectionStatus")
        if vecchio is not None and vecchio.endswith("DISCONNECTED"):
            s.cavo_collegato = False
        elif vecchio is not None and vecchio.endswith(("CONNECTED_AC", "CONNECTED_DC")):
            s.cavo_collegato = True
            s.ricarica_rapida = vecchio.endswith("_DC")

    tipo = _testo(energia, "chargingType")
    if tipo is not None:
        s.ricarica_rapida = tipo == "DC"

    in_carica = s.stato_ricarica == StatoRicarica.IN_CARICA
    minuti = numero(energia, "estimatedChargingTimeToTargetBatteryChargeLevel")
    if minuti is None:
        minuti = numero(v1, "estimatedChargingTime")
    # Il cloud dice dei minuti anche a carica finita: contano solo in carica.
    s.tempo_ricarica_min = _intero(minuti) if in_carica and minuti is not None else None

    limite = numero(energia, "targetBatteryChargeLevel")
    s.limite_ricarica = _intero(limite)

    watt = numero(energia, "chargingPower")
    if watt is not None:
        s.potenza_ricarica_diretta_kw = round(max(watt, 0.0) / 1000, 3)
    elif s.stato_ricarica is not None and not in_carica:
        s.potenza_ricarica_diretta_kw = 0.0


def _in_sonno(accessibilita: dict[str, Any] | None) -> bool | None:
    """``UNAVAILABLE`` per ``POWER_SAVING_MODE`` è l'auto che dorme."""
    c = campo(accessibilita, "availabilityStatus")
    if c is None:
        return None
    stato = str(c.get("value") or "").upper()
    motivo = str(c.get("unavailableReason") or c.get("unavailable_reason") or "").upper()
    if stato == "AVAILABLE":
        return False
    if motivo == "POWER_SAVING_MODE":
        return True
    return False if stato else None


def _posizione(s: StatoVettura, posizione: dict[str, Any] | None) -> None:
    if not isinstance(posizione, dict):
        return
    coordinate = (posizione.get("geometry") or {}).get("coordinates") or []
    if len(coordinate) < 2:
        return
    lon, lat = _numero(coordinate[0]), _numero(coordinate[1])
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return
    s.latitudine, s.longitudine = lat, lon
    s.posizione_con_segno = True


def _piu_recente(letture: dict[str, Any]) -> datetime | None:
    """Il dato più fresco fra quelli che l'auto ha mandato."""
    quando: list[datetime] = []
    for nome, risorsa in letture.items():
        if not isinstance(risorsa, dict):
            continue
        if nome == "location":
            q = _quando(risorsa.get("properties"))
            if q:
                quando.append(q)
            continue
        for c in risorsa.values():
            if (q := _quando(c)) and (not isinstance(c, dict) or str(c.get("status") or "OK").upper() != "ERROR"):
                quando.append(q)
    return max(quando) if quando else None


def avvisi(letture: dict[str, Any]) -> list[str]:
    """Gli avvisi accesi: luci guaste, liquidi bassi, olio, tagliando."""
    accesi: list[str] = []
    for nome in ("warnings", "brakes", "engine", "diagnostics"):
        risorsa = letture.get(nome)
        if not isinstance(risorsa, dict):
            continue
        for chiave in risorsa:
            v = _testo(risorsa, chiave)
            if v and (v in _GUASTI or v.endswith("_FOR_SERVICE") or v == "UNKNOWN_WARNING"):
                accesi.append(chiave)
    return sorted(accesi)


def grezzo(letture: dict[str, Any]) -> dict[str, Any]:
    """Quello che il cloud ha detto, per la diagnostica: «risorsa.campo» → valore (e unità).

    La posizione resta fuori: nella diagnostica non deve finire dov'è l'auto.
    """
    piatto: dict[str, Any] = {}
    for nome, risorsa in sorted(letture.items()):
        if not isinstance(risorsa, dict):
            continue
        if nome == "location":
            direzione = (risorsa.get("properties") or {}).get("heading")
            if direzione is not None:
                piatto["location.heading"] = direzione
            continue
        for chiave, c in risorsa.items():
            if not isinstance(c, dict):
                continue
            if str(c.get("status") or "OK").upper() == "ERROR":
                continue
            v = c.get("value")
            unita = c.get("unit")
            piatto[f"{nome}.{chiave}"] = f"{v} {unita}" if unita and v is not None else v
            if c.get("unavailableReason"):
                piatto[f"{nome}.{chiave}.motivo"] = c["unavailableReason"]
    accesi = avvisi(letture)
    if accesi or any(n in letture for n in ("warnings", "brakes", "engine", "diagnostics")):
        piatto["avvisi"] = accesi
    return piatto
