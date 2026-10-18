"""Risposte d'esempio delle API Volvo Cars, come le manda il cloud (con l'involucro ``data``).

I contenuti vengono dai fixture delle prove dell'integrazione ``volvo`` di Home
Assistant (home-assistant/core, Apache 2.0): EX30 2024, XC40 elettrica 2024,
XC90 plug-in 2024. I VIN sono finti.
"""

from __future__ import annotations

from typing import Any

VIN_EX30 = "YV1EX30EX30000001"
VIN_XC40 = "YV1XC40XC40000002"
VIN_XC90 = "YV1XC90XC90000003"
VIN_S90 = "YV1S90DIESEL00004"


def _campo(valore: Any, quando: str, unita: str | None = None) -> dict[str, Any]:
    c: dict[str, Any] = {"value": valore, "timestamp": quando}
    if unita:
        c["unit"] = unita
    return c


T = "2024-12-30T14:18:56.849Z"

ELENCO = {"data": [{"vin": VIN_EX30}, {"vin": VIN_XC40}, {"vin": VIN_XC90}, {"vin": VIN_S90}]}

DETTAGLI = {
    VIN_EX30: {
        "vin": VIN_EX30,
        "modelYear": 2024,
        "gearbox": "AUTOMATIC",
        "fuelType": "NONE",
        "externalColour": "Crystal White Pearl",
        "batteryCapacityKWH": 66.0,
        "images": {"exteriorImageUrl": "https://wizz.volvocars.com/x.png", "internalImageUrl": "https://w/i.png"},
        "descriptions": {"model": "EX30", "upholstery": "R310", "steering": "LEFT"},
    },
    VIN_XC40: {
        "vin": VIN_XC40,
        "modelYear": 2024,
        "gearbox": "AUTOMATIC",
        "fuelType": "ELECTRIC",
        "externalColour": "Silver Dawn",
        "batteryCapacityKWH": 81.608,
        "descriptions": {"model": "XC40", "upholstery": "null", "steering": "LEFT"},
    },
    VIN_XC90: {
        "vin": VIN_XC90,
        "modelYear": 2024,
        "gearbox": "AUTOMATIC",
        "fuelType": "PETROL/ELECTRIC",
        "externalColour": "Crystal White Pearl",
        "batteryCapacityKWH": 18.819,
        "descriptions": {"model": "XC90", "upholstery": "null", "steering": "LEFT"},
    },
    VIN_S90: {
        "vin": VIN_S90,
        "modelYear": 2018,
        "gearbox": "AUTOMATIC",
        "fuelType": "DIESEL",
        "descriptions": {"model": "S90"},
    },
}


def comandi(vin: str, *nomi: str) -> dict[str, Any]:
    pezzi = {
        "LOCK_REDUCED_GUARD": "lock-reduced-guard",
        "LOCK": "lock",
        "UNLOCK": "unlock",
        "HONK": "honk",
        "HONK_AND_FLASH": "honk-flash",
        "FLASH": "flash",
        "CLIMATIZATION_START": "climatization-start",
        "CLIMATIZATION_STOP": "climatization-stop",
        "ENGINE_START": "engine-start",
        "ENGINE_STOP": "engine-stop",
    }
    return {
        "data": [{"command": n, "href": f"/v2/vehicles/{vin}/commands/{pezzi[n]}"} for n in nomi or tuple(pezzi)[:8]]
    }


# --- l'Energy API v2 (senza involucro) -----------------------------------------------------

