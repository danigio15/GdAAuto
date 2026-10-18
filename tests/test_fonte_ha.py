"""Il riconoscimento delle auto che hanno già un'integrazione, con i nomi veri.

Gli elenchi vengono dalle integrazioni come le pubblicano: Tesla Fleet (nel
nucleo di Home Assistant), Stellantis Vehicles (HACS, Peugeot, Fiat, Opel,
Citroën, Jeep…), Kia/Hyundai (kia_uvo) e Renault. I nomi sono gli
``original_name``: quelli che il registro delle entità tiene senza il nome
del dispositivo davanti.
"""

from __future__ import annotations

from custom_components.gdaauto import fonte_ha as f
from custom_components.gdaauto.fonte_ha import Entita as E

TESLA = [
    E("sensor.model_y_battery_level", "Battery level", "%", "battery"),
    E("sensor.model_y_battery_range", "Battery range", "mi", "distance"),
    E("sensor.model_y_charging", "Charging", None, "enum"),
    E("sensor.model_y_charger_power", "Charger power", "kW", "power"),
    E("sensor.model_y_charge_rate", "Charge rate", "mph", "speed"),
    E("sensor.model_y_time_to_full_charge", "Time to full charge", None, "timestamp"),
    E("sensor.model_y_odometer", "Odometer", "mi", "distance"),
    E("sensor.model_y_speed", "Speed", "mph", "speed"),
    E("sensor.model_y_inside_temperature", "Inside temperature", "°C", "temperature"),
    E("sensor.model_y_outside_temperature", "Outside temperature", "°C", "temperature"),
    E("binary_sensor.model_y_charge_cable", "Charge cable", None, "connectivity"),
    E("lock.model_y_charge_cable_lock", "Charge cable lock"),
    E("lock.model_y_lock", "Lock"),
    E("climate.model_y_climate", "Climate"),
    E("switch.model_y_charge", "Charge"),
    E("switch.model_y_sentry_mode", "Sentry mode"),
    E("number.model_y_charge_limit", "Charge limit", "%", "battery"),
    E("number.model_y_charge_current", "Charge current", "A", "current"),
    E("button.model_y_honk_horn", "Honk horn"),
    E("button.model_y_flash_lights", "Flash lights"),
    E("device_tracker.model_y_route", "Route"),
    E("device_tracker.model_y_location", "Location"),
]

STELLANTIS = [
    E("sensor.e_208_battery", "Battery", "%", "battery"),
    E("sensor.e_208_battery_soh", "Battery soh", "%"),
    E("sensor.e_208_autonomy", "Autonomy", "km", "distance"),
    E("sensor.e_208_mileage", "Mileage", "km", "distance"),
    E("sensor.e_208_battery_charging_rate", "Battery charging rate", "km/h", "speed"),
    E("sensor.e_208_battery_charging_end", "Battery charging end", None, "timestamp"),
    E("sensor.e_208_temperature", "Temperature", "°C", "temperature"),
    E("binary_sensor.e_208_battery_plugged", "Battery plugged", None, "plug"),
    E("binary_sensor.e_208_battery_charging", "Battery charging", None, "battery_charging"),
    E("number.e_208_battery_charging_limit", "Battery charging limit", "%"),
    E("switch.e_208_air_conditioning", "Air conditioning"),
    E("switch.e_208_battery_charging", "Battery charging"),
    E("button.e_208_horn", "Horn"),
    E("button.e_208_lights", "Lights"),
    E("lock.e_208_doors", "Doors"),
    E("device_tracker.e_208", "E-208"),
]

KIA = [
    E("sensor.ev6_car_battery_level", "Car Battery Level", "%", "battery"),
    E("sensor.ev6_ev_battery_level", "EV Battery Level", "%", "battery"),
    E("sensor.ev6_ev_range", "EV Range", "km", "distance"),
    E("sensor.ev6_total_driving_range", "Total Driving Range", "km", "distance"),
    E("sensor.ev6_odometer", "Odometer", "km", "distance"),
    E("sensor.ev6_estimated_charge_duration", "Estimated Charge Duration", "min", "duration"),
    E("binary_sensor.ev6_ev_battery_charge", "EV Battery Charge", None, "battery_charging"),
    E("binary_sensor.ev6_ev_battery_plug", "EV Battery Plug", None, "plug"),
    E("binary_sensor.ev6_hood", "Hood", None, "door"),
    E("lock.ev6_door_lock", "Door Lock"),
    E("number.ev6_ac_charging_limit", "AC Charging Limit", "%"),
    E("number.ev6_dc_charging_limit", "DC Charging Limit", "%"),
    E("device_tracker.ev6_location", "Location"),
]

