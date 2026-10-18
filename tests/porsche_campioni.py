"""Risposte del cloud My Porsche per le prove.

La forma (``measurements`` con ``key``, ``status.isEnabled`` e ``value``) e i
nomi dei campi sono quelli che legge CJNE/pyporscheconnectapi (MIT) e che
usano le prove di CJNE/ha-porscheconnect; i valori sono inventati. I VIN sono
finti.
"""

from __future__ import annotations

from typing import Any

VIN_TAYCAN = "WP0ZZZY1ZPSA00001"
VIN_CAYENNE = "WP1ZZZ9YZRDA00002"
VIN_MACAN = "WP1ZZZXAZRLA00003"


def _m(key: str, value: dict[str, Any] | None, acceso: bool = True) -> dict[str, Any]:
    voce: dict[str, Any] = {"key": key, "status": {"isEnabled": acceso}}
    if value is not None:
        voce["value"] = value
    return voce


ELENCO = [
    {
        "vin": VIN_TAYCAN,
        "modelName": "Taycan 4S",
        "customName": "La Blu",
        "modelType": {"code": "Y1ADE1", "year": "2023", "body": "SEDAN", "engine": "BEV", "model": "TAYCAN"},
        "connect": True,
        "relationship": "OWNER",
        "timestamp": "2026-10-18T09:00:00Z",
    },
    {
        "vin": VIN_CAYENNE,
        "modelName": "Cayenne E-Hybrid",
        "modelType": {"code": "9YBBS1", "year": "2024", "engine": "PHEV", "model": "CAYENNE"},
        "connect": True,
        "relationship": "SECONDARY_USER",
    },
]

MACAN = {
    "vin": VIN_MACAN,
    "modelName": "Macan 4 Electric",
    "modelType": {"year": "2025", "engine": "BEV", "model": "MACAN"},
}

#: Taycan in carica a casa col profilo «Casa» (80 %), chiusa, una gomma bassa.
TAYCAN_IN_CARICA = {
    "vin": VIN_TAYCAN,
    "modelName": "Taycan 4S",
    "customName": "La Blu",
    "modelType": ELENCO[0]["modelType"],
    "timestamp": "2026-10-18T10:00:03Z",
    "measurements": [
        _m("BATTERY_LEVEL", {"percent": 63}),
        _m("E_RANGE", {"kilometers": 251}),
        _m("RANGE", {"kilometers": 251}),
        _m("MILEAGE", {"kilometers": 13247}),
        _m("FUEL_LEVEL", None, acceso=False),
        _m(
            "CHARGING_SUMMARY",
            {
                "mode": "PROFILE",
                "status": "CHARGING",
                "chargingProfile": {"id": 4, "name": "Casa", "minSoC": 80},
                "targetDateTimeWithOffset": "2026-10-18T14:30:00.000+02:00",
            },
        ),
        _m("CHARGING_RATE", {"chargingRate": 4.6, "chargingPower": 10.9}),
        _m(
            "CHARGING_PROFILES",
            {
                "list": [
                    {"id": 4, "name": "Casa", "isEnabled": True, "minSoc": 80, "chargingMode": "SMART"},
                    {"id": 5, "name": "Ufficio", "isEnabled": False, "minSoc": 60, "chargingMode": "SMART"},
                ]
            },
        ),
        _m(
            "HVAC_SUMMARY",
            {
                "type": "CLIMATIZER",
                "status": "OFF",
                "targetTemperature": 293.15,
                "climateZonesEnabled": {"frontLeft": False, "frontRight": False, "rearLeft": False, "rearRight": False},
            },
        ),
        _m("LOCK_STATE_VEHICLE", {"isLocked": True}),
        _m("OPEN_STATE_DOOR_FRONT_LEFT", {"isOpen": False}),
        _m("OPEN_STATE_DOOR_FRONT_RIGHT", {"isOpen": False}),
        _m("OPEN_STATE_DOOR_REAR_LEFT", {"isOpen": False}),
        _m("OPEN_STATE_DOOR_REAR_RIGHT", {"isOpen": False}),
        _m("OPEN_STATE_LID_FRONT", {"isOpen": False}),
        _m("OPEN_STATE_LID_REAR", {"isOpen": False}),
        _m("OPEN_STATE_WINDOW_FRONT_LEFT", {"isOpen": False}),
        _m("OPEN_STATE_WINDOW_FRONT_RIGHT", {"isOpen": False}),
        _m("OPEN_STATE_WINDOW_REAR_LEFT", {"isOpen": False}),
        _m("OPEN_STATE_WINDOW_REAR_RIGHT", {"isOpen": False}),
        _m("OPEN_STATE_SUNROOF", {"isOpen": False}),
        _m("TIRE_PRESSURE_FRONT_LEFT", {"actualPressureBar": 2.6, "differenceBar": -0.1}),
        _m("TIRE_PRESSURE_FRONT_RIGHT", {"actualPressureBar": 2.6, "differenceBar": -0.1}),
        _m("TIRE_PRESSURE_REAR_LEFT", {"actualPressureBar": 2.4, "differenceBar": -0.5}),
        _m("TIRE_PRESSURE_REAR_RIGHT", {"actualPressureBar": 2.9, "differenceBar": 0.0}),
        _m(
            "GPS_LOCATION",
            {"location": "48.834323,9.152218", "direction": 192, "lastModified": "2026-10-18T09:58:12Z"},
        ),
        _m("PARKING_BRAKE", {"isOn": True}),
        _m("PARKING_LIGHT", {"isOn": False}),
        _m("GLOBAL_PRIVACY_MODE", {"isEnabled": False}),
        _m("REMOTE_ACCESS_AUTHORIZATION", {"isEnabled": True}),
        _m("GLOBAL_TIMESTAMP", {"timestamp": "2026-10-18T09:59:30Z"}),
        _m("MAIN_SERVICE_RANGE", {"kilometers": 16753}),
    ],
}

