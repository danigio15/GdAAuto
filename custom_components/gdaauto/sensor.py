"""I numeri dell'auto: batteria, autonomia, ricarica, gomme…"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GdaautoConfigEntry
from .auto import Auto
from .entita import EntitaGdaauto, Voce, aggiungi_quando_pronte
from .vettura import Marcia, StatoRicarica, StatoVettura


@dataclass(frozen=True, kw_only=True)
class Descrizione(SensorEntityDescription):
    valore: Callable[[StatoVettura], Any]


# La ruota per nome, e la sigla che va nell'entity_id: «ant_sx» è come la
# plancia di GdAHome riconosce le quattro gomme.
_RUOTE = {
    "anteriore_sinistro": "ant_sx",
    "anteriore_destro": "ant_dx",
    "posteriore_sinistro": "post_sx",
    "posteriore_destro": "post_dx",
}

DESCRIZIONI: tuple[Descrizione, ...] = (
    Descrizione(
        key="batteria",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.batteria,
    ),
    Descrizione(
        key="autonomia",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.autonomia_km,
    ),
    Descrizione(
        key="odometro",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        valore=lambda s: s.odometro_km,
    ),
    Descrizione(
        key="velocita",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.velocita_kmh,
    ),
    Descrizione(
        key="potenza",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        valore=lambda s: s.potenza_kw,
    ),
    Descrizione(
        key="potenza_ricarica",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        valore=lambda s: s.potenza_ricarica_kw,
    ),
    Descrizione(
        key="tempo_ricarica",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        valore=lambda s: s.tempo_ricarica_min if s.in_carica else (0 if s.tempo_ricarica_min is not None else None),
    ),
    Descrizione(
        key="energia_residua",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        valore=lambda s: s.energia_residua_kwh,
    ),
    Descrizione(
        key="stato_ricarica",
        device_class=SensorDeviceClass.ENUM,
        options=[r.nome for r in StatoRicarica],
        valore=lambda s: s.stato_ricarica.nome if s.stato_ricarica is not None else None,
    ),
    Descrizione(
        key="marcia",
        device_class=SensorDeviceClass.ENUM,
        options=[m.nome for m in Marcia],
        valore=lambda s: s.marcia.nome if s.marcia is not None else None,
    ),
    Descrizione(
        key="temperatura_batteria",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        valore=lambda s: s.temperatura_batteria_c,
    ),
    Descrizione(
        key="temperatura_interna",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        valore=lambda s: s.temperatura_interna_c,
    ),
    Descrizione(
        key="temperatura_esterna",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        valore=lambda s: s.temperatura_esterna_c,
    ),
    Descrizione(
        key="autonomia_nominale",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        valore=lambda s: s.autonomia_nominale_km,
    ),
    Descrizione(
        key="carburante",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.carburante_percento,
    ),
    Descrizione(
        key="autonomia_carburante",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.autonomia_carburante_km,
    ),
    Descrizione(
        key="autonomia_totale",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.autonomia_totale_km,
    ),
    Descrizione(
        key="consumo_medio",
        native_unit_of_measurement="kWh/100 km",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        valore=lambda s: s.consumo_medio_kwh_100km,
    ),
    Descrizione(
        key="potenza_riscaldatore",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.potenza_riscaldatore_w,
    ),
    *(
        Descrizione(
            key=f"energia_oggi_{dove}",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL,
            suggested_display_precision=1,
            valore=lambda s, c=campo: getattr(s, c),
        )
        for dove, campo in (
            ("guida", "energia_oggi_guida_kwh"),
            ("clima", "energia_oggi_clima_kwh"),
            ("altro", "energia_oggi_altro_kwh"),
        )
    ),
    Descrizione(
        key="km_7_giorni",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        valore=lambda s: s.km_7_giorni,
    ),
    Descrizione(
        key="energia_7_giorni",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        valore=lambda s: s.energia_7_giorni_kwh,
    ),
    Descrizione(
        key="ultima_ricarica_energia",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        valore=lambda s: s.ultima_ricarica_kwh,
    ),
    Descrizione(
        key="ultima_ricarica_tipo",
        device_class=SensorDeviceClass.ENUM,
        options=["ac", "dc"],
        valore=lambda s: s.ultima_ricarica_tipo,
    ),
    Descrizione(
        key="ultima_ricarica_inizio",
        device_class=SensorDeviceClass.TIMESTAMP,
        valore=lambda s: s.ultima_ricarica_inizio,
    ),
    Descrizione(
        key="ultima_ricarica_fine",
        device_class=SensorDeviceClass.TIMESTAMP,
        valore=lambda s: s.ultima_ricarica_fine,
    ),
    Descrizione(
        key="ricarica_inizio",
        valore=lambda s: s.ricarica_ora_inizio,
    ),
    Descrizione(
        key="ricarica_fine",
        valore=lambda s: s.ricarica_ora_fine,
    ),
    *(
        Descrizione(
            key=f"pneumatici_{sigla}",
            device_class=SensorDeviceClass.PRESSURE,
            native_unit_of_measurement=UnitOfPressure.KPA,
            suggested_unit_of_measurement=UnitOfPressure.BAR,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            entity_category=EntityCategory.DIAGNOSTIC,
            valore=lambda s, r=ruota: s.pneumatici_kpa.get(r),
        )
        for ruota, sigla in _RUOTE.items()
    ),
    Descrizione(
        key="ultimo_aggiornamento",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        valore=lambda s: s.letto,
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant, entry: GdaautoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for auto in entry.runtime_data.auto:
        voci: list[Voce] = [
            (d.key, lambda a, d=d: d.valore(a.data) is not None, lambda auto=auto, d=d: SensoreGdaauto(auto, d))
            for d in DESCRIZIONI
        ]
        aggiungi_quando_pronte(entry, async_add_entities, auto, voci)


class SensoreGdaauto(EntitaGdaauto, SensorEntity):
    _dominio = "sensor"
    entity_description: Descrizione

    def __init__(self, auto: Auto, descrizione: Descrizione) -> None:
        self.entity_description = descrizione
        super().__init__(auto, descrizione.key)

    @property
    def native_value(self) -> Any:
        s = self.stato
        if s is None:
            return None
        valore = self.entity_description.valore(s)
        # 85.0 si legge «85»: i cloud mandano interi come numeri con la virgola.
        if isinstance(valore, float) and valore.is_integer():
            return int(valore)
        return valore
