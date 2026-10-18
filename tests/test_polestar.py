"""Polestar: le risposte GraphQL, il cliente contro un cloud finto, e Home Assistant."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any
from unittest.mock import patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gdaauto.const import CONF_FONTE, CONF_MARCA, DOMAIN, FONTE_CLOUD
from custom_components.gdaauto.marche import MARCHE, polestar
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.polestar import cliente as modulo_cliente
from custom_components.gdaauto.marche.polestar.cliente import CLIENT_ID, REDIRECT_URI, ClientePolestar
from custom_components.gdaauto.marche.polestar.dati import interpreta, modello, stato_ricarica, vettura_da
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from .polestar_campioni import IN_CARICA_V1, IN_CARICA_V2, POLESTAR2, POLESTAR3, POLESTAR4, VIN3, VIN_ALTRA

EMAIL = "Guido@Esempio.it"
PASSWORD = "segreta"

# --- l'interpretazione ------------------------------------------------------------


def test_una_polestar_2_ferma_nella_forma_vecchia() -> None:
    s = interpreta(POLESTAR2)
    assert s.batteria == 68
    assert s.autonomia_km == 280
    assert s.odometro_km == 24790
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.tempo_ricarica_min is None
    assert s.potenza_ricarica_diretta_kw == 0.0
    assert s.potenza_ricarica_kw == 0.0
    assert s.letto == datetime(2025, 1, 28, 8, 44, 34, tzinfo=UTC)
    # Porte, serrature e posizione il cloud dell'app non le dice.
    assert s.chiusa is None
    assert s.portiere_aperte is None
    assert s.latitudine is None
    assert s.limite_ricarica is None


def test_i_vecchi_get_battery_e_get_odometer() -> None:
    soli = {k: POLESTAR2[k] for k in ("getBatteryData", "getOdometerData")}
    s = interpreta(soli)
    assert (s.batteria, s.autonomia_km, s.odometro_km) == (68, 280, 24790)
    assert s.letto == datetime(2025, 1, 28, 8, 44, 34, tzinfo=UTC)


def test_una_polestar_3_nella_forma_di_oggi() -> None:
    s = interpreta(POLESTAR3, VIN3)
    assert s.batteria == 79
    assert s.autonomia_km == 390
    assert s.odometro_km == 11131
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    # V2 non dice il cavo: senza ricarica non si sa.
    assert s.cavo_collegato is None
    assert s.potenza_ricarica_diretta_kw is None
    assert s.letto == datetime.fromtimestamp(1747822967, tz=UTC)


def test_una_polestar_4_ferma_non_ha_tempo_di_ricarica() -> None:
    s = interpreta(POLESTAR4)
    assert (s.batteria, s.autonomia_km, s.odometro_km) == (42, 261, 3525)
    # Il cloud dice 74 minuti anche da ferma: contano solo in carica.
    assert s.tempo_ricarica_min is None


def test_due_auto_in_una_risposta_ognuna_la_sua() -> None:
    s = interpreta(IN_CARICA_V2, VIN3)
    assert s.batteria == 54
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 95
    assert s.odometro_km == 11250.4
    assert s.letto == datetime.fromtimestamp(1792320000, tz=UTC)
    altra = interpreta(IN_CARICA_V2, VIN_ALTRA)
    assert (altra.batteria, altra.stato_ricarica, altra.odometro_km) == (90, StatoRicarica.NON_IN_CARICA, None)


def test_la_forma_vecchia_in_carica_ha_cavo_e_potenza() -> None:
    s = interpreta(IN_CARICA_V1)
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_diretta_kw == 10.9
    assert s.potenza_ricarica_kw == 10.9
    assert s.tempo_ricarica_min == 240
    assert s.autonomia_km == 160.9  # 100 miglia
    assert s.odometro_km == 24800.5
    assert s.letto == datetime(2025, 1, 28, 20, 0, tzinfo=UTC)


def test_gli_stati_della_ricarica() -> None:
    assert stato_ricarica("CHARGING_STATUS_V2_CHARGING") == StatoRicarica.IN_CARICA
    assert stato_ricarica("CHARGING_STATUS_DONE") == StatoRicarica.COMPLETATA
    assert stato_ricarica("CHARGING_STATUS_SCHEDULED") == StatoRicarica.PROGRAMMATA
    assert stato_ricarica("CHARGING_STATUS_SMART_CHARGING_PAUSED") == StatoRicarica.IN_PAUSA
    assert stato_ricarica("CHARGING_STATUS_FAULT") == StatoRicarica.ERRORE
    assert stato_ricarica("CHARGING_STATUS_UNSPECIFIED") is None
    assert stato_ricarica(None) is None


def test_l_anagrafica() -> None:
    v = vettura_da(POLESTAR4["getConsumerCarsV2"])
    assert v is not None
    assert (v.vin, v.marca, v.modello, v.titolo, v.targa, v.anno) == (
        "XXXXXXXXXXX000000",
        "polestar",
        "Polestar 4",
        "Polestar 4",
        "MLB007",
        None,
    )
    assert v.extra["id"] == "cf4bfecc-cb00-49f3-af84-4a5b21b02da6"
    assert vettura_da({**POLESTAR3["getConsumerCarsV2"], "modelYear": "2024"}).anno == 2024
    assert vettura_da({"modelName": "Polestar 2"}) is None
    assert modello("") == "Polestar"


def test_il_contratto() -> None:
    assert MARCHE["polestar"] is polestar
    assert PER_ID["polestar"].nativo == "polestar"
    assert polestar.SENZA_COMANDI is True
    assert polestar.INTERVALLO_MINUTI == 10
    assert [c.nome for c in polestar.CAMPI] == ["email", "password"]
    assert polestar.comandi_di(Vettura(vin=VIN3, marca="polestar", modello="Polestar 3")) == set()


# --- il cloud finto -------------------------------------------------------------------

RIPRESA = "/as/Xy12/resume/as/authorization.ping"


def _sfida(verificatore: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verificatore.encode()).digest()).decode().rstrip("=")


@dataclass
class FintoPolestar:
    """Polestar ID e l'API GraphQL, quanto basta per le prove."""

    termini: bool = False
    guasto_api: bool = False
    guasto_login: bool = False
    validi: set[str] = field(default_factory=set)
    ruotati: int = 0
    login: int = 0
    sfida: str = ""
    chiamate: list[str] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/.well-known/openid-configuration", self._configurazione)
        app.router.add_get("/as/authorization.oauth2", self._autorizza)
        app.router.add_get("/pf/login", self._pagina)
        app.router.add_post(RIPRESA, self._modulo)
        app.router.add_post("/as/token.oauth2", self._token)
        app.router.add_post("/eu-north-1/mystar-v2/", self._api)
        return app

    async def _configurazione(self, r: web.Request) -> web.Response:
        base = f"http://{r.host}"
        return web.json_response(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/as/authorization.oauth2",
                "token_endpoint": f"{base}/as/token.oauth2",
            }
        )

    async def _autorizza(self, r: web.Request) -> web.Response:
        if self.guasto_login:
            return web.Response(status=502, text="bad gateway")
        q = r.query
        assert q["client_id"] == CLIENT_ID
        assert q["redirect_uri"] == REDIRECT_URI
        assert q["response_type"] == "code"
        assert q["code_challenge_method"] == "S256"
        assert "customer:attributes" in q["scope"]
        self.sfida = q["code_challenge"]
        risposta = web.HTTPFound("/pf/login")
        risposta.set_cookie("PF", "sessione-1")
        raise risposta

    async def _pagina(self, r: web.Request) -> web.Response:
        assert r.cookies.get("PF") == "sessione-1"
        testo = f'<script>window.pf = {{ action: "{RIPRESA}", authMessage: "" }}</script>'
        return web.Response(text=testo, content_type="text/html")

    async def _modulo(self, r: web.Request) -> web.Response:
        assert r.cookies.get("PF") == "sessione-1"
        assert r.query["code_challenge"] == self.sfida
        modulo = await r.post()
        if modulo.get("pf.submit"):
            assert modulo["subject"] == "U-1"
            raise web.HTTPFound(f"{REDIRECT_URI}?code=CODICE&state={r.query['state']}")
        if modulo.get("pf.username") != EMAIL or modulo.get("pf.pass") != PASSWORD:
            return web.Response(text='<script>authMessage: "ERR001"</script>', content_type="text/html")
        if self.termini:
            raise web.HTTPFound(f"{REDIRECT_URI}?uid=U-1")
        raise web.HTTPFound(f"{REDIRECT_URI}?code=CODICE&state={r.query['state']}")

    async def _token(self, r: web.Request) -> web.Response:
        modulo = await r.post()
        assert modulo["client_id"] == CLIENT_ID
        if modulo["grant_type"] == "authorization_code":
            if modulo["code"] != "CODICE" or _sfida(str(modulo["code_verifier"])) != self.sfida:
                return web.json_response({"error": "invalid_grant"}, status=400)
            assert modulo["redirect_uri"] == REDIRECT_URI
            self.login += 1
            self.validi = {f"L{self.login}"}
            return web.json_response({"access_token": f"L{self.login}", "refresh_token": "R1", "expires_in": 1800})
        if modulo["grant_type"] == "refresh_token":
            if modulo["refresh_token"] != f"R{self.ruotati + 1}":
                return web.json_response({"error": "invalid_grant", "error_description": "revoked"}, status=400)
            self.ruotati += 1
            nuovo = f"A{self.ruotati}"
            self.validi = {nuovo}
            return web.json_response(
                {"access_token": nuovo, "refresh_token": f"R{self.ruotati + 1}", "expires_in": 1800}
            )
        return web.json_response({"error": "unsupported_grant_type"}, status=400)

    async def _api(self, r: web.Request) -> web.Response:
        if self.guasto_api:
            return web.Response(status=503, text="Service Unavailable")
        corpo = await r.json()
        self.chiamate.append(corpo["operationName"])
        if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.validi:
            errore = {"message": "Unauthorized", "extensions": {"code": "UNAUTHENTICATED"}}
            return web.json_response({"data": None, "errors": [errore]})
        if corpo["operationName"] == "GetConsumerCarsV2":
            assert "getConsumerCarsV2" in corpo["query"]
            auto = [POLESTAR3["getConsumerCarsV2"], {**POLESTAR2["getConsumerCarsV2"], "vin": VIN_ALTRA}]
            return web.json_response({"data": {"getConsumerCarsV2": auto}})
        if corpo["operationName"] == "CarTelematicsV2":
            assert "carTelematicsV2(vins: $vins)" in corpo["query"]
            vin = corpo["variables"]["vins"][0]
            filtra = {k: [x for x in v if x["vin"] == vin] for k, v in IN_CARICA_V2["carTelematicsV2"].items()}
            return web.json_response({"data": {"carTelematicsV2": filtra}})
        return web.json_response({"errors": [{"message": "Validation error", "errorType": "ValidationError"}]})


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoPolestar, str]]:
    finto = FintoPolestar()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _cliente(
    s: aiohttp.ClientSession,
    url: str,
    token: dict[str, Any] | None = None,
    salvati: list[dict[str, Any]] | None = None,
    password: str = PASSWORD,
) -> ClientePolestar:
    return ClientePolestar(
        s,
        EMAIL,
        password,
        token,
        salvati.append if salvati is not None else None,
        oidc=url,
        api=f"{url}/eu-north-1/mystar-v2/",
    )


