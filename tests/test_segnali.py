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
        "3257": 405,
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


# --- quello che le auto europee fanno davvero (leapmotor-ha, leapmotor-mate) -------------


def _c10(**segnali: object) -> dict:
    return {"signal": {**C10["signal"], **segnali}, "config": C10["config"]}


def test_le_gomme_sono_sulle_ruote_giuste() -> None:
    s = interpreta(_c10(**{"2646": 241, "2653": 242, "2660": 243, "2667": 244, "2655": 1}))
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 241,
        "anteriore_destro": 242,
        "posteriore_sinistro": 243,
        "posteriore_destro": 244,
    }
    assert s.pneumatici_allarme is True  # 2655: la posteriore sinistra
    assert interpreta(_c10(**{"2641": 0, "2648": 0})).pneumatici_allarme is False
    assert interpreta(C10).pneumatici_allarme is None  # chi non lo dice


def test_la_c10_che_carica_dicendo_2() -> None:
    # 1149 = 2 mentre entrano 26 A: carica, non «finita».
    s = interpreta(_c10(**{"1149": 2}))
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.potenza_ricarica_kw == 11.047
    # 2 senza corrente e senza tempo: finita davvero, col cavo ancora dentro.
    s = interpreta(_c10(**{"1149": 2, "1178": 0.1, "1200": 0}))
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_kw == 0.0


def test_i_codici_della_ricarica() -> None:
    fermo = {"1178": 0.1, "1200": 0}
    assert interpreta(_c10(**{"1149": 4, **fermo})).stato_ricarica == StatoRicarica.PROGRAMMATA
    assert interpreta(_c10(**{"1149": 3, **fermo})).stato_ricarica == StatoRicarica.IN_PAUSA  # ibrida, a metà
    assert interpreta(_c10(**{"1149": 3})).stato_ricarica == StatoRicarica.IN_CARICA
    # 5 è un codice di marcia, non un cavo.
    s = interpreta(_c10(**{"1149": 5, "47": 0, **fermo}))
    assert (s.stato_ricarica, s.cavo_collegato) == (StatoRicarica.NON_IN_CARICA, False)


def test_in_marcia_non_si_carica_e_il_cavo_non_c_e() -> None:
    # La B10 tiene 47 = 1 per qualche minuto dopo aver staccato: in marcia non conta.
    s = interpreta(_c10(**{"1319": 48.0, "1010": 3}))
    assert s.marcia == Marcia.AVANTI
    assert s.in_marcia is True
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False


def test_la_marcia_come_la_dice_l_app() -> None:
    assert interpreta(_c10(**{"1010": 1})).marcia == Marcia.RETROMARCIA
    assert interpreta(_c10(**{"1010": 3})).marcia == Marcia.AVANTI
    assert interpreta(_c10(**{"1010": 0xFFFFFF})).marcia is None


def test_v2l_e_ricarica_rapida() -> None:
    s = interpreta(_c10(**{"47": 2, "1149": 0, "1178": 5.0, "1200": 0}))
    assert s.v2l_attivo is True
    assert s.in_carica is False
    s = interpreta(_c10(**{"47": 0, "1197": 1, "1178": -250.0}))
    assert s.ricarica_rapida is True
    assert interpreta(C10).ricarica_rapida is False
    # La T03 accende «rapida» anche in pausa alla wallbox: lì non si dice.
    assert interpreta({**T03, "chargeState": 1, "dcInputFastCharge": 1}).ricarica_rapida is None


def test_lo_zero_km_della_c10_che_si_addormenta() -> None:
    s = interpreta(_c10(**{"3260": 0, "2188": 0, "1204": 90}))
    assert s.autonomia_km is None
    assert interpreta(_c10(**{"3260": 0, "2188": 310})).autonomia_km == 310
    assert interpreta(_c10(**{"3260": 0, "1204": 3, "100003": 3})).autonomia_km == 0


def test_la_serratura_solo_con_1_o_0() -> None:
    assert interpreta(_c10(**{"1298": 1})).chiusa is True
    assert interpreta(_c10(**{"1298": 0})).chiusa is False
    assert interpreta(_c10(**{"1298": 2})).chiusa is None


def test_i_finestrini_secondo_il_modello() -> None:
    # B10: il segnale dice 2 quando è aperto; la percentuale resta a 0.
    assert interpreta(_c10(**{"1694": 2})).finestrini_aperti is True
    assert interpreta(_c10(**{"3727": 40, "1693": 0})).finestrini_aperti is False
    # T03: il contrario.
    assert interpreta({**T03, "driverWindowStatus": 0, "leftFrontWindowPercent": 30}).finestrini_aperti is True


def test_i_dati_nuovi() -> None:
    s = interpreta(
        _c10(**{"3257": 410, "1186": 4, "1480": 1, "1255": 2, "1945": 2})
        | {"config": {"3": {"percent": 80, "isEnable": 1, "beginTime": "1:30", "endTime": "07:00"}}}
    )
    assert s.autonomia_nominale_km == 410
    assert s.batteria_in_riscaldamento is True
    assert s.freno_stazionamento is True
    assert s.antifurto is True
    assert s.sbrinamento is True
    assert (s.ricarica_programmata, s.ricarica_ora_inizio, s.ricarica_ora_fine) == (True, "01:30", "07:00")
    assert interpreta(C10).ricarica_programmata is False
    assert interpreta({**T03, "outdoorTemp": 12.5}).temperatura_esterna_c == 12.5


def test_l_ibrida_col_range_extender() -> None:
    s = interpreta(_c10(**{"3235": 62, "3259": 610, "3261": 888}))
    assert (s.carburante_percento, s.autonomia_carburante_km, s.autonomia_totale_km) == (62, 610, 888)
    assert interpreta(C10).carburante_percento is None


def test_i_dati_vecchi_sono_di_un_auto_che_dorme() -> None:
    adesso = int(datetime.now(UTC).timestamp() * 1000)
    assert interpreta(_c10(sts=adesso)).in_sonno is False
    assert interpreta(_c10(sts=adesso - 3_600_000)).in_sonno is True
    assert interpreta(T03).in_sonno is None  # la T03: non si sa
