"""Risposte d'esempio del cloud MyŠkoda, per le prove.

La forma (percorsi, nomi dei campi, valori possibili) è quella dei fixture di
skodaconnect/myskoda (MIT: ``fixtures/*.yaml`` e ``tests/fixtures/``, presi da
auto vere e resi anonimi); alcuni valori sono cambiati per provare i casi, e i
VIN sono finti.
"""

from __future__ import annotations

import base64

VIN_ENYAQ = "TMBENYAQFINTO0001"
VIN_SUPERB = "TMBSUPERBFINTO002"
VIN_OCTAVIA = "TMBOCTAVIFINTO003"

_RENDER = (
    "https://mysmob.api.connect.skoda-auto.cz/api/v2/vehicle-status/render?carType=SUV&vehicleState={}&dimension=1x"
)


def _servizi(*ids: str, spenti: tuple[str, ...] = ()) -> dict[str, object]:
    voci: list[dict[str, object]] = [{"id": i, "statuses": []} for i in ids]
    voci += [{"id": i, "statuses": ["INSUFFICIENT_RIGHTS"]} for i in spenti]
    return {"capabilities": voci}


GARAGE = {
    "vehicles": [
        {
            "vin": VIN_ENYAQ,
            "name": "Fulmine",
            "state": "ACTIVATED",
            "title": "Škoda Enyaq",
            "priority": 1,
            "devicePlatform": "WCAR",
            "systemModelId": "5AZJK2",
        },
        {
            "vin": VIN_SUPERB,
            "name": "Superb",
            "state": "GUEST_USER",
            "title": "Škoda Superb",
            "priority": 2,
            "devicePlatform": "MBB_ODP",
            "systemModelId": "NZ3DYC",
        },
        {
            "vin": VIN_OCTAVIA,
            "name": "Octavia",
            "state": "ACTIVATED",
            "title": "Škoda Octavia Combi",
            "priority": 3,
            "devicePlatform": "MBB_ODP",
            "systemModelId": "NX53L5",
        },
    ],
    "errors": [],
}

#: ``/v2/garage/vehicles/<vin>``: l'Enyaq 85 del 2025 di ``enyaq_2025_5azjk2_85.yaml``.
INFO_ENYAQ = {
    "vin": VIN_ENYAQ,
    "name": "Fulmine",
    "workshopModeEnabled": False,
    "licensePlate": "AB 123 CD",
    "state": "ACTIVATED",
    "devicePlatform": "WCAR",
    "softwareVersion": "5.4",
    "specification": {
        "title": "Škoda Enyaq",
        "manufacturingDate": "2025-03-31",
        "model": "Enyaq",
        "modelYear": "2026",
        "body": "SUV",
        "trimLevel": "Business Edition",
        "systemModelId": "5AZJK2",
        "maxChargingPowerInKW": 125,
        "battery": {"capacityInKWh": 77},
        "engine": {"type": "iV", "powerInKW": 210},
        "gearbox": {"type": "E1H"},
    },
    "capabilities": _servizi(
        "ACCESS",
        "AIR_CONDITIONING",
        "BATTERY_CHARGING_CARE",
        "CHARGING",
        "CHARGING_MEB",
        "EXTENDED_CHARGING_SETTINGS",
        "HONK_AND_FLASH",
        "MEASUREMENTS",
        "OUTSIDE_TEMPERATURE",
        "PARKING_POSITION",
        "READINESS",
        "STATE",
        "TRIP_STATISTICS",
        "VEHICLE_HEALTH_INSPECTION",
        "WARNING_LIGHTS",
        "WINDOW_HEATING",
        spenti=("PLUG_AND_CHARGE",),
    ),
}

