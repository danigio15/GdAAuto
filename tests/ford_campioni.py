"""Payload d'esempio di FordPass, nella forma che il cloud manda.

Le forme (nomi dei campi, elenchi di porte e serrature, il registro delle
ricariche) sono quelle documentate nei commenti di marq24/ha-fordpass
(Apache 2.0); i valori sono inventati.
"""

from __future__ import annotations

VIN = "3FMTK3SU0MMA00001"
VIN_KUGA = "WF0FXXWPMHNA00002"

T = "2026-10-18T10:00:00Z"

#: Una Mustang Mach-E in carica in AC, chiusa, col bagagliaio aperto.
MACH_E = {
    "updateTime": "2026-10-18T10:05:00Z",
    "metrics": {
        "xevBatteryStateOfCharge": {"updateTime": T, "value": 64.5},
        "xevBatteryActualStateOfCharge": {"updateTime": T, "value": 62.1},
        "xevBatteryRange": {"updateTime": T, "value": 287.5},
        "xevBatteryEnergyRemaining": {"updateTime": T, "value": 56.8},
        "xevBatteryCapacity": {"updateTime": T, "value": 88.0},
        "xevBatteryTemperature": {"updateTime": T, "value": 18.0},
        "xevBatteryVoltage": {"updateTime": T, "value": 372.0},
        "xevBatteryIoCurrent": {"updateTime": T, "value": -29.5},
        "xevPlugChargerStatus": {"updateTime": T, "value": "CONNECTED"},
        "xevBatteryChargeDisplayStatus": {"updateTime": T, "value": "IN_PROGRESS"},
        "xevChargeStationPowerType": {"updateTime": T, "value": "AC_BASIC"},
        "xevBatteryChargerVoltageOutput": {"updateTime": T, "value": 370.0},
        "xevBatteryChargerCurrentOutput": {"updateTime": T, "value": 29.7},
        "xevBatteryTimeToFullCharge": {"updateTime": T, "value": 95},
        "odometer": {"updateTime": T, "value": 23456.7},
        "speed": {"updateTime": T, "value": 0.0},
        "gearLeverPosition": {"updateTime": T, "value": "PARK"},
        "ignitionStatus": {"updateTime": T, "value": "OFF"},
        "parkingBrakeStatus": {"updateTime": T, "value": "ENGAGED"},
        "outsideTemperature": {"updateTime": T, "value": 14.5},
        "alarmStatus": {"updateTime": T, "value": "ARMED"},
        "remoteStartCountdownTimer": {"updateTime": T, "value": 0},
        "batteryStateOfCharge": {"updateTime": T, "value": 87},
        "batteryVoltage": {"updateTime": T, "value": 12.6, "vehicleBattery": "PRIMARY_BATTERY"},
        "position": {
            "updateTime": T,
            "value": {"location": {"lat": 45.4642, "lon": 9.19, "alt": 120.0}, "gpsDimension": "3D"},
        },
        "doorLockStatus": [
            {
                "updateTime": T,
                "value": "LOCKED",
                "vehicleDoor": "UNSPECIFIED_FRONT",
                "vehicleOccupantRole": "DRIVER",
                "vehicleSide": "DRIVER",
            },
            {"updateTime": T, "value": "LOCKED", "vehicleDoor": "ALL_DOORS"},
        ],
        "doorStatus": [
            {"updateTime": T, "value": "CLOSED", "vehicleDoor": "UNSPECIFIED_FRONT", "vehicleSide": "DRIVER"},
            {"updateTime": T, "value": "CLOSED", "vehicleDoor": "UNSPECIFIED_FRONT", "vehicleSide": "PASSENGER"},
            {"updateTime": T, "value": "CLOSED", "vehicleDoor": "UNSPECIFIED_REAR", "vehicleSide": "DRIVER"},
            {"updateTime": T, "value": "CLOSED", "vehicleDoor": "UNSPECIFIED_REAR", "vehicleSide": "PASSENGER"},
            {"updateTime": T, "value": "AJAR", "vehicleDoor": "TAILGATE"},
            {"updateTime": T, "value": "CLOSED", "vehicleDoor": "FRUNK"},
        ],
        "windowStatus": [
            {
                "updateTime": T,
                "value": {"doubleRange": {"lowerBound": 0.0, "upperBound": 0.0}},
                "vehicleWindow": "UNSPECIFIED_FRONT",
                "vehicleSide": "DRIVER",
            },
            {
                "updateTime": T,
                "value": {"doubleRange": {"lowerBound": 0.0, "upperBound": 0.0}},
                "vehicleWindow": "UNSPECIFIED_FRONT",
                "vehicleSide": "PASSENGER",
            },
        ],
        "tirePressure": [
            {"updateTime": T, "value": 262.0, "vehicleWheel": "FRONT_LEFT"},
            {"updateTime": T, "value": 260.0, "vehicleWheel": "FRONT_RIGHT"},
            {"updateTime": T, "value": 255.0, "vehicleWheel": "REAR_LEFT"},
            {"updateTime": T, "value": 258.0, "vehicleWheel": "REAR_RIGHT"},
        ],
        "tirePressureStatus": [
            {"updateTime": T, "value": "NORMAL", "vehicleWheel": "FRONT_LEFT"},
            {"updateTime": T, "value": "NORMAL", "vehicleWheel": "REAR_RIGHT"},
        ],
        "customMetrics": {
            "fordpass:custom:global-ac-target-soc": {"updateTime": T, "value": 80},
            "fordpass:custom:global-ac-current-limit": {"updateTime": T, "value": 32},
        },
    },
    "states": {
        "commandPreclusion": {"timestamp": T, "value": {"toState": "COMMANDS_PERMITTED"}},
    },
    "events": {
        "customEvents": {
            "xev-hv-battery-monitoring": {"updateTime": T, "oemData": {"target_soc": {"longValue": 90}}},
        }
    },
}

