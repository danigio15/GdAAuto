"""Lynk & Co: lo stato dell'app, il cliente contro un cloud finto, e la configurazione in Home Assistant.

ha_lynkco_2025 non ha risposte registrate: i pezzi qui sotto sono scritti coi
nomi e le unità dei campi che quel progetto legge (carica in frazione, km,
kW, minuti, kWh/100 km, °C).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
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
from custom_components.gdaauto.marche import lynk_co
from custom_components.gdaauto.marche.catalogo import PER_ID, Marchio
from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.lynk_co import cliente as modulo_cliente
from custom_components.gdaauto.marche.lynk_co.cliente import ClienteLynk, codice_da, pkce, scambia_codice
from custom_components.gdaauto.marche.lynk_co.dati import interpreta, stato_ricarica
from custom_components.gdaauto.vettura import StatoRicarica

VIN = "LB1E335A0RA000001"
EMAIL = "Guido@Esempio.it"


def jwt(**corpo: Any) -> str:
    return "x." + base64.urlsafe_b64encode(json.dumps(corpo).encode()).decode().rstrip("=") + ".y"


def token(n: int, exp: float | None = None) -> str:
    scade = exp if exp is not None else time.time() + 3600
    return jwt(snowflakeId="9876543210", customerNumber="C-42", email=EMAIL, exp=int(scade), n=n)


CARICA = {
    "batteryState": {
        "stateOfCharge": 0.57,
        "remainingRange": 241,
        "status": "CHARGING",
        "chargingSpeed": {"kW": 10.8},
        "remainingChargingTime": 95,
        "chargeLimit": {"value": 80},
        "powerAverageConsumption": 17.4,
        "updatedAt": "2026-10-18T10:05:00Z",
    }
}
METADATI = {"vehicle": {"odometer": 12345, "propulsionType": "BEV"}, "batteryInfo": {"batteryCapacity": 66.0}}
AUTO = {"centralLock": {"status": "LOCKED"}, "driveModeEnabled": False}
POSIZIONE = {
    "vehicleLocation": {
        "coordinates": {"latitude": 45.46, "longitude": 9.19},
        "longAddress": "Piazza del Duomo, Milano",
        "updatedAt": "2026-10-18T10:00:00Z",
    }
}
CLIMA = {
    "status": "INACTIVE",
    "interiorTemperature": 18.5,
    "targetTemperature": 21,
    "heaters": {"windshield": {"status": "INACTIVE"}},
    "updatedAt": "2026-10-18T09:00:00Z",
}
PORTE = {
    "doorFrontLeftStatus": "CLOSED",
    "doorFrontRightStatus": "CLOSED",
    "doorRearLeftStatus": "CLOSED",
    "doorRearRightStatus": "OPEN",
    "windowFrontLeftStatus": "CLOSED",
    "windowFrontRightStatus": "CLOSED",
    "windowRearLeftStatus": "CLOSED",
    "windowRearRightStatus": "CLOSED",
    "trunkStatus": "CLOSED",
    "hoodStatus": "CLOSED",
}


def test_una_02_in_carica() -> None:
    s = interpreta(
        {
            "charge": CARICA,
            "metadata": METADATI,
            "vehicle_data": AUTO,
            "location": POSIZIONE,
            "climate": CLIMA,
            "doors": PORTE,
        }
    )
    assert s.batteria == 57
    assert s.autonomia_km == 241
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_diretta_kw == 10.8
    assert s.tempo_ricarica_min == 95
    assert s.limite_ricarica == 80
    assert s.consumo_medio_kwh_100km == 17.4
    assert (s.capacita_batteria_kwh, s.energia_residua_kwh) == (66.0, 37.6)
    assert s.odometro_km == 12345
    assert (s.chiusa, s.quadro_acceso) == (True, False)
    assert (s.portiere_aperte, s.finestrini_aperti, s.bagagliaio_aperto) == (True, False, False)
    assert (s.clima_acceso, s.sbrinamento, s.temperatura_interna_c, s.temperatura_clima_c) == (False, False, 18.5, 21)
    assert (s.latitudine, s.longitudine) == (45.46, 9.19)
    assert s.letto == datetime(2026, 10, 18, 10, 5, tzinfo=UTC)


def test_una_08_ibrida_ferma() -> None:
    carica = {"batteryState": {"stateOfCharge": 0.9, "remainingRange": 120, "status": "NOT_CHARGING"}}
    s = interpreta(
        {
            "charge": carica,
            "fuel": {"fuelState": {"percentageOfRemainingFuel": 0.42, "remainingRange": 480}},
            "climate": {"status": "ACTIVE_HEATING"},
        }
    )
    assert (s.stato_ricarica, s.carburante_percento, s.autonomia_carburante_km) == (
        StatoRicarica.NON_IN_CARICA,
        42,
        480,
    )
    assert s.autonomia_totale_km == 600
    assert s.clima_acceso is True
    assert s.chiusa is None and s.latitudine is None


def test_gli_stati_della_ricarica() -> None:
    assert stato_ricarica("FULLY_CHARGED") == StatoRicarica.COMPLETATA
    assert stato_ricarica("charging") == StatoRicarica.IN_CARICA
    assert stato_ricarica("DISCONNECTED") == StatoRicarica.NON_IN_CARICA
    assert stato_ricarica("SCHEDULED") == StatoRicarica.PROGRAMMATA
    assert stato_ricarica("QUALCOSA") is None
    assert stato_ricarica(None) is None


def test_il_codice_e_la_pkce() -> None:
    assert codice_da("msauth://prod.lynkco.app.crisp.prod/2jmj?code=ABC-123&state=x") == "ABC-123"
    assert codice_da(" ABC-123 ") == "ABC-123"
    with pytest.raises(ErroreCredenziali):
        codice_da("msauth://prod.lynkco.app.crisp.prod/2jmj?error=access_denied")
    verificatore, sfida = pkce()
    attesa = base64.urlsafe_b64encode(hashlib.sha256(verificatore.encode()).digest()).decode().rstrip("=")
    assert sfida == attesa and 43 <= len(verificatore) <= 128


# --- il cloud finto --------------------------------------------------------------------


@dataclass
class FintoLynk:
    validi: set[str] = field(default_factory=set)
    refresh: str = "R1"
    rinnovi: int = 0
    comandi: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    guasto: bool = False
    rifiuta: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/lynkcoprod.onmicrosoft.com/b2c_1a_signin_mfa/oauth2/v2.0/token", self._token)
        app.router.add_route("*", "/mobile-app/{resto:.*}", self._api)
        return app

    def _nuovo(self) -> dict[str, Any]:
        self.rinnovi += 1
        t = token(self.rinnovi)
        self.validi = {t}
        self.refresh = f"R{self.rinnovi + 1}"
        return {"access_token": t, "refresh_token": self.refresh, "expires_in": 3600}

    async def _token(self, r: web.Request) -> web.Response:
        if self.guasto:
            return web.Response(status=502)
        f = await r.post()
        assert f["client_id"] == modulo_cliente.CLIENT_ID
        if f["grant_type"] == "authorization_code":
            if f["code"] != "CODICE" or not f["code_verifier"]:
                return web.json_response({"error": "invalid_grant", "error_description": "AADB2C90080"}, status=400)
            self.rinnovi = 0
            return web.json_response(self._nuovo())
        if f["grant_type"] == "refresh_token" and f["refresh_token"] == self.refresh:
            return web.json_response(self._nuovo())
        return web.json_response({"error": "invalid_grant"}, status=400)

    async def _api(self, r: web.Request) -> web.Response:
        if self.guasto:
            return web.Response(status=503)
        t = r.headers["Authorization"].removeprefix("Bearer ")
        if t not in self.validi:
            return web.json_response({"message": "unauthorized"}, status=401)
        resto = "/" + r.match_info["resto"]
        for radice in ("/love/v1", "/love-remote-command/v1"):
            if resto.startswith(radice + "/"):
                firmato = resto[len(radice) :]
                break
        else:
            firmato = r.path
        attesa = hashlib.sha256(("9876543210" + r.headers["X-NONCE"] + firmato).encode()).hexdigest()
        assert r.headers["X-SIGNATURE"] == attesa, "firma sbagliata"
        assert r.headers["X-CustomerNumber"] == "C-42"
        base = f"/love/v1/vehicle/{VIN}/"
        pezzi = {
            "charge_state": CARICA,
            "vehicle_metadata": METADATI,
            "vehicle_data": AUTO,
            "location_state": POSIZIONE,
            "climate_state": CLIMA,
            "doors_windows_state": PORTE,
        }
        if resto == "/iamservice/v1/validate-session":
            return web.json_response({})
        if resto == "/love/v1/list/vehicles":
            return web.json_response({"listOfVehicles": [{"vehicle": {"vin": VIN, "model": "E335"}}]})
        if resto.startswith(base) and resto[len(base) :] in pezzi:
            return web.json_response(pezzi[resto[len(base) :]])
        if resto.startswith(f"/love-remote-command/v1/vehicle/{VIN}/command/"):
            if self.rifiuta:
                return web.json_response({"message": "Car is offline"}, status=409)
            self.comandi.append((resto.rsplit("/", 1)[-1], dict(r.query)))
            return web.json_response({"status": "COMMAND_RECEIVED"})
        return web.json_response({"message": "not found"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintoLynk, str]]:
    finto = FintoLynk()
    runner = web.AppRunner(finto.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    yield finto, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _indirizzi(url: str) -> dict[str, str]:
    return {"login": f"{url}/lynkcoprod.onmicrosoft.com/b2c_1a_signin_mfa/", "api": url}


async def test_dal_codice_allo_stato(cloud) -> None:
    _finto, url = cloud
    async with aiohttp.ClientSession() as s:
        t = await scambia_codice(s, "CODICE", "verificatore", _indirizzi(url)["login"])
        c = ClienteLynk(s, t, indirizzi=_indirizzi(url))
        await c.accedi()
        (v,) = await c.vetture()
        assert (v.vin, v.modello, v.titolo) == (VIN, "02", "Lynk & Co 02")
        stato = await c.stato(v)
    assert stato.batteria == 57 and stato.chiusa is True and stato.odometro_km == 12345
    assert c.chi["email"] == EMAIL


async def test_il_token_scaduto_si_rinnova_e_si_salva(cloud) -> None:
    finto, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        t = await scambia_codice(s, "CODICE", "v", _indirizzi(url)["login"])
        t["scade"] = "2000-01-01T00:00:00+00:00"
        c = ClienteLynk(s, t, salva=salvati.append, indirizzi=_indirizzi(url))
        await c.vetture()
        # Un 401 a metà (il token revocato) rinnova e riprova una volta.
        finto.validi = set()
        await c.vetture()
    assert finto.rinnovi == 3
    assert [x["refresh_token"] for x in salvati] == ["R3", "R4"]


async def test_un_refresh_revocato_vuole_il_browser_e_un_guasto_no(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await scambia_codice(s, "SBAGLIATO", "v", _indirizzi(url)["login"])
        t = await scambia_codice(s, "CODICE", "v", _indirizzi(url)["login"])
        c = ClienteLynk(s, {**t, "scade": "2000-01-01T00:00:00+00:00"}, indirizzi=_indirizzi(url))
        finto.refresh = "ALTRO"  # l'app sul telefono è entrata: il refresh non vale più
        with pytest.raises(ErroreCredenziali):
            await c.vetture()
        finto.guasto = True
        with pytest.raises(ErroreRete):
            await ClienteLynk(s, t, indirizzi=_indirizzi(url)).vetture()


async def test_i_comandi(cloud) -> None:
    finto, url = cloud
    async with aiohttp.ClientSession() as s:
        t = await scambia_codice(s, "CODICE", "v", _indirizzi(url)["login"])
        c = ClienteLynk(s, t, indirizzi=_indirizzi(url))
        (v,) = await c.vetture()
        await c.chiudi(v, None)
        await c.apri(v, None)
        await c.trova(v, None)
        await c.clima(v, True, None, 30)
        await c.clima(v, False, None)
        await c.ricarica(v, True, None)
        await c.limite_ricarica(v, 82, None)
        with pytest.raises(ErroreComando):
            await c.limite_ricarica(v, 20, None)
        finto.rifiuta = True
        with pytest.raises(ErroreComando, match="offline"):
            await c.chiudi(v, None)
    assert finto.comandi == [
        ("door_lock", {}),
        ("door_unlock", {}),
        ("flash_lights", {}),
        ("auto_conditioning_start", {"temp": "28"}),
        ("auto_conditioning_stop", {}),
        ("charge_start", {}),
        ("set_charge_limit", {"percent": "80"}),
    ]


# --- dentro Home Assistant -------------------------------------------------------------

MARCHIO = Marchio("lynk_co", "Lynk & Co", "lynk_co", {}, "Lynk & Co (HACS)", "https://github.com/b12e/ha_lynkco_2025")


async def test_configurare_lynk_co_dal_catalogo(hass: HomeAssistant, cloud) -> None:
    finto, url = cloud
    with patch.dict(PER_ID, {"lynk_co": MARCHIO}), patch.dict(modulo_cliente.INDIRIZZI, _indirizzi(url)):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": "lynk_co"})
        assert r["step_id"] == "browser"
        assert "code_challenge_method=S256" in r["description_placeholders"]["link"]
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"codice": "msauth://x?code=SBAGLIATO"})
        assert r["errors"] == {"base": "codice"}
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"],
            {"codice": "msauth://prod.lynkco.app.crisp.prod/2jmj7l5rSw0yVb%2FvlWAYkK%2FYBwk%3D?code=CODICE"},
        )
        await hass.async_block_till_done()
        assert r["type"] is FlowResultType.CREATE_ENTRY
        assert r["title"] == f"Lynk & Co · {EMAIL}"
        assert r["result"].unique_id == "lynk_co_guido@esempio.it"
        assert r["data"][CONF_MARCA] == "lynk_co"
        assert r["data"]["dispositivo"]
        assert hass.states.get("sensor.lynk_co_02_batteria").state == "57"
        assert hass.states.get("lock.lynk_co_02_serratura").state == "locked"
    assert finto.rinnovi == 1  # il setup riusa il token della configurazione
    assert lynk_co.comandi_di(r["result"].runtime_data.auto[0].vettura) >= {"chiudi", "ricarica"}
