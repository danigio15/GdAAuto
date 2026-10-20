"""Le risposte del cloud OLA di SEAT e CUPRA, lette nel vocabolario di GdAAuto.

Il cloud non ha uno stato unico: ogni pezzo dell'auto ha il suo indirizzo, e
``interpreta`` mette insieme quelli che il cliente è riuscito a leggere (un
pezzo che manca resta ``None``):

- ``stato`` (``/v2/vehicles/{vin}/status``): serrature, portiere, cofano,
  bagagliaio, finestrini, quadro;
- ``mycar`` (``/v5/users/{id}/vehicles/{vin}/mycar``): i «motori» (batteria e
  serbatoio con le autonomie) e il riassunto di ricarica e clima;
- ``carica`` (``/v1/vehicles/{vin}/charging/status``): carica, potenza,
  minuti alla fine, cavo;
- ``impostazioni_carica`` (``/v1/vehicles/{vin}/charging/info``, o il vecchio
  ``/vehicles/{vin}/charging/settings``): il limite di ricarica;
- ``clima`` e ``impostazioni_clima`` (``climatisation/status`` e ``/settings``);
- ``parcheggio``, ``chilometri``, ``autonomie``, ``disponibilita``.

I nomi dei campi vengono da tillsteinbach/CarConnectivity-connector-seatcupra
(``connector.py``, ``charging.py``, MIT) e da WulfgarW/pycupra (Apache 2.0):
vedi RINGRAZIAMENTI.md. Il codice è scritto da capo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

#: Lo stato della ricarica del cloud → quello di GdAAuto.
_RICARICA = {
    "charging": StatoRicarica.IN_CARICA,
    "conservation": StatoRicarica.COMPLETATA,
    "chargepurposereachedandnotconservationcharging": StatoRicarica.COMPLETATA,
    "chargepurposereachedandconservation": StatoRicarica.COMPLETATA,
    "readyforcharging": StatoRicarica.NON_IN_CARICA,
    "notreadyforcharging": StatoRicarica.NON_IN_CARICA,
    "off": StatoRicarica.NON_IN_CARICA,
    "discharging": StatoRicarica.NON_IN_CARICA,
    "error": StatoRicarica.ERRORE,
}
#: Le modalità di ricarica che aspettano un orario: ricarica programmata.
_PROGRAMMATE = {"timer", "preferredchargingtimes", "timerchargingwithclimatisation"}
#: Il clima è spento solo in questi stati; gli altri (heating, cooling, ventilation…) sono accesi.
_CLIMA_SPENTO = {"off", "invalid", "unsupported", "unknown", ""}
#: I «motori» elettrici e quelli a carburante, come li chiama ``mycar``.
_ELETTRICI = {"electric"}
_CARBURANTE = {"gasoline", "petrol", "diesel", "fuel", "cng", "lpg"}


def _numero(valore: Any) -> float | None:
    if isinstance(valore, bool) or valore is None:
        return None
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def _vero(valore: Any) -> bool | None:
    """Il cloud scrive i sì e i no come ``true`` o come ``"true"``."""
    if isinstance(valore, bool):
        return valore
    if isinstance(valore, str):
        testo = valore.strip().lower()
        if testo in ("true", "on", "yes", "1"):
            return True
        if testo in ("false", "off", "no", "0"):
            return False
    return None


def _dict(valore: Any) -> dict[str, Any]:
    return valore if isinstance(valore, dict) else {}


def data_cloud(testo: Any) -> datetime | None:
    """Le date del cloud, «2025-02-18T17:24:02Z» o coi millesimi; sempre in UTC."""
    if not isinstance(testo, str) or not testo:
        return None
    try:
        quando = datetime.fromisoformat(testo)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def motori(mycar: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Il motore elettrico e quello a carburante di ``mycar`` (vuoti se mancano)."""
    elettrico: dict[str, Any] = {}
    carburante: dict[str, Any] = {}
    for chiave in ("primary", "secondary"):
        m = _dict(_dict(_dict(mycar).get("engines")).get(chiave))
        tipo = str(m.get("fuelType") or "").lower()
        if tipo in _ELETTRICI and not elettrico:
            elettrico = m
        elif tipo in _CARBURANTE and not carburante:
            carburante = m
    return elettrico, carburante