def _token(access: str, refresh: str, tra: timedelta) -> dict[str, Any]:
    return {"access_token": access, "refresh_token": refresh, "scade": (datetime.now(UTC) + tra).isoformat()}


async def test_dal_login_allo_stato(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, salvati=salvati)
        await c.accedi()
        vetture = await c.vetture()
        assert [v.vin for v in vetture] == [VIN3, VIN_ALTRA]
        assert [v.titolo for v in vetture] == ["Polestar 3", "Polestar 2"]
        stato = await c.stato(vetture[0])
    assert finto.login == 1
    assert salvati[-1]["access_token"] == "L1"
    assert salvati[-1]["refresh_token"] == "R1"
    assert stato.batteria == 54
    assert stato.stato_ricarica == StatoRicarica.IN_CARICA
    assert stato.tempo_ricarica_min == 95
    assert finto.chiamate == ["GetConsumerCarsV2", "CarTelematicsV2"]


async def test_le_condizioni_nuove_le_accetta_la_persona(cloud) -> None:
    """GdAAuto non accetta condizioni d'uso al posto di nessuno: si ferma e lo dice."""
    finto, url = cloud
    finto.termini = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        with pytest.raises(ErroreCredenziali, match="condizioni"):
            await c.accedi()
    assert finto.login == 0


