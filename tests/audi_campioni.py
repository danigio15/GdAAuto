"""Risposte del cloud Audi per le prove.

``Q8_ETRON`` è il ``selectivestatus`` vero di una Q8 Sportback e-tron del 2024
(18 agosto 2026), dal fixture di audiconnect/audi_connect_ha (licenza MIT,
``tests/fixture_q8_etron.py``), con le date come le manda il cloud (testo ISO)
e le liste dei timer accorciate. Le altre sono costruite con gli stessi campi
e i valori che i due progetti di riferimento leggono (CarConnectivity-connector-
audi per le temperature in kelvin, la prontezza e i tipi di ricarica).
"""

from __future__ import annotations

from typing import Any

VIN_Q8 = "WAUZZZGE0RB000001"
VIN_Q4 = "WAUZZZF40PA000002"
VIN_A3 = "WAUZZZGY0NA000003"

T = "2026-08-18T21:09:17Z"

Q8_ETRON: dict[str, Any] = {
    "access": {
        "accessStatus": {
            "value": {
                "carCapturedTimestamp": T,
                "doorLockStatus": "locked",
                "doors": [
                    {"name": "bonnet", "status": ["closed"]},
                    {"name": "frontLeft", "status": ["locked", "closed"]},
                    {"name": "frontRight", "status": ["locked", "closed"]},
                    {"name": "rearLeft", "status": ["locked", "closed"]},
                    {"name": "rearRight", "status": ["locked", "closed"]},
                    {"name": "trunk", "status": ["locked", "closed"]},
                ],
                "overallStatus": "safe",
                "windows": [
                    {"name": "frontLeft", "status": ["closed"]},
                    {"name": "frontRight", "status": ["closed"]},
                    {"name": "rearLeft", "status": ["closed"]},
                    {"name": "rearRight", "status": ["closed"]},
                    {"name": "roofCover", "status": ["unsupported"]},
                    {"name": "sunRoof", "status": ["unsupported"]},
                    {"name": "sunRoofRear", "status": ["unsupported"]},
                ],
            }
        }
    },
    "charging": {
        "batteryStatus": {"value": {"carCapturedTimestamp": T, "cruisingRangeElectric_km": 300, "currentSOC_pct": 69}},
        "chargeMode": {"value": {"availableChargeModes": [], "preferredChargeMode": "manual"}},
        "chargingSettings": {
            "value": {
                "autoUnlockPlugWhenChargedDC": "off",
                "carCapturedTimestamp": "2026-08-18T21:01:03Z",
                "targetSOC_pct": 70,
            }
        },
        "chargingStatus": {
            "value": {
                "carCapturedTimestamp": T,
                "chargeMode": "manual",
                "chargePower_kW": 0,
                "chargeRate_kmph": 0,
                "chargeType": "off",
                "chargingState": "readyForCharging",
                "remainingChargingTimeToComplete_min": 0,
            }
        },
        "plugStatus": {
            "value": {
                "carCapturedTimestamp": T,
                "externalPower": "ready",
                "ledColor": "green",
                "plugConnectionState": "connected",
                "plugLockState": "locked",
            }
        },
    },
    "chargingProfiles": {
        "chargingProfilesStatus": {
            "value": {
                "carCapturedTimestamp": "2026-08-18T21:01:04Z",
                "nextChargingTimer": {"id": 0, "targetSOCreachable": ""},
                "profiles": [
                    {
                        "id": 1,
                        "minSOC_enabled": False,
                        "minSOC_pct": 2,
                        "name": "Home",
                        "options": {"autoUnlockPlugWhenCharged": "unsupported"},
                        "position": {"lat": 0.0, "lon": 0.0},
                        "preferredChargingTimes": [
                            {"enabled": False, "endTimeLocal": "02:09", "id": 1, "startTimeLocal": "23:13"}
                        ],
                        "targetSOC_pct": 60,
                        "timers": [],
                    },
                    {
                        "id": 2,
                        "minSOC_enabled": False,
                        "minSOC_pct": 2,
                        "name": "Work",
                        "options": {"autoUnlockPlugWhenCharged": "unsupported"},
                        "position": {"lat": 0.0, "lon": 0.0},
                        "preferredChargingTimes": [
                            {"enabled": False, "endTimeLocal": "00:00", "id": 1, "startTimeLocal": "00:00"}
                        ],
                        "targetSOC_pct": 100,
                        "timers": [],
                    },
                ],
                "vehiclePositionedInProfileID": 1,
            }
        }
    },
    "chargingTimers": {
        "chargingTimersStatus": {
            "value": {
                "carCapturedTimestamp": "2026-08-18T17:57:12Z",
                "timeInCar": "",
                "timers": [
                    {
                        "climatisation": False,
                        "enabled": False,
                        "id": 1,
                        "recurringTimer": {
                            "departureTimeLocal": "11:05",
                            "repetitionDays": ["thursday"],
                            "targetTimeLocal": "11:05",
                        },
                    }
                ],
            }
        }
    },
    "climatisation": {
        "climatisationSettings": {
            "value": {
                "carCapturedTimestamp": "2026-08-18T21:09:02Z",
                "climatisationWithoutExternalPower": True,
                "climatizationAtUnlock": False,
                "targetTemperature_C": 15.5,
                "targetTemperature_F": 59,
                "windowHeatingEnabled": False,
                "zoneFrontLeftEnabled": False,
                "zoneFrontRightEnabled": False,
            }
        },
        "climatisationStatus": {
            "value": {
                "carCapturedTimestamp": T,
                "climatisationState": "off",
                "remainingClimatisationTime_min": -256,
            }
        },
        "windowHeatingStatus": {
            "value": {
                "carCapturedTimestamp": "2026-08-18T21:31:52Z",
                "windowHeatingStatus": [
                    {"windowHeatingState": "invalid", "windowLocation": "front"},
                    {"windowHeatingState": "invalid", "windowLocation": "rear"},
                ],
            }
        },
    },
    "fuelStatus": {
        "rangeStatus": {
            "value": {
                "carCapturedTimestamp": T,
                "carType": "electric",
                "primaryEngine": {"currentSOC_pct": 69, "remainingRange_km": 300, "type": "electric"},
                "totalRange_km": 300,
            }
        }
    },
    "measurements": {
        "fuelLevelStatus": {
            "value": {
                "carCapturedTimestamp": T,
                "carType": "electric",
                "currentSOC_pct": 69,
                "primaryEngineType": "electric",
            }
        },
        "odometerStatus": {"value": {"carCapturedTimestamp": T, "odometer": 43740}},
        "rangeStatus": {"value": {"carCapturedTimestamp": T, "electricRange": 300, "totalRange_km": 300}},
    },
    "userCapabilities": {
        "capabilitiesStatus": {
            "value": [
                {"id": "access", "expirationDate": "2033-09-30", "userDisablingAllowed": True},
                {"id": "charging", "expirationDate": "2033-09-30", "userDisablingAllowed": True},
                {"id": "climatisation", "expirationDate": "2033-09-30", "userDisablingAllowed": True},
                {"id": "honkAndFlash", "expirationDate": "2033-09-30", "userDisablingAllowed": True},
                {"id": "onlineLogBook", "status": [2003], "expirationDate": "2033-09-30"},
                {"id": "parkingPosition", "expirationDate": "2033-09-30", "userDisablingAllowed": True},
                {"id": "tripStatistics", "expirationDate": "2033-09-30", "userDisablingAllowed": True},
            ]
        }
    },
    "vehicleHealthInspection": {
        "maintenanceStatus": {
            "value": {
                "carCapturedTimestamp": "2026-08-18T21:09:15Z",
                "inspectionDue_days": 381,
                "inspectionDue_km": 16898,
                "mileage_km": 43740,
            }
        }
    },
    "vehicleHealthWarnings": {"warningLights": {"value": {}}},
    "vehicleLights": {
        "lightsStatus": {
            "value": {
                "carCapturedTimestamp": T,
                "lights": [{"name": "right", "status": "off"}, {"name": "left", "status": "off"}],
            }
        }
    },
    # I lavori che la Q8 non ha tornano con un errore al posto di «value».
    "lvBattery": {"lvBatteryStatus": {"error": {"message": "Not supported", "errorTimeStamp": T}}},
}

