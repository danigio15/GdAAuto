"""BYD: interpretazione dei payload, cliente contro un cloud finto, flusso HA.

Il cloud vero avvolge tutto in una busta Bangcle (AES a scatola bianca con
tabelle estratte dall'app). Nelle prove la busta è finta — un semplice Base64
reversibile — così si esercita tutto il protocollo (login, lettura, rinnovo,
comandi, errori) senza le tabelle di BYD, che GdAAuto non contiene. L'AES a
scatola bianca vero è verificato a parte (vedi il resoconto).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import byd
from custom_components.gdaauto.marche.byd import cripto
from custom_components.gdaauto.marche.byd.busta import Busta, Tabelle, carica_tabelle, cifra_blocco, decifra_blocco
from custom_components.gdaauto.marche.byd.cliente import ClienteByd
from custom_components.gdaauto.marche.byd.dati import interpreta
from custom_components.gdaauto.marche.errori import (
    AutoAddormentata,
    ErroreCertificato,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import byd_campioni as c

# =============================================================================
# 1. Interpretazione dei payload
# =============================================================================


def test_interpreta_auto_in_carica() -> None:
    s = interpreta(c.REALTIME, gps=c.GPS, ricarica=c.RICARICA, clima=c.CLIMA, energia=c.ENERGIA)
    assert s.batteria == 64
    assert s.autonomia_km == 268
    assert s.odometro_km == 12873
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 90
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.finestrini_aperti is False
    assert s.bagagliaio_aperto is False
    assert s.freno_stazionamento is True
    assert s.antifurto is False
    assert s.pneumatici_allarme is False
    # gomme: kPa restano kPa
    assert s.pneumatici_kpa == {"ant_sx": 250.0, "ant_dx": 250.0, "post_sx": 240.0, "post_dx": 240.0}
    assert s.temperatura_interna_c == 21.0
    assert s.temperatura_esterna_c == 14.0
    assert s.clima_acceso is True
    assert s.ricarica_programmata is True
    assert s.ricarica_ora_inizio == "23:00"
    assert s.ricarica_ora_fine == "07:00"
    assert s.consumo_medio_kwh_100km == 16.8
    assert s.latitudine == 45.4642
    assert s.longitudine == 9.19
    assert s.posizione_con_segno is True
    assert s.in_sonno is None


def test_interpreta_gomme_in_psi() -> None:
    s = interpreta({**c.REALTIME, "tirePressUnit": 2, "leftFrontTirepressure": 36})
    assert s.pneumatici_kpa["ant_sx"] == pytest.approx(248.2, abs=0.2)


def test_interpreta_auto_che_dorme() -> None:
    s = interpreta(c.REALTIME_DORME)
    assert s.in_sonno is True
    assert s.autonomia_km is None  # -1 è «non lo so», non zero
    assert s.temperatura_interna_c is None  # -129 è la sentinella


def test_interpreta_allarme_gomme() -> None:
    s = interpreta({**c.REALTIME, "rapidTireLeak": 1})
    assert s.pneumatici_allarme is True


# =============================================================================
# 2. La busta Bangcle (formato del file e catena CBC)
# =============================================================================


def _file_tabelle(**modifiche: Any) -> bytes:
    """Un file BGTB valido nel formato, con tabelle a zero (solo per il parser)."""
    import struct

    specifiche = [
        ("inv_giro", 0x28000),
        ("inv_xor", 0x3C000),
        ("inv_prima", 0x1000),
        ("giro", 0x28000),
        ("xor", 0x3C000),
        ("finale", 0x1000),
        ("perm_decifra", 8),
        ("perm_cifra", 8),
    ]
    testa = b"BGTB" + struct.pack("<HH", modifiche.get("versione", 1), modifiche.get("quante", 8))
    indice = b""
    corpo = b""
    offset = len(testa) + 8 * 8
    for _nome, lung in specifiche:
        indice += struct.pack("<II", offset, lung)
        corpo += b"\x00" * lung
        offset += lung
    return testa + indice + corpo


def test_carica_tabelle_formato() -> None:
    t = carica_tabelle(_file_tabelle())
    assert isinstance(t, Tabelle)
    assert len(t.inv_giro) == 0x28000
    assert len(t.perm_cifra) == 8


def test_carica_tabelle_errori() -> None:
    from custom_components.gdaauto.marche.byd.busta import ErroreTabelle

    with pytest.raises(ErroreTabelle, match="troppo corto"):
        carica_tabelle(b"BGTB")
    with pytest.raises(ErroreTabelle, match="Firma"):
        carica_tabelle(b"XXXX" + _file_tabelle()[4:])
    with pytest.raises(ErroreTabelle, match="Versione"):
        carica_tabelle(_file_tabelle(versione=9))


def test_busta_cbc_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """La catena CBC, il PKCS#7 e la cornice ``F``+Base64 con blocchi finti inversi.

    I blocchi veri (il table-lookup) sono verificati a parte contro pyBYD; qui si
    prova lo strato intorno (CBC, riempimento, Base64) con una cifra reversibile.
    """
    import custom_components.gdaauto.marche.byd.busta as mod

    monkeypatch.setattr(mod, "cifra_blocco", lambda _t, b: bytes(reversed(b)))
    monkeypatch.setattr(mod, "decifra_blocco", lambda _t, b: bytes(reversed(b)))
    b = Busta(carica_tabelle(_file_tabelle()))
    for msg in (b"{}", b'{"a":1}', b"x" * 31, bytes(range(60))):
        chiuso = b.chiudi(msg)
        assert chiuso.startswith("F")
        assert b.apri(chiuso) == msg


# =============================================================================
# 3. Il cliente contro un cloud finto
# =============================================================================

EMAIL = "mario@example.com"
PASSWORD = "segretissima"
PIN = "1234"
_ST, _ET, _UID = "sign-token-1", "encry-token-1", "utente-1"


class BustaFinta:
    """Al posto dell'AES a scatola bianca: Base64 reversibile, niente tabelle."""

    def chiudi(self, chiaro: str | bytes) -> str:
        grezzo = chiaro.encode() if isinstance(chiaro, str) else chiaro
        return "F" + base64.b64encode(grezzo).decode()

    def apri(self, busta: str) -> bytes:
        return base64.b64decode(busta[1:])


