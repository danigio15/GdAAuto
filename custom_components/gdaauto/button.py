"""I pulsanti: rileggi l'auto, e fatti trovare."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .const import COMANDO_TROVA
from .entita import EntitaGdaauto


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    nuove: list[ButtonEntity] = []
    for a in entry.runtime_data.auto:
        nuove.append(Aggiorna(a, "aggiorna"))
        if COMANDO_TROVA in a.comandi:
            nuove.append(Trova(a, "trova"))
    async_add_entities(nuove)


class Aggiorna(EntitaGdaauto, ButtonEntity):
    _dominio = "button"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class Trova(EntitaGdaauto, ButtonEntity):
    """Clacson e luci: per trovarla nel parcheggio."""

    _dominio = "button"

    async def async_press(self) -> None:
        await self.coordinator.async_comanda(COMANDO_TROVA)
