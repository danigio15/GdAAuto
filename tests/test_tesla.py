"""Tesla: i dati della Fleet API, e il cliente contro un cloud finto.

Il ``vehicle_data`` qui sotto ha la forma della documentazione Tesla (una
Model 3 in carica, in miglia e bar, come le manda il cloud).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from aiohttp import web
import pytest

from custom_components.gdaauto.marche.errori import ErroreComando, ErroreCredenziali
from custom_components.gdaauto.marche.tesla import ClienteTesla, regione
from custom_components.gdaauto.marche.tesla.dati import interpreta
from custom_components.gdaauto.vettura import Marcia, StatoRicarica

VIN = "5YJ3E7EB0KF000001"

VEHICLE_DATA = {
    "id": 100021,
    "vin": VIN,
    "state": "online",
    "charge_state": {
        "battery_level": 64,
        "battery_range": 175.5,
        "charge_limit_soc": 80,
        "charger_power": 11,
        "charging_state": "Charging",
        "conn_charge_cable": "IEC",
        "minutes_to_full_charge": 45,
        "timestamp": 1778137347360,
    },
    "climate_state": {
        "inside_temp": 19.5,
        "outside_temp": 12.0,
        "is_climate_on": False,
        "driver_temp_setting": 21.0,
        "timestamp": 1778137347360,
    },
    "drive_state": {
        "latitude": 41.9028,
        "longitude": 12.4964,
        "power": -11,
        "shift_state": None,
        "speed": None,
        "timestamp": 1778137347360,
    },
    "vehicle_state": {
        "locked": True,
        "odometer": 12345.6,
        "df": 0,
        "dr": 0,
        "pf": 0,
        "pr": 0,
        "rt": 0,
        "fd_window": 0,
        "fp_window": 0,
        "rd_window": 0,
        "rp_window": 0,
        "sentry_mode": False,
        "tpms_pressure_fl": 2.9,
        "tpms_pressure_fr": 2.9,
        "tpms_pressure_rl": 2.85,
        "tpms_pressure_rr": 2.85,
        "timestamp": 1778137347360,
    },
    "vehicle_config": {"car_type": "model3"},
}


def test_i_dati_di_una_model_3_in_carica() -> None:
    s = interpreta(VEHICLE_DATA)
    assert s.batteria == 64
    assert s.autonomia_km == 282.4  # 175,5 miglia
    assert s.odometro_km == 19868.3
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.potenza_ricarica_kw == 11
    assert s.tempo_ricarica_min == 45
    assert s.limite_ricarica == 80
    assert s.marcia == Marcia.PARCHEGGIO
    assert s.velocita_kmh == 0.0
    assert s.potenza_kw == 0.0  # in carica la potenza è negativa: non si sta consumando
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.finestrini_aperti is False
    assert s.pneumatici_kpa["anteriore_sinistro"] == 290.0
    assert (s.latitudine, s.longitudine) == (41.9028, 12.4964)
    assert s.temperatura_esterna_c == 12.0
    assert s.in_sonno is False


def test_cavo_staccato_e_in_marcia() -> None:
    dati = {
        "charge_state": {"charging_state": "Disconnected", "conn_charge_cable": "<invalid>", "battery_level": 50},
        "drive_state": {"shift_state": "D", "speed": 62, "power": 25},
    }
    s = interpreta(dati)
    assert s.cavo_collegato is False
    assert s.marcia == Marcia.AVANTI
    assert s.velocita_kmh == 99.8
    assert s.potenza_kw == 25
    assert s.potenza_ricarica_kw == 0.0


def test_la_regione_viene_dal_token() -> None:
    import base64
    import json

    def jwt(corpo: dict[str, Any]) -> str:
        return "x." + base64.urlsafe_b64encode(json.dumps(corpo).encode()).decode().rstrip("=") + ".y"

    assert regione(jwt({"ou_code": "NA"})) == "na"
    assert regione(jwt({"ou_code": "EU"})) == "eu"
    assert regione("non-un-token") == "eu"


@dataclass
class FintaTesla:
    dorme: bool = False
    firma: bool = False
    rifiuta: str = ""
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    sveglie: int = 0

    def app(self, nome: str) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/api/1/{resto:.*}", self._gestisci)
        app["nome"] = nome
        return app

    async def _gestisci(self, r: web.Request) -> web.Response:
        resto = r.match_info["resto"]
        self.chiamate.append(f"{r.app['nome']}:{r.method} {resto}")
        if r.headers.get("Authorization") != "Bearer TOKEN":
            return web.json_response({"error": "invalid_token"}, status=401)
        if resto == "vehicles":
            return web.json_response(
                {"response": [{"id": 1, "vin": VIN, "display_name": "Saetta", "state": "online"}], "count": 1}
            )
        if resto == f"vehicles/{VIN}":
            return web.json_response({"response": {"vin": VIN, "state": "asleep" if self.dorme else "online"}})
        if resto == f"vehicles/{VIN}/vehicle_data":
            assert "location_data" in r.query["endpoints"]
            return web.json_response({"response": VEHICLE_DATA})
        if resto == f"vehicles/{VIN}/wake_up":
            self.sveglie += 1
            self.dorme = False
            return web.json_response({"response": {"state": "online"}})
        if resto.startswith(f"vehicles/{VIN}/command/"):
            if self.dorme:
                return web.json_response({"error": "vehicle unavailable: asleep"}, status=408)
            if self.firma and r.app["nome"] == "fleet":
                return web.json_response(
                    {"error": "Tesla Vehicle Command Protocol required, please refer to the documentation"}, status=403
                )
            nome = resto.rsplit("/", 1)[-1]
            self.comandi.append((f"{r.app['nome']}:{nome}", await r.json()))
            if self.rifiuta:
                return web.json_response({"response": {"result": False, "reason": self.rifiuta}})
            return web.json_response({"response": {"result": True, "reason": ""}})
        return web.json_response({"error": "not found"}, status=404)


@pytest.fixture
async def tesla(socket_enabled: None) -> AsyncGenerator[tuple[FintaTesla, str, str]]:
    finta = FintaTesla()
    corridori = []
    indirizzi = []
    for nome in ("fleet", "proxy"):
        runner = web.AppRunner(finta.app(nome))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        corridori.append(runner)
        indirizzi.append(f"http://127.0.0.1:{runner.addresses[0][1]}")
    yield finta, indirizzi[0], indirizzi[1]
    for runner in corridori:
        await runner.cleanup()


def _cliente(s: aiohttp.ClientSession, fleet: str, proxy: str = "", token: str = "TOKEN") -> ClienteTesla:
    async def dammi() -> str:
        return token

    c = ClienteTesla(s, dammi, "eu", proxy=proxy)
    c.base_url = fleet
    return c


async def test_legge_l_auto_sveglia(tesla) -> None:
    _finta, fleet, _ = tesla
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, fleet)
        (v,) = await c.vetture()
        assert (v.vin, v.titolo, v.modello) == (VIN, "Saetta", "Model 3")
        stato = await c.stato(v)
    assert stato.batteria == 64


async def test_l_auto_che_dorme_non_si_sveglia_per_leggerla(tesla) -> None:
    finta, fleet, _ = tesla
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, fleet)
        (v,) = await c.vetture()
        prima = await c.stato(v)
        finta.dorme = True
        dopo = await c.stato(v)
    assert dopo.in_sonno is True
    assert dopo.batteria == prima.batteria
    assert finta.sveglie == 0
    assert finta.chiamate.count(f"fleet:GET vehicles/{VIN}/vehicle_data") == 1


async def test_addormentata_al_primo_avvio(tesla) -> None:
    finta, fleet, _ = tesla
    finta.dorme = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, fleet)
        (v,) = await c.vetture()
        stato = await c.stato(v)
    assert stato.in_sonno is True
    assert stato.batteria is None


async def test_per_un_comando_si_sveglia(tesla) -> None:
    finta, fleet, _ = tesla
    finta.dorme = True
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, fleet)
        (v,) = await c.vetture()
        await c.limite_ricarica(v, 90, None)
    assert finta.sveglie == 1
    assert finta.comandi == [("fleet:set_charge_limit", {"percent": 90})]


async def test_i_comandi_firmati_passano_dal_proxy(tesla) -> None:
    finta, fleet, proxy = tesla
    finta.firma = True
    async with aiohttp.ClientSession() as s:
        senza = _cliente(s, fleet)
        (v,) = await senza.vetture()
        with pytest.raises(ErroreComando, match="tesla-http-proxy"):
            await senza.chiudi(v, None)
        con = _cliente(s, fleet, proxy=proxy)
        await con.chiudi(v, None)
        await con.destinazione(v, "Casa", 41.9, 12.5)
    assert finta.comandi == [
        ("proxy:door_lock", {}),
        ("proxy:navigation_gps_request", {"lat": 41.9, "lon": 12.5, "order": 0}),
    ]
    # Leggere resta sulla Fleet API.
    assert not any(x.startswith("proxy:GET") for x in finta.chiamate)


async def test_un_no_che_vuol_dire_gia_fatto(tesla) -> None:
    finta, fleet, _ = tesla
    async with aiohttp.ClientSession() as s:
        c = _cliente(s, fleet)
        (v,) = await c.vetture()
        finta.rifiuta = "is_charging"
        await c.ricarica(v, True, None)
        finta.rifiuta = "cabin comfort remote settings not enabled"
        with pytest.raises(ErroreComando, match="cabin comfort"):
            await c.clima(v, True, None)


async def test_il_token_rifiutato(tesla) -> None:
    _, fleet, _ = tesla
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali):
            await _cliente(s, fleet, token="SCADUTO").vetture()


async def test_un_guasto_del_rinnovo_non_chiede_di_rientrare() -> None:
    from custom_components.gdaauto.marche.errori import ErroreRete

    async def rete_giu() -> str:
        raise aiohttp.ClientConnectionError("niente rete")

    async def rifiutato() -> str:
        raise aiohttp.ClientResponseError(None, (), status=401)  # type: ignore[arg-type]

    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreRete):
            await ClienteTesla(s, rete_giu, "eu").vetture()
        with pytest.raises(ErroreCredenziali):
            await ClienteTesla(s, rifiutato, "eu").vetture()
