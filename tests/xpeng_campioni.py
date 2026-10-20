"""Veicoli di Enode per le prove: una Xpeng G6 in carica e una G9 ferma senza cavo.

La forma è quella di ``enode_models.py`` di mnordseth/xpeng-homeassistant
(licenza MIT) e della documentazione pubblica di Enode; i valori sono
inventati ma realistici (una G6 RWD Long Range ha 87,5 kWh).
"""

from __future__ import annotations

from typing import Any

VIN_G6 = "LUZBGAFB7RA012345"
VIN_G9 = "LUZEGAFC5PA067890"


def _capacita(ricarica: bool) -> dict[str, Any]:
    nomi = ("information", "chargeState", "location", "odometer", "setMaxCurrent", "smartCharging")
    capacita: dict[str, Any] = {n: {"isCapable": True, "interventionIds": []} for n in nomi}
    capacita["setMaxCurrent"]["isCapable"] = False
    for n in ("startCharging", "stopCharging"):
        capacita[n] = {"isCapable": ricarica, "interventionIds": [] if ricarica else ["a1b2"]}
    return capacita


G6_IN_CARICA: dict[str, Any] = {
    "id": "veicolo-g6",
    "userId": "gdaauto-utente",
    "vendor": "XPENG",
    "lastSeen": "2026-10-18T09:58:12.000Z",
    "isReachable": True,
    "information": {"displayName": "La G6", "vin": VIN_G6, "brand": "XPENG", "model": "G6", "year": 2024},
    "chargeState": {
        "chargeRate": 10.8,
        "chargeTimeRemaining": 95,
        "isFullyCharged": False,
        "isPluggedIn": True,
        "isCharging": True,
        "batteryLevel": 64,
        "range": 312,
        "batteryCapacity": 87.5,
        "chargeLimit": 90,
        "lastUpdated": "2026-10-18T09:57:40.000Z",
        "powerDeliveryState": "PLUGGED_IN:CHARGING",
        "maxCurrent": 16,
    },
    "smartChargingPolicy": {"deadline": None, "isEnabled": False, "minimumChargeLimit": 0},
    "location": {"id": None, "latitude": 59.9139, "longitude": 10.7522, "lastUpdated": "2026-10-18T08:12:00.000Z"},
    "odometer": {"distance": 18234.5, "lastUpdated": "2026-10-18T08:12:00.000Z"},
    "capabilities": _capacita(True),
    "scopes": ["vehicle:read:data", "vehicle:read:location", "vehicle:control:charging"],
}

G9_FERMA: dict[str, Any] = {
    "id": "veicolo-g9",
    "userId": "gdaauto-utente",
    "vendor": "XPENG",
    "lastSeen": "2026-10-18T07:00:00.000Z",
    "isReachable": False,
    "information": {"displayName": None, "vin": VIN_G9, "brand": "XPENG", "model": "G9", "year": 2023},
    "chargeState": {
        "chargeRate": None,
        "chargeTimeRemaining": None,
        "isFullyCharged": False,
        "isPluggedIn": False,
        "isCharging": False,
        "batteryLevel": 81,
        "range": 402,
        "batteryCapacity": None,
        "chargeLimit": 100,
        "lastUpdated": "2026-10-18T06:59:00.000Z",
        "powerDeliveryState": "UNPLUGGED",
        "maxCurrent": None,
    },
    "smartChargingPolicy": None,
    "location": {"id": None, "latitude": None, "longitude": None, "lastUpdated": None},
    # Xpeng spesso non manda i chilometri.
    "odometer": {"distance": None, "lastUpdated": None},
    "capabilities": _capacita(False),
    "scopes": ["vehicle:read:data", "vehicle:read:location"],
}
