"""Xpeng da Enode: le misure, il cliente contro un Enode finto, il collegamento dal browser e Home Assistant."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import copy
from dataclasses import dataclass, field, replace
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import CONF_MARCA, DOMAIN
from custom_components.gdaauto.marche import MARCHE, xpeng
from custom_components.gdaauto.marche.base import ACCESSO_CODICE_BROWSER, Dispositivo
from custom_components.gdaauto.marche.catalogo import PER_ID
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.xpeng import cliente as modulo_cliente
from custom_components.gdaauto.marche.xpeng.cliente import (
    PERMESSI,
    ClienteXpeng,
    comandi_di,
    leggi_ritorno,
    ritorno_con,
    utente_di,
)
from custom_components.gdaauto.marche.xpeng.dati import interpreta, vettura_da
from custom_components.gdaauto.vettura import StatoRicarica

from .xpeng_campioni import G6_IN_CARICA, G9_FERMA, VIN_G6, VIN_G9

CLIENT_ID = "e2a1c0de-1234-4abc-9def-0123456789ab"
SEGRETO = "segreto-enode"

# --- l'interpretazione ------------------------------------------------------------


def test_una_g6_in_carica() -> None:
    s = interpreta(G6_IN_CARICA)
    assert (s.batteria, s.autonomia_km, s.limite_ricarica) == (64, 312, 90)
    assert s.capacita_batteria_kwh == 87.5
    assert s.cavo_collegato is True
    assert s.stato_ricarica == StatoRicarica.IN_CARICA and s.in_carica
    assert s.potenza_ricarica_kw == 10.8
    assert s.tempo_ricarica_min == 95
    assert s.v2l_attivo is False
    assert s.odometro_km == 18234.5
    assert (s.latitudine, s.longitudine, s.posizione_con_segno) == (59.9139, 10.7522, True)
    assert s.letto is not None and s.letto.isoformat() == "2026-10-18T09:57:40+00:00"
    assert s.grezzo["capabilities"]["startCharging"] is True
    assert "location" not in s.grezzo


def test_una_g9_ferma_senza_cavo_e_senza_chilometri() -> None:
    s = interpreta(G9_FERMA)
    assert (s.batteria, s.autonomia_km, s.limite_ricarica) == (81, 402, 100)
    assert s.capacita_batteria_kwh is None
    assert s.cavo_collegato is False
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.potenza_ricarica_kw == 0
    assert s.tempo_ricarica_min is None
    assert s.odometro_km is None
    assert s.latitudine is None
    assert s.letto is not None and s.letto.hour == 6


@pytest.mark.parametrize(
    ("consegna", "atteso", "v2l"),
    [
        ("PLUGGED_IN:COMPLETE", StatoRicarica.COMPLETATA, False),
        ("PLUGGED_IN:STOPPED", StatoRicarica.IN_PAUSA, False),
        ("PLUGGED_IN:FAULT", StatoRicarica.ERRORE, False),
        ("PLUGGED_IN:DISCHARGING", StatoRicarica.NON_IN_CARICA, True),
    ],
)
def test_gli_stati_della_consegna(consegna: str, atteso: StatoRicarica, v2l: bool) -> None:
    veicolo = copy.deepcopy(G6_IN_CARICA)
    veicolo["chargeState"].update(powerDeliveryState=consegna, isCharging=False)
    s = interpreta(veicolo)
    assert (s.stato_ricarica, s.v2l_attivo, s.cavo_collegato) == (atteso, v2l, True)
    assert s.potenza_ricarica_kw == 0


def test_senza_stato_della_consegna_e_con_i_campi_vuoti() -> None:
    veicolo = copy.deepcopy(G6_IN_CARICA)
    veicolo["chargeState"]["powerDeliveryState"] = None
    assert interpreta(veicolo).stato_ricarica == StatoRicarica.IN_CARICA
    veicolo["chargeState"].update(isCharging=False, isFullyCharged=True)
    assert interpreta(veicolo).stato_ricarica == StatoRicarica.COMPLETATA
    vuoto = interpreta({"chargeState": None, "location": None, "odometer": None, "capabilities": None})
    assert vuoto.batteria is None and vuoto.stato_ricarica is None and vuoto.letto is None


def test_l_anagrafica_e_i_comandi() -> None:
    g6, g9 = vettura_da(G6_IN_CARICA), vettura_da(G9_FERMA)
    assert g6 is not None and g9 is not None
    assert (g6.vin, g6.marca, g6.modello, g6.nome, g6.anno) == (VIN_G6, "xpeng", "G6", "La G6", 2024)
    assert (g9.vin, g9.nome) == (VIN_G9, "Xpeng G9")
    assert g6.extra["id"] == "veicolo-g6"
    assert comandi_di(g6) == {"ricarica"}
    assert comandi_di(g9) == set()
    assert vettura_da({"id": "x", "information": {}}) is None


def test_il_ritorno_incollato() -> None:
    assert ritorno_con("ABC").endswith("?gdaauto=ABC")
    assert leggi_ritorno("https://www.home-assistant.io/?gdaauto=ABC") == ("ABC", "")
    assert leggi_ritorno("fatto") == ("", "")
    assert leggi_ritorno("https://www.home-assistant.io/?error=access_denied") == ("", "access_denied")
    assert utente_di(CLIENT_ID) == utente_di(f" {CLIENT_ID} ") != utente_di("altro")


def test_il_contratto() -> None:
    assert MARCHE["xpeng"] is xpeng
    assert xpeng.ACCESSO == ACCESSO_CODICE_BROWSER
    assert [c.nome for c in xpeng.CAMPI] == ["client_id", "client_secret"]
    assert not getattr(xpeng, "SENZA_COMANDI", False)


# --- il cliente contro un Enode finto -----------------------------------------------


@dataclass
class FintoEnode:
    token: int = 0
    validi: set[str] = field(default_factory=set)
    collegamenti: list[dict[str, Any]] = field(default_factory=list)
    letture: list[str] = field(default_factory=list)
    comandi: list[dict[str, Any]] = field(default_factory=list)
    esiti: dict[str, int] = field(default_factory=dict)
    fallisce: bool = False
    guasto: bool = False
    veicoli: list[dict[str, Any]] = field(default_factory=lambda: [G6_IN_CARICA, G9_FERMA])

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/oauth2/token", self._token)
        app.router.add_post("/api/users/{utente}/link", self._collega)
        app.router.add_get("/api/vehicles", self._elenco)
        app.router.add_get("/api/vehicles/actions/{id}", self._esito)
        app.router.add_get("/api/vehicles/{id}", self._veicolo)
        app.router.add_post("/api/vehicles/{id}/charging", self._ricarica)
        return app

    async def _token(self, r: web.Request) -> web.Response:
        if self.guasto:
            return web.Response(status=502, text="Bad Gateway")
        auth = aiohttp.BasicAuth.decode(r.headers["Authorization"])
        modulo = await r.post()
        assert modulo["grant_type"] == "client_credentials"
        if (auth.login, auth.password) != (CLIENT_ID, SEGRETO):
            return web.json_response({"error": "invalid_client"}, status=401)
        self.token += 1
        self.validi = {f"T{self.token}"}
        return web.json_response({"access_token": f"T{self.token}", "expires_in": 3599, "token_type": "bearer"})

    def _no(self, r: web.Request) -> web.Response | None:
        if self.guasto:
            return web.Response(status=503, text="Service Unavailable")
        if r.headers.get("Authorization", "").removeprefix("Bearer ") not in self.validi:
            return web.json_response({"type": "unauthorized", "title": "Unauthorized"}, status=401)
        return None

    async def _collega(self, r: web.Request) -> web.Response:
        if no := self._no(r):
            return no
        corpo = await r.json()
        self.collegamenti.append({"utente": r.match_info["utente"], **corpo})
        return web.json_response({"linkUrl": "https://link.enode.com/abc", "linkToken": "lt"})

    async def _elenco(self, r: web.Request) -> web.Response:
        if no := self._no(r):
            return no
        self.letture.append(f"elenco {r.query.get('after', '')}")
        # Due pagine, un'auto per pagina.
        if r.query.get("after") == "c1":
            return web.json_response({"data": self.veicoli[1:], "pagination": {"after": None, "before": "c1"}})
        return web.json_response({"data": self.veicoli[:1], "pagination": {"after": "c1", "before": None}})

    async def _veicolo(self, r: web.Request) -> web.Response:
        if no := self._no(r):
            return no
        self.letture.append(r.match_info["id"])
        for v in self.veicoli:
            if v["id"] == r.match_info["id"]:
                return web.json_response(v)
        return web.json_response({"type": "not-found", "title": "Vehicle not found"}, status=404)

    async def _ricarica(self, r: web.Request) -> web.Response:
        if no := self._no(r):
            return no
        corpo = await r.json()
        if r.match_info["id"] == "veicolo-g9":
            return web.json_response(
                {"type": "bad-request", "title": "Bad Request", "detail": "Vehicle is not capable"}, status=400
            )
        id_ = f"azione-{len(self.comandi) + 1}"
        self.comandi.append(corpo)
        self.esiti[id_] = 0
        return web.json_response({"id": id_, "kind": corpo["action"], "state": "PENDING"})

    async def _esito(self, r: web.Request) -> web.Response:
        if no := self._no(r):
            return no
        id_ = r.match_info["id"]
        self.esiti[id_] += 1
        if self.esiti[id_] < 2:
            return web.json_response({"id": id_, "state": "PENDING"})
        if self.fallisce:
            return web.json_response(
                {"id": id_, "state": "FAILED", "failureReason": {"type": "NO_RESPONSE", "detail": "Car offline"}}
            )
        return web.json_response({"id": id_, "state": "CONFIRMED"})


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoEnode, str]]:
    finto = FintoEnode()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    with patch.object(modulo_cliente, "_ESITO_OGNI_S", 0.01):
        yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _cliente(s: aiohttp.ClientSession, url: str, segreto: str = SEGRETO) -> ClienteXpeng:
    return ClienteXpeng(s, CLIENT_ID, segreto, oauth=url, api=f"{url}/api")


async def test_le_auto_e_lo_stato(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        await c.accedi()
        auto = await c.vetture()
        assert [v.vin for v in auto] == [VIN_G6, VIN_G9]
        assert finto.letture == ["elenco ", "elenco c1"]
        st = await c.stato(auto[0])
        assert (st.batteria, st.in_carica) == (64, True)
        # Il token di un'ora si riusa.
        await c.stato(auto[1])
    assert finto.token == 1


async def test_un_token_rifiutato_se_ne_chiede_un_altro(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        [g6, _g9] = await c.vetture()
        finto.validi = set()  # Enode ha ritirato il token prima della scadenza
        assert (await c.stato(g6)).batteria == 64
    assert finto.token == 2


async def test_credenziali_sbagliate_e_guasti(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, url, segreto="sbagliato").vetture()
        finto.guasto = True
        with pytest.raises(ErroreRete):
            await _cliente(s, url).vetture()
        finto.guasto = False
        c = _cliente(s, url)
        [g6, _g9] = await c.vetture()
        finto.guasto = True
        with pytest.raises(ErroreRete):
            await c.stato(g6)
        finto.guasto = False
        g6.extra["id"] = "sparita"
        with pytest.raises(ErroreApi, match="not found"):
            await c.stato(g6)
        spento = ClienteXpeng(s, CLIENT_ID, SEGRETO, oauth="http://127.0.0.1:9", api="http://127.0.0.1:9")
        with pytest.raises(ErroreRete):
            await spento.accedi()


async def test_la_ricarica(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url)
        g6, g9 = await c.vetture()
        await c.ricarica(g6, False, None)
        assert finto.comandi == [{"action": "STOP"}]
        finto.fallisce = True
        with pytest.raises(ErroreComando, match="Car offline"):
            await c.ricarica(g6, True, None)
        with pytest.raises(ErroreComando, match="not capable"):
            await c.ricarica(g9, True, None)
        with patch.object(modulo_cliente, "_ESITO_ENTRO_S", 0.0), pytest.raises(ErroreComando, match="confermato"):
            finto.fallisce = False
            finto.esiti.clear()
            await c.ricarica(g6, True, None)


# --- il collegamento dal browser ---------------------------------------------------------


def _indirizzi(url: str):
    return patch.object(modulo_cliente, "OAUTH", url), patch.object(modulo_cliente, "API", f"{url}/api")


async def test_avvia_e_completa_il_collegamento(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    dati = {"client_id": CLIENT_ID, "client_secret": SEGRETO}
    a, b = _indirizzi(url)
    with a, b:
        d = await xpeng.avvia_browser(hass, dati)
        assert d.link == "https://link.enode.com/abc" and d.codice_utente == ""
        [collegamento] = finto.collegamenti
        assert collegamento["utente"] == utente_di(CLIENT_ID) == d.stato["utente"]
        assert collegamento["vendorType"] == "vehicle"
        assert collegamento["scopes"] == list(PERMESSI)
        ritorno = collegamento["redirectUri"]
        assert parse_qs(urlparse(ritorno).query)["gdaauto"] == [d.stato["state"]]
        with pytest.raises(ErroreCredenziali, match="appena aperto"):
            await xpeng.completa_browser(hass, dati, d, "https://www.home-assistant.io/?gdaauto=ALTRO")
        with pytest.raises(ErroreCredenziali, match="non è riuscito"):
            await xpeng.completa_browser(hass, dati, d, "https://www.home-assistant.io/?error=access_denied")
        accesso = await xpeng.completa_browser(hass, dati, d, ritorno)
        di_nuovo = await xpeng.completa_browser(hass, dati, d, "fatto")
    assert accesso.unique_id == f"xpeng_{CLIENT_ID}" == di_nuovo.unique_id
    assert accesso.titolo == "Xpeng · Enode e2a1c0de"
    assert accesso.dati == {"client_id": CLIENT_ID, "client_secret": SEGRETO, "utente_enode": utente_di(CLIENT_ID)}
    assert [v.vin for v in accesso.vetture] == [VIN_G6, VIN_G9]


async def test_credenziali_sbagliate_prima_del_link(hass: HomeAssistant, cloud) -> None:
    _finto, url = cloud
    a, b = _indirizzi(url)
    with a, b, pytest.raises(ErroreCredenziali):
        await xpeng.avvia_browser(hass, {"client_id": CLIENT_ID, "client_secret": "no"})
    d = Dispositivo(link="", codice_utente="", stato={"state": "S"})
    with a, b, pytest.raises(ErroreCredenziali):
        await xpeng.completa_browser(hass, {"client_id": CLIENT_ID, "client_secret": "no"}, d, "fatto")


# --- dentro Home Assistant -------------------------------------------------------------


@pytest.fixture
def catalogo():
    """Il catalogo punta Xpeng alla marca nativa (se non lo fa già)."""
    if PER_ID["xpeng"].nativo == "xpeng":
        yield
        return
    with patch.dict(PER_ID, {"xpeng": replace(PER_ID["xpeng"], nativo="xpeng")}):
        yield


async def test_configurare_xpeng_dal_catalogo(hass: HomeAssistant, cloud, catalogo) -> None:
    finto, url = cloud
    a, b = _indirizzi(url)
    with a, b:
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "xpeng"})
        assert r["step_id"] == "credenziali"
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"client_id": CLIENT_ID, "client_secret": SEGRETO}
        )
        assert r["step_id"] == "browser"
        assert r["description_placeholders"]["link"] == "https://link.enode.com/abc"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "?gdaauto=SBAGLIATO"})
        assert r["errors"] == {"base": "codice"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"codice": finto.collegamenti[0]["redirectUri"]}
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["result"].unique_id == f"xpeng_{CLIENT_ID}"
        assert r["data"][CONF_MARCA] == "xpeng"
        assert hass.states.get("sensor.la_g6_batteria").state == "64"
        assert hass.states.get("sensor.xpeng_g9_batteria").state == "81"
        assert hass.states.get("binary_sensor.la_g6_in_carica").state == "on"
        assert hass.states.get("switch.la_g6_comando_ricarica").state == "on"
        # La G9 non ha il permesso di comandare la ricarica.
        assert hass.states.get("switch.xpeng_g9_comando_ricarica") is None

        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": "switch.la_g6_comando_ricarica"}, blocking=True
        )
        assert finto.comandi == [{"action": "STOP"}]
        await hass.config_entries.async_unload(r["result"].entry_id)