RENAULT = [
    E("sensor.zoe_battery", "Battery", "%", "battery"),
    E("sensor.zoe_battery_autonomy", "Battery autonomy", "km", "distance"),
    E("sensor.zoe_mileage", "Mileage", "km", "distance"),
    E("sensor.zoe_charge_state", "Charge state", None, "enum"),
    E("sensor.zoe_plug_state", "Plug state", None, "enum"),
    E("sensor.zoe_charging_remaining_time", "Charging remaining time", "min", "duration"),
    E("sensor.zoe_admissible_charging_power", "Admissible charging power", "kW", "power"),
    E("sensor.zoe_battery_temperature", "Battery temperature", "°C", "temperature"),
    E("sensor.zoe_outside_temperature", "Outside temperature", "°C", "temperature"),
    E("binary_sensor.zoe_charging", "Charging", None, "battery_charging"),
    E("binary_sensor.zoe_plug", "Plug", None, "plug"),
    E("button.zoe_start_air_conditioner", "Start air conditioner"),
    E("button.zoe_start_charge", "Start charge"),
    E("device_tracker.zoe_location", "Location"),
]


def test_tesla() -> None:
    assert f.proponi(TESLA) == {
        f.BATTERIA: "sensor.model_y_battery_level",
        f.AUTONOMIA: "sensor.model_y_battery_range",
        f.IN_CARICA: "sensor.model_y_charging",
        f.CAVO: "binary_sensor.model_y_charge_cable",
        f.POTENZA_RICARICA: "sensor.model_y_charger_power",
        f.TEMPO_RICARICA: "sensor.model_y_time_to_full_charge",
        f.ODOMETRO: "sensor.model_y_odometer",
        f.VELOCITA: "sensor.model_y_speed",
        f.TEMPERATURA_INTERNA: "sensor.model_y_inside_temperature",
        f.TEMPERATURA_ESTERNA: "sensor.model_y_outside_temperature",
        f.LIMITE: "number.model_y_charge_limit",
        f.SERRATURA: "lock.model_y_lock",
        f.CLIMA: "climate.model_y_climate",
        f.INTERRUTTORE_RICARICA: "switch.model_y_charge",
        f.TROVA: "button.model_y_honk_horn",
        f.POSIZIONE: "device_tracker.model_y_location",
    }


def test_stellantis() -> None:
    p = f.proponi(STELLANTIS)
    assert p[f.BATTERIA] == "sensor.e_208_battery"  # non la salute (soh)
    assert p[f.AUTONOMIA] == "sensor.e_208_autonomy"
    assert p[f.ODOMETRO] == "sensor.e_208_mileage"
    assert p[f.IN_CARICA] == "binary_sensor.e_208_battery_charging"
    assert p[f.CAVO] == "binary_sensor.e_208_battery_plugged"
    assert p[f.TEMPO_RICARICA] == "sensor.e_208_battery_charging_end"
    assert p[f.LIMITE] == "number.e_208_battery_charging_limit"
    assert p[f.CLIMA] == "switch.e_208_air_conditioning"
    assert p[f.INTERRUTTORE_RICARICA] == "switch.e_208_battery_charging"
    assert p[f.SERRATURA] == "lock.e_208_doors"
    assert p[f.TROVA] == "button.e_208_horn"
    assert p[f.POSIZIONE] == "device_tracker.e_208"
    # Il «charging rate» in km/h non è la velocità dell'auto.
    assert f.VELOCITA not in p


def test_kia_la_batteria_da_12_volt_non_e_quella_di_trazione() -> None:
    p = f.proponi(KIA)
    assert p[f.BATTERIA] == "sensor.ev6_ev_battery_level"
    assert p[f.AUTONOMIA] == "sensor.ev6_ev_range"
    assert p[f.IN_CARICA] == "binary_sensor.ev6_ev_battery_charge"
    assert p[f.CAVO] == "binary_sensor.ev6_ev_battery_plug"
    assert p[f.TEMPO_RICARICA] == "sensor.ev6_estimated_charge_duration"
    assert p[f.SERRATURA] == "lock.ev6_door_lock"
    assert p[f.LIMITE] == "number.ev6_ac_charging_limit"


def test_renault() -> None:
    p = f.proponi(RENAULT)
    assert p[f.BATTERIA] == "sensor.zoe_battery"
    assert p[f.AUTONOMIA] == "sensor.zoe_battery_autonomy"
    assert p[f.IN_CARICA] == "binary_sensor.zoe_charging"
    assert p[f.CAVO] == "binary_sensor.zoe_plug"
    assert p[f.TEMPO_RICARICA] == "sensor.zoe_charging_remaining_time"
    assert p[f.TEMPERATURA_BATTERIA] == "sensor.zoe_battery_temperature"
    assert p[f.TEMPERATURA_ESTERNA] == "sensor.zoe_outside_temperature"
    assert p[f.POTENZA_RICARICA] == "sensor.zoe_admissible_charging_power"


def test_nessuna_entita_due_volte() -> None:
    for elenco in (TESLA, STELLANTIS, KIA, RENAULT):
        p = f.proponi(elenco)
        assert len(set(p.values())) == len(p)


def test_le_impostazioni_non_sono_l_auto() -> None:
    p = f.proponi([E("sensor.x_battery", "Battery", "%", "battery", categoria="config")])
    assert f.BATTERIA not in p
