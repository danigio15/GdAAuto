"""Dalle risposte di My Honda+ allo stato di GdAAuto.

Il cruscotto (``/tsp/dashboard-latest``) è un JSON solo, con quello che l'auto
ha mandato l'ultima volta al cloud:

- ``evStatus``: carica (``soc``), autonomia col clima acceso (``evRange``) e
  quanta in più a clima spento (``evClimateOffRange``), autonomia totale,
  stato della ricarica (``chargeStatus``: ``running``, ``stopped``,
  ``complete``…), cavo (``plugStatus``: ``plugged in``, ``unplugged``…),
  minuti al traguardo (``timeToTargetSoc``), i due limiti di carica (a casa e
  fuori, ``chargeLimitHome``/``Away``) e dove si trova l'auto (``homeAway``),
  il quadro (``igStatus``);
- ``fuelLevel``: sulle ibride il serbatoio e l'autonomia a benzina;
- ``odometer``, ``gpsData`` (posizione in gradi, primi e secondi separati da
  virgole: «41,53,24.904»; velocità), ``temperature.cabin``,
  ``climateControl.status.isActive``;
- ``doorStatus``, ``windowStatus``, ``lightStatus``, ``warningLamps``.

Tutti i numeri arrivano come stringhe, e le unità seguono l'account: per un
account britannico ``mile``. Qui tutto diventa km, km/h e °C.

Lo storico dei viaggi (``/tsp/journey-history``) è una tabella: ``def`` dà i
nomi delle colonne, ``data`` le righe (data, km, consumo medio…); da lì
vengono i km degli ultimi sette giorni e il consumo medio.

I nomi dei campi e il loro significato vengono da
enricobattocchi/pymyhondaplus (licenza MIT): vedi RINGRAZIAMENTI.md.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

_MIGLIO_KM = 1.609344

_RICARICA = {
    "running": StatoRicarica.IN_CARICA,
    "charging": StatoRicarica.IN_CARICA,
    "complete": StatoRicarica.COMPLETATA,
    "completed": StatoRicarica.COMPLETATA,
    "stopped": StatoRicarica.NON_IN_CARICA,
    "notcharging": StatoRicarica.NON_IN_CARICA,
    "not charging": StatoRicarica.NON_IN_CARICA,
}
_CAVO = {
    "plugged in": True,
    "plugged_in": True,
    "connected": True,
    "unplugged": False,
    "disconnected": False,
    "not plugged in": False,
}
#: ``doorStatus``: le portiere vere (il cofano e il bagagliaio contano a parte)
_NON_PORTIERE = frozenset({"hood", "trunk"})


def numero(v: Any) -> float | None:
    """Le stringhe di Honda in numeri; «unknown», «» e simili sono ``None``."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def in_miglia(unita: Any) -> bool:
    return str(unita or "").strip().lower() in {"mi", "mile", "miles"}


def _km(valore: Any, unita: Any) -> float | None:
    n = numero(valore)
    if n is None:
        return None
    return round(n * _MIGLIO_KM, 1) if in_miglia(unita) else n


def _celsius(valore: Any, unita: Any) -> float | None:
    n = numero(valore)
    if n is None:
        return None
    return round((n - 32) / 1.8, 1) if str(unita or "c").strip().lower().startswith("f") else n


def gradi(dms: Any) -> float | None:
    """«41,53,24.904» (gradi, primi, secondi) → 41.890251; un numero semplice resta com'è."""
    testo = str(dms or "").strip()
    if not testo:
        return None
    parti = testo.split(",")
    try:
        if len(parti) != 3:
            return float(testo)
        g, p, s = (float(x) for x in parti)
    except ValueError:
        return None
    segno = -1 if parti[0].strip().startswith("-") else 1
    return round(segno * (abs(g) + p / 60 + s / 3600), 7)


def _quando(testo: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(testo))
    except ValueError:
        return None


