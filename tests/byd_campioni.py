"""Payload realistici del cloud BYD, per le prove di interpretazione.

I campi e le forme vengono dai modelli e dalle catture della libreria MIT
pyBYD (``jkaberg/pyBYD``) e dall'adattatore MIT ``TA2k/ioBroker.byd``. Sono
riscritti qui, non copiati, e ripuliti dei dati personali.
"""

from __future__ import annotations

from typing import Any

VIN = "LGXCE4CB1N0000001"

#: Un elenco auto (``getAllListByUserId``): una Atto 3 e una Seal.
ELENCO: list[dict[str, Any]] = [
    {
        "vin": VIN,
        "modelName": "ATTO 3",
        "autoAlias": "Delfino",
        "autoPlate": "GA123AA",
        "energyType": 1,
        "modelId": 42,
        "outModelType": "EV",
    }
]

#: Lettura realtime di un'auto in carica, chiusa, col cavo collegato (Atto 3, EU).
REALTIME: dict[str, Any] = {
    "onlineState": 1,
    "connectState": 1,
    "elecPercent": 64,
    "enduranceMileage": 268,
    "totalMileage": 12873,
    "speed": 0,
    "powerGear": 1,
    "chargeState": 1,
    "fullHour": 1,
    "fullMinute": 30,
    "bookingChargeState": 0,
    "tempInCar": 21,
    "mainSettingTempNew": 22.0,
    "leftFrontDoor": 0,
    "rightFrontDoor": 0,
    "leftRearDoor": 0,
    "rightRearDoor": 0,
    "trunkLid": 0,
    "leftFrontWindow": 1,
    "rightFrontWindow": 1,
    "leftRearWindow": 1,
    "rightRearWindow": 1,
    "skylight": 1,
    "leftFrontDoorLock": 2,
    "rightFrontDoorLock": 2,
    "leftRearDoorLock": 2,
    "rightRearDoorLock": 2,
    "leftFrontTirepressure": 250,
    "rightFrontTirepressure": 250,
    "leftRearTirepressure": 240,
    "rightRearTirepressure": 240,
    "tirePressUnit": 3,
    "tirepressureSystem": 0,
    "rapidTireLeak": 0,
    "sentryStatus": 4,
    "batteryHeatState": 0,
    "epb": 1,
    "oilEndurance": -1,
    "oilPercent": -1,
    "time": 1778212356,
}

#: La stessa auto ma spenta e fuori campo: tutto a zero, ``onlineState`` 2.
REALTIME_DORME: dict[str, Any] = {
    "onlineState": 2,
    "elecPercent": 0,
    "enduranceMileage": -1,
    "totalMileage": 12873,
    "tempInCar": -129,
}

#: Posizione (``getGpsInfoResult``), con la busta del GPS già tolta.
GPS: dict[str, Any] = {
    "latitude": 45.4642,
    "longitude": 9.19,
    "speed": 0,
    "direction": 180,
    "gpsTimeStamp": 1778212300,
}

#: Pagina della ricarica intelligente (``smartCharge/homePage``): SOC, cavo, orari.
RICARICA: dict[str, Any] = {
    "vin": VIN,
    "soc": 64,
    "chargingState": 1,
    "connectState": 1,
    "fullHour": 1,
    "fullMinute": 30,
    "charge": {
        "startTime": "23:00",
        "endTime": "7:00",
        "status": 1,
        "chargeWay": "2",
    },
}

#: Clima (``getStatusNow``): condizionatore acceso, temperature interna ed esterna.
CLIMA: dict[str, Any] = {
    "status": 1,
    "mainSettingTempNew": 22.0,
    "tempInCar": 21.0,
    "tempOutCar": 14.0,
    "frontDefrostStatus": 0,
    "electricDefrostStatus": 0,
}

#: Consumi (``getEnergyConsumption``): il dato lento del consumo medio.
ENERGIA: dict[str, Any] = {
    "cumulativeEnergyConsumption": {
        "mileageUnit": "km",
        "evUnit": "kWh/100km",
        "avgEvConsumption": "16.8",
        "avgOilConsumption": "--",
        "oilUnit": "--",
        "totalMileage": "443",
    },
    "nearestEnergyConsumption": {
        "avgEvConsumption": "15.2",
        "evUnit": "kWh/100km",
    },
    "time": 1778212356,
}