#: La Superb iV del 2025 di ``superb_iv_2025_nz3dyc_liftback_style.yaml``, guidata da un ospite.
INFO_SUPERB = {
    "vin": VIN_SUPERB,
    "name": "",
    "licensePlate": "",
    "state": "GUEST_USER",
    "devicePlatform": "MBB_ODP",
    "specification": {
        "title": "Škoda Superb",
        "model": "Superb",
        "modelYear": "2025",
        "body": "Liftback",
        "trimLevel": "Style Explore",
        "systemModelId": "NZ3DYC",
        "engine": {"type": "TSI PHEV", "powerInKW": 150, "capacityInLiters": 1.5},
        "gearbox": {"type": "A6F"},
    },
    "capabilities": _servizi(
        "ACCESS",
        "AIR_CONDITIONING",
        "CHARGING",
        "CHARGING_MQB",
        "FUEL_STATUS",
        "HONK_AND_FLASH",
        "PARKING_POSITION",
        "STATE",
        "TRIP_STATISTICS",
        "VEHICLE_HEALTH_INSPECTION",
        "WINDOW_HEATING",
        spenti=("EXTENDED_CHARGING_SETTINGS",),
    ),
}

#: Un'Octavia a benzina: niente «CHARGING», non entra.
INFO_OCTAVIA = {
    "vin": VIN_OCTAVIA,
    "name": "Octavia",
    "state": "ACTIVATED",
    "specification": {"title": "Škoda Octavia Combi", "model": "Octavia", "modelYear": "2024"},
    "capabilities": _servizi("ACCESS", "STATE", "FUEL_STATUS", "PARKING_POSITION"),
}

INFO = {VIN_ENYAQ: INFO_ENYAQ, VIN_SUPERB: INFO_SUPERB, VIN_OCTAVIA: INFO_OCTAVIA}

# --- l'Enyaq, in carica alla colonnina rapida -------------------------------------------------

ENYAQ = {
    "ricarica": {
        "isVehicleInSavedLocation": False,
        "status": {
            "chargingRateInKilometersPerHour": 390.0,
            "chargePowerInKw": 87.0,
            "remainingTimeToFullyChargedInMinutes": 25,
            "state": "CHARGING",
            "chargeType": "DC",
            "battery": {"remainingCruisingRangeInMeters": 251000, "stateOfChargeInPercent": 63},
        },
        "settings": {
            "targetStateOfChargeInPercent": 80,
            "batteryCareModeTargetValueInPercent": 80,
            "preferredChargeMode": "MANUAL",
            "availableChargeModes": ["MANUAL"],
            "chargingCareMode": "ACTIVATED",
            "autoUnlockPlugWhenCharged": "OFF",
            "maxChargeCurrentAc": "MAXIMUM",
            "batterySupport": "ENABLED",
        },
        "carCapturedTimestamp": "2025-04-25T08:46:08Z",
        "errors": [],
    },
    "stato": {
        "overall": {"doorsLocked": "YES", "locked": "YES", "doors": "CLOSED", "windows": "CLOSED", "lights": "OFF"},
        "detail": {"sunroof": "UNSUPPORTED", "trunk": "CLOSED", "bonnet": "CLOSED"},
        "renders": {"lightMode": {"oneX": _RENDER.format("1-1-1-1-0-0-0-0-2")}},
        "carCapturedTimestamp": "2025-04-25T08:46:14.592Z",
    },
    "autonomia": {
        "carType": "electric",
        "totalRangeInKm": 252,
        "primaryEngineRange": {"engineType": "electric", "currentSoCInPercent": 63, "remainingRangeInKm": 252},
        "carCapturedTimestamp": "2025-04-25T08:46:14.625Z",
    },
    "clima": {
        "state": "OFF",
        "runningRequests": [],
        "targetTemperature": {"temperatureValue": 21.5, "unitInCar": "CELSIUS"},
        "airConditioningAtUnlock": False,
        "windowHeatingEnabled": False,
        "steeringWheelPosition": "LEFT",
        "seatHeatingActivated": {"frontLeft": False, "frontRight": False},
        "chargerConnectionState": "CONNECTED",
        "chargerLockState": "LOCKED",
        "windowHeatingState": {"front": "OFF", "rear": "OFF", "unspecified": "INVALID"},
        "timers": [],
        "carCapturedTimestamp": "2025-04-25T08:46:12Z",
        "outsideTemperature": {
            "temperatureValue": 10.5,
            "temperatureUnit": "CELSIUS",
            "carCapturedTimestamp": "2025-04-25T08:46:14.627Z",
        },
        "errors": [],
    },
    "connessione": {"unreachable": False, "inMotion": False, "ignitionOn": False, "batteryProtectionLimitOn": False},
    "posizione": {
        "positions": [
            {
                "type": "VEHICLE",
                "gpsCoordinates": {"latitude": 45.07049, "longitude": 7.68201},
                "address": {"city": "Torino", "street": "Via Roma", "houseNumber": "1", "countryCode": "ITA"},
            }
        ],
        "errors": [],
    },
    "manutenzione": {
        "maintenanceReport": {
            "capturedAt": "2025-04-25T08:46:14.602Z",
            "inspectionDueInDays": 722,
            "mileageInKm": 5453,
        },
        "predictiveMaintenance": {
            "setting": {"serviceActivated": False, "email": "user@example.com", "phone": "+49 1234 567890"}
        },
    },
}

