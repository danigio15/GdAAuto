"""Payload d'esempio dei due cloud Nissan.

- NissanConnect Services (Kamereon): la Micra elettrica e
  le risposte dei token sono quelle dei test di dan-r/HomeAssistant-NissanConnect
  (``tests/test_micra.py``, ``tests/test_kamereon.py``, MIT); la batteria v1
  della Leaf ZE1 e le porte sono composte con i campi che quel progetto legge
  (``kamereon.py``), con valori inventati.
- NissanConnect EV (Carwings): le risposte vengono dalle docstring e dai test
  di filcole/pycarwings2 (``responses.py``, ``tests/test_pycarwings2.py``,
  Apache-2.0).
"""

from __future__ import annotations

from typing import Any

VIN_MICRA = "SJNTEST0MICRA0001"
VIN_LEAF = "SJNFAAZE1U0000001"
VIN_VECCHIA = "SJNFAAZE0U6000001"


def dati(attributi: dict[str, Any], vin: str = VIN_MICRA) -> dict[str, Any]:
    return {"data": {"type": "Car", "id": vin, "attributes": attributi}}


# --- NissanConnect Services ---------------------------------------------------------

TOKEN_LOGIN = {
    "access_token": "wso2-access-token",
    "refresh_token": "wso2-refresh-token",
    "id_token": "wso2-id-token",
    "token_type": "Bearer",
    "expires_in": 3600,
}

UTENTE = {"userId": "test-user"}

MICRA_SERVIZI = [12, 97, 107, 202, 299, 308, 317, 319, 344, 366]
LEAF_SERVIZI = [12, 27, 97, 202, 235, 299, 303, 307, 319, 366, 2021]

AUTO = {
    "data": [
        {
            "vin": VIN_MICRA.lower(),
            "modelName": "MICRA",
            "modelYear": 2025,
            "carGateway": "RVG",
            "canGeneration": "C1A_HS EVO",
            "nickname": "",
            "registrationNumber": "GA123BC",
            "services": [{"id": s, "activationState": "ACTIVATED"} for s in MICRA_SERVIZI],
        },
        {
            "vin": VIN_LEAF,
            "modelName": "LEAF",
            "modelYear": 2020,
            "canGeneration": "1",
            "nickname": "La Leaf",
            "color": "QAB",
            "services": [
                *({"id": s, "activationState": "ACTIVATED"} for s in LEAF_SERVIZI),
                {"id": 96, "activationState": "DEACTIVATED"},
            ],
        },
    ]
}

MICRA_BATTERIA = {
    "lastUpdateTime": "2026-01-01T10:00:00.000Z",
    "batteryLevel": 57,
    "batteryTemperature": 0,
    "batteryAutonomy": 244,
    "plugStatus": 0,
    "chargingStatus": 0.0,
    "chargingRemainingTime": 1,
}
MICRA_POSIZIONE = {"gpsLatitude": 12.34, "gpsLongitude": 56.78, "lastUpdateTime": "2026-01-01T06:00:00.000Z"}
MICRA_CLIMA = {"internalTemperature": 21.0, "hvacStatus": "off", "lastUpdateTime": "2026-01-01T04:00:00.000Z"}
MICRA_COCKPIT_V2 = {"fuelQuantity": 0.0, "totalMileage": 12345.0}
#: come risponde la Micra al cockpit v1 (dan-r#109)
NON_IMPLEMENTATO = {
    "errors": [
        {
            "status": "Not Implemented",
            "code": "501",
            "title": "Not supported Feature",
            "detail": "This feature is not technically supported by this gateway",
        }
    ]
}
NON_TROVATO = {"errors": [{"status": "Not Found", "code": "404"}]}
PRIVACY = {
    "errors": [
        {"status": "Forbidden", "code": "403009", "title": "Forbidden action", "detail": "Privacy mode currently ON"}
    ]
}

