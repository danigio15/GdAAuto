"""Dov'è l'auto."""

from __future__ import annotations

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .entita import EntitaGdaauto, Voce, aggiungi_quando_pronte


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for auto in entry.runtime_data.auto:
        voce: Voce = (
            "posizione",
            lambda a: a.data is not None and a.data.latitudine is not None and a.data.longitudine is not None,
            lambda auto=auto: Posizione(auto, "posizione"),
        )
        aggiungi_quando_pronte(entry, async_add_entities, auto, [voce])


class Posizione(EntitaGdaauto, TrackerEntity):
    _dominio = "device_tracker"

    @property
    def latitude(self) -> float | None:
        return self.stato.latitudine if self.stato else None

    @property
    def longitude(self) -> float | None:
        return self.stato.longitudine if self.stato else None