T4 = "2026-10-17T18:42:05Z"

#: Una Q4 in carica in corrente alternata, con una portiera e il bagagliaio aperti e i dati della prontezza.
Q4_IN_CARICA: dict[str, Any] = {
    "access": {
        "accessStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "doorLockStatus": "unlocked",
                "doors": [
                    {"name": "bonnet", "status": ["closed"]},
                    {"name": "frontLeft", "status": ["unlocked", "open"]},
                    {"name": "frontRight", "status": ["unlocked", "closed"]},
                    {"name": "rearLeft", "status": ["unlocked", "closed"]},
                    {"name": "rearRight", "status": ["unlocked", "closed"]},
                    {"name": "trunk", "status": ["unlocked", "open"]},
                ],
                "overallStatus": "unsafe",
                "windows": [
                    {"name": "frontLeft", "status": ["open"]},
                    {"name": "frontRight", "status": ["closed"]},
                    {"name": "rearLeft", "status": ["closed"]},
                    {"name": "rearRight", "status": ["closed"]},
                    {"name": "sunRoof", "status": ["unsupported"]},
                ],
            }
        }
    },
    "charging": {
        "batteryStatus": {"value": {"carCapturedTimestamp": T4, "cruisingRangeElectric_km": 212, "currentSOC_pct": 54}},
        "chargeMode": {"value": {"availableChargeModes": ["manual", "timer"], "preferredChargeMode": "manual"}},
        "chargingSettings": {
            "value": {
                "carCapturedTimestamp": T4,
                "maxChargeCurrentAC": "maximum",
                "autoUnlockPlugWhenChargedAC": "permanent",
                "targetSOC_pct": 80,
            }
        },
        "chargingStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "chargeMode": "manual",
                "chargePower_kW": 10.8,
                "chargeRate_kmph": 52,
                "chargeType": "ac",
                "chargingState": "charging",
                "remainingChargingTimeToComplete_min": 95,
            }
        },
        "plugStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "externalPower": "active",
                "ledColor": "green",
                "plugConnectionState": "connected",
                "plugLockState": "locked",
            }
        },
    },
    "chargingProfiles": {
        "chargingProfilesStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "profiles": [
                    {
                        "id": 3,
                        "name": "Casa",
                        "preferredChargingTimes": [
                            {"enabled": True, "endTimeLocal": "06:00", "id": 1, "startTimeLocal": "23:30"}
                        ],
                        "targetSOC_pct": 80,
                        "timers": [],
                    }
                ],
                "vehiclePositionedInProfileID": 3,
            }
        }
    },
    "climatisation": {
        "climatisationSettings": {
            "value": {
                "carCapturedTimestamp": T4,
                "climatisationWithoutExternalPower": True,
                "climatizationAtUnlock": False,
                "targetTemperature_C": 21.5,
                "windowHeatingEnabled": True,
                "zoneFrontLeftEnabled": True,
                "zoneFrontRightEnabled": False,
                "heaterSource": "electric",
            }
        },
        "climatisationStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "climatisationState": "heating",
                "remainingClimatisationTime_min": 18,
            }
        },
        "windowHeatingStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "windowHeatingStatus": [
                    {"windowHeatingState": "on", "windowLocation": "front"},
                    {"windowHeatingState": "off", "windowLocation": "rear"},
                ],
            }
        },
    },
    "measurements": {
        "odometerStatus": {"value": {"carCapturedTimestamp": T4, "odometer": 18215}},
        "temperatureOutsideStatus": {"value": {"carCapturedTimestamp": T4, "temperatureOutside_K": 281.15}},
        "temperatureBatteryStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "temperatureHvBatteryMin_K": 293.15,
                "temperatureHvBatteryMax_K": 295.15,
            }
        },
    },
    "readiness": {
        "readinessStatus": {
            "value": {"connectionState": {"isOnline": True, "isActive": False, "batteryPowerLevel": "comfort"}}
        }
    },
    "vehicleHealthInspection": {
        "maintenanceStatus": {
            "value": {"carCapturedTimestamp": T4, "inspectionDue_days": 512, "inspectionDue_km": 21785}
        }
    },
}