#: la Leaf ZE1 in carica a 7 kW (``chargePower`` 2, normale)
LEAF_BATTERIA = {
    "lastUpdateTime": "2026-10-18T09:30:00Z",
    "batteryLevel": 64,
    "batteryCapacity": 42000,
    "batteryTemperature": 60,
    "batteryBarLevel": 160,
    "instantaneousPower": 6.6,
    "chargePower": 2,
    "timeRequiredToFullFast": 25,
    "timeRequiredToFullNormal": 240,
    "timeRequiredToFullSlow": 720,
    "rangeHvacOff": 180,
    "rangeHvacOn": 165,
    "chargeStatus": 1,
    "plugStatus": 1,
    "vehiclePlugTimestamp": "2026-10-18T08:00:00Z",
}
LEAF_CLIMA = {
    "externalTemperature": 12.5,
    "internalTemperature": 15.0,
    "hvacStatus": "on",
    "nextTargetTemperature": 21,
    "lastUpdateTime": "2026-10-18T09:35:00Z",
}
LEAF_PORTE = {
    "lockStatus": "unlocked",
    "doorStatusFrontLeft": "closed",
    "doorStatusFrontRight": "open",
    "doorStatusRearLeft": "closed",
    "doorStatusRearRight": "closed",
    "hatchStatus": "closed",
    "lastUpdateTime": "2026-10-18T09:20:00Z",
}
LEAF_POSIZIONE = {"gpsLatitude": 45.4642, "gpsLongitude": 9.19, "lastUpdateTime": "2026-10-18T09:20:00Z"}
LEAF_COCKPIT = {"totalMileage": 31250.4}
LEAF_VIAGGI = {
    "data": {
        "attributes": {
            "summaries": [
                {
                    "tripsNumber": 3,
                    "distance": 62.0,
                    "duration": 75,
                    "firstTripStart": "2026-10-16T07:00:00Z",
                    "lastTripEnd": "2026-10-16T19:00:00Z",
                    "consumedFuel": 0,
                    "consumedElectricity": 9.8,
                    "savedElectricity": 1.1,
                    "day": "20261016",
                },
                {
                    "tripsNumber": 2,
                    "distance": 38.0,
                    "duration": 50,
                    "firstTripStart": "2026-10-17T07:00:00Z",
                    "lastTripEnd": "2026-10-17T18:00:00Z",
                    "consumedFuel": 0,
                    "consumedElectricity": 6.2,
                    "savedElectricity": 0.6,
                    "day": "20261017",
                },
            ]
        }
    }
}

# --- NissanConnect EV (Carwings) -------------------------------------------------------

AVVIO = {"status": 200, "message": "success", "baseprm": "88dSp7wWnV3bvv9Z88zEwg"}

LOGIN_CARWINGS = {
    "status": 200,
    "message": "success",
    "sessionId": "12345678-1234-1234-1234-1234567890",
    "VehicleInfoList": {
        "VehicleInfo": [{"charger20066": "false", "nickname": "LEAF", "telematicsEnabled": "true", "vin": VIN_VECCHIA}],
        "vehicleInfo": [
            {
                "charger20066": "false",
                "nickname": "LEAF",
                "telematicsEnabled": "true",
                "vin": VIN_VECCHIA,
                "custom_sessionid": "SESSIONE-1",
            }
        ],
    },
    "vehicle": {
        "profile": {
            "vin": VIN_VECCHIA,
            "gdcUserId": "FG12345678",
            "gdcPassword": "password",
            "encAuthToken": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "dcmId": "123456789012",
            "nickname": "Alpha124",
            "status": "ACCEPTED",
            "statusDate": "Aug 15, 2015 07:00 PM",
        }
    },
    "EncAuthToken": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "CustomerInfo": {
        "UserId": "AB12345678",
        "Language": "en-US",
        "Timezone": "Europe/Rome",
        "RegionCode": "NE",
        "OwnerId": "1234567890",
        "Nickname": "Bravo456",
        "Country": "IT",
        "VehicleImage": "/content/language/default/images/img/ph_car.jpg",
        "UserVehicleBoundDurationSec": "999971200",
        "VehicleInfo": {
            "VIN": VIN_VECCHIA,
            "DCMID": "201212345678",
            "SIMID": "12345678901234567890",
            "NAVIID": "1234567890",
            "EncryptedNAVIID": "1234567890ABCDEFGHIJKLMNOP",
            "MSN": "123456789012345",
            "LastVehicleLoginTime": "",
            "UserVehicleBoundTime": "2015-08-17T14:16:32Z",
            "LastDCMUseTime": "",
        },
    },
    "UserInfoRevisionNo": "1",
}
#: l'abbonamento scaduto (docstring di pycarwings2)
ABBONAMENTO_SCADUTO = {"status": "AAS-604", "message": "INVALID PARAMS"}
LOGIN_SBAGLIATO = {"status": 404, "message": "INVALID PARAMS"}