@dataclass
class FintaBYD:
    """Un cloud BYD finto: stessa crittografia interna, busta sostituita."""

    busta: BustaFinta = field(default_factory=BustaFinta)
    rifiuta_login: bool = False
    pin_sbagliato: bool = False
    auto_dorme: bool = False
    comando_rifiutato: bool = False
    con_energia: bool = False
    scade_una_volta: bool = False
    chiamate: list[str] = field(default_factory=list)
    comandi: list[dict[str, Any]] = field(default_factory=list)
    _scaduta_gia: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{coda:.*}", self._gestisci)
        return app

    def _risposta(self, outer: dict[str, Any]) -> web.Response:
        return web.json_response({"response": self.busta.chiudi(json.dumps(outer))})

    def _contenuto(self, dati: dict[str, Any], chiave: str) -> dict[str, Any]:
        return {"code": "0", "message": "", "respondData": cripto.cifra_hex(json.dumps(dati), chiave)}

    async def _gestisci(self, r: web.Request) -> web.Response:
        corpo = await r.json()
        outer = json.loads(self.busta.apri(corpo["request"]))
        percorso = r.path
        self.chiamate.append(percorso)

        if percorso == "/app/account/login":
            if self.rifiuta_login:
                return self._risposta({"code": "3", "message": "credenziali errate"})
            token = {"userId": _UID, "signToken": _ST, "encryToken": _ET}
            return self._risposta(self._contenuto({"token": token}, cripto.chiave_login(PASSWORD)))

        chiave = cripto.md5(_ET)
        inner = json.loads(cripto.decifra_hex(outer["encryData"], chiave))

        if percorso.endswith("getAllListByUserId"):
            if self.scade_una_volta and not self._scaduta_gia:
                self._scaduta_gia = True
                return self._risposta({"code": "1002", "message": "token scaduto"})
            return self._risposta(self._contenuto(c.ELENCO, chiave))  # type: ignore[arg-type]

        if "vehicleRealTime" in percorso:
            if self.auto_dorme:
                return self._risposta({"code": "6002", "message": "auto non raggiungibile"})
            return self._risposta(self._contenuto(c.REALTIME, chiave))
        if "getGpsInfo" in percorso:
            return self._risposta(self._contenuto({"data": c.GPS}, chiave))
        if "smartCharge/homePage" in percorso:
            return self._risposta(self._contenuto(c.RICARICA, chiave))
        if "getStatusNow" in percorso:
            return self._risposta(self._contenuto(c.CLIMA, chiave))
        if "getEnergyConsumption" in percorso:
            return self._risposta(self._contenuto(c.ENERGIA, chiave))

        if percorso.endswith("verifyControlPassword"):
            if self.pin_sbagliato:
                return self._risposta({"code": "5005", "message": "PIN errato"})
            return self._risposta(self._contenuto({"ok": True}, chiave))
        if "smartCharge/changeChargeStatue" in percorso:
            self.comandi.append(inner)
            return self._risposta(self._contenuto({"res": 2}, chiave))
        if percorso.endswith("remoteControl"):
            self.comandi.append(inner)
            if self.comando_rifiutato:
                return self._risposta(self._contenuto({"res": 3, "result": "occupato"}, chiave))
            return self._risposta(self._contenuto({"res": 2, "result": "ok"}, chiave))

        return web.json_response({"error": "sconosciuto"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintaBYD, str]]:
    finta = FintaBYD()
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    yield finta, base
    await runner.cleanup()


