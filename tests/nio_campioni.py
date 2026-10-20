"""Le risposte della NIO Open Telematics API per le prove.

Campi, scale e valori seguono i fixture e la mappa dei sensori di
nicolasvangeluwe/nio-home-assistant (licenza MIT, ``tests/test_api.py``,
``tests/test_energy_fields.py``, ``sensor.py``): una ET5 Touring europea
ferma, chiusa, al 52 %, limite al 90 %, un pacco solo da 359 V.
"""

from __future__ import annotations

from typing import Any

VIN = "LJNABC12345678901"
T = 1_760_000_100_000

SOC_CAMBI: list[dict[str, Any]] = [
    {"soc": 51, "remaining_range": 270, "sample_timestamp": T - 60_000},
    # NIO manda solo i campi cambiati: il secondo record non ha l'autonomia.
    {
        "soc": 52,
        "max_soc": 90,
        "chrg_final_soc": 0,
        "chrg_state": "3",
        "hivolt_btry_curnt": -0.5,
        "lock_soc": 100,
        "highest_temp": 0,
        "lowest_temp": 0,
        "btry_paks": [
            {
                "btry_pak_sn": "SERIALE-PACCO",
                "btry_pak_voltage": 359,
                "btry_pak_curnt": 0,
                "sin_btry_qunty_of_pak": 96,
                "temp_prb_qunty": 48,
            }
        ],
        "sample_timestamp": T,
    },
]

STATO: dict[str, Any] = {
    "soc": 0,  # il segnaposto dello stato: la carica vera è in soc_status
    "remaining_range": 0,
    "mileage": 12_345,
    "speed": 0,
    "vehl_totl_volt": 3_965,
    "vehl_totl_curnt": 10_012,
    "gear": 1,
    "vehl_state": "locked",
    "oprtn_mode": 0,
    "sample_timestamp": T + 1_000,
}

POSIZIONE: list[dict[str, Any]] = [
    {"latitude": 52.3702, "longitude": 4.8952, "heading": 90, "latitude_uncertainty": 5, "sample_timestamp": T}
]

PORTE: list[dict[str, Any]] = [
    {"vehicle_lock_status": "0", "sample_timestamp": T - 5_000},
    {
        "vehicle_lock_status": "1",
        "door_ajar_front_left_status": 1,
        "door_ajar_front_right_status": 1,
        "tailgate_ajar_status": 0,
        "sample_timestamp": T,
    },
]

FINESTRINI: list[dict[str, Any]] = [
    {"win_front_left_posn": 0, "win_front_right_posn": 0, "win_rear_left_posn": 0, "win_rear_right_posn": 0}
]
CLIMA: list[dict[str, Any]] = [{"outside_temp_c": 11.5, "amb_temp_c": 19, "sample_timestamp": T}]
ESTREMI: list[dict[str, Any]] = [{"highest_temp": 59, "lowest_temp": 57, "sample_timestamp": T}]
CONTAKM: list[dict[str, Any]] = [
    {"value": 12_000, "recorded_at": "2026-09-01T10:00:00Z"},
    {"value": 12_300, "recorded_at": "2026-10-01T10:00:00Z"},
]


def busta(dati: Any) -> dict[str, Any]:
    return {"request_id": "r-1", "result_code": "success", "data": dati}


def busta_token(access: str, refresh: str) -> dict[str, Any]:
    return busta({"access_token": access, "refresh_token": refresh, "expires_in": 3600, "token_type": "Bearer"})