def _autonomia_da_elenco(autonomie: dict[str, Any] | None, nomi: tuple[str, ...]) -> float | None:
    for voce in _dict(autonomie).get("ranges") or []:
        if isinstance(voce, dict) and voce.get("rangeName") in nomi:
            return _numero(voce.get("value"))
    return None


def limite_impostato(impostazioni: dict[str, Any] | None) -> int | None:
    """Il limite di ricarica: «targetSoc» in ``charging/info``, «targetSoc_pct» nel formato vecchio."""
    s = _dict(_dict(impostazioni).get("settings")) or _dict(impostazioni)
    for chiave in ("targetSoc", "targetSoc_pct", "targetSocPercentage"):
        if (valore := _numero(s.get(chiave))) is not None:
            return int(valore)
    return None


def _porte(stato: dict[str, Any], s: StatoVettura) -> None:
    porte = _dict(stato.get("doors"))
    aperte = [_vero(_dict(p).get("open")) for nome, p in porte.items() if nome not in ("hood", "trunk")]
    if any(a is not None for a in aperte):
        s.portiere_aperte = any(a is True for a in aperte)
    bagagliaio = _dict(stato.get("trunk")) or _dict(porte.get("trunk"))
    s.bagagliaio_aperto = _vero(bagagliaio.get("open"))
    finestrini = dict(_dict(stato.get("windows")))
    if stato.get("sunRoof") is not None:
        finestrini["sunRoof"] = stato["sunRoof"]
    valori = [str(v).lower() for v in finestrini.values() if isinstance(v, str)]
    noti = [v for v in valori if v in ("open", "closed")]
    if noti:
        s.finestrini_aperti = "open" in noti


def _chiusura(stato: dict[str, Any], s: StatoVettura) -> None:
    s.chiusa = _vero(stato.get("locked"))
    if s.chiusa is None:
        # Senza la serratura generale, si guarda quella di ogni portiera.
        chiuse = [_vero(_dict(p).get("locked")) for p in _dict(stato.get("doors")).values()]
        if chiuse and all(c is not None for c in chiuse):
            s.chiusa = all(chiuse)
    motore = str(stato.get("engine") or "").lower()
    if motore in ("on", "running"):
        s.quadro_acceso = True
    elif motore in ("off", "stopped"):
        s.quadro_acceso = False


def _ricarica(carica: dict[str, Any], riassunto: dict[str, Any], s: StatoVettura) -> None:
    c = _dict(carica.get("charging"))
    stato = str(c.get("state") or riassunto.get("status") or "").lower()
    modo = str(c.get("chargeMode") or riassunto.get("chargeMode") or "").lower()
    s.stato_ricarica = _RICARICA.get(stato)
    if s.stato_ricarica == StatoRicarica.NON_IN_CARICA and stato == "readyforcharging" and modo in _PROGRAMMATE:
        s.stato_ricarica = StatoRicarica.PROGRAMMATA
    if modo:
        s.ricarica_programmata = modo in _PROGRAMMATE
    if stato:
        s.v2l_attivo = stato == "discharging"
    potenza = _numero(c.get("chargedPowerInKw", c.get("chargePower_kW")))
    if potenza is None:
        potenza = _numero(riassunto.get("chargedPowerInKw"))
    if potenza is None and s.stato_ricarica is not None and s.stato_ricarica != StatoRicarica.IN_CARICA:
        potenza = 0.0
    s.potenza_ricarica_diretta_kw = potenza
    minuti = _numero(c.get("remainingTimeInMinutes", c.get("remainingChargingTimeToComplete_min")))
    if minuti is None:
        minuti = _numero(riassunto.get("remainingTime"))
    s.tempo_ricarica_min = int(minuti) if minuti is not None and minuti >= 0 else None
    tipo = str(c.get("chargeType") or "").lower()
    if tipo in ("ac", "dc"):
        s.ricarica_rapida = tipo == "dc"
    spina = _dict(carica.get("plug"))
    connessione = str(spina.get("connection") or spina.get("plugConnectionState") or "").lower()
    if connessione in ("connected", "disconnected"):
        s.cavo_collegato = connessione == "connected"


