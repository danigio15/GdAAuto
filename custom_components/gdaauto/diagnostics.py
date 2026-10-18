"""La diagnostica: quello che serve per capire un problema, senza segreti."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
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
                "stato": async_redact_data(stato, _DA_TOGLIERE) if stato else None,
            }
        )
    return {
        "dati": async_redact_data(dict(entry.data), _DA_TOGLIERE),
        "opzioni": async_redact_data(dict(entry.options), _DA_TOGLIERE),
        "auto": auto,
    }
