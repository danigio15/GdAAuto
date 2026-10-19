"""smart: lo stato del cloud Geely, il cliente contro un cloud finto, e la configurazione in Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import CONF_MARCA, DOMAIN
from custom_components.gdaauto.marche import smart
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.smart import cliente as modulo_cliente
from custom_components.gdaauto.marche.smart.cliente import ClienteSmart, modello_di, token_da
from custom_components.gdaauto.marche.smart.dati import interpreta, limite_da_soc, potenza_dc_smart5, ultimi_7_giorni
from custom_components.gdaauto.vettura import StatoRicarica

from .smart_campioni import AUTO, STATO

VIN = AUTO["vin"]
SEGRETO = b"74e7469aff5042bbbed7bbb1b36c15e9"


# --- lo stato ---------------------------------------------------------------------------


def test_lo_stato_di_una_1_parcheggiata() -> None:
    s = interpreta(STATO)
    assert s.batteria == 47
    assert s.autonomia_km == 128
    assert s.odometro_km == 500
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.tempo_ricarica_min is None  # 2047 vuol dire «non in carica»
    assert s.potenza_ricarica_diretta_kw == 0.0
    assert s.chiusa is True  # centralLockingStatus 2
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is False
    assert s.freno_stazionamento is True
    assert s.temperatura_interna_c == 6.6
    assert s.temperatura_esterna_c == 6.4
    assert s.clima_acceso is False
    assert s.sbrinamento is False
    assert s.quadro_acceso is False
    assert s.velocita_kmh == 0.0
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 241.6,
        "anteriore_destro": 244.4,
        "posteriore_sinistro": 240.3,
        "posteriore_destro": 237.5,
    }
    assert s.pneumatici_allarme is False
    assert (s.latitudine, s.longitudine) == (45.46, 9.19)
    assert s.posizione_con_segno is True
    assert s.letto == datetime(2024, 1, 23, 16, 44, tzinfo=UTC)


def _in_carica(**ev: str) -> dict[str, Any]:
    stato = copy.deepcopy(STATO)
    stato["additionalVehicleStatus"]["electricVehicleStatus"].update(ev)
    return stato


def test_una_ricarica_trifase() -> None:
    s = interpreta(
        _in_carica(
            chargerState="2",
            statusOfChargerConnection="3",
            chargeUAct="230.0",
            chargeIAct="16.0",
            timeToFullyCharged="95",
        )
    )
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.ricarica_rapida is False
    assert s.tempo_ricarica_min == 95
    assert s.potenza_ricarica_diretta_kw == 3.68  # monofase: 230 V × 16 A
    s = interpreta(_in_carica(chargerState="2", chargeUAct="400.0", chargeIAct="16.0"))
    assert s.potenza_ricarica_diretta_kw == 11.09  # trifase: 400 V × 16 A × √3


def test_la_ricarica_dc_e_quella_finita() -> None:
    dc = interpreta(_in_carica(chargerState="15", statusOfChargerConnection="1", dcChargeIAct="-150"))
    assert (dc.stato_ricarica, dc.ricarica_rapida, dc.cavo_collegato) == (StatoRicarica.IN_CARICA, True, True)
    assert dc.potenza_ricarica_diretta_kw is None  # la tensione DC della #1 non si sa
    finita = interpreta(_in_carica(chargerState="11", statusOfChargerConnection="2"))
    assert (finita.stato_ricarica, finita.cavo_collegato) == (StatoRicarica.COMPLETATA, True)
    attesa = interpreta(_in_carica(chargerState="10", statusOfChargerConnection="2"))
    assert attesa.stato_ricarica == StatoRicarica.PROGRAMMATA
    assert potenza_dc_smart5({"chargeI": "17880", "chargeU": "1600"}) == (150.0, 400.0)


def test_porte_aperte_e_auto_aperta() -> None:
    stato = copy.deepcopy(STATO)
    sic = stato["additionalVehicleStatus"]["drivingSafetyStatus"]
    sic.update(centralLockingStatus="0", doorOpenStatusPassengerRear="1", trunkOpenStatus="1")
    stato["additionalVehicleStatus"]["climateStatus"]["winPosDriver"] = "40"
    stato["additionalVehicleStatus"]["maintenanceStatus"]["tyrePreWarningDriver"] = "1"
    stato["basicVehicleStatus"].update(engineStatus="engine_running", speed="48.5", speedValidity="true")
    s = interpreta(stato)
    assert (s.chiusa, s.portiere_aperte, s.bagagliaio_aperto) == (False, True, True)
    assert s.finestrini_aperti is True
    assert s.pneumatici_allarme is True
    assert (s.quadro_acceso, s.velocita_kmh, s.in_marcia) == (True, 48.5, True)


def test_un_cloud_che_manda_poco() -> None:
    s = interpreta({"additionalVehicleStatus": {"electricVehicleStatus": {"chargeLevel": "80"}}})
    assert s.batteria == 80
    assert s.chiusa is None and s.latitudine is None and s.pneumatici_kpa == {}
    assert s.stato_ricarica is None and s.potenza_ricarica_diretta_kw is None


def test_limite_diario_e_modelli() -> None:
    assert limite_da_soc("800") == 80
    assert limite_da_soc(None) is None
    ora = datetime(2026, 10, 18, 12, tzinfo=UTC)
    viaggi = [
        {
            "startTime": int((ora - timedelta(days=1)).timestamp() * 1000),
            "traveledDistance": 15.4,
            "electricConsumption": 20.8,
        },
        {
            "startTime": int((ora - timedelta(days=2)).timestamp() * 1000),
            "traveledDistance": 7.1,
            "electricConsumption": 21.1,
        },
        {
            "startTime": int((ora - timedelta(days=9)).timestamp() * 1000),
            "traveledDistance": 99,
            "electricConsumption": 20,
        },
    ]
    assert ultimi_7_giorni(viaggi, ora) == {"km_7_giorni": 22.5, "energia_7_giorni_kwh": 4.70}
    assert (modello_di("HX11"), modello_di("HC1H"), modello_di("HY11")) == ("#1", "#3", "#5")


def test_il_token_dall_indirizzo_incollato() -> None:
    assert token_da("https://app.id.smart.com/x#access_token=AAA&refresh_token=RRR&expires_in=3") == ("AAA", "RRR")
    assert token_da("smart://login?access_token=BBB") == ("BBB", None)
    assert token_da("  CCC ") == ("CCC", None)
    with pytest.raises(ErroreCredenziali):
        token_da("https://app.id.smart.com/proxy?context=x")


# --- il cloud finto --------------------------------------------------------------------


def _firma_attesa(r: web.Request, corpo: str) -> str:
    """La firma calcolata dal cloud finto, da capo, su quello che arriva davvero."""
    md5 = base64.b64encode(hashlib.md5(corpo.encode()).digest()).decode()
    testo = (
        "application/json;responseformat=3\n"
        f"x-api-signature-nonce:{r.headers['x-api-signature-nonce']}\n"
        "x-api-signature-version:1.0\n\n"
        f"{r.rel_url.raw_query_string}\n{md5}\n{r.headers['x-timestamp']}\n{r.method}\n{r.path}"
    )
    return base64.b64encode(hmac.new(SEGRETO, testo.encode(), hashlib.sha1).digest()).decode()


@dataclass
class FintoSmart:
    oauth_validi: set[str] = field(default_factory=lambda: {"OAUTH1"})
    api_validi: set[str] = field(default_factory=set)
    sessioni: int = 0
    scambi: int = 0
    attiva: str | None = None
    comandi: list[dict[str, Any]] = field(default_factory=list)
    guasto: bool = False
    url: str = ""
    rifiuta_comandi: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/login-app/api/v1/authorize", self._inizio)
        app.router.add_get("/oidc/authorize", self._oidc)
        app.router.add_route("*", "/{resto:.*}", self._api)
        return app

    async def _inizio(self, r: web.Request) -> web.Response:
        assert r.headers["x-requested-with"] == "com.smart.hellosmart"
        raise web.HTTPFound(f"{self.url}/oidc/authorize?client_id=x")

    async def _oidc(self, _r: web.Request) -> web.Response:
        raise web.HTTPFound("https://app.id.smart.com/proxy?context=CONTESTO&lang=it")

    def _nuova_sessione(self) -> dict[str, Any]:
        self.sessioni += 1
        token = f"API{self.sessioni}"
        self.api_validi = {token}
        return {
            "code": 1000,
            "data": {
                "accessToken": token,
                "refreshToken": f"REF{self.sessioni}",
                "userId": "112233",
                "clientId": "CLIENTE",
                "expiresIn": 4579200,
            },
            "success": True,
        }

    async def _api(self, r: web.Request) -> web.Response:
        corpo = await r.text()
        if self.guasto:
            return web.Response(status=503, text="giù")
        assert r.headers["x-signature"] == _firma_attesa(r, corpo), "firma sbagliata"
        assert r.headers["x-app-id"] == "SmartAPPEU"
        percorso = r.path
        dati = json.loads(corpo) if corpo else {}
        if percorso == "/auth/account/session/secure" and r.method == "POST":
            assert r.query_string == "identity_type=smart"
            if dati["accessToken"] == "OAUTH_SCADUTO":
                return web.json_response({"code": "1501", "message": "token expired", "success": False})
            if dati["accessToken"] not in self.oauth_validi:
                return web.json_response({"code": "1401", "message": "token invalid", "success": False})
            return web.json_response(self._nuova_sessione())
        if percorso == "/auth/account/session/secure" and r.method == "PUT":
            assert r.headers["X-CLIENT-ID"] == "CLIENTE"
            if dati["refreshToken"] != f"REF{self.sessioni}":
                return web.json_response({"code": "1400", "message": "Illegal parameter", "success": False})
            self.scambi += 1
            nuovo = f"OAUTH_NUOVO{self.scambi}"
            self.oauth_validi = {nuovo}
            return web.json_response({"code": 1000, "data": {"accessToken": nuovo}, "success": True})
        if r.headers.get("authorization") not in self.api_validi:
            return web.json_response({"code": "1402", "message": "token expired", "success": False})
        if percorso == "/device-platform/user/vehicle/secure":
            assert r.query_string == "needSharedCar=1&userId=112233"
            return web.json_response({"code": "1000", "data": {"list": [AUTO]}, "success": True})
        if percorso == f"/remote-control/vehicle/status/{VIN}":
            assert r.rel_url.raw_query_string == "latest=True&target=basic%2Cmore&userId=112233"
            assert r.headers["X-Vehicle-IDENTIFIER"] == VIN
            return web.json_response({"code": "1000", "data": {"vehicleStatus": STATO}, "success": True})
        if percorso == f"/remote-control/vehicle/status/soc/{VIN}":
            return web.json_response({"code": "1000", "data": {"soc": "800"}, "success": True})
        if percorso == "/remote-control/user/authorization/insert":
            return web.json_response({"code": "1000", "success": True})
        if percorso == f"/geelyTCAccess/tcservices/vehicle/status/journalLogV4/{VIN}":
            ora = int(time.time() * 1000)
            viaggio = {"startTime": ora - 3_600_000, "traveledDistance": 12.0, "electricConsumption": 18.0}
            return web.json_response({"code": "1000", "data": {"list": [viaggio]}, "success": True})
        if percorso == "/device-platform/user/session/update":
            assert "X-Vehicle-IDENTIFIER" not in r.headers
            self.attiva = dati["vin"]
            return web.json_response({"code": "1000", "success": True})
        if percorso == f"/remote-control/vehicle/telematics/{VIN}":
            if self.attiva != VIN:
                return web.json_response({"code": "8006", "message": "relationship does not exist"})
            self.comandi.append(dati)
            if self.rifiuta_comandi:
                return web.json_response({"code": "1000", "success": False, "message": "vehicle offline"})
            return web.json_response({"code": "1000", "success": True})
        if percorso == "/geelyTCAccess/tcservices/ihu/send/to/car":
            assert r.query_string == f"vin={VIN}"
            self.comandi.append(dati)
            return web.json_response({"code": "1000", "success": True})
        return web.json_response({"code": "9999", "message": "sconosciuto"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoSmart, str]]:
    finto = FintoSmart()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    finto.url = url
    yield finto, url
    await runner.cleanup()


def _indirizzi(url: str) -> dict[str, str]:
    return {"login": f"{url}/login-app/api/v1/authorize?uiLocales=it-IT", "api": url, "api_v2": url}


async def test_dal_token_del_browser_allo_stato(cloud) -> None:
    _finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, indirizzi=_indirizzi(url))
        assert await c.link_login() == "https://app.id.smart.com/proxy?context=CONTESTO&lang=it"
        token = await c.apri_sessione("OAUTH1")
        assert (token["api_token"], token["utente"], token["cliente"]) == ("API1", "112233", "CLIENTE")
        (v,) = await c.vetture()
        assert (v.vin, v.modello, v.titolo, v.colore) == (VIN, "#1", "smart #1", "G68 SPECTRUM BLUE")
        stato = await c.stato(v)
    assert stato.batteria == 47
    assert stato.limite_ricarica == 80
    assert (stato.km_7_giorni, stato.energia_7_giorni_kwh) == (12.0, 2.16)


async def test_la_sessione_scaduta_si_rifa_col_token_oauth(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, salvati.append, indirizzi=_indirizzi(url))
        await c.apri_sessione("OAUTH1")
        finto.api_validi = set()  # il cloud fa scadere la sessione (1402)
        (v,) = await c.vetture()
    assert v.vin == VIN
    assert finto.sessioni == 2 and finto.scambi == 0
    assert salvati[-1]["api_token"] == "API2"


async def test_il_token_oauth_scaduto_si_rinnova_col_refresh(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, salvati.append, indirizzi=_indirizzi(url))
        await c.apri_sessione("OAUTH1")
        c.token["oauth"] = "OAUTH_SCADUTO"
        c.token["scade"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await c.vetture()
    assert finto.scambi == 1
    assert salvati[-1]["oauth"] == "OAUTH_NUOVO1"
    assert salvati[-1]["api_token"] == "API2"


async def test_un_refresh_rifiutato_vuole_il_browser(cloud) -> None:
    _finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, indirizzi=_indirizzi(url))
        await c.apri_sessione("OAUTH1")
        c.token.update(oauth="OAUTH_SCADUTO", api_refresh="VECCHIO", scade="2000-01-01T00:00:00+00:00")
        with pytest.raises(ErroreCredenziali):
            await c.vetture()


async def test_un_token_sbagliato_e_un_guasto_non_si_confondono(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, indirizzi=_indirizzi(url))
        with pytest.raises(ErroreCredenziali):
            await c.apri_sessione("SBAGLIATO")
        finto.guasto = True
        with pytest.raises(ErroreRete):
            await c.apri_sessione("OAUTH1")
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, indirizzi=_indirizzi("http://127.0.0.1:1"))
        with pytest.raises(ErroreRete):
            await c.apri_sessione("OAUTH1")


async def test_i_comandi(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, indirizzi=_indirizzi(url))
        await c.apri_sessione("OAUTH1")
        (v,) = await c.vetture()
        await c.chiudi(v, None)
        await c.apri(v, None)
        await c.trova(v, None)
        await c.clima(v, True, None, 21)
        await c.ricarica(v, False, None)
        await c.limite_ricarica(v, 82, None)
        await c.destinazione(v, "Casa", 45.46, 9.19, "Piazza del Duomo")
        with pytest.raises(ErroreComando):
            await c.limite_ricarica(v, 30, None)
        finto.rifiuta_comandi = True
        with pytest.raises(ErroreComando, match="offline"):
            await c.chiudi(v, None)
    chiudi, apri, trova, clima, ricarica, limite, meta, _rifiutato = finto.comandi
    assert (chiudi["serviceId"], chiudi["serviceParameters"]) == ("RDL_2", [{"key": "door", "value": "all"}])
    assert apri["serviceId"] == "RDU_2"
    assert trova["serviceParameters"] == [{"key": "rhl", "value": "light-flash"}]
    assert clima["serviceId"] == "RCE_2" and {"key": "rce.temp", "value": "21.0"} in clima["serviceParameters"]
    assert ricarica["serviceParameters"] == [{"key": "operation", "value": "0"}, {"key": "rcs.terminate", "value": "1"}]
    assert {"key": "soc", "value": "800"} in limite["serviceParameters"]  # 82 → 80, in decimi
    assert meta == {"lat": 45.46, "lon": 9.19, "name": "Casa", "address": "Piazza del Duomo"}


async def test_il_cloud_che_perde_l_auto_attiva(cloud) -> None:
    """Se l'app sul telefono sceglie un'altra auto, il comando la risceglie e riprova."""
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, indirizzi=_indirizzi(url))
        await c.apri_sessione("OAUTH1")
        (v,) = await c.vetture()
        primo = c.scegli_auto

        async def sceglie_un_altra(vin: str) -> None:
            await primo(vin)
            finto.attiva = "ALTRA"
            c.scegli_auto = primo  # type: ignore[method-assign]

        c.scegli_auto = sceglie_un_altra  # type: ignore[method-assign]
        await c.chiudi(v, None)
    assert finto.attiva == VIN and len(finto.comandi) == 1


async def test_il_clima_della_5_usa_zaf(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = ClienteSmart(s, {}, indirizzi=_indirizzi(url))
        await c.apri_sessione("OAUTH1")
        (v,) = await c.vetture()
        c._auto[VIN]["seriesCodeVs"] = "HY11"
        await c.clima(v, False, None)
    (zaf,) = finto.comandi
    assert zaf["serviceId"] == "ZAF" and zaf["command"] == "start"
    assert {"key": "AC", "value": "false"} in zaf["serviceParameters"]


# --- dentro Home Assistant -------------------------------------------------------------


MARCHIO = Marchio("smart", "smart", "smart", {}, "Hello smart (HACS)", "https://github.com/DasBasti/SmartHashtag")


async def test_configurare_smart_dal_catalogo(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    with patch.dict(PER_ID, {"smart": MARCHIO}), patch.dict(modulo_cliente.INDIRIZZI, _indirizzi(url)):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "smart"})
        assert r["step_id"] == "browser"
        assert r["description_placeholders"]["link"] == "https://app.id.smart.com/proxy?context=CONTESTO&lang=it"
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": "https://app.id.smart.com/proxy?context=x"}
        )
        assert r["errors"] == {"base": "codice"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": "https://smart.example/fine#access_token=OAUTH1&refresh_token=R"}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == "smart · 112233"
        assert r["result"].unique_id == "smart_112233"
        assert r["data"][CONF_MARCA] == "smart"
        assert r["data"]["token"]["oauth"] == "OAUTH1"
        assert hass.states.get("sensor.smart_1_batteria").state == "47"
        assert hass.states.get("lock.smart_1_serratura").state == "locked"
    assert smart.comandi_di(r["result"].runtime_data.auto[0].vettura) >= {"chiudi", "clima"}
    assert finto.sessioni == 1  # il setup riusa la sessione della configurazione