ENERGIA_EX30 = {
    "batteryChargeLevel": {"status": "OK", "value": 90.0, "unit": "percentage", "updatedAt": "2025-08-07T14:30:32Z"},
    "electricRange": {"status": "OK", "value": 327, "unit": "km", "updatedAt": "2025-08-07T14:30:32Z"},
    "chargerConnectionStatus": {"status": "OK", "value": "CONNECTED", "updatedAt": "2025-08-07T14:30:32Z"},
    "chargingStatus": {"status": "OK", "value": "DONE", "updatedAt": "2025-08-07T14:30:32Z"},
    "chargingType": {"status": "OK", "value": "AC", "updatedAt": "2025-08-07T14:30:32Z"},
    "chargerPowerStatus": {"status": "OK", "value": "FAULT", "updatedAt": "2025-08-07T14:30:32Z"},
    "estimatedChargingTimeToTargetBatteryChargeLevel": {
        "status": "OK",
        "value": 2,
        "unit": "minutes",
        "updatedAt": "2025-08-07T14:30:32Z",
    },
    "chargingCurrentLimit": {
        "status": "ERROR",
        "code": "NOT_SUPPORTED",
        "message": "Resource is not supported for this vehicle",
    },
    "targetBatteryChargeLevel": {
        "status": "OK",
        "value": 90,
        "unit": "percentage",
        "updatedAt": "2025-08-07T14:49:50Z",
    },
    "chargingPower": {
        "status": "ERROR",
        "code": "PROPERTY_NOT_FOUND",
        "message": "No valid value could be found for the requested property",
    },
}

ENERGIA_XC40 = {
    "batteryChargeLevel": {"status": "OK", "value": 53, "unit": "percentage", "updatedAt": "2025-07-02T08:51:23Z"},
    "electricRange": {"status": "OK", "value": 150, "unit": "mi", "updatedAt": "2025-07-02T08:51:23Z"},
    "chargerConnectionStatus": {"status": "OK", "value": "CONNECTED", "updatedAt": "2025-07-02T08:51:23Z"},
    "chargingStatus": {"status": "OK", "value": "CHARGING", "updatedAt": "2025-07-02T08:51:23Z"},
    "chargingType": {"status": "OK", "value": "AC", "updatedAt": "2025-07-02T08:51:23Z"},
    "chargerPowerStatus": {"status": "OK", "value": "PROVIDING_POWER", "updatedAt": "2025-07-02T08:51:23Z"},
    "estimatedChargingTimeToTargetBatteryChargeLevel": {
        "status": "OK",
        "value": 1440,
        "unit": "minutes",
        "updatedAt": "2025-07-02T08:51:23Z",
    },
    "chargingCurrentLimit": {"status": "OK", "value": 32, "unit": "ampere", "updatedAt": "2024-03-05T08:38:44Z"},
    "targetBatteryChargeLevel": {
        "status": "OK",
        "value": 90,
        "unit": "percentage",
        "updatedAt": "2024-09-22T09:40:12Z",
    },
    "chargingPower": {"status": "OK", "value": 1386, "unit": "watts", "updatedAt": "2025-07-02T08:51:23Z"},
}

ENERGIA_XC90 = {
    "batteryChargeLevel": {"status": "OK", "value": 87.3, "unit": "percentage", "updatedAt": "2025-09-05T07:58:14Z"},
    "electricRange": {"status": "OK", "value": 26, "unit": "miles", "updatedAt": "2025-09-05T07:58:14Z"},
    "chargerConnectionStatus": {"status": "OK", "value": "DISCONNECTED", "updatedAt": "2025-09-05T07:58:14Z"},
    "chargingStatus": {"status": "OK", "value": "IDLE", "updatedAt": "2025-09-05T07:58:14Z"},
    "chargingType": {"status": "OK", "value": "NONE", "updatedAt": "2025-09-05T07:58:14Z"},
    "chargerPowerStatus": {"status": "OK", "value": "NO_POWER_AVAILABLE", "updatedAt": "2025-09-05T07:58:14Z"},
    "estimatedChargingTimeToTargetBatteryChargeLevel": {
        "status": "OK",
        "value": 0,
        "unit": "minutes",
        "updatedAt": "2025-09-05T07:58:14Z",
    },
    "chargingCurrentLimit": {"status": "ERROR", "code": "NOT_SUPPORTED"},
    "targetBatteryChargeLevel": {"status": "ERROR", "code": "ERROR_READING_PROPERTY"},
    "chargingPower": {"status": "ERROR", "code": "NOT_SUPPORTED"},
}

