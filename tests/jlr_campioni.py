"""Risposte d'esempio del cloud InControl di Jaguar Land Rover.

Le chiavi e i valori sono quelli che leggono ardevd/jlrpy e
msp1974/homeassistant-jlrincontrol (licenza MIT) e la documentazione Postman
dell'API citata da jlrpy; telai, nomi e coordinate sono inventati.
"""

from __future__ import annotations

from typing import Any

VIN_IPACE = "SADHA2B10K1F00001"
VIN_RR = "SALGA2AE0PA000002"
ID_UTENTE = "UTENTE-1234"


def _kv(tabella: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": k, "value": v} for k, v in tabella.items()]


TOKEN = {
    "access_token": "ACCESSO-1",
    "authorization_token": "AUTORIZZAZIONE-1",
    "expires_in": "86400",
    "refresh_token": "RINNOVO-1",
    "token_type": "bearer",
}
TOKEN_SBAGLIATO = {"error": "invalid_grant", "error_description": "Bad credentials"}

UTENTE = {
    "userId": ID_UTENTE,
    "loginName": "mario@esempio.it",
    "userType": "Customer",
    "contact": {
        "firstName": "Mario",
        "lastName": "Rossi",
        "userPreferences": {"unitsOfMeasurement": "Km Litre Celsius VolPerDist Wh DistPerkWh"},
    },
}

AUTO = {
    "vehicles": [
        {"userId": ID_UTENTE, "vin": VIN_IPACE, "role": "Primary"},
        {"userId": ID_UTENTE, "vin": VIN_RR, "role": "Primary"},
    ]
}


def _servizi(*attivi: str, spenti: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    servizi = [{"serviceType": s, "vehicleCapable": True, "serviceEnabled": True} for s in attivi]
    return servizi + [{"serviceType": s, "vehicleCapable": True, "serviceEnabled": False} for s in spenti]


ATTRIBUTI_IPACE = {
    "engineCode": "AJ-EV",
    "seatsQuantity": 5,
    "exteriorColorName": "Firenze Red",
    "fuelType": "Electric",
    "powerTrainType": "BEV",
    "modelYear": 2019,
    "numberOfDoors": 5,
    "registrationNumber": "GA123BC",
    "vehicleBrand": "Jaguar",
    "vehicleType": "I-PACE",
    "bodyType": "SUV",
    "roofType": "SUNROOF",
    "nickname": "",
    "availableServices": _servizi("RDL", "RDU", "HBLF", "ECC", "CP", "VHS", "ALOFF", spenti=("GMCC",)),
    "capabilities": [{"capability": "EV_CHARGING", "capabilityClass": "EV"}],
}

ATTRIBUTI_RR = {
    "fuelType": "Petrol",
    "powerTrainType": "PHEV",
    "modelYear": 2023,
    "registrationNumber": "GB456CD",
    "vehicleBrand": "Land Rover",
    "vehicleType": "Range Rover",
    "exteriorColorName": "Carpathian Grey",
    "nickname": "La Range di Mario",
    "availableServices": _servizi("RDL", "RDU", "HBLF", "ECC", "CP", "REON", "REOFF"),
}

#: Un'I-PACE in carica a casa, chiusa, col bagagliaio aperto e una gomma in allarme.
CORE_IPACE = {
    "ODOMETER_METER": "48123456",
    "ODOMETER_MILES": "29902",
    "DISTANCE_TO_EMPTY_FUEL": "0",
    "FUEL_LEVEL_PERC": "0",
    "DOOR_IS_ALL_DOORS_LOCKED": "TRUE",
    "DOOR_FRONT_LEFT_LOCK_STATUS": "LOCKED",
    "DOOR_FRONT_RIGHT_LOCK_STATUS": "LOCKED",
    "DOOR_REAR_LEFT_LOCK_STATUS": "LOCKED",
    "DOOR_REAR_RIGHT_LOCK_STATUS": "LOCKED",
    "DOOR_BOOT_LOCK_STATUS": "UNLOCKED",
    "DOOR_ENGINE_HOOD_LOCK_STATUS": "LOCKED",
    "DOOR_FRONT_LEFT_POSITION": "CLOSED",
    "DOOR_FRONT_RIGHT_POSITION": "CLOSED",
    "DOOR_REAR_LEFT_POSITION": "CLOSED",
    "DOOR_REAR_RIGHT_POSITION": "CLOSED",
    "DOOR_BOOT_POSITION": "OPEN",
    "DOOR_ENGINE_HOOD_POSITION": "CLOSED",
    "WINDOW_FRONT_LEFT_STATUS": "CLOSED",
    "WINDOW_FRONT_RIGHT_STATUS": "CLOSED",
    "WINDOW_REAR_LEFT_STATUS": "CLOSED",
    "WINDOW_REAR_RIGHT_STATUS": "CLOSED",
    "IS_SUNROOF_OPEN": "FALSE",
    "TYRE_PRESSURE_FRONT_LEFT": "2480",
    "TYRE_PRESSURE_FRONT_RIGHT": "2470",
    "TYRE_PRESSURE_REAR_LEFT": "2510",
    "TYRE_PRESSURE_REAR_RIGHT": "1990",
    "TYRE_STATUS_FRONT_LEFT": "NORMAL",
    "TYRE_STATUS_FRONT_RIGHT": "NORMAL",
    "TYRE_STATUS_REAR_LEFT": "NORMAL",
    "TYRE_STATUS_REAR_RIGHT": "LOW",
    "THEFT_ALARM_STATUS": "ALARM_ARMED",
    "IS_PANIC_ALARM_TRIGGERED": "FALSE",
    "VEHICLE_STATE_TYPE": "KEY_REMOVED",
    "PRIVACY_SWITCH": "FALSE",
    "BRAKE_FLUID_WARN": "NORMAL",
    "WASHER_FLUID_WARN": "NORMAL",
    "EXT_KILOMETERS_TO_SERVICE": "12500",
    "CLIMATE_STATUS_OPERATING_STATUS": "OFF",
    "SERVICE_MODE_STOP": "",
    "TRANSPORT_MODE_STOP": "",
}
EV_IPACE = {
    "EV_STATE_OF_CHARGE": "64",
    "EV_RANGE_ON_BATTERY_KM": "251",
    "EV_RANGE_ON_BATTERY_MILES": "156",
    "EV_CHARGING_STATUS": "CHARGING",
    "EV_CHARGING_METHOD": "WIRED",
    "EV_MINUTES_TO_FULLY_CHARGED": "185",
    "EV_CHARGING_RATE_KM_PER_HOUR": "40",
    "EV_CHARGING_RATE_SOC_PER_HOUR": "12",
    "EV_ENERGY_CONSUMED_LAST_CHARGE_KWH": "412",
    "EV_PERMANENT_MAX_SOC_CHARGE_SETTING_CHOICE": "80",
    "EV_ONE_OFF_MAX_SOC_CHARGE_SETTING_CHOICE": "CLEAR",
    "EV_PRECONDITION_OPERATING_STATUS": "OFF",
    "EV_CHARGE_NOW_SETTING": "DEFAULT",
}
STATO_IPACE = {
    "vehicleStatus": {"coreStatus": _kv(CORE_IPACE), "evStatus": _kv(EV_IPACE)},
    "vehicleAlerts": [
        {"key": "TYRE_PRESSURE_WARNING", "value": "true", "active": True, "lastUpdatedTime": "2024-03-01T18:10:00+0000"}
    ],
    "lastUpdatedTime": "2024-03-01T18:12:43+0000",
}

#: Una Range Rover P440e ibrida plug-in: aperta, una portiera e un finestrino aperti, clima acceso.
CORE_RR = {
    "ODOMETER_METER": "",
    "ODOMETER_MILES": "10000",
    "FUEL_LEVEL_PERC": "55",
    "DISTANCE_TO_EMPTY_FUEL": "480",
    "DOOR_IS_ALL_DOORS_LOCKED": "FALSE",
    "DOOR_FRONT_LEFT_POSITION": "OPEN",
    "DOOR_FRONT_RIGHT_POSITION": "CLOSED",
    "DOOR_REAR_LEFT_POSITION": "CLOSED",
    "DOOR_REAR_RIGHT_POSITION": "CLOSED",
    "DOOR_BOOT_POSITION": "CLOSED",
    "WINDOW_FRONT_LEFT_STATUS": "PARTIAL_OPEN",
    "WINDOW_FRONT_RIGHT_STATUS": "CLOSED",
    "WINDOW_REAR_LEFT_STATUS": "CLOSED",
    "WINDOW_REAR_RIGHT_STATUS": "UNUSED",
    "TYRE_PRESSURE_FRONT_LEFT": "250",
    "TYRE_PRESSURE_FRONT_RIGHT": "251",
    "TYRE_PRESSURE_REAR_LEFT": "0",
    "TYRE_STATUS_FRONT_LEFT": "NORMAL",
    "TYRE_STATUS_FRONT_RIGHT": "NORMAL",
    "THEFT_ALARM_STATUS": "ALARM_OFF",
    "VEHICLE_STATE_TYPE": "ENGINE_ON_REMOTE_START",
}
EV_RR = {
    "EV_STATE_OF_CHARGE": "38",
    "EV_RANGE_ON_BATTERY_KM": "",
    "EV_RANGE_ON_BATTERY_MILES": "25",
    "EV_PHEV_RANGE_COMBINED_KM": "520",
    "EV_CHARGING_STATUS": "NOTCONNECTED",
    "EV_CHARGING_METHOD": "NOTCONNECTED",
    "EV_MINUTES_TO_FULLY_CHARGED": "65535",
    "EV_PERMANENT_MAX_SOC_CHARGE_SETTING_CHOICE": "CLEAR",
    "EV_ONE_OFF_MAX_SOC_CHARGE_SETTING_CHOICE": "90",
    "EV_PRECONDITION_OPERATING_STATUS": "PRECLIM",
}
STATO_RR = {
    "vehicleStatus": {"coreStatus": _kv(CORE_RR), "evStatus": _kv(EV_RR)},
    "vehicleAlerts": [],
    "lastUpdatedTime": "2024-03-02T07:00:01.000Z",
}

#: Il vecchio formato (v3): un elenco solo, auto ed elettrica insieme.
STATO_V3 = {
    "vehicleStatus": _kv({"EV_STATE_OF_CHARGE": "90", "ODOMETER_METER": "1000", "DOOR_IS_ALL_DOORS_LOCKED": "TRUE"}),
    "lastUpdatedTime": "2020-04-12T16:02:43+0000",
}

POSIZIONE = {
    "position": {
        "longitude": 9.18951,
        "latitude": 45.46427,
        "timestamp": "2024-03-01T18:05:00+0000",
        "speed": 0,
        "heading": 270,
        "positionQuality": None,
    },
    "calculatedPosition": None,
}

VIAGGI_DAL = "2024-03-01T00:00:00+00:00"
VIAGGI = {
    "trips": [
        {
            "id": 1,
            "tripDetails": {
                "startTime": "2024-03-01T08:00:00+0000",
                "endTime": "2024-03-01T08:40:00+0000",
                "distance": 32500,
                "averageSpeed": 48,
                "averageEnergyConsumption": 21.4,
                "startPosition": {"latitude": 45.4, "longitude": 9.1, "address": "Via Roma"},
                "endPosition": {"latitude": 45.5, "longitude": 9.2, "address": "Via Milano"},
            },
        },
        {"id": 2, "tripDetails": {"startTime": "2024-03-02T09:00:00+0000", "distance": 12000}},
        # Un viaggio di prima: non conta.
        {"id": 3, "tripDetails": {"startTime": "2024-02-20T09:00:00+0000", "distance": 99000}},
    ]
}

AUTENTICA = {"token": "TOKEN-SERVIZIO"}


def esito(stato: str, servizio: str, pratica: str = "PRATICA-1", motivo: str = "") -> dict[str, Any]:
    return {
        "status": stato,
        "statusTimestamp": "2024-03-01T18:20:00+0000",
        "startTime": "2024-03-01T18:19:58+0000",
        "serviceType": servizio,
        "failureDescription": motivo,
        "failureReason": motivo or None,
        "customerServiceId": pratica,
        "vehicleId": VIN_IPACE,
        "active": stato in ("Started", "Running"),
        "initiator": "USER",
        "eventTrigger": None,
        "serviceCommand": None,
        "serviceParameters": None,
    }