def _tutte(gruppo: Any, chiave: str, valore: str, *, salta: frozenset[str] = frozenset()) -> bool | None:
    """Vero se ogni voce del gruppo che dice ``chiave`` vale ``valore``; ``None`` se nessuna lo dice."""
    if not isinstance(gruppo, dict):
        return None
    letti = [
        str(v.get(chiave)).lower() == valore
        for k, v in gruppo.items()
        if k not in salta and isinstance(v, dict) and v.get(chiave) is not None
    ]
    return all(letti) if letti else None


def interpreta(cruscotto: dict[str, Any]) -> StatoVettura:
    """Il cruscotto di ``/tsp/dashboard-latest`` → ``StatoVettura``."""
    ev = cruscotto.get("evStatus") if isinstance(cruscotto.get("evStatus"), dict) else {}
    gps = cruscotto.get("gpsData") if isinstance(cruscotto.get("gpsData"), dict) else {}
    contakm = cruscotto.get("odometer") if isinstance(cruscotto.get("odometer"), dict) else {}
    serbatoio = cruscotto.get("fuelLevel") if isinstance(cruscotto.get("fuelLevel"), dict) else {}
    unita = ev.get("rangeUnit") or contakm.get("unit") or "km"
    s = StatoVettura()

    s.batteria = numero(ev.get("soc"))
    s.autonomia_km = _km(ev.get("evRange"), unita)
    if (spento := numero(ev.get("evClimateOffRange"))) is not None and s.autonomia_km is not None:
        # L'autonomia «di omologazione» più vicina che Honda dia: quella a clima spento.
        s.autonomia_nominale_km = round(s.autonomia_km + (_km(spento, unita) or 0.0), 1)
    totale = _km(ev.get("totalRange"), unita)
    autonomia_benzina = _km(
        (serbatoio.get("driveRange") or {}).get("value"), (serbatoio.get("driveRange") or {}).get("unit") or unita
    )
    livello = serbatoio.get("currentLevel") if isinstance(serbatoio.get("currentLevel"), dict) else {}
    if serbatoio:
        # Le ibride: il serbatoio, e l'autonomia a benzina che Honda chiama «driveRange».
        if str(livello.get("unit") or "percentage").lower().startswith("perc"):
            s.carburante_percento = numero(livello.get("value"))
        s.autonomia_carburante_km = autonomia_benzina
    s.autonomia_totale_km = totale if totale is not None else autonomia_benzina

    stato = str(ev.get("chargeStatus") or "").strip().lower()
    s.stato_ricarica = _RICARICA.get(stato)
    s.cavo_collegato = _CAVO.get(str(ev.get("plugStatus") or "").strip().lower())
    if s.stato_ricarica == StatoRicarica.IN_CARICA:
        minuti = numero(ev.get("timeToTargetSoc"))
        s.tempo_ricarica_min = int(minuti) if minuti is not None and minuti >= 0 else None
    modo = str(ev.get("chargeMode") or "").lower()
    if s.stato_ricarica == StatoRicarica.IN_CARICA and modo:
        s.ricarica_rapida = "fast" in modo
    casa = numero(ev.get("chargeLimitHome"))
    fuori = numero(ev.get("chargeLimitAway"))
    dove = str(ev.get("homeAway") or "").strip().lower()
    limite = casa if dove == "home" else fuori if dove == "away" else None
    s.limite_ricarica = int(limite) if limite else None

    accensione = str(ev.get("igStatus") or "").strip().lower()
    s.quadro_acceso = {"on": True, "off": False}.get(accensione)

    s.odometro_km = _km(contakm.get("value"), contakm.get("unit") or unita)
    velocita = gps.get("velocity") if isinstance(gps.get("velocity"), dict) else {}
    s.velocita_kmh = _km(velocita.get("value"), str(velocita.get("unit") or "km/h").split("/")[0].replace("mph", "mi"))
    coord = gps.get("coordinate") if isinstance(gps.get("coordinate"), dict) else {}
    lat, lon = gradi(coord.get("latitude")), gradi(coord.get("longitude"))
    if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
        s.latitudine, s.longitudine = lat, lon
        # I gradi arrivano col segno («-3,42,…» a ovest).
        s.posizione_con_segno = True

    abitacolo = (cruscotto.get("temperature") or {}).get("cabin") or {}
    s.temperatura_interna_c = _celsius(abitacolo.get("value"), abitacolo.get("unit"))
    clima = (cruscotto.get("climateControl") or {}).get("status") or {}
    if isinstance(clima.get("isActive"), bool):
        s.clima_acceso = clima["isActive"]
    sbrina = str(ev.get("acDefAutoSetting") or "").lower()
    if s.clima_acceso and sbrina:
        s.sbrinamento = sbrina.startswith("def auto on")

    porte = cruscotto.get("doorStatus")
    s.chiusa = _tutte(porte, "lockState", "lock")
    chiuse = _tutte(porte, "openState", "closed", salta=_NON_PORTIERE)
    s.portiere_aperte = None if chiuse is None else not chiuse
    if isinstance(porte, dict) and isinstance(porte.get("trunk"), dict) and porte["trunk"].get("openState"):
        s.bagagliaio_aperto = str(porte["trunk"]["openState"]).lower() != "closed"
    finestrini = _tutte(cruscotto.get("windowStatus"), "closeState", "closed")
    s.finestrini_aperti = None if finestrini is None else not finestrini

    spie = [
        str(m.get("lampName") or "").lower()
        for m in (cruscotto.get("warningLamps") or {}).get("messages") or []
        if isinstance(m, dict) and str(m.get("condition") or "").upper() == "ON"
    ]
    if isinstance((cruscotto.get("warningLamps") or {}).get("messages"), list):
        s.pneumatici_allarme = any("tyre" in n or "tire" in n or "tpms" in n for n in spie)

    s.letto = _quando(cruscotto.get("timestamp") or gps.get("dtTime"))
    s.grezzo = {k: v for k, v in cruscotto.items() if k != "gpsData"}
    return s


