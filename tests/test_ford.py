"""Ford: la telemetria di FordPass, il cliente contro un cloud finto, e la configurazione in Home Assistant."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import ford
from custom_components.gdaauto.marche.base import Dispositivo
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.ford import cliente as cliente_ford
from custom_components.gdaauto.marche.ford.cliente import (
    ClienteFord,
    Indirizzi,
    codice_da,
    comandi_di,
    indirizzo_login,
    sfida,
    vettura_da_garage,
)
from custom_components.gdaauto.marche.ford.dati import interpreta
from custom_components.gdaauto.marche.ford.regioni import CLIENT_ID, OAUTH_ID, REGIONI, regione_di
from custom_components.gdaauto.vettura import Marcia, StatoRicarica, Vettura

from .ford_campioni import GARAGE, KUGA, MACH_E, ORARI_RICARICA, PROFILO_CLIMA, ULTIME_RICARICHE, VIN

ADESSO = datetime(2026, 10, 18, 10, 6, tzinfo=UTC)
LENTI = {"etl": ULTIME_RICARICHE, "rcc": PROFILO_CLIMA, "pct": ORARI_RICARICA}


def jwt(**corpo: Any) -> str:
    return "x." + base64.urlsafe_b64encode(json.dumps(corpo).encode()).decode().rstrip("=") + ".y"


# --- l'interpretazione -----------------------------------------------------------------


def test_una_mach_e_in_carica() -> None:
    s = interpreta(MACH_E, LENTI, ADESSO)
    assert s.batteria == 64.5
    assert s.autonomia_km == 287.5
    assert s.energia_residua_kwh == 56.8
    assert s.capacita_batteria_kwh == 88.0
    assert s.temperatura_batteria_c == 18.0
    assert s.tensione_v == 372.0
    assert s.odometro_km == 23456.7
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.ricarica_rapida is False
    assert s.potenza_ricarica_kw == 10.99  # 370 V × 29,7 A
    assert s.tempo_ricarica_min == 95
    # Il limite dell'auto vince su quello del luogo (85).
    assert s.limite_ricarica == 90
    assert s.velocita_kmh == 0.0
    assert s.marcia == Marcia.PARCHEGGIO
    assert s.quadro_acceso is False
    assert s.freno_stazionamento is True
    assert s.temperatura_esterna_c == 14.5
    assert s.clima_acceso is False
    assert s.temperatura_clima_c == 21.5
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is True
    assert s.finestrini_aperti is False
    assert s.pneumatici_kpa == {
        "anteriore_sinistro": 262.0,
        "anteriore_destro": 260.0,
        "posteriore_sinistro": 255.0,
        "posteriore_destro": 258.0,
    }
    assert s.pneumatici_allarme is False
    assert s.antifurto is True
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (45.4642, 9.19, True)
    assert s.in_sonno is False
    assert s.letto == datetime(2026, 10, 18, 10, 5, tzinfo=UTC)
    assert s.ultima_ricarica_kwh == 60.2
    assert s.ultima_ricarica_tipo == "ac"
    assert s.ultima_ricarica_inizio == datetime(2026, 10, 16, 16, 5, 9, tzinfo=UTC)
    assert s.ultima_ricarica_fine == datetime(2026, 10, 17, 4, 33, 5, tzinfo=UTC)
    # La batteria a 12 V non ha un campo suo: resta nei dati grezzi.
    assert s.grezzo["telemetria"]["metrics"]["batteryVoltage"]["value"] == 12.6


def test_una_kuga_plug_in_in_marcia() -> None:
    s = interpreta(KUGA, {}, ADESSO)
    assert s.batteria == 40
    assert s.carburante_percento == 55.2
    assert s.autonomia_carburante_km == 410.0
    assert s.autonomia_totale_km == 432.0
    assert s.cavo_collegato is False
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.potenza_ricarica_kw == 0.0
    assert s.velocita_kmh == 50.0  # 13,9 m/s
    assert s.marcia == Marcia.AVANTI
    assert s.in_marcia is True
    assert s.quadro_acceso is True
    assert s.chiusa is False  # una serratura su due
    assert s.portiere_aperte is True
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is True
    assert s.pneumatici_allarme is True
    # L'avvio a distanza è partito due minuti fa per quindici: il clima è acceso.
    assert s.clima_acceso is True
    assert s.in_sonno is True
    assert s.limite_ricarica is None
    assert s.latitudine is None


def test_la_ricarica_finita_al_limite() -> None:
    dati = json.loads(json.dumps(MACH_E))
    m = dati["metrics"]
    m["xevBatteryChargeDisplayStatus"]["value"] = "STOPPED"
    m["xevBatteryStateOfCharge"]["value"] = 90
    m["xevChargeStationPowerType"]["value"] = "DC_FAST"
    s = interpreta(dati, {}, ADESSO)
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.ricarica_rapida is True
    assert s.tempo_ricarica_min is None


def test_il_codice_dall_indirizzo_o_da_solo() -> None:
    assert codice_da("fordapp://userauthorized/?code=eyJraWQi.ABC-123") == "eyJraWQi.ABC-123"
    assert codice_da("fordapp://userauthorized?code=XYZ&state=s") == "XYZ"
    assert codice_da("  'XYZ'  ") == "XYZ"
    with pytest.raises(ErroreCredenziali):
        codice_da("fordapp://userauthorized/?error=access_denied&error_description=annullato")
    with pytest.raises(ErroreCredenziali):
        codice_da("fordapp://userauthorized/?state=s")


def test_il_link_del_login() -> None:
    link = indirizzo_login(REGIONI["ita"], "VERIFIER")
    assert link.startswith(f"https://login.ford.com/{OAUTH_ID}/B2C_1A_SignInSignUp_it-IT/oauth2/v2.0/authorize?")
    assert "redirect_uri=fordapp%3A%2F%2Fuserauthorized" in link
    assert f"code_challenge={sfida('VERIFIER')}" in link
    assert "code_challenge_method=S256" in link
    assert f"client_id={CLIENT_ID}" in link
    assert "country_code=ITA" in link
    assert regione_di("deu").login == "https://login.ford.de"
    assert regione_di("sconosciuto").paese == "ITA"
    assert regione_di("usa", "lincoln").schema == "lincolnapp"


def test_il_garage_e_i_comandi() -> None:
    v = vettura_da_garage(GARAGE[0])
    assert v is not None
    assert (v.vin, v.modello, v.nome, v.anno) == (VIN, "Mustang Mach-E", "Saetta", 2023)
    assert comandi_di(v) == {"chiudi", "trova", "clima", "ricarica", "limite_ricarica"}
    # Una Fiesta a benzina non è un'auto per GdAAuto.
    assert vettura_da_garage(GARAGE[1]) is None
    kuga = Vettura("K", "ford", "Kuga", extra={"motore": "PHEV", "capacita": {"remoteLock": "Display"}})
    assert comandi_di(kuga) == {"chiudi"}


# --- il cliente contro un cloud finto -------------------------------------------------------


@dataclass
class FintoFord:
    validi: set[str] = field(default_factory=lambda: {"CAT1"})
    auto_validi: set[str] = field(default_factory=set)
    refresh_atteso: str = "RT1"
    ruotati: int = 0
    scambi_auto: int = 0
    ws: str = "dati"  # «dati», «vuoto»
    guasto_rinnovo: int = 0
    esito_comando: str = "SUCCESS"
    comandi: list[dict[str, Any]] = field(default_factory=list)
    clima: list[dict[str, Any]] = field(default_factory=list)
    aperti: list[web.WebSocketResponse] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/login/{resto:.*}", self._login)
        app.router.add_post("/fondazionale/token/v2/cat-with-b2c-access-token", self._cat)
        app.router.add_post("/fondazionale/token/v2/cat-with-refresh-token", self._rinnovo)
        app.router.add_post("/account/auth/oidc/token", self._auto)
        app.router.add_get("/veicolo/fpcpl-user-garage-service/v1/user/garage", self._garage)
        app.router.add_get("/veicolo/electrification/experiences/v2/devices/energy-transfer-logs", self._etl)
        app.router.add_get("/veicolo/electrification/experiences/v2/vehicles/preferred-charge-times", self._pct)
        app.router.add_post("/veicolo/rcc/profile/status", self._rcc)
        app.router.add_put("/veicolo/rcc/profile/update", self._rcc_cambia)
        app.router.add_get("/v1beta/telemetry/sources/fordpass/vehicles/{vin}/ws", self._websocket)
        app.router.add_get("/v1/telemetry/sources/fordpass/vehicles/{vin}", self._rest)
        app.router.add_post("/{base:v1|v1beta}/command/vehicles/{vin}/commands", self._comando)
        return app

    async def _login(self, r: web.Request) -> web.Response:
        f = await r.post()
        assert r.match_info["resto"] == f"{OAUTH_ID}/B2C_1A_SignInSignUp_it-IT/oauth2/v2.0/token"
        assert f["client_id"] == CLIENT_ID and f["redirect_uri"] == "fordapp://userauthorized"
        if f["code"] != "CODICE" or sfida(str(f["code_verifier"])) != sfida("VERIFIER"):
            return web.json_response(
                {"error": "invalid_grant", "error_description": "AADB2C90080: The provided grant has expired."},
                status=400,
            )
        return web.json_response(
            {"access_token": "B2C", "id_token": jwt(sub="utente-ford-1", email="mario@esempio.it"), "expires_in": 300}
        )

    async def _cat(self, r: web.Request) -> web.Response:
        assert r.headers["Application-Id"] == REGIONI["ita"].app_id
        assert (await r.json()) == {"idpToken": "B2C"}
        return web.json_response({"access_token": "CAT1", "refresh_token": "RT1", "expires_in": 300})

    async def _rinnovo(self, r: web.Request) -> web.Response:
        if self.guasto_rinnovo:
            return web.json_response({"message": "Service Unavailable"}, status=503)
        if (await r.json())["refresh_token"] != self.refresh_atteso:
            return web.json_response({"message": "Invalid or Expired Token", "errorCode": "460"}, status=400)
        self.ruotati += 1
        self.refresh_atteso = f"RT{self.ruotati + 1}"
        nuovo = f"CAT{self.ruotati + 1}"
        self.validi = {nuovo}
        return web.json_response({"access_token": nuovo, "refresh_token": self.refresh_atteso, "expires_in": 300})

    async def _auto(self, r: web.Request) -> web.Response:
        f = await r.post()
        assert f["subject_issuer"] == "fordpass" and f["client_id"] == "fordpass-prod"
        if f["subject_token"] != self.refresh_atteso:
            return web.json_response({"error": "invalid_grant"}, status=401)
        self.scambi_auto += 1
        nuovo = f"AUTO{self.scambi_auto}"
        self.auto_validi = {nuovo}
        return web.json_response({"access_token": nuovo, "refresh_token": "x", "expires_in": 300})

    def _ford_ok(self, r: web.Request) -> bool:
        return r.headers.get("auth-token") in self.validi

    async def _garage(self, r: web.Request) -> web.Response:
        if not self._ford_ok(r):
            return web.json_response({"message": "unauthorized"}, status=401)
        assert r.headers["countryCode"] == "ITA" and r.headers["locale"] == "it-IT"
        return web.json_response(GARAGE, status=207)

    async def _etl(self, r: web.Request) -> web.Response:
        assert self._ford_ok(r) and r.headers["deviceId"] == VIN
        return web.json_response(ULTIME_RICARICHE)

    async def _pct(self, r: web.Request) -> web.Response:
        return web.json_response(ORARI_RICARICA)

    async def _rcc(self, r: web.Request) -> web.Response:
        return web.json_response(PROFILO_CLIMA)

    async def _rcc_cambia(self, r: web.Request) -> web.Response:
        self.clima.append(await r.json())
        return web.json_response({"status": 200})

    def _auto_ok(self, r: web.Request) -> bool:
        return r.headers.get("Authorization", "").removeprefix("Bearer ") in self.auto_validi

    async def _websocket(self, r: web.Request) -> web.StreamResponse:
        if not self._auto_ok(r):
            return web.json_response({"message": "unauthorized"}, status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(r)
        if self.ws == "vuoto":
            await ws.close()
            return ws
        self.aperti.append(ws)
        await ws.send_json({"_httpStatus": 200})
        # L'istantanea arriva in due pezzi, come capita.
        metriche = MACH_E["metrics"]
        prima = {k: v for k, v in list(metriche.items())[:10]}
        dopo = {k: v for k, v in list(metriche.items())[10:]}
        await ws.send_json({"_data": {"metrics": prima, "updateTime": MACH_E["updateTime"]}})
        await ws.send_json({"_data": {"metrics": dopo, "states": MACH_E["states"], "events": MACH_E["events"]}})
        async for _msg in ws:
            pass
        self.aperti.remove(ws)
        return ws

    async def _rest(self, r: web.Request) -> web.Response:
        if not self._auto_ok(r):
            return web.json_response({"message": "unauthorized"}, status=401)
        assert r.query["lrdt"] == "01-01-1970 00:00:00"
        return web.json_response(MACH_E)

    async def _comando(self, r: web.Request) -> web.Response:
        if not self._auto_ok(r):
            return web.json_response({"message": "unauthorized"}, status=401)
        corpo = await r.json()
        self.comandi.append({"base": r.match_info["base"], **corpo})
        id_ = f"cmd-{len(self.comandi)}"
        chiave = corpo["type"] if corpo["type"].endswith("Command") else f"{corpo['type']}Command"

        async def esito() -> None:
            await asyncio.sleep(0.05)
            for verso in ("REQUEST_QUEUED", self.esito_comando):
                valore: dict[str, Any] = {"toState": verso}
                if verso == "COMMAND_FAILED_ON_DEVICE":
                    valore["data"] = {"commandError": {"commandExecutionFailure": {"oemErrorContext": "PORTA_APERTA"}}}
                for ws in list(self.aperti):
                    stati = {"commands": {chiave: {"commandId": id_, "value": valore}}}
                    await ws.send_json({"_data": {"states": stati}})

        asyncio.get_running_loop().create_task(esito())
        return web.json_response({"id": id_}, status=202)


@pytest.fixture
async def finto(socket_enabled: None, monkeypatch) -> AsyncGenerator[tuple[FintoFord, Indirizzi]]:
    monkeypatch.setattr(cliente_ford, "_WS_QUIETE_S", 0.2)
    monkeypatch.setattr(cliente_ford, "_WS_MASSIMO_S", 3.0)
    monkeypatch.setattr(cliente_ford, "_COMANDO_MASSIMO_S", 3.0)
    f = FintoFord()
    runner = web.AppRunner(f.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    yield (
        f,
        Indirizzi(
            fondazionale=f"{base}/fondazionale",
            veicolo=f"{base}/veicolo",
            autonomic=f"{base}/v1",
            autonomic_beta=f"{base}/v1beta",
            autonomic_ws=f"{base.replace('http', 'ws')}/v1beta",
            account=f"{base}/account",
            login=f"{base}/login",
        ),
    )
    await runner.cleanup()


def _token(scade_fra: timedelta = timedelta(minutes=4), **extra: Any) -> dict[str, Any]:
    return {
        "access_token": "CAT1",
        "refresh_token": "RT1",
        "scade": (datetime.now(UTC) + scade_fra).isoformat(),
        **extra,
    }


SAETTA = vettura_da_garage(GARAGE[0])


async def test_dal_codice_allo_stato(finto) -> None:
    f, url = finto
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = ClienteFord(s, REGIONI["ita"], {}, salvati.append, indirizzi=url)
        token = await c.scambia_codice("CODICE", "VERIFIER")
        assert token["access_token"] == "CAT1" and token["refresh_token"] == "RT1"
        assert token["utente"] == {"id": "utente-ford-1", "email": "mario@esempio.it"}
        (v,) = await c.vetture()
        assert v.titolo == "Saetta"
        stato = await c.stato(v)
    assert stato.batteria == 64.5
    assert stato.chiusa is True
    assert stato.ultima_ricarica_kwh == 60.2  # i dati lenti
    assert stato.temperatura_clima_c == 21.5
    assert f.scambi_auto == 1
    assert salvati == []  # lo scambio del codice lo salva la configurazione


async def test_il_codice_sbagliato(finto) -> None:
    _, url = finto
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await ClienteFord(s, REGIONI["ita"], {}, indirizzi=url).scambia_codice("VECCHIO", "VERIFIER")


async def test_il_token_scaduto_ruota_e_si_salva(finto) -> None:
    f, url = finto
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = ClienteFord(
            s, REGIONI["ita"], _token(-timedelta(minutes=1), utente={"id": "u"}), salvati.append, indirizzi=url
        )
        await c.vetture()
        # Il token della telemetria si scambia col refresh token nuovo.
        await c.stato(SAETTA)
    assert f.ruotati == 1
    assert salvati[-1]["refresh_token"] == "RT2" and salvati[-1]["access_token"] == "CAT2"
    assert salvati[-1]["utente"] == {"id": "u"}


async def test_un_401_rinnova_e_riprova(finto) -> None:
    f, url = finto
    f.validi = {"ALTRO"}  # il token salvato non vale più, anche se non è scaduto
    async with aiohttp.ClientSession() as s:
        c = ClienteFord(s, REGIONI["ita"], _token(), indirizzi=url)
        assert len(await c.vetture()) == 1
    assert f.ruotati == 1


async def test_il_refresh_token_rifiutato_chiede_di_rientrare(finto) -> None:
    f, url = finto
    f.refresh_atteso = "ALTRO"
    async with aiohttp.ClientSession() as s:
        c = ClienteFord(s, REGIONI["ita"], _token(-timedelta(minutes=1)), indirizzi=url)
        with pytest.raises(ErroreCredenziali):
            await c.vetture()


async def test_un_guasto_non_chiede_di_rientrare(finto) -> None:
    f, url = finto
    f.guasto_rinnovo = 1
    async with aiohttp.ClientSession() as s:
        c = ClienteFord(s, REGIONI["ita"], _token(-timedelta(minutes=1)), indirizzi=url)
        with pytest.raises(ErroreRete):
            await c.vetture()
        # E un cloud che non c'è è rete, non credenziali.
        spento = ClienteFord(s, REGIONI["ita"], _token(), indirizzi=Indirizzi(veicolo="http://127.0.0.1:9"))
        with pytest.raises(ErroreRete):
            await spento.vetture()


async def test_senza_websocket_si_legge_dal_rest(finto) -> None:
    f, url = finto
    f.ws = "vuoto"
    async with aiohttp.ClientSession() as s:
        stato = await ClienteFord(s, REGIONI["ita"], _token(), indirizzi=url).stato(SAETTA)
    assert stato.batteria == 64.5
    assert stato.odometro_km == 23456.7


async def test_il_token_della_telemetria_rifiutato_si_rifà(finto) -> None:
    f, url = finto
    async with aiohttp.ClientSession() as s:
        c = ClienteFord(s, REGIONI["ita"], _token(), indirizzi=url)
        await c.stato(SAETTA)
        f.auto_validi = {"REVOCATO"}  # il cloud ha buttato il token di Autonomic
        stato = await c.stato(SAETTA)
    assert stato.batteria == 64.5
    assert f.scambi_auto == 2


async def test_chiudere_e_ricaricare(finto) -> None:
    f, url = finto
    async with aiohttp.ClientSession() as s:
        c = ClienteFord(s, REGIONI["ita"], _token(), indirizzi=url)
        await c.chiudi(SAETTA, None)
        await c.ricarica(SAETTA, True, None)
        await c.limite_ricarica(SAETTA, 75, None)
    lock, carica, limite = f.comandi
    assert (lock["base"], lock["type"], lock["wakeUp"]) == ("v1", "lock", True)
    assert "version" not in lock
    assert (carica["base"], carica["type"], carica["version"]) == ("v1beta", "startGlobalChargeCommand", "1.0.1")
    assert "properties" not in carica
    # Sotto l'80 % Ford vuole le decine.
    assert limite["properties"] == {
        "chargeSettings": {"globalDCTargetSoc": 70, "globalReserveSoc": 70, "globalTargetSoc": 70}
    }


async def test_il_clima_con_la_temperatura(finto) -> None:
    f, url = finto
    async with aiohttp.ClientSession() as s:
        await ClienteFord(s, REGIONI["ita"], _token(), indirizzi=url).clima(SAETTA, True, None, 23)
    (profilo,) = f.clima
    assert {"preferenceType": "SetPointTemp_Rq", "preferenceValue": "23_0"} in profilo["userPreferences"]
    assert profilo["vin"] == VIN
    assert f.comandi[-1]["type"] == "remoteStart"


async def test_il_comando_rifiutato_dall_auto(finto) -> None:
    f, url = finto
    f.esito_comando = "COMMAND_FAILED_ON_DEVICE"
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreComando, match="PORTA_APERTA"):
            await ClienteFord(s, REGIONI["ita"], _token(), indirizzi=url).chiudi(SAETTA, None)


# --- in Home Assistant ----------------------------------------------------------------------

STATO_SAETTA = interpreta(MACH_E, LENTI, ADESSO)
TOKEN = {
    "access_token": "CAT1",
    "refresh_token": "RT1",
    "scade": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    "utente": {"id": "Utente-Ford-1", "email": "mario@esempio.it"},
}


async def _scambia(self: ClienteFord, codice: str, verifier: str) -> dict[str, Any]:
    assert codice == "CODICE"
    assert verifier
    self.token = dict(TOKEN)
    return dict(TOKEN)


async def test_avvia_e_completa_dal_browser(hass: HomeAssistant) -> None:
    d = await ford.avvia_browser(hass, {"paese": "deu"})
    assert d.link.startswith("https://login.ford.de/") and d.codice_utente == ""
    assert d.stato["paese"] == "deu" and d.stato["verifier"]
    with (
        patch.object(ClienteFord, "scambia_codice", _scambia),
        patch.object(ClienteFord, "vetture", AsyncMock(return_value=[SAETTA])),
    ):
        accesso = await ford.completa_browser(hass, {"paese": "deu"}, d, "fordapp://userauthorized/?code=CODICE")
    assert accesso.unique_id == "ford_utente-ford-1"
    assert accesso.titolo == "Ford · mario@esempio.it"
    assert accesso.dati == {"paese": "deu", "token": TOKEN}
    with pytest.raises(ErroreCredenziali):
        await ford.completa_browser(hass, {}, Dispositivo("x", "", {}), "CODICE")


async def test_configurare_ford_dal_browser(hass: HomeAssistant) -> None:
    # Il catalogo punta Ford a questa marca; la prova non dipende da quando lo fa.
    marchio = replace(PER_ID["ford"], nativo="ford")
    with (
        patch.dict(PER_ID, {"ford": marchio}),
        patch.object(ClienteFord, "scambia_codice", _scambia),
        patch.object(ClienteFord, "vetture", AsyncMock(return_value=[SAETTA])),
        patch.object(ClienteFord, "stato", AsyncMock(return_value=STATO_SAETTA)),
    ):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "ford"})
        assert r["step_id"] == "credenziali"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"paese": "ita"})
        assert r["step_id"] == "browser"
        assert r["description_placeholders"]["link"].startswith(f"https://login.ford.com/{OAUTH_ID}/")
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": "fordapp://userauthorized/?code=CODICE"}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == "Ford · mario@esempio.it"
        assert r["data"][CONF_MARCA] == "ford" and r["data"]["paese"] == "ita"
        assert r["result"].unique_id == "ford_utente-ford-1"
        assert hass.states.get("sensor.saetta_batteria").state == "64.5"
        assert hass.states.get("lock.saetta_serratura").state == "locked"
        assert hass.states.get("binary_sensor.saetta_bagagliaio").state == "on"
        assert hass.states.get("number.saetta_limite_ricarica").state == "90"


async def test_il_token_che_ruota_non_ricarica_l_integrazione(hass: HomeAssistant) -> None:
    voce = MockConfigEntry(
        domain=DOMAIN,
        title="Ford · mario@esempio.it",
        unique_id="ford_utente-ford-1",
        data={CONF_FONTE: FONTE_CLOUD, CONF_MARCA: "ford", "paese": "ita", "token": TOKEN},
    )
    voce.add_to_hass(hass)
    with (
        patch.object(ClienteFord, "vetture", AsyncMock(return_value=[SAETTA])),
        patch.object(ClienteFord, "stato", AsyncMock(return_value=STATO_SAETTA)),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        prima = voce.runtime_data
        cliente = prima.conto.cliente
        assert cliente.regione.paese == "ITA"
        cliente._salva({**TOKEN, "access_token": "CAT2", "refresh_token": "RT2"})
        await hass.async_block_till_done()
    assert voce.data["token"]["refresh_token"] == "RT2"
    assert voce.runtime_data is prima
