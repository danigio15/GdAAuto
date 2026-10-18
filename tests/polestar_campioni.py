"""Risposte d'esempio dell'API GraphQL di Polestar.

``POLESTAR2``, ``POLESTAR3`` e ``POLESTAR4`` sono i fixture anonimi di
pypolestar/pypolestar (``tests/data/polestar{2,3,4}.json``, licenza MIT,
Copyright (c) 2023 Tuen Lee), senza le immagini. ``IN_CARICA_V2`` è scritto
qui, con i campi della query ``carTelematicsV2`` di oggi.
"""

from __future__ import annotations

from typing import Any

# Una Polestar 2 ferma, nella forma vecchia (``carTelematics`` e ``get…Data``).
POLESTAR2: dict[str, Any] = {
    "getConsumerCarsV2": {
        "vin": "AAAAAAAA1AA111111",
        "internalVehicleIdentifier": "88888888-aaaa-bbbb-cccc-aaa11aaa1111",
        "registrationNo": "AA-00-AA",
        "modelYear": "0000",
        "modelName": "Polestar 2",
    },
    "getOdometerData": {
        "averageSpeedKmPerHour": 32,
        "eventUpdatedTimestamp": {"iso": "2025-01-28T08:36:45.000Z", "unix": "1738053405"},
        "odometerMeters": 24790000,
        "tripMeterAutomaticKm": 6.5,
        "tripMeterManualKm": 4772.5,
    },
    "getBatteryData": {
        "averageEnergyConsumptionKwhPer100Km": 17.5,
        "batteryChargeLevelPercentage": 68,
        "chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_DISCONNECTED",
        "chargingCurrentAmps": None,
        "chargingPowerWatts": None,
        "chargingStatus": "CHARGING_STATUS_IDLE",
        "estimatedChargingTimeMinutesToTargetDistance": None,
        "estimatedChargingTimeToFullMinutes": 0,
        "estimatedDistanceToEmptyKm": 280,
        "estimatedDistanceToEmptyMiles": 170,
        "eventUpdatedTimestamp": {"iso": "2025-01-28T08:44:34.000Z", "unix": "1738053874"},
    },
    "carTelematics": {
        "health": {
            "brakeFluidLevelWarning": "BRAKE_FLUID_LEVEL_WARNING_NO_WARNING",
            "daysToService": 196,
            "distanceToServiceKm": 5227,
            "engineCoolantLevelWarning": "ENGINE_COOLANT_LEVEL_WARNING_NO_WARNING",
            "eventUpdatedTimestamp": {"iso": "2025-01-28T08:36:45.000Z", "unix": "1738053405"},
            "oilLevelWarning": "OIL_LEVEL_WARNING_NO_WARNING",
            "serviceWarning": "SERVICE_WARNING_NO_WARNING",
        },
        "battery": {
            "averageEnergyConsumptionKwhPer100Km": 17.5,
            "batteryChargeLevelPercentage": 68.0,
            "chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_DISCONNECTED",
            "chargingCurrentAmps": None,
            "chargingPowerWatts": None,
            "chargingStatus": "CHARGING_STATUS_IDLE",
            "estimatedChargingTimeMinutesToTargetDistance": None,
            "estimatedChargingTimeToFullMinutes": 0,
            "estimatedDistanceToEmptyKm": 280,
            "estimatedDistanceToEmptyMiles": 170,
            "eventUpdatedTimestamp": {"iso": "2025-01-28T08:44:34.000Z", "unix": "1738053874"},
        },
        "odometer": {
            "averageSpeedKmPerHour": 32,
            "eventUpdatedTimestamp": {"iso": "2025-01-28T08:36:45.000Z", "unix": "1738053405"},
            "odometerMeters": 24790000,
            "tripMeterAutomaticKm": 6.5,
            "tripMeterManualKm": 4772.5,
        },
    },
}

# Una Polestar 3 nella forma di oggi (``carTelematicsV2``, liste per VIN).
POLESTAR3: dict[str, Any] = {
    "getConsumerCarsV2": {
        "vin": "YSMYKEAE7RB000000",
        "internalVehicleIdentifier": "1aaeb452-700e-46f3-9899-395b6219c8a6",
        "registrationNo": "MLB007",
        "modelYear": "0000",
        "modelName": "Polestar 3",
    },
    "carTelematicsV2": {
        "health": [None],
        "battery": [
            {
                "vin": "YSMYKEAE7RB000000",
                "batteryChargeLevelPercentage": 79,
                "chargingStatus": "CHARGING_STATUS_IDLE",
                "estimatedChargingTimeToFullMinutes": 0,
                "estimatedDistanceToEmptyKm": 390,
                "timestamp": {"seconds": "1747822967", "nanos": 996856149},
            }
        ],
        "odometer": [
            {
                "vin": "YSMYKEAE7RB000000",
                "odometerMeters": 11131000,
                "timestamp": {"seconds": "1747765507", "nanos": 288842041},
            }
        ],
    },
}

