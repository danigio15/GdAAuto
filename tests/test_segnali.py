"""Le due lingue del cloud diventano uno stato solo."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.gdaauto.marche.leapmotor.segnali import interpreta
from custom_components.gdaauto.vettura import Marcia, MemoriaEmisfero, StatoRicarica

# Dalla documentazione della comunità: una T03 parcheggiata.
T03 = {
    "soc": 85,
    "chargeState": 0,
    "chargeRemainTime": 0,
    "batteryCurrent": 0.0,
    "batteryVoltage": 400.0,
    "dcInputFastCharge": 0,
    "dumpEnergy": 45000,
    "expectedMileage": 300,
    "speed": 0,
    "totalMileage": 15000,
    "gearStatus": 0,
    "latitude": 45.123,
    "longitude": 7.456,
    "acSwitch": False,
    "acSetting": 22.0,
    "driverDoorLockStatus": True,
    "lbcmDriverDoorStatus": False,
    "bbcmBackDoorStatus": False,
    "leftFrontTirePressure": 250,
    "rightFrontTirePressure": 255,
    "leftRearTirePressure": 260,
    "rightRearTirePressure": 245,
    "collectTime": "2026-05-07 10:30:00",
    "chargesocSetting": 80,
}

# Una C10 vera (anonima), coi segnali a numeri.
C10 = {
    "privacyGPS": 1,
    "signal": {
        "47": 1,
        "1010": 0,
        "1149": 1,
        "1177": 424.9,
        "1178": -26.0,
        "1182": 18,
        "1197": 0,
        "1200": 95,
        "1204": 65,
        "1258": 0,
        "1277": 0,
        "1278": 1,
        "1281": 0,
        "1298": 1,
        "1318": 3030,
        "1319": 0.0,
        "1349": 21.5,
        "1693": 0,
        "1938": 0,
        "2183": 23.0,
        "2188": 278,
        "2190": 40.858536,
        "2191": 14.283342,
        "2646": 255,
        "2653": 250,
        "2660": 253,
        "2667": 253,
        "3260": 278,
        "3724": 14.28319,
        "3725": 40.85812,
        "3727": 0,
        "sts": 1778137347360,
    },
    "config": {"3": {"percent": 90, "isEnable": 0, "beginTime": "22:00", "endTime": "08:00"}},
}


def test_t03_coi_campi_col_nome() -> None:
    s = interpreta(T03)
    assert s.batteria == 85
    assert s.autonomia_km == 300
    assert s.odometro_km == 15000
    assert s.marcia == Marcia.PARCHEGGIO
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.in_carica is False
    assert s.cavo_collegato is False
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.clima_acceso is False
    assert s.energia_residua_kwh == 45.0
    assert s.limite_ricarica == 80
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 250,
        "anteriore_destro": 255,
        "posteriore_sinistro": 260,
        "posteriore_destro": 245,
    }
    assert (s.latitudine, s.longitudine) == (45.123, 7.456)
    assert s.letto == datetime(2026, 5, 7, 10, 30, tzinfo=UTC)


def test_c10_coi_segnali_a_numeri() -> None:
    s = interpreta(C10)
    assert s.batteria == 65
    assert s.autonomia_km == 278
    assert s.odometro_km == 3030
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True  # il segnale 47, la presa lenta
    # 424,9 V × -26 A: carica a 11 kW
    assert s.potenza_ricarica_kw == 11.047
    assert s.potenza_kw == 0.0
    assert s.tempo_ricarica_min == 95
    assert s.temperatura_batteria_c == 18
    assert s.temperatura_interna_c == 21.5
    assert s.portiere_aperte is True  # la porta del passeggero, 1278
    assert s.limite_ricarica == 90
    assert s.latitudine == 40.85812  # 3725 prima di 2190
    assert s.posizione_con_segno is False
    assert s.letto == datetime.fromtimestamp(1778137347.36, UTC)


def test_i_segnali_col_segno_vincono() -> None:
    dati = {"signal": {"2": -8.61, "3": 41.15, "3724": 8.61, "3725": 41.15}}
    s = interpreta(dati)
    assert (s.latitudine, s.longitudine) == (41.15, -8.61)
    assert s.posizione_con_segno is True


def test_la_memoria_dell_emisfero_rimette_il_segno() -> None:
    memoria = MemoriaEmisfero()
    con_segno = interpreta({"signal": {"2": -8.61, "3": 41.15}})
    memoria.correggi("VIN", con_segno)
    senza = interpreta({"signal": {"3724": 8.62, "3725": 41.16}})
    memoria.correggi("VIN", senza)
    assert (senza.latitudine, senza.longitudine) == (41.16, -8.62)
    # Un'altra auto non eredita il segno.
    altra = interpreta({"signal": {"3724": 8.62, "3725": 41.16}})
    memoria.correggi("ALTRO", altra)
    assert altra.longitudine == 8.62


def test_zero_zero_non_e_una_posizione() -> None:
    s = interpreta({"latitude": 0, "longitude": 0, "soc": 50})
    assert s.latitudine is None
    assert s.longitudine is None


def test_valori_strani_non_rompono() -> None:
    s = interpreta({"soc": "n/a", "gearStatus": 16777215, "chargeState": 99, "collectTime": "ieri"})
    assert s.batteria is None
    assert s.marcia is None
    assert s.stato_ricarica is None
    assert s.letto is None


def test_in_marcia_la_ricarica_e_rigenerazione() -> None:
    s = interpreta({"signal": {"1149": 1, "1010": 1, "1319": 50, "1177": 400, "1178": -10}})
    assert s.in_marcia
    assert s.in_carica is False
    assert s.potenza_ricarica_kw == 0.0