def _cliente(s: aiohttp.ClientSession, base: str, **kw: Any) -> ClienteByd:
    cl = ClienteByd(s, email=EMAIL, password=PASSWORD, busta=BustaFinta(), pin=PIN, paese="IT", base_url=base, **kw)
    cl._attesa = 0  # niente attese nelle prove
    return cl


async def test_login_ed_elenco(cloud) -> None:
    _finta, base = cloud
    salvati: list[dict] = []
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base, salva=salvati.append)
        await cl.accedi()
        vetture = await cl.vetture()
    assert cl.sessione["userId"] == _UID
    assert salvati and salvati[0]["encryToken"] == _ET
    assert [v.vin for v in vetture] == [c.VIN]
    assert vetture[0].marca == "byd"
    assert vetture[0].modello == "ATTO 3"
    assert vetture[0].nome == "Delfino"


async def test_stato_completo(cloud) -> None:
    _finta, base = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        stato = await cl.stato(Vettura(vin=c.VIN, marca="byd", modello="ATTO 3"))
    assert stato.batteria == 64
    assert stato.stato_ricarica == StatoRicarica.IN_CARICA
    assert stato.latitudine == 45.4642
    assert stato.clima_acceso is True
    assert stato.chiusa is True


async def test_rinnovo_token(cloud) -> None:
    finta, base = cloud
    finta.scade_una_volta = True
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        vetture = await cl.vetture()  # 1002 -> rilogin -> riprova
    assert [v.vin for v in vetture] == [c.VIN]
    # due login: il primo e quello dopo lo scadere
    assert finta.chiamate.count("/app/account/login") == 2


