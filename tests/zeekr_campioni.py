"""Campioni del cloud Zeekr (gateway TSP europeo), nella forma che il cloud restituisce.

Il cloud Zeekr non ha fixture pubblici completi: questi sono ricostruiti dai
campi e dai valori documentati da zeekr_homeassistant ed zeekr_ev_api
(Fryyyyy, licenza MIT; ``tests/test_sensor.py``, ``sensor.py``,
``binary_sensor.py``) e da OpenZeekr (borconi, licenza MIT;
``core/.../net/model/Models.kt``), nell'albero ``vehicleStatus`` della
piattaforma Geely: valori come testo, posizione in gradi, VIN finto.
"""

from __future__ import annotations

from typing import Any

VIN = "L6TTESTVIN0000001"

#: una voce dell'elenco delle auto (``ms-app-bff/api/v4.0/veh/vehicle-list``)
AUTO: dict[str, Any] = {
    "vin": VIN,
    "nickName": "Freccia",
    "appModelCode": "CX1E",
    "appYearCode": "1614",
    "colorName": "Tech Grey",
    "plateNo": "GD123AA",
    "isOwner": "false",
}

#: una 7X parcheggiata a Milano, chiusa, al 76 %, col cavo collegato e la ricarica finita
STATO: dict[str, Any] = {
    "updateTime": "1760950800000",
    "basicVehicleStatus": {
        "usageMode": "1",
        "carMode": "0",
        "engineStatus": "engine-off",
        "distanceToEmpty": "381",
        "speed": "0",
        "speedValidity": "true",
        "position": {
            "latitude": "45.464211",
            "longitude": "9.191383",
            "altitude": "122",
            "posCanBeTrusted": "true",
            "marsCoordinates": "false",
        },
    },
    "additionalVehicleStatus": {
        "electricVehicleStatus": {
            "stateOfCharge": "",
            "chargeLevel": "76",
            "isCharging": False,
            "isPluggedIn": False,
            "chargerState": "4",
            "statusOfChargerConnection": "3",
            "dcDcConnectStatus": "0",
            "chargeLidAcStatus": "2",
            "chargeIAct": "0.0",
            "chargeUAct": "0.0",
            "distanceToEmptyOnBatteryOnly": "384",
            "distanceToEmptyOnBattery100Soc": "505",
            "distanceToEmptyOnBattery20Soc": "101",
            "averPowerConsumption": "18.7",
            "hvBatteryPreHeatingActive": False,
            "timeToFullyCharged": "2047",
        },
        "maintenanceStatus": {
            "odometer": "12873.000",
            "daysToService": "211",
            "distanceToService": "17127",
            "tyreStatusDriver": "262.0",
            "tyreStatusPassenger": "265.0",
            "tyreStatusDriverRear": "268.0",
            "tyreStatusPassengerRear": "268.0",
            "tyreTempDriver": "20",
            "tyreTempPassenger": "21",
            "tyreTempDriverRear": "22",
            "tyreTempPassengerRear": "23",
            "tyrePreWarningDriver": "0",
            "tyrePreWarningPassenger": "0",
            "tyrePreWarningDriverRear": "0",
            "tyrePreWarningPassengerRear": "0",
            "tyreTempWarningDriver": "0",
            "tyreTempWarningPassenger": "0",
            "tyreTempWarningDriverRear": "0",
            "tyreTempWarningPassengerRear": "0",
        },
        "drivingSafetyStatus": {
            "centralLockingStatus": "1",
            "electricParkBrakeStatus": "1",
            "doorOpenStatusDriver": "0",
            "doorOpenStatusPassenger": "0",
            "doorOpenStatusDriverRear": "0",
            "doorOpenStatusPassengerRear": "0",
            "doorLockStatusDriver": "1",
            "trunkOpenStatus": "0",
            "trunkLockStatus": "1",
            "engineHoodOpenStatus": "0",
        },
        "climateStatus": {
            "interiorTemp": "18.5",
            "exteriorTemp": "12.0",
            "preClimateActive": False,
            "defrost": "0",
            "drvHeatSts": "0",
            "winStatusDriver": "2",
            "winStatusPassenger": "2",
            "winStatusDriverRear": "2",
            "winStatusPassengerRear": "2",
            "winPosDriver": "0",
            "winPosPassenger": "0",
            "winPosDriverRear": "0",
            "winPosPassengerRear": "0",
        },
        "runningStatus": {"tripMeter2": "153.2", "avgSpeed": "47"},
    },
}

#: lo stato di ricarica a parte (``vehicle/status/qrvs``) durante una ricarica AC (zeekr_homeassistant)
QRVS: dict[str, Any] = {
    "chargerState": "2",
    "chargeVoltage": "222.0",
    "chargeCurrent": "9.4",
    "chargePower": "2.1",
    "chargeSpeed": "14",
}

#: il limite di ricarica (``charge/getLatestSoc``), in decimi di percento
LIMITE: dict[str, Any] = {"soc": "800"}

#: la ricarica programmata (``charge/getChargingPlan``)
PIANO: dict[str, Any] = {"command": "start", "startTime": "01:15", "endTime": "6:45", "timerId": "2"}

#: lo stato dei comandi (``remoteControl/getVehicleState``)
STATO_COMANDI: dict[str, Any] = {"vstdModeState": "1", "storageBoxStatus": []}


def viaggio(inizio_ms: int, km: float, consumo: float) -> dict[str, Any]:
    """Un viaggio del diario (``journalLog/trip/listForPage``)."""
    return {
        "tripId": 101,
        "reportTime": inizio_ms + 1_800_000,
        "startTime": inizio_ms,
        "endTime": inizio_ms + 1_500_000,
        "startOdometer": 12840,
        "endOdometer": 12840 + int(km),
        "traveledDistance": km,
        "avgSpeed": 38,
        "electricConsumption": consumo,
        "electricRegeneration": 560,
        "trackPoints": [{"latitude": 45.46, "longitude": 9.19}, {"latitude": 45.48, "longitude": 9.2}],
    }
