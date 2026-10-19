"""Risposte d'esempio del cloud Volkswagen (BFF di CARIAD), per le prove.

La forma (sezioni, nomi dei campi, valori possibili) è quella che leggono
tillsteinbach/CarConnectivity-connector-volkswagen (MIT) e i suoi esempi; i
valori sono inventati, e i VIN finti.
"""

from __future__ import annotations

VIN_ID3 = "WVWZZZE1FINTO0001"
VIN_EUP = "WVWZZZAAFINTO0002"
VIN_GTE = "WVWZZZAUFINTO0003"
VIN_DIESEL = "WVWZZZCDFINTO0004"


def _capacita(*ids: str, spenti: tuple[str, ...] = ()) -> list[dict[str, object]]:
    voci: list[dict[str, object]] = [
        {"id": i, "expirationDate": "2030-01-01T00:00:00Z", "userDisablingAllowed": False, "status": []} for i in ids
    ]
    voci += [{"id": i, "userDisablingAllowed": True, "status": [1007]} for i in spenti]
    return voci


ELENCO = {
    "data": [
        {
            "vin": VIN_ID3,
            "role": "PRIMARY_USER",
            "enrollmentStatus": "completed",
            "model": "ID.3",
            "nickname": "Fulmine",
            "capabilities": _capacita(
                "access",
                "charging",
                "climatisation",
                "fuelStatus",
                "measurements",
                "readiness",
                "honkAndFlash",
                "parkingPosition",
                "vehicleHealthInspection",
                "batteryChargingCare",
                "tripStatistics",
            ),
        },
        {
            "vin": VIN_EUP,
            "role": "SECONDARY_USER",
            "model": "e-up!",
            "nickname": None,
            # L'e-up! non ha il servizio della serratura: lo ha spento la licenza.
            "capabilities": _capacita("charging", "climatisation", "fuelStatus", spenti=("access",)),
        },
        {
            "vin": VIN_GTE,
            "role": "PRIMARY_USER",
            "model": "Golf GTE",
            "nickname": "Ibrida",
            "capabilities": _capacita("access", "charging", "climatisation", "fuelStatus", "measurements"),
        },
        # Una diesel: niente «charging», GdAAuto non la prende.
        {"vin": VIN_DIESEL, "model": "Golf", "capabilities": _capacita("access", "fuelStatus", "measurements")},
    ]
}

