"""Risposte d'esempio del cloud della Mazda 6e (gateway Changan europeo).

Ricostruite con i nomi e i valori che usa fano0001/home-assistant-mazda-6e
(Apache 2.0) nei suoi sensori e nelle sue prove; il VIN è inventato.
"""

from __future__ import annotations

from typing import Any

VIN = "LVRHDAEJ0RN000001"
ID_AUTO = 1234567

AUTO = [
    {
        "carId": ID_AUTO,
        "vin": VIN,
        "modelName": "Mazda 6e",
        "carName": "La mia 6e",
        "plateNumber": "GD 123 AA",
        "seriesName": "6e",
    }
]

FUNZIONI = {"confList": ["ACSW", "FlashHonk", "BatteryMaxSoc", "TrunkUnlock", "#batteryScheduleHeating"]}

#: In carica in garage, chiusa, con un finestrino socchiuso.
STATO_IN_CARICA: dict[str, Any] = {
    "lastUpdatedAt": 1760961600000,
    "vehicleStatus": {
        "soc": 64,
        "drvMileage": 312,
        "totalMileage": 8421.5,
        "speed": 0,
        "powerStatus": 0,
        "status": 2,
        "steeringWheelHeater": 0,
        "connectStatus": 1,
    },
    "charge": {
        "chargeStatus": 6,
        "chargeConStatus": 1,
        "dcChargeGunConnectStatus": 0,
        "chargeCurrent": 15.8,
        "acChargeCurrent": 15.8,
        "dcChargeCurrent": 0,
        "maxSocPercent": 90,
        "remainChargeTime": 185,
    },
    "hvac": {
        "insideTemp": 195,
        "consTempCockpit": 0,
        "remoteTemp": 215,
        "acStatus": 0,
        "defrostStatus": 0,
        "insideHumidity": 48,
        "insidePm25": 6,
    },
    "door": {"doors": [0, 0, 0, 0], "trunk": 0, "driverLock": 0, "passengerLock": 0},
    "window": {"windows": [0, 1, 0, 0], "openDegree": [0, 12, 0, 0]},
    "tire": {
        "leftFront": {"pressure": 250, "status": 0},
        "rightFront": {"pressure": 252, "status": 0},
        "leftBack": {"pressure": 248, "status": 0},
        "rightBack": {"pressure": 249, "status": 0},
    },
    "lamp": {"lowBeam": 0, "highBeam": 0, "positionLamp": 0, "leftTurn": 0, "rightTurn": 0},
    "location": {"latitude": "45.464211", "longitude": "9.191383"},
}

#: In marcia, aperta, senza spina, col clima acceso; il tempo di ricarica è «non si sa».
STATO_IN_MARCIA: dict[str, Any] = {
    "lastUpdatedAt": 1760965200000,
    "vehicleStatus": {
        "soc": 58,
        "drvMileage": 281,
        "totalMileage": 8460,
        "speed": 87.5,
        "powerStatus": 2,
        "status": 1,
        "connectStatus": 1,
    },
    "charge": {
        "chargeStatus": 0,
        "chargeConStatus": 0,
        "dcChargeGunConnectStatus": 0,
        "maxSocPercent": 90,
        "remainChargeTime": 8191,
    },
    "hvac": {"insideTemp": 0, "remoteTemp": 210, "acStatus": 1, "defrostStatus": 1},
    "door": {"doors": [1, 0, 0, 0], "trunk": 1, "driverLock": 1, "passengerLock": 0},
    "window": {"windows": [0, 0, 0, 0]},
    "tire": None,
    "location": None,
}


def ok(dati: Any) -> dict[str, Any]:
    return {"success": True, "code": "0", "msg": "success", "data": dati}


def no(codice: str, messaggio: str = "") -> dict[str, Any]:
    return {"success": False, "code": codice, "msg": messaggio, "data": None}