SALUTE_ENYAQ = {
    "capturedAt": "2025-04-25T08:15:26.939Z",
    "mileageInKm": 5450,
    "warningLights": [
        {"category": "ASSISTANCE", "defects": []},
        {"category": "BRAKE", "defects": []},
        {"category": "ELECTRIC_ENGINE", "defects": []},
        {"category": "TIRE", "defects": [{"text": "Tyre pressure low", "priority": "WARNING"}]},
        {"category": "OTHER", "defects": []},
    ],
}

# --- la Superb iV, ferma col cavo staccato (il fixture così com'è) -------------------------------

SUPERB = {
    "ricarica": {
        "isVehicleInSavedLocation": False,
        "status": {
            "chargePowerInKw": 0.0,
            "state": "CONNECT_CABLE",
            "battery": {"remainingCruisingRangeInMeters": 118000, "stateOfChargeInPercent": 92},
        },
        "settings": {
            "targetStateOfChargeInPercent": 80,
            "preferredChargeMode": "MANUAL",
            "availableChargeModes": ["MANUAL"],
            "autoUnlockPlugWhenCharged": "OFF",
            "maxChargeCurrentAc": "MAXIMUM",
        },
        "carCapturedTimestamp": "2025-08-27T15:08:53Z",
        "errors": [{"type": "CARE_MODE_IS_NOT_AVAILABLE", "description": "Care mode is not available."}],
    },
    "stato": {
        "overall": {"doorsLocked": "YES", "locked": "YES", "doors": "CLOSED", "windows": "CLOSED", "lights": "OFF"},
        "detail": {"sunroof": "UNSUPPORTED", "trunk": "CLOSED", "bonnet": "CLOSED"},
        "carCapturedTimestamp": "2025-08-27T15:12:41Z",
    },
    "autonomia": {
        "carType": "hybrid",
        "totalRangeInKm": 578,
        "primaryEngineRange": {
            "engineType": "gasoline",
            "currentSoCInPercent": 64,
            "currentFuelLevelInPercent": 64,
            "remainingRangeInKm": 460,
        },
        "secondaryEngineRange": {
            "engineType": "electric",
            "currentSoCInPercent": 92,
            "currentFuelLevelInPercent": 100,
            "remainingRangeInKm": 118,
        },
        "carCapturedTimestamp": "2025-08-27T15:12:41Z",
    },
    "clima": {
        "state": "OFF",
        "runningRequests": [],
        "targetTemperature": {"temperatureValue": 22.0, "unitInCar": "CELSIUS"},
        "airConditioningWithoutExternalPower": True,
        "airConditioningAtUnlock": False,
        "windowHeatingEnabled": False,
        "chargerConnectionState": "DISCONNECTED",
        "chargerLockState": "INVALID",
        "windowHeatingState": {"front": "INVALID", "rear": "OFF", "unspecified": "INVALID"},
        "carCapturedTimestamp": "2025-08-27T15:08:54Z",
        "outsideTemperature": {
            "temperatureValue": 19.0,
            "temperatureUnit": "CELSIUS",
            "carCapturedTimestamp": "2025-08-27T15:12:41Z",
        },
        "errors": [],
    },
    # Mentre va il cloud non dice dove.
    "posizione": {
        "positions": [],
        "errors": [{"type": "VEHICLE_IN_MOTION", "description": "Vehicle is in motion"}],
    },
    "manutenzione": {
        "maintenanceReport": {
            "capturedAt": "2025-08-27T15:12:31Z",
            "inspectionDueInDays": 696,
            "inspectionDueInKm": 28000,
            "mileageInKm": 2105,
            "oilServiceDueInDays": 667,
            "oilServiceDueInKm": 27900,
        },
        "preferredServicePartner": {"name": "Officina d'esempio", "contact": {"email": "user@example.com"}},
    },
}

