"""Dalle letture della NIO Open Telematics API (Europa) allo stato di GdAAuto.

L'API ufficiale divide l'auto in «risorse»; ognuna è un record con i nomi
interni di NIO e un ``sample_timestamp`` in millisecondi:

- ``soc_status``: ``soc`` (la carica), ``remaining_range`` (km), ``max_soc``
  (il limite di carica scelto nell'auto), ``chrg_state``,
  ``hivolt_btry_curnt`` (la corrente della batteria, in A), ``highest_temp``
  e ``lowest_temp`` (la batteria, con 40 gradi di scarto) e ``btry_paks``
  (i pacchi: tensione e corrente di ognuno);
- ``vehicle_status``: ``mileage`` (km), ``speed`` (decimi di km/h),
  ``vehl_totl_volt`` (decimi di volt), ``vehl_totl_curnt`` (decimi di ampere
  con 1000 A di scarto)… Il suo ``soc`` è un segnaposto (zero): la carica
  vera è quella di ``soc_status``;
- ``position_status``: ``latitude``, ``longitude``;
- ``door_status``: ``vehicle_lock_status`` (1 chiusa, 0 aperta) e, dove ci
  sono, ``door_ajar_*_status`` e ``tailgate_ajar_status`` (1 chiusa, 0 aperta);
- ``window_status``: ``win_*_posn`` (0 chiuso, di più aperto);
- ``hvac_status``: ``outside_temp_c``; ``extremum_data``: le temperature più
  alta e più bassa della batteria, come in ``soc_status``;
- ``odometer_report`` (assistenza): il contachilometri, quando lo stato non lo dà.

I numeri «impossibili» (0xFFFE, 0xFFFF, 0xFFFFFFFE…) vogliono dire «non lo so».

Nomi, scale e scarti vengono da nicolasvangeluwe/nio-home-assistant (licenza
MIT), che li ha presi dallo schema pubblicato da NIO; il significato di
``vehicle_lock_status`` e dei campi delle portiere da genelee26/ha-nio
(licenza MIT), provato su un'auto vera. Vedi RINGRAZIAMENTI.md. Lo stato
della ricarica NIO lo manda con codici che nessuno ha ancora documentato: si
leggono solo quelli scritti in chiaro, gli altri restano nel grezzo.
"""

from __future__ import annotations

from datetime import UTC, datetime
import math
from typing import Any

from ...vettura import StatoRicarica, StatoVettura

_NON_SO_16 = (0xFFFE, 0xFFFF)
_NON_SO_32 = (0xFFFFFFFE, 0xFFFFFFFF)
_NON_SO_8 = (0xFE, 0xFF)

_RICARICA = {
    "charging": StatoRicarica.IN_CARICA,
    "not_charging": StatoRicarica.NON_IN_CARICA,
    "idle": StatoRicarica.NON_IN_CARICA,
    "charge_finished": StatoRicarica.COMPLETATA,
    "finished": StatoRicarica.COMPLETATA,
}
_PORTIERE = (
    "door_ajar_front_left_status",
    "door_ajar_front_right_status",
    "door_ajar_rear_left_status",
    "door_ajar_rear_right_status",
)
_FINESTRINI = ("win_front_left_posn", "win_front_right_posn", "win_rear_left_posn", "win_rear_right_posn")
#: quello che non va nel grezzo (la posizione precisa, i numeri di serie dei pacchi)
_RISERVATI = ("latitude", "longitude", "btry_pak_sn")


def numero(v: Any, *, scala: float = 1, scarto: float = 0, non_so: tuple[int, ...] = ()) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or n in non_so:
        return None
    # Si arrotonda solo una scala (i decimi), non le coordinate.
    return round(n * scala + scarto, 3) if (scala, scarto) != (1, 0) else n