# --- lo storico dei viaggi -------------------------------------------------------


def righe(pagina: dict[str, Any]) -> list[dict[str, Any]]:
    """Una pagina di ``journey-history``: le colonne in ``def``, le righe in ``data``."""
    corpo = pagina.get("payload") if isinstance(pagina.get("payload"), dict) else {}
    colonne = [str(c) for c in corpo.get("def") or []]
    return [dict(zip(colonne, r, strict=False)) for r in corpo.get("data") or [] if isinstance(r, list)]


def storico(viaggi: list[dict[str, Any]], oggi: date, *, elettrica: bool, miglia: bool) -> dict[str, Any]:
    """Dai viaggi del mese: km ed energia degli ultimi sette giorni, e il consumo medio.

    ``Mileage`` è nell'unità dell'account, ``AveFuelEconomy`` per un'elettrica è
    kWh/100 km (o mi/kWh con le miglia). Per un'ibrida è carburante: niente kWh.
    """
    dal = (oggi - timedelta(days=6)).isoformat()
    km_7 = energia_7 = km_tot = energia_tot = 0.0
    contati = False
    for r in viaggi:
        distanza = numero(r.get("Mileage"))
        if distanza is None or distanza < 0:
            continue
        km = distanza * _MIGLIO_KM if miglia else distanza
        resa = numero(r.get("AveFuelEconomy"))
        kwh = None
        if elettrica and resa and resa > 0:
            kwh = distanza / resa if miglia else km * resa / 100
        if kwh is not None:
            km_tot += km
            energia_tot += kwh
        if str(r.get("OneTripDate") or "")[:10] >= dal:
            contati = True
            km_7 += km
            energia_7 += kwh or 0.0
    esito: dict[str, Any] = {}
    if viaggi:
        esito["km_7_giorni"] = round(km_7, 1) if contati else 0.0
        if elettrica:
            esito["energia_7_giorni_kwh"] = round(energia_7, 2) if contati else 0.0
    if elettrica and km_tot >= 20:
        # Sotto i 20 km il consumo medio dice più del traffico che dell'auto.
        esito["consumo_medio_kwh_100km"] = round(energia_tot / km_tot * 100, 1)
    return esito