# --- un'Enyaq ferma, collegata e a pieno, con una porta e il bagagliaio aperti ---------------------

ENYAQ_APERTA = {
    "ricarica": {
        "status": {
            "chargePowerInKw": 0.0,
            "remainingTimeToFullyChargedInMinutes": 0,
            "state": "READY_FOR_CHARGING",
            "battery": {"remainingCruisingRangeInMeters": 261000, "stateOfChargeInPercent": 70},
        },
        "settings": {"targetStateOfChargeInPercent": 70, "preferredChargeMode": "TIMER"},
        "carCapturedTimestamp": "2024-12-04T13:32:15Z",
    },
    "stato": {
        "overall": {"doorsLocked": "OPENED", "locked": "NO", "doors": "OPEN", "windows": "CLOSED", "lights": "OFF"},
        "detail": {"sunroof": "CLOSED", "trunk": "OPEN", "bonnet": "OPEN"},
        "renders": {"lightMode": {"oneX": _RENDER.format("1-1-3-2-0-0-1-1-2")}},
        "carCapturedTimestamp": "2024-12-04T13:30:00Z",
    },
    "clima": {
        "estimatedDateTimeToReachTargetTemperature": "2024-12-04T13:44:40.843030158Z",
        "state": "HEATING",
        "targetTemperature": {"temperatureValue": 72.0, "unitInCar": "FAHRENHEIT"},
        "windowHeatingState": {"front": "ON", "rear": "OFF"},
        "outsideTemperature": {"temperatureValue": 35.6, "temperatureUnit": "FAHRENHEIT"},
        "carCapturedTimestamp": "2024-12-04T13:31:00Z",
    },
    "connessione": {"unreachable": True, "inMotion": False, "batteryProtectionLimitOn": False},
}

# --- le letture lente -----------------------------------------------------------------------

#: ``/v1/trip-statistics/<vin>?offsetType=week``, dalla Superb iV.
VIAGGI_SETTIMANA = {
    "overallMileageInKm": 126,
    "overallTravelTimeInMin": 238,
    "overallAverageElectricConsumption": 20.07,
    "overallAverageFuelConsumption": 0.0,
    "vehicleType": "HYBRID",
    "detailedStatistics": [
        {"date": "2025-08-25", "mileageInKm": 52, "averageElectricConsumption": 20.1},
        {"date": "2025-08-26", "mileageInKm": 50, "averageElectricConsumption": 20.19},
        {"date": "2025-08-27", "mileageInKm": 24, "averageElectricConsumption": 19.75},
        {"date": "2025-08-28"},
    ],
}

#: ``/v1/trip-statistics/<vin>/single-trips``, da ``single-trip-statistics-timezones.json``.
VIAGGI_SINGOLI = {
    "vehicleType": "ELECTRIC",
    "dailyTrips": [
        {
            "date": "2026-06-09",
            "overallMileage": 85,
            "trips": [
                {
                    "id": "trip-1",
                    "startTime": "15:09",
                    "endTime": "20:02",
                    "startMileageInKm": 15568,
                    "endMileageInKm": 15613,
                    "mileageInKm": 45,
                    "averageElectricConsumption": 17.1,
                    "electricConsumption": 7.7,
                },
                {
                    "id": "trip-2",
                    "startTime": "06:15",
                    "endTime": "06:57",
                    "mileageInKm": 40,
                    "averageElectricConsumption": 21.2,
                    "electricConsumption": 8.47,
                },
            ],
        },
        {"date": "2026-06-08", "overallMileage": 12, "trips": [{"id": "trip-3", "mileageInKm": 12}]},
    ],
}

