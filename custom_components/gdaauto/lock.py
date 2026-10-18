"""La serratura, quando la marca la sa comandare.

Chiudere si può sempre; aprire solo se nelle opzioni c'è il permesso «apri»:
è il comando che fa più danni se lo dà la persona o l'automazione sbagliata.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .const import COMANDO_APRI, COMANDO_CHIUDI
from .entita import EntitaGdaauto


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        Serratura(a, "serratura")
        for a in entry.runtime_data.auto
        if COMANDO_CHIUDI in a.comandi and a.data is not None and a.data.chiusa is not None
    )


class Serratura(EntitaGdaauto, LockEntity):
    _dominio = "lock"

    @property
    def is_locked(self) -> bool | None:
        return self.stato.chiusa if self.stato else None

    async def async_lock(self, **_kwargs: Any) -> None:
        await self.coordinator.async_comanda(COMANDO_CHIUDI)

    async def async_unlock(self, **_kwargs: Any) -> None:
        await self.coordinator.async_comanda(COMANDO_APRI)