#: Un'A3 Sportback 40 TFSI e (ibrida plug-in): benzina come motore primario, la batteria come secondario.
A3_TFSI_E: dict[str, Any] = {
    "charging": {
        "batteryStatus": {"value": {"carCapturedTimestamp": T4, "cruisingRangeElectric_km": 48, "currentSOC_pct": 81}},
        "chargingStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "chargingState": "readyForCharging",
                "chargeType": "invalid",
                "chargePower_kW": 0,
                "remainingChargingTimeToComplete_min": 0,
            }
        },
        "chargeMode": {"value": {"preferredChargeMode": "timer"}},
        "plugStatus": {"value": {"carCapturedTimestamp": T4, "plugConnectionState": "connected"}},
    },
    "fuelStatus": {
        "rangeStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "carType": "hybrid",
                "primaryEngine": {"type": "gasoline", "currentFuelLevel_pct": 62, "remainingRange_km": 418},
                "secondaryEngine": {"type": "electric", "currentSOC_pct": 81, "remainingRange_km": 48},
                "totalRange_km": 466,
            }
        }
    },
    "measurements": {
        "fuelLevelStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "carType": "hybrid",
                "currentFuelLevel_pct": 62,
                "currentSOC_pct": 81,
                "primaryEngineType": "gasoline",
                "secondaryEngineType": "electric",
            }
        },
        "odometerStatus": {"value": {"carCapturedTimestamp": T4, "odometer": 61022}},
    },
    "vehicleHealthInspection": {
        "maintenanceStatus": {
            "value": {
                "carCapturedTimestamp": T4,
                "inspectionDue_days": 120,
                "inspectionDue_km": 9100,
                "oilServiceDue_days": 120,
                "oilServiceDue_km": 9100,
            }
        }
    },
    "readiness": {"readinessStatus": {"value": {"connectionState": {"isOnline": False, "isActive": False}}}},
}