CAPACITA_SI = {
    "getEnergyState": {
        "isSupported": True,
        "batteryChargeLevel": {"isSupported": True},
        "electricRange": {"isSupported": True},
        "chargerConnectionStatus": {"isSupported": True},
        "chargingSystemStatus": {"isSupported": True},
        "chargingType": {"isSupported": True},
        "chargerPowerStatus": {"isSupported": True},
        "estimatedChargingTimeToTargetBatteryChargeLevel": {"isSupported": True},
        "targetBatteryChargeLevel": {"isSupported": True},
        "chargingCurrentLimit": {"isSupported": False},
        "chargingPower": {"isSupported": True},
    }
}

# --- l'Energy API v1 (recharge-status), quella vecchia -------------------------------------

RICARICA_V1 = {
    "data": {
        "estimatedChargingTime": {"value": "780", "unit": "minutes", "timestamp": "2024-12-30T14:30:08Z"},
        "batteryChargeLevel": {"value": "58.0", "unit": "percentage", "timestamp": "2024-12-30T14:30:08Z"},
        "electricRange": {"value": "250", "unit": "kilometers", "timestamp": "2024-12-30T14:30:08Z"},
        "chargingSystemStatus": {"value": "CHARGING_SYSTEM_CHARGING", "timestamp": "2024-12-30T14:30:08Z"},
        "chargingConnectionStatus": {"value": "CONNECTION_STATUS_CONNECTED_DC", "timestamp": "2024-12-30T14:30:08Z"},
    }
}

# --- la Connected Vehicle API v2 ----------------------------------------------------------

PORTE = {
    "data": {
        "centralLock": _campo("LOCKED", "2024-12-30T14:20:20.570Z"),
        "frontLeftDoor": _campo("CLOSED", "2024-12-30T14:20:20.570Z"),
        "frontRightDoor": _campo("CLOSED", "2024-12-30T14:20:20.570Z"),
        "rearLeftDoor": _campo("CLOSED", "2024-12-30T14:20:20.570Z"),
        "rearRightDoor": _campo("CLOSED", "2024-12-30T14:20:20.570Z"),
        "hood": _campo("CLOSED", "2024-12-30T14:20:20.570Z"),
        "tailgate": _campo("CLOSED", "2024-12-30T14:20:20.570Z"),
        "tankLid": _campo("CLOSED", "2024-12-30T14:20:20.570Z"),
    }
}

PORTE_APERTE = {
    "data": {
        "centralLock": _campo("UNLOCKED", "2024-12-31T09:00:00.000Z"),
        "frontLeftDoor": _campo("AJAR", "2024-12-31T09:00:00.000Z"),
        "frontRightDoor": _campo("CLOSED", "2024-12-31T09:00:00.000Z"),
        "rearLeftDoor": _campo("UNSPECIFIED", "2024-12-31T09:00:00.000Z"),
        "rearRightDoor": _campo("CLOSED", "2024-12-31T09:00:00.000Z"),
        "hood": _campo("CLOSED", "2024-12-31T09:00:00.000Z"),
        "tailgate": _campo("OPEN", "2024-12-31T09:00:00.000Z"),
    }
}

FINESTRINI = {
    "data": {
        "frontLeftWindow": _campo("CLOSED", "2024-12-30T14:28:12.202Z"),
        "frontRightWindow": _campo("CLOSED", "2024-12-30T14:28:12.202Z"),
        "rearLeftWindow": _campo("CLOSED", "2024-12-30T14:28:12.202Z"),
        "rearRightWindow": _campo("CLOSED", "2024-12-30T14:28:12.202Z"),
        "sunroof": _campo("UNSPECIFIED", "2024-12-30T14:28:12.202Z"),
    }
}

TETTO_APERTO = {
    "data": {
        "frontLeftWindow": _campo("CLOSED", T),
        "frontRightWindow": _campo("CLOSED", T),
        "sunroof": _campo("OPEN", T),
    }
}

MOTORE = {"data": {"engineStatus": _campo("STOPPED", "2024-12-30T15:00:00.000Z")}}
MOTORE_ACCESO = {"data": {"engineStatus": _campo("RUNNING", "2024-12-31T09:00:00.000Z")}}

