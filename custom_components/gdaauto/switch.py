"""Gli interruttori: il clima e la ricarica."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .const import COMANDO_CLIMA, COMANDO_RICARICA
from .entita import EntitaGdaauto, Voce, aggiungi_quando_pronte


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for auto in entry.runtime_data.auto:
        voci: list[Voce] = []
        if COMANDO_CLIMA in auto.comandi:
            voci.append(("clima", lambda a: a.data is not None, lambda auto=auto: Clima(auto, "clima")))
        if COMANDO_RICARICA in auto.comandi:
            voci.append(
                (
                    "comando_ricarica",
                    lambda a: a.data is not None and a.data.stato_ricarica is not None,
                    lambda auto=auto: Ricarica(auto, "comando_ricarica"),
                )
            )
        aggiungi_quando_pronte(entry, async_add_entities, auto, voci)


class Clima(EntitaGdaauto, SwitchEntity):
    _dominio = "switch"

    @property
    def is_on(self) -> bool | None:
        return self.stato.clima_acceso if self.stato else None

    async def async_turn_on(self, **_kwargs: Any) -> None:
        temperatura = self.stato.temperatura_clima_c if self.stato else None
        await self.coordinator.async_comanda(COMANDO_CLIMA, acceso=True, temperatura=temperatura or 22)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self.coordinator.async_comanda(COMANDO_CLIMA, acceso=False)


class Ricarica(EntitaGdaauto, SwitchEntity):
    """Avvia o ferma la ricarica col cavo già attaccato."""

    _dominio = "switch"

    @property
    def is_on(self) -> bool | None:
        return self.stato.in_carica if self.stato else None

    @property
    def available(self) -> bool:
        return super().available and bool(self.stato and self.stato.cavo_collegato)

    async def async_turn_on(self, **_kwargs: Any) -> None:
        await self.coordinator.async_comanda(COMANDO_RICARICA, avvia=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self.coordinator.async_comanda(COMANDO_RICARICA, avvia=False)