#: Cayenne E-Hybrid ferma senza cavo, in ricarica immediata, a Lisbona, aperta, col clima acceso.
CAYENNE_FERMA = {
    "vin": VIN_CAYENNE,
    "modelName": "Cayenne E-Hybrid",
    "modelType": ELENCO[1]["modelType"],
    "measurements": [
        _m("BATTERY_LEVEL", {"percent": 100}),
        _m("E_RANGE", {"kilometers": 72}),
        _m("RANGE", {"kilometers": 610}),
        _m("FUEL_LEVEL", {"percent": 54}),
        _m("MILEAGE", {"kilometers": 8120}),
        _m("CHARGING_SUMMARY", {"mode": "DIRECT", "status": "NOT_PLUGGED", "minSoC": None}),
        _m("CHARGING_RATE", {"chargingRate": 0, "chargingPower": 0}),
        _m("HVAC_SUMMARY", {"type": "CLIMATIZER", "status": "ON", "targetTemperature": 295.15, "remainingTime": 12}),
        _m("LOCK_STATE_VEHICLE", {"isLocked": False}),
        _m("OPEN_STATE_DOOR_FRONT_LEFT", {"isOpen": True}),
        _m("OPEN_STATE_DOOR_FRONT_RIGHT", {"isOpen": False}),
        _m("OPEN_STATE_LID_REAR", {"isOpen": False}),
        _m("OPEN_STATE_SUNROOF", {"isOpen": True}),
        _m("GPS_LOCATION", {"location": "38.722252,-9.139337", "direction": 0, "lastModified": "2026-10-17T18:02:00Z"}),
        # La modalità privacy spegne le gomme: non si sa, non è zero.
        _m("TIRE_PRESSURE_FRONT_LEFT", None, acceso=False),
        _m("PARKING_BRAKE", {"isOn": False}),
    ],
}

#: Macan Electric con le partenze: il limite è nelle impostazioni di ricarica.
MACAN_COMPLETATA = {
    "vin": VIN_MACAN,
    "modelName": "Macan 4 Electric",
    "modelType": MACAN["modelType"],
    "measurements": [
        _m("BATTERY_LEVEL", {"percent": 85}),
        _m("E_RANGE", {"kilometers": 447}),
        _m("CHARGING_SUMMARY", {"mode": "TIMER", "status": "CHARGING_COMPLETED", "targetSoC": None}),
        _m("CHARGING_SETTINGS", {"targetSoc": 85, "autoPlug": False}),
        _m("DEPARTURES", {"list": [{"id": 1, "isEnabled": True, "dateTime": "2026-10-19T07:30:00Z"}]}),
        _m("CHARGING_RATE", {"chargingRate": 0, "chargingPower": 0}),
    ],
}

#: I comandi (``cf=…``): la Taycan li ha tutti; il clima dice i suoi limiti in kelvin.
CAPACITA_TAYCAN = {
    "vin": VIN_TAYCAN,
    "measurements": [_m("REMOTE_ACCESS_AUTHORIZATION", {"isEnabled": True})],
    "commands": [
        {"key": "LOCK", "status": {"isEnabled": True}},
        {"key": "UNLOCK", "status": {"isEnabled": True}},
        {"key": "SPIN_CHALLENGE", "status": {"isEnabled": True}},
        {"key": "HONK_FLASH", "status": {"isEnabled": True}},
        {
            "key": "REMOTE_CLIMATIZER_START",
            "status": {"isEnabled": True},
            "metadata": {"minTemperature": 288.15, "maxTemperature": 303.15},
        },
        {"key": "REMOTE_CLIMATIZER_STOP", "status": {"isEnabled": True}},
        {"key": "DIRECT_CHARGING_START", "status": {"isEnabled": True}},
        {"key": "DIRECT_CHARGING_STOP", "status": {"isEnabled": True}},
        {"key": "CHARGING_PROFILES_EDIT", "status": {"isEnabled": True}},
        {"key": "CHARGING_SETTINGS_EDIT", "status": {"isEnabled": False, "cause": "NOT_SUPPORTED"}},
    ],
}

#: La Cayenne di un utente secondario: i servizi remoti sono spenti.
CAPACITA_CAYENNE = {
    "vin": VIN_CAYENNE,
    "measurements": [_m("REMOTE_ACCESS_AUTHORIZATION", {"isEnabled": False})],
    "commands": [{"key": "LOCK", "status": {"isEnabled": False, "cause": "NO_REMOTE_ACCESS"}}],
}

#: Le statistiche dei viaggi (forma supposta: vedi il resoconto).
VIAGGI = {
    "vin": VIN_TAYCAN,
    "measurements": [
        _m(
            "TRIP_STATISTICS_LONG_TERM",
            {
                "averageElectricEngineConsumption": {"kilowattHoursPer100Kilometers": 23.4},
                "averageSpeed": {"kilometersPerHour": 48},
            },
        ),
    ],
}