async def test_la_password_sbagliata(cloud) -> None:
    _finto, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, url, password="sbagliata").accedi()


async def test_il_token_che_scade_si_rinnova_e_si_salva(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("VECCHIO", "R1", timedelta(minutes=2)), salvati)
        assert len(await c.vetture()) == 2
    assert (finto.ruotati, finto.login) == (1, 0)
    assert salvati[-1]["access_token"] == "A1"
    assert salvati[-1]["refresh_token"] == "R2"


async def test_un_refresh_revocato_rientra_con_la_password(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("VECCHIO", "R-REVOCATO", timedelta(minutes=-1)), salvati)
        await c.vetture()
    assert finto.login == 1
    assert salvati[-1]["access_token"] == "L1"


async def test_un_token_rifiutato_si_rinnova_e_si_riprova_una_volta(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("REVOCATO", "R1", timedelta(hours=1)))
        assert len(await c.vetture()) == 2
    assert finto.ruotati == 1
    assert finto.chiamate == ["GetConsumerCarsV2", "GetConsumerCarsV2"]


async def test_un_guasto_non_e_una_password_sbagliata(cloud) -> None:
    finto, url = cloud
    finto.guasto_api = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("L1", "R1", timedelta(hours=1)))
        with pytest.raises(ErroreRete):
            await c.vetture()
        finto.guasto_login = True
        with pytest.raises(ErroreRete):
            await _cliente(s, url).accedi()