#: Un guasto di CARIAD: 200, ma ogni lavoro torna con un errore (audi_connect_ha #793).
TUTTI_IN_ERRORE: dict[str, Any] = {
    lavoro: {"status": {"error": {"message": "Upstream service responded with an unexpected status", "code": 4111}}}
    for lavoro in ("access", "charging", "climatisation", "measurements", "fuelStatus")
}

POSIZIONE: dict[str, Any] = {
    "data": {"lat": 45.464211, "lon": 9.191383, "carCapturedTimestamp": "2026-10-17T18:43:00Z"}
}

#: ``/vehicle/v1/vehicles``: la Q8 col suo nome, la Q4 senza, e condivisa.
VEICOLI: dict[str, Any] = {
    "data": [
        {
            "vin": VIN_Q8,
            "role": "PRIMARY_USER",
            "userRoleStatus": "ENABLED",
            "enrollmentStatus": "COMPLETED",
            "model": "Q8 Sportback e-tron",
            "nickname": "Anello",
            "devicePlatform": "MBB_ODP",
            "capabilities": [
                {"id": "access", "expirationDate": "2033-09-30T00:00:00Z", "userDisablingAllowed": True},
                {"id": "charging", "expirationDate": "2033-09-30T00:00:00Z", "userDisablingAllowed": True},
                {"id": "climatisation", "expirationDate": "2033-09-30T00:00:00Z", "userDisablingAllowed": True},
                {"id": "honkAndFlash", "expirationDate": "2033-09-30T00:00:00Z", "userDisablingAllowed": True},
                {"id": "parkingPosition", "expirationDate": "2033-09-30T00:00:00Z", "userDisablingAllowed": True},
            ],
        },
        {
            "vin": VIN_Q4,
            "role": "SECONDARY_USER",
            "userRoleStatus": "ENABLED",
            "model": "Q4 Sportback e-tron",
            "nickname": None,
            "devicePlatform": "WCAR",
            "capabilities": [
                {"id": "charging"},
                {"id": "climatisation", "status": [1007]},
                {"id": "access", "status": [1001]},
            ],
        },
    ]
}

#: Le statistiche di viaggio (``tripdata``): consumo in decimi di kWh/100 km, km, istanti.
VIAGGI_LUNGO: dict[str, Any] = {
    "tripDataList": {
        "tripData": [
            {
                "tripID": 1001,
                "averageElectricEngineConsumption": 221,
                "averageSpeed": 54,
                "mileage": 9800,
                "startMileage": 33940,
                "overallMileage": 43740,
                "timestamp": "2026-10-17T17:30:00Z",
                "traveltime": 10900,
            },
            {
                "tripID": 900,
                "averageElectricEngineConsumption": 230,
                "mileage": 5000,
                "startMileage": 28940,
                "overallMileage": 33940,
                "timestamp": "2026-03-01T10:00:00Z",
            },
        ]
    }
}


def viaggi_corti(ora: str = "2026-10-17") -> dict[str, Any]:
    """Tre viaggi della settimana (uno con due record man mano che cresce) e uno vecchio."""
    return {
        "tripDataList": {
            "tripData": [
                {
                    "tripID": 5,
                    "averageElectricEngineConsumption": 200,
                    "mileage": 30,
                    "startMileage": 43700,
                    "overallMileage": 43730,
                    "timestamp": f"{ora}T08:10:00Z",
                },
                {
                    "tripID": 6,
                    "averageElectricEngineConsumption": 180,
                    "mileage": 40,
                    "startMileage": 43700,
                    "overallMileage": 43740,
                    "timestamp": f"{ora}T08:40:00Z",
                },
                {
                    "tripID": 4,
                    "averageElectricEngineConsumption": 250,
                    "mileage": 60,
                    "startMileage": 43640,
                    "overallMileage": 43700,
                    "timestamp": f"{ora}T07:00:00Z",
                },
                {
                    "tripID": 1,
                    "averageElectricEngineConsumption": 300,
                    "mileage": 500,
                    "startMileage": 40000,
                    "overallMileage": 40500,
                    "timestamp": "2026-06-01T07:00:00Z",
                },
            ]
        }
    }