#: una 30 kWh staccata (21/01/2019)
BATTERIA_30 = {
    "status": 200,
    "BatteryStatusRecords": {
        "OperationResult": "START",
        "OperationDateAndTime": "21-Jan-2019 13:29",
        "BatteryStatus": {
            "BatteryChargingStatus": "NOT_CHARGING",
            "BatteryCapacity": "240",
            "BatteryRemainingAmount": "220",
            "BatteryRemainingAmountWH": "24480",
            "BatteryRemainingAmountkWH": "",
            "SOC": {"Value": "91"},
        },
        "PluginState": "NOT_CONNECTED",
        "CruisingRangeAcOn": "146000",
        "CruisingRangeAcOff": "168000",
        "TimeRequiredToFull": {"HourRequiredToFull": "4", "MinutesRequiredToFull": "30"},
        "TimeRequiredToFull200": {"HourRequiredToFull": "3", "MinutesRequiredToFull": "0"},
        "TimeRequiredToFull200_6kW": {"HourRequiredToFull": "1", "MinutesRequiredToFull": "30"},
        "NotificationDateAndTime": "2019/01/21 13:29",
        "TargetDate": "2019/01/21 13:29",
    },
}

#: una 24 kWh alla colonnina rapida
BATTERIA_RAPIDA = {
    "status": 200,
    "message": "success",
    "BatteryStatusRecords": {
        "OperationResult": "START",
        "OperationDateAndTime": "Feb 14, 2016 03:28 PM",
        "BatteryStatus": {
            "BatteryChargingStatus": "RAPIDLY_CHARGING",
            "BatteryCapacity": "12",
            "BatteryRemainingAmount": "11",
            "BatteryRemainingAmountWH": "",
            "BatteryRemainingAmountkWH": "",
        },
        "PluginState": "QC_CONNECTED",
        "CruisingRangeAcOn": "107136.0",
        "CruisingRangeAcOff": "115776.0",
        "NotificationDateAndTime": "2016/02/14 20:28",
        "TargetDate": "2016/02/14 20:28",
    },
}

#: una 24 kWh alla wallbox
BATTERIA_IN_CARICA = {
    "status": 200,
    "message": "success",
    "BatteryStatusRecords": {
        "OperationResult": "START",
        "OperationDateAndTime": "Feb 19, 2016 12:12 PM",
        "BatteryStatus": {
            "BatteryChargingStatus": "NORMAL_CHARGING",
            "BatteryCapacity": "12",
            "BatteryRemainingAmount": "12",
            "BatteryRemainingAmountWH": "",
            "BatteryRemainingAmountkWH": "",
        },
        "PluginState": "CONNECTED",
        "CruisingRangeAcOn": "132000.0",
        "CruisingRangeAcOff": "134000.0",
        "TimeRequiredToFull200_6kW": {"HourRequiredToFull": "0", "MinutesRequiredToFull": "40"},
        "NotificationDateAndTime": "2016/02/19 17:12",
        "TargetDate": "2016/02/19 17:12",
    },
}

