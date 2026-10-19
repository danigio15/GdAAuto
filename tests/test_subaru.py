"""Subaru: le letture di MySubaru, il cliente contro un cloud finto su
127.0.0.1 (login, verifica del dispositivo, sessione a cookie, comandi col PIN)
e la configurazione in Home Assistant.

I payload sono quelli dei fixture di G-Two/subarulink (``subaru_campioni``).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import subaru
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import (
    CodiceInviato,
    CodiceSbagliato,
    ErroreComando,
    ErroreCredenziali,
    ErrorePin,
    ErroreRete,
)
from custom_components.gdaauto.marche.subaru import cliente as modulo_cliente
from custom_components.gdaauto.marche.subaru.cliente import ClienteSubaru, Sessione, autorizza, clima_da
from custom_components.gdaauto.marche.subaru.dati import comandi_di, interpreta, vettura_da
from custom_components.gdaauto.vettura import StatoRicarica

from . import subaru_campioni as c

# --- le letture --------------------------------------------------------------------


def _phev():
    v = vettura_da(c.SCELTA_PHEV["data"])
    assert v is not None
    return v


def test_una_crosstrek_plug_in_in_carica() -> None:
    risultato = c.CONDIZIONI["data"]["result"]
    s = interpreta(c.STATO["data"], risultato, c.POSIZIONE["data"]["result"], c.SPIE["data"])
    assert s.batteria == 60  # le condizioni sono più fresche dello stato (23 %)
    assert s.autonomia_km == 643
    assert s.odometro_km == 19867
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 120
    assert s.carburante_percento == 20
    assert s.autonomia_carburante_km == 640
    assert s.autonomia_totale_km == 1283
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 240.0,
        "anteriore_destro": 240.0,
        "posteriore_sinistro": 240.0,
        "posteriore_destro": 245.0,
    }
    assert s.pneumatici_allarme is False
    assert s.quadro_acceso is False
    # Il baule è aperto: l'auto non è tutta chiusa. I finestrini anteriori sono socchiusi.
    assert s.chiusa is False
    assert s.portiere_aperte is None  # «UNKNOWN» dappertutto
    assert s.finestrini_aperti is True
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (39.83, -98.585, True)
    assert s.letto == datetime(2022, 9, 27, 11, 22, 15, tzinfo=UTC)


def test_solo_lo_stato_e_i_valori_non_validi() -> None:
    stato = {
        **c.STATO["data"],
        "evStateOfChargePercent": 81,
        "tirePressureFrontLeft": 32767,
        "tirePressureFrontLeftPsi": 32767,
        "distanceToEmptyFuelKilometers10s": 16383,
        "latitude": 90,
        "longitude": 180,
    }
    s = interpreta(stato)
    assert s.batteria == 81
    assert "anteriore_sinistro" not in s.pneumatici_kpa
    assert s.autonomia_carburante_km is None
    assert s.latitudine is None
    assert s.finestrini_aperti is True  # «VENTED» dallo stato
    assert s.stato_ricarica is None and s.chiusa is None
    # Un'auto a benzina non ha batteria di trazione.
    assert interpreta(stato, elettrica=False).batteria is None


def test_le_auto_e_i_comandi() -> None:
    phev = _phev()
    assert (phev.vin, phev.modello, phev.nome, phev.anno) == (c.VIN_PHEV, "Crosstrek", "TEST_SUBARU_2", 2019)
    assert phev.extra["elettrica"] is True and phev.extra["remoto"] is True and phev.extra["percorso"] == "g2"
    assert comandi_di(phev) == {"chiudi", "apri", "trova", "clima", "ricarica"}
    benzina = vettura_da(c.SCELTA_BENZINA["data"])
    assert benzina is not None and benzina.extra["elettrica"] is False
    assert comandi_di(benzina) == {"chiudi", "apri", "trova", "clima"}  # ha l'avviamento a distanza
    senza = vettura_da({**c.SCELTA_PHEV["data"], "subscriptionStatus": "EXPIRED"})
    assert senza is not None and comandi_di(senza) == set()


def test_il_clima_in_fahrenheit() -> None:
    assert clima_da(22, elettrica=True)["climateZoneFrontTemp"] == "72"
    assert clima_da(40, elettrica=True)["climateZoneFrontTemp"] == "85"
    assert clima_da(22, elettrica=False)["startConfiguration"] == "START_ENGINE_ALLOW_KEY_IN_IGNITION"


# --- il cliente contro un cloud finto ----------------------------------------------


@dataclass
class FintaSubaru:
    password: str = "giusta"
    pin: str = "1234"
    versione: str = "/g2v33"
    registrati: set[str] = field(default_factory=set)
    codice: str = "123456"
    sessioni: int = 0
    valida: str = ""
    rotto: bool = False
    #: quante domande a remoteService/status.json prima di «finished»
    passi: int = 1
    esito_comando: dict[str, Any] | None = None
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, Any]] = field(default_factory=list)
    codice_mandato_a: str = ""

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{versione}/{resto:.*}", self._tutto)
        return app

    def _risposta(self, corpo: Any, **kw: Any) -> web.Response:
        return web.json_response(corpo, **kw)

    async def _tutto(self, r: web.Request) -> web.Response:
        resto = "/" + r.match_info["resto"]
        self.chiamate.append(resto)
        if self.rotto:
            return web.Response(status=503, text="Service Unavailable")
        if "/" + r.match_info["versione"] != self.versione:
            return web.Response(status=404, text="Not Found")
        assert r.headers["X-Requested-With"] == "com.subaru.telematics.app.remote"
        if resto == "/login.json":
            modulo = await r.post()
            assert modulo["deviceType"] == "android" and modulo["env"] == "cloudprod"
            if modulo["password"] != self.password:
                return self._risposta(c.LOGIN_SBAGLIATO)
            self.sessioni += 1
            self.valida = f"S-{self.sessioni}"
            corpo = copy.deepcopy(c.LOGIN)
            corpo["data"]["deviceRegistered"] = modulo["deviceId"] in self.registrati
            corpo["data"]["vehicles"][0]["vin"] = c.VIN_PHEV
            risposta = self._risposta(corpo)
            risposta.set_cookie("JSESSIONID", self.valida)
            return risposta
        if r.cookies.get("JSESSIONID") != self.valida:
            return self._risposta(c.TOKEN_NON_VALIDO)
        if resto == "/twoStepAuthContacts.json":
            return self._risposta(c.CONTATTI)
        if resto == "/twoStepAuthSendVerification.json":
            self.codice_mandato_a = r.query["contactMethod"]
            return self._risposta(c.RIUSCITO)
        if resto == "/twoStepAuthVerify.json":
            if r.query["verificationCode"] != self.codice:
                return self._risposta({"success": False, "errorCode": "InvalidCode", "data": None})
            assert r.query["rememberDevice"] == "on"
            self.registrati.add(r.query["deviceId"])
            return self._risposta(c.RIUSCITO)
        if resto == "/validateSession.json":
            return self._risposta(c.SESSIONE_VALIDA)
        if resto == "/selectVehicle.json":
            assert r.query["vin"] == c.VIN_PHEV
            return self._risposta(c.SCELTA_PHEV)
        if resto == "/vehicleStatus.json":
            return self._risposta(c.STATO)
        if resto == "/service/g2/condition/execute.json":
            return self._risposta(c.CONDIZIONI)
        if resto == "/service/g2/locate/execute.json":
            return self._risposta(c.POSIZIONE)
        if resto == "/vehicleHealth.json":
            return self._risposta(c.SPIE)
        if resto == "/service/g2/remoteService/status.json":
            assert r.query["serviceRequestId"].startswith("JF2ABCDE6L0000002")
            self.passi -= 1
            if self.passi > 0:
                return self._risposta(c.esito("started"))
            return self._risposta(self.esito_comando or c.esito("finished", True))
        if resto == "/service/g2/remoteEngineQuickStartSettings/save.json":
            self.comandi.append((resto, await r.json()))
            return self._risposta(c.RIUSCITO)
        if resto.startswith("/service/g2/") and resto.endswith("/execute.json"):
            corpo = await r.json()
            self.comandi.append((resto, corpo))
            if corpo["pin"] != self.pin:
                return self._risposta(c.PIN_SBAGLIATO)
            return self._risposta(c.avviato(resto.split("/")[3]))
        return web.Response(status=404, text="Not Found")


@pytest.fixture
async def cloud(socket_enabled: None, monkeypatch) -> AsyncGenerator[tuple[FintaSubaru, str]]:
    finta = FintaSubaru(registrati={"1700000000"})
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    modulo_cliente._IN_SOSPESO.clear()
    for nome in ("PAUSA_COMANDO", "PAUSA_REGISTRAZIONE", "PAUSA_GIA_IN_CORSO"):
        monkeypatch.setattr(modulo_cliente, nome, 0.0)
    yield finta, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _sessione(s: aiohttp.ClientSession, server: str, **kw: Any) -> Sessione:
    dati = {"email": "mario@esempio.com", "password": "giusta", "dispositivo": "1700000000", **kw}
    return Sessione(s, dati["email"], dati["password"], dati["dispositivo"], server=server)


async def test_login_auto_e_stato(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        cliente = ClienteSubaru(_sessione(s, server))
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        assert auto.vin == c.VIN_PHEV and comandi_di(auto)
        stato = await cliente.stato(auto)
        assert stato.batteria == 60
        assert stato.latitudine == 39.83
        await cliente.stato(auto)
    # Un login solo; le spie una volta ogni mezz'ora.
    assert finta.chiamate.count("/login.json") == 1
    assert finta.chiamate.count("/vehicleHealth.json") == 1
    assert finta.chiamate.count("/vehicleStatus.json") == 2


async def test_la_verifica_del_dispositivo(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        nuova = _sessione(s, server, dispositivo="42")
        with pytest.raises(CodiceInviato, match=r"my_\*+@email.com"):
            await autorizza(nuova)
        assert finta.codice_mandato_a == "userName"
        # Al secondo invio il modulo crea una sessione nuova (e un dispositivo nuovo): il codice basta.
        altra = _sessione(s, server, dispositivo="99")
        with pytest.raises(CodiceSbagliato):
            await autorizza(altra, "12")
        with pytest.raises(CodiceSbagliato):
            await autorizza(altra, "000000")
        await autorizza(altra, "123456")
        assert altra.registrato and altra.dispositivo == "42"
    assert "42" in finta.registrati


async def test_password_sbagliata_sessione_scaduta_e_guasti(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        with pytest.raises(ErroreCredenziali, match="InvalidCredentials"):
            await _sessione(s, server, password="sbagliata").entra()
        cliente = ClienteSubaru(_sessione(s, server))
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        # Il cloud dimentica la sessione: si rientra con la password, senza chiedere niente.
        finta.valida = "scaduta"
        assert (await cliente.stato(auto)).batteria == 60
        # Il login rifiutato, quello buono, e quello dopo la sessione scaduta.
        assert finta.chiamate.count("/login.json") == 3
        # Un dispositivo non più autorizzato chiede di rientrare.
        finta.registrati.clear()
        finta.valida = "scaduta"
        with pytest.raises(ErroreCredenziali, match="dispositivo"):
            await cliente.stato(auto)
        finta.rotto = True
        with pytest.raises(ErroreRete):
            await cliente.accedi()
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreRete):
            await _sessione(s, "http://127.0.0.1:9").entra()


async def test_la_versione_che_cambia(cloud) -> None:
    finta, server = cloud
    finta.versione = "/g2v35"
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        sessione = _sessione(s, server)
        await sessione.entra()
        assert sessione.versione == "/g2v35"


async def test_i_comandi_col_pin(cloud) -> None:
    finta, server = cloud
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as s:
        cliente = ClienteSubaru(_sessione(s, server))
        await cliente.accedi()
        (auto,) = await cliente.vetture()
        finta.passi = 3
        await cliente.chiudi(auto, "1234")
        await cliente.apri(auto, "1234")
        await cliente.trova(auto, "1234")
        await cliente.clima(auto, True, "1234", 21)
        await cliente.clima(auto, False, "1234")
        await cliente.ricarica(auto, True, "1234")
        with pytest.raises(ErroreComando, match="non ferma"):
            await cliente.ricarica(auto, False, "1234")
        with pytest.raises(ErrorePin, match="opzioni"):
            await cliente.chiudi(auto, None)
        # L'auto rifiuta: il motivo arriva a chi ha chiesto.
        finta.esito_comando = c.esito("finished", False, "NegativeAcknowledge:otherCommandsOngoing")
        with pytest.raises(ErroreComando, match="altro comando"):
            await cliente.chiudi(auto, "1234")
        # Un PIN sbagliato si prova una volta sola, per non bloccare l'account.
        with pytest.raises(ErrorePin, match="rifiuta"):
            await cliente.chiudi(auto, "0000")
        with pytest.raises(ErrorePin, match="già rifiutato"):
            await cliente.chiudi(auto, "0000")
    percorsi = [p for p, _ in finta.comandi]
    assert percorsi == [
        "/service/g2/lock/execute.json",
        "/service/g2/unlock/execute.json",
        "/service/g2/hornLights/execute.json",
        "/service/g2/remoteEngineQuickStartSettings/save.json",
        "/service/g2/engineStart/execute.json",
        "/service/g2/engineStop/execute.json",
        "/service/g2/phevChargeNow/execute.json",
        "/service/g2/lock/execute.json",
        "/service/g2/lock/execute.json",
    ]
    chiudi = finta.comandi[0][1]
    assert chiudi == {"pin": "1234", "delay": 0, "vin": c.VIN_PHEV, "forceKeyInCar": False}
    assert finta.comandi[1][1]["unlockDoorType"] == "ALL_DOORS_CMD"
    avvio = finta.comandi[4][1]
    assert avvio["climateZoneFrontTemp"] == "70"
    assert avvio["startConfiguration"] == "START_CLIMATE_CONTROL_ONLY_ALLOW_KEY_IN_IGNITION"


# --- dentro Home Assistant ------------------------------------------------------------

MARCHIO = Marchio(
    "subaru", "Subaru", "subaru", {}, "Subaru (solo Stati Uniti)", "https://www.home-assistant.io/integrations/subaru"
)


async def test_configurare_una_subaru(hass: HomeAssistant) -> None:
    auto = _phev()
    stato = interpreta(c.STATO["data"], c.CONDIZIONI["data"]["result"])
    with patch.dict(PER_ID, {"subaru": MARCHIO}):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "subaru"})
        assert r["step_id"] == "credenziali"
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password", "paese", "pin", "codice"}
        autorizza_finto = AsyncMock(side_effect=[CodiceInviato("codice"), None])
        with (
            patch.object(subaru, "autorizza", autorizza_finto),
            patch.object(subaru.ClienteSubaru, "accedi", AsyncMock()),
            patch.object(subaru.ClienteSubaru, "vetture", AsyncMock(return_value=[auto])),
            patch.object(subaru.ClienteSubaru, "stato", AsyncMock(return_value=stato)),
        ):
            dati = {"email": "Mario@Esempio.com", "password": "segreta", "paese": "USA", "pin": "1234"}
            r = await hass.config_entries.flow.async_configure(r["flow_id"], dati)
            assert r["errors"] == {"base": "codice_inviato"}
            r = await hass.config_entries.flow.async_configure(r["flow_id"], {**dati, "codice": "123456"})
            await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Subaru · Mario@Esempio.com"
    assert r["result"].unique_id == "subaru_mario@esempio.com"
    assert r["data"]["marca"] == "subaru"
    assert r["data"]["dispositivo"].isdigit()
    assert "pin" not in r["data"] and r["result"].options["pin"] == "1234"
    assert autorizza_finto.await_args.args[1] == "123456"
    assert hass.states.get("sensor.test_subaru_2_batteria").state == "60"
    assert r["result"].runtime_data.auto[0].comandi_possibili == {"chiudi", "apri", "trova", "clima", "ricarica"}
