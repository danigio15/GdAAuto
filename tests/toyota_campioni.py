"""Risposte d'esempio del cloud Toyota Connected Europe.

Hanno la forma dei fixture di pytoyoda (``tests/unit_tests/data``, MIT):
la RAV4 plug-in del loro ``v2_vehicleguid.json`` con il suo stato elettrico,
la telemetria e la posizione; lo stato delle portiere di
``v1_global_remote_status.json``; il clima acceso dei loro test. La bZ4X in
carica e la Yaris ibrida sono le stesse forme con altri valori.
"""

from __future__ import annotations

import base64
import json
from typing import Any

VIN_RAV4 = "JTMGBRFV80D109752"
VIN_BZ4X = "JTMABABA00A000001"
VIN_YARIS = "VNKKBAC3X0A000002"
UUID = "12345678-1234-1234-1234-123456789012"


def jwt(**corpo: Any) -> str:
    return "x." + base64.urlsafe_b64encode(json.dumps(corpo).encode()).decode().rstrip("=") + ".y"


def _capacita(**si: bool) -> dict[str, Any]:
    est = {
        "econnectVehicleStatusCapable": False,
        "vehicleStatus": False,
        "telemetryCapable": True,
        "lastParkedCapable": True,
        "climateCapable": False,
        "econnectClimateCapable": False,
        "remoteEngineStartStop": False,
        "doorLockUnlockCapable": False,
    }
    est.update(si)
    return est


RAV4 = {
    "brand": "T",
    "carlineName": "RAV4 PHEV",
    "color": "Dark Blue Metallic",
    "evVehicle": True,
    "fuelType": "I",
    "modelName": "RAV4 Plug-in MY21",
    "modelYear": "2020",
    "nickName": "RAV4",
    "owner": True,
    "region": "EU",
    "registrationNumber": None,
    "remoteSubscriptionStatus": "ACTIVE",
    "vin": VIN_RAV4,
    "extendedCapabilities": _capacita(econnectVehicleStatusCapable=True, econnectClimateCapable=True),
    "features": {"climateStartEngine": 0, "doorLockCapable": 0, "lastParked": 1, "telemetry": 1, "vehicleStatus": 0},
    "remoteServiceCapabilities": {"dlockUnlockCapable": False, "estartStopCapable": False},
}

BZ4X = {
    "brand": "T",
    "carlineName": "bZ4X",
    "color": "Precious Silver",
    "evVehicle": True,
    "fuelType": "E",
    "modelName": "bZ4X MY23",
    "modelYear": "2023",
    "nickName": "Bianca",
    "owner": True,
    "region": "EU",
    "registrationNumber": "GX123YZ",
    "vin": VIN_BZ4X,
    "extendedCapabilities": _capacita(
        econnectVehicleStatusCapable=True, vehicleStatus=True, climateCapable=True, doorLockUnlockCapable=True
    ),
    "features": {"climateStartEngine": 1, "doorLockCapable": 1, "lastParked": 1, "telemetry": 1, "vehicleStatus": 1},
    "remoteServiceCapabilities": {"dlockUnlockCapable": True},
}

YARIS = {
    "brand": "T",
    "carlineName": "Yaris",
    "evVehicle": False,
    "fuelType": "B",
    "modelName": "Yaris Hybrid",
    "modelYear": "2021",
    "nickName": "Yaris",
    "vin": VIN_YARIS,
    "extendedCapabilities": _capacita(),
    "features": {"lastParked": 1, "telemetry": 1},
}

GARAGE = {"payload": [RAV4, BZ4X, YARIS], "status": "SUCCESS", "code": 200, "message": "Ok", "errors": []}

_RIUSCITA = {
    "messages": [
        {
            "description": "Request Completed Successfully",
            "detailedDescription": "Request Completed Successfully",
            "responseCode": "ONE-GLOBAL-RS-10000",
        }
    ]
}

ELETTRICO_RAV4 = {
    "payload": {
        "batteryLevel": 79,
        "phevUsableBatteryLevel": 74,
        "canSetNextChargingEvent": True,
        "chargingStatus": "none",
        "evRange": {"unit": "km", "value": 57.3},
        "evRangeWithAc": {"unit": "km", "value": 54.434999999999995},
        "fuelLevel": 41,
        "fuelRange": {"unit": "mi", "value": 188.0},
        "lastUpdateTimestamp": "2023-11-27T17:53:59Z",
    },
    "status": _RIUSCITA,
}

ELETTRICO_BZ4X = {
    "payload": {
        "batteryLevel": 62,
        "chargingStatus": "charging",
        "evRange": {"unit": "km", "value": 281.0},
        "evRangeWithAc": {"unit": "km", "value": 262.0},
        "remainingChargeTime": 95,
        "lastUpdateTimestamp": "2026-10-18T07:40:00Z",
    },
    "status": _RIUSCITA,
}

TELEMETRIA_RAV4 = {
    "payload": {
        "fuelType": "B",
        "odometer": {"value": 30420, "unit": "km"},
        "fuelLevel": 99,
        "distanceToEmpty": {"value": 634, "unit": "km"},
        "timestamp": "2023-11-28T09:45:40Z",
    },
    "status": "SUCCESS",
    "code": 200,
    "message": "Ok",
    "errors": [],
}