#: Il registro delle ricariche (``energy-transfer-logs``), la più recente per prima.
ULTIME_RICARICHE = {
    "energyTransferLogs": [
        {
            "id": "voce-1",
            "deviceId": VIN,
            "eventType": "ChargeData",
            "chargerType": "AC_BASIC",
            "energyConsumed": 60.19749,
            "timeStamp": "2026-10-17T07:58:23Z",
            "targetSoc": 100,
            "plugDetails": {
                "plugInTime": "2026-10-16T16:03:54Z",
                "plugOutTime": "2026-10-17T07:57:20Z",
                "totalDistanceAdded": 367.5,
            },
            "stateOfCharge": {"firstSOC": 28, "lastSOC": 100, "socDifference": 72},
            "energyTransferDuration": {
                "begin": "2026-10-16T16:05:09Z",
                "end": "2026-10-17T04:33:05Z",
                "totalTime": 21138,
            },
        }
    ]
}

PROFILO_CLIMA = {
    "rccUserProfiles": [
        {"preferenceType": "RccHeatedWindshield_Rq", "preferenceValue": "Off"},
        {"preferenceType": "SetPointTemp_Rq", "preferenceValue": "21_5"},
    ]
}

ORARI_RICARICA = [
    {
        "vin": VIN,
        "chargeProfile": {"chargeMode": "VALUE_CHARGE", "schedules": [], "targetSoc": 85},
        "location": {"id": 1, "name": "Casa", "type": "SAVED"},
    }
]

#: Una Kuga plug-in, ferma e scollegata, con un finestrino socchiuso e una portiera aperta.
KUGA = {
    "updateTime": "2026-10-18T09:00:00Z",
    "metrics": {
        "xevBatteryStateOfCharge": {"updateTime": T, "value": 40},
        "xevBatteryRange": {"updateTime": T, "value": 22},
        "xevPlugChargerStatus": {"updateTime": T, "value": "DISCONNECTED"},
        "xevBatteryChargeDisplayStatus": {"updateTime": T, "value": "NOT_READY"},
        "fuelLevel": {"updateTime": T, "value": 55.2},
        "fuelRange": {"updateTime": T, "value": 410.0},
        "odometer": {"updateTime": T, "value": 51000},
        "speed": {"updateTime": T, "value": 13.9},
        "gearLeverPosition": {"updateTime": T, "value": "DRIVE"},
        "ignitionStatus": {"updateTime": T, "value": "ON"},
        "doorLockStatus": [
            {"updateTime": T, "value": "LOCKED", "vehicleDoor": "FRONT_LEFT"},
            {"updateTime": T, "value": "UNLOCKED", "vehicleDoor": "FRONT_RIGHT"},
        ],
        "doorStatus": [
            {"updateTime": T, "value": "OPEN", "vehicleDoor": "UNSPECIFIED_REAR", "vehicleSide": "PASSENGER"},
            {"updateTime": T, "value": "CLOSED", "vehicleDoor": "TAILGATE"},
        ],
        "windowStatus": [{"updateTime": T, "value": "Btwn 10% and 60% open", "vehicleWindow": "UNSPECIFIED_FRONT"}],
        "tirePressureStatus": [{"updateTime": T, "value": "LOW", "vehicleWheel": "REAR_LEFT"}],
        "remoteStartCountdownTimer": {"updateTime": "2026-10-18T09:58:00Z", "value": 900},
    },
    "states": {"commandPreclusion": {"value": {"toState": "COMMANDS_PRECLUDED"}}},
}

#: Il garage dell'account (``fpcpl-user-garage-service``).
GARAGE = [
    {
        "vin": VIN,
        "profile": {"model": "Mustang Mach-E", "year": "2023", "nickName": "Saetta", "engineType": "BEV"},
        "capabilities": {
            "remoteLock": "Display",
            "remoteStart": "Display",
            "remotePanicAlarm": "Display",
            "remoteClimateControl": "Display",
        },
    },
    {
        "vin": "WF0XXXGCDXXX00003",
        "profile": {"model": "Fiesta", "year": "2019", "engineType": "ICE"},
        "capabilities": {"remoteLock": "Display"},
    },
]
