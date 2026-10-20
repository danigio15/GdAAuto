"""Lo stato di una Škoda, dalle risposte del cloud MyŠkoda.

Il cloud non ha una risposta sola: ogni parte dell'auto ha la sua, e GdAAuto
le mette insieme per nome («sezioni»):

- ``ricarica`` (``/v1/charging/<vin>``): carica, autonomia in metri, stato
  della ricarica, potenza, minuti alla fine, limite e modo di ricarica;
- ``stato`` (``/v2/vehicle-status/<vin>``): serratura, porte, finestrini,
  bagagliaio, cofano, tetto apribile, luci;
- ``autonomia`` (``/v2/vehicle-status/<vin>/driving-range``): l'autonomia di
  ogni motore (le plug-in hanno benzina ed elettrico) e quella totale;
- ``clima`` (``/v2/air-conditioning/<vin>``): clima, temperatura voluta,
  temperatura fuori, sbrinamento e cavo collegato;
- ``connessione`` (``/v2/connection-status/<vin>/readiness``): l'auto
  raggiungibile, in movimento, col quadro acceso;
- ``posizione`` (``/v1/maps/positions``): dove è parcheggiata (mentre va, niente);
- ``manutenzione`` (``/v3/vehicle-maintenance/vehicles/<vin>``): il
  contachilometri e il tagliando;
- ``salute`` (``/v1/vehicle-health-report/warning-lights/<vin>``): le spie,
  fra cui quella delle gomme.

Le unità sono nel nome dei campi (``InPercent``, ``InKm``, ``InMeters``,
``InKw``, ``InMinutes``); le temperature dicono la loro unità. Le gomme non ci
sono: il cloud non dà la pressione, solo la spia.

I nomi dei campi e il significato dei valori vengono da skodaconnect/myskoda
(``models/``) e skodaconnect/homeassistant-myskoda, licenza MIT: vedi
RINGRAZIAMENTI.md. Le regole sono scritte da capo.
"""

from __future__ import annotations

import base64
import csv
from datetime import UTC, datetime, timedelta
import io
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ...vettura import StatoRicarica, StatoVettura

#: ``state`` della ricarica → lo stato di GdAAuto (``READY_FOR_CHARGING`` si decide a parte).
_STATI_RICARICA = {
    "CHARGING": StatoRicarica.IN_CARICA,
    "CONSERVING": StatoRicarica.COMPLETATA,
    "CHARGING_INTERRUPTED": StatoRicarica.IN_PAUSA,
    "CONNECT_CABLE": StatoRicarica.NON_IN_CARICA,
    "DISCHARGING": StatoRicarica.NON_IN_CARICA,
    "ERROR": StatoRicarica.ERRORE,
}
#: I modi di ricarica che aspettano un orario.
_MODI_PROGRAMMATI = {"TIMER", "PREFERRED_CHARGING_TIMES", "TIMER_CHARGING_WITH_CLIMATISATION"}
#: Gli stati del clima che vogliono dire «acceso».
_CLIMA_ACCESO = {"HEATING", "COOLING", "VENTILATION", "ON", "HEATING_AUXILIARY"}
#: I motori a carburante, nell'autonomia.
_CARBURANTI = {"gasoline", "diesel", "petrol", "cng", "lpg"}
#: Il contachilometri a volte arriva come 429 496 729 (un intero a 32 bit tagliato): non è vero.
_KM_ASSURDI = 400_000_000


def valore(d: Any, percorso: str) -> Any:
    """``valore(d, "status.battery.stateOfChargeInPercent")``; ``None`` se manca un pezzo."""
    for chiave in percorso.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(chiave)
    return d


def numero(x: Any) -> float | None:
    if isinstance(x, bool) or x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _primo(*valori: Any) -> float | None:
    """Il primo numero fra quelli dati (lo zero conta)."""
    return next((n for v in valori if (n := numero(v)) is not None), None)


def _maiuscolo(x: Any) -> str:
    return str(x or "").strip().upper()


