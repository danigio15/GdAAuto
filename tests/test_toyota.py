"""Toyota e Lexus: i dati del cloud europeo, il cliente contro un cloud finto, e Home Assistant.

I payload sono in ``toyota_campioni.py``, nella forma dei fixture di pytoyoda.
Il cloud finto fa i tre passi del login ForgeRock (con i suoi cookie), il
rinnovo che ruota il refresh token, e ``ctpa-oneapi`` con le sue intestazioni.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.gdaauto.const import DOMAIN
from custom_components.gdaauto.marche import MARCHE, toyota
from custom_components.gdaauto.marche.errori import ErroreApi, ErroreComando, ErroreCredenziali, ErroreRete
from custom_components.gdaauto.marche.toyota.cliente import ClienteToyota, capacita, comandi_di, uuid_da
from custom_components.gdaauto.marche.toyota.dati import interpreta
from custom_components.gdaauto.vettura import StatoRicarica, Vettura

from . import toyota_campioni as c

EMAIL = "mario@esempio.it"
PASSWORD = "giusta"

# --- i dati -------------------------------------------------------------------------


def test_la_rav4_plug_in() -> None:
    s = interpreta(
        {
            "elettrico": c.ELETTRICO_RAV4["payload"],
            "telemetria": c.TELEMETRIA_RAV4["payload"],
            "posizione": c.POSIZIONE["payload"],
        }
    )
    assert s.batteria == 79
    assert s.autonomia_km == 57.3  # l'autonomia elettrica, non quella col serbatoio
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is False
    assert s.tempo_ricarica_min is None
    assert s.odometro_km == 30420
    assert (s.latitudine, s.longitudine) == (52.169516, 0.135654)
    assert s.posizione_con_segno is True
    # Senza lo stato delle portiere non si inventa niente.
    assert s.chiusa is None and s.portiere_aperte is None
    assert s.letto == datetime(2023, 11, 28, 9, 45, 40, tzinfo=UTC)


def test_la_bz4x_in_carica_chiusa_e_col_clima() -> None:
    s = interpreta(
        {
            "elettrico": c.ELETTRICO_BZ4X["payload"],
            "telemetria": c.TELEMETRIA_BZ4X["payload"],
            "stato": c.STATO["payload"],
            "clima": c.CLIMA_ACCESO["payload"],
        }
    )
    assert s.batteria == 62
    assert s.autonomia_km == 281
    assert s.stato_ricarica == StatoRicarica.IN_CARICA
    assert s.in_carica is True
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min == 95
    assert s.odometro_km == 18250
    assert s.chiusa is True
    assert s.portiere_aperte is False
    assert s.bagagliaio_aperto is False
    assert s.finestrini_aperti is False
    assert s.clima_acceso is True
    assert s.temperatura_interna_c == 21.5
    assert s.temperatura_clima_c == 22
    assert s.latitudine is None


def test_miglia_fahrenheit_e_segnaposti() -> None:
    stato = copy.deepcopy(c.STATO["payload"])
    stato["doors"]["rearLeft"]["lockStatus"]["status"] = "unlocked"
    stato["doors"]["rearBack"]["openStatus"]["status"] = "open"
    stato["windows"]["passenger"]["status"] = "open"
    s = interpreta(
        {
            "elettrico": {
                "batteryLevel": 100,
                "chargingStatus": "chargeComplete",
                "evRange": {"unit": "mi", "value": 200},
                "remainingChargeTime": 65535,
            },
            "telemetria": {"odometer": {"unit": "mi", "value": 1000}},
            "stato": stato,
            "clima": {"status": "stopped", "currentTemperature": {"value": 68, "unit": "F"}},
        }
    )
    assert s.autonomia_km == 321.9
    assert s.odometro_km == 1609.3
    assert s.stato_ricarica == StatoRicarica.COMPLETATA
    assert s.cavo_collegato is True
    assert s.tempo_ricarica_min is None  # 65535 è il «non so» del cloud
    assert s.chiusa is False
    assert s.bagagliaio_aperto is True
    assert s.finestrini_aperti is True
    assert s.clima_acceso is False
    assert s.temperatura_interna_c == 20


def test_cavo_attaccato_senza_carica_e_autonomia_dalla_telemetria() -> None:
    s = interpreta({"elettrico": {"chargingStatus": "plugged"}, "telemetria": c.TELEMETRIA_BZ4X["payload"]})
    assert s.stato_ricarica == StatoRicarica.NON_IN_CARICA
    assert s.cavo_collegato is True
    assert s.batteria == 62
    assert s.autonomia_km == 281


def test_capacita_e_comandi() -> None:
    rav4, bz4x, yaris = (capacita(x) for x in (c.RAV4, c.BZ4X, c.YARIS))
    assert rav4["elettrica"] and bz4x["elettrica"] and not yaris["elettrica"]
    assert rav4["clima"] and not rav4["serrature"] and not rav4["stato"]
    assert bz4x["serrature"] and bz4x["stato"]
    assert comandi_di(Vettura("X", "toyota", "RAV4", extra=rav4)) == {"clima"}
    assert comandi_di(Vettura("X", "toyota", "bZ4X", extra=bz4x)) == {"chiudi", "apri", "clima"}
    assert uuid_da(c.jwt(uuid="u-1")) == "u-1"
    assert uuid_da("non-un-token") is None


def test_la_marca_e_registrata() -> None:
    assert MARCHE["toyota"] is toyota
    assert toyota.INTERVALLO_MINUTI >= 15
    assert [x.nome for x in toyota.CAMPI] == ["email", "password"]


# --- il cloud finto ------------------------------------------------------------------


@dataclass
class FintaToyota:
    utenti: dict[str, str] = field(default_factory=lambda: {EMAIL: PASSWORD})
    guasto_login: bool = False
    guasto_api: bool = False
    rifiuta_refresh: bool = False
    clima_codice: str = "000000"
    comando_http: int = 200
    access: str = ""
    refresh: str = ""
    emessi: int = 0
    chiamate: list[str] = field(default_factory=list)
    comandi: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    marchi: set[str] = field(default_factory=set)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/json/realms/root/realms/tme/authenticate", self._autentica)
        app.router.add_get("/oauth2/realms/root/realms/tme/authorize", self._autorizza)
        app.router.add_post("/oauth2/realms/root/realms/tme/access_token", self._token)
        app.router.add_route("*", "/{resto:v[0-9]/.*}", self._api)
        return app

    # --- il login ForgeRock

    async def _autentica(self, r: web.Request) -> web.Response:
        self.chiamate.append("authenticate")
        if self.guasto_login:
            return web.Response(status=503, text="Service Unavailable")
        if r.query.get("authIndexValue") != "oneapp":
            return web.json_response({"code": 400}, status=400)
        corpo = await r.json()
        domande = corpo.get("callbacks") or []
        if domande:
            # Dopo il primo passo il server vuole indietro il suo cookie di bilanciamento.
            assert r.cookies.get("amlbcookie") == "01", r.headers.get("Cookie")
        risposta: dict[str, Any] = {"authId": "AUTH"}
        tipi = {d["type"]: d for d in domande}
        if not domande:
            risposta["callbacks"] = c.DOMANDE_LINGUA
        elif "PasswordCallback" in tipi:
            email = self._email
            if self.utenti.get(email) != tipi["PasswordCallback"]["input"][0]["value"]:
                return web.json_response(c.PASSWORD_SBAGLIATA, status=401)
            risposta = {"tokenId": f"TID-{email}", "successUrl": "/console", "realm": "/tme"}
            esito = web.json_response(risposta)
            esito.set_cookie("iPlanetDirectoryPro", f"TID-{email}")
            esito.set_cookie("amlbcookie", "01")
            return esito
        elif domande[0]["output"][0]["value"] == "User Name":
            self._email = domande[0]["input"][0]["value"]
            risposta["callbacks"] = c.DOMANDE_PASSWORD if self._email in self.utenti else c.UTENTE_SCONOSCIUTO
        else:
            risposta["callbacks"] = c.DOMANDE_UTENTE
        esito = web.json_response(risposta)
        esito.set_cookie("amlbcookie", "01")
        esito.set_cookie("route", "abc")
        return esito

    _email: str = ""

    async def _autorizza(self, r: web.Request) -> web.Response:
        self.chiamate.append("authorize")
        assert r.query["client_id"] == "oneapp"
        assert r.query["redirect_uri"] == "com.toyota.oneapp:/oauth2Callback"
        if not r.cookies.get("iPlanetDirectoryPro", "").startswith("TID-"):
            return web.Response(status=200, text="<html>login</html>")
        raise web.HTTPFound("com.toyota.oneapp:/oauth2Callback?code=CODICE&iss=x&client_id=oneapp")

    def _emetti(self) -> web.Response:
        self.emessi += 1
        self.access, self.refresh = f"A{self.emessi}", f"R{self.emessi}"
        return web.json_response(
            {
                "access_token": self.access,
                "refresh_token": self.refresh,
                "scope": "openid profile write",
                "id_token": c.jwt(name="Mario", uuid=c.UUID),
                "token_type": "Bearer",
                "expires_in": 3599,
            }
        )

    async def _token(self, r: web.Request) -> web.Response:
        modulo = await r.post()
        self.chiamate.append(f"token:{modulo['grant_type']}")
        assert r.headers["Authorization"] == "basic b25lYXBwOm9uZWFwcA=="
        assert modulo["client_id"] == "oneapp"
        if modulo["grant_type"] == "authorization_code":
            assert modulo["code"] == "CODICE"
            return self._emetti()
        if self.rifiuta_refresh or modulo["refresh_token"] != self.refresh:
            return web.json_response({"error": "invalid_grant"}, status=400)
        return self._emetti()

    # --- ctpa-oneapi

    async def _api(self, r: web.Request) -> web.Response:
        percorso = "/" + r.match_info["resto"]
        self.chiamate.append(f"{r.method} {percorso}")
        if self.guasto_api:
            return web.json_response({"message": "Internal Server Error"}, status=503)
        if r.headers.get("Authorization") != f"Bearer {self.access}":
            return web.json_response({"message": "Unauthorized"}, status=401)
        assert r.headers["x-api-key"] == "tTZipv6liF74PwMfk9Ed68AQ0bISswwf3iHQdqcF"
        assert r.headers["x-guid"] == c.UUID
        assert len(r.headers["x-client-ref"]) == 64
        self.marchi.add(r.headers["x-brand"])
        vin = r.headers.get("vin")
        if percorso == "/v2/vehicle/guid":
            return web.json_response(c.GARAGE)
        letture = {
            "/v1/vehicle/electric/status": {c.VIN_BZ4X: c.ELETTRICO_BZ4X, c.VIN_RAV4: c.ELETTRICO_RAV4},
            "/v1/vehicle/status": {c.VIN_BZ4X: c.STATO},
            "/v3/telemetry": {c.VIN_BZ4X: c.TELEMETRIA_BZ4X, c.VIN_RAV4: c.TELEMETRIA_RAV4},
            "/v1/location": {c.VIN_RAV4: c.POSIZIONE},
            "/v1/vehicle/climate-status": {c.VIN_BZ4X: c.CLIMA_ACCESO, c.VIN_RAV4: c.CLIMA_SPENTO},
        }
        if r.method == "GET" and percorso in letture:
            if vin in letture[percorso]:
                return web.json_response(letture[percorso][vin])
            return web.json_response({"status": {"messages": [{"responseCode": "CTP-404"}]}}, status=404)
        if r.method == "POST" and percorso in ("/v1/global/remote/command", "/v2/remote/climate-control"):
            self.comandi.append((percorso, await r.json()))
            if self.comando_http != 200:
                return web.json_response(
                    {"status": {"messages": [{"responseCode": "CTP-REMOTE-40006", "description": "Not allowed"}]}},
                    status=self.comando_http,
                )
            if percorso.endswith("climate-control"):
                return web.json_response({"payload": {"appRequestNo": "REQ", "returnCode": self.clima_codice}})
            return web.json_response({"status": c._RIUSCITA, "payload": {"appRequestNo": "REQ"}})
        return web.json_response({"message": "Not Found"}, status=404)


@pytest.fixture
async def cloud(socket_enabled: None) -> AsyncGenerator[tuple[FintaToyota, str]]:
    finta = FintaToyota()
    runner = web.AppRunner(finta.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    yield finta, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _cliente(s: aiohttp.ClientSession, url: str, **kw: Any) -> ClienteToyota:
    kw.setdefault("password", PASSWORD)
    return ClienteToyota(s, kw.pop("email", EMAIL), login=url, api=url, **kw)


async def test_entra_elenca_e_legge(cloud) -> None:
    finta, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, url, salva=salvati.append)
        await cl.accedi()
        vetture = await cl.vetture()
        rav4, bz4x = vetture
        stato_bz4x = await cl.stato(bz4x)
        stato_rav4 = await cl.stato(rav4)
    assert finta.chiamate[:6] == [
        "authenticate",
        "authenticate",
        "authenticate",
        "authenticate",
        "authorize",
        "token:authorization_code",
    ]
    assert cl.token["uuid"] == c.UUID
    assert salvati and salvati[-1]["refresh_token"] == "R1"
    # La Yaris ibrida non ha la spina: non entra.
    assert [(v.vin, v.nome, v.modello, v.anno) for v in vetture] == [
        (c.VIN_RAV4, "RAV4", "RAV4 PHEV", 2020),
        (c.VIN_BZ4X, "Bianca", "bZ4X", 2023),
    ]
    assert bz4x.targa == "GX123YZ"
    assert stato_bz4x.batteria == 62 and stato_bz4x.chiusa is True and stato_bz4x.clima_acceso is True
    assert stato_rav4.batteria == 79 and stato_rav4.latitudine == 52.169516 and stato_rav4.clima_acceso is False
    # La RAV4 non dichiara lo stato delle portiere: non lo si chiede.
    assert finta.chiamate.count("GET /v1/vehicle/status") == 1
    assert finta.marchi == {"T"}


async def test_lexus_e_lo_stesso_cloud_con_un_altro_marchio(cloud) -> None:
    finta, url = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, url, marchio="lexus")
        await cl.vetture()
    assert finta.marchi == {"L"}
    assert cl.nome_marchio == "Lexus"


async def test_password_sbagliata_e_utente_sconosciuto(cloud) -> None:
    _, url = cloud
    async with aiohttp.ClientSession() as s:
        with pytest.raises(ErroreCredenziali, match="email o password"):
            await _cliente(s, url, password="sbagliata").accedi()
        with pytest.raises(ErroreCredenziali, match="non conosce"):
            await _cliente(s, url, email="nessuno@esempio.it").accedi()


async def test_un_guasto_non_chiede_di_rientrare(cloud) -> None:
    finta, url = cloud
    async with aiohttp.ClientSession() as s:
        finta.guasto_login = True
        with pytest.raises(ErroreRete):
            await _cliente(s, url).accedi()
        finta.guasto_login = False
        cl = _cliente(s, url)
        (v, _) = await cl.vetture()
        finta.guasto_api = True
        with pytest.raises(ErroreRete):
            await cl.stato(v)
        with pytest.raises(ErroreRete):
            await _cliente(s, "http://127.0.0.1:9").accedi()


async def test_il_token_scaduto_si_rinnova_e_ruota(cloud) -> None:
    finta, url = cloud
    salvati: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, url, salva=salvati.append)
        await cl.accedi()
        cl.token["scade"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await cl.vetture()
    assert "token:refresh_token" in finta.chiamate
    assert finta.chiamate.count("authenticate") == 4  # il login con la password, una volta sola
    assert salvati[-1]["refresh_token"] == "R2"
    assert salvati[-1]["uuid"] == c.UUID


async def test_un_refresh_rifiutato_rientra_con_la_password(cloud) -> None:
    finta, url = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, url)
        await cl.accedi()
        finta.rifiuta_refresh = True
        cl.token["scade"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await cl.vetture()
        assert finta.chiamate.count("authenticate") == 8
        # Un token che il server non vuole più a metà strada: se ne prende uno nuovo e si riprova.
        finta.access = "CAMBIATO"
        finta.rifiuta_refresh = False
        await cl.vetture()
    assert finta.chiamate.count("GET /v2/vehicle/guid") == 3


async def test_salvati_i_token_non_si_rientra(cloud) -> None:
    finta, url = cloud
    async with aiohttp.ClientSession() as s:
        primo = _cliente(s, url)
        await primo.accedi()
        finta.chiamate.clear()
        secondo = _cliente(s, url, token=primo.token)
        await secondo.vetture()
    assert finta.chiamate == ["GET /v2/vehicle/guid"]


async def test_serrature_e_clima(cloud) -> None:
    finta, url = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, url)
        _, bz4x = await cl.vetture()
        await cl.chiudi(bz4x, None)
        await cl.apri(bz4x, None)
        await cl.clima(bz4x, True, None, 35)
        await cl.clima(bz4x, False, None)
        finta.clima_codice = "118003"
        with pytest.raises(ErroreComando, match="118003"):
            await cl.clima(bz4x, True, None, 21)
        finta.comando_http = 403
        with pytest.raises(ErroreComando, match="Not allowed"):
            await cl.chiudi(bz4x, None)
    assert finta.comandi[:4] == [
        ("/v1/global/remote/command", {"command": "door-lock"}),
        ("/v1/global/remote/command", {"command": "door-unlock"}),
        ("/v2/remote/climate-control", {"command": "start", "temperature": {"value": 29, "unit": "C"}}),
        ("/v2/remote/climate-control", {"command": "stop"}),
    ]


async def test_un_auto_senza_dati_e_un_errore(cloud) -> None:
    _, url = cloud
    async with aiohttp.ClientSession() as s:
        cl = _cliente(s, url)
        await cl.accedi()
        with pytest.raises(ErroreApi):
            await cl.stato(Vettura("SCONOSCIUTA", "toyota", "bZ4X", extra={"stato": False}))


# --- dentro Home Assistant -----------------------------------------------------------


def _bianca() -> Vettura:
    return Vettura(c.VIN_BZ4X, "toyota", "bZ4X", "Bianca", extra=capacita(c.BZ4X))


_STATO_BIANCA = interpreta({"elettrico": c.ELETTRICO_BZ4X["payload"], "stato": c.STATO["payload"]})
_TOKEN = {"access_token": "A1", "refresh_token": "R1", "uuid": c.UUID, "scade": "2099-01-01T00:00:00+00:00"}


async def _configura(hass: HomeAssistant, marchio: str) -> Any:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"marchio_scelto": marchio})
    assert r["step_id"] == "credenziali"
    assert {str(k) for k in r["data_schema"].schema} == {"email", "password"}

    async def accedi(self: ClienteToyota) -> None:
        if self._password != PASSWORD:
            raise ErroreCredenziali("no")
        self.token = dict(_TOKEN)

    with (
        patch.object(ClienteToyota, "accedi", accedi),
        patch.object(ClienteToyota, "vetture", AsyncMock(return_value=[_bianca()])),
        patch.object(ClienteToyota, "stato", AsyncMock(return_value=_STATO_BIANCA)),
    ):
        r2 = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": EMAIL, "password": "no"})
        assert r2["errors"] == {"base": "credenziali"}
        r2 = await hass.config_entries.flow.async_configure(r["flow_id"], {"email": f" {EMAIL} ", "password": PASSWORD})
        await hass.async_block_till_done()
    return r2


async def test_configurare_una_toyota(hass: HomeAssistant) -> None:
    r = await _configura(hass, "toyota")
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == f"Toyota · {EMAIL}"
    assert r["result"].unique_id == f"toyota_{EMAIL}"
    assert r["data"]["marca"] == "toyota"
    assert r["data"]["marchio"] == "toyota"
    assert r["data"]["email"] == EMAIL
    assert r["data"]["token"] == _TOKEN
    assert hass.states.get("sensor.bianca_batteria").state == "62"
    assert hass.states.get("lock.bianca_serratura").state == "locked"
    assert hass.states.get("switch.bianca_clima") is not None

    # Il token che ruota finisce nella voce, senza ricaricarla.
    voce = r["result"]
    cliente = voce.runtime_data.conto.cliente
    assert isinstance(cliente, ClienteToyota) and cliente.marchio == "toyota"
    cliente._salva({**_TOKEN, "refresh_token": "R9"})
    assert voce.data["token"]["refresh_token"] == "R9"


async def test_configurare_una_lexus(hass: HomeAssistant) -> None:
    r = await _configura(hass, "lexus")
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == f"Lexus · {EMAIL}"
    assert r["result"].unique_id == f"toyota_lexus_{EMAIL}"
    assert r["data"]["marchio"] == "lexus"
    assert r["result"].runtime_data.conto.cliente.nome_marchio == "Lexus"
    assert hass.states.get("sensor.bianca_batteria").state == "62"
