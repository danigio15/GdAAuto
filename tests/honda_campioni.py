"""Le risposte di My Honda+ per le prove.

Il cruscotto e l'auto sono quelli dei fixture di enricobattocchi/pymyhondaplus
(``tests/conftest.py`` e ``tests/test_vehicle.py``, licenza MIT): una Honda e
italiana, ferma e attaccata alla colonnina.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any

VIN = "JHMZC7840LX204934"

CRUSCOTTO: dict[str, Any] = {
    "climateControl": {
        "status": {"isActive": False, "canBeEnabled": True, "remainingTimeInMinutes": 0, "initialDurationInMinutes": 30}
    },
    "timestamp": "2026-03-24T22:53:01+00:00",
    "gpsData": {
        "coordinate": {"latitude": "41,53,24.904", "longitude": "12,29,32.543"},
        "dtTime": "2026-03-24T22:53:01+00:00",
        "velocity": {"value": "0.0", "unit": "km/h"},
    },
    "odometer": {"value": "43202", "unit": "km"},
    "temperature": {"cabin": {"value": "24", "unit": "c"}},
    "doorStatus": {
        "firstRowDriver": {"openState": "closed", "lockState": "lock"},
        "firstRowPassenger": {"openState": "closed", "lockState": "lock"},
        "secondRowDriver": {"openState": "closed", "lockState": "lock"},
        "secondRowPassenger": {"openState": "closed", "lockState": "lock"},
        "hood": {"openState": "closed"},
        "trunk": {"openState": "closed"},
    },
    "windowStatus": {
        "frontWindowDR": {"closeState": "closed"},
        "frontWindowAS": {"closeState": "closed"},
        "rearWindowRR": {"closeState": "closed"},
        "rearWindowRL": {"closeState": "closed"},
    },
    "lightStatus": {"headlights": {"lightState": "off"}, "parkingLights": {"lightState": "off"}},
    "warningLamps": {"languageCode": "it", "messages": []},
    "evStatus": {
        "soc": "82",
        "evRange": "176",
        "evClimateOffRange": "5",
        "totalRange": "176",
        "chargeStatus": "stopped",
        "plugStatus": "plugged in",
        "chargeMode": "unconfirmed",
        "timeToTargetSoc": "0",
        "intTemp": "15",
        "igStatus": "OFF",
        "homeAway": "AWAY",
        "chargeLimitHome": "80",
        "chargeLimitAway": "90",
        "acTempVal": "04",
        "acDurationSetting": "30",
        "acDefAutoSetting": "def auto on",
    },
}


def in_carica() -> dict[str, Any]:
    """La stessa auto mentre carica in rapida, aperta, col bagagliaio su."""
    c = copy.deepcopy(CRUSCOTTO)
    c["evStatus"].update({"chargeStatus": "running", "timeToTargetSoc": "35", "chargeMode": "fast_charging"})
    c["doorStatus"]["firstRowDriver"]["lockState"] = "unlock"
    c["doorStatus"]["trunk"]["openState"] = "open"
    c["windowStatus"]["frontWindowDR"]["closeState"] = "open"
    c["climateControl"]["status"]["isActive"] = True
    return c


def britannica() -> dict[str, Any]:
    """Un account del Regno Unito: miglia e Fahrenheit, e a ovest di Greenwich."""
    c = copy.deepcopy(CRUSCOTTO)
    c["evStatus"]["rangeUnit"] = "mile"
    c["odometer"] = {"value": "1000", "unit": "mile"}
    c["temperature"]["cabin"] = {"value": "68", "unit": "f"}
    c["gpsData"]["coordinate"] = {"latitude": "51,30,0", "longitude": "-0,7,30"}
    c["gpsData"]["velocity"] = {"value": "10", "unit": "mile/h"}
    return c


def ibrida() -> dict[str, Any]:
    """Un'ibrida: niente autonomia elettrica totale, il serbatoio in ``fuelLevel``."""
    c = copy.deepcopy(CRUSCOTTO)
    c["evStatus"]["totalRange"] = "unknown"
    c["fuelLevel"] = {
        "currentLevel": {"gaugeBars": 10, "value": "100", "unit": "percentage"},
        "driveRange": {"value": "675", "unit": "km"},
    }
    return c


AUTO: dict[str, Any] = {
    "vin": VIN,
    "vehicleNickName": "Honda e",
    "vehicleRegNumber": "GE395KM",
    "role": "primary",
    "fuelType": "E",
    "grade": "E ADVANCE",
    "modelYear": 2020,
    "vehicleUIConfiguration": {"friendlyModelName": "Honda e"},
    "vehicleCapability": {
        "capabilities": {
            "telematicsRemoteLockUnlock": {"featureStatus": "active"},
            "telematicsRemoteClimate": {"featureStatus": "active"},
            "telematicsRemoteCharge": {"featureStatus": "active"},
            "telematicsRemoteHorn": {"featureStatus": "active"},
            "telematicsMaxChargeSettings": {"featureStatus": "active"},
            "telematicsJourneyHistory": {"featureStatus": "active"},
            "telematicsCareAssistance": {"featureStatus": "notSupported"},
        }
    },
}

ACCOUNT: dict[str, Any] = {"personalId": "pid789", "firstName": "Mario", "vehiclesInfo": [AUTO, {"senza": "vin"}]}

COLONNE = [
    "OneTripDate",
    "OneTripNo",
    "Mileage",
    "AveSpeed",
    "MaxSpeed",
    "DriveTime",
    "AveFuelEconomy",
    "StartTime",
    "EndTime",
]


def viaggi(oggi: date) -> dict[str, Any]:
    """Tre viaggi come quelli di pymyhondaplus: due di ieri, uno di dieci giorni fa."""
    ieri = (oggi - timedelta(days=1)).isoformat()
    prima = (oggi - timedelta(days=10)).isoformat()
    return {
        "maxPage": 1,
        "payload": {
            "def": COLONNE,
            "data": [
                [
                    ieri,
                    "1774102361",
                    "5",
                    "7.7",
                    "57.2",
                    "37",
                    "18.7",
                    f"{ieri}T14:12:41+00:00",
                    f"{ieri}T14:49:55+00:00",
                ],
                [
                    ieri,
                    "1774092477",
                    "6",
                    "27.5",
                    "83.2",
                    "13",
                    "14.4",
                    f"{ieri}T11:27:57+00:00",
                    f"{ieri}T11:41:55+00:00",
                ],
                [
                    prima,
                    "1773937393",
                    "33",
                    "46.3",
                    "103.0",
                    "42",
                    "13.2",
                    f"{prima}T16:23:13+00:00",
                    f"{prima}T17:05:56+00:00",
                ],
            ],
        },
    }
