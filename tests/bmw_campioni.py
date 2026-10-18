"""Risposte d'esempio di BMW CarData, nella forma della documentazione di BMW.

I descrittori, le unità e le parole sono quelli visti sulle auto vere nei
campioni di JustChr/BavarianData (``tests/cars/*.json``, MIT) e nel catalogo
telematico di BMW; i valori sono inventati.
"""

from __future__ import annotations

VIN = "WBA71AW010FX00001"
VIN_MINI = "WMW11DJ010TX00002"
CLIENT_ID = "cj5b3499-4918-40x6-a232-f4112f837d72"
GCID = "aa111192-d570-4d3d-8835-656ee00edcd7"

T = "2026-10-18T09:40:12.000Z"
T_ULTIMO = "2026-10-18T09:55:03.000Z"


def _v(valore: object, unita: str | None = None, quando: str = T) -> dict[str, object]:
    return {"value": valore, "unit": unita, "timestamp": quando}


# Una i4 eDrive40 in carica in AC, a Monaco, chiusa con l'allarme, un finestrino socchiuso.
I4_IN_CARICA = {
    "telematicData": {
        "vehicle.drivetrain.batteryManagement.header": _v("57", "percent", T_ULTIMO),
        "vehicle.drivetrain.electricEngine.charging.level": _v("56", "%"),
        "vehicle.drivetrain.electricEngine.remainingElectricRange": _v("287", "km"),
        "vehicle.vehicle.travelledDistance": _v("23456", "km"),
        "vehicle.drivetrain.electricEngine.charging.status": _v("CHARGINGACTIVE"),
        "vehicle.drivetrain.electricEngine.charging.hvStatus": _v("CHARGING"),
        "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged": _v("true"),
        "vehicle.body.chargingPort.status": _v("CONNECTED"),
        "vehicle.powertrain.electric.battery.charging.power": _v("10850", "W"),
        "vehicle.drivetrain.electricEngine.charging.acVoltage": _v("230", "V"),
        "vehicle.drivetrain.electricEngine.charging.acAmpere": _v("16", "A"),
        "vehicle.drivetrain.electricEngine.charging.phaseNumber": _v("3"),
        "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged": _v("135", "min"),
        "vehicle.drivetrain.electricEngine.charging.timeRemaining": _v("140", "min"),
        "vehicle.powertrain.electric.battery.stateOfCharge.target": _v("80", "%"),
        "vehicle.cabin.door.lock.status": _v("SECURED"),
        "vehicle.cabin.door.status": _v("SECURED"),
        "vehicle.cabin.door.row1.driver.isOpen": _v("CLOSED"),
        "vehicle.cabin.door.row1.passenger.isOpen": _v("CLOSED"),
        "vehicle.cabin.door.row2.driver.isOpen": _v("CLOSED"),
        "vehicle.cabin.door.row2.passenger.isOpen": _v("CLOSED"),
        "vehicle.body.trunk.isOpen": _v("CLOSED"),
        "vehicle.body.trunk.door.isOpen": _v(None),
        "vehicle.cabin.window.row1.driver.status": _v("CLOSED"),
        "vehicle.cabin.window.row1.passenger.status": _v("INTERMEDIATE"),
        "vehicle.cabin.window.row2.driver.status": _v("CLOSED"),
        "vehicle.cabin.window.row2.passenger.status": _v("CLOSED"),
        "vehicle.cabin.infotainment.navigation.currentLocation.latitude": _v("48.177567", "degrees"),
        "vehicle.cabin.infotainment.navigation.currentLocation.longitude": _v("11.556123", "degrees"),
        "vehicle.chassis.axle.row1.wheel.left.tire.pressure": _v("250", "kPa"),
        "vehicle.chassis.axle.row1.wheel.right.tire.pressure": _v("252", "kPa"),
        "vehicle.chassis.axle.row2.wheel.left.tire.pressure": _v("270", "kpa"),
        "vehicle.chassis.axle.row2.wheel.right.tire.pressure": _v("268", "kPa"),
    }
}

# Una MINI Countryman E ferma, in miglia e bar, aperta, il cavo staccato dopo una carica finita.
COUNTRYMAN_FERMA = {
    "telematicData": {
        "vehicle.drivetrain.electricEngine.charging.level": _v("91", "%"),
        "vehicle.drivetrain.electricEngine.remainingElectricRange": _v("150", "mi"),
        "vehicle.vehicle.travelledDistance": _v("10000", "mi"),
        "vehicle.drivetrain.electricEngine.charging.status": _v("CHARGINGENDED"),
        "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged": _v("false"),
        "vehicle.powertrain.electric.battery.charging.power": _v("0", "W"),
        "vehicle.powertrain.electric.battery.stateOfCharge.target": _v("90", "%"),
        "vehicle.cabin.door.lock.status": _v("SELECTIVE-LOCKED"),
        "vehicle.cabin.door.row1.driver.isOpen": _v("OPEN"),
        "vehicle.cabin.window.row1.driver.status": _v("CLOSED"),
        "vehicle.cabin.sunroof.overallStatus": _v("OPEN_TILT"),
        "vehicle.drivetrain.engine.isActive": _v("ASN_isFalse"),
        "vehicle.chassis.axle.row1.wheel.left.tire.pressure": _v("2.5", "bar"),
    }
}

# La forma con ``name`` della documentazione, e lo stato porte come oggetto.
FORMA_CON_NOME = [
    {"name": "vehicle.drivetrain.electricEngine.charging.hvStatus", "value": "WAITING_FOR_CHARGING", "timestamp": T},
    {"name": "vehicle.body.chargingPort.status", "value": "CONNECTED", "timestamp": T},
    {"name": "vehicle.cabin.door.status", "value": '{"allDoorsLocked": "ASN_isTrue", "newDoorStatus": "ASN_locked"}'},
]

MAPPINGS = [
    {"vin": VIN, "mappedSince": "2025-03-01T10:00:00.000Z", "mappingType": "PRIMARY"},
    {"vin": VIN_MINI, "mappedSince": "2025-05-01T10:00:00.000Z", "mappingType": "PRIMARY"},
    {"vin": "WBA00000000000003", "mappedSince": "2025-05-01T10:00:00.000Z", "mappingType": "SECONDARY"},
]

BASIC = {
    VIN: {
        "vin": VIN,
        "brand": "BMW",
        "isTelematicsCapable": True,
        "series": "4",
        "modelName": "i4 eDrive40",
        "propulsionType": "BEV",
        "colourCode": "475",
        "constructionDate": "2024-11-12",
        "countryCodeISO": "DE",
    },
    VIN_MINI: {
        "vin": VIN_MINI,
        "brand": "MINI",
        "isTelematicsCapable": True,
        "modelName": "Countryman E",
        "propulsionType": "BEV",
        "constructionDate": "2025-02-01",
    },
}
