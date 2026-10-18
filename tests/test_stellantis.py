"""Stellantis: lo stato del Connected Car API v4, e il cliente contro un cloud finto."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import web
import pytest

from custom_components.gdaauto.marche.errori import ErroreCredenziali
from custom_components.gdaauto.marche.stellantis import ClienteStellantis, codice_da, indirizzo_login
from custom_components.gdaauto.marche.stellantis.cliente import configurazione
from custom_components.gdaauto.marche.stellantis.dati import interpreta, minuti
from custom_components.gdaauto.vettura import StatoRicarica

VIN = "VR3UHZKXZPT000001"

# Una e-208 in carica, nella forma che il cloud restituisce.
STATO = {
    "updatedAt": "2026-10-18T10:00:00Z",
    "energies": [
        {
            "type": "Electric",
            "level": 57,
            "autonomy": 210,
            "updatedAt": "2026-10-18T10:05:00Z",
            "extension": {
                "electric": {
                    "battery": {"load": {"capacity": 46, "residual": 26.2}},
                    "charging": {
                        "plugged": True,
                        "status": "InProgress",
                        "remainingTime": "PT1H23M",
                        "chargingRate": 30,
                        "chargingMode": "Slow",
                    },
                }
            },
        }
    ],
    "odometer": {"mileage": 23456.7},
    "kinetic": {"moving": False},
    "environment": {"air": {"temp": 14}},
    "doorsState": {
        "lockedStates": ["Locked"],
        "opening": [
            {"identifier": "Driver", "state": "Closed"},
            {"identifier": "Passenger", "state": "Closed"},
            {"identifier": "Trunk", "state": "Open"},
        ],
    },
    "preconditioning": {"airConditioning": {"status": "Disabled"}},
    "ignition": {"type": "Stop"},
    "lastPosition": {"type": "Feature", "geometry": {"type": "Point", "coordinates": [9.19, 45.46, 120]}},
}


def test_lo_stato_di_una_e208_in_carica() -> None:
    s = interpreta(STATO)
    assert s.batteria == 57
    assert s.autonomia_km == 210
    assert s.energia_residua_kwh == 26.2
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 83
    assert s.odometro_km == 23456.7
    assert s.velocita_kmh == 0.0
    assert s.temperatura_esterna_c == 14
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is True
    assert s.clima_acceso is False
    assert s.quadro_acceso is False
    assert (s.latitudine, s.longitudine) == (45.46, 9.19)
    assert s.letto == datetime(2026, 10, 18, 10, 5, tzinfo=UTC)


def test_le_durate_iso() -> None:
    assert minuti("PT1H23M") == 83
    assert minuti("PT45M") == 45
    assert minuti("P1DT2H") == 1560
    assert minuti("PT0S") == 0
    assert minuti("domani") is None
    assert minuti(None) is None


def test_il_codice_dall_indirizzo_o_da_solo() -> None:
    assert codice_da("mymap://oauth2redirect/it?code=ABC-123&state=x") == "ABC-123"
    assert codice_da("  ABC-123 ") == "ABC-123"


def test_il_link_del_login() -> None:
    link = indirizzo_login("MyPeugeot", "IT")
    assert link.startswith("https://idpcvs.peugeot.com/am/oauth2/authorize?")
    assert "redirect_uri=mymap%3A%2F%2Foauth2redirect%2Fit" in link
    assert "response_type=code" in link


@dataclass
class FintoStellantis:
    config: dict[str, str]
    validi: set[str] = field(default_factory=lambda: {"A1"})
    ruotati: int = 0
    chiamate: list[str] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/am/oauth2/access_token", self._token)
        app.router.add_get("/connectedcar/v4/user/{resto:.*}", self._api)
        return app

    async def _token(self, r: web.Request) -> web.Response:
        import base64

        attesa = base64.b64encode(f"{self.config['client_id']}:{self.config['client_secret']}".encode()).decode()
        assert r.headers["Authorization"] == f"Basic {attesa}"
        q = r.query
        if q["grant_type"] == "authorization_code":
            if q["code"] != "CODICE" or q["redirect_uri"] != "mymap://oauth2redirect/it":
                return web.json_response({"error": "invalid_grant", "error_description": "code"}, status=400)
            return web.json_response({"access_token": "A1", "refresh_token": "R1", "expires_in": 900})
        if q["grant_type"] == "refresh_token":
            if q["refresh_token"] != f"R{self.ruotati + 1}":
                return web.json_response({"error": "invalid_grant"}, status=400)
            self.ruotati += 1
            nuovo = f"A{self.ruotati + 1}"
            self.validi = {nuovo}
            return web.json_response(
                {"access_token": nuovo, "refresh_token": f"R{self.ruotati + 1}", "expires_in": 900}
            )
        return web.json_response({}, status=400)

    async def _api(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(resto)
        assert r.query["client_id"] == self.config["client_id"]
        assert r.headers["x-introspect-realm"] == "clientsB2CPeugeot"
        if r.headers["Authorization"].removeprefix("Bearer ") not in self.validi:
            return web.json_response({"message": "unauthorized"}, status=401)
        if resto == "vehicles":
            return web.json_response(
                {"_embedded": {"vehicles": [{"id": "abc", "vin": VIN, "brand": "Peugeot", "motorization": "Electric"}]}}
            )
        if resto == "vehicles/abc/status":
            return web.json_response(STATO)
        return web.json_response({"code": 40400, "message": "not found"}, status=404)


@pytest.fixture
async def stellantis(socket_enabled: None) -> AsyncGenerator[tuple[FintoStellantis, str]]:
    finto = FintoStellantis(configurazione("MyPeugeot", "IT"))
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _cliente(s: aiohttp.ClientSession, url: str, token: dict[str, Any], salvati: list[dict[str, Any]]):
    c = ClienteStellantis(s, "MyPeugeot", "IT", token, salvati.append, api=url)
    c.config["oauth_url"] = url
    return c


async def test_dal_codice_allo_stato(stellantis) -> None:
    _finto, url = stellantis
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, {}, salvati)
        token = await c.scambia_codice("CODICE")
        assert token["refresh_token"] == "R1"
        (v,) = await c.vetture()
        assert (v.vin, v.modello, v.titolo) == (VIN, "Peugeot", "Peugeot")
        stato = await c.stato(v)
    assert stato.batteria == 57
    assert salvati == []  # lo scambio del codice lo salva la configurazione


async def test_il_token_scaduto_si_rinnova_e_si_salva(stellantis) -> None:
    finto, url = stellantis
    salvati: list[dict[str, Any]] = []
    scaduto = {
        "access_token": "A1",
        "refresh_token": "R1",
        "scade": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, scaduto, salvati)
        await c.vetture()
    assert finto.ruotati == 1
    assert salvati[-1]["refresh_token"] == "R2"


async def test_un_401_rinnova_e_riprova_una_volta(stellantis) -> None:
    finto, url = stellantis
    salvati: list[dict[str, Any]] = []
    valido = {
        "access_token": "VECCHIO",
        "refresh_token": "R1",
        "scade": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, url, valido, salvati)
        assert len(await c.vetture()) == 1
    assert finto.ruotati == 1


async def test_il_codice_sbagliato(stellantis) -> None:
    _, url = stellantis
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, url, {}, []).scambia_codice("SBAGLIATO")