def _clima(clima: dict[str, Any], impostazioni: dict[str, Any], riassunto: dict[str, Any], s: StatoVettura) -> None:
    stato_clima = _dict(clima.get("climatisationStatus"))
    stato = str(stato_clima.get("climatisationState") or riassunto.get("status") or "").lower()
    if stato and stato not in ("unknown", "invalid", "unsupported"):
        s.clima_acceso = stato not in _CLIMA_SPENTO
    temperatura = _numero(impostazioni.get("targetTemperatureInCelsius"))
    if temperatura is None:
        temperatura = _numero(riassunto.get("targetTemperatureCelsius"))
    s.temperatura_clima_c = temperatura
    vetri = _dict(clima.get("windowHeatingStatus")).get("windowHeatingStatus")
    if isinstance(vetri, list):
        stati = [str(_dict(v).get("windowHeatingState") or "").lower() for v in vetri]
        if any(x in ("on", "off") for x in stati):
            s.sbrinamento = "on" in stati


def interpreta(
    *,
    stato: dict[str, Any] | None = None,
    mycar: dict[str, Any] | None = None,
    carica: dict[str, Any] | None = None,
    impostazioni_carica: dict[str, Any] | None = None,
    clima: dict[str, Any] | None = None,
    impostazioni_clima: dict[str, Any] | None = None,
    parcheggio: dict[str, Any] | None = None,
    chilometri: dict[str, Any] | None = None,
    autonomie: dict[str, Any] | None = None,
    disponibilita: dict[str, Any] | None = None,
) -> StatoVettura:
    """Lo stato dell'auto da quello che il cloud ha detto; ogni pezzo è facoltativo."""
    stato, mycar, carica = _dict(stato), _dict(mycar), _dict(carica)
    servizi = _dict(mycar.get("services"))
    riassunto_carica = _dict(servizi.get("charging"))
    riassunto_clima = _dict(servizi.get("climatisation"))
    s = StatoVettura()

    # La batteria: prima lo stato della ricarica, poi i «motori» di mycar.
    elettrico, carburante = motori(mycar)
    batteria = _dict(carica.get("battery"))
    s.batteria = _numero(batteria.get("currentSocPercentage", batteria.get("currentSOC_pct")))
    if s.batteria is None:
        s.batteria = _numero(elettrico.get("levelPct"))
    if s.batteria is None:
        s.batteria = _numero(riassunto_carica.get("currentPct"))
    s.autonomia_km = _numero(batteria.get("cruisingRangeElectricKm", batteria.get("cruisingRangeElectric_km")))
    if s.autonomia_km is None:
        s.autonomia_km = _numero(elettrico.get("rangeKm", elettrico.get("rangeInKm")))
    if s.autonomia_km is None:
        s.autonomia_km = _autonomia_da_elenco(autonomie, ("electricRangeKm",))

    # Le ibride plug-in: il serbatoio, la sua autonomia e quella totale.
    if carburante:
        s.carburante_percento = _numero(carburante.get("levelPct"))
        s.autonomia_carburante_km = _numero(carburante.get("rangeKm", carburante.get("rangeInKm")))
        if s.autonomia_carburante_km is None:
            s.autonomia_carburante_km = _autonomia_da_elenco(autonomie, ("gasolineRangeKm", "dieselRangeKm"))
        if s.autonomia_km is not None and s.autonomia_carburante_km is not None:
            s.autonomia_totale_km = s.autonomia_km + s.autonomia_carburante_km

    s.odometro_km = _numero(_dict(chilometri).get("mileageKm"))

    if carica or riassunto_carica:
        _ricarica(carica, riassunto_carica, s)
    s.limite_ricarica = limite_impostato(impostazioni_carica)
    if s.limite_ricarica is None and (obiettivo := _numero(riassunto_carica.get("targetPct"))) is not None:
        s.limite_ricarica = int(obiettivo)

    if clima or impostazioni_clima or riassunto_clima:
        _clima(_dict(clima), _dict(impostazioni_clima), riassunto_clima, s)

    if stato:
        _chiusura(stato, s)
        _porte(stato, s)

    p = _dict(parcheggio)
    lat, lon = _numero(p.get("lat")), _numero(p.get("lon"))
    if lat is not None and lon is not None:
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True

    disponibile = str(_dict(disponibilita).get("remote-availability") or "").lower()
    if disponibile in ("online", "offline"):
        s.in_sonno = disponibile == "offline"

    s.letto = (
        data_cloud(stato.get("updatedAt"))
        or data_cloud(_dict(carica.get("charging")).get("carCapturedTimestamp"))
        or data_cloud(_dict(_dict(clima).get("climatisationStatus")).get("carCapturedTimestamp"))
        or data_cloud(p.get("updatedAt"))
    )
    s.grezzo = {k: v for k, v in (("stato", stato), ("mycar", mycar), ("carica", carica), ("clima", clima)) if v}
    return s