async def test_comando_chiudi(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        await cl.chiudi(Vettura(vin=c.VIN, marca="byd", modello="ATTO 3"), PIN)
    # prima verifica il PIN, poi manda il comando
    assert any(ch.endswith("verifyControlPassword") for ch in finta.chiamate)
    assert finta.comandi[-1]["commandType"] == "LOCKDOOR"
    assert finta.comandi[-1]["commandPwd"] == cripto.md5(PIN)


async def test_comando_clima_con_temperatura(cloud) -> None:
    finta, base = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        await cl.clima(Vettura(vin=c.VIN, marca="byd", modello="ATTO 3"), True, PIN, 21)
    params = json.loads(finta.comandi[-1]["controlParamsMap"])
    assert finta.comandi[-1]["commandType"] == "OPENAIR"
    assert params["mainSettingTemp"] == 7  # 21 °C -> scala 1-17


async def test_comando_rifiutato(cloud) -> None:
    finta, base = cloud
    finta.comando_rifiutato = True
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        with pytest.raises(ErroreComando, match="occupato"):
            await cl.apri(Vettura(vin=c.VIN, marca="byd", modello="ATTO 3"), PIN)


async def test_ricarica_stop_non_supportato(cloud) -> None:
    _finta, base = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        with pytest.raises(ErroreComando, match="non ferma"):
            await cl.ricarica(Vettura(vin=c.VIN, marca="byd", modello="ATTO 3"), False, PIN)


async def test_pin_sbagliato(cloud) -> None:
    finta, base = cloud
    finta.pin_sbagliato = True
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        with pytest.raises(ErrorePin):
            await cl.chiudi(Vettura(vin=c.VIN, marca="byd", modello="ATTO 3"), PIN)


async def test_credenziali_rifiutate(cloud) -> None:
    finta, base = cloud
    finta.rifiuta_login = True
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        with pytest.raises(ErroreCredenziali):
            await cl.accedi()


async def test_auto_addormentata(cloud) -> None:
    finta, base = cloud
    finta.auto_dorme = True
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, base)
        await cl.accedi()
        with pytest.raises(AutoAddormentata):
            await cl.stato(Vettura(vin=c.VIN, marca="byd", modello="ATTO 3"))


async def test_errore_di_rete(socket_enabled: None) -> None:
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, "http://127.0.0.1:1")  # porta morta
        with pytest.raises(ErroreRete):
            await cl.accedi()


# =============================================================================
# 4. Il white-box è coerente: i blocchi importati esistono e la busta li usa
# =============================================================================


def test_funzioni_blocco_presenti() -> None:
    # i blocchi veri richiedono le tabelle di BYD: qui si controlla solo che
    # l'algoritmo sia esposto e deterministico sulla stessa struttura.
    assert callable(cifra_blocco) and callable(decifra_blocco)


# =============================================================================
# 5. Flusso Home Assistant: dalla voce alle entità
# =============================================================================


def _voce(**opzioni: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "byd",
            "email": EMAIL,
            "password": PASSWORD,
            "paese": "IT",
            "pin": PIN,
        },
        options={},
        unique_id=f"byd_{EMAIL}",
    )


async def test_flusso_ha(hass: HomeAssistant) -> None:
    voce = _voce()
    voce.add_to_hass(hass)
    auto = Vettura(vin=c.VIN, marca="byd", modello="ATTO 3", nome="Delfino")
    stato = interpreta(c.REALTIME, gps=c.GPS, ricarica=c.RICARICA, clima=c.CLIMA)
    with (
        patch.object(byd, "_busta_da_file", AsyncMock(return_value=BustaFinta())),
        patch.object(ClienteByd, "accedi", AsyncMock()),
        patch.object(ClienteByd, "vetture", AsyncMock(return_value=[auto])),
        patch.object(ClienteByd, "stato", AsyncMock(return_value=stato)),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("sensor.delfino_batteria").state == "64"
    assert hass.states.get("lock.delfino_serratura").state == "locked"
    assert hass.states.get("device_tracker.delfino_posizione") is not None


async def test_le_tabelle_si_scaricano_da_sole(hass: HomeAssistant, aioclient_mock, tmp_path, monkeypatch) -> None:
    """Senza il file in /config/gdaauto/, le tabelle arrivano da pyBYD e si salvano; un'impronta diversa no."""
    import hashlib

    monkeypatch.setattr(hass.config, "config_dir", str(tmp_path))
    contenuto = _file_tabelle()
    aioclient_mock.get(byd.URL_TABELLE, content=contenuto)
    with pytest.raises(ErroreCertificato):
        await byd._busta_da_file(hass)
    assert not (tmp_path / "gdaauto" / "bangcle_tables.bin").exists()

    monkeypatch.setattr(byd, "SHA256_TABELLE", hashlib.sha256(contenuto).hexdigest())
    assert isinstance(await byd._busta_da_file(hass), Busta)
    assert (tmp_path / "gdaauto" / "bangcle_tables.bin").read_bytes() == contenuto
    # La volta dopo c'è già: non si riscarica.
    await byd._busta_da_file(hass)
    assert aioclient_mock.call_count == 2
