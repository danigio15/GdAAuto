"""Risposte d'esempio di Gigya e Kamereon, dai fixture di hacf-fr/renault-api (MIT).

Sono le risposte vere, accorciate dove i campi non servono (le immagini, i
servizi dell'auto).
"""

from __future__ import annotations

from typing import Any

VIN_SPRING = "VF1AAAAA555777123"
VIN_R5 = "VF1AAAA0000000000"
VIN_ZOE = "VF1AAAAA555777999"

GIGYA_LOGIN = {
    "callId": "callId",
    "errorCode": 0,
    "apiVersion": 2,
    "statusCode": 200,
    "statusReason": "OK",
    "sessionInfo": {"cookieName": "cookieName", "cookieValue": "sample-cookie-value"},
}
GIGYA_LOGIN_SBAGLIATO = {
    "callId": "callId",
    "errorCode": 403042,
    "errorDetails": "invalid loginID or password",
    "errorMessage": "Invalid LoginID",
    "apiVersion": 2,
    "statusCode": 403,
    "statusReason": "Forbidden",
}
GIGYA_VERIFICA = {
    "callId": "callId",
    "errorCode": 403101,
    "errorDetails": "Pending Two-Factor Authentication",
    "errorMessage": "Pending Two-Factor Authentication",
    "apiVersion": 2,
    "statusCode": 403,
    "statusReason": "Forbidden",
    "regToken": "sample-reg-token",
}
GIGYA_ACCOUNT = {"callId": "callId", "errorCode": 0, "data": {"personId": "person-id-1", "gigyaDataCenter": "eu1"}}
GIGYA_JWT_SCADUTO = {
    "callId": "782cef59173646f1840134cdaec798f0",
    "errorCode": 403005,
    "errorDetails": "Unauthorized user",
    "errorMessage": "Unauthorized user",
    "apiVersion": 2,
    "statusCode": 403,
    "statusReason": "Forbidden",
}
GIGYA_CODICE_SBAGLIATO = {
    "callId": "callId",
    "errorCode": 400006,
    "errorDetails": "Wrong verification code",
    "errorMessage": "Invalid parameter value",
    "statusCode": 400,
}

PERSONA = {
    "personId": "person-id-1",
    "type": "I",
    "country": "FR",
    "accounts": [
        {
            "accountId": "account-id-1",
            "accountType": "MYRENAULT",
            "accountStatus": "ACTIVE",
            "country": "FR",
            "personId": "person-id-1",
            "relationType": "OWNER",
        },
        {
            "accountId": "account-id-2",
            "externalId": "externalId",
            "accountType": "SFDC",
            "accountStatus": "ACTIVE",
            "country": "FR",
            "personId": "person-id-1",
            "relationType": "OWNER",
        },
    ],
}


def _auto(vin: str, marca: str, codice: str, nome: str, energia: str = "ELEC", data: str = "2021-11-29") -> dict:
    return {
        "brand": marca,
        "vin": vin,
        "status": "ACTIVE",
        "linkType": "OWNER",
        "vehicleDetails": {
            "vin": vin,
            "engineEnergyType": energia,
            "brand": {"label": marca},
            "model": {"code": codice, "group": "971", "label": nome},
            "energy": {"code": energia, "group": "019", "label": "ELECTRIQUE"},
            "registrationNumber": "REG-NUMBER",
            "firstRegistrationDate": data,
        },
    }


AUTO = {
    "accountId": "account-id-1",
    "country": "IT",
    "vehicleLinks": [
        _auto(VIN_SPRING, "DACIA", "XBG1VE", "SPRING"),
        _auto(VIN_R5, "RENAULT", "R5E1VE", "RENAULT 5", data="2025-02-17"),
        _auto(VIN_ZOE, "RENAULT", "X102VE", "ZOE", data="2020-01-13"),
        # Senza dettagli (un'auto appena comprata): si salta.
        {"vin": "VF1SENZADETTAGLI", "brand": "RENAULT", "status": "ACTIVE"},
    ],
}


def dati(attributi: dict[str, Any], vin: str = VIN_ZOE) -> dict[str, Any]:
    return {"data": {"type": "Car", "id": vin, "attributes": attributi}}