# --- lo storico dei viaggi --------------------------------------------------


def giorni_di_viaggio(risposta: dict[str, Any] | None) -> list[tuple[date, dict[str, float]]]:
    """I riassunti giornalieri di ``driving-data``: per ogni giorno, i suoi valori (km, consumo…)."""
    giorni: list[tuple[date, dict[str, float]]] = []
    dati = _dict(risposta)
    voci = _dict(dati.get("data")).get("dailySummary") or dati.get("dailySummary") or []
    for voce in voci if isinstance(voci, list) else []:
        giorno = _dict(voce).get("day")
        testo = str(_dict(giorno).get("id") if isinstance(giorno, dict) else giorno or "")
        try:
            quando = date.fromisoformat(testo[:10])
        except ValueError:
            continue
        valori: dict[str, float] = {}
        for v in _dict(voce).get("values") or []:
            if not isinstance(v, dict) or not v.get("id"):
                continue
            numero = _numero(v.get("value"))
            if numero is None:
                numero = _numero(v.get("total"))
            if numero is not None:
                valori[str(v["id"])] = numero
        giorni.append((quando, valori))
    return giorni


def storico_viaggi(risposta: dict[str, Any] | None, oggi: date) -> dict[str, Any]:
    """Km ed energia degli ultimi sette giorni, e il consumo medio di tutti i giorni avuti.

    Il cloud dà, per ogni giorno, i km (``distanceDriven``) e il consumo
    elettrico medio in kWh/100 km (``electricConsumption``): l'energia è il
    consumo per i km.
    """
    giorni = giorni_di_viaggio(risposta)
    if not giorni:
        return {}
    da = oggi - timedelta(days=6)
    km_7 = energia_7 = km_tutti = energia_tutti = 0.0
    for quando, valori in giorni:
        km = valori.get("distanceDriven") or 0.0
        consumo = valori.get("electricConsumption")
        energia = km * consumo / 100 if consumo is not None and 0 < consumo < 100 else 0.0
        if energia:
            km_tutti += km
            energia_tutti += energia
        if da <= quando <= oggi:
            km_7 += km
            energia_7 += energia
    campi: dict[str, Any] = {"km_7_giorni": round(km_7, 1), "energia_7_giorni_kwh": round(energia_7, 2)}
    medio = energia_tutti / km_tutti * 100 if km_tutti >= 20 else None
    campi["consumo_medio_kwh_100km"] = round(medio, 1) if medio is not None and 5 <= medio <= 60 else None
    return campi