def data_cloud(testo: Any) -> datetime | None:
    """``2025-04-25T08:46:14.592Z``, ``2025-04-25T08:46:12Z`` o coi nanosecondi; sempre con il fuso."""
    if not isinstance(testo, str) or not testo:
        return None
    pulito = re.sub(r"(\.\d{6})\d+", r"\1", testo.strip())
    try:
        quando = datetime.fromisoformat(pulito)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def _celsius(temperatura: Any) -> float | None:
    """``{"temperatureValue": 10.5, "temperatureUnit": "CELSIUS"}`` (o ``unitInCar``), in °C."""
    if not isinstance(temperatura, dict):
        return None
    gradi = numero(temperatura.get("temperatureValue"))
    if gradi is None:
        gradi = numero(temperatura.get("celsius"))
        return round(gradi, 1) if gradi is not None else None
    unita = _maiuscolo(temperatura.get("temperatureUnit") or temperatura.get("unitInCar") or "CELSIUS")
    if unita == "FAHRENHEIT":
        gradi = (gradi - 32) * 5 / 9
    elif unita == "KELVIN":
        gradi -= 273.15
    return round(gradi, 1)


def _aperto(*stati: Any) -> bool | None:
    """``OPEN``/``CLOSED``: uno aperto è ``True``; tutti chiusi ``False``; nessuno che lo dica ``None``."""
    noti = [_maiuscolo(s) for s in stati if _maiuscolo(s) in ("OPEN", "CLOSED")]
    if not noti:
        return None
    return "OPEN" in noti


def _porte_dal_disegno(stato: dict[str, Any]) -> bool | None:
    """Le quattro portiere, dall'indirizzo del disegno dell'auto (``vehicleState=1-1-3-2-…``).

    Le prime quattro cifre sono le portiere (sinistra e destra davanti, poi
    dietro): 1 chiusa, 2 finestrino aperto, 3 portiera aperta, 4 tutte e due.
    Il riassunto ``overall.doors`` conta anche il bagagliaio; il disegno no.
    """
    indirizzo = valore(stato, "renders.lightMode.oneX")
    if not isinstance(indirizzo, str):
        return None
    try:
        cifre = [int(x) for x in parse_qs(urlsplit(indirizzo).query)["vehicleState"][0].split("-")[:4]]
    except (KeyError, IndexError, ValueError):
        return None
    if len(cifre) < 4 or any(c not in (1, 2, 3, 4) for c in cifre):
        return None
    return any(c in (3, 4) for c in cifre)