#: Una ID.3 in carica a una colonnina rapida, ferma e chiusa.
ID3 = {
    "access": {
        "accessStatus": {
            "value": {
                "overallStatus": "safe",
                "carCapturedTimestamp": "2025-04-20T18:01:10Z",
                "doors": [
                    {"name": "bonnet", "status": ["closed"]},
                    {"name": "frontLeft", "status": ["locked", "closed"]},
                    {"name": "frontRight", "status": ["locked", "closed"]},
                    {"name": "rearLeft", "status": ["locked", "closed"]},
                    {"name": "rearRight", "status": ["locked", "closed"]},
                    {"name": "trunk", "status": ["locked", "closed"]},
                ],
                "windows": [
                    {"name": "frontLeft", "status": ["closed"]},
                    {"name": "frontRight", "status": ["closed"]},
                    {"name": "rearLeft", "status": ["closed"]},
                    {"name": "rearRight", "status": ["closed"]},
                    {"name": "roofCover", "status": ["unsupported"]},
                    {"name": "sunRoof", "status": ["unsupported"]},
                ],
                "doorLockStatus": "locked",
            }
        }
    },
    "charging": {
        "batteryStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:42Z",
                "currentSOC_pct": 63,
                "cruisingRangeElectric_km": 251,
            }
        },
        "chargingStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:42Z",
                "remainingChargingTimeToComplete_min": 25,
                "chargingState": "charging",
                "chargeMode": "manual",
                "chargePower_kW": 87.5,
                "chargeRate_kmph": 520,
                "chargeType": "dc",
                "chargingSettings": "default",
            }
        },
        "chargingSettings": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T17:40:00Z",
                "maxChargeCurrentAC": "maximum",
                "autoUnlockPlugWhenCharged": "off",
                "autoUnlockPlugWhenChargedAC": "off",
                "targetSOC_pct": 80,
            }
        },
        "plugStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:40Z",
                "plugConnectionState": "connected",
                "plugLockState": "locked",
                "externalPower": "active",
                "ledColor": "green",
            }
        },
        "chargeMode": {"value": {"preferredChargeMode": "manual", "availableChargeModes": ["manual", "timer"]}},
    },
    "climatisation": {
        "climatisationSettings": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T17:40:00Z",
                "targetTemperature_C": 21.5,
                "targetTemperature_F": 71,
                "unitInCar": "celsius",
                "climatisationWithoutExternalPower": True,
                "climatizationAtUnlock": False,
                "windowHeatingEnabled": True,
                "zoneFrontLeftEnabled": True,
                "zoneFrontRightEnabled": False,
            }
        },
        "climatisationStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:30Z",
                "remainingClimatisationTime_min": 0,
                "climatisationState": "off",
            }
        },
        "windowHeatingStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:30Z",
                "windowHeatingStatus": [
                    {"windowLocation": "front", "windowHeatingState": "off"},
                    {"windowLocation": "rear", "windowHeatingState": "off"},
                ],
            }
        },
    },
    "fuelStatus": {
        "rangeStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:42Z",
                "carType": "electric",
                "primaryEngine": {"type": "electric", "currentSOC_pct": 63, "remainingRange_km": 251},
                "totalRange_km": 251,
            }
        }
    },
    "measurements": {
        "rangeStatus": {
            "value": {"carCapturedTimestamp": "2025-04-20T18:05:42Z", "electricRange": 251, "totalRange_km": 251}
        },
        "odometerStatus": {"value": {"carCapturedTimestamp": "2025-04-20T18:01:10Z", "odometer": 23817}},
        "temperatureOutsideStatus": {
            "value": {"carCapturedTimestamp": "2025-04-20T18:01:10Z", "temperatureOutside_K": 287.15}
        },
        "temperatureBatteryStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:42Z",
                "temperatureHvBatteryMin_K": 303.15,
                "temperatureHvBatteryMax_K": 305.15,
            }
        },
        "fuelLevelStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:05:42Z",
                "currentSOC_pct": 63,
                "primaryEngineType": "electric",
                "carType": "electric",
            }
        },
    },
    "readiness": {
        "readinessStatus": {
            "value": {
                "connectionState": {
                    "isOnline": True,
                    "isActive": False,
                    "batteryPowerLevel": "comfort",
                    "dailyPowerBudgetAvailable": True,
                },
                "connectionWarning": {"insufficientBatteryLevelWarning": False},
            }
        }
    },
    "vehicleHealthInspection": {
        "maintenanceStatus": {
            "value": {
                "carCapturedTimestamp": "2025-04-20T18:01:10Z",
                "inspectionDue_days": 290,
                "inspectionDue_km": 6200,
                "mileage_km": 23817,
            }
        }
    },
    # Un servizio che non va: si salta, il resto si legge.
    "batteryChargingCare": {
        "chargingCareSettings": {"error": {"message": "Bad Gateway", "code": 4111, "group": 2, "retry": True}}
    },
}

