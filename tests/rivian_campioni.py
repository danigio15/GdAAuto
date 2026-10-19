"""Le risposte del cloud Rivian per le prove.

Vengono dai test di bretterer/rivian-python-client (``tests/responses.py``,
licenza MIT), con qualche segnale in più (limite, capacità, cavo) nella forma
che lo stesso progetto chiede a ``vehicleState``. Anche la coppia di chiavi
di prova e il vettore dell'HMAC vengono da lì (``tests/test_utils.py``).
"""

from __future__ import annotations

from typing import Any

VIN = "7FCTGAAL0NN000001"
ID_AUTO = "01-vehicle-id"

CHIAVE_PRIVATA = (
    "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1JR0hBZ0VBTUJNR0J5cUdTTTQ5QWdFR0NDcUdTTTQ5QXdFSEJHMHdhd0lCQVFRZ0tzMzNE"
    "ek8rbjBZbVI1RFUKNUFIb2N6cUw1RlBXdUZSZ2E4ano1QVZmbWl5aFJBTkNBQVRIL2lQSmxtbTh5RjdsUFJOYlcvZFFDTDJseVpjWQo4U0dK"
    "cGpNQ1k4WkhCa0xXV3hoSTZ6RVFTdW5QaUM0Vy9zYUpPVW5EVm15N1Vkbm1EOCtzOCtFNAotLS0tLUVORCBQUklWQVRFIEtFWS0tLS0tCg=="
)
CHIAVE_AUTO = (
    "04334cc40a88768920b54bdfdcd38238df2ec65ce3605a1d343ef2ab8c1a1daa0545cbb804ebf3ab89826924ea3011352b1d23957a52de0"
    "acd5a326078d222d31c"
)
#: HMAC di «WAKE_VEHICLE» + «1707000000» con le due chiavi qui sopra
HMAC_SVEGLIA = "2a68bdda69ff8643e37bac595905f6a481435e00bb63bdd415ecbb425a5bb598"


def _v(valore: Any, quando: str = "2022-10-26T19:45:39.179Z", tipo: str = "TimeStampedString") -> dict[str, Any]:
    return {"__typename": tipo, "timeStamp": quando, "value": valore}


CSRF = {
    "data": {
        "createCsrfToken": {
            "__typename": "CreateCsrfTokenResponse",
            "csrfToken": "valid_csrf_token",
            "appSessionToken": "valid_app_session_token",
        }
    }
}
LOGIN = {
    "data": {
        "login": {
            "__typename": "MobileLoginResponse",
            "accessToken": "valid_access_token",
            "refreshToken": "valid_refresh_token",
            "userSessionToken": "valid_user_session_token",
        }
    }
}
LOGIN_OTP = {"data": {"login": {"__typename": "MobileMFALoginResponse", "otpToken": "token"}}}
LOGIN_CON_CODICE = {
    "data": {
        "loginWithOTP": {
            "__typename": "MobileLoginResponse",
            "accessToken": "token",
            "refreshToken": "token",
            "userSessionToken": "valid_user_session_token",
        }
    }
}


def errore(codice: str, motivo: str | None = None) -> dict[str, Any]:
    return {"errors": [{"extensions": {"code": codice, "reason": motivo or codice}, "message": codice}], "data": None}


def utente(chiave_pubblica: str = "publicKey") -> dict[str, Any]:
    return {
        "data": {
            "currentUser": {
                "__typename": "User",
                "id": "user-id",
                "vehicles": [
                    {
                        "__typename": "UserVehicle",
                        "id": ID_AUTO,
                        "vin": VIN,
                        "name": "R1T",
                        "roles": ["primary-owner"],
                        "vas": {
                            "__typename": "UserVehicleAccess",
                            "vasVehicleId": "vasVehicleId",
                            "vehiclePublicKey": CHIAVE_AUTO,
                        },
                        "vehicle": {
                            "__typename": "Vehicle",
                            "id": ID_AUTO,
                            "vin": VIN,
                            "modelYear": 2022,
                            "make": "Rivian",
                            "model": "R1T",
                            "vehicleState": {
                                "supportedFeatures": [
                                    {"__typename": "SupportedFeature", "name": "CHARG_CMD", "status": "AVAILABLE"},
                                    {"__typename": "SupportedFeature", "name": "CHARG_SLIDER", "status": "AVAILABLE"},
                                    {"__typename": "SupportedFeature", "name": "PET_MODE", "status": "AVAILABLE"},
                                    {"__typename": "SupportedFeature", "name": "TRIP_PLANNER", "status": "LOCKED"},
                                ]
                            },
                        },
                    }
                ],
                "registrationChannels": [{"type": "SMS"}],
                "enrolledPhones": [
                    {
                        "__typename": "UserEnrolledPhone",
                        "vas": {
                            "__typename": "UserEnrolledPhoneAccess",
                            "vasPhoneId": "vasPhoneId",
                            "publicKey": chiave_pubblica,
                        },
                        "enrolled": [
                            {
                                "__typename": "UserEnrolledPhoneEntry",
                                "deviceType": "phone/rivian",
                                "deviceName": "Home",
                                "vehicleId": ID_AUTO,
                                "identityId": "identityId",
                            }
                        ],
                    }
                ],
            }
        }
    }


