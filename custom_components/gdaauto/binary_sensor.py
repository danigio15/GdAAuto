"""Sì o no: in carica, cavo, porte, finestrini, quadro."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .auto import Auto
from .const import COMANDO_CHIUDI
from .entita import EntitaGdaauto, Voce, aggiungi_quando_pronte
from .vettura import StatoVettura


@dataclass(frozen=True, kw_only=True)
class Descrizione(BinarySensorEntityDescription):
    valore: Callable[[StatoVettura], bool | None]


DESCRIZIONI: tuple[Descrizione, ...] = (
    Descrizione(key="in_carica", device_class=BinarySensorDeviceClass.BATTERY_CHARGING, valore=lambda s: s.in_carica),
    Descrizione(key="cavo_collegato", device_class=BinarySensorDeviceClass.PLUG, valore=lambda s: s.cavo_collegato),
    Descrizione(key="portiere", device_class=BinarySensorDeviceClass.DOOR, valore=lambda s: s.portiere_aperte),
    Descrizione(key="bagagliaio", device_class=BinarySensorDeviceClass.OPENING, valore=lambda s: s.bagagliaio_aperto),
    Descrizione(key="finestrini", device_class=BinarySensorDeviceClass.WINDOW, valore=lambda s: s.finestrini_aperti),
    Descrizione(key="quadro", device_class=BinarySensorDeviceClass.POWER, valore=lambda s: s.quadro_acceso),
    Descrizione(key="clima_acceso", valore=lambda s: s.clima_acceso),
    Descrizione(key="sentinella", valore=lambda s: s.sentinella),
    Descrizione(key="in_sonno", valore=lambda s: s.in_sonno),
    # La serratura come sola lettura, quando non si può comandare (niente
    # PIN, o la marca non lo permette). Per Home Assistant «on» è aperta.
    Descrizione(
        key="serratura",
        device_class=BinarySensorDeviceClass.LOCK,
        valore=lambda s: None if s.chiusa is None else not s.chiusa,
    ),
)


def _pronta(d: Descrizione) -> Callable[[Auto], bool]:
    def pronta(a: Auto) -> bool:
        if a.data is None or d.valore(a.data) is None:
            return False
        # Con la serratura o il clima comandabili c'è già l'entità che comanda.
        if d.key == "serratura" and COMANDO_CHIUDI in a.comandi:
            return False
        return not (d.key == "clima_acceso" and "clima" in a.comandi)

    return pronta


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for auto in entry.runtime_data.auto:
        voci: list[Voce] = [(d.key, _pronta(d), lambda auto=auto, d=d: BinarioGdaauto(auto, d)) for d in DESCRIZIONI]
        aggiungi_quando_pronte(entry, async_add_entities, auto, voci)


class BinarioGdaauto(EntitaGdaauto, BinarySensorEntity):
    _dominio = "binary_sensor"
    entity_description: Descrizione

    def __init__(self, auto: Auto, descrizione: Descrizione) -> None:
        self.entity_description = descrizione
        super().__init__(auto, descrizione.key)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.valore(self.stato) if self.stato else None
