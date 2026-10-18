"""La diagnostica: quello che serve per capire un problema, senza segreti."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import GdaautoConfigEntry, chiavi_da_redigere

_DA_TOGLIERE = chiavi_da_redigere() | {
    "latitude",
    "longitude",
    "plateNumber",
    "carId",
    "car_id",
    "2",
    "3",
    "2190",
    "2191",
    "3724",
    "3725",
    "dispositivo",
}


#: Nei dati grezzi dei cloud la posizione ha mille nomi: «Latitude», «gpsLatitude», «lat»…
_POSIZIONE = ("latitud", "longitud")
_CORTE = {"lat", "lon", "lng", "vin"}


def _da_togliere(chiave: object) -> bool:
    testo = str(chiave).lower()
    return chiave in _DA_TOGLIERE or testo in _CORTE or any(p in testo for p in _POSIZIONE)


def _togli(dati: Any) -> Any:
    """Come ``async_redact_data``, ma la posizione si riconosce in qualunque forma."""
    if isinstance(dati, dict):
        return {k: "**REDACTED**" if _da_togliere(k) else _togli(v) for k, v in dati.items()}
    if isinstance(dati, list | tuple):
        return [_togli(x) for x in dati]
    return dati


async def async_get_config_entry_diagnostics(_hass: HomeAssistant, entry: GdaautoConfigEntry) -> dict[str, Any]:
    auto = []
    for i, a in enumerate(entry.runtime_data.auto):
        v = a.vettura
        stato = asdict(a.data) if a.data else None
        auto.append(
            {
                "auto": i + 1,
                "marca": v.marca,
                "modello": v.modello,
                "condivisa": v.condivisa,
                "comandi": sorted(a.comandi),
                "intervallo": str(a.update_interval) if a.update_interval else None,
                "ultimo_esito": a.last_update_success,
                "stato": _togli(stato) if stato else None,
            }
        )
    return {
        "dati": _togli(dict(entry.data)),
        "opzioni": _togli(dict(entry.options)),
        "auto": auto,
    }
