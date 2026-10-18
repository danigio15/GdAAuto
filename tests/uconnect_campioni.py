"""Risposte del cloud Uconnect, nella forma che il cloud restituisce.

I nomi dei campi e i loro tipi (numeri come stringhe, «"null"», unità a
parte, tempi in minuti, date in millisecondi) sono quelli che leggono
py-uconnect e il client di evcc; i valori sono inventati.
"""

from __future__ import annotations

VIN_500E = "ZFAEFAC39MX018014"
VIN_4XE = "1C4JJXP68MW700001"
UID = "8a1b2c3d4e5f60718293a4b5c6d7e8f9"

#: 2026-10-18 10:00:00 UTC, in millisecondi
ORA_MS = 1792317600000

# GET /v4/accounts/{uid}/vehicles?stage=ALL&sdp=ALL&brand=FIAT
ELENCO = {
    "userid": UID,
    "version": "v4",
    "vehicles": [
        {
            "regStatus": "COMPLETED_STAGE_3",
            "color": "BLUE",
            "year": 2022,
            "tsoBodyCode": "",
            "navEnabledHu": False,
            "language": "",
            "customerRegStatus": "Y",
            "radio": "",
            "activationSource": "DEALER",
            "nickname": "Cinquino",
            "vin": VIN_500E,
            "company": "FCA",
            "model": 332,
            "modelDescription": "Nuova 500",
            "tcuType": 2,
            "brandCode": "00",
            "soldRegion": "EMEA",
            "make": "FIAT",
            "fuelType": "E",
            "sdp": "IGNITE",
            "isCompanyCar": False,
            "tsoModelYear": "2022",
            "services": [
                {"service": "RDL", "serviceEnabled": True, "vehicleCapable": True},
                {"service": "RDU", "serviceEnabled": True, "vehicleCapable": True},
                {"service": "ROPRECOND", "serviceEnabled": True, "vehicleCapable": True},
                {"service": "ROPRECOND_OFF", "serviceEnabled": True, "vehicleCapable": True},
                {"service": "CNOW", "serviceEnabled": True, "vehicleCapable": True},
                {"service": "DEEPREFRESH", "serviceEnabled": True, "vehicleCapable": True},
                {"service": "VF", "serviceEnabled": True, "vehicleCapable": True},
                {"service": "HBLF", "serviceEnabled": False, "vehicleCapable": True},
                {"service": "REON", "serviceEnabled": True, "vehicleCapable": False},
            ],
        }
    ],
}

# GET /v3/accounts/{uid}/vehicles/{vin}/status/ — una 500e in carica a una wallbox (AC)
STATO_500E = {
    "vehicleInfo": {
        "totalRangeADA": None,
        "odometer": {"odometer": {"value": "12345", "unit": "km"}},
        "daysToService": "null",
        "distanceToService": {"distanceToService": {"value": "17655.0", "unit": "km"}},
        "fuel": {"fuelAmountLevel": None, "isFuelLevelLow": False, "distanceToEmpty": None, "fuelAmount": None},
        "oilLevel": None,
        "tyrePressure": [
            {"type": "FL", "pressure": {"value": "250", "unit": "kPa"}, "warning": False, "status": "NORMAL"},
            {"type": "FR", "pressure": {"value": "248", "unit": "kPa"}, "warning": False, "status": "NORMAL"},
            {"type": "RL", "pressure": {"value": "240", "unit": "kPa"}, "warning": False, "status": "NORMAL"},
            {"type": "RR", "pressure": {"value": "null", "unit": "kPa"}, "warning": False, "status": "NORMAL"},
        ],
        "batteryInfo": {"batteryStateOfCharge": "NORMAL", "batteryVoltage": {"value": "14.2", "unit": "volts"}},
        "timestamp": ORA_MS - 600000,
    },
    "evInfo": {
        "chargeScheduleAdditionalInfo": {"cabinCond": False, "headSeat": False},
        "chargePowerPreference": "NOT_USED_VALUE",
        "ignitionStatus": "OFF",
        "battery": {
            "stateOfCharge": 71,
            "chargingLevel": "LEVEL_2",
            "plugInStatus": True,
            "timeToFullyChargeL1": 1260,
            "timeToFullyChargeL2": 135,
            "timeToFullyChargeL3": -1,
            "chargingStatus": "CHARGING",
            "totalRange": 195,
            "distanceToEmpty": {"value": 195, "unit": "km"},
        },
        "schedules": [
            {
                "cabinPriority": False,
                "chargeToFull": False,
                "enableScheduleType": False,
                "endTime": "07:00",
                "repeatSchedule": True,
                "scheduleType": "CHARGE",
                "scheduledDays": {
                    "friday": True,
                    "monday": True,
                    "saturday": False,
                    "sunday": False,
                    "thursday": True,
                    "tuesday": True,
                    "wednesday": True,
                },
                "startTime": "00:30",
            }
        ],
        "timestamp": ORA_MS,
    },
    "timestamp": ORA_MS - 60000,
}

# GET /v1/accounts/{uid}/vehicles/{vin}/location/lastknown
POSIZIONE = {
    "timeStamp": ORA_MS - 120000,
    "longitude": 7.6869,
    "latitude": 45.0703,
    "altitude": 239.0,
    "bearing": 0.0,
    "isLocationApprox": False,
}

# GET /v1/accounts/{uid}/vehicles/{vin}/remote/status
REMOTO = {
    "doors": {
        "driver": {"status": "LOCKED"},
        "passenger": {"status": "LOCKED"},
        "leftRear": {"status": "LOCKED"},
        "rightRear": {"status": "LOCKED"},
    },
    "windows": {"driver": {"status": "CLOSED"}, "passenger": {"status": "OPEN"}},
    "trunk": {"status": "LOCKED"},
    "engine": {"status": "OFF"},
    "evRunning": {"status": "OFF"},
    "timestamp": ORA_MS - 300000,
}

# Una Jeep Wrangler 4xe americana: miglia, psi, ferma e senza cavo.
STATO_4XE = {
    "vehicleInfo": {
        "odometer": {"odometer": {"value": "10000", "unit": "mi"}},
        "fuel": {
            "fuelAmountLevel": 54,
            "isFuelLevelLow": False,
            "distanceToEmpty": {"value": "230", "unit": "mi"},
        },
        "tyrePressure": [
            {"type": "FL", "pressure": {"value": "36", "unit": "psi"}, "warning": False},
            {"type": "FR", "pressure": {"value": "36", "unit": "psi"}, "warning": False},
            {"type": "RL", "pressure": {"value": "35", "unit": "psi"}, "warning": False},
            {"type": "RR", "pressure": {"value": "0", "unit": "psi"}, "warning": True},
        ],
        "timestamp": ORA_MS,
    },
    "evInfo": {
        "ignitionStatus": "ON",
        "battery": {
            "stateOfCharge": "38",
            "chargingLevel": "DEFAULT",
            "plugInStatus": False,
            "timeToFullyChargeL1": 0,
            "timeToFullyChargeL2": 0,
            "timeToFullyChargeL3": 0,
            "chargingStatus": "NOT_CHARGING",
            "totalRange": 250,
            "gasRange": 237,
            "distanceToEmpty": {"value": 13, "unit": "mi"},
        },
        "timestamp": ORA_MS,
    },
    "timestamp": ORA_MS,
}