#: Un'e-up! ferma, scollegata, aperta, col clima acceso; il cloud ha solo il «fuelStatus».
EUP = {
    "access": {
        "accessStatus": {
            "value": {
                "overallStatus": "unsafe",
                "carCapturedTimestamp": "2025-01-10T07:20:00Z",
                "doors": [
                    {"name": "frontLeft", "status": ["unlocked", "open"]},
                    {"name": "frontRight", "status": ["unlocked", "closed"]},
                    {"name": "trunk", "status": ["unlocked", "closed"]},
                ],
                "windows": [{"name": "frontLeft", "status": ["unsupported"]}],
                "doorLockStatus": "unlocked",
            }
        }
    },
    "charging": {
        "chargingStatus": {
            "value": {
                "carCapturedTimestamp": "2025-01-10T07:18:00Z",
                "remainingChargingTimeToComplete_min": 80,
                "chargingState": "notReadyForCharging",
                "chargeMode": "",
                "chargeType": "",
            }
        },
        "plugStatus": {
            "value": {
                "carCapturedTimestamp": "2025-01-10T07:18:00Z",
                "plugConnectionState": "disconnected",
                "plugLockState": "unlocked",
                "externalPower": "unavailable",
            }
        },
        "chargeMode": {"error": {"message": "Bad Gateway", "code": 4111, "group": 2, "retry": True}},
    },
    "climatisation": {
        "climatisationSettings": {
            "value": {
                "carCapturedTimestamp": "2025-01-10T07:00:00Z",
                "targetTemperature_K": 295.15,
                "climatisationWithoutExternalPower": True,
            }
        },
        "climatisationStatus": {
            "value": {
                "carCapturedTimestamp": "2025-01-10T07:19:00Z",
                "remainingClimatisationTime_min": 12,
                "climatisationState": "heating",
            }
        },
        "windowHeatingStatus": {
            "value": {
                "carCapturedTimestamp": "2025-01-10T07:19:00Z",
                "windowHeatingStatus": [
                    {"windowLocation": "front", "windowHeatingState": "on"},
                    {"windowLocation": "rear", "windowHeatingState": "off"},
                ],
            }
        },
    },
    "fuelStatus": {
        "rangeStatus": {
            "value": {
                "carCapturedTimestamp": "2025-01-10T07:18:00Z",
                "carType": "electric",
                "primaryEngine": {"type": "electric", "currentSOC_pct": 44, "remainingRange_km": 96},
                "totalRange_km": 96,
            }
        }
    },
}

#: Una Golf GTE: benzina e batteria, collegata e in attesa della ricarica a orario.
GTE = {
    "charging": {
        "batteryStatus": {
            "value": {
                "carCapturedTimestamp": "2025-02-02T21:00:15Z",
                "currentSOC_pct": 35,
                "cruisingRangeElectric_km": 18,
            }
        },
        "chargingStatus": {
            "value": {
                "carCapturedTimestamp": "2025-02-02T21:00:15Z",
                "chargingState": "readyForCharging",
                "chargeMode": "timer",
                "chargeType": "",
                "chargePower_kW": 0,
            }
        },
        "chargingSettings": {
            "value": {
                "carCapturedTimestamp": "2025-02-02T20:00:00Z",
                "maxChargeCurrentAC_A": 10,
                "autoUnlockPlugWhenChargedAC": "on",
            }
        },
        "plugStatus": {
            "value": {
                "carCapturedTimestamp": "2025-02-02T21:00:15Z",
                "plugConnectionState": "connected",
                "plugLockState": "locked",
                "externalPower": "available",
            }
        },
    },
    "fuelStatus": {
        "rangeStatus": {
            "value": {
                "carCapturedTimestamp": "2025-02-02T21:00:15Z",
                "carType": "hybrid",
                "primaryEngine": {
                    "type": "gasoline",
                    "currentSOC_pct": 0,
                    "remainingRange_km": 410,
                    "currentFuelLevel_pct": 58,
                },
                "secondaryEngine": {"type": "electric", "currentSOC_pct": 35, "remainingRange_km": 18},
                "totalRange_km": 428,
            }
        }
    },
    "measurements": {
        "odometerStatus": {"value": {"carCapturedTimestamp": "2025-02-02T21:00:15Z", "odometer": 61002}},
        "temperatureOutsideStatus": {
            "value": {"carCapturedTimestamp": "2025-02-02T21:00:15Z", "temperatureOutside_K": 270.65}
        },
    },
    "readiness": {"readinessStatus": {"value": {"connectionState": {"isOnline": False, "isActive": False}}}},
}

PARCHEGGIO = {"data": {"lon": 7.68201, "lat": 45.07049, "carCapturedTimestamp": "2025-04-20T17:58:00Z"}}

VIAGGIO_LUNGO = {
    "data": {
        "id": "3000000001",
        "tripEndTimestamp": "2025-04-20T17:58:00Z",
        "tripType": "longTerm",
        "vehicleType": "electric",
        "mileage_km": 3120,
        "startMileage_km": 20697,
        "overallMileage_km": 23817,
        "travelTime": 2900,
        "averageElectricConsumption": 16.4,
        "averageAuxConsumption": 1.2,
        "averageRecuperation": 4.8,
        "averageSpeed_kmph": 64,
    }
}
