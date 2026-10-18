"""Dov'è l'auto."""

from __future__ import annotations

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .entita import EntitaGdaauto


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for auto in entry.runtime_data.auto:
        fatta: list[bool] = []

        @callback
        def controlla(auto=auto, fatta=fatta) -> None:
            s = auto.data
            if not fatta and s is not None and s.latitudine is not None and s.longitudine is not None:
                fatta.append(True)
                async_add_entities([Posizione(auto, "posizione")])

        controlla()
        entry.async_on_unload(auto.async_add_listener(controlla))


class Posizione(EntitaGdaauto, TrackerEntity):
    _dominio = "device_tracker"

    @property
    def latitude(self) -> float | None:
        return self.stato.latitudine if self.stato else None

    @property
    def longitude(self) -> float | None:
        return self.stato.longitudine if self.stato else None