# Una Polestar 4: il cloud scrive il modello attaccato («Polestar4»).
POLESTAR4: dict[str, Any] = {
    "getConsumerCarsV2": {
        "vin": "XXXXXXXXXXX000000",
        "internalVehicleIdentifier": "cf4bfecc-cb00-49f3-af84-4a5b21b02da6",
        "registrationNo": "MLB007",
        "modelYear": "0000",
        "modelName": "Polestar4",
    },
    "carTelematics": {
        "health": {
            "brakeFluidLevelWarning": "BRAKE_FLUID_LEVEL_WARNING_NO_WARNING",
            "daysToService": 601,
            "distanceToServiceKm": 26515,
            "engineCoolantLevelWarning": None,
            "eventUpdatedTimestamp": {"iso": "2025-02-15T08:01:37.000Z", "unix": "1739606497"},
            "oilLevelWarning": None,
            "serviceWarning": "SERVICE_WARNING_NO_WARNING",
        },
        "battery": {
            "averageEnergyConsumptionKwhPer100Km": None,
            "batteryChargeLevelPercentage": 42,
            "chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_DISCONNECTED",
            "chargingCurrentAmps": None,
            "chargingPowerWatts": None,
            "chargingStatus": "CHARGING_STATUS_IDLE",
            "estimatedChargingTimeMinutesToTargetDistance": None,
            "estimatedChargingTimeToFullMinutes": 74,
            "estimatedDistanceToEmptyKm": 261,
            "estimatedDistanceToEmptyMiles": 162,
            "eventUpdatedTimestamp": {"iso": "2025-02-15T08:01:37.000Z", "unix": "1739606497"},
        },
        "odometer": {
            "averageSpeedKmPerHour": None,
            "eventUpdatedTimestamp": {"iso": "2025-02-15T08:01:37.000Z", "unix": "1739606497"},
            "odometerMeters": 3525000,
            "tripMeterAutomaticKm": 9,
            "tripMeterManualKm": 3459,
        },
    },
}

VIN3 = POLESTAR3["getConsumerCarsV2"]["vin"]
VIN_ALTRA = "LPSVSEDEEML000002"

# La risposta di oggi per due auto in carica: è la query del cliente, campo per campo.
IN_CARICA_V2: dict[str, Any] = {
    "carTelematicsV2": {
        "battery": [
            {
                "vin": VIN_ALTRA,
                "chargingStatusV2": "CHARGING_STATUS_V2_IDLE",
                "batteryChargeLevelPercentage": 90,
                "estimatedChargingTimeToFullMinutes": 0,
                "estimatedDistanceToEmptyKm": 410,
                "timestamp": {"seconds": "1792300000", "nanos": 0},
            },
            {
                "vin": VIN3,
                "chargingStatusV2": "CHARGING_STATUS_V2_CHARGING",
                "batteryChargeLevelPercentage": 54,
                "estimatedChargingTimeToFullMinutes": 95,
                "estimatedDistanceToEmptyKm": 268,
                "timestamp": {"seconds": "1792320000", "nanos": 120000000},
            },
        ],
        "odometer": [
            {"vin": VIN3, "odometerMeters": 11250400, "timestamp": {"seconds": "1792310000", "nanos": 0}},
        ],
    }
}

# Una vecchia risposta di una Polestar 2 in carica, con cavo e potenza.
IN_CARICA_V1: dict[str, Any] = {
    "carTelematics": {
        "battery": {
            "batteryChargeLevelPercentage": 35,
            "chargerConnectionStatus": "CHARGER_CONNECTION_STATUS_CONNECTED",
            "chargingCurrentAmps": 16,
            "chargingPowerWatts": 10900,
            "chargingStatus": "CHARGING_STATUS_CHARGING",
            "estimatedChargingTimeToFullMinutes": 240,
            "estimatedDistanceToEmptyKm": None,
            "estimatedDistanceToEmptyMiles": 100,
            "eventUpdatedTimestamp": {"iso": "2025-01-28T20:00:00.000Z", "unix": None},
        },
        "odometer": {"odometerMeters": 24800500},
    }
}