CLIMA_ACCESO = {
    "status": 200,
    "message": "success",
    "RemoteACRecords": {
        "OperationResult": "START_BATTERY",
        "OperationDateAndTime": "Feb 10, 2016 10:22 PM",
        "RemoteACOperation": "START",
        "ACStartStopDateAndTime": "Feb 10, 2016 10:23 PM",
        "CruisingRangeAcOn": "107712.0",
        "CruisingRangeAcOff": "109344.0",
        "ACStartStopURL": "",
        "PluginState": "NOT_CONNECTED",
        "ACDurationBatterySec": "900",
        "ACDurationPluggedSec": "7200",
    },
    "OperationDateAndTime": "",
}
CLIMA_SPENTO = {
    "status": 200,
    "RemoteACRecords": {
        "OperationResult": "START_BATTERY",
        "OperationDateAndTime": "11-Jan-2019 17:51",
        "RemoteACOperation": "STOP",
        "ACStartStopDateAndTime": "2019/01/11 16:52",
        "CruisingRangeAcOn": "74328.0",
        "CruisingRangeAcOff": "79544.0",
        "ACStartStopURL": "",
        "PluginState": "NOT_CONNECTED",
        "ACDurationBatterySec": "900",
        "ACDurationPluggedSec": "7200",
    },
}
CLIMA_SENZA_CAMPO = {
    "status": 200,
    "RemoteACRecords": {
        "OperationResult": "ELECTRIC_WAVE_ABNORMAL",
        "OperationDateAndTime": "2018/04/08 10:00",
        "RemoteACOperation": "START",
        "ACStartStopDateAndTime": "08-Apr-2018 11:06",
        "ACStartStopURL": "",
        "PluginState": "INVALID",
        "ACDurationBatterySec": "900",
        "ACDurationPluggedSec": "7200",
        "PreAC_unit": "C",
        "PreAC_temp": "22",
    },
}
CLIMA_NIENTE = {"status": 200, "RemoteACRecords": []}

CLIMA_RISULTATO_ACCESO = {
    "status": 200,
    "message": "success",
    "responseFlag": "1",
    "operationResult": "START_BATTERY",
    "acContinueTime": "15",
    "cruisingRangeAcOn": "106400.0",
    "cruisingRangeAcOff": "107920.0",
    "timeStamp": "2016-02-05 12:59:46",
    "hvacStatus": "ON",
}
CLIMA_RISULTATO_SPENTO = {
    "status": 200,
    "message": "success",
    "responseFlag": "1",
    "operationResult": "START",
    "timeStamp": "2016-02-09 03:32:51",
    "hvacStatus": "OFF",
}
IN_ATTESA = {"status": 200, "responseFlag": "0"}
CHIAVE_RISULTATO = {
    "status": 200,
    "userId": "user@domain.com",
    "vin": VIN_VECCHIA,
    "resultKey": "12345678901234567890123456789012345678901234567890",
}

#: il riepilogo del mese (i campi che pycarwings2 legge, valori inventati)
CONSUMI_MESE = {
    "status": 200,
    "PriceSimulatorDetailInfoResponsePersonalData": {
        "DisplayMonth": "Oct/2026",
        "PriceSimulatorDetailInfoDateList": {"PriceSimulatorDetailInfoDate": []},
        "PriceSimulatorTotalInfo": {
            "TotalNumberOfTrips": "21",
            "TotalPowerConsumptTotal": "58.5",
            "TotalPowerConsumptMoter": "66.0",
            "TotalPowerConsumptMinus": "9.1",
            "TotalTravelDistance": "390000",
            "TotalElectricMileage": "15.0",
            "TotalCO2Reductiont": "52",
        },
        "ElectricPrice": "0.25",
        "ElectricBill": "14.6",
        "ElectricCostScale": "kWh/100km",
    },
}