#: ``vehicleState`` di una R1T in carica, chiusa, in parcheggio (dai test di rivian-python-client)
STATO = {
    "__typename": "VehicleState",
    "gnssLocation": {
        "__typename": "VehicleLocation",
        "latitude": 42.3601866,
        "longitude": -71.0589682,
        "timeStamp": "2022-10-26T20:07:01.081Z",
    },
    "alarmSoundStatus": _v("false", "2022-10-07T04:42:39.880Z"),
    "timeToEndOfCharge": _v(95, "2022-10-26T20:04:38.716Z", "TimeStampedFloat"),
    "doorFrontLeftLocked": _v("locked"),
    "doorFrontLeftClosed": _v("closed"),
    "doorFrontRightLocked": _v("locked"),
    "doorFrontRightClosed": _v("closed"),
    "doorRearLeftLocked": _v("locked"),
    "doorRearLeftClosed": _v("closed"),
    "doorRearRightLocked": _v("locked"),
    "doorRearRightClosed": _v("closed"),
    "windowFrontLeftClosed": _v("closed"),
    "windowFrontRightClosed": _v("closed"),
    "windowRearLeftClosed": _v("closed"),
    "windowRearRightClosed": _v("closed"),
    "closureFrunkLocked": _v("locked"),
    "closureFrunkClosed": _v("closed"),
    "gearGuardLocked": _v("unlocked"),
    "closureLiftgateLocked": _v("locked"),
    "closureLiftgateClosed": _v("signal_not_available"),
    "closureSideBinLeftLocked": _v("locked"),
    "closureSideBinLeftClosed": _v("closed"),
    "closureSideBinRightLocked": _v("locked"),
    "closureSideBinRightClosed": _v("closed"),
    "closureTailgateLocked": _v("locked"),
    "closureTailgateClosed": _v("closed"),
    "closureTonneauLocked": _v("locked"),
    "closureTonneauClosed": _v("closed"),
    "wiperFluidState": _v("normal", "2022-10-14T01:01:22.260Z"),
    "powerState": _v("ready", "2022-10-26T19:46:39.763Z"),
    "vehicleMileage": _v(8928840, "2022-10-26T19:43:13.847Z", "TimeStampedInt"),
    "brakeFluidLow": None,
    "gearStatus": _v("park", "2022-10-26T19:43:18.344Z"),
    "tirePressureStatusFrontLeft": _v("OK"),
    "tirePressureStatusValidFrontLeft": _v("valid"),
    "tirePressureStatusFrontRight": _v("OK"),
    "tirePressureStatusValidFrontRight": _v("valid"),
    "tirePressureStatusRearLeft": _v("OK"),
    "tirePressureStatusValidRearLeft": _v("valid"),
    "tirePressureStatusRearRight": _v("OK"),
    "tirePressureStatusValidRearRight": _v("valid"),
    "batteryLevel": _v(53.400002, "2022-10-26T19:46:30.360Z", "TimeStampedFloat"),
    "batteryLimit": _v(85, "2022-10-26T19:46:30.360Z", "TimeStampedFloat"),
    "batteryCapacity": _v(131.7, "2022-10-26T19:46:30.360Z", "TimeStampedFloat"),
    "chargerState": _v("charging_active", "2022-10-26T18:00:45.533Z"),
    "chargerStatus": _v("chrgr_sts_connected_charging", "2022-10-26T18:00:45.533Z"),
    "distanceToEmpty": _v(266, "2022-10-26T19:38:56.266Z", "TimeStampedInt"),
    "cabinClimateInteriorTemperature": _v(21, "2022-10-26T20:07:04.559Z", "TimeStampedFloat"),
    "cabinClimateDriverTemperature": _v(20, "2022-10-26T20:07:04.559Z", "TimeStampedFloat"),
    "cabinPreconditioningStatus": _v("undefined", "2022-10-26T19:45:39.808Z"),
    "cabinPreconditioningType": _v("NONE", "2022-10-26T19:45:39.808Z"),
    "defrostDefogStatus": _v("Off"),
    "gnssSpeed": _v(0, "2022-10-26T20:07:01.081Z", "TimeStampedFloat"),
    "petModeStatus": _v("Off", "2022-10-26T19:43:18.485Z"),
}

#: la stessa auto addormentata, aperta, con una porta e il bagagliaio aperti
STATO_APERTA = {
    **STATO,
    "powerState": _v("sleep"),
    "doorFrontLeftLocked": _v("unlocked"),
    "doorFrontLeftClosed": _v("open"),
    "closureTailgateClosed": _v("open"),
    "windowRearLeftClosed": _v("open"),
    "tirePressureStatusRearRight": _v("LOW"),
    "chargerState": _v("charging_ready"),
    "chargerStatus": _v("chrgr_sts_not_connected"),
    "cabinPreconditioningStatus": _v("active"),
    "cabinClimateDriverTemperature": _v(63.5, tipo="TimeStampedFloat"),
    "gearStatus": _v("drive"),
    "gnssSpeed": _v(25.0, tipo="TimeStampedFloat"),
}

RICARICHE = {
    "data": {
        "getCompletedSessionSummaries": [
            {
                "chargerType": "DC",
                "startInstant": "2024-03-01T10:00:00.000Z",
                "endInstant": "2024-03-01T10:40:00.000Z",
                "totalEnergyKwh": 61.2,
                "rangeAddedKm": 300,
                "vehicleId": ID_AUTO,
                "transactionId": "t1",
            },
            {
                "chargerType": "AC",
                "startInstant": "2024-03-03T22:00:00.000Z",
                "endInstant": "2024-03-04T05:00:00.000Z",
                "totalEnergyKwh": 40.123,
                "rangeAddedKm": 200,
                "vehicleId": ID_AUTO,
                "transactionId": "t2",
            },
            {
                "chargerType": "DC",
                "startInstant": "2024-03-05T10:00:00.000Z",
                "endInstant": "2024-03-05T10:30:00.000Z",
                "totalEnergyKwh": 30,
                "vehicleId": "un'altra auto",
                "transactionId": "t3",
            },
        ]
    }
}
