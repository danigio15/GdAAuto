"""Il limite di ricarica: fin dove carica l'auto."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .const import COMANDO_LIMITE
from .entita import EntitaGdaauto, Voce, aggiungi_quando_pronte


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for auto in entry.runtime_data.auto:
        if COMANDO_LIMITE in auto.comandi:
            voce: Voce = (
                "limite_ricarica",
                lambda a: a.data is not None and a.data.limite_ricarica is not None,
                lambda auto=auto: Limite(auto, "limite_ricarica"),
            )
            aggiungi_quando_pronte(entry, async_add_entities, auto, [voce])


class Limite(EntitaGdaauto, NumberEntity):
    _dominio = "number"
    _attr_device_class = NumberDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float | None:
        return self.stato.limite_ricarica if self.stato else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_comanda(COMANDO_LIMITE, percento=int(value))