TELEMETRIA_BZ4X = {
    "payload": {
        "fuelType": "E",
        "odometer": {"value": 18250, "unit": "km"},
        "batteryLevel": 62,
        "distanceToEmpty": {"value": 281, "unit": "km"},
        "timestamp": "2026-10-18T07:41:00Z",
    },
    "status": "SUCCESS",
    "code": 200,
    "message": "Ok",
    "errors": [],
}

POSIZIONE = {
    "code": 200,
    "errors": [],
    "message": "Ok",
    "payload": {
        "lastTimestamp": "2023-11-27T07:42:41Z",
        "vehicleLocation": {
            "displayName": "Last Parked",
            "latitude": 52.169516,
            "locationAcquisitionDatetime": "2023-11-27T07:42:41Z",
            "longitude": 0.135654,
        },
        "vin": VIN_RAV4,
    },
    "status": "SUCCESS",
}

_T = "2026-06-30T20:29:07.000Z"


def _stato(valore: str) -> dict[str, str]:
    return {"lastUpdateTimestamp": _T, "status": valore}


def _porta(serratura: str | None = "locked", apertura: str = "close") -> dict[str, Any]:
    porta: dict[str, Any] = {"openStatus": _stato(apertura)}
    if serratura:
        porta["lockStatus"] = _stato(serratura)
    return porta


STATO = {
    "status": {
        "serviceIdentifier": "vehicle-status-service",
        "messages": [
            {
                "responseCode": "CTP-GENERIC-20001",
                "description": "Success",
                "detailedDescription": "Request successfully completed",
            }
        ],
    },
    "payload": {
        "vin": VIN_BZ4X,
        "lastUpdateTimestamp": _T,
        "overallStatus": "ok",
        "overallWarningCounts": 0,
        "leftHandDrive": True,
        "doors": {
            "driver": _porta(),
            "passenger": _porta(),
            "rearLeft": _porta(),
            "rearRight": _porta(),
            "rearBack": _porta(None),
            "hood": _porta(None),
        },
        "windows": {
            "driver": _stato("close"),
            "passenger": _stato("close"),
            "rearLeft": _stato("close"),
            "rearRight": _stato("close"),
        },
        "lights": {"hazard": _stato("off"), "tail": _stato("off"), "head": _stato("off")},
        "rearSeatReminder": {"lastUpdateTimestamp": _T, "warning": False, "reason": "notDetected"},
    },
}

CLIMA_ACCESO = {
    "status": {"messages": []},
    "payload": {
        "status": "running",
        "startedAt": "2026-07-01T07:30:00Z",
        "updatedAt": "2026-07-01T07:36:00Z",
        "duration": 20,
        "currentTemperature": {"value": 21.5, "unit": "C"},
        "targetTemperature": {"value": 22.0, "unit": "C"},
        "heatingOptions": {"frontDefroster": "on", "rearDefogger": "off", "steeringHeater": "on"},
    },
}

CLIMA_SPENTO = {"status": {"messages": []}, "payload": {"status": "stopped"}}

#: Le domande del login ForgeRock, come in ``authenticate_working.json`` di pytoyoda.
DOMANDE_LINGUA = [
    {
        "type": "NameCallback",
        "output": [{"name": "prompt", "value": "Market Locale"}],
        "input": [{"name": "IDToken1", "value": ""}],
        "_id": 0,
    },
    {
        "type": "NameCallback",
        "output": [{"name": "prompt", "value": "Internationalization"}],
        "input": [{"name": "IDToken2", "value": ""}],
        "_id": 1,
    },
    {
        "type": "NameCallback",
        "output": [{"name": "prompt", "value": "UI Locales"}],
        "input": [{"name": "IDToken3", "value": ""}],
        "_id": 2,
    },
]

DOMANDE_UTENTE = [
    {
        "type": "NameCallback",
        "output": [{"name": "prompt", "value": "User Name"}],
        "input": [{"name": "IDToken1", "value": ""}],
        "_id": 3,
    },
    {
        "type": "ChoiceCallback",
        "output": [
            {"name": "prompt", "value": "Prompt"},
            {"name": "choices", "value": ["Local", "Google", "Facebook", "Apple"]},
            {"name": "defaultChoice", "value": 0},
        ],
        "input": [{"name": "IDToken2", "value": 0}],
        "_id": 4,
    },
]

DOMANDE_PASSWORD = [
    {
        "type": "PasswordCallback",
        "output": [{"name": "prompt", "value": "Password"}],
        "input": [{"name": "IDToken1", "value": ""}],
        "_id": 5,
    },
    {
        "type": "ChoiceCallback",
        "output": [
            {"name": "prompt", "value": "Prompt"},
            {"name": "choices", "value": ["continue", "resetpass"]},
            {"name": "defaultChoice", "value": 0},
        ],
        "input": [{"name": "IDToken2", "value": 0}],
        "_id": 6,
    },
]

UTENTE_SCONOSCIUTO = [
    {
        "type": "TextOutputCallback",
        "output": [{"name": "message", "value": "User Not Found"}, {"name": "messageType", "value": "2"}],
    }
]

PASSWORD_SBAGLIATA = {
    "code": 401,
    "reason": "Unauthorized",
    "message": "Login failure",
    "detail": {"failureUrl": "error://passworderror"},
}