# ZOE 50 in carica (battery-status.2.json)
BATTERIA_IN_CARICA = {
    "timestamp": "2020-01-12T21:40:16Z",
    "batteryLevel": 60,
    "batteryTemperature": 20,
    "batteryAutonomy": 141,
    "batteryCapacity": 0,
    "batteryAvailableEnergy": 31,
    "plugStatus": 1,
    "chargingStatus": 1.0,
    "chargingRemainingTime": 145,
    "chargingInstantaneousPower": 27.0,
}
# ZOE 40 staccata (battery-status.1.json): -1.0 vuol dire «non in carica»
BATTERIA_STACCATA = {
    "timestamp": "2020-11-17T09:06:48+01:00",
    "batteryLevel": 50,
    "batteryAutonomy": 128,
    "batteryCapacity": 0,
    "batteryAvailableEnergy": 0,
    "plugStatus": 0,
    "chargingStatus": -1.0,
}
# Renault 5 (battery-status.renault_5.json)
BATTERIA_R5 = {
    "timestamp": "2025-03-24T19:03:58Z",
    "batteryLevel": 49,
    "batteryAutonomy": 169,
    "plugStatus": 0,
    "chargingStatus": 0.0,
    "chargingRemainingTime": 0,
    "V2L_SystemStatusDisplay": 0,
}
COCKPIT = {"totalMileage": 49114.27}
COCKPIT_R5 = {"fuelQuantity": 0.0, "totalMileage": 559}
CLIMA = {"externalTemperature": 8.0, "hvacStatus": "off"}
CLIMA_R5 = {
    "internalTemperature": 15.0,
    "hvacStatus": "off",
    "socThreshold": 15.0,
    "lastUpdateTime": "2025-03-24T18:57:22Z",
}
POSIZIONE = {"gpsLatitude": 48.1234567, "gpsLongitude": 11.1234567, "lastUpdateTime": "2020-02-18T16:58:38Z"}
PORTE = {
    "lockStatus": "locked",
    "doorStatusRearLeft": "closed",
    "doorStatusRearRight": "closed",
    "doorStatusDriver": "closed",
    "doorStatusPassenger": "closed",
    "hatchStatus": "open",
    "lastUpdateTime": "2022-02-02T13:51:13Z",
}
GOMME = {
    "flPressure": 2460,
    "frPressure": 2730,
    "rlPressure": 2790,
    "rrPressure": 2790,
    "flStatus": 0,
    "frStatus": 0,
    "rlStatus": 0,
    "rrStatus": 0,
}
# ev-soc-levels.json (KCM: il JSON nudo, senza data/attributes)
LIMITI = {"lastEnergyUpdateTimestamp": "2025-04-18T06:51:09Z", "socMin": 20, "socTarget": 80}
# ev-settings.single.active.json (KCM)
IMPOSTAZIONI_RICARICA = {
    "lastSettingsUpdateTimestamp": "2025-04-24T12:17:12.247Z",
    "delegatedActivated": False,
    "chargeModeRq": "SCHEDULED",
    "chargeTimeStart": "00:00",
    "chargeDuration": 9000,
    "preconditioningTemperature": 20.0,
    "programs": [
        {
            "programActivationStatus": True,
            "programType": "CHARGE_AND_PRECONDITIONING",
            "programDepartureTime": "02:00",
            "programActivationMonday": True,
        }
    ],
}

NON_TROVATO = {
    "type": "FUNCTIONAL",
    "messages": [{"code": "err.func.wired.notFound", "message": "Resource not found"}],
    "errors": [{"errorCode": "err.func.wired.notFound", "errorMessage": "Resource not found"}],
}
VIETATO = {
    "type": "FUNCTIONAL",
    "messages": [{"code": "err.func.wired.forbidden", "message": "The access is forbidden"}],
    "errors": [{"errorCode": "err.func.wired.forbidden", "errorMessage": "The access is forbidden"}],
}
NON_AUTORIZZATO = {
    "type": "FUNCTIONAL",
    "errors": [{"errorCode": "err.func.wired.unauthorized", "errorMessage": "The access is unauthorized"}],
}
QUOTA = {
    "type": "FUNCTIONAL",
    "errors": [{"errorCode": "err.func.wired.overloaded", "errorMessage": "You have reached your quota limit"}],
}
CATTIVO_GATEWAY = "<html><head><title>502 Bad Gateway</title></head><body><h1>502 Bad Gateway</h1></body></html>"
