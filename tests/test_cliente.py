"""Il cliente Leapmotor contro un cloud finto col TLS reciproco vero."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
import json
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

from custom_components.gdaauto.marche.leapmotor import comandi_di, crea, cripto
from custom_components.gdaauto.marche.leapmotor.errori import ErroreCertificato, ErroreCredenziali, ErrorePin
from custom_components.gdaauto.vettura import StatoRicarica

from .finto_cloud import FintoCloud
from .test_segnali import C10, T03

VIN_T03 = "LTEST000000000T03"
VIN_B10 = "LTEST000000000B10"


@pytest.fixture
async def cloud(tmp_path: Path, socket_enabled: None) -> AsyncGenerator[tuple[FintoCloud, str]]:
    finto = FintoCloud(tmp_path)
    finto.vetture = [
        {
            "vin": VIN_T03,
            "carType": "T03",
            "nickName": "Bianchina",
            "rightList": "110,120,130,170,180,190",
            "year": 2024,
        },
        {"vin": VIN_B10, "carType": "B10", "carId": "99", "_condivisa": True, "plateNumber": "GA123AA"},
    ]
    finto.stati = {VIN_T03: T03, VIN_B10: C10}
    runner = web.AppRunner(finto.app())
    await runner.setup()
    sito = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=finto.ssl())
    await sito.start()
    porta = runner.addresses[0][1]
    yield finto, f"https://127.0.0.1:{porta}"
    await runner.cleanup()


def _cliente(finto: FintoCloud, url: str, sessione: aiohttp.ClientSession, tmp: Path, password: str = "segreta"):
    return crea(
        sessione,
        {
            "email": "auto@esempio.it",
            "password": password,
            "cert_app": finto.cert_app.decode(),
            "chiave_app": finto.chiave_app.decode(),
            "base_url": url,
        },
        tmp / "cert",
        "it-IT",
    )


async def test_entra_elenca_e_legge_tutti_i_modelli(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [VIN_T03, VIN_B10]
        t03, b10 = vetture
        assert t03.titolo == "Bianchina"
        assert t03.anno == 2024
        assert b10.condivisa
        assert b10.targa == "GA123AA"

        st = await c.stato(t03)
        assert st.batteria == 85
        sb = await c.stato(b10)
        assert sb.stato_ricarica == StatoRicarica.IN_CARICA
    # La B10 si legge dall'indirizzo della C10.
    assert "vehicle/v1/status/get/t03" in finto.chiamate
    assert "vehicle/v1/status/get/c10" in finto.chiamate
    assert "vehicle/v1/status/get/b10" not in finto.chiamate
    # I certificati stanno su disco solo per chi li possiede.
    for nome in ("app_cert.pem", "app_key.pem", "account_cert.pem", "account_key.pem"):
        assert ((tmp_path / "cert" / nome).stat().st_mode & 0o777) == 0o600


async def test_un_modello_nuovo_ripiega_sulla_c10_e_se_lo_ricorda(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    finto.vetture = [{"vin": "NUOVA", "carType": "Z77"}]
    finto.stati = {"NUOVA": C10}
    finto.percorsi_che_non_esistono = {"z77"}
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (v,) = await c.vetture()
        assert (await c.stato(v)).batteria == 65
        assert (await c.stato(v)).batteria == 65
    assert finto.chiamate.count("vehicle/v1/status/get/z77") == 1
    assert finto.chiamate.count("vehicle/v1/status/get/c10") == 2


async def test_il_token_scaduto_si_rinnova_da_solo(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    finto.token_scade_alla = 2
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (t03, _) = await c.vetture()
        await c.stato(t03)
        assert (await c.stato(t03)).batteria == 85
    assert "acct/v1/token/refresh" in finto.chiamate
    assert finto.login == 1


async def test_se_il_rinnovo_non_va_si_rientra_da_capo(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    finto.token_scade_alla = 1
    finto.rinnovo_rotto = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (t03, _) = await c.vetture()
        assert (await c.stato(t03)).batteria == 85
    assert finto.login == 2


async def test_password_sbagliata(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path, password="sbagliata")
        with pytest.raises(ErroreCredenziali):
            await c.accedi()


async def test_senza_certificato_dell_app(tmp_path: Path) -> None:
    async with aiohttp.ClientSession() as s:
        c = crea(s, {"email": "a@b.it", "password": "x"}, tmp_path, "it-IT")
        with pytest.raises(ErroreCertificato):
            await c.accedi()


async def test_un_comando_col_pin(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    finto.esiti_prima_del_si = 2
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (t03, b10) = await c.vetture()
        await c.chiudi(t03, "1234")
        await c.clima(t03, False, "1234")
        await c.clima(b10, False, "1234")
    # Il certificato si sincronizza una volta per sessione, il PIN si verifica ogni volta.
    assert finto.chiamate.count("vehicle/v1/cert/sync") == 1
    assert finto.chiamate.count("vehicle/v1/operPwd/verify") == 3
    chiudi, clima_t03, clima_b10 = finto.comandi
    assert (chiudi["cmdId"], json.loads(chiudi["cmdContent"])) == ("110", {"value": "lock"})
    # La T03 vuole lo spegnimento col corpo intero; le altre quello corto.
    assert json.loads(clima_t03["cmdContent"])["windlevel"] == "3"
    assert json.loads(clima_b10["cmdContent"]) == {"operate": "off"}


async def test_il_pin_sbagliato(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (t03, _) = await c.vetture()
        with pytest.raises(ErrorePin):
            await c.trova(t03, "9999")
        with pytest.raises(ErrorePin):
            await c.trova(t03, None)
    assert finto.comandi == []


async def test_la_destinazione_non_vuole_il_pin(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (t03, _) = await c.vetture()
        await c.destinazione(t03, "Casa", 41.9028, 12.4964)
    assert "vehicle/v1/operPwd/verify" not in finto.chiamate
    (cmd,) = finto.comandi
    assert cmd["cmdId"] == "180"
    assert json.loads(cmd["cmdContent"])["latitude"] == "41.902800"


async def test_il_limite_tiene_il_piano_che_c_e(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (_, b10) = await c.vetture()
        b10.stato = await c.stato(b10)
        await c.limite_ricarica(b10, 85, "1234")
    contenuto = json.loads(finto.comandi[0]["cmdContent"])
    assert contenuto["chargesoc"] == 85
    assert contenuto["starttime"] == "22:00"


async def test_i_comandi_seguono_i_diritti(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        (t03, b10) = await c.vetture()
    # La T03 ha i diritti 110/120/170/180/190 ma il limite da remoto non lo accetta.
    assert comandi_di(t03) == {"chiudi", "trova", "clima", "destinazione"}
    # Senza elenco dei diritti decide il cloud.
    assert comandi_di(b10) == {"chiudi", "trova", "clima", "ricarica", "limite_ricarica", "destinazione"}


def test_il_nonce_del_cloud_e_quello_del_cliente() -> None:
    assert cripto.SORGENTE == "leapmotor"


async def test_il_consumo_medio_si_legge_ogni_mezz_ora(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        t03, b10 = await c.vetture()
        assert (await c.stato(b10)).consumo_medio_kwh_100km == 15.8
        # Alla lettura dopo non si richiede: è lo stesso numero.
        assert (await c.stato(b10)).consumo_medio_kwh_100km == 15.8
        assert finto.letture_consumo == 1
        # Ogni auto il suo.
        await c.stato(t03)
        assert finto.letture_consumo == 2


async def test_senza_consumo_lo_stato_arriva_lo_stesso(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    finto.consumo = None
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        _t03, b10 = await c.vetture()
        st = await c.stato(b10)
    assert st.batteria == 65
    assert st.consumo_medio_kwh_100km is None


async def test_senza_classifica_dei_consumi_lo_stato_arriva_lo_stesso(cloud, tmp_path: Path) -> None:
    """Una B10 nuova: il cloud risponde «rankResult»: null. Prima questo fermava ogni lettura."""
    finto, url = cloud
    finto.consumo = ""
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        _t03, b10 = await c.vetture()
        st = await c.stato(b10)
    assert st.batteria == 65
    assert st.consumo_medio_kwh_100km is None
    assert st.km_7_giorni == 60.5


async def test_lo_storico_energia_di_oggi_settimana_e_ultima_ricarica(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        t03, b10 = await c.vetture()
        st = await c.stato(b10)
        st_t03 = await c.stato(t03)
    assert (st.energia_oggi_guida_kwh, st.energia_oggi_clima_kwh, st.energia_oggi_altro_kwh) == (3.2, 0.8, 0.1)
    assert (st.km_7_giorni, st.energia_7_giorni_kwh) == (60.5, 10.0)
    # La T03 conta l'energia in Wh.
    assert st_t03.energia_7_giorni_kwh == 0.01
    # L'ultima è la più recente, in DC.
    assert (st.ultima_ricarica_kwh, st.ultima_ricarica_tipo) == (31.0, "dc")
    assert st.ultima_ricarica_inizio == datetime.fromtimestamp(1760800000, UTC)
    assert st.ultima_ricarica_fine == datetime.fromtimestamp(1760802000, UTC)
    assert "charge/daily/detail/page" in finto.chiamate


async def test_lo_storico_che_non_va_non_ferma_lo_stato(cloud, tmp_path: Path) -> None:
    finto, url = cloud
    finto.ricariche = [{"chargeGunStartTs": "x"}]
    finto.giorni = []
    finto.energia_oggi = {}
    async with aiohttp.ClientSession() as s:
        c = _cliente(finto, url, s, tmp_path)
        _t03, b10 = await c.vetture()
        st = await c.stato(b10)
    assert st.batteria == 65
    assert st.km_7_giorni is None and st.energia_oggi_guida_kwh is None
    assert st.ultima_ricarica_tipo is None
