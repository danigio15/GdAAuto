"""Risposte d'esempio del cloud OLA di SEAT e CUPRA, per le prove di ``marche/seat_cupra``.

Sono scritte da capo sulla forma che il cloud usa, come la descrivono
tillsteinbach/CarConnectivity-connector-seatcupra (MIT: i commenti con le
risposte vere in ``connector.py``) e WulfgarW/pycupra (Apache 2.0: i nomi dei
campi letti in ``vehicle.py`` e ``connection.py``). VIN, nomi e posizioni sono
inventati.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

VIN_BORN = "VSSZZZK1ZPP000001"
VIN_FORMENTOR = "VSSZZZKMZRR000002"
VIN_LEON = "VSSZZZKLZNR000003"

#: ``/v2/users/{id}/garage/vehicles``: una Born, una Formentor e-HYBRID e una Leon a benzina.
GARAGE: dict[str, Any] = {
    "vehicles": [
        {
            "vin": VIN_BORN,
            "enrollmentStatus": "completed",
            "userRole": "primaryUser",
            "vehicleNickname": "Saetta",
            "specifications": {
                "steeringRight": False,
                "factoryModel": {"vehicleBrand": "CUPRA", "vehicleModel": "Born", "modYear": 2023},
            },
        },
        {
            "vin": VIN_FORMENTOR,
            "enrollmentStatus": "completed",
            "userRole": "secondaryUser",
            "vehicleNickname": "",
            "specifications": {
                "steeringRight": False,
                "factoryModel": {"vehicleBrand": "CUPRA", "vehicleModel": "Formentor", "modYear": "2024"},
            },
        },
        {
            "vin": VIN_LEON,
            "enrollmentStatus": "completed",
            "userRole": "primaryUser",
            "vehicleNickname": "Leon",
            "specifications": {"factoryModel": {"vehicleBrand": "CUPRA", "vehicleModel": "Leon", "modYear": 2022}},
        },
    ]
}


def _capacita(*nomi: str, **parametri: dict[str, Any]) -> list[dict[str, Any]]:
    voci: list[dict[str, Any]] = []
    for nome in nomi:
        voce: dict[str, Any] = {"id": nome, "status": [], "expirationDate": "2030-01-01T00:00:00Z", "editable": False}
        if nome in parametri:
            voce["parameters"] = parametri[nome]
        voci.append(voce)
    return voci


#: ``/v1/user/{id}/vehicle/{vin}/capabilities``, per VIN.
CAPACITA: dict[str, dict[str, Any]] = {
    VIN_BORN: {
        "platform": "MOD4",
        "queriedOn": "2026-10-19T08:00:00Z",
        "capabilities": _capacita(
            "charging",
            "climatisation",
            "honkAndFlash",
            "parkingPosition",
            "transactionHistoryLockUnlock",
            "lockUnlockEnabled",
            "tripStatistics",
            "state",
            charging={"supportsTargetStateOfCharge": "true"},
            tripStatistics={"supportsCyclicTrips": "true"},
        ),
    },
    VIN_FORMENTOR: {
        "platform": "MOD3",
        "capabilities": [
            *_capacita("charging", "climatisation", "state", charging={"supportsTargetStateOfCharge": True}),
            # Un servizio scaduto non vale: clacson e luci non si danno.
            {"id": "honkAndFlash", "status": ["LicenseExpired"]},
            {"id": "parkingPosition", "active": False},
        ],
    },
    VIN_LEON: {"platform": "MOD3", "capabilities": _capacita("state", "parkingPosition", "honkAndFlash")},
}

# --- la Born, alla colonnina -------------------------------------------------------------

#: ``/v2/vehicles/{vin}/status``: chiusa, tutto chiuso.
STATO_BORN: dict[str, Any] = {
    "updatedAt": "2026-10-19T17:24:02Z",
    "locked": True,
    "lights": "off",
    "engine": "off",
    "hood": {"open": "false", "locked": "false"},
    "trunk": {"open": "false", "locked": "true"},
    "doors": {
        "frontLeft": {"open": "false", "locked": "true"},
        "frontRight": {"open": "false", "locked": "true"},
        "rearLeft": {"open": "false", "locked": "true"},
        "rearRight": {"open": "false", "locked": "true"},
    },
    "windows": {"frontLeft": "closed", "frontRight": "closed", "rearLeft": "closed", "rearRight": "closed"},
}

#: ``/v5/users/{id}/vehicles/{vin}/mycar``: il motore elettrico e i riassunti.
MYCAR_BORN: dict[str, Any] = {
    "engines": {"primary": {"fuelType": "electric", "levelPct": 63, "rangeKm": 251}},
    "services": {
        "charging": {
            "status": "Charging",
            "targetPct": 80,
            "currentPct": 63,
            "chargeMode": "manual",
            "active": True,
            "remainingTime": 95,
            "progressBarPct": 63,
        },
        "climatisation": {"status": "Off", "targetTemperatureCelsius": 21.0, "remainingTime": 0},
    },
}

#: ``/v1/vehicles/{vin}/charging/status``.
CARICA_BORN: dict[str, Any] = {
    "battery": {"carCapturedTimestamp": "2026-10-19T17:20:00Z", "currentSocPercentage": 63},
    "charging": {
        "carCapturedTimestamp": "2026-10-19T17:20:00Z",
        "state": "charging",
        "chargeMode": "manual",
        "chargeType": "ac",
        "chargedPowerInKw": 10.8,
        "remainingTimeInMinutes": 95,
        "rateInKmph": 55,
    },
    "plug": {"connection": "connected", "lock": "locked", "externalPower": "active"},
}

#: ``/v1/vehicles/{vin}/charging/info``: le impostazioni, col limite.
INFO_CARICA_BORN: dict[str, Any] = {
    "settings": {
        "maxChargeCurrentAc": "maximum",
        "maxChargeCurrentAcInAmperes": 16,
        "autoUnlockPlugWhenCharged": "off",
        "targetSoc": 80,
        "batteryCareModeEnabled": True,
        "batteryCareTargetSocPercentage": 80,
    }
}

#: ``/v1/vehicles/{vin}/climatisation/status`` (dal commento di CarConnectivity, MIT).
CLIMA_BORN: dict[str, Any] = {
    "climatisationStatus": {
        "carCapturedTimestamp": "2026-10-19T17:24:02Z",
        "climatisationState": "off",
        "climatisationTrigger": "unsupported",
    },
    "windowHeatingStatus": {
        "carCapturedTimestamp": "2026-10-19T16:57:51Z",
        "windowHeatingStatus": [
            {"windowLocation": "front", "windowHeatingState": "off"},
            {"windowLocation": "rear", "windowHeatingState": "off"},
        ],
    },
}

#: ``/v2/vehicles/{vin}/climatisation/settings``.
IMPOSTAZIONI_CLIMA_BORN: dict[str, Any] = {
    "targetTemperatureInCelsius": 21.5,
    "targetTemperatureInFahrenheit": 71,
    "climatisationWithoutExternalPower": True,
    "climatisationAtUnlock": False,
}

PARCHEGGIO_BORN: dict[str, Any] = {"lat": 41.50283, "lon": 1.81219, "updatedAt": "2026-10-19T17:24:00Z"}
CHILOMETRI_BORN: dict[str, Any] = {"mileageKm": 18342}
AUTONOMIE_BORN: dict[str, Any] = {"ranges": [{"rangeName": "electricRangeKm", "value": 251.0}]}
DISPONIBILITA_BORN: dict[str, Any] = {"remote-availability": "online"}

# --- la Formentor e-HYBRID, in attesa del timer ------------------------------------------

STATO_FORMENTOR: dict[str, Any] = {
    "updatedAt": "2026-10-19T07:10:00.123Z",
    "locked": False,
    "engine": "off",
    "trunk": {"open": "true", "locked": "false"},
    "doors": {
        "frontLeft": {"open": "true", "locked": "false"},
        "frontRight": {"open": "false", "locked": "false"},
    },
    "windows": {"frontLeft": "open", "frontRight": "closed"},
    "sunRoof": "closed",
}

#: Le ibride plug-in: il serbatoio è il motore principale, la batteria il secondario.
MYCAR_FORMENTOR: dict[str, Any] = {
    "engines": {
        "primary": {"fuelType": "gasoline", "levelPct": 55, "rangeKm": 480},
        "secondary": {"fuelType": "electric", "levelPct": 90, "rangeKm": 45},
    },
    "services": {
        "charging": {"status": "ReadyForCharging", "targetPct": 100, "chargeMode": "timer", "remainingTime": 0},
        "climatisation": {"status": "Heating", "targetTemperatureCelsius": 22.0},
    },
}

CARICA_FORMENTOR: dict[str, Any] = {
    "battery": {"currentSocPercentage": 90},
    "charging": {"state": "readyForCharging", "chargeMode": "timer"},
    "plug": {"connection": "connected", "lock": "unlocked", "externalPower": "ready"},
}

#: Il formato vecchio delle impostazioni (``/vehicles/{vin}/charging/settings``, dal commento di CarConnectivity).
INFO_CARICA_FORMENTOR: dict[str, Any] = {
    "settings": {
        "maxChargeCurrentAC": "reduced",
        "carCapturedTimestamp": "2025-03-18T16:50:33Z",
        "autoUnlockPlugWhenCharged": None,
        "targetSoc_pct": 100,
        "batteryCareTargetSocPercentage": 80,
    }
}

CLIMA_FORMENTOR: dict[str, Any] = {
    "climatisationStatus": {"climatisationState": "heating", "remainingClimatisationTimeInMinutes": 12},
    "windowHeatingStatus": {
        "windowHeatingStatus": [
            {"windowLocation": "front", "windowHeatingState": "on"},
            {"windowLocation": "rear", "windowHeatingState": "off"},
        ]
    },
}

#: ``/v1/vehicles/{vin}/ranges`` (dal commento di CarConnectivity, MIT).
AUTONOMIE_FORMENTOR: dict[str, Any] = {
    "ranges": [{"rangeName": "gasolineRangeKm", "value": 480.0}, {"rangeName": "electricRangeKm", "value": 45.0}]
}
CHILOMETRI_FORMENTOR: dict[str, Any] = {"mileageKm": 7021}
DISPONIBILITA_FORMENTOR: dict[str, Any] = {"remote-availability": "offline"}


def viaggi(oggi: date) -> dict[str, Any]:
    """``/v2/vehicles/{vin}/driving-data/CUSTOM``: i riassunti di tre settimane, uno ogni tre giorni."""
    giorni = []
    for n in range(0, 21, 3):
        giorni.append(
            {
                "day": {"id": (oggi - timedelta(days=n)).isoformat()},
                "values": [
                    {"id": "distanceDriven", "value": 40.0, "total": 40.0},
                    {"id": "electricConsumption", "value": 16.0},
                    {"id": "travelTime", "value": 55},
                    # Un valore senza «value» si legge dal «total», come fa pycupra.
                    {"id": "averageSpeed", "value": None, "total": 44.0},
                ],
            }
        )
    return {"data": {"dailySummary": giorni}}