def _motori(autonomia: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Dall'autonomia: il motore elettrico e quello a carburante (vuoti se non ci sono)."""
    elettrico: dict[str, Any] = {}
    carburante: dict[str, Any] = {}
    for nome in ("primaryEngineRange", "secondaryEngineRange"):
        motore = autonomia.get(nome)
        if not isinstance(motore, dict):
            continue
        tipo = str(motore.get("engineType") or "").lower()
        if tipo == "electric" and not elettrico:
            elettrico = motore
        elif tipo in _CARBURANTI and not carburante:
            carburante = motore
    return elettrico, carburante


def _letto(sezioni: dict[str, Any]) -> datetime | None:
    """Il momento più recente in cui l'auto ha mandato qualcosa (``carCapturedTimestamp``)."""
    date = [
        quando
        for nome in ("ricarica", "stato", "autonomia", "clima")
        if (quando := data_cloud(valore(sezioni.get(nome), "carCapturedTimestamp")))
    ]
    return max(date) if date else None


def km_validi(x: Any) -> float | None:
    km = numero(x)
    return km if km is not None and 0 <= km < _KM_ASSURDI else None


def interpreta(sezioni: dict[str, Any], capacita_kwh: float | None = None) -> StatoVettura:
    """Le sezioni lette (quelle che mancano sono ``None``) diventano lo stato dell'auto."""
    ricarica = sezioni.get("ricarica") or {}
    stato = sezioni.get("stato") or {}
    autonomia = sezioni.get("autonomia") or {}
    clima = sezioni.get("clima") or {}
    connessione = sezioni.get("connessione") or {}
    s = StatoVettura(grezzo=grezzo(sezioni))
    elettrico, carburante = _motori(autonomia)

    # --- la batteria e l'autonomia ---------------------------------------------------------
    batteria = valore(ricarica, "status.battery") or {}
    s.batteria = _primo(batteria.get("stateOfChargeInPercent"), elettrico.get("currentSoCInPercent"))
    if s.batteria is not None:
        s.batteria = min(s.batteria, 100.0)
    metri = numero(batteria.get("remainingCruisingRangeInMeters"))
    s.autonomia_km = _primo(elettrico.get("remainingRangeInKm"), metri / 1000 if metri is not None else None)
    if carburante or str(autonomia.get("carType") or "").lower() == "hybrid":
        s.carburante_percento = _primo(carburante.get("currentFuelLevelInPercent"))
        s.autonomia_carburante_km = _primo(carburante.get("remainingRangeInKm"))
        s.autonomia_totale_km = _primo(autonomia.get("totalRangeInKm"))
    s.capacita_batteria_kwh = capacita_kwh

    # --- la ricarica ----------------------------------------------------------------------
    regole = ricarica.get("settings") or {}
    if (limite := numero(regole.get("targetStateOfChargeInPercent"))) is not None:
        s.limite_ricarica = int(limite)
    modo = _maiuscolo(regole.get("preferredChargeMode"))
    if modo:
        s.ricarica_programmata = modo in _MODI_PROGRAMMATI

    stato_ricarica = _maiuscolo(valore(ricarica, "status.state"))
    if stato_ricarica == "READY_FOR_CHARGING":
        # Collegata e ferma: o ha finito, o aspetta l'orario, o aspetta e basta.
        if s.batteria is not None and s.limite_ricarica is not None and s.batteria >= s.limite_ricarica:
            s.stato_ricarica = StatoRicarica.COMPLETATA
        elif s.ricarica_programmata:
            s.stato_ricarica = StatoRicarica.PROGRAMMATA
        else:
            s.stato_ricarica = StatoRicarica.NON_IN_CARICA
    else:
        s.stato_ricarica = _STATI_RICARICA.get(stato_ricarica)
    # Il cloud non ha un servizio V2L: lo si dice solo se l'auto si dichiara «in scarica».
    if stato_ricarica == "DISCHARGING":
        s.v2l_attivo = True
    in_carica = s.stato_ricarica == StatoRicarica.IN_CARICA
    tipo = _maiuscolo(valore(ricarica, "status.chargeType"))
    if in_carica and tipo in ("AC", "DC"):
        s.ricarica_rapida = tipo == "DC"
    potenza = numero(valore(ricarica, "status.chargePowerInKw"))
    if potenza is not None:
        s.potenza_ricarica_diretta_kw = potenza if in_carica else 0.0
    minuti = numero(valore(ricarica, "status.remainingTimeToFullyChargedInMinutes"))
    if minuti is not None and in_carica:
        s.tempo_ricarica_min = int(minuti)

    # Il cavo lo dice il clima; lo stato «CONNECT_CABLE» della ricarica non basta (resta anche a cavo attaccato).
    cavo = _maiuscolo(clima.get("chargerConnectionState"))
    if cavo in ("CONNECTED", "DISCONNECTED"):
        s.cavo_collegato = cavo == "CONNECTED"
    elif stato_ricarica in ("CHARGING", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED"):
        s.cavo_collegato = True

    # --- il clima ---------------------------------------------------------------------------
    stato_clima = _maiuscolo(clima.get("state"))
    if stato_clima and stato_clima != "INVALID":
        s.clima_acceso = stato_clima in _CLIMA_ACCESO
    s.temperatura_clima_c = _celsius(clima.get("targetTemperature"))
    s.temperatura_esterna_c = _celsius(clima.get("outsideTemperature"))
    lunotti = clima.get("windowHeatingState")
    if isinstance(lunotti, dict):
        stati = [_maiuscolo(lunotti.get(k)) for k in ("front", "rear")]
        if any(x in ("ON", "OFF") for x in stati):
            s.sbrinamento = "ON" in stati

    # --- porte, finestrini, serratura ------------------------------------------------------------
    insieme = stato.get("overall") or {}
    dettagli = stato.get("detail") or {}
    serratura = _maiuscolo(insieme.get("doorsLocked") or insieme.get("locked"))
    if serratura in ("YES", "NO", "OPENED", "TRUNK_OPENED"):
        s.chiusa = serratura == "YES"
    portiere = _porte_dal_disegno(stato)
    s.portiere_aperte = portiere if portiere is not None else _aperto(insieme.get("doors"))
    s.bagagliaio_aperto = _aperto(dettagli.get("trunk"))
    s.finestrini_aperti = _aperto(insieme.get("windows"), dettagli.get("sunroof"))

    # --- l'auto sveglia, in moto, addormentata ---------------------------------------------------
    if isinstance(connessione.get("ignitionOn"), bool):
        s.quadro_acceso = connessione["ignitionOn"]
    if connessione.get("inMotion") is True and s.quadro_acceso is None:
        s.quadro_acceso = True
    if isinstance(connessione.get("unreachable"), bool):
        s.in_sonno = connessione["unreachable"]

    # --- il contachilometri e le spie -------------------------------------------------------------
    s.odometro_km = _primo(
        km_validi(valore(sezioni.get("manutenzione"), "maintenanceReport.mileageInKm")),
        km_validi(valore(sezioni.get("salute"), "mileageInKm")),
    )
    spie = valore(sezioni.get("salute"), "warningLights")
    if isinstance(spie, list):
        gomme = [w for w in spie if isinstance(w, dict) and _maiuscolo(w.get("category")) == "TIRE"]
        if gomme:
            s.pneumatici_allarme = any(w.get("defects") for w in gomme)

    s.letto = _letto(sezioni)
    return s


def grezzo(sezioni: dict[str, Any]) -> dict[str, Any]:
    """Quello che il cloud ha detto, per la diagnostica: senza indirizzi, disegni e contatti."""
    tenute: dict[str, Any] = {}
    for nome in ("ricarica", "stato", "autonomia", "clima", "connessione", "salute"):
        if isinstance(sezioni.get(nome), dict):
            tenute[nome] = {k: v for k, v in sezioni[nome].items() if k != "renders"}
    # Del tagliando solo il resoconto: il resto sono l'officina e i contatti di chi guida.
    if isinstance(rapporto := valore(sezioni.get("manutenzione"), "maintenanceReport"), dict):
        tenute["tagliando"] = rapporto
    return tenute


def posizione(risposta: Any) -> tuple[float, float] | None:
    """``{"positions": [{"type": "VEHICLE", "gpsCoordinates": {…}}], "errors": […]}``; mentre va, niente."""
    voci = risposta.get("positions") if isinstance(risposta, dict) else None
    for voce in voci if isinstance(voci, list) else []:
        if not isinstance(voce, dict) or _maiuscolo(voce.get("type")) != "VEHICLE":
            continue
        punto = voce.get("gpsCoordinates") or {}
        lat, lon = numero(punto.get("latitude")), numero(punto.get("longitude"))
        if lat is not None and lon is not None and not (lat == 0 and lon == 0):
            return lat, lon
    return None


def consumo_medio(risposta: Any) -> float | None:
    """Il consumo medio elettrico della settimana (``trip-statistics``), in kWh/100 km."""
    consumo = numero(valore(risposta, "overallAverageElectricConsumption"))
    return round(consumo, 1) if consumo is not None and 0 < consumo < 100 else None


def ultimi_viaggi(risposta: Any) -> tuple[float | None, float | None]:
    """I km e l'energia dei viaggi (``single-trips``) degli ultimi sette giorni."""
    giorni = risposta.get("dailyTrips") if isinstance(risposta, dict) else None
    if not isinstance(giorni, list):
        return None, None
    km = 0.0
    energia: float | None = None
    for giorno in giorni:
        for viaggio in (giorno.get("trips") or []) if isinstance(giorno, dict) else []:
            if not isinstance(viaggio, dict):
                continue
            km += numero(viaggio.get("mileageInKm")) or 0.0
            if (kwh := numero(viaggio.get("electricConsumption"))) is not None:
                energia = (energia or 0.0) + kwh
    if energia is None and km == 0:
        energia = 0.0
    return round(km, 1), round(energia, 2) if energia is not None else None


def _kwh(testo: Any) -> float | None:
    """``"~ 17 kWh"``, ``"17,5 kWh"`` → 17.0, 17.5."""
    trovato = re.search(r"(\d+(?:[.,]\d+)?)\s*kwh", str(testo or ""), re.IGNORECASE)
    return float(trovato.group(1).replace(",", ".")) if trovato else None


def _orari_dal_csv(csv_b64: Any) -> dict[str, tuple[datetime | None, datetime | None]]:
    """Il CSV (in base64) delle statistiche: ``"Session ID","Started on","Ended on"`` in UTC."""
    if not isinstance(csv_b64, str) or not csv_b64:
        return {}
    try:
        testo = base64.b64decode(csv_b64).decode("utf-8-sig", errors="replace")
    except ValueError:
        return {}
    orari: dict[str, tuple[datetime | None, datetime | None]] = {}
    for riga in csv.DictReader(io.StringIO(testo)):
        if sessione := str(riga.get("Session ID") or "").strip():
            orari[sessione] = (data_cloud(riga.get("Started on")), data_cloud(riga.get("Ended on")))
    return orari


def ultima_ricarica(risposta: Any) -> dict[str, Any]:
    """L'ultima ricarica finita, dalle statistiche di ricarica (``charging_statistics`` di CARIAD)."""
    sezioni = risposta.get("monthSections") if isinstance(risposta, dict) else None
    if not isinstance(sezioni, list):
        return {}
    orari = _orari_dal_csv(risposta.get("csvFile"))
    candidate: list[tuple[datetime | None, dict[str, Any]]] = []
    for sezione in sezioni:
        for voce in (sezione.get("entries") or []) if isinstance(sezione, dict) else []:
            dettagli = voce.get("details") if isinstance(voce, dict) else None
            if not isinstance(dettagli, dict) or dettagli.get("isActiveSession"):
                continue
            inizio, fine = orari.get(str(dettagli.get("sessionId") or ""), (None, None))
            campi: dict[str, Any] = {}
            if (kwh := _kwh(dettagli.get("formattedTotalEnergy") or voce.get("primaryValue"))) is not None:
                campi["ultima_ricarica_kwh"] = kwh
            if (tipo := str(dettagli.get("chargingPowerType") or "").lower()) in ("ac", "dc"):
                campi["ultima_ricarica_tipo"] = tipo
            if inizio:
                campi["ultima_ricarica_inizio"] = inizio
            if fine:
                campi["ultima_ricarica_fine"] = fine
            if campi:
                candidate.append((inizio, campi))
    if not candidate:
        return {}
    # Le voci arrivano dalla più recente; se gli orari ci sono, decidono loro.
    con_orario = [c for c in candidate if c[0] is not None]
    if con_orario:
        return max(con_orario, key=lambda c: c[0] or datetime.min.replace(tzinfo=UTC))[1]
    return candidate[0][1]


def ultima_ricarica_storica(risposta: Any) -> dict[str, Any]:
    """La stessa cosa dal vecchio ``/v1/charging/<vin>/history`` (che a volte smette di aggiornarsi)."""
    periodi = risposta.get("periods") if isinstance(risposta, dict) else None
    sessioni = [
        x
        for periodo in (periodi if isinstance(periodi, list) else [])
        if isinstance(periodo, dict)
        for x in periodo.get("sessions") or []
        if isinstance(x, dict) and data_cloud(x.get("startAt"))
    ]
    if not sessioni:
        return {}
    ultima = max(sessioni, key=lambda x: data_cloud(x.get("startAt")) or datetime.min.replace(tzinfo=UTC))
    inizio = data_cloud(ultima.get("startAt"))
    campi: dict[str, Any] = {"ultima_ricarica_inizio": inizio}
    if (kwh := numero(ultima.get("chargedInKWh"))) is not None:
        campi["ultima_ricarica_kwh"] = kwh
    if (tipo := str(ultima.get("currentType") or "").lower()) in ("ac", "dc"):
        campi["ultima_ricarica_tipo"] = tipo
    if inizio and (minuti := numero(ultima.get("durationInMinutes"))) is not None:
        campi["ultima_ricarica_fine"] = inizio + timedelta(minutes=minuti)
    return campi


def verifica_spin(risposta: Any) -> tuple[bool, int | None]:
    """``{"verificationStatus": "CORRECT_SPIN"}``, o sbagliato coi tentativi che restano."""
    giusto = _maiuscolo(valore(risposta, "verificationStatus")) == "CORRECT_SPIN"
    restano = numero(valore(risposta, "spinStatus.remainingTries"))
    return giusto, int(restano) if restano is not None else None