_CSV = (
    '"Session ID","Started on","Ended on"\r\n'
    '"a354fad4-d52d-44a6-badd-76ac76c9926a","2026-06-10T05:14:55Z","2026-06-10T13:31:51Z"\r\n'
    '"e3d6b8a4-a2f7-409d-a972-f1a9457a0b0f","2026-05-29T04:57:35Z","2026-05-29T10:42:05Z"\r\n'
)

#: ``charging_statistics`` del cloud di ricarica CARIAD, da ``charging-statistics.json``.
STATISTICHE_RICARICA = {
    "applicableFilterOptions": [{"filterType": "VEHICLE", "id": VIN_ENYAQ, "label": VIN_ENYAQ}],
    "monthSections": [
        {
            "title": "June 2026",
            "entries": [
                {
                    "id": "attiva",
                    "title": "Thu 11/06/2026, 18:00",
                    "primaryValue": "~ 3 kWh",
                    "details": {
                        "sessionId": "0b0b0b0b-0000-0000-0000-000000000000",
                        "chargingPowerType": "DC",
                        "formattedTotalEnergy": "~ 3 kWh",
                        "isActiveSession": True,
                    },
                },
                {
                    "id": "ruqGssoVwARYaaNHPawhrhAPMOKzzbO-YDHBL6ObgKo",
                    "title": "Wed 10/06/2026, 05:14",
                    "primaryValue": "~ 17 kWh",
                    "secondaryValue": "8h 16min",
                    "details": {
                        "sessionId": "a354fad4-d52d-44a6-badd-76ac76c9926a",
                        "chargingPowerType": "AC",
                        "formattedTotalEnergy": "~ 17 kWh",
                        "formattedChargingStartTime": "10/06/2026, 05:14",
                        "formattedChargingEndTime": "10/06/2026, 13:31",
                        "formattedStartSoc": "56%",
                        "formattedEndSoc": "80%",
                        "isActiveSession": False,
                    },
                },
                {
                    "id": "owGdHzSxhxwfPerQIAaDCawlrZwYmktzZ3ndGa3j8h8",
                    "title": "Fri 29/05/2026, 04:57",
                    "primaryValue": "~ 22 kWh",
                    "details": {
                        "sessionId": "e3d6b8a4-a2f7-409d-a972-f1a9457a0b0f",
                        "chargingPowerType": "AC",
                        "formattedTotalEnergy": "~ 22 kWh",
                        "isActiveSession": False,
                    },
                },
            ],
        }
    ],
    "missingElliConsent": False,
    "csvFile": base64.b64encode(_CSV.encode()).decode(),
}

#: Il vecchio ``/v1/charging/<vin>/history``, da ``charging-history.json``.
STORICO_RICARICA = {
    "nextCursor": "2025-06-28T10:09:59Z",
    "periods": [
        {
            "totalChargedInKWh": 132.0,
            "sessions": [
                {
                    "startAt": "2025-09-05T07:06:30Z",
                    "chargedInKWh": 24.0,
                    "durationInMinutes": 141,
                    "currentType": "AC",
                },
                {"startAt": "2025-09-09T16:49:38Z", "chargedInKWh": 27.0, "durationInMinutes": 22, "currentType": "DC"},
                {"startAt": "2025-09-09T07:28:23Z", "chargedInKWh": 28.0, "durationInMinutes": 14, "currentType": "DC"},
            ],
        }
    ],
}

SPIN_GIUSTO = {"verificationStatus": "CORRECT_SPIN"}
SPIN_SBAGLIATO = {
    "verificationStatus": "INCORRECT_SPIN",
    "spinStatus": {"remainingTries": 2, "lockedWaitingTimeInSeconds": 0, "state": "DEFINED"},
}
UTENTE = {"id": "b8bc126c-ee36-402b-8723-2c1c3dff8dec", "email": "Mario@Esempio.it", "firstName": "Mario"}
