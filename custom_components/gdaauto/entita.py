"""Quello che hanno in comune tutte le entità di GdAAuto.

Gli ``entity_id`` sono in italiano e sempre uguali («sensor.<auto>_batteria»,
«binary_sensor.<auto>_cavo_collegato»), qualunque sia la lingua di Home
Assistant e qualunque sia la marca: è da quelle parole e dalle classi che la
plancia di GdAHome e gdanav riconoscono l'auto da sole.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .auto import Auto, AutoHA
from .const import DOMAIN
from .marche import MARCHE
from .vettura import StatoVettura


class EntitaGdaauto(CoordinatorEntity[Auto]):
    _attr_has_entity_name = True

    #: sensor, binary_sensor, lock…: lo dice ogni piattaforma.
    _dominio: str

    def __init__(self, auto: Auto, chiave: str) -> None:
        super().__init__(auto)
        v = auto.vettura
        self.chiave = chiave
        self.entity_id = f"{self._dominio}.{slugify(v.titolo) or 'auto'}_{chiave}"
        self._attr_translation_key = chiave
        self._attr_unique_id = f"{v.vin}_{chiave}"
        marca = MARCHE.get(v.marca)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, v.vin)},
            name=v.titolo,
            manufacturer=marca.NOME if marca else v.marca,
            model=v.modello or None,
            model_id=v.modello or None,
            serial_number=None if isinstance(auto, AutoHA) else v.vin,
        )

    @property
    def stato(self) -> StatoVettura | None:
        return self.coordinator.data

    async def async_update(self) -> None:
        """«Aggiorna» (anche da gdanav in viaggio): una rilettura sola, al massimo
        ogni trenta secondi, qualunque entità la chieda."""
        await self.coordinator.async_request_refresh()


#: (chiave, quando è pronta, come si fa)
Voce = tuple[str, Callable[[Auto], bool], Callable[[], Entity]]


def aggiungi_quando_pronte(
    entry: ConfigEntry, aggiungi: AddConfigEntryEntitiesCallback, auto: Auto, voci: list[Voce]
) -> None:
    """Ogni entità nasce quando la sua auto ha il dato che le serve.

    Una T03 non dice la temperatura della batteria; una Tesla addormentata al
    primo avvio non dice nemmeno se è chiusa. Non si crea niente di sempre
    «sconosciuto»: l'entità nasce alla prima lettura che ha il dato.
    """
    fatte: set[str] = set()

    @callback
    def controlla() -> None:
        if auto.data is None:
            return
        nuove = [(chiave, fai) for chiave, pronta, fai in voci if chiave not in fatte and pronta(auto)]
        if nuove:
            fatte.update(chiave for chiave, _ in nuove)
            aggiungi([fai() for _, fai in nuove])

    controlla()
    entry.async_on_unload(auto.async_add_listener(controlla))