async def test_un_cloud_spento(socket_enabled: None) -> None:
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreRete):
            await _cliente(s, "http://127.0.0.1:9").accedi()


async def test_una_query_sbagliata_e_un_errore_api(cloud) -> None:
    _finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, _token("L1", "R1", timedelta(hours=1)))
        with pytest.raises(ErroreApi):
            await c._chiedi("prova", "Sconosciuta", "query Sconosciuta { x }", {})


# --- dentro Home Assistant -------------------------------------------------------------


async def test_configurare_polestar_dal_catalogo(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    with (
        patch.object(modulo_cliente, "OIDC", url),
        patch.object(modulo_cliente, "API", f"{url}/eu-north-1/mystar-v2/"),
    ):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "polestar"})
        assert r["step_id"] == "credenziali"
        assert {str(k) for k in r["data_schema"].schema} == {"email", "password"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": EMAIL, "password": "no"})
        assert r["errors"] == {"base": "credenziali"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": EMAIL, "password": PASSWORD})
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == f"Polestar · {EMAIL}"
        assert r["result"].unique_id == "polestar_guido@esempio.it"
        assert r["data"][CONF_MARCA] == "polestar"
        assert r["data"]["token"]["access_token"] == "L1"
        # Le due auto dell'account, senza chiedere il VIN.
        assert hass.states.get("sensor.polestar_3_batteria").state == "54"
        assert hass.states.get("sensor.polestar_2_batteria").state == "90"
        # Si legge soltanto: nessuna serratura da comandare.
        assert hass.states.get("lock.polestar_3_serratura") is None
    assert finto.login == 1  # il setup riusa il token del login, non rientra


async def test_il_token_rinnovato_finisce_nella_voce(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    voce = MockConfigEntry(
        domain=DOMAIN,
        title=f"Polestar · {EMAIL}",
        unique_id="polestar_guido@esempio.it",
        data={
            CONF_FONTE: FONTE_CLOUD,
            CONF_MARCA: "polestar",
            "marchio_catalogo": "polestar",
            "email": EMAIL,
            "password": PASSWORD,
            "token": _token("VECCHIO", "R1", timedelta(minutes=-5)),
        },
    )
    voce.add_to_hass(hass)
    with (
        patch.object(modulo_cliente, "OIDC", url),
        patch.object(modulo_cliente, "API", f"{url}/eu-north-1/mystar-v2/"),
    ):
        assert await hass.config_entries.async_setup(voce.entry_id)
        await hass.async_block_till_done()
        assert voce.state is ConfigEntryState.LOADED
        assert voce.data["token"]["refresh_token"] == "R2"
        assert hass.states.get("sensor.polestar_3_batteria").state == "54"
        auto = voce.runtime_data.auto[0]
        assert auto._normale == timedelta(minutes=10)
        # In carica la si legge più spesso, ma con calma.
        assert auto.update_interval == timedelta(seconds=120)
        await hass.config_entries.async_unload(voce.entry_id)
    assert (finto.ruotati, finto.login) == (1, 0)