def quando(v: Any) -> datetime | None:
    """``sample_timestamp``: millisecondi (o secondi) dall'epoca."""
    n = numero(v)
    if n is None or n <= 0:
        return None
    if n > 10_000_000_000:
        n /= 1000
    try:
        return datetime.fromtimestamp(n, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _uno_zero(v: Any) -> bool | None:
    """«1»/1 → vero, «0»/0 → falso, il resto non si sa."""
    n = numero(v)
    return {1.0: True, 0.0: False}.get(n) if n is not None else None


def _valido(chiave: str, valore: Any) -> bool:
    """Un ``soc`` o un'autonomia «impossibili» non coprono quelli buoni di prima."""
    if valore is None:
        return False
    if chiave == "soc":
        n = numero(valore)
        return n is not None and 0 <= n <= 100
    if chiave == "remaining_range":
        n = numero(valore, non_so=_NON_SO_16)
        return n is not None and n >= 0
    return True


def unisci(vecchio: dict[str, Any] | None, nuovi: list[dict[str, Any]]) -> dict[str, Any]:
    """I record di ``…/changes`` (dal più vecchio al più nuovo) sopra quello che si sapeva già.

    NIO manda solo i campi cambiati: un campo che manca tiene il valore di prima,
    e un record più vecchio di quello che si sa non lo copre.
    """
    unito = dict(vecchio or {})
    gia = numero(unito.get("sample_timestamp")) or 0
    for record in sorted(
        (r for r in nuovi if isinstance(r, dict)), key=lambda r: numero(r.get("sample_timestamp")) or 0
    ):
        momento = numero(record.get("sample_timestamp")) or 0
        if momento and momento < gia:
            continue
        for chiave, valore in record.items():
            if chiave != "sample_timestamp" and _valido(chiave, valore):
                unito[chiave] = valore
        if momento:
            unito["sample_timestamp"] = record["sample_timestamp"]
            gia = momento
    return unito


def _temperatura_batteria(*fonti: dict[str, Any]) -> float | None:
    """La media fra la più alta e la più bassa; uno zero grezzo (−40 °C) è «non misurata»."""
    for fonte in fonti:
        valori = []
        for chiave in ("highest_temp", "lowest_temp"):
            grezzo = numero(fonte.get(chiave), non_so=_NON_SO_8)
            if grezzo is not None and 0 < grezzo < 254:
                valori.append(grezzo - 40)
        if valori:
            return round(sum(valori) / len(valori), 1)
    return None


def _un_pacco(energia: dict[str, Any]) -> dict[str, Any]:
    """Il pacco batteria, se ce n'è uno solo (con più pacchi le misure non si sommano a occhio)."""
    pacchi = energia.get("btry_paks")
    if isinstance(pacchi, list) and len(pacchi) == 1 and isinstance(pacchi[0], dict):
        return pacchi[0]
    return {}


def _ripulito(record: dict[str, Any]) -> dict[str, Any]:
    pulito: dict[str, Any] = {}
    for chiave, valore in record.items():
        if chiave in _RISERVATI:
            continue
        if isinstance(valore, list):
            valore = [_ripulito(v) if isinstance(v, dict) else v for v in valore]
        pulito[chiave] = valore
    return pulito


def interpreta(parti: dict[str, dict[str, Any]]) -> StatoVettura:
    """Le risorse lette (``soc_status``, ``vehicle_status``…) → ``StatoVettura``."""
    energia = parti.get("soc_status") or {}
    veicolo = parti.get("vehicle_status") or {}
    posizione = parti.get("position_status") or {}
    porte = parti.get("door_status") or {}
    finestrini = parti.get("window_status") or {}
    clima = parti.get("hvac_status") or {}
    estremi = parti.get("extremum_data") or {}
    assistenza = parti.get("odometer_report") or {}
    pacco = _un_pacco(energia)
    s = StatoVettura()

    soc = numero(energia.get("soc"))
    if soc is not None and 0 <= soc <= 100:
        s.batteria = soc
    autonomia = numero(energia.get("remaining_range"), non_so=_NON_SO_16)
    if autonomia is None or autonomia < 0:
        # Lo stato dell'auto ha anche l'autonomia, ma lo zero lì è un segnaposto.
        autonomia = numero(veicolo.get("remaining_range"), non_so=_NON_SO_16)
        autonomia = autonomia if autonomia and autonomia > 0 else None
    s.autonomia_km = autonomia
    limite = numero(energia.get("max_soc"))
    s.limite_ricarica = int(limite) if limite is not None and 0 < limite <= 100 else None
    s.stato_ricarica = _RICARICA.get(str(energia.get("chrg_state") or veicolo.get("chrg_state") or "").strip().lower())

    km = numero(veicolo.get("mileage"), non_so=_NON_SO_32)
    if km is None or km < 0:
        km = numero(assistenza.get("value"), non_so=_NON_SO_32)
    s.odometro_km = km if km is not None and km >= 0 else None
    s.velocita_kmh = numero(veicolo.get("speed"), scala=0.1, non_so=_NON_SO_16)

    # La tensione e la corrente: quelle del pacco (o della batteria) prima, quelle dello stato dopo.
    s.tensione_v = numero(pacco.get("btry_pak_voltage")) or numero(
        veicolo.get("vehl_totl_volt"), scala=0.1, non_so=_NON_SO_16
    )
    corrente = numero(energia.get("hivolt_btry_curnt"))
    if corrente is None:
        corrente = numero(pacco.get("btry_pak_curnt"))
    if corrente is None:
        corrente = numero(veicolo.get("vehl_totl_curnt"), scala=0.1, scarto=-1000, non_so=_NON_SO_16)
    s.corrente_a = corrente

    s.temperatura_esterna_c = numero(clima.get("outside_temp_c"))
    s.temperatura_batteria_c = _temperatura_batteria(energia, estremi)

    lat = numero(posizione.get("latitude"))
    lon = numero(posizione.get("longitude"))
    if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0, 0):
        s.latitudine, s.longitudine = lat, lon
        s.posizione_con_segno = True

    s.chiusa = _uno_zero(porte.get("vehicle_lock_status"))
    chiuse = [x for k in _PORTIERE if (x := _uno_zero(porte.get(k))) is not None]
    if chiuse:
        s.portiere_aperte = not all(chiuse)
    if (bagagliaio := _uno_zero(porte.get("tailgate_ajar_status"))) is not None:
        s.bagagliaio_aperto = not bagagliaio
    posizioni = [p for k in _FINESTRINI if (p := numero(finestrini.get(k))) is not None]
    if posizioni:
        s.finestrini_aperti = any(p > 0 for p in posizioni)

    momenti = [m for p in parti.values() if isinstance(p, dict) and (m := quando(p.get("sample_timestamp")))]
    s.letto = max(momenti) if momenti else None
    s.grezzo = {nome: _ripulito(p) for nome, p in parti.items() if isinstance(p, dict)}
    return s