ODOMETRO = {"data": {"odometer": _campo(30000, T, "km")}}
ODOMETRO_MIGLIA = {"data": {"odometer": _campo(10000, T, "miles")}}

STATISTICHE_EX30 = {
    "data": {
        "averageEnergyConsumption": _campo(22.6, "2024-12-30T14:53:44.785Z", "kWh/100km"),
        "averageSpeed": _campo(53, T, "km/h"),
        "averageSpeedAutomatic": _campo(26, T, "km/h"),
        "tripMeterManual": _campo(3822.9, T, "km"),
        "tripMeterAutomatic": _campo(18.2, T, "km"),
        "distanceToEmptyBattery": _campo(250, "2024-12-30T14:30:08.338Z", "km"),
    }
}

STATISTICHE_XC90 = {
    "data": {
        "averageFuelConsumption": _campo(2.0, "2025-09-04T18:03:57.437Z", "l/100km"),
        "averageEnergyConsumption": _campo(19.9, "2025-09-04T18:03:57.437Z", "kWh/100km"),
        "averageSpeed": _campo(47, "2025-09-04T18:03:57.437Z", "km/h"),
        "tripMeterManual": _campo(5935.8, "2025-09-04T18:03:57.437Z", "km"),
        "distanceToEmptyTank": _campo(804, "2025-09-04T18:03:57.437Z", "km"),
        "distanceToEmptyBattery": _campo(43, "2025-09-05T07:58:14.760Z", "km"),
    }
}

CARBURANTE = {
    "data": {
        "fuelAmount": _campo("47.3", "2020-11-19T21:23:24.123Z", "l"),
        "batteryChargeLevel": _campo("87.3", "2020-11-19T21:23:24.123Z", "%"),
    }
}

POSIZIONE = {
    "data": {
        "type": "Feature",
        "properties": {"timestamp": "2024-12-30T15:00:00.000Z", "heading": "90"},
        "geometry": {"type": "Point", "coordinates": [11.849843629550225, 57.72537482589284, 0.0]},
    }
}

DISPONIBILE = {"data": {"availabilityStatus": _campo("AVAILABLE", "2024-12-30T14:32:26.169Z")}}
ADDORMENTATA = {
    "data": {
        "availabilityStatus": {
            "value": "UNAVAILABLE",
            "unavailableReason": "POWER_SAVING_MODE",
            "timestamp": "2024-12-30T14:32:26.169Z",
        }
    }
}

GOMME = {
    "data": {
        "frontLeft": _campo("NO_WARNING", T),
        "frontRight": _campo("NO_WARNING", T),
        "rearLeft": _campo("NO_WARNING", T),
        "rearRight": _campo("NO_WARNING", T),
    }
}
GOMME_SCONOSCIUTE = {
    "data": {n: _campo("UNSPECIFIED", T) for n in ("frontLeft", "frontRight", "rearLeft", "rearRight")}
}
GOMMA_BASSA = {"data": {**GOMME["data"], "rearLeft": _campo("LOW_PRESSURE", T)}}

DIAGNOSTICA = {
    "data": {
        "serviceWarning": _campo("NO_WARNING", T),
        "engineHoursToService": _campo(1266, T, "h"),
        "distanceToService": _campo(29000, T, "km"),
        "washerFluidLevelWarning": _campo("TOO_LOW", T),
        "timeToService": _campo(23, T, "months"),
    }
}

AVVISI = {
    "data": {
        "brakeLightCenterWarning": _campo("NO_WARNING", T),
        "brakeLightLeftWarning": _campo("FAILURE", T),
        "fogLightFrontWarning": _campo("NO_WARNING", T),
        "hazardLightsWarning": _campo("UNSPECIFIED", T),
    }
}

FRENI = {"data": {"brakeFluidLevelWarning": _campo("NO_WARNING", T)}}
AVVISI_MOTORE = {
    "data": {
        "oilLevelWarning": _campo("NO_WARNING", T),
        "engineCoolantLevelWarning": _campo("NO_WARNING", T),
    }
}
